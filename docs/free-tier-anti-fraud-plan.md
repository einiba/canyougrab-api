# Free Tier Anti-Fraud Plan — canyougrab.it

## Previous Plans (deprecated)

| Plan | Monthly Lookups | Hourly Rate | Price |
|------|----------------|-------------|-------|
| ~~Starter~~ | 100 | 100/hr | $1/mo |
| Basic | 10,000 | 1,000/hr | $10/mo |
| Pro | 50,000 | 5,000/hr | $20/mo |
| Business | 300,000 | 30,000/hr | $30/mo |

The $1 Starter plan has been **retired** — replaced by the Free+ tier (same 100 lookups/month, but free with card on file). Existing Starter users are auto-migrated to Free+.

---

## Implemented Plan Lineup

| | Free | Free+ | Basic | Pro | Business |
|---|---|---|---|---|---|
| Monthly Lookups | 25 | 100 | 10,000 | 50,000 | 300,000 |
| Hourly Rate | 15/hr | 50/hr | 1,000/hr | 5,000/hr | 30,000/hr |
| Per-request cap | 5 | 25 | 100 | 100 | 100 |
| Price | $0 | $0 | $10/mo | $20/mo | $30/mo |
| Gate | Email verification | Card on file | Stripe subscription | Stripe subscription | Stripe subscription |

**Free** — Available immediately after signup. Enough to evaluate the product (a few searches per day).

**Free+** — Unlocked by adding a card on file via Stripe SetupIntent ($0 charge). Card fingerprint enforces one Free+ account per card. Upgrades all active API keys from Free to Free+.

**Basic / Pro / Business** — Paid Stripe subscriptions (unchanged from before, except Starter is removed).

The free tier is intentionally small — enough to evaluate the product (a few searches per day) but not enough to replace a paid plan for any real workflow.

---

## Anti-Fraud Measures: Analysis & Recommendations

### Layer 1: Signup Gate (Blocks Automated & Low-Effort Abuse)

#### 1A. Cloudflare Turnstile on Signup/Login

| | |
|---|---|
| **What** | Invisible CAPTCHA challenge on registration and login forms |
| **Effectiveness** | High against bots — Cloudflare blocks 1M+ automated signups on their own site |
| **Friction** | Near-zero for humans (invisible most of the time) |
| **Cost** | Free (up to 1M verifications/month) |
| **Complexity** | Low — JS snippet on frontend, single API call on backend |

**Pros:**
- Eliminates automated bulk account creation
- Free, privacy-friendly, GDPR-compliant
- Works without Cloudflare CDN
- Invisible to most legitimate users

**Cons:**
- Does not stop manual multi-accounting
- Some accessibility concerns for users who get challenged

**RECOMMENDATION: IMPLEMENT (Phase 1).** Zero-cost, near-zero friction, high impact on automated abuse. No reason not to.

---

#### 1B. Email Verification (Confirmation Link)

| | |
|---|---|
| **What** | Require users to click a link in their email before the account activates |
| **Effectiveness** | Moderate — adds real cost to each fake account (must have working inbox) |
| **Friction** | Low (standard practice, users expect it) |
| **Cost** | Negligible (already sending emails via Auth0) |
| **Complexity** | Low — Auth0 supports this natively |

**Pros:**
- Standard practice, no user surprise
- Blocks accounts with typo'd or non-existent emails
- Auth0 likely already supports this as a toggle

**Cons:**
- Gmail allows unlimited accounts, so this alone doesn't prevent multi-accounting
- Slight delay to first use

**RECOMMENDATION: IMPLEMENT (Phase 1).** If not already enabled in Auth0, turn it on. Table stakes.

---

#### 1C. Disposable Email Detection

| | |
|---|---|
| **What** | Block signups from known disposable/temporary email providers (Guerrilla Mail, Mailinator, etc.) |
| **Effectiveness** | Moderate — blocks lowest-effort throwaway accounts |
| **Friction** | None for legitimate users (personal Gmail/Yahoo/Outlook all pass) |
| **Cost** | Free (DeBounce free API or open-source domain lists) |
| **Complexity** | Low — single API call or list lookup at signup |

**Pros:**
- Eliminates the easiest multi-accounting vector
- No impact on legitimate personal-email users
- Free or near-free to implement

**Cons:**
- New disposable domains appear daily; lists lag behind
- Determined abusers will just use real Gmail accounts instead

**RECOMMENDATION: IMPLEMENT (Phase 1).** Easy win. Use a combination of open-source list + DeBounce API for coverage.

---

#### 1D. Email Normalization & Deduplication

| | |
|---|---|
| **What** | Normalize Gmail addresses (strip dots, strip `+` aliases) before checking for duplicates |
| **Effectiveness** | High against Gmail alias abuse specifically |
| **Friction** | None — invisible to users |
| **Cost** | Free |
| **Complexity** | Low — string manipulation at signup |

**Details:** `john.doe+canyougrabit@gmail.com`, `johndoe@gmail.com`, and `j.o.h.n.d.o.e@gmail.com` all route to the same inbox. Normalize to `johndoe@gmail.com` before storing and checking for existing accounts.

Apply similar normalization for: `googlemail.com` → `gmail.com`, and other known providers with aliasing.

**Pros:**
- Catches the most common multi-account trick
- Zero user friction
- Zero cost

**Cons:**
- Only works for providers with known aliasing rules (mainly Gmail)
- Doesn't catch someone who creates `john.doe.real@gmail.com` as a separate account

**RECOMMENDATION: IMPLEMENT (Phase 1).** Free, invisible, effective. Store both the original email (for communication) and the normalized form (for dedup).

---

### Layer 2: Identity Binding (Connects Accounts to Real People)

#### 2A. Credit Card on File via Stripe SetupIntent

| | |
|---|---|
| **What** | Require a valid credit card to access the free tier — charge $0 but validate the card |
| **Effectiveness** | Very high — credit cards are hard to fabricate and expensive to obtain in bulk |
| **Friction** | HIGH — 30-60% of potential signups will abandon |
| **Cost** | Free (Stripe charges nothing for SetupIntents or $0 subscriptions) |
| **Complexity** | Low-medium — Stripe SetupIntent flow is well-documented |

**Card fingerprint deduplication:** Every card in Stripe gets a deterministic `fingerprint` hash. Store fingerprints and enforce **one free tier per card fingerprint**. If someone uses 50 emails with the same card, they get one free account.

**Pros:**
- Strongest single anti-abuse measure
- Free from Stripe
- Card fingerprint enables reliable cross-account dedup
- Higher trial-to-paid conversion (~50% vs ~15% without card)

**Cons:**
- Significant signup drop-off (30-60% fewer total signups)
- International users may not have cards that work with Stripe
- Apple Pay / Google Pay tokenized cards may produce different fingerprints for the same underlying card
- Some users are philosophically opposed to providing a card for a free service
- **Directly conflicts with marketing goal of easy access for domain researchers**

**RECOMMENDATION: IMPLEMENT AS A TIERED GATE (Phase 2).** Don't require a card for initial signup. Instead, offer two sub-tiers:

| | Free (no card) | Free+ (card on file) |
|---|---|---|
| Monthly Lookups | 15 | 50 |
| Hourly Rate | 10/hr | 25/hr |
| Per-request cap | 5 | 10 |

This lets anyone try the product instantly (15 lookups is enough to evaluate it), while the card-on-file gate unlocks a more useful free tier. Users who are serious enough to search 50 domains/month are serious enough to add a card.

**Key implementation detail:** Use Stripe `SetupIntent` with `usage: "off_session"` to validate the card. Store the `payment_method.card.fingerprint` and enforce one Free+ account per fingerprint. If a card fingerprint already has a free account, reject the card with a message like "This card is already associated with a free account."

---

#### 2B. Phone Number Verification

| | |
|---|---|
| **What** | SMS one-time code verification |
| **Effectiveness** | Moderate-high — phone numbers cost $0.50-2.00 each on bypass services |
| **Friction** | Medium — widely accepted but some users dislike providing phone numbers |
| **Cost** | ~$0.05/verification via Twilio Verify |
| **Complexity** | Low-medium |

**Pros:**
- Real economic cost to abusers ($0.50+ per fake account)
- Widely normalized (Google, X, etc. all require it)
- Can detect VoIP numbers (though non-VoIP bypass services exist)

**Cons:**
- International SMS delivery can be unreliable
- Privacy-conscious users dislike providing phone numbers
- Bypass services exist, though they cost money
- Ongoing per-verification cost

**RECOMMENDATION: USE AS ESCALATION ONLY (Phase 3).** Don't require phone verification for all signups. Reserve it as a challenge for accounts that trigger risk signals (e.g., VPN IP + suspicious behavioral patterns). This keeps the clean path frictionless.

---

#### 2C. Device Fingerprinting

| | |
|---|---|
| **What** | Browser fingerprinting to generate a stable device identifier across sessions |
| **Effectiveness** | High for detecting multi-accounting from the same device |
| **Friction** | None — invisible to users |
| **Cost** | Free for 1,000 IDs/month (Fingerprint Pro), $99/mo for 20K |
| **Complexity** | Low-medium — JS snippet + backend API |

**Pros:**
- Invisible — zero user friction
- Persists across incognito mode, cookie clearing, different browsers
- Links accounts that share a device
- 99.5% accuracy claimed by Fingerprint Pro

**Cons:**
- Can be spoofed by sophisticated users (VMs, browser extensions)
- Privacy-focused browsers (Brave, Firefox) resist fingerprinting
- GDPR may require disclosure
- Cost scales with signups ($99/mo at moderate volume)

**RECOMMENDATION: IMPLEMENT (Phase 2).** Use the free tier initially. Store `visitorId` at signup and flag accounts that share a device fingerprint with existing accounts. Don't hard-block (shared computers exist), but feed into the risk score.

---

### Layer 3: Reputation & Behavioral Signals

#### 3A. IP Reputation Scoring

| | |
|---|---|
| **What** | Check signup and request IPs against known VPN/proxy/datacenter/Tor databases |
| **Effectiveness** | Moderate — catches bulk abuse from infrastructure IPs |
| **Friction** | None if used as a signal; high if used as a hard block |
| **Cost** | ~$25/month (IPQS) |
| **Complexity** | Low — single API call |

**Pros:**
- Catches abuse from datacenter IPs, known proxy services, Tor exit nodes
- Can be applied silently as a risk signal
- Useful data point for composite scoring

**Cons:**
- **Many legitimate domain researchers use VPNs** — hard-blocking VPNs would lose real users
- Residential proxy services are increasingly hard to detect
- High false-positive rate as a standalone signal
- Ongoing monthly cost

**RECOMMENDATION: IMPLEMENT AS SIGNAL ONLY (Phase 2).** Never hard-block based on IP reputation alone. Feed the score into the composite risk system. A VPN user with a verified card on file and clean behavioral patterns should not be restricted. A VPN user with a disposable email and a device fingerprint matching 3 other accounts should face escalation.

---

#### 3B. Behavioral Analysis & Risk Scoring

| | |
|---|---|
| **What** | Internal scoring system that combines all signals to assess account risk |
| **Effectiveness** | Very high when tuned — the "force multiplier" for all other measures |
| **Friction** | None for clean accounts; escalating friction for suspicious ones |
| **Cost** | Engineering time only |
| **Complexity** | Medium-high — requires logging, scoring logic, ongoing tuning |

**Proposed Risk Score Model:**

| Signal | Points | Notes |
|---|---|---|
| Disposable email domain | +20 | Blocked at signup, but track attempts |
| Email matches normalized form of existing account | +30 | Gmail alias abuse |
| Device fingerprint matches existing free account | +25 | Multi-accounting from same device |
| Card fingerprint matches existing free account | +40 | Definitive duplicate |
| VPN / datacenter IP at signup | +10 | Common among legit users too |
| Signup form completed in < 3 seconds | +15 | Bot-like speed |
| Quota exhausted within 1 hour of signup | +10 | Unusual for organic users |
| Quota maxed every day for 7+ consecutive days | +15 | Systematic free-tier farming |
| Same domains queried as another flagged account | +20 | Coordinated abuse |
| Multiple signups from same IP in 24 hours | +15 | Batch account creation |

**Action thresholds:**

| Score | Action |
|---|---|
| 0-29 | Normal — full free tier access |
| 30-49 | Elevated — reduce to lower free tier limits, add Turnstile challenge on API calls |
| 50-69 | High — require phone verification or card on file to continue |
| 70+ | Critical — suspend account, require manual review |

**Pros:**
- Flexible — can tune thresholds without code changes
- Proportional — clean users see nothing, suspicious users see friction
- Compounds the value of every other anti-fraud measure

**Cons:**
- Requires ongoing tuning as abuse patterns evolve
- False positives will require a manual review/appeal process
- Complex to build well from scratch

**RECOMMENDATION: IMPLEMENT (Phase 2-3).** Start simple (3-4 signals), add complexity over time. Store scores in a new `account_risk` table. Log all scoring events for auditability.

---

#### 3C. Rate Limiting Escalation (IP-Level)

| | |
|---|---|
| **What** | Add IP-based rate limits alongside existing per-account limits |
| **Effectiveness** | Moderate — catches abuse from shared infrastructure |
| **Friction** | None for normal usage patterns |
| **Cost** | Negligible — uses existing Valkey infrastructure |
| **Complexity** | Low — same pattern as existing hourly rate limiter |

**Proposed IP limits:**
- 200 lookups/hour per IP (across all accounts)
- 1,000 lookups/day per IP (across all accounts)

These are generous enough that multiple legitimate users behind a corporate NAT won't be affected, but catch a single person running 10 free accounts through one IP.

**RECOMMENDATION: IMPLEMENT (Phase 1).** Low effort, uses existing Valkey infrastructure, catches obvious abuse.

---

### Layer 4: Stripe-Specific Protections

#### 4A. Stripe Radar — Customer Abuse Evaluation

| | |
|---|---|
| **What** | Stripe's ML-based risk scoring at registration, using their network-wide fraud data |
| **Effectiveness** | Stripe claims 90% accuracy at predicting free trial abuse |
| **Friction** | None — runs in background via Stripe.js |
| **Cost** | $0.07-0.14 per evaluation (pricing may change, currently early access) |
| **Complexity** | Low-medium — Stripe.js integration + API call |

**Pros:**
- Leverages Stripe's massive data network (billions of transactions)
- No user friction
- Catches patterns invisible to individual merchants

**Cons:**
- Currently in early access (may need to request access)
- Per-evaluation cost adds up at scale
- Dependent on Stripe continuing to offer this feature

**RECOMMENDATION: EVALUATE IN PHASE 3.** Worth exploring once you have meaningful free-tier volume. Contact trial-abuse-prevention@stripe.com to request access.

---

## Implementation Plan

### Phase 1 — Quick Wins (Week 1-2)

| # | Measure | Effort | Cost |
|---|---|---|---|
| 1 | Cloudflare Turnstile on signup/login | 1-2 days | Free |
| 2 | Enable email verification in Auth0 | Hours | Free |
| 3 | Disposable email detection at signup | 1 day | Free |
| 4 | Email normalization + dedup | 1 day | Free |
| 5 | IP-based rate limiting via Valkey | 1 day | Free |
| 6 | Add `free` plan to `PLAN_MONTHLY_LIMITS` and `PLAN_HOURLY_LIMITS` | Hours | Free |
| 7 | Lower per-request domain cap for free plan (10 vs 100) | Hours | Free |

**Phase 1 blocks:** Bots, disposable emails, Gmail alias abuse, same-IP floods. Total cost: $0.

### Phase 2 — Identity Binding (Week 3-4)

| # | Measure | Effort | Cost |
|---|---|---|---|
| 8 | Tiered free plan (Free vs Free+ with card on file) | 2-3 days | Free |
| 9 | Card fingerprint storage + one-free-per-card enforcement | 1 day | Free |
| 10 | Device fingerprinting (Fingerprint Pro) | 1-2 days | Free (1K/mo) |
| 11 | IP reputation scoring (IPQS) | 1 day | $25/mo |
| 12 | Composite risk scoring table + basic scoring logic | 2-3 days | Free |

**Phase 2 blocks:** Multi-accounting via same card, same device, datacenter IPs. Total cost: ~$25/mo.

### Phase 3 — Escalation & Refinement (Week 5-6)

| # | Measure | Effort | Cost |
|---|---|---|---|
| 13 | Phone verification as escalation challenge | 2-3 days | ~$0.05/verification |
| 14 | Behavioral scoring rules (quota patterns, signup speed, query overlap) | 2-3 days | Free |
| 15 | Admin dashboard for risk review + manual actions | 2-3 days | Free |
| 16 | Evaluate Stripe Radar customer abuse evaluation | 1 day | $0.07-0.14/eval |

**Phase 3 handles:** Sophisticated abusers, edge cases, ongoing tuning.

---

## Ongoing Monthly Cost Estimate

| Item | Cost |
|---|---|
| Cloudflare Turnstile | Free |
| Disposable email detection | Free |
| Stripe SetupIntent / card validation | Free |
| Fingerprint Pro (under 1K signups/mo) | Free |
| Fingerprint Pro (scaled) | $99/mo |
| IPQS IP reputation | $25/mo |
| Twilio Verify (escalation only, ~100/mo estimate) | ~$5/mo |
| Stripe Radar evaluations (optional, at scale) | Variable |
| **Total (moderate scale)** | **~$30-130/mo** |

---

## Key Design Principles

1. **Friction is proportional to risk.** A clean signup path should feel instant. Barriers appear only when signals suggest abuse.

2. **No single signal is a hard block** (except disposable emails). VPN? Fine, if your card and device are clean. New device? Fine, if your email and IP are clean. Every signal feeds a score; the score drives the response.

3. **Legitimate personal email users are never penalized.** Gmail, Yahoo, Outlook, iCloud, ProtonMail — all allowed. Only disposable/temporary domains are blocked.

4. **The free tier is intentionally tight.** 50 lookups/month is enough to evaluate the product but worthless for any real workflow. The goal is conversion, not retention on free.

5. **Multi-accounting is the primary threat.** One person with 50 free accounts gets 2,500 lookups/month — more than a Basic plan. Every measure targets this vector.

6. **Defense in depth.** No single measure is foolproof. Layering cheap, fast checks (Turnstile, email normalization) with slower, more expensive ones (card validation, phone verification) creates an economic barrier that scales with abuse sophistication.

---

## Database Schema Additions

```sql
-- Normalized email for deduplication
ALTER TABLE api_keys ADD COLUMN email_normalized TEXT;
CREATE INDEX idx_api_keys_email_normalized ON api_keys(email_normalized);

-- Card fingerprint tracking
CREATE TABLE card_fingerprints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub TEXT NOT NULL,
    stripe_fingerprint TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_card_fingerprints_stripe ON card_fingerprints(stripe_fingerprint);

-- Device fingerprint tracking
CREATE TABLE device_fingerprints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_device_fingerprints_visitor ON device_fingerprints(visitor_id);

-- Risk scoring
CREATE TABLE account_risk (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_sub TEXT NOT NULL UNIQUE,
    risk_score INTEGER DEFAULT 0,
    risk_signals JSONB DEFAULT '{}',
    last_evaluated_at TIMESTAMPTZ DEFAULT NOW(),
    action_taken TEXT DEFAULT 'none'  -- none, reduced, challenged, suspended
);
CREATE INDEX idx_account_risk_score ON account_risk(risk_score);

-- IP rate limiting (supplement Valkey with daily tracking)
CREATE TABLE ip_daily_usage (
    ip_address INET NOT NULL,
    lookups INTEGER DEFAULT 0,
    recorded_at DATE NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE(ip_address, recorded_at)
);
```

---

## Addressing Your Specific Concern

> "We would still need to avoid someone using 50 personal emails with the same Stripe account to get 50x free access."

The card fingerprint approach handles this directly. Here's the enforcement flow:

1. User signs up with `email1@gmail.com` → gets Free tier (15 lookups/mo)
2. User adds a card via SetupIntent → card fingerprint `fp_abc123` stored → upgraded to Free+ (50 lookups/mo)
3. User signs up with `email2@gmail.com` → gets Free tier (15 lookups/mo)
4. User tries to add the same card → fingerprint `fp_abc123` already exists → **rejected**: "This card is already associated with a free account"
5. Meanwhile, email normalization catches `e.mail1@gmail.com` at signup → blocked as duplicate

Even without the card gate, the layered signals catch this:
- Same device fingerprint across accounts → risk score +25 each
- Same IP for multiple signups → risk score +15
- Behavioral pattern match (same domains queried) → risk score +20
- Score hits 50+ → phone verification required → real economic cost per account

The combination makes it economically irrational: getting 50x free access would require 50 unique phones (~$25-100), 50 unique cards (impractical), 50 unique devices/VMs (time-intensive), all while defeating behavioral analysis. At that point, paying $10/mo for Basic is vastly cheaper.
