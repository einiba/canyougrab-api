#!/usr/bin/env python3
"""
Seed brand/closed TLDs in the tld_registry table.

Brand TLDs are owned by corporations and do not allow public domain
registrations (e.g., .nike, .apple, .google, .amazon).  Domains under
these TLDs should return available=null with error=brand_tld.

Detection heuristics:
  1. TLDs using rdap.nic.{tld} pattern (single-operator brand registries)
  2. Known brand TLDs on multi-tenant registries (Nominet, GMO, etc.)
  3. Subtract known open TLDs that use rdap.nic.{tld} pattern

Usage:
  python3 scripts/seed_brand_tlds.py          # dry run
  python3 scripts/seed_brand_tlds.py --apply  # write to DB
"""

import os
import sys
import psycopg2
from urllib.parse import urlparse


# ── Known open TLDs that use rdap.nic.{tld} pattern ──────────────────────
# These are NOT brand TLDs despite having their own RDAP server.

KNOWN_OPEN_TLDS = {
    # Open gTLDs with public registration
    'abogado', 'accountant', 'ad', 'adult', 'africa', 'alsace',
    'amsterdam', 'ar', 'arab', 'as', 'barcelona', 'bayern', 'bcn',
    'beer', 'berlin', 'bible', 'bid', 'biz', 'blackfriday', 'boston',
    'brussels', 'buzz', 'bzh', 'capetown', 'casa', 'cat', 'catholic',
    'club', 'cloud', 'cm', 'compare', 'cooking', 'corsica', 'courses',
    'cpa', 'cr', 'cricket', 'cx', 'date', 'design', 'download', 'dubai',
    'durban', 'earth', 'eus', 'faith', 'fashion', 'film', 'fishing',
    'fit', 'gal', 'garden', 'gay', 'gdn', 'gmx', 'gov', 'hamburg',
    'health', 'hiphop', 'horse', 'hn', 'ht', 'ink', 'insurance',
    'joburg', 'krd', 'law', 'loan', 'locker', 'love', 'luxe',
    'ly', 'madrid', 'melbourne', 'men', 'menu', 'miami', 'ml',
    'moe', 'moscow', 'ms', 'museum', 'music', 'nf', 'nrw', 'nyc',
    'one', 'osaka', 'ovh', 'paris', 'party', 'photo', 'physio',
    'porn', 'quebec', 'racing', 'radio', 'review', 'rodeo', 'rugby',
    'scot', 'science', 'select', 'sex', 'sport', 'stream', 'study',
    'sucks', 'surf', 'swiss', 'sydney', 'taipei', 'tattoo', 'tel',
    'trade', 'tube', 'versicherung', 'vip', 'vlaanderen', 'vodka',
    'voting', 'webcam', 'wedding', 'wiki', 'win', 'work', 'xxx',
    'yoga',
    # ccTLDs
    'cv', 'pg', 'sd', 'sn', 'ss', 'vi', 'zm',
    # Regional open TLDs
    'cologne', 'koeln', 'tirol', 'wien', 'nrw',
}

# ── Brand TLDs on multi-tenant registries ─────────────────────────────────
# These use shared RDAP servers (Nominet, GMO, etc.) but are brand-only.

BRAND_TLDS_ON_SHARED_REGISTRIES = {
    # CentralNic — brand TLDs on shared registry
    'bmw', 'mini', 'jnj',
    # Nominet (rdap.nominet.uk) — brand TLDs mixed with .uk
    'abbvie', 'amazon', 'audible', 'author', 'aws', 'azure', 'bbc',
    'bbva', 'bentley', 'bing', 'bloomberg', 'boehringer', 'bond',
    'boots', 'bot', 'box', 'bnpparibas', 'bugatti', 'cal',
    'case', 'cbs', 'cern', 'chanel', 'cipriani', 'circle',
    'comcast', 'comsec', 'contact', 'crown', 'dclk', 'dealer',
    'deal', 'dell', 'deloitte', 'delta', 'dhl', 'discover', 'dnp',
    'dodge', 'dot', 'dtv', 'dunlop', 'dupont', 'dvr', 'edeka',
    'emerald', 'epson', 'ericsson', 'etisalat', 'everbank',
    'extraspace', 'fage', 'fairwinds', 'family', 'fast', 'fedex',
    'fidelity', 'fido', 'fire', 'flickr', 'flir', 'fly',
    'foo', 'ford', 'fox', 'free', 'frontier', 'ftr',
    'fujitsu', 'fun', 'gallo', 'gallup', 'gap', 'gea',
    'george', 'ggee', 'giving', 'glass', 'globo', 'gmail',
    'gmo', 'godaddy', 'gold', 'goldpoint', 'goo', 'goodyear',
    'goog', 'google', 'grainger', 'grocery', 'guardian',
    'gucci', 'hbo', 'hdfc', 'hdfcbank', 'hermes', 'hisamitsu',
    'hitachi', 'homegoods', 'homesense', 'honeywell', 'hot',
    'hoteles', 'hotels', 'hotmail', 'house', 'hsbc', 'hughes',
    'hyatt', 'hyundai', 'ibm', 'iinet', 'ikano', 'imdb',
    'infiniti', 'intel', 'intuit', 'ipiranga', 'irish', 'iselect',
    'itau', 'itv', 'iveco', 'iwc',
    # GMO Registry (rdap.gmoregistry.net) — all are brand TLDs
    'bridgestone', 'brother', 'canon', 'datsun', 'epson',
    'firestone', 'fujitsu', 'hitachi', 'honda', 'infiniti',
    'jcb', 'koelnmesse', 'kyocera', 'lexus', 'lixil', 'maserati',
    'mazda', 'mitsubishi', 'nissan', 'nikon', 'panasonic',
    'pioneer', 'ricoh', 'sharp', 'softbank', 'suzuki', 'toshiba',
    'toyota', 'yamaha', 'yokohama',
    # Google Registry (pubapi.registry.google) — brand TLDs
    'ads', 'android', 'chrome', 'dclk', 'drive', 'earth',
    'gmail', 'goog', 'google', 'hangout', 'meet', 'nexus',
    'play', 'youtube',
    # Samsung
    'samsung',
    # Mobile Registry
    'blockbuster', 'data', 'dish', 'dot', 'dtv', 'dvr',
    'latino', 'mobile', 'ollo', 'ott', 'sling', 'comcast',
}


def get_db_conn():
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=int(os.environ.get('POSTGRES_PORT', '25060')),
        dbname=os.environ.get('POSTGRES_DB', 'canyougrab'),
        user=os.environ.get('POSTGRES_USER', 'canyougrab'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
        sslmode='require',
    )


def detect_brand_tlds(conn) -> set[str]:
    """Detect brand TLDs using heuristics + curated lists."""
    cur = conn.cursor()
    cur.execute('SELECT tld, rdap_server FROM tld_registry ORDER BY tld')
    rows = cur.fetchall()

    brands = set()

    # Heuristic 1: rdap.nic.{tld} pattern = single-operator TLD
    for tld, server in rows:
        if not server:
            continue
        host = urlparse(server).hostname
        if host == f'rdap.nic.{tld}' and tld not in KNOWN_OPEN_TLDS:
            brands.add(tld)

    # Heuristic 2: known brand TLDs on shared registries
    all_tlds = {tld for tld, _ in rows}
    for tld in BRAND_TLDS_ON_SHARED_REGISTRIES:
        if tld in all_tlds:
            brands.add(tld)

    # Safety: never mark major open TLDs as brand
    for tld in ('com', 'net', 'org', 'io', 'co', 'dev', 'app', 'xyz',
                'online', 'site', 'store', 'us', 'uk', 'de', 'fr',
                'ca', 'au', 'jp', 'nl', 'no', 'pl', 'fi', 'cz',
                'br', 'in', 'sg', 'tw', 'biz'):
        brands.discard(tld)

    return brands


def main():
    apply = '--apply' in sys.argv
    conn = get_db_conn()

    brands = detect_brand_tlds(conn)
    print(f'Detected {len(brands)} brand TLDs')

    # Show sample
    sample = sorted(brands)[:30]
    print(f'Sample: {", ".join("." + t for t in sample)}')

    # Verify known brands are included
    expected = {'nike', 'apple', 'google', 'amazon', 'bmw', 'samsung', 'chase', 'ford'}
    missing = expected - brands
    if missing:
        print(f'WARNING: expected brand TLDs not detected: {missing}')

    if not apply:
        print('\nDry run — pass --apply to write to DB')
        conn.close()
        return

    cur = conn.cursor()

    # Reset all to false first
    cur.execute('UPDATE tld_registry SET is_brand = FALSE')
    reset_count = cur.rowcount

    # Set detected brands to true
    if brands:
        cur.execute(
            'UPDATE tld_registry SET is_brand = TRUE WHERE tld = ANY(%s)',
            (sorted(brands),),
        )
        set_count = cur.rowcount
    else:
        set_count = 0

    conn.commit()
    print(f'Reset {reset_count} TLDs to is_brand=FALSE')
    print(f'Set {set_count} TLDs to is_brand=TRUE')
    conn.close()


if __name__ == '__main__':
    main()
