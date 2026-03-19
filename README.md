# CanYouGrab API

Domain availability lookup API with subscription billing, built on FastAPI + DNS (Unbound resolver).

**Live services:**
- API: `https://api.canyougrab.it`
- Developer portal: `https://portal.canyougrab.it`
- Auth: `https://auth.canyougrab.it` (Auth0 custom domain)

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Developer Portal                          │
│              (Zudoku/React on portal.canyougrab.it)              │
│   Usage Dashboard · API Keys · Pricing · API Reference (OAS)    │
└──────────────────┬───────────────────────────────────────────────┘
                   │  Auth0 JWT (portal)  /  Bearer API key (API)
                   ▼
┌──────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (v5.0.0)                       │
│                    api.canyougrab.it:8000                         │
│                                                                  │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ app.py      │  │ keys.py  │  │billing.py│  │ auth.py     │  │
│  │ /check/bulk │  │ /keys    │  │/billing  │  │ API key +   │  │
│  │ /usage      │  │ CRUD     │  │/stripe   │  │ JWT auth    │  │
│  │             │  │ rotate   │  │ webhook  │  │             │  │
│  └──────┬──────┘  └──────────┘  └────┬─────┘  └─────────────┘  │
│         │                            │                           │
└─────────┼────────────────────────────┼───────────────────────────┘
          │                            │
          ▼                            ▼
┌─────────────────┐          ┌─────────────────┐
│  Valkey (Redis)  │          │   Stripe API     │
│  Job queue +     │          │   Subscriptions   │
│  Rate limiting   │          │   Webhooks        │
└────────┬────────┘          └─────────────────┘
         │
         ▼
┌─────────────────┐          ┌──────────────────────────────┐
│  Worker Process  │──DNS──▶ │   Unbound Resolver            │
│  (worker.py)     │          │   (dedicated droplet)         │
│  ThreadPool(10)  │          │   NS queries via VPC          │
│  BRPOP queue     │          └──────────────────────────────┘
└─────────────────┘
                             ┌──────────────────────────────┐
         ───────────────────▶│   PostgreSQL                  │
          (auth, usage,      │   API keys + Usage logs       │
           billing only)     └──────────────────────────────┘
```

## Directory Structure

```
zuplo/
├── backend/                    # Python FastAPI backend
│   ├── app.py                  # Main API: /check/bulk, /usage, /health
│   ├── auth.py                 # API key auth (SHA-256) + Auth0 JWT auth (RS256)
│   ├── billing.py              # Stripe checkout, portal, webhooks, card-on-file, usage details
│   ├── keys.py                 # API key CRUD: create, list, rotate, revoke (+ Turnstile)
│   ├── antifraud.py            # Anti-fraud: Turnstile, device fingerprints, risk scoring
│   ├── email_utils.py          # Email normalization + disposable email detection
│   ├── dns_client.py           # DNS-based domain availability checking via Unbound
│   ├── queries.py              # PostgreSQL queries: usage tracking, auth, billing
│   ├── valkey_client.py        # Redis/Valkey job queue client
│   ├── worker.py               # Background job processor (ThreadPoolExecutor)
│   ├── migrations/             # SQL migrations
│   │   └── 001_free_tier_antifraud.sql
│   └── requirements.txt        # Python dependencies
├── portal/                     # Developer portal (Zuplo + Zudoku)
│   ├── config/
│   │   ├── routes.oas.json     # OpenAPI 3.1 spec (public API documentation)
│   │   └── policies.json       # Zuplo policies (empty — all routing is direct)
│   ├── docs/                   # Zudoku documentation portal
│   │   ├── src/
│   │   │   ├── config.ts       # API_BASE, Turnstile site key, Stripe PK
│   │   │   ├── UsageDashboard.tsx   # Usage + billing dashboard component
│   │   │   ├── PricingPage.tsx      # Plan selection + Stripe checkout
│   │   │   ├── PricingPlans.tsx     # Pricing card grid component
│   │   │   └── CardSetupPage.tsx    # Stripe Elements card-on-file for Free+
│   │   ├── public/             # Static assets (logos, banners, CSS overrides)
│   │   ├── zudoku.config.tsx   # Portal config: theme, nav, Auth0, API key mgmt
│   │   └── package.json        # Frontend dependencies (React 19, Zudoku)
│   ├── package.json            # Workspace root (Zuplo v6, TypeScript v5)
│   └── README.md               # Zuplo boilerplate (not project-specific)
├── mcp-server/                 # MCP package for ChatGPT, Claude, and remote MCP clients
│   ├── pyproject.toml          # MCP package metadata + version
│   ├── server.json             # MCP registry/server metadata
│   ├── uv.lock                 # Locked MCP runtime dependencies
│   └── src/canyougrab_mcp/
│       ├── __init__.py
│       └── server.py           # stdio + streamable-http MCP entrypoint
├── .github/workflows/
│   ├── deploy.yml              # Production deploy (on tag push v*)
│   └── deploy-dev.yml          # Dev deploy (on push to dev branch)
├── .claude/
│   └── launch.json             # Local dev server config (port 9200)
└── package.json                # Root workspace config
```

## API Endpoints

### Public API (API key auth: `Authorization: Bearer cyg_...`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/check/bulk` | Check up to 100 domains. Long-polls until results ready (30s max). |
| `GET` | `/api/account/usage` | Usage summary for the authenticated consumer. |
| `GET` | `/api/account/quota-check` | Lightweight monthly + per-minute quota check. |
| `GET` | `/health` | Health check (no auth). |

### Portal API (Auth0 JWT auth)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/keys` | Create new API key. |
| `GET` | `/api/keys` | List user's API keys. |
| `POST` | `/api/keys/{id}/rotate` | Rotate key (revoke old, create new). |
| `DELETE` | `/api/keys/{id}` | Revoke (soft-delete) a key. |
| `POST` | `/api/billing/checkout` | Create Stripe Checkout session. |
| `POST` | `/api/billing/portal` | Create Stripe Customer Portal session. |
| `POST` | `/api/billing/setup-card` | Create SetupIntent for Free+ card-on-file. |
| `POST` | `/api/billing/confirm-free-plus` | Verify card fingerprint and upgrade to Free+. |
| `GET` | `/api/billing/card-status` | Check if user has a card on file. |
| `GET` | `/api/billing/usage/detailed` | Per-key usage breakdown for portal dashboard. |
| `POST` | `/api/antifraud/turnstile/verify` | Verify Cloudflare Turnstile token. |
| `POST` | `/api/antifraud/device/register` | Register device fingerprint (Fingerprint Pro). |
| `GET` | `/api/antifraud/risk` | Get risk assessment for authenticated user. |
| `POST` | `/api/antifraud/assess-signup` | Run multi-signal risk assessment at signup. |

### Internal / Webhook

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/account/usage/detailed` | Multi-consumer usage breakdown. |
| `POST` | `/api/stripe/webhook` | Stripe webhook receiver (signature-verified). |

## Core Request Flow

### Domain Availability Check

```
Client                    FastAPI                     Valkey                      Worker              Unbound
  │                          │                          │                          │                    │
  │  POST /api/check/bulk    │                          │                          │                    │
  │  { domains: [...] }      │                          │                          │                    │
  │─────────────────────────▶│                          │                          │                    │
  │                          │── validate key ──────────│                          │                    │
  │                          │── check minute rate ────▶│ INCR ratelimit:id:min   │                    │
  │                          │── check monthly quota ──▶│ (PostgreSQL)             │                    │
  │                          │── record usage ─────────▶│ (PostgreSQL)             │                    │
  │                          │── create_job() ─────────▶│ HSET job:{uuid}          │                    │
  │                          │                          │ LPUSH queue:jobs          │                    │
  │                          │                          │                          │                    │
  │                          │                          │◀── BRPOP queue:jobs ─────│                    │
  │                          │                          │                          │                    │
  │                          │                          │──── claim_job() ────────▶│                    │
  │                          │                          │                          │── ThreadPool(10)   │
  │                          │                          │                          │── check_domain_dns()
  │                          │                          │                          │── NS query ───────▶│
  │                          │                          │                          │◀── NOERROR/NXDOMAIN│
  │                          │                          │◀── complete_job() ───────│                    │
  │                          │                          │                          │                    │
  │                          │◀─ poll get_job_status() ─│                          │                    │
  │                          │   (0.3s interval, 30s    │                          │                    │
  │                          │    max timeout)          │                          │                    │
  │◀─────────────────────────│                          │                          │                    │
  │  { results: [...] }      │                          │                          │                    │
```

### Billing / Subscription Flow

```
User → Pricing Page → Select Plan → Auth0 login
  → POST /api/billing/checkout (JWT auth)
  → Find/create Stripe customer (linked by auth0_sub metadata)
  → Stripe Checkout Session created → redirect to Stripe
  → User pays → Stripe fires webhook
  → POST /api/stripe/webhook (HMAC-SHA256 verified)
  → checkout.session.completed → fetch subscription → get price ID
  → Map price to plan → UPDATE api_keys SET plan, lookups_limit
```

## Subscription Plans

| Plan | Monthly Lookups | Per-Minute Rate Limit | Domains/Request | Price |
|------|----------------|----------------------|-----------------|-------|
| Free | 500 | 30/min | 30 | $0 |
| Free+ | 10,000 | 100/min | 100 | $0 (card on file) |
| Basic | 20,000 | 300/min | 100 | $10/mo |
| Pro | 50,000 | 1,000/min | 100 | $20/mo |
| Business | 300,000 | 3,000/min | 100 | $30/mo |

## Authentication

### API Key Auth (public API consumers)

- Format: `Authorization: Bearer cyg_<token>`
- Keys are SHA-256 hashed in the `api_keys` table (plaintext never stored)
- Key prefix (`cyg_XXXXXXXX...`) is stored for display in the portal
- Full key returned **only once** at creation time
- Soft-delete on revocation (`revoked_at` timestamp, not physically deleted)

### JWT Auth (portal/dashboard)

- Auth0 tenant: `dev-mqe5tavp6dr62e7u`
- Custom domain: `auth.canyougrab.it`
- Algorithm: RS256 with JWKS validation (1-hour cache)
- Audience: `https://api.canyougrab.it`
- Social logins: Google, Apple (Sign in with Apple)

## Domain Availability via DNS

Domain availability is checked by querying a dedicated [Unbound recursive DNS resolver](https://github.com/ericismaking/canyougrab-unbound) running on a separate DigitalOcean droplet. The worker sends NS record queries over the VPC private network and interprets the response:

| DNS Response | `available` | Meaning |
|---|---|---|
| NOERROR + NS records | `false` | Domain is registered and delegated |
| NXDOMAIN | `true` | Domain not in zone — probably available |
| NoAnswer (NOERROR, no NS) | `false` | Registered but parked/undelegated |
| SERVFAIL / Timeout | `null` | Ambiguous — check failed |

The Unbound resolver caches aggressively (7 days for registered domains, 5 minutes for NXDOMAIN) and queries TLD authoritative servers directly, avoiding public resolver rate limits.

## Database Schema

PostgreSQL is used for authentication, usage tracking, and billing — not for domain lookups.

### PostgreSQL Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `api_keys` | User API keys | `id`, `user_sub`, `email_normalized`, `key_hash`, `key_prefix`, `plan`, `lookups_limit`, `revoked_at` |
| `usage_log_daily` | Daily usage aggregates | `consumer`, `lookups`, `recorded_at` (DATE, unique per consumer+day) |
| `usage_log_minute` | Per-minute usage aggregates | `consumer`, `lookups`, `minute_start` (TIMESTAMP, unique per consumer+minute) |
| `card_fingerprints` | One free account per card | `user_sub`, `stripe_fingerprint` (unique pair) |
| `device_fingerprints` | Device-based multi-account detection | `user_sub`, `visitor_id` (Fingerprint Pro) |
| `account_risk` | Composite risk scoring | `user_sub` (unique), `risk_score`, `risk_signals` (JSONB) |

### Valkey (Redis) Data Structures

| Key Pattern | Type | Purpose | TTL |
|-------------|------|---------|-----|
| `job:{uuid}` | Hash | Job status, domains, results | 1 hour |
| `queue:jobs` | List | FIFO job queue (BRPOP by worker) | — |
| `ratelimit:{consumer}:{YYYYMMDDHHmm}` | String | Per-minute rate limit counter (INCR) | 60s |

## Infrastructure

### Servers

| Environment | Domain | Purpose |
|-------------|--------|---------|
| Production | `api.canyougrab.it` | FastAPI + Worker |
| Dev | `dev.canyougrab.it` | FastAPI + Worker |
| Portal | `portal.canyougrab.it` | Zudoku static site |
| Auth | `auth.canyougrab.it` | Auth0 custom domain |

### External Services

| Service | Purpose |
|---------|---------|
| **DigitalOcean** | Droplets (API servers, Unbound resolver), Managed PostgreSQL, Managed Valkey |
| **Cloudflare** | DNS, CDN, SSL for canyougrab.it zone, Turnstile bot prevention |
| **Auth0** | User authentication, social login (Google + Apple), JWT issuance |
| **Stripe** | Subscription billing, checkout, customer portal, webhooks |
| **GitHub Actions** | CI/CD deployment pipelines |

## Deployment Pipelines

### Production Deploy

**Trigger:** Push a git tag matching `v*` (e.g., `v1.0.5`)

```
git tag v1.0.5 && git push origin v1.0.5
```

**Pipeline** (`.github/workflows/deploy.yml`):
1. GitHub Actions triggers on `v*` tag push
2. SSH into production server (`DEPLOY_HOST` secret) using `DEPLOY_SSH_KEY`
3. Bootstraps the target ref on the server (`git fetch` + `git checkout <tag>`)
4. Runs the repo-managed deploy script: `/opt/canyougrab-repo/scripts/deploy-host.sh`

### Dev Deploy

**Trigger:** Push to the `dev` branch

```
git push origin dev
```

**Pipeline** (`.github/workflows/deploy-dev.yml`):
1. GitHub Actions triggers on push to `dev`
2. SSH into dev server (`DEV_DEPLOY_HOST` secret) using same `DEPLOY_SSH_KEY`
3. Bootstraps the `dev` ref on the server (`git fetch` + `git checkout dev`)
4. Runs the repo-managed deploy script: `/opt/canyougrab-repo/scripts/deploy-host.sh`

### Repo-Managed Host Deploy Script

Both pipelines use a small inline SSH bootstrap to update `/opt/canyougrab-repo` to the target ref, then call `scripts/deploy-host.sh` from that checked-out revision. The repo-managed deploy script handles:
- `pip install -r requirements.txt` for backend dependencies
- `rsync` of backend, portal, and MCP source trees into their runtime directories
- Reinstall or refresh the `mcp-server` package/runtime when the host also runs `canyougrab-mcp.service`
- Restart of FastAPI (uvicorn), worker, and MCP services via systemd

If `/mcp` is served by the same host as the API, backend-only deploys are not enough. The MCP service must be updated and restarted during the same deploy or the OAuth metadata and live MCP behavior can drift apart.

An existing `/opt/deploy.sh` can still be kept as a manual compatibility shim if desired, but the automated pipeline should treat the repo copy of `scripts/deploy-host.sh` as the source of truth.

### Branching Strategy

| Branch | Purpose | Deploys To |
|--------|---------|------------|
| `main` | Production releases | Tagged → `api.canyougrab.it` |
| `dev` | Development/staging | Auto → `dev.canyougrab.it` |
| Feature branches | In-progress work | No auto-deploy |

## Environment Variables

### Backend (required on servers)

```bash
# DNS Resolver (Unbound on dedicated droplet)
DNS_RESOLVER_HOSTNAME=unbound.canyougrab.internal  # VPC internal hostname (resolved via socket.gethostbyname)
DNS_RESOLVER_PORT=53
DNS_QUERY_TIMEOUT=5.0  # Per-query timeout in seconds

# PostgreSQL (DigitalOcean Managed Database — auth, usage, billing only)
POSTGRES_HOST=         # DB cluster hostname
POSTGRES_PORT=5432
POSTGRES_DB=canyougrab
POSTGRES_USER=canyougrab
POSTGRES_PASSWORD=     # DB password
POSTGRES_SSLMODE=require

# Valkey / Redis (DigitalOcean Managed Database)
VALKEY_HOST=           # Valkey cluster hostname
VALKEY_PORT=25061
VALKEY_USERNAME=default
VALKEY_PASSWORD=       # Valkey password

# Auth0
AUTH0_DOMAIN=dev-mqe5tavp6dr62e7u.us.auth0.com
AUTH0_AUDIENCE=https://api.canyougrab.it

# Stripe
STRIPE_SECRET_KEY=     # sk_live_... (prod) or sk_test_... (dev)
STRIPE_WEBHOOK_SECRET= # whsec_... (per-environment)
STRIPE_PRICE_BASIC=    # price_... (live price ID for Basic plan)
STRIPE_PRICE_PRO=      # price_... (live price ID for Pro plan)
STRIPE_PRICE_BUSINESS= # price_... (live price ID for Business plan)

# Cloudflare Turnstile (bot prevention on key creation)
TURNSTILE_SECRET_KEY=  # 0x4AAAA... (from Cloudflare dashboard)

# Portal
PORTAL_URL=https://portal.canyougrab.it

# Worker
BATCH_CONCURRENCY=10   # Thread pool size for domain checks
```

### GitHub Actions Secrets

| Secret | Purpose |
|--------|---------|
| `DEPLOY_HOST` | Production server IP |
| `DEV_DEPLOY_HOST` | Dev server IP |
| `DEPLOY_SSH_KEY` | SSH private key for both servers |

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt

# Start API server
uvicorn app:app --reload --port 8000

# Start worker (separate terminal)
python worker.py
```

Requires `DNS_RESOLVER_HOST` pointing to an Unbound instance (or use `8.8.8.8` for basic testing), plus PostgreSQL and Valkey/Redis (local or tunneled to managed instances).

### Portal

```bash
cd portal/docs
npm install
npm run dev          # Starts Zudoku dev server (via zuplo)
```

Or use the configured launch server:
```bash
# From repo root
npx zuplo dev        # Starts on port 9200
```

Portal dev server hardcodes `API_BASE` to `https://api.canyougrab.it` in `portal/docs/src/config.ts`. To develop against a local backend, change this to `http://localhost:8000`.

## Key Design Decisions

- **Live DNS lookups**: Domain availability is checked via NS queries to a dedicated [Unbound recursive resolver](https://github.com/ericismaking/canyougrab-unbound). This gives real-time results (no 24-hour zone file lag), works for any TLD automatically, and avoids the operational complexity of daily batch zone file loading. Unbound caches aggressively (7 days for registered domains) so repeated queries are ~1ms.
- **Nullable availability**: DNS has more failure modes than a database lookup. When Unbound returns SERVFAIL or times out, the API returns `available: null` instead of a potentially dangerous false positive. Consumers should treat `null` as "could not determine."
- **Job queue for bulk checks**: The bulk endpoint uses a Valkey job queue + worker to parallelize DNS queries across a thread pool and avoid blocking the API event loop.
- **Long-polling**: The bulk check endpoint holds the HTTP connection open (polling Valkey every 0.3s, up to 30s) rather than requiring clients to implement polling.
- **API keys with SHA-256 hashing**: Keys are hashed before storage (like passwords), so a database breach doesn't expose raw keys.
- **Stripe metadata linking**: Stripe customers are linked to Auth0 users via `auth0_sub` metadata on the Stripe customer object, avoiding a separate mapping table.
- **Usage double-tracking**: Both daily (`usage_log_daily`) and per-minute (`usage_log_minute`) usage are recorded for monthly quota and per-minute rate limiting respectively.
- **No Zuplo gateway policies**: The project originally used Zuplo as an API gateway but migrated to direct FastAPI (commit `3abef70`). The Zuplo portal/docs framework is still used for the developer portal.

## Processes on Servers

Each server runs at least two systemd-managed processes:

1. **FastAPI** (uvicorn): Serves all HTTP endpoints on port 8000
2. **Worker** (worker.py): Processes domain check jobs from the Valkey queue
3. **MCP server** (`mcp-server-canyougrab --streamable-http`, if `/mcp` is hosted on this server): Serves remote MCP clients and OAuth-aware tool flows

All active services on the host must be managed via systemd and restarted during deployments.
All automated deploys should route through `scripts/deploy-host.sh`, which is responsible for restarting whichever of these services are installed on the host.
