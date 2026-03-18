-- Add published_at and retired_at columns to plans table
-- Plans are only shown on pricing pages when:
--   published_at IS NOT NULL AND published_at <= NOW()
--   AND (retired_at IS NULL OR retired_at > NOW())

ALTER TABLE plans ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ;

-- Publish all existing plans
UPDATE plans SET published_at = NOW() WHERE published_at IS NULL;
