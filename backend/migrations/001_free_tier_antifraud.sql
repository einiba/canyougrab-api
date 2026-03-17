-- Migration: Free tier + anti-fraud infrastructure
-- Adds tables and columns needed for Phase 1 & Phase 2 anti-fraud measures.
-- Run against the canyougrab database.

BEGIN;

-- 1. Add normalized email column to api_keys for deduplication
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS email_normalized TEXT;
CREATE INDEX IF NOT EXISTS idx_api_keys_email_normalized ON api_keys(email_normalized);

-- Backfill normalized emails for existing rows (basic lowercase + strip dots/plus for gmail)
UPDATE api_keys
SET email_normalized = LOWER(
    CASE
        WHEN SPLIT_PART(LOWER(email), '@', 2) IN ('gmail.com', 'googlemail.com')
        THEN REPLACE(SPLIT_PART(SPLIT_PART(LOWER(email), '@', 1), '+', 1), '.', '') || '@gmail.com'
        ELSE SPLIT_PART(SPLIT_PART(LOWER(email), '@', 1), '+', 1) || '@' || SPLIT_PART(LOWER(email), '@', 2)
    END
)
WHERE email IS NOT NULL AND email_normalized IS NULL;

-- 2. Card fingerprint tracking (one free account per card)
CREATE TABLE IF NOT EXISTS card_fingerprints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub TEXT NOT NULL,
    stripe_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_sub, stripe_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_card_fingerprints_stripe ON card_fingerprints(stripe_fingerprint);
CREATE INDEX IF NOT EXISTS idx_card_fingerprints_user ON card_fingerprints(user_sub);

-- 3. Device fingerprint tracking (Fingerprint Pro visitorId)
CREATE TABLE IF NOT EXISTS device_fingerprints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_sub, visitor_id)
);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_visitor ON device_fingerprints(visitor_id);
CREATE INDEX IF NOT EXISTS idx_device_fingerprints_user ON device_fingerprints(user_sub);

-- 4. Risk scoring
CREATE TABLE IF NOT EXISTS account_risk (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub TEXT NOT NULL UNIQUE,
    risk_score INTEGER DEFAULT 0,
    risk_signals JSONB DEFAULT '{}',
    last_evaluated_at TIMESTAMPTZ DEFAULT NOW(),
    action_taken TEXT DEFAULT 'none'
);
CREATE INDEX IF NOT EXISTS idx_account_risk_score ON account_risk(risk_score);
CREATE INDEX IF NOT EXISTS idx_account_risk_user ON account_risk(user_sub);

-- 5. Migrate existing 'starter' plan users to 'free_plus' (they had 100 lookups, same as free_plus)
UPDATE api_keys SET plan = 'free_plus', lookups_limit = 100
WHERE plan = 'starter' AND revoked_at IS NULL;

-- 6. Migrate existing 'none' plan users to 'free'
UPDATE api_keys SET plan = 'free', lookups_limit = 25
WHERE plan = 'none' AND revoked_at IS NULL;

COMMIT;
