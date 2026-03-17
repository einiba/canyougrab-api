"""
Valkey-based domain lookup cache.

Stores aggregated DNS + WHOIS results as hashes keyed by dom:{domain}.
TTL strategy:
  - Registered + has expiry date: min(seconds_to_expiry, 7 days)
  - Registered + no expiry:       24 hours
  - Available (NXDOMAIN):         5 minutes (matches Unbound negative TTL)
  - DNS errors:                   not cached
"""

import json
import logging
from datetime import datetime, timezone

from valkey_client import get_valkey

logger = logging.getLogger(__name__)

# TTL constants (seconds)
CACHE_AVAILABLE_TTL = 300          # 5 minutes for available domains
CACHE_REGISTERED_DEFAULT_TTL = 86400  # 24 hours when no expiry date
CACHE_REGISTERED_MAX_TTL = 604800    # 7 days cap even if expiry is years away


def _compute_ttl(available: bool | None, expiration_date: str | None) -> int | None:
    """Compute the Valkey key TTL based on availability and WHOIS expiry.

    Returns TTL in seconds, or None if the result should not be cached.
    """
    # Never cache errors / unknowns
    if available is None:
        return None

    # Available domains: short TTL
    if available is True:
        return CACHE_AVAILABLE_TTL

    # Registered: use expiry if available, otherwise default
    if expiration_date:
        try:
            exp_dt = datetime.fromisoformat(expiration_date.replace('Z', '+00:00'))
            seconds_left = int((exp_dt - datetime.now(timezone.utc)).total_seconds())
            if seconds_left > 0:
                return min(seconds_left, CACHE_REGISTERED_MAX_TTL)
        except (ValueError, TypeError):
            pass

    return CACHE_REGISTERED_DEFAULT_TTL


def get_cached_domain(domain: str) -> dict | None:
    """Look up a domain in the cache.

    Returns a dict with all cached fields, or None on cache miss.
    The returned dict always includes 'cached': True.
    """
    r = get_valkey()
    key = f'dom:{domain}'

    try:
        data = r.hgetall(key)
    except Exception as e:
        logger.warning('Cache read error for %s: %s', domain, e)
        return None

    if not data:
        return None

    # Deserialize
    result = {
        'domain': domain,
        'cached': True,
        'source': data.get('source', 'cache'),
    }

    # available: stored as string "true"/"false"/"null"
    avail_str = data.get('available')
    if avail_str == 'true':
        result['available'] = True
    elif avail_str == 'false':
        result['available'] = False
    else:
        result['available'] = None

    # Simple string fields
    for field in ('tld', 'dns_status', 'registrar', 'creation_date',
                  'expiration_date', 'updated_date', 'whois_server',
                  'dns_checked_at', 'whois_checked_at', 'error'):
        val = data.get(field)
        if val:
            result[field] = val

    # JSON array fields
    for field in ('name_servers', 'status_codes'):
        val = data.get(field)
        if val:
            try:
                result[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass

    return result


def cache_domain(domain: str, data: dict) -> None:
    """Write a domain lookup result to the cache.

    data should include at minimum:
        available: bool | None
        source: str ('dns', 'whois', 'dns+whois')
    And optionally any of the enrichment fields.
    """
    available = data.get('available')
    expiration_date = data.get('expiration_date')
    ttl = _compute_ttl(available, expiration_date)

    if ttl is None:
        return  # Don't cache errors

    r = get_valkey()
    key = f'dom:{domain}'

    # Serialize available as string
    if available is True:
        avail_str = 'true'
    elif available is False:
        avail_str = 'false'
    else:
        avail_str = 'null'

    mapping = {'available': avail_str}

    # Simple string fields
    for field in ('tld', 'dns_status', 'registrar', 'creation_date',
                  'expiration_date', 'updated_date', 'whois_server',
                  'source', 'dns_checked_at', 'whois_checked_at', 'error'):
        val = data.get(field)
        if val is not None:
            mapping[field] = str(val)

    # JSON array fields
    for field in ('name_servers', 'status_codes'):
        val = data.get(field)
        if val is not None:
            mapping[field] = json.dumps(val)

    try:
        pipe = r.pipeline(transaction=True)
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception as e:
        logger.warning('Cache write error for %s: %s', domain, e)
