-- Migration 006: TLD registry for controlling WHOIS/RDAP behavior per TLD
-- Allows disabling WHOIS lookups for TLDs where it's slow/broken,
-- and tracking RDAP server overrides for TLDs missing from IANA bootstrap.

BEGIN;

CREATE TABLE IF NOT EXISTS tld_registry (
    tld                 TEXT PRIMARY KEY,
    rdap_server         TEXT,           -- override RDAP server URL (NULL = use default discovery)
    whois_disabled_at   TIMESTAMPTZ,    -- when WHOIS was disabled for this TLD
    whois_disabled_reason TEXT,         -- why (e.g., "no RDAP, slow WHOIS fallback")
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
