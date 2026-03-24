#!/usr/bin/env python3
"""
Refresh IANA RDAP bootstrap data into tld_registry.

Fetches https://data.iana.org/rdap/dns.json and UPSERTs TLD→RDAP server
mappings into PostgreSQL. Manual and supplemental overrides are never
overwritten by IANA data.

Usage: python3 scripts/refresh_iana_rdap.py
"""

import json
import logging
import os
import sys

import httpx
import psycopg2

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

IANA_RDAP_URL = 'https://data.iana.org/rdap/dns.json'

# Known-working RDAP servers for TLDs not in IANA bootstrap
SUPPLEMENTAL_SERVERS = {
    'io': 'https://rdap.identitydigital.services/rdap/',
    'me': 'https://rdap.identitydigital.services/rdap/',
    'co': 'https://rdap.centralnic.com/co/',
}


def get_db_conn():
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        dbname=os.environ.get('POSTGRES_DB', 'canyougrab'),
        user=os.environ.get('POSTGRES_USER', 'canyougrab'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
        sslmode=os.environ.get('POSTGRES_SSLMODE', 'require'),
    )


def fetch_iana_data() -> dict[str, str]:
    """Fetch IANA dns.json and return {tld: rdap_server_url} dict."""
    logger.info('Fetching %s', IANA_RDAP_URL)
    resp = httpx.get(IANA_RDAP_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    publication = data.get('publication', 'unknown')
    logger.info('IANA publication date: %s', publication)

    mappings = {}
    for service in data.get('services', []):
        tlds = service[0]
        servers = service[1]
        if not servers:
            continue
        server = servers[0]  # Always 1 server per service in practice
        for tld in tlds:
            mappings[tld.lower()] = server

    logger.info('Parsed %d TLD→server mappings from IANA', len(mappings))
    return mappings


def upsert_iana(conn, mappings: dict[str, str]) -> tuple[int, int, int]:
    """UPSERT IANA mappings. Returns (inserted, updated, skipped) counts."""
    inserted = updated = skipped = 0
    with conn.cursor() as cur:
        for tld, server in mappings.items():
            cur.execute("""
                INSERT INTO tld_registry (tld, rdap_server, origin, iana_updated_at, updated_at)
                VALUES (%s, %s, 'iana', NOW(), NOW())
                ON CONFLICT (tld) DO UPDATE SET
                    rdap_server = EXCLUDED.rdap_server,
                    iana_updated_at = NOW(),
                    updated_at = NOW()
                WHERE tld_registry.origin = 'iana'
                RETURNING (xmax = 0) AS is_insert
            """, (tld, server))
            row = cur.fetchone()
            if row is None:
                skipped += 1  # origin != 'iana', not overwritten
            elif row[0]:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated, skipped


def upsert_supplemental(conn, servers: dict[str, str]) -> int:
    """UPSERT supplemental servers. Only inserts new rows, never overwrites existing."""
    added = 0
    with conn.cursor() as cur:
        for tld, server in servers.items():
            cur.execute("""
                INSERT INTO tld_registry (tld, rdap_server, origin, updated_at)
                VALUES (%s, %s, 'supplemental', NOW())
                ON CONFLICT (tld) DO UPDATE SET
                    rdap_server = EXCLUDED.rdap_server,
                    updated_at = NOW()
                WHERE tld_registry.origin = 'supplemental'
                RETURNING tld
            """, (tld, server))
            if cur.fetchone():
                added += 1
    conn.commit()
    return added


def main():
    # Load DB env from env files if running on server
    for env_file in ['/opt/canyougrab/database.env', '.env']:
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        os.environ.setdefault(key, val)

    iana_mappings = fetch_iana_data()

    conn = get_db_conn()
    try:
        inserted, updated, skipped = upsert_iana(conn, iana_mappings)
        logger.info('IANA: %d inserted, %d updated, %d skipped (manual/supplemental override)',
                     inserted, updated, skipped)

        supp_count = upsert_supplemental(conn, SUPPLEMENTAL_SERVERS)
        logger.info('Supplemental: %d upserted', supp_count)

        # Summary
        with conn.cursor() as cur:
            cur.execute("SELECT origin, COUNT(*) FROM tld_registry GROUP BY origin ORDER BY origin")
            for row in cur.fetchall():
                logger.info('  %s: %d TLDs', row[0], row[1])
    finally:
        conn.close()

    logger.info('Done.')


if __name__ == '__main__':
    main()
