"""
Valkey-based domain lookup cache.

Stores aggregated DNS + WHOIS results as hashes keyed by dom:{domain}.
TTL strategy:
  - Registered + has expiry date: min(seconds_to_expiry, 7 days)
  - Registered + no expiry:       24 hours
  - Available (NXDOMAIN):         5 minutes (matches Unbound negative TTL)
  - DNS errors:                   not cached

Cache reads return v7 response shape with:
  - confidence (with staleness downgrade for entries > 24h old)
  - nested registration object
  - checked_at / cache_age_seconds
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
STALENESS_THRESHOLD = 86400          # 24 hours — after this, downgrade confidence


def _compute_ttl(available: bool | None, expiration_date: str | None) -> int | None:
    """Compute the Valkey key TTL based on availability and WHOIS expiry."""
    if available is None:
        return None

    if available is True:
        return CACHE_AVAILABLE_TTL

    if expiration_date:
        try:
            exp_dt = datetime.fromisoformat(expiration_date.replace('Z', '+00:00'))
            seconds_left = int((exp_dt - datetime.now(timezone.utc)).total_seconds())
            if seconds_left > 0:
                return min(seconds_left, CACHE_REGISTERED_MAX_TTL)
        except (ValueError, TypeError):
            pass

    return CACHE_REGISTERED_DEFAULT_TTL


_CONFIDENCE_DOWNGRADE = {'high': 'medium', 'medium': 'low', 'low': 'low'}


def get_cached_domain(domain: str) -> dict | None:
    """Look up a domain in the cache. Returns v7-shaped dict or None."""
    r = get_valkey()
    key = f'dom:{domain}'

    try:
        data = r.hgetall(key)
    except Exception as e:
        logger.warning('Cache read error for %s: %s', domain, e)
        return None

    if not data:
        return None

    # Parse available
    avail_str = data.get('available')
    if avail_str == 'true':
        available = True
    elif avail_str == 'false':
        available = False
    else:
        available = None

    # Compute cache_age_seconds
    cached_at = data.get('cached_at', '')
    cache_age_seconds = 0
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            cache_age_seconds = max(0, int((datetime.now(timezone.utc) - cached_dt).total_seconds()))
        except (ValueError, TypeError):
            pass

    # Compute confidence with staleness downgrade
    original_confidence = data.get('confidence', 'medium')
    if cache_age_seconds > STALENESS_THRESHOLD:
        confidence = _CONFIDENCE_DOWNGRADE.get(original_confidence, 'low')
    else:
        confidence = original_confidence

    result = {
        'domain': domain,
        'available': available,
        'confidence': confidence,
        'tld': data.get('tld', ''),
        'source': 'cache',
        'checked_at': cached_at,
        'cache_age_seconds': cache_age_seconds,
    }

    # Build registration object for taken domains
    if available is False:
        registrar = data.get('registrar')
        created_at = data.get('created_at')
        expires_at = data.get('expires_at')
        updated_at = data.get('updated_at')
        if registrar or created_at or expires_at or updated_at:
            result['registration'] = {
                'registrar': registrar or None,
                'created_at': created_at or None,
                'expires_at': expires_at or None,
                'updated_at': updated_at or None,
            }
        else:
            result['registration'] = None
    else:
        result['registration'] = None

    # Error field
    error = data.get('error')
    if error:
        result['error'] = error

    return result


def cache_domain(domain: str, data: dict) -> None:
    """Write a domain lookup result to the cache.

    Expects v7-shaped data with 'available', 'confidence', 'source',
    'checked_at', and optionally 'registration' dict and 'error'.
    """
    available = data.get('available')

    # Extract expires_at for TTL computation
    reg = data.get('registration') or {}
    expires_at = reg.get('expires_at')
    ttl = _compute_ttl(available, expires_at)

    if ttl is None:
        return

    r = get_valkey()
    key = f'dom:{domain}'

    # Serialize available
    if available is True:
        avail_str = 'true'
    elif available is False:
        avail_str = 'false'
    else:
        avail_str = 'null'

    mapping = {
        'available': avail_str,
        'cached_at': data.get('checked_at', datetime.now(timezone.utc).isoformat()),
        'confidence': data.get('confidence', 'medium'),
        'original_source': data.get('source', 'dns'),
    }

    # Simple string fields
    for field in ('tld', 'error'):
        val = data.get(field)
        if val is not None:
            mapping[field] = str(val)

    # Flatten registration object into cache hash
    if reg:
        for field in ('registrar', 'created_at', 'expires_at', 'updated_at'):
            val = reg.get(field)
            if val is not None:
                mapping[field] = str(val)

    try:
        pipe = r.pipeline(transaction=True)
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception as e:
        logger.warning('Cache write error for %s: %s', domain, e)
