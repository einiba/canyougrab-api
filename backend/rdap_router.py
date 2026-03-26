"""
RDAP/WHOIS adaptive routing — real-time TLD classification.

Uses Valkey counters with 180s TTL to track per-TLD RDAP success/failure
rates. Domains are classified into RDAP (fast) or WHOIS (slow) queues
based on:
1. Whether the TLD has a known RDAP server
2. Whether RDAP failure rate exceeds 30% in the last 3 minutes

This feeds the split-queue pipeline: RDAP-capable TLDs go to the fast
queue, WHOIS-only or degraded TLDs go to the slow queue.
"""

import logging
from tld_registry import get_rdap_server, is_whois_disabled
from valkey_client import get_valkey

logger = logging.getLogger(__name__)

# Adaptive routing thresholds
FAILURE_RATE_THRESHOLD = 0.30   # Route to WHOIS if >30% failure
COUNTER_TTL = 180               # 3-minute sliding window
MIN_SAMPLES = 5                 # Need at least 5 lookups to judge


def record_rdap_result(tld: str, success: bool):
    """Record an RDAP lookup result for adaptive routing.

    Called from lookup.py alongside record_rdap_outcome().
    Uses Valkey INCR with TTL for a ~3-minute sliding window.
    """
    try:
        v = get_valkey()
        key = f"rdap:ok:{tld}" if success else f"rdap:fail:{tld}"
        pipe = v.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, COUNTER_TTL)
        pipe.execute()
    except Exception as e:
        logger.debug("Failed to record RDAP result for %s: %s", tld, e)


def get_tld_failure_rate(tld: str) -> float:
    """Get the RDAP failure rate for a TLD over the last ~3 minutes.

    Returns 0.0 if no data (default to RDAP — optimistic).
    """
    try:
        v = get_valkey()
        ok = int(v.get(f"rdap:ok:{tld}") or 0)
        fail = int(v.get(f"rdap:fail:{tld}") or 0)
        total = ok + fail
        if total < MIN_SAMPLES:
            return 0.0  # Not enough data — default to RDAP
        return fail / total
    except Exception:
        return 0.0  # Valkey error — default to RDAP


def should_route_to_whois(tld: str) -> bool:
    """Check if a TLD should be routed to the WHOIS queue due to high RDAP failure rate."""
    return get_tld_failure_rate(tld) > FAILURE_RATE_THRESHOLD


def classify_domains(domains: list[str]) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Classify domains into RDAP (fast) and WHOIS (slow) batches.

    Returns:
        (rdap_batch, whois_batch) — each is a list of (original_index, domain) tuples.
        Indices are preserved for result reassembly.

    Routing logic per domain:
        1. No RDAP server known for TLD → WHOIS queue
        2. WHOIS disabled for TLD → RDAP queue (skip WHOIS entirely)
        3. RDAP failure rate > 30% in 3 min → WHOIS queue (adaptive)
        4. Otherwise → RDAP queue
    """
    rdap_batch: list[tuple[int, str]] = []
    whois_batch: list[tuple[int, str]] = []

    for i, domain in enumerate(domains):
        domain = domain.lower().strip().rstrip('.')
        parts = domain.rsplit('.', 1)
        tld = parts[-1] if len(parts) > 1 else ''

        if not tld:
            # Invalid domain — send to RDAP queue (will fail fast at DNS)
            rdap_batch.append((i, domain))
            continue

        # Rule 1: No RDAP server → WHOIS queue
        rdap_server = get_rdap_server(tld)
        if rdap_server is None:
            # Check IANA bootstrap (TLD might have RDAP but not in our registry)
            # For now, assume no registry entry = no RDAP
            whois_batch.append((i, domain))
            continue

        # Rule 2: WHOIS disabled → RDAP queue (even if RDAP is failing)
        if is_whois_disabled(tld):
            rdap_batch.append((i, domain))
            continue

        # Rule 3: Adaptive — high RDAP failure rate → WHOIS queue
        if should_route_to_whois(tld):
            whois_batch.append((i, domain))
            continue

        # Rule 4: Default → RDAP queue
        rdap_batch.append((i, domain))

    if rdap_batch and whois_batch:
        logger.info("Split %d domains: %d RDAP, %d WHOIS",
                     len(domains), len(rdap_batch), len(whois_batch))

    return rdap_batch, whois_batch
