# CanYouGrab API

Domain availability lookup API with subscription billing, built on FastAPI + PostgreSQL zone data.

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
│                    FastAPI Backend (v4.0.0)                       │
│                    api.canyougrab.it:8000                         │
│                                                                  │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ app.py      │  │ keys.py  │  │billing.py│  │ auth.py     │  │
│  │ /check/bulk │  │ /keys    │  │/billing  │  │ API key +   │  │
│  │ /tlds       │  │ CRUD     │  │/stripe   │  │ JWT auth    │  │
│  │ /usage      │  │ rotate   │  │ webhook  │  │             │  │
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
│  Worker Process  │────────▶│   PostgreSQL                  │
│  (worker.py)     │          │   domains_live / zones_live   │
│  ThreadPool(10)  │          │   (views → domains_YYYYMMDD)  │
│  BRPOP queue     │          │   API keys + Usage logs       │
└─────────────────┘          └──────────────────────────────┘
```

## Directory Structure

```
zuplo/
├── backend/                    # Python FastAPI backend (~1500 LOC)
│   ├── app.py                  # Main API: /check/bulk, /tlds, /usage, /health
│   ├── auth.py                 # API key auth (SHA-256) + Auth0 JWT auth (RS256)
│   ├── billing.py              # Stripe checkout, portal, webhooks, usage details
│   ├── keys.py                 # API key CRUD: create, list, rotate, revoke
│   ├── queries.py              # PostgreSQL queries: domain lookup, usage tracking
│   ├── valkey_client.py        # Redis/Valkey job queue client
│   ├── worker.py               # Background job processor (ThreadPoolExecutor)
│   └── requirements.txt        # Python dependencies
├── portal/                     # Developer portal (Zuplo + Zudoku)
│   ├── config/
│   │   ├── routes.oas.json     # OpenAPI 3.1 spec (public API documentation)
│   │   └── policies.json       # Zuplo policies (empty — all routing is direct)
│   ├── docs/                   # Zudoku documentation portal
│   │   ├── src/
│   │   │   ├── config.ts       # API_BASE URL constant
│   │   │   ├── UsageDashboard.tsx   # Usage + billing dashboard component
│   │   │   ├── PricingPage.tsx      # Plan selection + Stripe checkout
│   │   │   └── PricingPlans.tsx     # Pricing card grid component
│   │   ├── public/             # Static assets (logos, banners, CSS overrides)
│   │   ├── zudoku.config.tsx   # Portal config: theme, nav, Auth0, API key mgmt
│   │   └── package.json        # Frontend dependencies (React 19, Zudoku)
│   ├── package.json            # Workspace root (Zuplo v6, TypeScript v5)
│   └── README.md               # Zuplo boilerplate (not project-specific)
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
| `GET` | `/api/tlds` | List 800+ supported TLDs with record counts. |
| `GET` | `/api/account/usage` | Usage summary for the authenticated consumer. |
| `GET` | `/api/account/quota-check` | Lightweight monthly + hourly quota check. |
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
| `GET` | `/api/billing/usage/detailed` | Per-key usage breakdown for portal dashboard. |

### Internal / Webhook

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/zones` | Zone load metadata (no auth). |
| `POST` | `/api/account/usage/detailed` | Multi-consumer usage breakdown. |
| `POST` | `/api/stripe/webhook` | Stripe webhook receiver (signature-verified). |

## Core Request Flow

### Domain Availability Check

```
Client                    FastAPI                     Valkey                      Worker
  │                          │                          │                          │
  │  POST /api/check/bulk    │                          │                          │
  │  { domains: [...] }      │                          │                          │
  │─────────────────────────▶│                          │                          │
  │                          │── validate key ──────────│                          │
  │                          │── check hourly rate ────▶│ INCR ratelimit:id:hour  │
  │                          │── check monthly quota ──▶│ (PostgreSQL)             │
  │                          │── record usage ─────────▶│ (PostgreSQL)             │
  │                          │── create_job() ─────────▶│ HSET job:{uuid}          │
  │                          │                          │ LPUSH queue:jobs          │
  │                          │                          │                          │
  │                          │                          │◀── BRPOP queue:jobs ─────│
  │                          │                          │                          │
  │                          │                          │──── claim_job() ────────▶│
  │                          │                          │                          │── ThreadPool(10)
  │                          │                          │                          │── check_domain_pooled()
  │                          │                          │                          │── SELECT from domains_live
  │                          │                          │◀── complete_job() ───────│
  │                          │                          │                          │
  │                          │◀─ poll get_job_status() ─│                          │
  │                          │   (0.3s interval, 30s    │                          │
  │                          │    max timeout)          │                          │
  │◀─────────────────────────│                          │                          │
  │  { results: [...] }      │                          │                          │
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

| Plan | Monthly Lookups | Hourly Rate Limit | Price |
|------|----------------|-------------------|-------|
| Starter | 100 | 100/hr | $1/mo |
| Basic | 10,000 | 1,000/hr | $10/mo |
| Pro | 50,000 | 5,000/hr | $20/mo |
| Business | 300,000 | 30,000/hr | $30/mo |

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

## Database Schema

### Zone Data (Zero-Downtime Architecture)

Zone data is stored in **date-suffixed tables** (`domains_YYYYMMDD`, `zones_YYYYMMDD`) and accessed through PostgreSQL views. The [batch loader](https://github.com/ericismaking/canyougrab-batch) writes to fresh tables daily, validates the new data, then atomically swaps the view definitions. This prevents false-positive "available" results during the ~5-hour daily load window.

```
┌─────────────────────┐       ┌─────────────────────────┐
│  domains_live (VIEW) │──────▶│  domains_20260314       │  (active)
│  zones_live   (VIEW) │──────▶│  zones_20260314         │
└─────────────────────┘       └─────────────────────────┘
                              ┌─────────────────────────┐
                              │  domains_20260313       │  (previous, retained for fallback)
                              │  zones_20260313         │
                              └─────────────────────────┘
```

The API reads exclusively through `domains_live` and `zones_live` — it never references date-suffixed tables directly. The `active_dataset` table tracks which date is currently live, its validation status, and the previous date for fallback.

### PostgreSQL Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `domains_YYYYMMDD` | Zone file data for a given day (SLD+TLD pairs) | `domain` (SLD), `tld` |
| `zones_YYYYMMDD` | Zone load metadata per TLD for a given day | `tld`, `loaded_at`, `record_count` |
| `active_dataset` | Single-row pointer to active date-suffixed tables | `active_date`, `status`, `total_rows`, `validation_log` |
| `sanity_checks` | Known-registered domains for post-load validation | `domain`, `tld` |
| `all_TLDs` | TLD catalog with file sizes | `tld`, `last_compressed_file_size`, `last_file_size` |
| `api_keys` | User API keys | `id`, `user_sub`, `key_hash`, `key_prefix`, `plan`, `lookups_limit`, `revoked_at` |
| `usage_log` | Daily usage aggregates | `consumer`, `lookups`, `recorded_at` (DATE, unique per consumer+day) |
| `hourly_usage_log` | Hourly usage aggregates | `consumer`, `lookups`, `hour_start` (TIMESTAMP, unique per consumer+hour) |

### PostgreSQL Views (API reads through these)

| View | Points To | Purpose |
|------|-----------|---------|
| `domains_live` | `domains_YYYYMMDD` | Domain availability lookups |
| `zones_live` | `zones_YYYYMMDD` | TLD metadata and record counts |

### Valkey (Redis) Data Structures

| Key Pattern | Type | Purpose | TTL |
|-------------|------|---------|-----|
| `job:{uuid}` | Hash | Job status, domains, results | 1 hour |
| `queue:jobs` | List | FIFO job queue (BRPOP by worker) | — |
| `ratelimit:{consumer}:{YYYYMMDDHH}` | String | Hourly rate limit counter (INCR) | 1 hour |

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
| **DigitalOcean** | Droplets (API servers), Managed PostgreSQL, Managed Valkey |
| **Cloudflare** | DNS, CDN, SSL for canyougrab.it zone |
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
3. Runs `/opt/deploy.sh <tag>` on the server (e.g., `/opt/deploy.sh v1.0.5`)

### Dev Deploy

**Trigger:** Push to the `dev` branch

```
git push origin dev
```

**Pipeline** (`.github/workflows/deploy-dev.yml`):
1. GitHub Actions triggers on push to `dev`
2. SSH into dev server (`DEV_DEPLOY_HOST` secret) using same `DEPLOY_SSH_KEY`
3. Runs `/opt/deploy.sh dev` on the server

### Server-Side Deploy Script

Both pipelines call `/opt/deploy.sh` on the respective server. This script (on the server, not in the repo) handles:
- `git fetch` + `git checkout` to the specified tag or branch
- `pip install -r requirements.txt` for backend dependencies
- Restart of FastAPI (uvicorn) and worker processes via systemd

### Branching Strategy

| Branch | Purpose | Deploys To |
|--------|---------|------------|
| `main` | Production releases | Tagged → `api.canyougrab.it` |
| `dev` | Development/staging | Auto → `dev.canyougrab.it` |
| Feature branches | In-progress work | No auto-deploy |

## Environment Variables

### Backend (required on servers)

```bash
# PostgreSQL (DigitalOcean Managed Database)
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
STRIPE_SECRET_KEY=     # sk_live_... or sk_test_...
STRIPE_WEBHOOK_SECRET= # whsec_...

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

Requires local PostgreSQL and Valkey/Redis, or tunnels to managed instances.

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

- **Zone file approach**: Domain availability is checked against imported TLD zone files stored in PostgreSQL, not live WHOIS/DNS queries. This enables sub-millisecond lookups but requires periodic zone data refresh.
- **Zero-downtime table swap**: The batch loader writes to date-suffixed tables (`domains_YYYYMMDD`) while the API reads from views (`domains_live`). After validation, `CREATE OR REPLACE VIEW` atomically swaps the read path. This prevents false-positive "available" results during the ~5-hour daily load window. Old tables are retained for 3 days for fallback. See the [batch loader repo](https://github.com/ericismaking/canyougrab-batch) for details.
- **Job queue for bulk checks**: Even though individual domain lookups are fast, the bulk endpoint uses a Valkey job queue + worker to parallelize across a thread pool and avoid blocking the API event loop.
- **Long-polling**: The bulk check endpoint holds the HTTP connection open (polling Valkey every 0.3s, up to 30s) rather than requiring clients to implement polling.
- **API keys with SHA-256 hashing**: Keys are hashed before storage (like passwords), so a database breach doesn't expose raw keys.
- **Stripe metadata linking**: Stripe customers are linked to Auth0 users via `auth0_sub` metadata on the Stripe customer object, avoiding a separate mapping table.
- **Usage double-tracking**: Both daily (`usage_log`) and hourly (`hourly_usage_log`) usage are recorded for monthly quota and hourly rate limiting respectively.
- **No Zuplo gateway policies**: The project originally used Zuplo as an API gateway but migrated to direct FastAPI (commit `3abef70`). The Zuplo portal/docs framework is still used for the developer portal.

## Processes on Servers

Each server runs two systemd-managed processes:

1. **FastAPI** (uvicorn): Serves all HTTP endpoints on port 8000
2. **Worker** (worker.py): Processes domain check jobs from the Valkey queue

Both are managed via systemd and restarted by `/opt/deploy.sh` during deployments.
