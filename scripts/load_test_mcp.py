#!/usr/bin/env python3
"""
Load Test — exercises the full pipeline via REST API and optionally MCP.

Sends concurrent bulk domain check requests with a configurable mix of
registered and unregistered domains to exercise both Unbound (DNS) and
rust-whois (RDAP) equally.

Usage:
    # REST API (default — exercises full pipeline including workers)
    python3 load_test_mcp.py --url https://api.canyougrab.it \
                             --api-key cyg_... \
                             --concurrency 10 \
                             --batches 20 \
                             --domains-per-batch 50

    # MCP endpoint (requires OAuth token, not API key)
    python3 load_test_mcp.py --url https://api.canyougrab.it/mcp \
                             --mode mcp \
                             --api-key cyg_... \
                             --concurrency 5

The test generates random unregistered .com domains (hit Unbound + rust-whois)
and mixes in known registered domains (hit Unbound only).

After the test, it prints a summary of latencies, throughput, error rates,
and service-level breakdown (cache / dns-only / whois).
"""

import argparse
import asyncio
import json
import random
import string
import time
import uuid
from dataclasses import dataclass, field

import httpx

# Known registered domains (DNS returns NOERROR → Unbound only)
REGISTERED_DOMAINS = [
    "google.com", "amazon.com", "facebook.com", "apple.com", "microsoft.com",
    "netflix.com", "twitter.com", "linkedin.com", "github.com", "reddit.com",
    "wikipedia.org", "youtube.com", "instagram.com", "whatsapp.com", "zoom.us",
    "spotify.com", "dropbox.com", "slack.com", "stripe.com", "shopify.com",
    "cloudflare.com", "digitalocean.com", "heroku.com", "vercel.com", "netlify.com",
]


def random_unregistered_domain(tld: str = "com") -> str:
    """Generate a random domain that almost certainly doesn't exist."""
    prefix = "".join(random.choices(string.ascii_lowercase, k=12))
    suffix = "".join(random.choices(string.digits, k=4))
    return f"{prefix}{suffix}.{tld}"


def generate_batch(size: int, registered_ratio: float = 0.5) -> list[str]:
    """Generate a batch of domains with the given ratio of registered to unregistered."""
    n_registered = int(size * registered_ratio)
    n_unregistered = size - n_registered

    domains = []
    domains.extend(random.choices(REGISTERED_DOMAINS, k=n_registered))
    domains.extend(random_unregistered_domain() for _ in range(n_unregistered))
    random.shuffle(domains)
    return domains


@dataclass
class BatchResult:
    batch_id: int
    domains: int
    status_code: int
    latency_ms: float
    results: list = field(default_factory=list)
    error: str = ""


@dataclass
class LoadTestSummary:
    total_batches: int = 0
    total_domains: int = 0
    successful: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)
    latencies_ms: list = field(default_factory=list)
    results_by_source: dict = field(default_factory=lambda: {"cache": 0, "dns": 0, "whois": 0, "rdap": 0, "other": 0})
    results_by_available: dict = field(default_factory=lambda: {True: 0, False: 0, None: 0})
    start_time: float = 0.0
    end_time: float = 0.0


async def send_rest_check(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    domains: list[str],
    batch_id: int,
) -> BatchResult:
    """Send a bulk check via the REST API and poll for results."""
    t_start = time.monotonic()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        # Step 1: Submit bulk check
        resp = await client.post(
            f"{url}/api/check/bulk",
            json={"domains": domains},
            headers=headers,
            timeout=45.0,
        )

        if resp.status_code != 200:
            return BatchResult(
                batch_id=batch_id, domains=len(domains),
                status_code=resp.status_code,
                latency_ms=(time.monotonic() - t_start) * 1000,
                error=f"submit failed: {resp.status_code} {resp.text[:200]}",
            )

        data = resp.json()
        job_id = data.get("job_id", "")

        if not job_id:
            # Instant response (all cached)
            results = data.get("results", [])
            return BatchResult(
                batch_id=batch_id, domains=len(domains),
                status_code=200,
                latency_ms=(time.monotonic() - t_start) * 1000,
                results=results,
            )

        # Step 2: Poll for completion
        poll_url = f"{url}/api/check/bulk/{job_id}"
        for _ in range(120):  # 60 seconds max
            await asyncio.sleep(0.5)
            resp = await client.get(poll_url, headers=headers, timeout=10.0)
            if resp.status_code != 200:
                continue
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                results = data.get("results", [])
                return BatchResult(
                    batch_id=batch_id, domains=len(domains),
                    status_code=200,
                    latency_ms=(time.monotonic() - t_start) * 1000,
                    results=results,
                )
            elif status == "failed":
                return BatchResult(
                    batch_id=batch_id, domains=len(domains),
                    status_code=200,
                    latency_ms=(time.monotonic() - t_start) * 1000,
                    error=f"job failed: {data.get('error', 'unknown')}",
                )

        return BatchResult(
            batch_id=batch_id, domains=len(domains),
            status_code=0,
            latency_ms=(time.monotonic() - t_start) * 1000,
            error="poll timeout (60s)",
        )

    except httpx.TimeoutException:
        return BatchResult(
            batch_id=batch_id, domains=len(domains),
            status_code=0, latency_ms=(time.monotonic() - t_start) * 1000,
            error="timeout",
        )
    except Exception as e:
        return BatchResult(
            batch_id=batch_id, domains=len(domains),
            status_code=0, latency_ms=(time.monotonic() - t_start) * 1000,
            error=str(e),
        )


async def run_load_test(
    url: str,
    api_key: str,
    concurrency: int,
    batches: int,
    domains_per_batch: int,
    registered_ratio: float,
) -> LoadTestSummary:
    """Run the load test with the given parameters."""
    summary = LoadTestSummary()
    summary.start_time = time.monotonic()
    summary.total_batches = batches
    summary.total_domains = batches * domains_per_batch

    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=90.0) as client:
        async def run_batch(batch_id: int) -> BatchResult:
            async with semaphore:
                domains = generate_batch(domains_per_batch, registered_ratio)
                print(f"  Batch {batch_id+1:3d}/{batches} — {len(domains)} domains...", end="", flush=True)
                result = await send_rest_check(client, url, api_key, domains, batch_id)
                n_results = len(result.results)
                status = f"OK ({n_results} results)" if result.status_code == 200 and not result.error else f"ERR({result.error[:30]})"
                print(f" {result.latency_ms:7.0f}ms {status}")
                return result

        tasks = [run_batch(i) for i in range(batches)]
        results = await asyncio.gather(*tasks)

    summary.end_time = time.monotonic()

    for r in results:
        if r.status_code == 200 and not r.error:
            summary.successful += 1
        else:
            summary.failed += 1
            if r.error:
                summary.errors.append(r.error)

        summary.latencies_ms.append(r.latency_ms)

        for domain_result in r.results:
            if isinstance(domain_result, dict):
                source = domain_result.get("source", "other")
                summary.results_by_source[source] = summary.results_by_source.get(source, 0) + 1
                avail = domain_result.get("available")
                summary.results_by_available[avail] = summary.results_by_available.get(avail, 0) + 1

    return summary


def print_summary(s: LoadTestSummary):
    duration = s.end_time - s.start_time
    latencies = sorted(s.latencies_ms)

    print("\n" + "=" * 60)
    print("LOAD TEST SUMMARY")
    print("=" * 60)
    print(f"  Duration:           {duration:.1f}s")
    print(f"  Batches:            {s.successful}/{s.total_batches} successful ({s.failed} failed)")
    print(f"  Domains checked:    {s.total_domains}")
    print(f"  Throughput:         {s.total_domains / duration:.1f} domains/sec")
    print()

    if latencies:
        print("  Batch Latency:")
        print(f"    p50:   {latencies[len(latencies)//2]:7.0f}ms")
        print(f"    p90:   {latencies[int(len(latencies)*0.9)]:7.0f}ms")
        print(f"    p99:   {latencies[int(len(latencies)*0.99)]:7.0f}ms")
        print(f"    max:   {latencies[-1]:7.0f}ms")
        print(f"    avg:   {sum(latencies)/len(latencies):7.0f}ms")
    print()

    total_results = sum(s.results_by_source.values())
    if total_results > 0:
        print("  Pipeline breakdown:")
        for source, count in sorted(s.results_by_source.items(), key=lambda x: -x[1]):
            if count > 0:
                print(f"    {source:>8}: {count:5d} ({count/total_results*100:5.1f}%)")
        print()

        print("  Availability:")
        for avail, count in sorted(s.results_by_available.items(), key=lambda x: str(x[0])):
            label = {True: "available", False: "taken", None: "error/unknown"}[avail]
            print(f"    {label:>12}: {count:5d} ({count/total_results*100:5.1f}%)")
    print()

    if s.errors:
        print(f"  Errors ({len(s.errors)}):")
        # Deduplicate
        from collections import Counter
        for err, count in Counter(s.errors).most_common(5):
            print(f"    [{count}x] {err[:80]}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="MCP Server Load Test")
    parser.add_argument("--url", default="https://api.canyougrab.it", help="API base URL")
    parser.add_argument("--api-key", required=True, help="API key (cyg_...)")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent batches (default: 5)")
    parser.add_argument("--batches", type=int, default=20, help="Total batches to send (default: 20)")
    parser.add_argument("--domains-per-batch", type=int, default=50, help="Domains per batch (default: 50)")
    parser.add_argument("--registered-ratio", type=float, default=0.5,
                        help="Ratio of registered domains (0.0=all unregistered, 1.0=all registered, default: 0.5)")
    args = parser.parse_args()

    print(f"MCP Load Test")
    print(f"  Target:     {args.url}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Batches:     {args.batches} × {args.domains_per_batch} domains")
    print(f"  Total:       {args.batches * args.domains_per_batch} domains")
    print(f"  Registered:  {args.registered_ratio*100:.0f}% / Unregistered: {(1-args.registered_ratio)*100:.0f}%")
    print(f"  Expected:    Unbound={100:.0f}%, rust-whois={((1-args.registered_ratio)*100):.0f}%")
    print()

    summary = asyncio.run(run_load_test(
        url=args.url,
        api_key=args.api_key,
        concurrency=args.concurrency,
        batches=args.batches,
        domains_per_batch=args.domains_per_batch,
        registered_ratio=args.registered_ratio,
    ))

    print_summary(summary)


if __name__ == "__main__":
    main()
