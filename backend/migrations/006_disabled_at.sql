-- Migration 006: Add disabled_at to api_keys.
-- Disabled keys still count toward usage but reject new lookups.
-- This replaces the "delete" (revoke) action in the portal.

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ;
