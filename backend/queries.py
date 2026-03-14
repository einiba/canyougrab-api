"""
Query logic for domain availability from PostgreSQL zone data.
"""

import os
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor


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


def check_domain(domain: str) -> dict:
    """Check if domain exists in zone data. Splits input into SLD + TLD to match DB schema."""
    domain = domain.lower().strip().rstrip('.')
    if not domain or '..' in domain:
        return {"domain": domain, "available": True, "error": "invalid domain"}

    parts = domain.split('.')
    if len(parts) < 2:
        return {"domain": domain, "available": True, "error": "need at least sld.tld"}

    sld = parts[-2]
    tld = parts[-1]

    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT domain, tld FROM domains WHERE domain = %s AND tld = %s LIMIT 1",
                (sld, tld),
            )
            row = cur.fetchone()
        if row:
            return {"domain": domain, "available": False, "tld": row["tld"]}
        return {"domain": domain, "available": True}
    finally:
        conn.close()


def get_zone_info(tld: Optional[str] = None) -> list:
    """Get zone load metadata."""
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if tld:
                cur.execute("SELECT tld, loaded_at, record_count FROM zones WHERE tld = %s", (tld,))
            else:
                cur.execute("SELECT tld, loaded_at, record_count FROM zones ORDER BY loaded_at DESC")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_tld_list() -> list:
    """Get list of supported TLDs with record counts for the public API."""
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT z.tld, z.record_count, z.loaded_at,
                       a.last_compressed_file_size, a.last_file_size
                FROM zones z
                LEFT JOIN all_TLDs a ON z.tld = a.tld
                ORDER BY z.tld
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _ensure_usage_table(conn):
    """Create the usage tracking table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
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
                INSERT INTO usage_log (consumer, lookups, recorded_at)
                VALUES (%s, %s, CURRENT_DATE)
                ON CONFLICT (consumer, recorded_at)
                DO UPDATE SET lookups = usage_log.lookups + EXCLUDED.lookups
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
                FROM usage_log
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
                    FROM usage_log
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
                FROM usage_log
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
                    FROM usage_log
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


def _ensure_hourly_usage_table(conn):
    """Create the hourly usage tracking table if it does not exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hourly_usage_log (
                id SERIAL PRIMARY KEY,
                consumer TEXT NOT NULL,
                lookups INTEGER NOT NULL DEFAULT 0,
                hour_start TIMESTAMP NOT NULL,
                UNIQUE (consumer, hour_start)
            )
        """)
        conn.commit()


def record_hourly_usage(consumer: str, lookup_count: int):
    """Record lookup usage for the current UTC hour."""
    conn = get_db_conn()
    try:
        _ensure_hourly_usage_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO hourly_usage_log (consumer, lookups, hour_start)
                VALUES (%s, %s, DATE_TRUNC('hour', NOW()))
                ON CONFLICT (consumer, hour_start)
                DO UPDATE SET lookups = hourly_usage_log.lookups + EXCLUDED.lookups
            """, (consumer, lookup_count))
            conn.commit()
    finally:
        conn.close()


def get_hourly_usage(consumer: str) -> int:
    """Get total lookups for a consumer in the current UTC hour."""
    conn = get_db_conn()
    try:
        _ensure_hourly_usage_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(lookups), 0)
                FROM hourly_usage_log
                WHERE consumer = %s
                  AND hour_start = DATE_TRUNC('hour', NOW())
            """, (consumer,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def get_hourly_detailed_usage(consumers: list) -> dict:
    """Get hourly usage breakdown for multiple consumers."""
    conn = get_db_conn()
    try:
        _ensure_hourly_usage_table(conn)
        by_consumer = {}
        total = 0
        if consumers:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(consumers))
                cur.execute(f"""
                    SELECT consumer, COALESCE(SUM(lookups), 0) AS lookups_hour
                    FROM hourly_usage_log
                    WHERE consumer IN ({placeholders})
                      AND hour_start = DATE_TRUNC('hour', NOW())
                    GROUP BY consumer
                """, consumers)
                for row in cur.fetchall():
                    by_consumer[row[0]] = row[1]
                    total += row[1]
        return {"by_consumer": by_consumer, "total": total}
    finally:
        conn.close()


def check_domain_pooled(domain: str, pool) -> dict:
    """Check domain using a connection pool (for worker). Same logic as check_domain()."""
    domain = domain.lower().strip().rstrip('.')
    if not domain or '..' in domain:
        return {"domain": domain, "available": True, "error": "invalid domain"}

    parts = domain.split('.')
    if len(parts) < 2:
        return {"domain": domain, "available": True, "error": "need at least sld.tld"}

    sld = parts[-2]
    tld = parts[-1]

    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT domain, tld FROM domains WHERE domain = %s AND tld = %s LIMIT 1",
                (sld, tld),
            )
            row = cur.fetchone()
        if row:
            return {"domain": domain, "available": False, "tld": row["tld"]}
        return {"domain": domain, "available": True}
    finally:
        pool.putconn(conn)
