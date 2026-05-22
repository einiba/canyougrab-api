-- Migration 017: marketing opt-in / opt-out preference on users
--
-- Records whether each user has agreed to receive marketing email, when
-- they last opted in, where the opt-in was set (e.g. "pricing:pro",
-- "account_page"), and when (if ever) they opted out. Transactional
-- messages are not gated by this preference.
--
-- Effective flag: marketing_opt_in = TRUE AND marketing_unsubscribed_at
-- IS NULL. The partial index makes that filter fast for outbound mailings.

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS marketing_opt_in           boolean      NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS marketing_opt_in_at        timestamptz,
  ADD COLUMN IF NOT EXISTS marketing_opt_in_source    text,
  ADD COLUMN IF NOT EXISTS marketing_unsubscribed_at  timestamptz;

CREATE INDEX IF NOT EXISTS users_marketing_marketable_idx
  ON public.users (marketing_opt_in_at DESC)
  WHERE marketing_opt_in = true AND marketing_unsubscribed_at IS NULL;
