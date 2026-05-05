-- Migration: hosted-LLM monthly cap per plan
-- Adds hosted_llm_monthly to the plans table so the marketing + portal pricing
-- pages can render included AI generation credits, and so the future
-- POST /api/portal/names/generate endpoint has a budget gate to consult.
--
-- Display-only today; enforcement endpoint not yet implemented.

BEGIN;

ALTER TABLE plans
  ADD COLUMN IF NOT EXISTS hosted_llm_monthly INTEGER NOT NULL DEFAULT 0;

UPDATE plans SET hosted_llm_monthly = 0     WHERE name = 'free';
UPDATE plans SET hosted_llm_monthly = 50    WHERE name = 'free_plus';
UPDATE plans SET hosted_llm_monthly = 200   WHERE name = 'basic';
UPDATE plans SET hosted_llm_monthly = 500   WHERE name = 'pro';
UPDATE plans SET hosted_llm_monthly = 2000  WHERE name = 'business';

COMMIT;
