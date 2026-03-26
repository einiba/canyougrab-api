-- Migration 005: Add stripe_price_id_test column for dev/test Stripe mode.
-- Both dev and prod share the same DB, so we need separate columns for
-- live vs test Stripe price IDs. The app selects the right column based
-- on the STRIPE_SECRET_KEY prefix (sk_test_ vs sk_live_).

ALTER TABLE plans ADD COLUMN IF NOT EXISTS stripe_price_id_test TEXT;
