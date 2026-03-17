"""
Domain availability lookup pipeline.

Orchestrates the 3-step lookup:
  1. Valkey cache check
  2. DNS NS query to Unbound
  3. WHOIS/RDAP query to rust-whois (only for NXDOMAIN results)

Results are cached in Valkey with smart TTLs.
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

    Returns a dict suitable for API response:
        {
            "domain": "example.com",
            "available": bool | None,
            "tld": str,
            "registrar": str | None,
            "expiration_date": str | None,
            "name_servers": list[str] | None,
            "source": "cache" | "dns" | "dns+whois",
            "cached": bool,
            ...
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
    tld = dns_result.get('tld')
    error = dns_result.get('error')

    # DNS error — return immediately, don't cache
    if available is None:
        dns_result['source'] = 'dns'
        dns_result['cached'] = False
        return dns_result

    # Registered (NOERROR+NS or NoAnswer) — cache and return
    if available is False:
        result = {
            'domain': domain,
            'available': False,
            'tld': tld,
            'dns_status': dns_result.get('dns_status', 'noerror_ns'),
            'source': 'dns',
            'cached': False,
            'dns_checked_at': now,
        }
        cache_domain(domain, result)
        return result

    # Validation errors — return as-is, don't cache
    if error:
        dns_result['source'] = 'dns'
        dns_result['cached'] = False
        return dns_result

    # ── Step 3: WHOIS verification (DNS said NXDOMAIN) ───────────
    whois_data = check_domain_whois(domain)

    if whois_data is not None and whois_data.get('expiration_date'):
        # WHOIS found a registration — DNS was stale/wrong
        result = {
            'domain': domain,
            'available': False,
            'tld': tld,
            'dns_status': 'nxdomain',
            'registrar': whois_data.get('registrar'),
            'creation_date': whois_data.get('creation_date'),
            'expiration_date': whois_data.get('expiration_date'),
            'updated_date': whois_data.get('updated_date'),
            'name_servers': whois_data.get('name_servers'),
            'status_codes': whois_data.get('status'),
            'whois_server': whois_data.get('whois_server'),
            'source': 'dns+whois',
            'cached': False,
            'dns_checked_at': now,
            'whois_checked_at': now,
        }
        cache_domain(domain, result)
        return result

    # WHOIS also confirms available (or WHOIS failed/returned no expiry)
    result = {
        'domain': domain,
        'available': True,
        'tld': tld,
        'dns_status': 'nxdomain',
        'source': 'dns+whois' if whois_data is not None else 'dns',
        'cached': False,
        'dns_checked_at': now,
    }
    if whois_data is not None:
        result['whois_checked_at'] = now

    cache_domain(domain, result)
    return result
