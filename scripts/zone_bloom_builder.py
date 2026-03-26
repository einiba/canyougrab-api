#!/usr/bin/env python3
"""
Zone file bloom filter builder.

Downloads TLD zone files from ICANN CZDS, extracts domain SLDs,
and builds Valkey bitfield bloom filters for fast registration lookup.

Runs as a K8s CronJob (daily at 07:00 UTC, after CZDS updates at 06:00).

Usage:
    python zone_bloom_builder.py                  # Build all configured TLDs
    python zone_bloom_builder.py --tld com        # Build specific TLD
    python zone_bloom_builder.py --test            # Test mode with small sample

Environment:
    CZDS_USERNAME    — ICANN CZDS account email
    CZDS_PASSWORD    — ICANN CZDS account password
    VALKEY_HOST      — Valkey connection host
    VALKEY_PORT      — Valkey connection port
    VALKEY_PASSWORD  — Valkey password
"""

import os
import sys
import gzip
import time
import logging
import argparse
import tempfile
from pathlib import Path

import requests

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from zone_bloom import build_bloom_filter, check_domain_bloom, bloom_key, meta_key

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [zone-builder] %(message)s',
)
logger = logging.getLogger(__name__)

# TLDs to build bloom filters for (top 9 = ~90% of traffic)
TARGET_TLDS = ['com', 'net', 'org', 'xyz', 'info', 'shop', 'top', 'online', 'store']

CZDS_AUTH_URL = "https://account-api.icann.org/api/authenticate"
CZDS_DOWNLOAD_URL = "https://czds-download-api.icann.org/czds/downloads/{tld}.zone"


def get_czds_token(username: str, password: str) -> str:
    """Authenticate with ICANN CZDS and get a JWT token."""
    resp = requests.post(CZDS_AUTH_URL, json={
        "username": username,
        "password": password,
    })
    resp.raise_for_status()
    return resp.json()["accessToken"]


def download_zone_file(tld: str, token: str, output_dir: str) -> str:
    """Download a TLD zone file from CZDS. Returns path to downloaded file."""
    url = CZDS_DOWNLOAD_URL.format(tld=tld)
    output_path = os.path.join(output_dir, f"{tld}.zone.gz")

    logger.info('Downloading .%s zone file from CZDS...', tld)
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, stream=True)
    resp.raise_for_status()

    total = 0
    with open(output_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            total += len(chunk)

    logger.info('.%s zone file downloaded: %.1f MB', tld, total / 1024 / 1024)
    return output_path


def extract_slds_from_zone(zone_path: str, tld: str):
    """Stream-parse a zone file and yield unique SLDs.

    Zone file format (simplified):
        google.com.  NS  ns1.google.com.
        amazon.com.  NS  ns1.amazon.com.

    We extract the first column (domain), strip the TLD, and deduplicate.
    """
    seen = set()
    suffix = f".{tld}."
    suffix_len = len(suffix)

    opener = gzip.open if zone_path.endswith('.gz') else open
    mode = 'rt' if zone_path.endswith('.gz') else 'r'

    with opener(zone_path, mode, encoding='ascii', errors='ignore') as f:
        for line in f:
            # Skip comments and empty lines
            if not line or line[0] in (';', '$', '\n', ' ', '\t'):
                continue

            # First whitespace-separated field is the domain name
            domain = line.split(None, 1)[0] if line[0] not in (' ', '\t') else None
            if not domain:
                continue

            # Must end with .tld.
            if not domain.endswith(suffix):
                continue

            # Extract SLD (strip .tld.)
            sld = domain[:-suffix_len]

            # Skip subdomain records (we only want SLD.tld)
            if '.' in sld:
                continue

            if sld not in seen:
                seen.add(sld)
                yield sld

    logger.info('.%s: extracted %d unique SLDs', tld, len(seen))


def get_valkey():
    """Get Valkey connection using same config as backend."""
    import redis
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = int(os.environ.get('VALKEY_PORT', '25061'))
    password = os.environ.get('VALKEY_PASSWORD', '')
    username = os.environ.get('VALKEY_USERNAME', 'default')

    # Detect SSL from port
    use_ssl = port in (25061, 6380)

    return redis.Redis(
        host=host, port=port, password=password, username=username,
        ssl=use_ssl, ssl_cert_reqs=None,
        decode_responses=False,
        max_connections=5,
    )


def build_test_filter(valkey_client):
    """Build a tiny test filter to verify the pipeline works."""
    logger.info('Building test bloom filter...')
    test_domains = [
        'google', 'amazon', 'facebook', 'microsoft', 'apple',
        'netflix', 'twitter', 'github', 'stackoverflow', 'reddit',
    ]
    result = build_bloom_filter(valkey_client, 'com', test_domains, expected_count=10)
    logger.info('Test result: %s', result)

    # Verify
    for d in test_domains:
        found = check_domain_bloom(valkey_client, f'{d}.com')
        logger.info('  %s.com → %s', d, 'HIT' if found else 'MISS')

    # Check a domain NOT in the filter
    found = check_domain_bloom(valkey_client, 'thisdomaindoesnotexist12345.com')
    logger.info('  thisdomaindoesnotexist12345.com → %s (should be MISS)', 'HIT (false positive)' if found else 'MISS')


def build_tld(tld: str, token: str, valkey_client, work_dir: str) -> dict:
    """Download zone file and build bloom filter for a single TLD."""
    t0 = time.monotonic()

    # Download
    zone_path = download_zone_file(tld, token, work_dir)

    # Stream-parse and build
    slds = extract_slds_from_zone(zone_path, tld)

    # We need to know the count for optimal sizing — first pass counts, second builds
    # For large files, we estimate from file size
    file_size = os.path.getsize(zone_path)
    # Rough estimate: ~65 bytes per NS record, ~2 NS records per domain
    estimated_count = file_size // 130
    logger.info('.%s: estimated %dM domains from %.1f MB zone file',
                tld, estimated_count // 1_000_000, file_size / 1024 / 1024)

    # Re-extract (generator was consumed)
    slds = extract_slds_from_zone(zone_path, tld)
    result = build_bloom_filter(valkey_client, tld, slds, expected_count=estimated_count)

    # Cleanup zone file
    os.remove(zone_path)

    elapsed = time.monotonic() - t0
    result['elapsed_seconds'] = round(elapsed, 1)
    logger.info('.%s complete in %.1fs', tld, elapsed)
    return result


def main():
    parser = argparse.ArgumentParser(description='Build zone file bloom filters')
    parser.add_argument('--tld', help='Build specific TLD only')
    parser.add_argument('--test', action='store_true', help='Test mode with sample data')
    args = parser.parse_args()

    valkey_client = get_valkey()
    valkey_client.ping()
    logger.info('Valkey connected')

    if args.test:
        build_test_filter(valkey_client)
        return

    # CZDS credentials
    username = os.environ.get('CZDS_USERNAME')
    password = os.environ.get('CZDS_PASSWORD')
    if not username or not password:
        logger.error('Set CZDS_USERNAME and CZDS_PASSWORD environment variables')
        sys.exit(1)

    # Authenticate
    token = get_czds_token(username, password)
    logger.info('CZDS authenticated')

    # Build filters
    tlds = [args.tld] if args.tld else TARGET_TLDS
    results = []

    with tempfile.TemporaryDirectory() as work_dir:
        for tld in tlds:
            try:
                result = build_tld(tld, token, valkey_client, work_dir)
                results.append(result)
            except Exception as e:
                logger.error('Failed to build .%s: %s', tld, e)
                results.append({"tld": tld, "error": str(e)})

    # Summary
    logger.info('=== BUILD COMPLETE ===')
    total_domains = 0
    total_mb = 0
    for r in results:
        if 'error' in r:
            logger.info('  .%s: FAILED — %s', r['tld'], r['error'])
        else:
            logger.info('  .%s: %dM domains, %.1f MB, %.1fs',
                        r['tld'], r['domains_loaded'] // 1_000_000,
                        r['size_mb'], r['elapsed_seconds'])
            total_domains += r['domains_loaded']
            total_mb += r['size_mb']

    logger.info('  TOTAL: %dM domains, %.1f MB in Valkey', total_domains // 1_000_000, total_mb)


if __name__ == '__main__':
    main()
