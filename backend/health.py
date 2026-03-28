"""
Tiered health check endpoints for CanYouGrab.it.

Tier 1: /health          — Liveness. Process alive? (already in app.py)
Tier 2: /health/ready    — Readiness. Per-component deep checks with caching.
Tier 3: /health/deep     — Synthetic transaction. Full pipeline verification.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone

import dns.resolver
import dns.exception
import httpx
from fastapi import APIRouter, Request

from valkey_client import get_valkey, get_rq_connection, create_job, get_job_status, get_job_results, QUEUE_NAME

logger = logging.getLogger(__name__)

router = APIRouter()

# Cache for readiness check results (avoid hammering dependencies)
_ready_cache = {"result": None, "expires_at": 0}
READY_CACHE_TTL = 10  # seconds

# Circuit breaker for /health/deep
_deep_circuit = {"failures": 0, "open_until": 0}
DEEP_FAILURE_THRESHOLD = 3
DEEP_COOLDOWN_SECONDS = 60

# DNS resolver config (same as dns_client.py)
DNS_RESOLVER_HOSTNAME = os.environ.get('DNS_RESOLVER_HOSTNAME', 'unbound.canyougrab.internal')
DNS_RESOLVER_PORT = int(os.environ.get('DNS_RESOLVER_PORT', '53'))

# WHOIS service config (same as whois_client.py)
WHOIS_HOSTNAME = os.environ.get('WHOIS_HOSTNAME', 'rust-whois.canyougrab.internal')
WHOIS_PORT = int(os.environ.get('WHOIS_PORT', '3000'))


def _check_valkey() -> dict:
    """Tier 2: Valkey read/write check (not just PING)."""
    start = time.monotonic()
    try:
        v = get_valkey()
        test_key = "_healthcheck:readiness"
        v.set(test_key, "ok", ex=60)
        val = v.get(test_key)
        latency_ms = round((time.monotonic() - start) * 1000)
        if val == "ok":
            return {"status": "ok", "latency_ms": latency_ms}
        return {"status": "error", "latency_ms": latency_ms, "error": f"unexpected value: {val}"}
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": str(e)}


def _check_bloom() -> dict:
    """Tier 2: Zone bloom filter availability check."""
    start = time.monotonic()
    try:
        from zone_bloom import check_domain_bloom, meta_key
        v = get_valkey()

        # Check which TLDs have bloom filters loaded
        loaded_tlds = []
        for tld in ['com', 'net', 'org']:
            meta = v.hgetall(meta_key(tld))
            if meta:
                count = meta.get(b'domains_loaded') or meta.get('domains_loaded', '0')
                loaded_tlds.append({"tld": tld, "domains": int(count)})

        latency_ms = round((time.monotonic() - start) * 1000)

        if not loaded_tlds:
            return {"status": "degraded", "latency_ms": latency_ms, "message": "no bloom filters loaded"}

        # Verify a known domain returns True
        result = check_domain_bloom(v, "google.com")
        if result is True:
            return {"status": "ok", "latency_ms": latency_ms, "tlds": loaded_tlds}
        elif result is False:
            return {"status": "error", "latency_ms": latency_ms, "error": "google.com not found in .com bloom filter"}
        else:
            return {"status": "degraded", "latency_ms": latency_ms, "message": "bloom filter not loaded for .com", "tlds": loaded_tlds}

    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "degraded", "latency_ms": latency_ms, "error": str(e)}


def _check_dns() -> dict:
    """Tier 2: DNS known-answer test. Resolve _healthcheck.test via Unbound."""
    start = time.monotonic()
    try:
        import socket
        resolver_ip = socket.gethostbyname(DNS_RESOLVER_HOSTNAME)
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [resolver_ip]
        resolver.port = DNS_RESOLVER_PORT
        resolver.timeout = 3.0
        resolver.lifetime = 3.0

        answer = resolver.resolve("_healthcheck.test", "A")
        result_ip = str(answer[0])
        latency_ms = round((time.monotonic() - start) * 1000)

        if result_ip == "127.0.0.1":
            return {"status": "ok", "latency_ms": latency_ms}
        return {"status": "error", "latency_ms": latency_ms, "error": f"unexpected: {result_ip}"}

    except dns.resolver.NXDOMAIN:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": "NXDOMAIN — _healthcheck.test local zone not configured in Unbound"}

    except dns.exception.Timeout:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": "timeout — Unbound unreachable"}

    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": str(e)}


def _check_whois() -> dict:
    """Tier 2: RDAP known-answer test. Query example.com via rust-whois."""
    start = time.monotonic()
    try:
        import socket
        whois_ip = socket.gethostbyname(WHOIS_HOSTNAME)
        url = f"http://{whois_ip}:{WHOIS_PORT}/whois/example.com"
        resp = httpx.get(url, timeout=5.0)
        latency_ms = round((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            parsed = data.get("parsed_data") or {}
            if parsed.get("registrar") or parsed.get("name_servers"):
                return {"status": "ok", "latency_ms": latency_ms}
            return {"status": "degraded", "latency_ms": latency_ms, "error": "response missing expected fields"}
        return {"status": "error", "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}"}

    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": str(e)}


# MCP health check URL — checks the public ingress path (same route ChatGPT uses)
# Falls back to localhost if no public hostname is configured
MCP_HEALTH_URL = os.environ.get('MCP_HEALTH_URL', '')

_MCP_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "id": "_healthcheck",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "healthcheck", "version": "1.0"},
    },
}
_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _check_mcp() -> dict:
    """Tier 2: MCP server liveness — checks BOTH local sidecar AND public ingress path.

    Local check proves the MCP process is running.
    Public check proves the full path works (ingress → service → pod) — the same
    route that ChatGPT/Claude use. A 502 at the ingress would only be caught here.
    """
    start = time.monotonic()
    mcp_port = int(os.environ.get('MCP_PORT', '8001'))
    checks = {}

    # Check 1: Local sidecar (proves process is alive)
    try:
        resp = httpx.post(
            f"http://127.0.0.1:{mcp_port}/mcp",
            headers=_MCP_HEADERS,
            json=_MCP_INIT_PAYLOAD,
            timeout=5.0,
        )
        latency_ms = round((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            checks["local"] = {"status": "ok", "latency_ms": latency_ms}
        else:
            checks["local"] = {"status": "error", "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        checks["local"] = {"status": "error", "latency_ms": latency_ms, "error": str(e)}

    # Check 2: Public ingress path (proves end-to-end routing works)
    if MCP_HEALTH_URL:
        t0 = time.monotonic()
        try:
            resp = httpx.post(
                MCP_HEALTH_URL,
                headers=_MCP_HEADERS,
                json=_MCP_INIT_PAYLOAD,
                timeout=10.0,
                follow_redirects=True,
            )
            latency_ms = round((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                checks["public"] = {"status": "ok", "latency_ms": latency_ms}
            else:
                checks["public"] = {"status": "error", "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            latency_ms = round((time.monotonic() - t0) * 1000)
            checks["public"] = {"status": "error", "latency_ms": latency_ms, "error": str(e)}

    total_ms = round((time.monotonic() - start) * 1000)

    # Both must pass
    all_ok = all(c["status"] == "ok" for c in checks.values())
    any_error = any(c["status"] == "error" for c in checks.values())

    if all_ok:
        return {"status": "ok", "latency_ms": total_ms, "checks": checks}
    elif any_error:
        first_error = next(c.get("error") for c in checks.values() if c.get("error"))
        return {"status": "error", "latency_ms": total_ms, "error": first_error, "checks": checks}
    return {"status": "degraded", "latency_ms": total_ms, "checks": checks}


def _check_workers() -> dict:
    """Tier 2: Check RQ worker heartbeats via Valkey."""
    start = time.monotonic()
    try:
        from rq.worker import Worker
        rq_conn = get_rq_connection()
        workers = Worker.all(connection=rq_conn)
        latency_ms = round((time.monotonic() - start) * 1000)

        active = len(workers)
        busy = sum(1 for w in workers if w.get_state() == "busy")

        if active == 0:
            return {"status": "error", "latency_ms": latency_ms, "active": 0, "busy": 0, "error": "no workers registered"}

        # Check heartbeat freshness (RQ heartbeats may be timezone-naive)
        stale_workers = 0
        now_utc = datetime.now(timezone.utc)
        now_naive = datetime.utcnow()
        for w in workers:
            hb = w.last_heartbeat
            if hb is None:
                stale_workers += 1
                continue
            # Handle both tz-aware and tz-naive heartbeats
            if hb.tzinfo is None:
                age = (now_naive - hb).total_seconds()
            else:
                age = (now_utc - hb).total_seconds()
            if age > 120:
                stale_workers += 1

        if stale_workers == active:
            return {"status": "error", "latency_ms": latency_ms, "active": active, "busy": busy, "error": f"all {active} workers have stale heartbeats"}
        if stale_workers > 0:
            return {"status": "degraded", "latency_ms": latency_ms, "active": active, "busy": busy, "stale": stale_workers}

        return {"status": "ok", "latency_ms": latency_ms, "active": active, "busy": busy}

    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": str(e)}


@router.get("/health/ready")
def health_ready():
    """Tier 2: Deep readiness check with per-component status and caching."""
    now = time.time()

    # Return cached result if fresh
    if _ready_cache["result"] and now < _ready_cache["expires_at"]:
        return _ready_cache["result"]

    valkey = _check_valkey()
    dns = _check_dns()
    whois = _check_whois()
    workers = _check_workers()
    mcp = _check_mcp()
    bloom = _check_bloom()

    components = {
        "valkey": valkey,
        "dns_resolver": dns,
        "whois_service": whois,
        "workers": workers,
        "mcp_server": mcp,
        "zone_bloom": bloom,
    }

    # Determine overall status
    statuses = [c["status"] for c in components.values()]
    if all(s == "ok" for s in statuses):
        overall = "healthy"
    elif valkey["status"] == "error" or workers["status"] == "error" or mcp["status"] == "error":
        # Critical path broken
        overall = "unhealthy"
    else:
        overall = "degraded"

    result = {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": components,
    }

    # Cache the result
    _ready_cache["result"] = result
    _ready_cache["expires_at"] = now + READY_CACHE_TTL

    return result


# Synthetic test domains — each exercises a specific service path:
# Synthetic domains exercise every service path:
# 1. google.com — bloom filter hit (registered .com, skips DNS+RDAP entirely)
# 2. example.com — registered via DNS (may also bloom-hit, proves Unbound works)
# 3. _healthcheck-not-registered.com — bloom miss → DNS NXDOMAIN → RDAP 404 (proves RDAP path)
# 4. _healthcheck-not-registered.us — bloom miss → DNS NXDOMAIN → no RDAP → legacy WHOIS
# 5. _healthcheck-bloom-miss.net — bloom miss for .net (proves .net filter works on misses)
SYNTHETIC_DOMAINS = [
    {"domain": "google.com", "expect_available": False, "path": "bloom", "description": "Zone bloom filter hit (.com registered)"},
    {"domain": "example.com", "expect_available": False, "path": "dns", "description": "Unbound DNS (registered .com)"},
    {"domain": "_healthcheck-not-registered.com", "expect_available": True, "path": "rdap", "description": "Bloom miss → RDAP via rust-whois (unregistered .com)"},
    {"domain": "_healthcheck-not-registered.us", "expect_available": True, "path": "whois", "description": "Legacy WHOIS fallback (unregistered .us, no RDAP)"},
]


@router.get("/health/deep")
def health_deep():
    """Tier 3: Synthetic transaction — full pipeline verification.

    Enqueues three domain lookups to exercise every service path:
    1. example.com → DNS NOERROR → proves Unbound works
    2. _healthcheck-not-registered.com → NXDOMAIN → RDAP 404 → proves RDAP path
    3. _healthcheck-not-registered.us → NXDOMAIN → no RDAP for .us → legacy WHOIS → proves WHOIS fallback
    """
    now = time.time()

    # Circuit breaker: if too many recent failures, return cached degraded status
    if now < _deep_circuit["open_until"]:
        return {
            "status": "degraded",
            "circuit_breaker": "open",
            "message": f"Circuit open after {DEEP_FAILURE_THRESHOLD} failures. Retrying in {int(_deep_circuit['open_until'] - now)}s.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    start = time.monotonic()
    correlation_id = f"_synthetic_{uuid.uuid4().hex[:8]}"
    steps = {}
    all_domains = [s["domain"] for s in SYNTHETIC_DOMAINS]

    try:
        # Step 1: Enqueue synthetic job with all test domains
        t0 = time.monotonic()
        v = get_valkey()
        job_id = create_job(
            job_id=correlation_id,
            consumer="healthcheck",
            domains=all_domains,
        )
        steps["enqueue"] = {"status": "ok", "latency_ms": round((time.monotonic() - t0) * 1000)}

        # Step 2: Poll for completion (timeout: 25 seconds — legacy WHOIS path is slow)
        t0 = time.monotonic()
        poll_timeout = 25.0
        poll_interval = 0.5
        elapsed = 0
        result = None

        while elapsed < poll_timeout:
            time.sleep(poll_interval)
            elapsed = time.monotonic() - t0
            status = get_job_status(correlation_id)
            if status and status.get("status") == "completed":
                result = get_job_results(correlation_id)
                break
            if status and status.get("status") == "failed":
                steps["processing"] = {"status": "error", "latency_ms": round(elapsed * 1000), "error": status.get("error", "unknown")}
                raise Exception("Synthetic job failed")

        if result is None:
            steps["processing"] = {"status": "error", "latency_ms": round(elapsed * 1000), "error": "timeout"}
            raise Exception("Synthetic job timed out")

        steps["processing"] = {"status": "ok", "latency_ms": round(elapsed * 1000)}

        # Step 3: Verify each domain result and its source path
        t0 = time.monotonic()
        results_list = result if isinstance(result, list) else [result]
        results_by_domain = {r.get("domain"): r for r in results_list if isinstance(r, dict)}

        verification_errors = []

        for spec in SYNTHETIC_DOMAINS:
            domain = spec["domain"]
            expected_path = spec["path"]
            expect_available = spec["expect_available"]
            description = spec["description"]
            r = results_by_domain.get(domain, {})
            actual_available = r.get("available")
            actual_source = r.get("source", "unknown")

            step_key = f"{expected_path}_path"
            step_data = {
                "domain": domain,
                "description": description,
                "available": actual_available,
                "source": actual_source,
                "expected_source": expected_path,
            }

            # Check availability matches expectation
            if actual_available is None:
                step_data["status"] = "degraded"
                step_data["error"] = f"got available=None (expected {expect_available})"
            elif actual_available != expect_available:
                step_data["status"] = "error"
                step_data["error"] = f"expected available={expect_available}, got {actual_available}"
                verification_errors.append(f"{domain}: expected available={expect_available}")
            else:
                step_data["status"] = "ok"

            # Check source matches expected path (bloom, dns, rdap, or whois)
            source_map = {"bloom": "zone_bloom", "dns": "dns", "rdap": "rdap", "whois": "whois"}
            expected_source = source_map.get(expected_path, expected_path)
            if actual_source == "cache":
                # Cached result — can't verify the path, but availability is correct
                step_data["note"] = "result was cached, source path not re-verified"
            elif expected_path == "bloom" and actual_source != "zone_bloom":
                # Bloom filter expected but didn't hit — filter may not be loaded
                step_data["source_mismatch"] = True
                if actual_available == expect_available:
                    # Correct answer via fallback path — degraded, not error
                    step_data["status"] = "degraded"
                    step_data["warning"] = f"bloom filter miss, fell through to {actual_source}"
                else:
                    step_data["status"] = "error"
                    step_data["error"] = f"bloom miss + wrong answer: expected available={expect_available}"
                    verification_errors.append(f"{domain}: bloom miss + wrong answer")
            elif expected_path in ("rdap", "whois") and actual_source not in (expected_source, "cache", "zone_bloom"):
                # Source doesn't match — e.g., RDAP fell back to WHOIS or vice versa
                step_data["source_mismatch"] = True
                step_data["warning"] = f"expected source={expected_path}, got {actual_source}"
                if step_data["status"] == "ok":
                    step_data["status"] = "degraded"

            steps[step_key] = step_data

        steps["verification"] = {"status": "ok" if not verification_errors else "error", "latency_ms": round((time.monotonic() - t0) * 1000)}

        if verification_errors:
            raise Exception("; ".join(verification_errors))

        # Step 4: Enrichment check — verify inline enrichment works
        t0 = time.monotonic()
        try:
            from enrichment import enrich_results_inline
            enrich_results_inline(results_list)

            enrichment_errors = []

            # google.com should have nameservers and parked=False
            google_r = results_by_domain.get("google.com", {})
            google_ns = google_r.get("nameservers")
            if not google_ns or not isinstance(google_ns, list) or len(google_ns) == 0:
                enrichment_errors.append("google.com missing nameservers")
            if google_r.get("parked") is not False:
                enrichment_errors.append(f"google.com parked should be False, got {google_r.get('parked')}")

            # All results should have the enrichment fields
            for r in results_list:
                if "parked" not in r or "hosting_provider" not in r:
                    enrichment_errors.append(f"{r.get('domain', '?')} missing enrichment fields")
                    break

            latency_ms = round((time.monotonic() - t0) * 1000)
            if enrichment_errors:
                steps["enrichment"] = {"status": "degraded", "latency_ms": latency_ms, "errors": enrichment_errors}
            else:
                provider = google_r.get("hosting_provider")
                steps["enrichment"] = {"status": "ok", "latency_ms": latency_ms, "sample_provider": provider}

        except Exception as e:
            latency_ms = round((time.monotonic() - t0) * 1000)
            steps["enrichment"] = {"status": "error", "latency_ms": latency_ms, "error": str(e)}

        # Step 5: MCP server check — must return HTTP 200
        mcp_result = _check_mcp()
        steps["mcp_server"] = mcp_result
        if mcp_result["status"] != "ok":
            raise Exception(f"MCP server: {mcp_result.get('error', 'not healthy')}")

        total_ms = round((time.monotonic() - start) * 1000)

        # Reset circuit breaker on success
        _deep_circuit["failures"] = 0

        # Overall status: healthy if all ok, degraded if any degraded, unhealthy if any error
        path_statuses = [steps.get(f"{s['path']}_path", {}).get("status", "ok") for s in SYNTHETIC_DOMAINS]
        if all(s == "ok" for s in path_statuses):
            overall = "healthy"
        elif "error" in path_statuses:
            overall = "unhealthy"
        else:
            overall = "degraded"

        return {
            "status": overall,
            "synthetic_check": {
                "domains": all_domains,
                "result": "completed",
                "total_latency_ms": total_ms,
                "steps": steps,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        total_ms = round((time.monotonic() - start) * 1000)

        # Increment circuit breaker
        _deep_circuit["failures"] += 1
        if _deep_circuit["failures"] >= DEEP_FAILURE_THRESHOLD:
            _deep_circuit["open_until"] = time.time() + DEEP_COOLDOWN_SECONDS

        return {
            "status": "unhealthy",
            "synthetic_check": {
                "domains": all_domains,
                "result": "failed",
                "total_latency_ms": total_ms,
                "steps": steps,
                "error": str(e),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
