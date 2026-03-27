-- Add TOS acceptance tracking to users table
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS tos_accepted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS tos_version TEXT;
