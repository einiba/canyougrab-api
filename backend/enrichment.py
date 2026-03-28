"""
Domain result enrichment — parking/for-sale detection, provider ID, domain age.

Runs on every API response as pure computation (regex + date math).
No I/O, no DNS lookups, no database queries — uses data already in the
worker result (nameservers from Go worker's LookupNS, dates from RDAP/WHOIS).
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── NS provider database ─────────────────────────────────────────────────
#
# Categories:
#   dns_hosting  — DNS provider (Cloudflare, Route 53, etc.)
#   registrar    — Registrar default NS (GoDaddy, Namecheap, etc.)
#   self_hosted  — Company runs its own NS
#   for_sale     — Domain marketplace (definitely listed for sale)
#   parking      — Ad-revenue parking (may or may not be for sale)

_NS_PROVIDERS: list[tuple[re.Pattern, str, str]] = [
    # ── DNS hosting providers ──
    (re.compile(r'ns[1-4]\.google\.com$', re.I), 'Google', 'self_hosted'),
    (re.compile(r'ns[1-4]\.googledomains\.com$', re.I), 'Google Domains', 'dns_hosting'),
    (re.compile(r'\w+\.ns\.cloudflare\.com$', re.I), 'Cloudflare', 'dns_hosting'),
    (re.compile(r'ns-\d+\.awsdns-\d+\.(com|net|org|co\.uk)$', re.I), 'AWS Route 53', 'dns_hosting'),
    (re.compile(r'ns[1-4]-\d+\.azure-dns\.(com|net|org|info)$', re.I), 'Azure DNS', 'dns_hosting'),
    (re.compile(r'ns[1-4]\.squarespace\.com$', re.I), 'Squarespace', 'dns_hosting'),
    (re.compile(r'ns[1-9]\.wixdns\.net$', re.I), 'Wix', 'dns_hosting'),
    (re.compile(r'ns[1-9]\.myshopify\.com$', re.I), 'Shopify', 'dns_hosting'),
    (re.compile(r'(ns[1-9]|a|b)\.vercel-dns\.com$', re.I), 'Vercel', 'dns_hosting'),
    (re.compile(r'dns[1-9]\.p\d+\.nsone\.net$', re.I), 'NS1 / Netlify', 'dns_hosting'),
    (re.compile(r'ns[1-9]\.hostinger\.com$', re.I), 'Hostinger', 'dns_hosting'),
    (re.compile(r'ns\d+\.digitalocean\.com$', re.I), 'DigitalOcean', 'dns_hosting'),
    (re.compile(r'ns[1-4]\.dnsimple\.com$', re.I), 'DNSimple', 'dns_hosting'),
    (re.compile(r'ns[1-4]\.hover\.com$', re.I), 'Hover', 'dns_hosting'),
    (re.compile(r'ns[1-4]\.linode\.com$', re.I), 'Linode', 'dns_hosting'),
    (re.compile(r'[a-z]+\.dnsmadeeasy\.com$', re.I), 'DNS Made Easy', 'dns_hosting'),
    (re.compile(r'pdns\d+\.ultradns\.(com|net|org)$', re.I), 'UltraDNS', 'dns_hosting'),
    (re.compile(r'ns[1-4]\.afraid\.org$', re.I), 'FreeDNS', 'dns_hosting'),

    # ── Registrar default NS ──
    (re.compile(r'ns\d+\.domaincontrol\.com$', re.I), 'GoDaddy', 'registrar'),
    (re.compile(r'(dns[1-9]|ns[1-9])\.registrar-servers\.com$', re.I), 'Namecheap', 'registrar'),
    (re.compile(r'ns[1-9]\.dreamhost\.com$', re.I), 'DreamHost', 'registrar'),
    (re.compile(r'ns\d+\.name-services\.com$', re.I), 'Enom', 'registrar'),
    (re.compile(r'ns[1-4]\.porkbun\.com$', re.I), 'Porkbun', 'registrar'),
    (re.compile(r'ns[1-4]\.dynadot\.com$', re.I), 'Dynadot', 'registrar'),
    (re.compile(r'ns[1-4]\.namebrightdns\.com$', re.I), 'NameBright', 'registrar'),
    (re.compile(r'ns\d+\.1und1\.de$', re.I), 'IONOS', 'registrar'),
    (re.compile(r'ns\d+\.ui-dns\.(com|de|org|biz)$', re.I), 'IONOS', 'registrar'),
    (re.compile(r'ns[1-4]\.gandi\.net$', re.I), 'Gandi', 'registrar'),
    (re.compile(r'ns\d+\.ovh\.(net|com)$', re.I), 'OVH', 'registrar'),
    (re.compile(r'ns[1-4]\.epik\.com$', re.I), 'Epik', 'registrar'),
    (re.compile(r'ns[1-4]\.opensrs\.net$', re.I), 'OpenSRS / Tucows', 'registrar'),

    # ── Marketplace services (for_sale — definitely listed for sale) ──
    (re.compile(r'ns[1-9]\.dan\.com$', re.I), 'Dan.com', 'for_sale'),
    (re.compile(r'ns[1-9]\.undeveloped\.com$', re.I), 'Dan.com', 'for_sale'),
    (re.compile(r'ns[1-9]\.park\.do$', re.I), 'Dan.com', 'for_sale'),
    (re.compile(r'ns[1-9]\.afternic\.com$', re.I), 'Afternic', 'for_sale'),
    (re.compile(r'ns[1-9]\.eftydns\.com$', re.I), 'Efty', 'for_sale'),
    (re.compile(r'ns[1-9]\.squadhelp\.com$', re.I), 'Squadhelp', 'for_sale'),
    (re.compile(r'ns[1-9]\.hugedomains\.com$', re.I), 'HugeDomains', 'for_sale'),
    (re.compile(r'ns[1-9]\.domainmarket\.com$', re.I), 'DomainMarket', 'for_sale'),
    (re.compile(r'ns[1-9]\.brandshelter\.com$', re.I), 'BrandShelter', 'for_sale'),
    (re.compile(r'ns[1-9]\.sav\.com$', re.I), 'Sav.com', 'for_sale'),
    (re.compile(r'ns[1-9]\.uniregistry\.net$', re.I), 'Uniregistry', 'for_sale'),
    (re.compile(r'ns[1-9]\.namefind\.com$', re.I), 'NameFind', 'for_sale'),
    (re.compile(r'domain-for-sale\.(se|at|eu)$', re.I), 'Domain For Sale', 'for_sale'),
    (re.compile(r'ns[1-9]\.domainprofi\.de$', re.I), 'DomainProfi', 'for_sale'),
    (re.compile(r'ns[1-9]\.buydomains\.com$', re.I), 'BuyDomains', 'for_sale'),

    # ── Parking services (parking — ad revenue, may or may not be for sale) ──
    (re.compile(r'(ns|cns)[1-9]\.sedoparking\.com$', re.I), 'Sedo', 'parking'),
    (re.compile(r'ns[1-9]\.parkingcrew\.net$', re.I), 'ParkingCrew', 'parking'),
    (re.compile(r'ns[1-9]\.above\.com$', re.I), 'Above.com', 'parking'),
    (re.compile(r'ns[1-9]\.bodis\.com$', re.I), 'Bodis', 'parking'),
    (re.compile(r'ns\d+\.cashparking\.com$', re.I), 'GoDaddy CashParking', 'parking'),
    (re.compile(r'ns\d+\.smartname\.com$', re.I), 'GoDaddy CashParking', 'parking'),
    (re.compile(r'ns[1-9]\.parklogic\.com$', re.I), 'ParkLogic', 'parking'),
    (re.compile(r'ns[1-9]\.voodoo\.com$', re.I), 'Voodoo', 'parking'),
    (re.compile(r'ns\d+\.dsredirection\.(com|net)$', re.I), 'DS Redirection', 'parking'),
    (re.compile(r'ns[1-9]\.domainnamesales\.com$', re.I), 'Domain Name Sales', 'parking'),
    (re.compile(r'ns[1-9]\.domainparkingserver\.net$', re.I), 'DomainParkingServer', 'parking'),
    (re.compile(r'ns[1-9]\.parkpage\.(com|net)$', re.I), 'ParkPage', 'parking'),
    (re.compile(r'ns[1-9]\.ztomy\.com$', re.I), 'Ztomy', 'parking'),
    (re.compile(r'ns[1-9]\.realtime\.at$', re.I), 'Realtime', 'parking'),
    (re.compile(r'ns[1-9]\.dopa\.com$', re.I), 'DOPA', 'parking'),
    (re.compile(r'ns[1-9]\.rookdns\.com$', re.I), 'RookDNS', 'parking'),
    (re.compile(r'ns[1-9]\.itidns\.com$', re.I), 'ITI DNS', 'parking'),
    (re.compile(r'ns[1-9]\.trafficz\.com$', re.I), 'Trafficz', 'parking'),
    (re.compile(r'ns[1-9]\.namedrive\.(com|net)$', re.I), 'NameDrive', 'parking'),
    (re.compile(r'ns[1-9]\.skenzo\.com$', re.I), 'Skenzo', 'parking'),
    (re.compile(r'ns[1-9]\.tonic\.to$', re.I), 'Tonic', 'parking'),
]


# ── Sale URL templates ────────────────────────────────────────────────────
# Marketplaces with public buy/check pages — no auth required to view.

_SALE_URLS: dict[str, str | None] = {
    # Marketplace (for_sale) — direct buy page
    'Dan.com':        'https://dan.com/buy-domain/{domain}',
    'Afternic':       'https://www.afternic.com/domain/{domain}',
    'HugeDomains':    'https://www.hugedomains.com/domain_profile.cfm?d={name}&e={ext}',
    'BuyDomains':     'https://www.buydomains.com/{domain}',
    'Sav.com':        'https://www.sav.com/domain/{domain}',
    # Parking (parking) — listing page (may or may not be for sale)
    'Sedo':           'https://sedo.com/search/details.php4?domain={domain}',
    'Bodis':          'https://www.bodis.com/domain/{domain}',
}


def _sale_url(domain: str, platform: str) -> str | None:
    """Generate the public sale/listing URL for a domain on a known platform."""
    template = _SALE_URLS.get(platform)
    if not template:
        return None
    parts = domain.rsplit('.', 1)
    name = parts[0] if len(parts) == 2 else domain
    ext = parts[1] if len(parts) == 2 else ''
    return template.format(domain=domain, name=name, ext=ext)


# ── Provider detection ────────────────────────────────────────────────────

def derive_provider(nameservers: list[str]) -> tuple[str, str, bool, bool | None]:
    """Return (provider_name, category, is_parked, is_for_sale) from nameserver hostnames.

    is_for_sale:
      True  = marketplace NS (definitely listed for sale)
      None  = parking NS (possibly for sale, can't confirm from DNS)
      False = not on any known sale/parking platform
    """
    if not nameservers:
        return 'unknown', 'unknown', False, False

    for ns in nameservers:
        ns = ns.lower().rstrip('.')
        for pattern, provider, category in _NS_PROVIDERS:
            if pattern.search(ns):
                is_parked = category in ('parking', 'for_sale')
                if category == 'for_sale':
                    is_for_sale = True
                elif category == 'parking':
                    is_for_sale = None  # possibly for sale
                else:
                    is_for_sale = False
                return provider, category, is_parked, is_for_sale

    return 'unknown', 'unknown', False, False


# ── Date helpers ──────────────────────────────────────────────────────────

def _days_since(iso_str: str | None) -> int | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _days_until(iso_str: str | None) -> int | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return None


# ── Main enrichment ───────────────────────────────────────────────────────

def enrich_results(results: list[dict]) -> list[dict]:
    """Add enrichment fields to every result — no I/O.

    Fields added:
    - parked: bool — domain is on a parking or marketplace service
    - for_sale: true/false/null — true=marketplace, null=possibly (parking), false=no
    - sale_platform: str | None — marketplace/parking service name
    - sale_url: str | None — direct link to buy/check page
    - hosting_provider: str | None — detected DNS provider
    - domain_age_days: int | None
    - expires_in_days: int | None
    """
    for r in results:
        domain = r.get('domain', '')
        ns = r.get('nameservers')

        if ns and isinstance(ns, list):
            provider, _category, parked, for_sale = derive_provider(ns)
        else:
            provider, parked, for_sale = None, False, False

        r['parked'] = parked
        r['for_sale'] = for_sale
        r['hosting_provider'] = provider if provider and provider != 'unknown' else None

        # Sale platform + URL for parking and marketplace services
        if parked and provider and provider != 'unknown':
            r['sale_platform'] = provider
            r['sale_url'] = _sale_url(domain, provider)
        else:
            r['sale_platform'] = None
            r['sale_url'] = None

        # Domain age + expiry
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
