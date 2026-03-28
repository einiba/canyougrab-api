"""
Domain result enrichment — parking detection, provider identification, domain age.

Runs on every API response as pure computation (regex + date math).
No I/O, no DNS lookups, no database queries — uses data already in the
worker result (nameservers from Go worker's LookupNS, dates from RDAP/WHOIS).
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# NS hostname pattern → provider name + category
# category: "dns_hosting" | "registrar" | "parking" | "cdn" | "self_hosted"
_NS_PROVIDERS: list[tuple[re.Pattern, str, str]] = [
    # Google
    (re.compile(r'ns[1-4]\.google\.com$', re.I), 'Google', 'self_hosted'),
    (re.compile(r'ns[1-4]\.googledomains\.com$', re.I), 'Google Domains', 'dns_hosting'),

    # Cloudflare
    (re.compile(r'\w+\.ns\.cloudflare\.com$', re.I), 'Cloudflare', 'dns_hosting'),

    # AWS Route 53
    (re.compile(r'ns-\d+\.awsdns-\d+\.(com|net|org|co\.uk)$', re.I), 'AWS Route 53', 'dns_hosting'),

    # Azure
    (re.compile(r'ns[1-4]-\d+\.azure-dns\.(com|net|org|info)$', re.I), 'Azure DNS', 'dns_hosting'),

    # GoDaddy
    (re.compile(r'ns\d+\.domaincontrol\.com$', re.I), 'GoDaddy', 'registrar'),

    # Namecheap
    (re.compile(r'(dns[1-9]|ns[1-9])\.registrar-servers\.com$', re.I), 'Namecheap', 'registrar'),

    # Squarespace (formerly Google Domains)
    (re.compile(r'ns[1-4]\.squarespace\.com$', re.I), 'Squarespace', 'dns_hosting'),

    # Wix
    (re.compile(r'ns[1-9]\.wixdns\.net$', re.I), 'Wix', 'dns_hosting'),

    # Shopify
    (re.compile(r'ns[1-9]\.myshopify\.com$', re.I), 'Shopify', 'dns_hosting'),

    # Vercel
    (re.compile(r'(ns[1-9]|a|b)\.vercel-dns\.com$', re.I), 'Vercel', 'dns_hosting'),

    # Netlify / NS1
    (re.compile(r'dns[1-9]\.p\d+\.nsone\.net$', re.I), 'NS1 / Netlify', 'dns_hosting'),

    # Parking services
    (re.compile(r'ns[1-9]\.sedoparking\.com$', re.I), 'Sedo', 'parking'),
    (re.compile(r'ns[1-9]\.parkingcrew\.net$', re.I), 'ParkingCrew', 'parking'),
    (re.compile(r'ns[1-9]\.above\.com$', re.I), 'Above.com', 'parking'),
    (re.compile(r'ns[1-9]\.bodis\.com$', re.I), 'Bodis', 'parking'),
    (re.compile(r'ns[1-9]\.afternic\.com$', re.I), 'Afternic', 'parking'),
    (re.compile(r'ns[1-9]\.hugedomains\.com$', re.I), 'HugeDomains', 'parking'),
    (re.compile(r'ns[1-9]\.dan\.com$', re.I), 'Dan.com', 'parking'),
    (re.compile(r'ns[1-9]\.undeveloped\.com$', re.I), 'Dan.com', 'parking'),
    (re.compile(r'ns[1-9]\.efty\.com$', re.I), 'Efty', 'parking'),

    # Hostinger
    (re.compile(r'ns[1-9]\.hostinger\.com$', re.I), 'Hostinger', 'dns_hosting'),
]


def derive_provider(nameservers: list[str]) -> tuple[str, str, bool]:
    """Return (provider_name, category, is_parked) from a list of nameserver hostnames."""
    if not nameservers:
        return 'unknown', 'unknown', False

    for ns in nameservers:
        ns = ns.lower().rstrip('.')
        for pattern, provider, category in _NS_PROVIDERS:
            if pattern.search(ns):
                return provider, category, category == 'parking'

    return 'unknown', 'unknown', False


def _days_since(iso_str: str | None) -> int | None:
    """Days since an ISO 8601 date string."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _days_until(iso_str: str | None) -> int | None:
    """Days until an ISO 8601 date string (negative if past)."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return None


def enrich_results(results: list[dict]) -> list[dict]:
    """Add enrichment fields to every result — no I/O.

    Uses nameservers already provided by the Go worker to derive:
    - parked: bool
    - hosting_provider: str | None
    - domain_age_days: int | None
    - expires_in_days: int | None

    Pure computation (regex matching + date math), runs in <1ms.
    """
    for r in results:
        ns = r.get('nameservers')

        # Provider + parking detection from NS records
        if ns and isinstance(ns, list):
            provider, _category, parked = derive_provider(ns)
        else:
            provider, parked = None, False

        r['parked'] = parked
        r['hosting_provider'] = provider if provider != 'unknown' else None

        # Domain age + expiry from registration dates (already in result)
        reg = r.get('registration')
        if isinstance(reg, dict):
            r['domain_age_days'] = _days_since(
                reg.get('created_at') or reg.get('creation_date')
            )
            r['expires_in_days'] = _days_until(
                reg.get('expires_at') or reg.get('expiration_date')
            )
        else:
            r['domain_age_days'] = None
            r['expires_in_days'] = None

    return results


# Backwards compat alias
enrich_results_inline = enrich_results
