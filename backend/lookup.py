"""
Domain availability lookup pipeline.

Orchestrates the 3-step lookup:
  1. Valkey cache check
  2. DNS NS query to Unbound
  3. WHOIS/RDAP query to rust-whois (only for NXDOMAIN results)

Returns v7 response shape with confidence scoring.
"""

import logging
import os
import time
from datetime import datetime, timezone

import dns.resolver

from domain_cache import get_cached_domain, cache_domain
from dns_client import check_domain_dns
from whois_client import check_domain_whois
from rdap_stats import record_rdap_outcome
from tld_registry import is_whois_disabled

logger = logging.getLogger(__name__)

# Per-domain timing stats (populated during profiling)
_profiling_enabled = True


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
    t_start = time.monotonic()

    # ── Step 1: Cache check ──────────────────────────────────────
    t0 = time.monotonic()
    cached = get_cached_domain(domain)
    t_cache = time.monotonic() - t0
    if cached is not None:
        if _profiling_enabled:
            logger.info('PROFILE %s cache_hit=1 cache_ms=%.1f total_ms=%.1f',
                        domain, t_cache * 1000, t_cache * 1000)
        return cached

    # ── Step 2: DNS NS query ─────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    dns_result = check_domain_dns(domain, resolver)
    t_dns = time.monotonic() - t0

    available = dns_result.get('available')
    tld = dns_result.get('tld', '')
    error = dns_result.get('error')

    # DNS error (SERVFAIL / timeout) — return low confidence, don't cache
    if available is None:
        if _profiling_enabled:
            logger.info('PROFILE %s cache_ms=%.1f dns_ms=%.1f dns_status=%s whois_ms=0 total_ms=%.1f',
                        domain, t_cache * 1000, t_dns * 1000,
                        dns_result.get('dns_status', 'error'),
                        (time.monotonic() - t_start) * 1000)
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
        if _profiling_enabled:
            logger.info('PROFILE %s cache_ms=%.1f dns_ms=%.1f dns_status=%s whois_ms=0 total_ms=%.1f',
                        domain, t_cache * 1000, t_dns * 1000,
                        dns_result.get('dns_status', 'registered'),
                        (time.monotonic() - t_start) * 1000)
        return result

    # ── Step 3: WHOIS verification (DNS said NXDOMAIN) ───────────

    # Skip WHOIS entirely for TLDs where it's disabled (slow/broken)
    if is_whois_disabled(tld):
        result = {
            'domain': domain,
            'available': None,
            'confidence': 'low',
            'tld': tld,
            'source': 'dns',
            'checked_at': now,
            'cache_age_seconds': 0,
            'registration': None,
            'error': 'whois_disabled_for_this_tld',
        }
        if _profiling_enabled:
            logger.info('PROFILE %s cache_ms=%.1f dns_ms=%.1f dns_status=%s whois_ms=0 whois_outcome=tld_disabled total_ms=%.1f',
                        domain, t_cache * 1000, t_dns * 1000,
                        dns_result.get('dns_status', 'nxdomain'),
                        (time.monotonic() - t_start) * 1000)
        return result

    t0 = time.monotonic()
    whois_data = check_domain_whois(domain)
    t_whois = time.monotonic() - t0

    lookup_source = whois_data.get('lookup_source') if whois_data else None

    _profile_whois = lambda outcome: (
        logger.info('PROFILE %s cache_ms=%.1f dns_ms=%.1f dns_status=%s whois_ms=%.1f whois_outcome=%s total_ms=%.1f',
                    domain, t_cache * 1000, t_dns * 1000,
                    dns_result.get('dns_status', 'nxdomain'),
                    t_whois * 1000, outcome,
                    (time.monotonic() - t_start) * 1000)
        if _profiling_enabled else None
    )

    # RDAP definitively says domain not found — trust it, high confidence
    if lookup_source == 'rdap_domain_not_found':
        result = {
            'domain': domain,
            'available': True,
            'confidence': 'high',
            'tld': tld,
            'source': 'rdap',
            'checked_at': now,
            'cache_age_seconds': 0,
            'registration': None,
        }
        cache_domain(domain, result)
        _profile_whois('rdap_domain_not_found')
        record_rdap_outcome(tld, 'rdap_domain_not_found')
        return result

    # RDAP rate limited (429) — trust DNS NXDOMAIN, don't wait for WHOIS
    if lookup_source == 'rdap_rate_limited':
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
        cache_domain(domain, result)  # skipped (medium confidence)
        _profile_whois('rdap_rate_limited')
        record_rdap_outcome(tld, 'rdap_rate_limited')
        return result

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
        _profile_whois('registered')
        record_rdap_outcome(tld, 'rdap_success' if lookup_source == 'rdap' else 'whois_fallback')
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
        _profile_whois('available')
        record_rdap_outcome(tld, 'rdap_success' if lookup_source == 'rdap' else 'whois_fallback')
        return result

    # WHOIS failed/timed out — DNS NXDOMAIN is our only signal.
    # Downgrade to 'low' if health checks show DNS or WHOIS is degraded.
    dns_ok = os.environ.get('_DNS_HEALTHY', '1') == '1'
    whois_ok = os.environ.get('_WHOIS_HEALTHY', '1') == '1'
    confidence = 'medium' if (dns_ok and whois_ok) else 'low'
    result = {
        'domain': domain,
        'available': True,
        'confidence': confidence,
        'tld': tld,
        'source': 'dns',
        'checked_at': now,
        'cache_age_seconds': 0,
        'registration': None,
    }
    cache_domain(domain, result)  # skipped unless high confidence
    _profile_whois('failed')
    record_rdap_outcome(tld, 'rdap_error')
    return result
