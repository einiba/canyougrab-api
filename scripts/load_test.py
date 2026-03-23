#!/usr/bin/env python3
"""
Load test for dev-api.canyougrab.it /api/check/bulk endpoint.

Sends concurrent requests with randomized domain batches to stress-test
the worker pipeline (API → Valkey → RQ workers → DNS/WHOIS → response).

Usage: python3 load_test.py [--concurrency N] [--requests N] [--domains-per N]
"""

import argparse
import random
import string
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests

API_URL = 'https://dev-api.canyougrab.it/api/check/bulk'
API_KEY = 'cyg_0zNzNOAfK7CurQN_9ZGHUKot-_gr_MYSgtg6y0yX3dNDD2TgE9SW4A'

TLDS = ['com', 'net', 'org', 'io', 'ai', 'co', 'app', 'dev', 'xyz', 'me']
WORDS = [
    'cloud', 'fast', 'bright', 'wave', 'flux', 'pixel', 'nova', 'apex',
    'swift', 'drift', 'spark', 'bloom', 'lunar', 'solar', 'cyber', 'data',
    'peak', 'nest', 'core', 'edge', 'pulse', 'bolt', 'grid', 'sync',
    'link', 'dash', 'mint', 'vibe', 'glow', 'snap', 'flow', 'zoom',
]


def random_domain() -> str:
    """Generate a random plausible domain name."""
    w1 = random.choice(WORDS)
    w2 = random.choice(WORDS)
    suffix = ''.join(random.choices(string.digits, k=random.randint(0, 3)))
    tld = random.choice(TLDS)
    return f'{w1}{w2}{suffix}.{tld}'


def random_domain_batch(size: int) -> list[str]:
    """Generate a batch of random domains, with some known-registered ones mixed in."""
    domains = [random_domain() for _ in range(size)]
    # Mix in a few known domains to test the registered path
    known = ['google.com', 'amazon.com', 'github.com', 'reddit.com', 'netflix.com']
    for d in random.sample(known, min(3, size)):
        domains[random.randint(0, len(domains) - 1)] = d
    return domains


@dataclass
class Stats:
    success: int = 0
    errors: int = 0
    timeouts: int = 0
    rate_limited: int = 0
    latencies: list = field(default_factory=list)
    error_details: list = field(default_factory=list)


def send_request(domains: list[str], timeout: float = 60) -> dict:
    """Send a single bulk check request."""
    start = time.time()
    try:
        resp = requests.post(
            API_URL,
            json={'domains': domains},
            headers={
                'Authorization': f'Bearer {API_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=timeout,
        )
        elapsed = time.time() - start
        return {
            'status': resp.status_code,
            'latency': elapsed,
            'body': resp.json() if resp.headers.get('content-type', '').startswith('application/json') else resp.text,
            'domain_count': len(domains),
        }
    except requests.Timeout:
        return {'status': 0, 'latency': time.time() - start, 'body': 'timeout', 'domain_count': len(domains)}
    except Exception as e:
        return {'status': -1, 'latency': time.time() - start, 'body': str(e), 'domain_count': len(domains)}


def run_load_test(concurrency: int, total_requests: int, domains_per: int):
    """Run the load test with the given parameters."""
    stats = Stats()

    print(f'\n=== Load Test: dev-api.canyougrab.it ===')
    print(f'Concurrency: {concurrency} parallel requests')
    print(f'Total requests: {total_requests}')
    print(f'Domains per request: {domains_per}')
    print(f'Total domains: {total_requests * domains_per:,}')
    print(f'{"=" * 45}\n')

    # Pre-generate all domain batches
    batches = [random_domain_batch(domains_per) for _ in range(total_requests)]

    start_time = time.time()
    completed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(send_request, batch): i
            for i, batch in enumerate(batches)
        }

        for future in as_completed(futures):
            result = future.result()
            completed += 1
            status = result['status']

            if status == 200:
                stats.success += 1
                stats.latencies.append(result['latency'])
            elif status == 429:
                stats.rate_limited += 1
            elif status == 0:
                stats.timeouts += 1
            else:
                stats.errors += 1
                stats.error_details.append(f'HTTP {status}: {str(result["body"])[:100]}')

            # Progress update every 10 requests
            if completed % 10 == 0 or completed == total_requests:
                elapsed = time.time() - start_time
                rps = completed / elapsed if elapsed > 0 else 0
                avg_lat = sum(stats.latencies) / len(stats.latencies) if stats.latencies else 0
                sys.stdout.write(
                    f'\r  [{completed}/{total_requests}] '
                    f'{rps:.1f} req/s | '
                    f'OK:{stats.success} ERR:{stats.errors} 429:{stats.rate_limited} TOUT:{stats.timeouts} | '
                    f'avg {avg_lat:.2f}s'
                )
                sys.stdout.flush()

    total_time = time.time() - start_time
    print(f'\n\n{"=" * 45}')
    print(f'=== Results ===')
    print(f'Total time: {total_time:.1f}s')
    print(f'Requests/sec: {total_requests / total_time:.1f}')
    print(f'Domains/sec: {total_requests * domains_per / total_time:.0f}')
    print(f'')
    print(f'Success: {stats.success}/{total_requests} ({100*stats.success/total_requests:.0f}%)')
    print(f'Errors: {stats.errors}')
    print(f'Rate limited: {stats.rate_limited}')
    print(f'Timeouts: {stats.timeouts}')

    if stats.latencies:
        stats.latencies.sort()
        print(f'\nLatency (successful requests):')
        print(f'  Min:  {stats.latencies[0]:.2f}s')
        print(f'  Avg:  {sum(stats.latencies)/len(stats.latencies):.2f}s')
        print(f'  p50:  {stats.latencies[len(stats.latencies)//2]:.2f}s')
        print(f'  p90:  {stats.latencies[int(len(stats.latencies)*0.9)]:.2f}s')
        print(f'  p95:  {stats.latencies[int(len(stats.latencies)*0.95)]:.2f}s')
        print(f'  p99:  {stats.latencies[int(len(stats.latencies)*0.99)]:.2f}s')
        print(f'  Max:  {stats.latencies[-1]:.2f}s')

    if stats.error_details:
        print(f'\nError details (first 5):')
        for e in stats.error_details[:5]:
            print(f'  - {e}')

    print(f'\n{"=" * 45}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Load test dev-api.canyougrab.it')
    parser.add_argument('-c', '--concurrency', type=int, default=10, help='Concurrent requests (default: 10)')
    parser.add_argument('-n', '--requests', type=int, default=50, help='Total requests (default: 50)')
    parser.add_argument('-d', '--domains-per', type=int, default=50, help='Domains per request (default: 50)')
    args = parser.parse_args()

    run_load_test(args.concurrency, args.requests, args.domains_per)
