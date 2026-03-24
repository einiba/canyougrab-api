"""
TLD registry — controls WHOIS/RDAP behavior per TLD.

Loads the tld_registry table from PostgreSQL and caches it in-memory.
Refreshed every 5 minutes so changes take effect without restarts.
"""

import time
import logging
import threading

logger = logging.getLogger(__name__)

_cache: dict[str, dict] | None = None
_cache_lock = threading.Lock()
_cache_loaded_at: float = 0
_CACHE_TTL = 300  # 5 minutes


def _load_registry() -> dict[str, dict]:
    """Load tld_registry from PostgreSQL. Returns {tld: {...}} dict."""
    from queries import get_db_conn
    registry = {}
    try:
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT tld, rdap_server, whois_disabled_at, whois_disabled_reason
                    FROM tld_registry
                """)
                for row in cur.fetchall():
                    registry[row[0]] = {
                        'rdap_server': row[1],
                        'whois_disabled': row[2] is not None,
                        'whois_disabled_reason': row[3],
                    }
        finally:
            conn.close()
        logger.info('Loaded TLD registry: %d TLDs (%d with WHOIS disabled)',
                     len(registry),
                     sum(1 for v in registry.values() if v['whois_disabled']))
    except Exception as e:
        logger.warning('Failed to load TLD registry: %s', e)
    return registry


def _get_registry() -> dict[str, dict]:
    """Get the cached registry, refreshing if stale."""
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_loaded_at) < _CACHE_TTL:
        return _cache
    with _cache_lock:
        # Double-check after acquiring lock
        if _cache is not None and (now - _cache_loaded_at) < _CACHE_TTL:
            return _cache
        _cache = _load_registry()
        _cache_loaded_at = now
        return _cache


def is_whois_disabled(tld: str) -> bool:
    """Check if WHOIS lookups are disabled for this TLD."""
    registry = _get_registry()
    entry = registry.get(tld.lower())
    if entry is None:
        return False  # Unknown TLD — allow WHOIS
    return entry['whois_disabled']
