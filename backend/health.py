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

        # Check heartbeat freshness
        stale_workers = 0
        now = datetime.now(timezone.utc)
        for w in workers:
            hb = w.last_heartbeat
            if hb and (now - hb).total_seconds() > 120:
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

    components = {
        "valkey": valkey,
        "dns_resolver": dns,
        "whois_service": whois,
        "workers": workers,
    }

    # Determine overall status
    statuses = [c["status"] for c in components.values()]
    if all(s == "ok" for s in statuses):
        overall = "healthy"
    elif valkey["status"] == "error" or workers["status"] == "error":
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


@router.get("/health/deep")
def health_deep():
    """Tier 3: Synthetic transaction — full pipeline verification.

    Enqueues a real lookup for example.com, waits for completion,
    and verifies the result contains expected data.
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
        # Step 1: Enqueue synthetic job
        t0 = time.monotonic()
        v = get_valkey()
        job_id = create_job(
            job_id=correlation_id,
            consumer="healthcheck",
            domains=["example.com"],
        )
        steps["enqueue"] = {"status": "ok", "latency_ms": round((time.monotonic() - t0) * 1000)}

        # Step 2: Poll for completion (timeout: 15 seconds)
        t0 = time.monotonic()
        poll_timeout = 15.0
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

        # Step 3: Verify result
        t0 = time.monotonic()
        results_list = result if isinstance(result, list) else [result]
        example_result = results_list[0] if results_list else {}

        if example_result.get("available") is False:
            steps["verification"] = {"status": "ok", "latency_ms": round((time.monotonic() - t0) * 1000)}
        else:
            steps["verification"] = {
                "status": "error",
                "latency_ms": round((time.monotonic() - t0) * 1000),
                "error": f"expected available=False for example.com, got {example_result.get('available')}",
            }
            raise Exception("Unexpected result for example.com")

        total_ms = round((time.monotonic() - start) * 1000)

        # Reset circuit breaker on success
        _deep_circuit["failures"] = 0

        return {
            "status": "healthy",
            "synthetic_check": {
                "domain": "example.com",
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
                "domain": "example.com",
                "result": "failed",
                "total_latency_ms": total_ms,
                "steps": steps,
                "error": str(e),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
