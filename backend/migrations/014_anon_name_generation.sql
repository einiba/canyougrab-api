-- Migration: Anonymous name-generation trial gate
-- Tracks anonymous visitors so the /api/names/generate endpoint can enforce
-- the curious / trying / engaged trial tiers across cookies, fingerprints,
-- and salted IP hashes.

BEGIN;

-- Daily IP salt rotation. IPs are never stored; we store sha256(ip + daily_salt)
-- and rotate the salt every 24h, so usage history older than ~8 days cannot
-- be re-correlated to an IP.
CREATE TABLE IF NOT EXISTS anon_ip_salt (
    salt_date DATE PRIMARY KEY,
    salt TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per generation attempt. We aggregate usage by MAX(count) across
-- matches on any of (visitor_id, fingerprint, ip_hash), so clearing cookies
-- alone or rotating fingerprints alone does not reset the counter.
CREATE TABLE IF NOT EXISTS anon_name_gen_usage (
    id BIGSERIAL PRIMARY KEY,
    visitor_id TEXT NOT NULL,
    fingerprint TEXT,
    ip_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_anon_namegen_visitor
    ON anon_name_gen_usage (visitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_anon_namegen_fingerprint
    ON anon_name_gen_usage (fingerprint, created_at DESC) WHERE fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_anon_namegen_ip
    ON anon_name_gen_usage (ip_hash, created_at DESC) WHERE ip_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_anon_namegen_created
    ON anon_name_gen_usage (created_at);

-- Optional: persist generated lists so we can attach them to a new account
-- when an anonymous visitor signs up. Lets users keep their work.
CREATE TABLE IF NOT EXISTS name_generation_lists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    visitor_id TEXT NOT NULL,
    user_sub TEXT,
    description TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_namegen_lists_visitor ON name_generation_lists (visitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_namegen_lists_user ON name_generation_lists (user_sub) WHERE user_sub IS NOT NULL;

COMMIT;
