-- Migration: starred_domains
-- Persistent per-user "shortlist of names I'm considering" surface.
--
-- Today the favorites tray on /find-a-name and /interactive is in-memory
-- only — clear the tab and the stars are gone. This table promotes ★ to a
-- first-class account feature: one row per (user, domain), survives session,
-- shown on the new /starred portal page.
--
-- Domains starred while anonymous are kept in localStorage on the client
-- and POSTed to /api/portal/names/star/claim after signup.

BEGIN;

CREATE TABLE IF NOT EXISTS starred_domains (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub TEXT NOT NULL,
    domain TEXT NOT NULL,
    base TEXT,
    tld TEXT,
    -- Snapshot of availability at the moment the user starred. Useful for the
    -- /starred view to colour-code without re-running a check, and for
    -- diff-since-last-check telemetry later.
    available_at_star BOOLEAN,
    source_list_id UUID REFERENCES name_generation_lists(id) ON DELETE SET NULL,
    notes TEXT,
    starred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_sub, domain)
);

CREATE INDEX IF NOT EXISTS idx_starred_user_recent
    ON starred_domains (user_sub, starred_at DESC);

COMMIT;
