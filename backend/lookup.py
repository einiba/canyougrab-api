"""
Domain availability lookup pipeline.

Orchestrates the 3-step lookup:
  1. Valkey cache check
  2. DNS NS query to Unbound
  3. WHOIS/RDAP query to rust-whois (only for NXDOMAIN results)

Returns v7 response shape with confidence scoring.
"""

import logging
from datetime import datetime, timezone

import dns.resolver

from domain_cache import get_cached_domain, cache_domain
from dns_client import check_domain_dns
from whois_client import check_domain_whois

logger = logging.getLogger(__name__)


def check_domain(domain: str, resolver: dns.resolver.Resolver) -> dict:
    """Full lookup pipeline for a single domain.

    Returns v7 response dict:
        {
            "domain": str,
            "available": bool | None,
            "confidence": "high" | "medium" | "low",
            "tld": str,
            "source": "cache" | "dns" | "whois",
            "checked_at": str (ISO 8601),
            "cache_age_seconds": int,
            "registration": {...} | None,
            "error": str (only when available is null),
        }
    """
    domain = domain.lower().strip().rstrip('.')

    # ── Step 1: Cache check ──────────────────────────────────────
    cached = get_cached_domain(domain)
    if cached is not None:
        return cached

    # ── Step 2: DNS NS query ─────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    dns_result = check_domain_dns(domain, resolver)

    available = dns_result.get('available')
    tld = dns_result.get('tld', '')
    error = dns_result.get('error')

    # DNS error (SERVFAIL / timeout) — return low confidence, don't cache
    if available is None:
        return {
            'domain': domain,
            'available': None,
            'confidence': 'low',
            'tld': tld,
            'source': 'dns',
            'checked_at': now,
            'cache_age_seconds': 0,
            'registration': None,
            'error': error or 'dns_error',
        }

    # Validation errors (invalid domain, missing TLD) — return as-is
    if error:
        return {
            'domain': domain,
            'available': available,
            'confidence': 'low',
            'tld': tld,
            'source': 'dns',
            'checked_at': now,
            'cache_age_seconds': 0,
            'registration': None,
            'error': error,
        }

    # Registered (NOERROR+NS or NoAnswer) — high confidence, cache and return
    if available is False:
        result = {
            'domain': domain,
            'available': False,
            'confidence': 'high',
            'tld': tld,
            'source': 'dns',
            'checked_at': now,
            'cache_age_seconds': 0,
            'registration': None,
        }
        cache_domain(domain, result)
        return result

    # ── Step 3: WHOIS verification (DNS said NXDOMAIN) ───────────
    whois_data = check_domain_whois(domain)

    if whois_data is not None and whois_data.get('expiration_date'):
        # WHOIS found a registration — DNS was stale/wrong
        result = {
            'domain': domain,
            'available': False,
            'confidence': 'high',
            'tld': tld,
            'source': 'whois',
            'checked_at': now,
            'cache_age_seconds': 0,
            'registration': {
                'registrar': whois_data.get('registrar'),
                'created_at': whois_data.get('creation_date'),
                'expires_at': whois_data.get('expiration_date'),
                'updated_at': whois_data.get('updated_date'),
            },
        }
        cache_domain(domain, result)
        return result

    # WHOIS confirms available (no record found)
    if whois_data is not None:
        result = {
            'domain': domain,
            'available': True,
            'confidence': 'high',
            'tld': tld,
            'source': 'whois',
            'checked_at': now,
            'cache_age_seconds': 0,
            'registration': None,
        }
        cache_domain(domain, result)
        return result

    # WHOIS failed/timed out — DNS NXDOMAIN is our only signal
    result = {
        'domain': domain,
        'available': True,
        'confidence': 'medium',
        'tld': tld,
        'source': 'dns',
        'checked_at': now,
        'cache_age_seconds': 0,
        'registration': None,
    }
    cache_domain(domain, result)
    return result
