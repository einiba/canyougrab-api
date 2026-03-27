"""
Zone file bloom filter — fast domain registration lookup using Valkey bitfields.

Uses a manual bloom filter implementation over Valkey SETBIT/GETBIT commands.
No Redis modules required. Works on vanilla Valkey/Redis.

Each TLD gets its own bloom filter key: zone:bloom:{tld}
A staging key (zone:bloom:{tld}:staging) is built first, then atomically
renamed to replace the live filter.

False positive rate: ~0.1% with k=7 hash functions and m/n ratio of ~10.
False negatives: impossible (bloom filter guarantee).
"""

import logging
import math
import struct

import xxhash
from typing import Optional

logger = logging.getLogger(__name__)

# Bloom filter parameters
FALSE_POSITIVE_RATE = 0.001  # 0.1%
NUM_HASHES = 7  # Optimal for 0.1% FP rate

BLOOM_KEY_PREFIX = "zone:bloom"
BLOOM_META_PREFIX = "zone:meta"


def _optimal_size(num_items: int, fp_rate: float = FALSE_POSITIVE_RATE) -> int:
    """Calculate optimal bloom filter bit count for given items and FP rate."""
    if num_items <= 0:
        return 1024
    m = -1 * (num_items * math.log(fp_rate)) / (math.log(2) ** 2)
    return int(math.ceil(m))


def _hash_positions(domain: str, filter_size: int, k: int = NUM_HASHES) -> list[int]:
    """Generate k bit positions for a domain using double hashing.

    Uses SHA-256 split into two 64-bit hashes, then generates k positions
    via: h(i) = (h1 + i * h2) % filter_size
    """
    digest = xxhash.xxh3_128(domain.encode('ascii', errors='ignore')).digest()
    h1 = struct.unpack_from('<Q', digest, 0)[0]
    h2 = struct.unpack_from('<Q', digest, 8)[0]

    positions = []
    for i in range(k):
        pos = (h1 + i * h2) % filter_size
        positions.append(pos)
    return positions


def bloom_key(tld: str, staging: bool = False) -> str:
    """Get the Valkey key for a TLD's bloom filter."""
    suffix = ":staging" if staging else ""
    return f"{BLOOM_KEY_PREFIX}:{tld}{suffix}"


def meta_key(tld: str) -> str:
    """Get the Valkey key for a TLD's bloom filter metadata."""
    return f"{BLOOM_META_PREFIX}:{tld}"


def check_domain_bloom(valkey_client, domain: str) -> Optional[bool]:
    """Check if a domain is in the bloom filter.

    Returns:
        True  — domain is probably registered (may be false positive, 0.1%)
        False — domain is definitely NOT registered (guaranteed)
        None  — no bloom filter for this TLD
    """
    parts = domain.lower().strip().rstrip('.').split('.')
    if len(parts) < 2:
        return None

    tld = parts[-1]
    sld = parts[-2]  # We store just the SLD in the filter
    key = bloom_key(tld)

    # Check if filter exists (cached metadata)
    meta = valkey_client.hgetall(meta_key(tld))
    if not meta:
        return None

    filter_size = int(meta.get(b'filter_size') or meta.get('filter_size') or 0)
    if filter_size == 0:
        return None

    # Check all k bit positions — all must be 1 for a match
    positions = _hash_positions(sld, filter_size)

    pipe = valkey_client.pipeline(transaction=False)
    for pos in positions:
        pipe.getbit(key, pos)
    results = pipe.execute()

    if all(results):
        return True  # Probably registered (0.1% false positive)
    return False  # Definitely not registered


def build_bloom_filter(
    valkey_client,
    tld: str,
    domains,
    expected_count: int = None,
) -> dict:
    """Build a bloom filter for a TLD from domain SLDs.

    Builds the entire bitfield in local memory, then uploads to Valkey in
    one SET command. This is orders of magnitude faster than individual SETBIT
    calls over the network (~30s vs ~5 hours for 169M .com domains).

    Args:
        valkey_client: Valkey connection
        tld: The TLD (e.g., 'com')
        domains: Iterator/list of SLD strings (e.g., ['google', 'amazon', ...])
        expected_count: Expected number of domains (for sizing). If None, uses len(domains).

    Returns:
        Dict with stats: {filter_size, domains_loaded, false_positive_rate}
    """
    count = expected_count or (len(domains) if hasattr(domains, '__len__') else 100_000)
    filter_size = _optimal_size(count)
    num_bytes = (filter_size + 7) // 8

    logger.info('Building bloom filter for .%s: ~%d domains, %d bits (%.1f MB) — building in memory',
                tld, count, filter_size, num_bytes / 1024 / 1024)

    # Build bitfield in local memory (fast — no network)
    bitfield = bytearray(num_bytes)
    loaded = 0

    for domain in domains:
        sld = domain.lower().strip().rstrip('.')
        if not sld:
            continue

        positions = _hash_positions(sld, filter_size)
        for pos in positions:
            byte_idx = pos >> 3  # pos // 8
            bit_idx = 7 - (pos & 7)  # big-endian bit order (Valkey convention)
            bitfield[byte_idx] |= (1 << bit_idx)

        loaded += 1
        if loaded % 5_000_000 == 0:
            logger.info('.%s bloom: %dM domains hashed...', tld, loaded // 1_000_000)

    logger.info('.%s bloom: %d domains hashed, uploading %.1f MB to Valkey...',
                tld, loaded, num_bytes / 1024 / 1024)

    # Upload in one shot — Valkey SET with binary data
    staging = bloom_key(tld, staging=True)
    valkey_client.delete(staging)
    valkey_client.set(staging, bytes(bitfield))

    logger.info('.%s bloom: uploaded, verifying...', tld)

    # Free memory
    del bitfield

    # Verify with known domains using GETBIT (reads from the uploaded data)
    verification_passed = True
    verify_domains = {
        'com': ['google', 'amazon', 'facebook', 'microsoft', 'apple'],
        'net': ['speedtest', 'cloudflare', 'wordpress', 'sourceforge'],
        'org': ['wikipedia', 'mozilla', 'apache', 'linux'],
    }

    known = verify_domains.get(tld, [])
    for d in known:
        positions = _hash_positions(d, filter_size)
        pipe = valkey_client.pipeline(transaction=False)
        for pos in positions:
            pipe.getbit(staging, pos)
        results = pipe.execute()
        if not all(results):
            logger.error('VERIFICATION FAILED: %s.%s not found in bloom filter!', d, tld)
            verification_passed = False

    if not verification_passed:
        logger.error('Bloom filter verification failed for .%s — aborting, keeping old filter', tld)
        valkey_client.delete(staging)
        return {"error": "verification_failed", "tld": tld}

    # Atomic swap: staging → live
    live_key = bloom_key(tld)
    valkey_client.rename(staging, live_key)

    # Update metadata
    valkey_client.hset(meta_key(tld), mapping={
        'filter_size': str(filter_size),
        'domains_loaded': str(loaded),
        'num_hashes': str(NUM_HASHES),
        'fp_rate': str(FALSE_POSITIVE_RATE),
    })

    logger.info('.%s bloom: LIVE — %d domains, %d bits (%.1f MB)',
                tld, loaded, filter_size, filter_size / 8 / 1024 / 1024)

    return {
        "tld": tld,
        "filter_size": filter_size,
        "domains_loaded": loaded,
        "size_mb": round(num_bytes / 1024 / 1024, 1),
        "false_positive_rate": FALSE_POSITIVE_RATE,
    }
