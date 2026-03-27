"""
PostgreSQL queries for usage tracking, auth, and billing.
"""

import logging
import os

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


def get_db_conn():
    kwargs = dict(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        dbname=os.environ.get('POSTGRES_DB', 'canyougrab'),
        user=os.environ.get('POSTGRES_USER', 'canyougrab'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
    )
    sslmode = os.environ.get('POSTGRES_SSLMODE', 'require')
    kwargs['sslmode'] = sslmode
    return psycopg2.connect(**kwargs)


def _ensure_usage_table(conn):
    """Create the usage tracking table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_log_daily (
                id SERIAL PRIMARY KEY,
                consumer TEXT NOT NULL,
                lookups INTEGER NOT NULL DEFAULT 0,
                recorded_at DATE NOT NULL DEFAULT CURRENT_DATE,
                UNIQUE (consumer, recorded_at)
            )
        """)
        conn.commit()


def record_usage(consumer: str, lookup_count: int):
    """Record lookup usage for a consumer. Upserts today's row."""
    conn = get_db_conn()
    try:
        _ensure_usage_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO usage_log_daily (consumer, lookups, recorded_at)
                VALUES (%s, %s, CURRENT_DATE)
                ON CONFLICT (consumer, recorded_at)
                DO UPDATE SET lookups = usage_log_daily.lookups + EXCLUDED.lookups
            """, (consumer, lookup_count))
            conn.commit()
    finally:
        conn.close()


def get_usage(consumer: str) -> dict:
    """Get usage summary for a consumer."""
    conn = get_db_conn()
    try:
        _ensure_usage_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT COALESCE(SUM(lookups), 0) AS lookups_today
                FROM usage_log_daily
                WHERE consumer = %s AND recorded_at = CURRENT_DATE
            """, (consumer,))
            row = cur.fetchone()
            lookups_today = row["lookups_today"] if row else 0
        return {
            "plan": "free",
            "lookups_today": lookups_today,
            "lookups_limit": 100,
            "period": "daily",
        }
    finally:
        conn.close()


def get_detailed_usage(consumers: list) -> dict:
    """Get usage breakdown for multiple consumers."""
    conn = get_db_conn()
    try:
        _ensure_usage_table(conn)
        by_consumer = {}
        total = 0
        if consumers:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                placeholders = ",".join(["%s"] * len(consumers))
                cur.execute(f"""
                    SELECT consumer, COALESCE(SUM(lookups), 0) AS lookups_today
                    FROM usage_log_daily
                    WHERE consumer IN ({placeholders}) AND recorded_at = CURRENT_DATE
                    GROUP BY consumer
                """, consumers)
                for row in cur.fetchall():
                    by_consumer[row["consumer"]] = row["lookups_today"]
                    total += row["lookups_today"]
        return {"by_consumer": by_consumer, "total": total}
    finally:
        conn.close()


def get_monthly_usage(consumer: str) -> int:
    """Get total lookups for a consumer this calendar month."""
    conn = get_db_conn()
    try:
        _ensure_usage_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(lookups), 0)
                FROM usage_log_daily
                WHERE consumer = %s
                  AND recorded_at >= DATE_TRUNC('month', CURRENT_DATE)::date
            """, (consumer,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def get_monthly_detailed_usage(consumers: list) -> dict:
    """Get monthly usage breakdown for multiple consumers."""
    conn = get_db_conn()
    try:
        _ensure_usage_table(conn)
        by_consumer = {}
        total = 0
        if consumers:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(consumers))
                cur.execute(f"""
                    SELECT consumer, COALESCE(SUM(lookups), 0) AS lookups_month
                    FROM usage_log_daily
                    WHERE consumer IN ({placeholders})
                      AND recorded_at >= DATE_TRUNC('month', CURRENT_DATE)::date
                    GROUP BY consumer
                """, consumers)
                for row in cur.fetchall():
                    by_consumer[row[0]] = row[1]
                    total += row[1]
        return {"by_consumer": by_consumer, "total": total}
    finally:
        conn.close()


def _ensure_minute_usage_table(conn):
    """Create the per-minute usage tracking table if it does not exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_log_minute (
                id SERIAL PRIMARY KEY,
                consumer TEXT NOT NULL,
                lookups INTEGER NOT NULL DEFAULT 0,
                minute_start TIMESTAMP NOT NULL,
                UNIQUE (consumer, minute_start)
            )
        """)
        conn.commit()


def record_minute_usage(consumer: str, lookup_count: int):
    """Record lookup usage for the current UTC minute."""
    conn = get_db_conn()
    try:
        _ensure_minute_usage_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO usage_log_minute (consumer, lookups, minute_start)
                VALUES (%s, %s, DATE_TRUNC('minute', NOW()))
                ON CONFLICT (consumer, minute_start)
                DO UPDATE SET lookups = usage_log_minute.lookups + EXCLUDED.lookups
            """, (consumer, lookup_count))
            conn.commit()
    finally:
        conn.close()


def get_minute_usage(consumer: str) -> int:
    """Get total lookups for a consumer in the current UTC minute."""
    conn = get_db_conn()
    try:
        _ensure_minute_usage_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(lookups), 0)
                FROM usage_log_minute
                WHERE consumer = %s
                  AND minute_start = DATE_TRUNC('minute', NOW())
            """, (consumer,))
            return cur.fetchone()[0]
    finally:
        conn.close()


# ── domain_whois_enrichment (migration 009) ───────────────────────────────

def upsert_whois_enrichment(domain: str, tld: str, source: str, registration: dict):
    """Cache WHOIS/RDAP registration data for 90 days."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO domain_whois_enrichment
                    (domain, tld, source, registered, registrar,
                     created_date, updated_date, expires_date,
                     fetched_at, expires_at)
                VALUES (%s, %s, %s, TRUE, %s, %s, %s, %s,
                        NOW(), NOW() + INTERVAL '90 days')
                ON CONFLICT (domain) DO UPDATE SET
                    source       = EXCLUDED.source,
                    registered   = TRUE,
                    registrar    = EXCLUDED.registrar,
                    created_date = EXCLUDED.created_date,
                    updated_date = EXCLUDED.updated_date,
                    expires_date = EXCLUDED.expires_date,
                    fetched_at   = NOW(),
                    expires_at   = NOW() + INTERVAL '90 days'
            """, (
                domain, tld, source,
                registration.get('registrar'),
                registration.get('created_at') or registration.get('creation_date'),
                registration.get('updated_at') or registration.get('updated_date'),
                registration.get('expires_at') or registration.get('expiration_date'),
            ))
            conn.commit()
    except Exception:
        logger.exception('Failed to upsert whois enrichment for %s', domain)
    finally:
        conn.close()


def get_whois_enrichment_bulk(domains: list[str]) -> dict[str, dict]:
    """Return cached WHOIS registration data for multiple domains.

    Returns {domain: {registrar, created_at, updated_at, expires_at, source}}.
    Missing or expired entries are omitted.
    """
    if not domains:
        return {}
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            placeholders = ','.join(['%s'] * len(domains))
            cur.execute(f"""
                SELECT domain, source, registrar,
                       created_date, updated_date, expires_date
                FROM domain_whois_enrichment
                WHERE domain IN ({placeholders}) AND expires_at > NOW()
            """, domains)
            result = {}
            for row in cur.fetchall():
                result[row['domain']] = {
                    'source':     row['source'],
                    'registrar':  row['registrar'],
                    'created_at': row['created_date'].isoformat() if row['created_date'] else None,
                    'updated_at': row['updated_date'].isoformat() if row['updated_date'] else None,
                    'expires_at': row['expires_date'].isoformat() if row['expires_date'] else None,
                }
            return result
    except Exception:
        return {}
    finally:
        conn.close()


# ── zone_ns_snapshots (migration 010) ─────────────────────────────────────

def upsert_ns_snapshots_bulk(entries: list[tuple[str, str, list[str]]]):
    """Write today's NS snapshot for multiple domains.

    entries: [(domain_sld, tld, [nameserver, ...]), ...]
    """
    if not entries:
        return
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO zone_ns_snapshots (domain, tld, nameservers, snapshot_date)
                VALUES (%s, %s, %s, CURRENT_DATE)
                ON CONFLICT (domain, tld, snapshot_date) DO UPDATE SET
                    nameservers = EXCLUDED.nameservers
            """, entries)
            conn.commit()
    except Exception:
        pass  # Non-critical
    finally:
        conn.close()


def get_ns_snapshots_bulk(domains: list[str]) -> dict[str, list[str]]:
    """Return today's or yesterday's NS for multiple FQDNs.

    domains: ['google.com', 'example.net', ...]
    Returns: {'google.com': ['ns1.google.com', ...], ...}
    """
    if not domains:
        return {}

    # Split FQDNs into (sld, tld) — assumes single-label TLD
    pairs = []
    fqdn_map: dict[tuple[str, str], str] = {}
    for fqdn in domains:
        parts = fqdn.rsplit('.', 1)
        if len(parts) == 2:
            key = (parts[0], parts[1])
            pairs.append(key)
            fqdn_map[key] = fqdn

    if not pairs:
        return {}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            placeholders = ','.join(['(%s,%s)'] * len(pairs))
            flat = [v for pair in pairs for v in pair]
            cur.execute(f"""
                SELECT DISTINCT ON (domain, tld) domain, tld, nameservers
                FROM zone_ns_snapshots
                WHERE (domain, tld) IN ({placeholders})
                  AND snapshot_date >= CURRENT_DATE - INTERVAL '1 day'
                ORDER BY domain, tld, snapshot_date DESC
            """, flat)
            result = {}
            for row in cur.fetchall():
                fqdn = fqdn_map.get((row[0], row[1]))
                if fqdn:
                    result[fqdn] = row[2]
            return result
    except Exception:
        return {}
    finally:
        conn.close()


def get_minute_detailed_usage(consumers: list) -> dict:
    """Get per-minute usage breakdown for multiple consumers."""
    conn = get_db_conn()
    try:
        _ensure_minute_usage_table(conn)
        by_consumer = {}
        total = 0
        if consumers:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(consumers))
                cur.execute(f"""
                    SELECT consumer, COALESCE(SUM(lookups), 0) AS lookups_minute
                    FROM usage_log_minute
                    WHERE consumer IN ({placeholders})
                      AND minute_start = DATE_TRUNC('minute', NOW())
                    GROUP BY consumer
                """, consumers)
                for row in cur.fetchall():
                    by_consumer[row[0]] = row[1]
                    total += row[1]
        return {"by_consumer": by_consumer, "total": total}
    finally:
        conn.close()


