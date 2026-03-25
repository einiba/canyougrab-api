# End-to-End Health Check Design for CanYouGrab.it

## Overview

Three tiers of health checks, each serving a different purpose. The key insight from AWS and industry practice: **never let deep health checks trigger automatic restarts** — they should only inform monitoring. Shallow checks are for load balancers and process supervisors.

## Tier 1: Liveness (Shallow)

**Purpose**: Is the process running?
**Used by**: systemd, load balancer
**Interval**: Every 5-10 seconds
**Timeout**: 1 second max
**Failure action**: systemd restarts the process

| Service | Endpoint | Check |
|---------|----------|-------|
| API server | `GET /health` | Returns `{"status":"ok"}` — already exists |
| MCP server | `GET /health` | Returns `{"status":"ok"}` — just added |
| Unbound | TCP connect to port 53 | Process accepting connections |
| rust-whois | `GET /health` | Returns `{"status":"ok"}` |
| Valkey | `PING` → `PONG` | Process responding |
| Workers | N/A (no inbound port) | systemd monitors process existence |

**No dependency checks in this tier.** If Valkey is down and you restart the API, you've made things worse.

## Tier 2: Readiness (Deep)

**Purpose**: Can the service actually do useful work?
**Used by**: External monitoring (Grafana, third-party synthetics)
**Interval**: Every 30-60 seconds
**Timeout**: 5 seconds
**Failure action**: Alert, mark degraded — do NOT restart
**Cache**: Results cached for 10 seconds to prevent thundering-herd on dependencies

### Endpoint: `GET /health/ready`

Returns per-component status with latency:

```json
{
  "status": "healthy",
  "timestamp": "2026-03-25T19:00:00Z",
  "components": {
    "valkey": {"status": "ok", "latency_ms": 2},
    "dns_resolver": {"status": "ok", "latency_ms": 15},
    "whois_service": {"status": "ok", "latency_ms": 180},
    "workers": {"status": "ok", "active": 3, "busy": 1, "last_heartbeat_age_s": 12}
  }
}
```

### Checks performed:

1. **Valkey**: `SET` + `GET` a test key with 60s TTL (not just PING — proves read/write works)
2. **Unbound DNS**: Resolve `_healthcheck.test` via Unbound → expect `127.0.0.1` (known-answer test, never hits public internet)
3. **rust-whois**: RDAP query for `example.com` → expect IANA registration data (stable, well-known, no rate limits)
4. **Workers**: Read RQ worker heartbeats from Valkey → assert ≥1 worker seen within last 60 seconds

### Status logic:

- **healthy**: All components ok
- **degraded**: Non-critical components impaired (e.g., rust-whois slow but DNS works — availability checking still functional at reduced confidence)
- **unhealthy**: Critical path broken (Valkey down, no workers, DNS resolver unreachable)

HTTP status: always 200 for healthy/degraded (service is still useful), 503 only for unhealthy.

## Tier 3: Synthetic Transaction (End-to-End)

**Purpose**: Prove the ENTIRE pipeline works, from API to result
**Used by**: External monitoring, on-call dashboards
**Interval**: Every 60-120 seconds
**Timeout**: 15 seconds
**Failure action**: Alert, open circuit breaker — do NOT restart anything
**Security**: Authenticated or rate-limited (enqueues real jobs)

### Endpoint: `GET /health/deep` (requires API key or internal-only)

Performs a full synthetic domain lookup:

1. **API enqueues a synthetic job** for `example.com` with a unique correlation ID and a special `_synthetic: true` flag
2. **Worker dequeues it** from Valkey/RQ queue
3. **Worker resolves DNS** via Unbound (example.com → nameservers found)
4. **Worker queries RDAP** via rust-whois (example.com → IANA registration data)
5. **Worker writes result** to Valkey with the correlation ID
6. **API polls for result** (timeout: 10 seconds)
7. **API asserts** the result contains expected fields (available=false, registrar present, confidence=high)

```json
{
  "status": "healthy",
  "synthetic_check": {
    "domain": "example.com",
    "result": "completed",
    "total_latency_ms": 2340,
    "steps": {
      "enqueue": {"status": "ok", "latency_ms": 5},
      "worker_pickup": {"status": "ok", "latency_ms": 120},
      "dns_resolution": {"status": "ok", "latency_ms": 45},
      "rdap_lookup": {"status": "ok", "latency_ms": 1800},
      "result_delivery": {"status": "ok", "latency_ms": 370}
    }
  }
}
```

If this passes, **every service in the stack is proven working**: API → Valkey → Worker → Unbound → rust-whois → Valkey → API.

### Circuit breaker:

- If the last 3 synthetic transactions failed → stop running them for 60 seconds
- Return cached "degraded" status during cooldown
- This prevents a broken synthetic check from itself becoming a load source

## DNS Known-Answer Testing with `.test` TLD

RFC 2606 reserves `.test` specifically for testing DNS systems. It will never be registered publicly.

### Unbound configuration:

```
# Add to unbound.conf
server:
    local-zone: "_healthcheck.test." static
    local-data: "_healthcheck.test. A 127.0.0.1"
```

When the health check resolves `_healthcheck.test` through Unbound:
- If it returns `127.0.0.1` → Unbound is working
- If it returns NXDOMAIN → local zone config is missing
- If it times out → Unbound is down or unreachable

This never touches the public internet. No rate limits. No external dependency.

### Using `example.com` for RDAP known-answer testing:

`example.com` is an IANA-reserved domain (RFC 2606). Its RDAP data is:
- Always present (IANA manages it directly)
- Stable (doesn't change)
- Not rate-limited (IANA's RDAP bootstrap serves it)
- Contains predictable fields (registrar: IANA, nameservers present)

This makes it the ideal target for verifying the rust-whois RDAP pipeline.

## Public vs Internal Endpoints

| Endpoint | Public? | Auth? | Purpose |
|----------|---------|-------|---------|
| `/health` | Yes | No | Third-party uptime monitoring (Pingdom, etc.) |
| `/mcp/health` | Yes | No | Third-party MCP monitoring |
| `/health/ready` | Yes | No | Detailed component status for dashboards |
| `/health/deep` | No* | API key | Synthetic transaction — enqueues real jobs |

*`/health/deep` should be rate-limited to 1 request/minute even with a valid API key, to prevent it from becoming a load source.

## Fail-Open Behavior

**Critical rule**: If ALL dependency checks fail simultaneously, keep the API online. Return degraded status but continue serving:
- Cached results from Valkey (if Valkey is still readable)
- DNS-only results (if Unbound works but rust-whois is down)
- Partial results with reduced confidence

Only return 503 if the API process itself is broken (can't parse requests, event loop deadlocked).

**Rationale**: A shared dependency going down (e.g., Valkey) is not a reason to take the entire API offline. Some functionality may still work, and taking everything down guarantees zero availability rather than partial availability.

## Implementation Priority

1. **Phase 1** (do now): Add `/health/ready` to the API with Valkey ping, worker heartbeat check, and DNS resolution test
2. **Phase 2** (next): Configure Unbound with `_healthcheck.test` local zone, add RDAP known-answer test for `example.com`
3. **Phase 3** (later): Add `/health/deep` synthetic transaction endpoint with circuit breaker
4. **Phase 4** (polish): Add per-step latency tracking, historical baseline comparison, Grafana dashboard for health check metrics

## References

- [AWS Builders' Library: Implementing Health Checks](https://aws.amazon.com/builders-library/implementing-health-checks/)
- [RFC 2606: Reserved Top Level DNS Names](https://www.rfc-editor.org/rfc/rfc2606.html)
- [Azure: Health Endpoint Monitoring Pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/health-endpoint-monitoring)
- [Colin Breck: K8s Probes — How to Avoid Shooting Yourself in the Foot](https://blog.colinbreck.com/kubernetes-liveness-and-readiness-probes-how-to-avoid-shooting-yourself-in-the-foot/)
- [Datadog: DNS Testing](https://docs.datadoghq.com/synthetics/api_tests/dns_tests/)
