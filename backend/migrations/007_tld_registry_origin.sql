-- Migration 007: Add origin tracking to tld_registry
-- Tracks where each TLD mapping came from (iana, supplemental, manual)
-- and when it was last refreshed from IANA.

BEGIN;

ALTER TABLE tld_registry
  ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'manual',
  ADD COLUMN IF NOT EXISTS iana_updated_at TIMESTAMPTZ;

-- Mark existing rows as manual (they were inserted by hand)
UPDATE tld_registry SET origin = 'manual' WHERE origin = 'manual';

COMMIT;
