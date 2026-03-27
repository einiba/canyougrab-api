-- Migration 010: Zone file NS snapshots
-- Stores current + previous nameserver records extracted from daily zone file builds.
-- 2-day rolling retention: we only need current and previous to detect NS changes.
-- The bloom builder writes here after a successful zone parse; the API uses it to
-- answer "what NS does this domain use?" without a live DNS lookup.

BEGIN;

CREATE TABLE IF NOT EXISTS zone_ns_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    domain      TEXT NOT NULL,       -- SLD only, e.g. "google" (no TLD)
    tld         TEXT NOT NULL,       -- e.g. "com"
    nameservers TEXT[] NOT NULL,     -- e.g. ARRAY['ns1.google.com', 'ns2.google.com']
    snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Primary lookup: domain + tld for a given date
CREATE UNIQUE INDEX IF NOT EXISTS idx_zone_ns_domain_tld_date
    ON zone_ns_snapshots (domain, tld, snapshot_date);

-- Retention sweep: find old snapshots to delete
CREATE INDEX IF NOT EXISTS idx_zone_ns_snapshot_date
    ON zone_ns_snapshots (snapshot_date);

COMMIT;
