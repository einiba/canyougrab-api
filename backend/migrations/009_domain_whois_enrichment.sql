-- Migration 009: Domain WHOIS/RDAP enrichment cache
-- Persists slow-to-fetch structured registration data from RDAP/WHOIS lookups.
-- 90-day retention: domain registrations don't change often, stale data is acceptable.
-- Workers write here after a successful RDAP or WHOIS fetch; API reads this before
-- dispatching a live lookup (avoids redundant calls for recently-checked domains).

BEGIN;

CREATE TABLE IF NOT EXISTS domain_whois_enrichment (
    id              BIGSERIAL PRIMARY KEY,
    domain          TEXT NOT NULL,               -- e.g. "example.com"
    tld             TEXT NOT NULL,               -- e.g. "com"
    source          TEXT NOT NULL,               -- "rdap" | "whois"

    -- Registration status
    registered      BOOLEAN,
    status          TEXT[],                      -- e.g. ARRAY['clientTransferProhibited']
    registrar       TEXT,
    registrar_url   TEXT,

    -- Dates
    created_date    TIMESTAMPTZ,
    updated_date    TIMESTAMPTZ,
    expires_date    TIMESTAMPTZ,

    -- Raw response for replay / debugging (capped at 32KB)
    raw_response    TEXT,

    -- Metadata
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '90 days'
);

-- Lookup by domain (most common access pattern)
CREATE UNIQUE INDEX IF NOT EXISTS idx_whois_enrichment_domain
    ON domain_whois_enrichment (domain);

-- Retention sweep: find rows ready to expire
CREATE INDEX IF NOT EXISTS idx_whois_enrichment_expires_at
    ON domain_whois_enrichment (expires_at);

COMMIT;
