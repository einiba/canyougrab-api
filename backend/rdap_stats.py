"""
Per-TLD RDAP outcome tracking.

Collects RDAP outcomes (success, domain_not_found, error, unsupported, whois_fallback)
in an in-memory buffer per worker process. Flushes to PostgreSQL in batches to avoid
per-lookup DB overhead.

The rdap_tld_stats table stores one row per TLD per day, using UPSERT to
atomically increment counters.
"""

import threading
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_buffer: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_last_flush = time.monotonic()
_FLUSH_INTERVAL = 10   # seconds
_FLUSH_THRESHOLD = 10   # total lookups across all TLDs (low: RQ forks per job)
_table_ensured = False


def record_rdap_outcome(tld: str, outcome: str):
    """Record an RDAP lookup outcome for a TLD. Thread-safe, batched writes."""
    global _last_flush
    with _lock:
        _buffer[tld][outcome] += 1
        total = sum(sum(v.values()) for v in _buffer.values())
        if total >= _FLUSH_THRESHOLD or (time.monotonic() - _last_flush) > _FLUSH_INTERVAL:
            _flush_to_db()
            _last_flush = time.monotonic()


def _ensure_table(conn):
    """Create the rdap_tld_stats table if it doesn't exist."""
    global _table_ensured
    if _table_ensured:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rdap_tld_stats (
                id              SERIAL PRIMARY KEY,
                tld             TEXT NOT NULL,
                recorded_date   DATE NOT NULL DEFAULT CURRENT_DATE,
                rdap_success    INTEGER NOT NULL DEFAULT 0,
                rdap_domain_not_found INTEGER NOT NULL DEFAULT 0,
                rdap_error      INTEGER NOT NULL DEFAULT 0,
                rdap_unsupported INTEGER NOT NULL DEFAULT 0,
                whois_fallback  INTEGER NOT NULL DEFAULT 0,
                UNIQUE (tld, recorded_date)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_rdap_tld_stats_tld_date
                ON rdap_tld_stats (tld, recorded_date DESC)
        """)
        conn.commit()
    _table_ensured = True


def _flush_to_db():
    """Flush accumulated stats to PostgreSQL. Called under _lock."""
    if not _buffer:
        return

    snapshot = dict(_buffer)
    _buffer.clear()

    try:
        from queries import get_db_conn
        conn = get_db_conn()
        try:
            _ensure_table(conn)
            with conn.cursor() as cur:
                for tld, outcomes in snapshot.items():
                    cur.execute("""
                        INSERT INTO rdap_tld_stats (tld, recorded_date,
                            rdap_success, rdap_domain_not_found, rdap_error,
                            rdap_unsupported, whois_fallback)
                        VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s)
                        ON CONFLICT (tld, recorded_date)
                        DO UPDATE SET
                            rdap_success = rdap_tld_stats.rdap_success + EXCLUDED.rdap_success,
                            rdap_domain_not_found = rdap_tld_stats.rdap_domain_not_found + EXCLUDED.rdap_domain_not_found,
                            rdap_error = rdap_tld_stats.rdap_error + EXCLUDED.rdap_error,
                            rdap_unsupported = rdap_tld_stats.rdap_unsupported + EXCLUDED.rdap_unsupported,
                            whois_fallback = rdap_tld_stats.whois_fallback + EXCLUDED.whois_fallback
                    """, (
                        tld,
                        outcomes.get('rdap_success', 0),
                        outcomes.get('rdap_domain_not_found', 0),
                        outcomes.get('rdap_error', 0),
                        outcomes.get('rdap_unsupported', 0),
                        outcomes.get('whois_fallback', 0),
                    ))
                conn.commit()
            logger.info('Flushed RDAP stats: %d TLDs', len(snapshot))
        finally:
            conn.close()
    except Exception as e:
        logger.warning('Failed to flush RDAP stats to DB: %s', e)
