"""
PostgreSQL queries for usage tracking, auth, and billing.
"""

import os

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


def _ensure_minute_usage_table(conn):
    """Create the per-minute usage tracking table if it does not exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS minute_usage_log (
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
                INSERT INTO minute_usage_log (consumer, lookups, minute_start)
                VALUES (%s, %s, DATE_TRUNC('minute', NOW()))
                ON CONFLICT (consumer, minute_start)
                DO UPDATE SET lookups = minute_usage_log.lookups + EXCLUDED.lookups
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
                FROM minute_usage_log
                WHERE consumer = %s
                  AND minute_start = DATE_TRUNC('minute', NOW())
            """, (consumer,))
            return cur.fetchone()[0]
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
                    FROM minute_usage_log
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


