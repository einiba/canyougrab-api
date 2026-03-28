"""
Domain result enrichment — adds dns/whois/intelligence sections to v7 results.

Called as a post-processing step when ?enrichment=full is requested.
Does NOT touch the core lookup pipeline; enrichment is always additive.
"""

import concurrent.futures
import logging
import re
from datetime import datetime, timezone

import dns.resolver

logger = logging.getLogger(__name__)

DNS_TIMEOUT = 2.0  # seconds per NS lookup

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

    # Netlify
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

    # GoDaddy / Sucuri
    (re.compile(r'(aron|bart|cass|dana|ella|finn|gary|hope)\.ns\.cloudflare\.com$', re.I), 'Cloudflare', 'dns_hosting'),
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


def _lookup_ns_sync(domain: str) -> list[str]:
    """Blocking NS record lookup. Run in a thread pool."""
    try:
        r = dns.resolver.Resolver()
        r.timeout = DNS_TIMEOUT
        r.lifetime = DNS_TIMEOUT
        answers = r.resolve(domain, 'NS')
        return sorted(str(rr.target).rstrip('.').lower() for rr in answers)
    except Exception:
        return []


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


def enrich_result(result: dict, nameservers: list[str] | None = None) -> dict:
    """Transform a v7 flat result into the enhanced sectioned format.

    Caller may pass pre-fetched nameservers; otherwise they are omitted
    (caller should use enrich_results_bulk for efficient parallel NS fetches).
    """
    domain = result.get('domain', '')
    available = result.get('available')
    tld = result.get('tld', '')
    registration = result.get('registration') or {}
    source = result.get('source', '')
    t_start = result.get('_lookup_start_ms', 0)
    t_end = result.get('_lookup_end_ms', 0)
    lookup_ms = round(t_end - t_start) if (t_start and t_end) else None

    # --- dns section ---
    dns_section: dict = {
        'status': 'available' if available else ('registered' if available is False else 'error'),
    }
    if nameservers is not None:
        dns_section['nameservers'] = nameservers
        provider, category, parked = derive_provider(nameservers)
        dns_section['provider'] = provider
        dns_section['ns_count'] = len(nameservers)
    else:
        parked = False
        provider = 'unknown'

    # --- whois section ---
    whois_section: dict | None = None
    if registration:
        whois_section = {
            'registrar': registration.get('registrar'),
            'created_at': registration.get('created_at'),
            'expires_at': registration.get('expires_at'),
            'updated_at': registration.get('updated_at'),
            'source': source if source in ('rdap', 'whois') else None,
        }
    elif source in ('rdap', 'whois'):
        whois_section = {'source': source}

    # --- intelligence section ---
    intelligence_section: dict | None = None
    if available is False:
        created_at = registration.get('created_at') if registration else None
        expires_at = registration.get('expires_at') if registration else None
        age_days = _days_since(created_at)
        expires_in_days = _days_until(expires_at)

        if parked:
            category_label = 'parked'
        elif expires_in_days is not None and expires_in_days < 30:
            category_label = 'expiring'
        elif nameservers and len(nameservers) >= 2 and provider != 'unknown':
            category_label = 'active'
        else:
            category_label = 'unknown'

        intelligence_section = {
            'category': category_label,
            'parked': parked,
            'domain_age_days': age_days,
            'expires_in_days': expires_in_days,
        }
        if provider != 'unknown':
            intelligence_section['hosting_provider'] = provider

    # --- _meta section ---
    meta_section: dict = {
        'source': source,
        'cache_age_seconds': result.get('cache_age_seconds', 0),
    }
    if lookup_ms is not None:
        meta_section['lookup_time_ms'] = lookup_ms

    # Build output — always include top-level availability fields
    enriched: dict = {
        'domain': domain,
        'available': available,
        'confidence': result.get('confidence'),
        'tld': tld,
        'checked_at': result.get('checked_at'),
    }
    if result.get('error'):
        enriched['error'] = result['error']
    enriched['dns'] = dns_section
    if whois_section:
        enriched['whois'] = whois_section
    if intelligence_section:
        enriched['intelligence'] = intelligence_section
    enriched['_meta'] = meta_section

    return enriched


def enrich_results_bulk(results: list[dict]) -> list[dict]:
    """Enrich a list of results into the sectioned format (?enrichment=true).

    NS records come from the worker result (populated by the Go worker's
    LookupNS on bloom/DNS hits, cached in Valkey dom:{domain} hashes).
    Falls back to live DNS for registered domains missing NS data.

    No Postgres tables are used — all enrichment data lives in Valkey.
    """
    registered = [r for r in results if r.get('available') is False]

    # Build NS map from worker results + live DNS fallback
    ns_map: dict[str, list[str]] = {}

    for r in registered:
        ns = r.get('nameservers')
        if ns and isinstance(ns, list):
            ns_map[r['domain']] = ns

    # Live DNS fallback for registered domains missing NS (e.g., old cache entries)
    needs_live_dns = [r for r in registered if r['domain'] not in ns_map]
    if needs_live_dns:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, len(needs_live_dns))) as pool:
            futures = {pool.submit(_lookup_ns_sync, r['domain']): r['domain'] for r in needs_live_dns}
            for fut in concurrent.futures.as_completed(futures, timeout=DNS_TIMEOUT + 1):
                domain = futures[fut]
                try:
                    ns_map[domain] = fut.result()
                except Exception:
                    ns_map[domain] = []

    enriched = []
    for r in results:
        ns = ns_map.get(r['domain']) or r.get('nameservers')
        enriched.append(enrich_result(r, nameservers=ns))
    return enriched


def enrich_results_inline(results: list[dict]) -> list[dict]:
    """Add lightweight enrichment fields to every result — no I/O.

    Uses nameservers already provided by the Go worker to derive:
    - parked: bool
    - hosting_provider: str | None
    - domain_age_days: int | None
    - expires_in_days: int | None

    This is pure computation (regex matching + date math) and runs in <1ms.
    Called on every response, not gated by ?enrichment=true.
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
