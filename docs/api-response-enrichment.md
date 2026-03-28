# API Response Enrichment — DNS & WHOIS Intelligence

## Current Response Format

```json
{
  "results": [
    {
      "domain": "google.com",
      "available": false,
      "confidence": "high",
      "tld": "com",
      "source": "dns",
      "checked_at": "2026-03-27T14:55:46Z",
      "cache_age_seconds": 0,
      "registration": null
    }
  ]
}
```

**Problems:**
- `registration` is always null for DNS-path results (majority of lookups)
- Even WHOIS-path only populates registrar + dates — no nameserver or hosting info
- No zone file intelligence exposed
- Flat structure mixes availability data with enrichment data

## Proposed Enhanced Response

```json
{
  "results": [
    {
      "domain": "google.com",
      "available": false,
      "confidence": "high",
      "tld": "com",
      "checked_at": "2026-03-27T14:55:46Z",

      "dns": {
        "status": "registered",
        "nameservers": ["ns1.google.com", "ns2.google.com", "ns3.google.com", "ns4.google.com"],
        "provider": "Google (self-hosted)",
        "dnssec": true,
        "resolver": "cloudflare"
      },

      "whois": {
        "registrar": "MarkMonitor, Inc.",
        "created_at": "1997-09-15T04:00:00Z",
        "expires_at": "2028-09-14T04:00:00Z",
        "updated_at": "2019-09-09T15:39:04Z",
        "status": ["clientDeleteProhibited", "clientTransferProhibited", "clientUpdateProhibited"],
        "source": "rdap"
      },

      "intelligence": {
        "category": "active",
        "hosting_provider": "Google",
        "parked": false,
        "ns_count": 4,
        "domain_age_days": 10420,
        "expires_in_days": 901
      },

      "_meta": {
        "source": "zone_file+dns+rdap",
        "cache_age_seconds": 0,
        "lookup_time_ms": 245
      }
    }
  ]
}
```

## Response Sections

### 1. Top-level fields (always present)
- `domain` — the queried domain
- `available` — true/false/null
- `confidence` — high/medium/low
- `tld` — extracted TLD
- `checked_at` — ISO 8601 timestamp

### 2. `dns` section (present for registered domains)
Populated from zone file data (pre-cached) or live DNS query.

| Field | Source | Description |
|-------|--------|-------------|
| `status` | DNS query | `registered`, `available`, `error` |
| `nameservers` | Zone file / DNS query | List of NS records |
| `provider` | Derived from NS | Hosting/DNS provider name |
| `dnssec` | Zone file (DS record) | Whether DNSSEC is enabled |
| `resolver` | Internal | Which resolver handled the query |

**NS → Provider mapping** (maintained in PostgreSQL `ns_providers` table):
```
ns*.google.com       → Google (self-hosted)
ns*.cloudflare.com   → Cloudflare
ns-*.awsdns-*.{com|net|org|co.uk} → AWS Route53
*.domaincontrol.com  → GoDaddy
*.registrar-servers.com → Namecheap
ns*.sedoparking.com  → Sedo (parked)
ns*.parkingcrew.net  → ParkingCrew (parked)
*.above.com          → Above.com (parked)
ns*.bodis.com        → Bodis (parked)
```

### 3. `whois` section (present when WHOIS/RDAP data is available)
Populated from rust-whois RDAP/WHOIS response.

| Field | Source | Description |
|-------|--------|-------------|
| `registrar` | RDAP/WHOIS | Registrar name |
| `created_at` | RDAP/WHOIS | Registration date |
| `expires_at` | RDAP/WHOIS | Expiration date |
| `updated_at` | RDAP/WHOIS | Last update date |
| `status` | RDAP/WHOIS | EPP status codes |
| `source` | Internal | `rdap` or `whois` (which protocol) |

### 4. `intelligence` section (derived, present for registered domains)
Computed from DNS + WHOIS data. This is the "value-add" layer.

| Field | Source | Description |
|-------|--------|-------------|
| `category` | Derived | `active`, `parked`, `expiring`, `redirected`, `unknown` |
| `hosting_provider` | NS mapping | Human-readable provider name |
| `parked` | NS + WHOIS | Boolean — domain is on a parking service |
| `ns_count` | Zone file | Number of nameservers (correlates with importance) |
| `domain_age_days` | WHOIS | Days since registration |
| `expires_in_days` | WHOIS | Days until expiration (null if unknown) |

**Category logic:**
```python
if ns in PARKING_NS_PATTERNS:
    category = "parked"
elif expires_in_days is not None and expires_in_days < 30:
    category = "expiring"
elif ns_count >= 2 and provider != "unknown":
    category = "active"
else:
    category = "unknown"
```

### 5. `_meta` section (always present)
Internal metadata — useful for debugging, can be hidden in production.

| Field | Description |
|-------|-------------|
| `source` | Pipeline path taken: `cache`, `zone_file`, `dns`, `rdap`, `whois`, or combinations |
| `cache_age_seconds` | How old the cached result is |
| `lookup_time_ms` | Total time for this domain's lookup |

## Data Pipeline Changes

### Zone File → PostgreSQL (daily, CronJob)
Store NS records and DNSSEC status in a `zone_domains` table:

```sql
CREATE TABLE zone_domains (
    domain TEXT NOT NULL,        -- SLD (e.g., "google")
    tld TEXT NOT NULL,           -- "com", "net", etc.
    nameservers TEXT[],          -- ["ns1.google.com", "ns2.google.com", ...]
    dnssec BOOLEAN DEFAULT FALSE,
    provider TEXT,               -- derived from NS mapping
    parked BOOLEAN DEFAULT FALSE, -- derived from NS patterns
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (domain, tld)
);
CREATE INDEX idx_zone_domains_provider ON zone_domains(provider);
CREATE INDEX idx_zone_domains_parked ON zone_domains(parked) WHERE parked = TRUE;
```

### NS Provider Mapping (PostgreSQL)
```sql
CREATE TABLE ns_providers (
    pattern TEXT PRIMARY KEY,    -- glob pattern: "*.cloudflare.com"
    provider TEXT NOT NULL,      -- "Cloudflare"
    category TEXT NOT NULL,      -- "dns_hosting", "registrar", "parking", "self_hosted"
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Worker Lookup Enhancement
1. **Zone file check** (before DNS): Look up `zone_domains` for NS + provider data
   - If bloom filter says registered AND zone_domains has NS data → return enriched result without live DNS
   - Saves a DNS query entirely for cached zone file domains
2. **DNS check** (live): If zone file miss, do live DNS as today
3. **WHOIS check** (if NXDOMAIN): As today, but also return `source` field
4. **Intelligence derivation**: Compute category, parked flag, age from combined data

### Caching Strategy
- Zone file data: refreshed daily, cached in PostgreSQL (not Valkey)
- Live DNS results: cached in Valkey as today (5 min available, 24h registered)
- WHOIS data: cached in Valkey as today
- Intelligence fields: computed on the fly from DNS + WHOIS, cached with the result

## API Versioning

Add `?enrichment=full` query parameter (or `X-Enrichment: full` header):
- **Default (no param)**: returns current flat format for backward compatibility
- **`enrichment=full`**: returns the new sectioned format with dns/whois/intelligence

The MCP server always requests `enrichment=full` since AI agents benefit from the structured data.

## Implementation Order

1. **NS provider mapping table + seed data** — create `ns_providers` table, populate with ~50 known patterns
2. **Zone file NS extraction** — update bloom builder CronJob to also extract and store NS + DS records in `zone_domains`
3. **Worker enrichment** — update `lookup.py` to populate `dns` section from zone_domains or live query
4. **Intelligence derivation** — add `intelligence` section computation
5. **API response restructuring** — add `?enrichment=full` parameter
6. **MCP server update** — pass enrichment=full, expose intelligence in tool response

## Estimated Storage

| Table | Rows | Size |
|-------|------|------|
| `zone_domains` (.com) | 169M | ~15 GB |
| `zone_domains` (all 9 TLDs) | ~195M | ~17 GB |
| `ns_providers` | ~100 | < 1 MB |

The zone_domains table is large — may need to stay in PostgreSQL with proper indexing rather than Valkey. The bloom filter stays in Valkey for fast "is it registered?" checks; PostgreSQL handles the enrichment data.
