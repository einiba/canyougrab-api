# DNS Load Balancing: Capacity-Aware Multi-Resolver Routing

## Problem
Under load, our single Unbound recursive resolver hits upstream TLD nameserver rate limits (Verisign for .com), causing 28% SERVFAIL errors. No amount of local Unbound tuning fixes this because the bottleneck is upstream.

## Solution
Distribute DNS queries across multiple public resolvers (Google, Cloudflare, Quad9, etc.), each with independent upstream connections. Track per-resolver error rates in a sliding 3-minute window to estimate each server's effective rate cap, and route queries proportionally based on capacity.

## Architecture

### 1. Database Schema: `nameservers` table

```sql
CREATE TABLE nameservers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,              -- human label: "Google Primary"
    address TEXT NOT NULL UNIQUE,    -- IP: "8.8.8.8"
    port INTEGER DEFAULT 53,
    provider TEXT,                   -- "google", "cloudflare", "quad9", "opendns"
    protocol TEXT DEFAULT 'udp',     -- "udp" or "tcp" or "doh"

    -- Capacity tracking (updated on every error)
    estimated_cap_qps REAL DEFAULT 1.0,     -- estimated queries/sec this server can handle
    success_count_3m INTEGER DEFAULT 0,     -- successes in trailing 3 min window
    error_count_3m INTEGER DEFAULT 0,       -- errors (SERVFAIL/timeout) in trailing 3 min
    last_error_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,

    -- State
    enabled BOOLEAN DEFAULT TRUE,
    disabled_at TIMESTAMPTZ,
    disabled_reason TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 2. Seed Data: Known High-Capacity Public DNS Servers

All seeded with `estimated_cap_qps = 1` (conservative start). The system discovers actual capacity through observation.

| Name | Address | Provider |
|------|---------|----------|
| Google Primary | 8.8.8.8 | google |
| Google Secondary | 8.8.4.4 | google |
| Cloudflare Primary | 1.1.1.1 | cloudflare |
| Cloudflare Secondary | 1.0.0.1 | cloudflare |
| Quad9 Primary | 9.9.9.9 | quad9 |
| Quad9 Secondary | 149.112.112.112 | quad9 |
| OpenDNS Primary | 208.67.222.222 | opendns |
| OpenDNS Secondary | 208.67.220.220 | opendns |
| Unbound (local) | unbound.canyougrab.svc.cluster.local | self |

### 3. Capacity Estimation Algorithm

**On every DNS query result:**

```python
def update_nameserver_stats(server_id: int, success: bool):
    """Called after every DNS query. Updates sliding window stats."""
    now = datetime.utcnow()
    window = timedelta(minutes=3)

    if success:
        # Increment success, update timestamp
        UPDATE nameservers SET
            success_count_3m = success_count_3m + 1,
            last_success_at = now,
            updated_at = now
        WHERE id = server_id
    else:
        # Increment error, recalculate cap
        UPDATE nameservers SET
            error_count_3m = error_count_3m + 1,
            last_error_at = now,
            updated_at = now
        WHERE id = server_id

def recalculate_caps():
    """Called every 30 seconds. Decays old counts and recalculates caps."""
    for server in nameservers:
        total = server.success_count_3m + server.error_count_3m
        if total == 0:
            continue

        error_rate = server.error_count_3m / total

        if error_rate > 0.5:
            # More than 50% errors — halve estimated cap
            new_cap = max(0.1, server.estimated_cap_qps * 0.5)
        elif error_rate > 0.1:
            # 10-50% errors — reduce by error rate
            new_cap = server.estimated_cap_qps * (1 - error_rate)
        elif error_rate < 0.02 and total > 100:
            # Under 2% errors with good sample — increase cap
            new_cap = server.estimated_cap_qps * 1.2
        else:
            new_cap = server.estimated_cap_qps

        UPDATE nameservers SET
            estimated_cap_qps = new_cap,
            # Decay counts by 50% each cycle (30s half-life)
            success_count_3m = success_count_3m / 2,
            error_count_3m = error_count_3m / 2
```

### 4. Query Routing: Weighted Random Selection

```python
def select_nameserver() -> Nameserver:
    """Select a nameserver proportional to its estimated capacity."""
    servers = get_enabled_nameservers()  # cached, refreshed every 30s

    total_cap = sum(s.estimated_cap_qps for s in servers)
    weights = [s.estimated_cap_qps / total_cap for s in servers]

    return random.choices(servers, weights=weights, k=1)[0]
```

**Example with seed values (all cap=1):**
- 9 servers × cap 1.0 = total 9.0
- Each gets ~11% of traffic (uniform distribution)

**After warm-up (discovered caps):**
- Google 8.8.8.8: cap=50, Cloudflare 1.1.1.1: cap=80, Unbound: cap=20, ...
- Google gets 50/total, Cloudflare gets 80/total, etc.

### 5. Worker Changes

**Current flow:**
```
worker → ThreadPool → check_domain_dns(domain, resolver) → Unbound → upstream
```

**New flow:**
```
worker → ThreadPool → check_domain_dns(domain) → select_nameserver() → chosen resolver → upstream
                                                  ↓ on SERVFAIL
                                                  update_nameserver_stats(error)
                                                  retry with different server
                                                  ↓ on success
                                                  update_nameserver_stats(success)
```

Key changes to `dns_client.py`:
1. Remove singleton resolver — create resolver per query (or pool of resolvers)
2. `select_nameserver()` picks server based on weights
3. On SERVFAIL, update stats AND retry with a DIFFERENT server (not the same one)
4. On success, update stats
5. Stats written to Valkey (fast), flushed to PostgreSQL every 30s by background task

### 6. Valkey as Hot Cache for Stats

PostgreSQL is too slow for per-query updates. Use Valkey:

```
ns:stats:{server_id}:success  — counter (3 min TTL)
ns:stats:{server_id}:error    — counter (3 min TTL)
```

Background task (every 30s):
1. Read all `ns:stats:*` from Valkey
2. Write to PostgreSQL `nameservers` table
3. Recalculate `estimated_cap_qps`
4. Workers read updated caps from Valkey cache

### 7. Implementation Order

1. **Migration**: Create `nameservers` table + seed data
2. **Valkey stats**: Add per-query stat tracking to `dns_client.py`
3. **Multi-resolver**: Replace singleton resolver with weighted selection
4. **Retry with different server**: On SERVFAIL, pick a different server
5. **Background flush**: Workers flush stats to PostgreSQL every 30s
6. **Cap recalculation**: Background task recalculates caps every 30s
7. **Health check integration**: `/health/ready` reports per-server status
8. **Grafana dashboard**: Visualize per-server caps, error rates, traffic distribution

### 8. Unbound's Role Changes

Unbound becomes ONE of many resolvers, not the only one. Its advantage: recursive resolution (no middleman), good for cache-heavy workloads. Its disadvantage: single upstream path (one source IP to Verisign).

Public resolvers have the opposite profile: each has massive distributed infrastructure with many upstream paths, but adds a hop and we're subject to their rate limits too.

The system discovers the right balance automatically through observation.

### 9. Risk Mitigation

- **All servers down**: If total estimated cap drops below threshold, fall back to Unbound-only (known working, just slow)
- **Bad actor server**: If a server returns incorrect results (says NXDOMAIN for google.com), the WHOIS verification step catches it. Add a periodic canary check for known-registered domains.
- **Abuse concerns**: Some public DNS providers may block us if we send too much traffic. The cap estimation naturally backs off when errors increase.
