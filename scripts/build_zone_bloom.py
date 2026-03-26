#!/usr/bin/env python3
"""
Download ICANN zone files and build bloom filters in Valkey.

Runs as a K8s CronJob. Downloads zone files from CZDS, extracts SLDs,
and builds bloom filters using SETBIT for each TLD.

Environment variables:
  CZDS_USERNAME — ICANN CZDS API username
  CZDS_PASSWORD — ICANN CZDS API password
  VALKEY_HOST, VALKEY_PORT, VALKEY_USERNAME, VALKEY_PASSWORD — Valkey connection
  BLOOM_TLDS — comma-separated TLDs to build (default: com,net,org,store,xyz,info,shop,top,online)
"""

import gzip
import io
import logging
import os
import sys
import tempfile
import time

import httpx
import redis

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from zone_bloom import build_bloom_filter, check_domain_bloom, meta_key

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

CZDS_USERNAME = os.environ.get('CZDS_USERNAME', '')
CZDS_PASSWORD = os.environ.get('CZDS_PASSWORD', '')
CZDS_AUTH_URL = 'https://account-api.icann.org/api/authenticate'
CZDS_DOWNLOAD_URL = 'https://czds-download-api.icann.org/czds/downloads/{tld}.zone'

DEFAULT_TLDS = 'com,net,org,store,xyz,info,shop,top,online'
BLOOM_TLDS = os.environ.get('BLOOM_TLDS', DEFAULT_TLDS).split(',')


def get_czds_token() -> str:
    """Authenticate with CZDS and return access token."""
    resp = httpx.post(CZDS_AUTH_URL, json={
        'username': CZDS_USERNAME,
        'password': CZDS_PASSWORD,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()['accessToken']


def download_zone_file(token: str, tld: str, dest_path: str) -> int:
    """Download a zone file from CZDS. Returns file size in bytes."""
    url = CZDS_DOWNLOAD_URL.format(tld=tld)
    logger.info('Downloading .%s zone file from CZDS...', tld)
    t0 = time.time()

    with httpx.stream('GET', url, headers={'Authorization': f'Bearer {token}'}, timeout=600) as resp:
        resp.raise_for_status()
        with open(dest_path, 'wb') as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)

    size = os.path.getsize(dest_path)
    elapsed = time.time() - t0
    logger.info('.%s zone file downloaded: %.1f MB in %.1fs (%.1f MB/s)',
                tld, size / 1024 / 1024, elapsed, size / 1024 / 1024 / elapsed)
    return size


def extract_slds(zone_path: str, tld: str):
    """Stream-extract unique SLDs from a gzipped zone file."""
    seen = set()
    suffix = f'.{tld}.'
    suffix_len = len(suffix)

    with gzip.open(zone_path, 'rt', encoding='ascii', errors='ignore') as f:
        for line in f:
            if not line or line[0] in (';', '$', '\n', ' ', '\t'):
                continue
            parts = line.split(None, 1)
            if not parts:
                continue
            domain = parts[0]
            if not domain.endswith(suffix):
                continue
            sld = domain[:-suffix_len]
            if '.' in sld or not sld:
                continue
            if sld not in seen:
                seen.add(sld)
                yield sld

    logger.info('.%s: extracted %d unique SLDs', tld, len(seen))


def get_valkey():
    """Create Valkey connection from environment."""
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = int(os.environ.get('VALKEY_PORT', '6379'))
    username = os.environ.get('VALKEY_USERNAME', '')
    password = os.environ.get('VALKEY_PASSWORD', '')
    use_ssl = port == 25061 or os.environ.get('VALKEY_SSL', '').lower() in ('true', '1')

    return redis.Redis(
        host=host, port=port,
        username=username or None,
        password=password or None,
        ssl=use_ssl,
        ssl_cert_reqs=None if use_ssl else None,
        decode_responses=False,
    )


def main():
    if not CZDS_USERNAME or not CZDS_PASSWORD:
        logger.error('CZDS_USERNAME and CZDS_PASSWORD must be set')
        sys.exit(1)

    v = get_valkey()
    v.ping()
    logger.info('Valkey connected (%s)', v.connection_pool.connection_kwargs.get('host', '?'))

    mem_before = v.info('memory')['used_memory_human']
    logger.info('Valkey memory before: %s', mem_before)

    token = get_czds_token()
    logger.info('CZDS authenticated')

    results = {}
    for tld in BLOOM_TLDS:
        tld = tld.strip()
        if not tld:
            continue

        try:
            with tempfile.NamedTemporaryFile(suffix=f'.{tld}.zone.gz', delete=True) as tmp:
                download_zone_file(token, tld, tmp.name)

                # Estimate domain count from file size (rough: 1 domain per ~25 compressed bytes)
                file_size = os.path.getsize(tmp.name)
                estimated_count = max(file_size // 25, 100_000)

                logger.info('.%s: building bloom filter (estimated %dM domains)...', tld, estimated_count // 1_000_000)
                t0 = time.time()

                result = build_bloom_filter(
                    v, tld,
                    extract_slds(tmp.name, tld),
                    expected_count=estimated_count,
                )
                elapsed = time.time() - t0

                if 'error' in result:
                    logger.error('.%s: FAILED — %s', tld, result['error'])
                    results[tld] = {'status': 'failed', 'error': result['error']}
                else:
                    logger.info('.%s: SUCCESS — %d domains, %.1f MB, took %.1fs',
                                tld, result['domains_loaded'], result['size_mb'], elapsed)
                    results[tld] = {'status': 'ok', **result}

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning('.%s: zone file not available (404) — skipping', tld)
                results[tld] = {'status': 'skipped', 'reason': 'not_available'}
            else:
                logger.error('.%s: HTTP error %d — %s', tld, e.response.status_code, str(e))
                results[tld] = {'status': 'failed', 'error': str(e)}
        except Exception as e:
            logger.error('.%s: FAILED — %s', tld, str(e), exc_info=True)
            results[tld] = {'status': 'failed', 'error': str(e)}

    mem_after = v.info('memory')['used_memory_human']
    logger.info('Valkey memory after: %s (was %s)', mem_after, mem_before)

    # Summary
    logger.info('=== BLOOM FILTER BUILD SUMMARY ===')
    for tld, r in results.items():
        if r['status'] == 'ok':
            logger.info('  .%s: %d domains, %.1f MB', tld, r['domains_loaded'], r['size_mb'])
        else:
            logger.info('  .%s: %s', tld, r.get('error', r.get('reason', '?')))

    failed = [t for t, r in results.items() if r['status'] == 'failed']
    if failed:
        logger.error('%d TLDs failed: %s', len(failed), ', '.join(failed))
        sys.exit(1)

    logger.info('All TLDs processed successfully')


if __name__ == '__main__':
    main()
