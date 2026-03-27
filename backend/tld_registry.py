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
_CACHE_TTL = 180  # 3 minutes — fast reaction to DB changes


def _load_registry() -> dict[str, dict]:
    """Load tld_registry from PostgreSQL. Returns {tld: {...}} dict."""
    from queries import get_db_conn
    registry = {}
    try:
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT tld, rdap_server, whois_disabled_at, whois_disabled_reason, origin
                    FROM tld_registry
                """)
                for row in cur.fetchall():
                    registry[row[0]] = {
                        'rdap_server': row[1],
                        'whois_disabled': row[2] is not None,
                        'whois_disabled_reason': row[3],
                        'origin': row[4],
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


def get_rdap_server(tld: str) -> str | None:
    """Get the RDAP server URL for a TLD, or None if unknown."""
    registry = _get_registry()
    entry = registry.get(tld.lower())
    if entry is None:
        return None
    return entry['rdap_server']


# ── TOS-covered TLD enforcement ─────────────────────────────────────────
#
# RDAP/WHOIS queries are only made to registry operators explicitly listed
# in our Terms of Service.  For uncovered TLDs, workers return DNS-only
# results (medium confidence).  The covered set is stored in Valkey so the
# Go workers can check it without DB access.

# RDAP server hostnames operated by our 26 named TOS operators.
# When you add a new operator to the TOS, add its hostname(s) here.
_TOS_COVERED_RDAP_HOSTS: set[str] = {
    # Verisign (.com, .net, .cc, .name + 12 others)
    'rdap.verisign.com', 'tld-rdap.verisign.com',
    # Identity Digital (.io, .co, .tv + 450 gTLDs)
    'rdap.identitydigital.services',
    # CentralNic (.xyz, .art + 90 gTLDs)
    'rdap.centralnic.com', 'rdap.centralnicregistry.com',
    # Nominet (.uk, .amazon, .aws + 80 gTLDs)
    'rdap.nominet.uk',
    # Google Registry (.dev, .app + 40 gTLDs)
    'pubapi.registry.google',
    # GMO Registry (.canon, .fujitsu + 40 gTLDs)
    'rdap.gmoregistry.net',
    # ZDNS (.top, .wang + 20 gTLDs)
    'rdap.zdnsgtld.com',
    # Tucows (.click, .link + 10 gTLDs)
    'rdap.tucowsregistry.net',
    # PIR (.org, .charity, .foundation + 8 gTLDs)
    'rdap.publicinterestregistry.org',
    # Radix (.online, .store, .site + 8 gTLDs)
    'rdap.radix.host',
    # GoDaddy/NeuStar (.biz)
    'rdap.nic.biz',
    # ccTLDs
    'rdap.nic.fr',              # AFNIC (.fr)
    'rdap.cctld.au',            # auDA (.au)
    'rdap.ca.fury.ca',          # CIRA (.ca)
    'rdap.sidn.nl',             # SIDN (.nl)
    'rdap.norid.no',            # Norid (.no)
    'rdap.dns.pl',              # NASK (.pl)
    'rdap.fi',                  # Traficom (.fi)
    'rdap.nic.cz',              # CZ.NIC (.cz)
    'rdap.sgnic.sg',            # SGNIC (.sg)
    'rdap.ta.sgnic.sg',         # SGNIC (.xn-- variants)
    'rdap.zh.sgnic.sg',         # SGNIC (.xn-- variants)
    'ccrdap.twnic.tw',          # TWNIC (.tw)
    'rdap.twnic.tw',            # TWNIC (.xn-- variant)
    'rdap.registro.br',         # Registro.br (.br)
    'rdap.nixiregistry.in',     # NIXI (.in)
    # AFNIC additional territories
    'rdap.nic.pm', 'rdap.nic.re', 'rdap.nic.wf', 'rdap.nic.yt',
    # JPRS (.jprs gTLD)
    'rdap.nic.jprs',
}

# TLDs explicitly covered regardless of RDAP hostname (ccTLDs where
# the RDAP hostname doesn't match a pattern above, or WHOIS-only TLDs).
_TOS_COVERED_TLDS_EXPLICIT: set[str] = {'us', 'de', 'jp'}

_VALKEY_COVERED_KEY = 'tos:covered_tlds'


def populate_covered_tlds_set() -> int:
    """Build the tos:covered_tlds Valkey set from the TLD registry.

    Called at API startup.  Workers SISMEMBER this set to decide whether
    to make RDAP/WHOIS queries for a given TLD.
    """
    from urllib.parse import urlparse
    from valkey_client import get_valkey

    registry = _get_registry()
    covered = set(_TOS_COVERED_TLDS_EXPLICIT)

    for tld, entry in registry.items():
        server = entry.get('rdap_server')
        if not server:
            continue
        host = urlparse(server).hostname
        if host and host in _TOS_COVERED_RDAP_HOSTS:
            covered.add(tld)

    r = get_valkey()
    pipe = r.pipeline(transaction=True)
    pipe.delete(_VALKEY_COVERED_KEY)
    if covered:
        pipe.sadd(_VALKEY_COVERED_KEY, *covered)
    pipe.execute()

    logger.info('Populated %s: %d covered TLDs (of %d total)',
                _VALKEY_COVERED_KEY, len(covered), len(registry))
    return len(covered)
