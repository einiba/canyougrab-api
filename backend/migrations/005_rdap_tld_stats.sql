-- Migration 005: RDAP per-TLD reliability tracking
-- Tracks RDAP success/failure rates per TLD per day for long-term analytics
-- and adaptive fallback decisions.

BEGIN;

CREATE TABLE IF NOT EXISTS rdap_tld_stats (
    id                    SERIAL PRIMARY KEY,
    tld                   TEXT NOT NULL,
    recorded_date         DATE NOT NULL DEFAULT CURRENT_DATE,
    rdap_success          INTEGER NOT NULL DEFAULT 0,
    rdap_domain_not_found INTEGER NOT NULL DEFAULT 0,
    rdap_error            INTEGER NOT NULL DEFAULT 0,
    rdap_unsupported      INTEGER NOT NULL DEFAULT 0,
    whois_fallback        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tld, recorded_date)
);

CREATE INDEX IF NOT EXISTS idx_rdap_tld_stats_tld_date
    ON rdap_tld_stats (tld, recorded_date DESC);

COMMIT;
