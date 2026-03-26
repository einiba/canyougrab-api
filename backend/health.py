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


def _check_mcp() -> dict:
    """Tier 2: MCP server liveness check. POST to /mcp and expect a JSON-RPC response."""
    start = time.monotonic()
    mcp_port = int(os.environ.get('MCP_PORT', '8001'))
    try:
        resp = httpx.post(
            f"http://127.0.0.1:{mcp_port}/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": "_healthcheck",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "healthcheck", "version": "1.0"},
                },
            },
            timeout=5.0,
        )
        latency_ms = round((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            return {"status": "ok", "latency_ms": latency_ms}
        return {"status": "error", "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}"}

    except httpx.ConnectError:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": f"MCP server not listening on port {mcp_port}"}

    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": str(e)}


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

    components = {
        "valkey": valkey,
        "dns_resolver": dns,
        "whois_service": whois,
        "workers": workers,
        "mcp_server": mcp,
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


# Synthetic test domains:
# - example.com: registered, exercises DNS (Unbound) → expects available=False
# - _healthcheck-not-registered.com: unregistered, exercises DNS (NXDOMAIN) → rust-whois (RDAP 404) → expects available=True
SYNTHETIC_REGISTERED = "example.com"
SYNTHETIC_AVAILABLE = "_healthcheck-not-registered.com"


@router.get("/health/deep")
def health_deep():
    """Tier 3: Synthetic transaction — full pipeline verification.

    Enqueues two domain lookups to exercise every service:
    1. example.com (registered) — DNS returns NOERROR → proves Unbound works
    2. _healthcheck-not-registered.com (unregistered) — DNS returns NXDOMAIN
       → worker calls rust-whois RDAP → RDAP returns 404 → proves rust-whois works
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

    try:
        # Step 1: Enqueue synthetic job with both test domains
        t0 = time.monotonic()
        v = get_valkey()
        job_id = create_job(
            job_id=correlation_id,
            consumer="healthcheck",
            domains=[SYNTHETIC_REGISTERED, SYNTHETIC_AVAILABLE],
        )
        steps["enqueue"] = {"status": "ok", "latency_ms": round((time.monotonic() - t0) * 1000)}

        # Step 2: Poll for completion (timeout: 20 seconds — WHOIS path is slower)
        t0 = time.monotonic()
        poll_timeout = 20.0
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

        # Step 3: Verify both results
        t0 = time.monotonic()
        results_list = result if isinstance(result, list) else [result]
        results_by_domain = {r.get("domain"): r for r in results_list if isinstance(r, dict)}

        verification_errors = []

        # Verify registered domain (exercises Unbound)
        reg = results_by_domain.get(SYNTHETIC_REGISTERED, {})
        if reg.get("available") is False:
            steps["dns_path"] = {"status": "ok", "domain": SYNTHETIC_REGISTERED, "available": False}
        else:
            steps["dns_path"] = {"status": "error", "domain": SYNTHETIC_REGISTERED, "error": f"expected available=False, got {reg.get('available')}"}
            verification_errors.append(f"{SYNTHETIC_REGISTERED}: expected registered")

        # Verify unregistered domain (exercises Unbound + rust-whois)
        avail = results_by_domain.get(SYNTHETIC_AVAILABLE, {})
        if avail.get("available") is True:
            steps["whois_path"] = {"status": "ok", "domain": SYNTHETIC_AVAILABLE, "available": True}
        elif avail.get("available") is None:
            # WHOIS might have timed out or errored — service reachable but degraded
            steps["whois_path"] = {"status": "degraded", "domain": SYNTHETIC_AVAILABLE, "available": None, "error": avail.get("error", "whois returned None")}
        else:
            steps["whois_path"] = {"status": "error", "domain": SYNTHETIC_AVAILABLE, "error": f"expected available=True, got {avail.get('available')}"}
            verification_errors.append(f"{SYNTHETIC_AVAILABLE}: expected available")

        steps["verification"] = {"status": "ok" if not verification_errors else "error", "latency_ms": round((time.monotonic() - t0) * 1000)}

        if verification_errors:
            raise Exception("; ".join(verification_errors))

        # Step 4: MCP server check — must return HTTP 200
        mcp_result = _check_mcp()
        steps["mcp_server"] = mcp_result
        if mcp_result["status"] != "ok":
            raise Exception(f"MCP server: {mcp_result.get('error', 'not healthy')}")

        total_ms = round((time.monotonic() - start) * 1000)

        # Reset circuit breaker on success
        _deep_circuit["failures"] = 0

        # Determine overall: healthy if all paths ok, degraded if whois path degraded
        whois_status = steps.get("whois_path", {}).get("status", "ok")
        overall = "healthy" if whois_status == "ok" else "degraded"

        return {
            "status": overall,
            "synthetic_check": {
                "domains": [SYNTHETIC_REGISTERED, SYNTHETIC_AVAILABLE],
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
                "domains": [SYNTHETIC_REGISTERED, SYNTHETIC_AVAILABLE],
                "result": "failed",
                "total_latency_ms": total_ms,
                "steps": steps,
                "error": str(e),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
