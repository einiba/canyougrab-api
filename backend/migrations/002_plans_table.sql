-- Migration 002: Create plans table as single source of truth for plan configuration.
-- Replaces hardcoded PLAN_MONTHLY_LIMITS, PLAN_MINUTE_LIMITS, PLAN_DOMAIN_CAPS in app.py
-- and PLAN_PRICE_MAP, FREE_PLAN_LIMITS in billing.py.

CREATE TABLE IF NOT EXISTS plans (
    name            TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    price_cents     INTEGER NOT NULL DEFAULT 0,
    monthly_limit   INTEGER NOT NULL,
    minute_limit    INTEGER NOT NULL,
    domain_cap      INTEGER NOT NULL,
    requires_card   BOOLEAN NOT NULL DEFAULT FALSE,
    stripe_price_id TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0
);

INSERT INTO plans (name, display_name, price_cents, monthly_limit, minute_limit, domain_cap, requires_card, stripe_price_id, sort_order) VALUES
    ('free',      'Free',     0,    500,     30,   30,  FALSE, NULL,                                  0),
    ('free_plus', 'Verified', 0,    10000,   100,  100, TRUE,  NULL,                                  1),
    ('basic',     'Basic',    1000, 20000,   300,  100, TRUE,  'price_1TAggjH8ksFkvmqRNEE6UHx3',     2),
    ('pro',       'Pro',      2000, 50000,   1000, 100, TRUE,  'price_1TAggkH8ksFkvmqRUx9kVWf9',     3),
    ('business',  'Business', 3000, 300000,  3000, 100, TRUE,  'price_1TAggkH8ksFkvmqRn7c63MZE',     4)
ON CONFLICT (name) DO NOTHING;

-- Make lookups_limit optional on api_keys so we can stop writing it
ALTER TABLE api_keys ALTER COLUMN lookups_limit SET DEFAULT 0;
