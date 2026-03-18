-- Migration 003: Create users table for canonical customer records.
-- Previously, user info (email, sub) was only stored per api_key row.
-- This table provides a single source of truth for user identity.

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth0_sub       TEXT NOT NULL UNIQUE,
    email           TEXT NOT NULL DEFAULT '',
    email_normalized TEXT NOT NULL DEFAULT '',
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    name            TEXT NOT NULL DEFAULT '',
    picture_url     TEXT NOT NULL DEFAULT '',
    auth_provider   TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_email_normalized ON users (email_normalized);

-- Seed from existing api_keys data (one row per distinct user_sub, newest email wins)
INSERT INTO users (auth0_sub, email, email_normalized, created_at)
SELECT DISTINCT ON (user_sub)
    user_sub,
    COALESCE(email, ''),
    COALESCE(email_normalized, ''),
    MIN(created_at) OVER (PARTITION BY user_sub)
FROM api_keys
WHERE user_sub IS NOT NULL AND user_sub != ''
ORDER BY user_sub, created_at DESC
ON CONFLICT (auth0_sub) DO NOTHING;

COMMIT;
