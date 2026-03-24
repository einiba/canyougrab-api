#!/usr/bin/env python3
"""
RQ-based background worker for processing bulk domain availability jobs.
Replaces the raw BRPOP loop with RQ's worker lifecycle, gaining
automatic retries, failed-job tracking, and graceful shutdown.
"""

import os
import sys
import logging

# Ensure the backend directory is on sys.path so RQ can import rq_tasks
# when it deserialises enqueued job references.
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from rq import Worker

from valkey_client import get_valkey, get_rq_connection, get_rq_queue, fail_job
from dns_client import create_resolver, DNS_RESOLVER_HOSTNAME, DNS_RESOLVER_PORT
from whois_client import WHOIS_HOSTNAME, WHOIS_PORT

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [worker] %(message)s',
)
logger = logging.getLogger(__name__)

BATCH_CONCURRENCY = int(os.environ.get('BATCH_CONCURRENCY', '10'))


def recover_stale_jobs():
    """On startup, find jobs stuck in 'processing' state and mark them failed.
    This handles jobs that were interrupted by a previous worker crash."""
    r = get_valkey()
    cursor = 0
    stale_count = 0

    while True:
        cursor, keys = r.scan(cursor, match='job:*', count=100)
        for key in keys:
            status = r.hget(key, 'status')
            if status == 'processing':
                parts = key.split(':', 1)
                if len(parts) == 2:
                    fail_job(parts[1], 'Worker restarted, job was interrupted')
                    stale_count += 1
        if cursor == 0:
            break

    if stale_count:
        logger.info('Recovered %d stale jobs on startup', stale_count)


def main():
    logger.info('Worker starting (concurrency=%d)', BATCH_CONCURRENCY)

    # Test Valkey connection
    r = get_valkey()
    r.ping()
    logger.info('Valkey connected')

    # Create DNS resolver and verify connectivity
    resolver = create_resolver()
    try:
        resolver.resolve('google.com', 'NS')
        logger.info('DNS resolver connected (%s:%d)', DNS_RESOLVER_HOSTNAME, DNS_RESOLVER_PORT)
    except Exception as e:
        logger.error('DNS resolver unreachable at %s:%d: %s', DNS_RESOLVER_HOSTNAME, DNS_RESOLVER_PORT, e)
        sys.exit(1)

    # Verify rust-whois connectivity (non-fatal — WHOIS is best-effort)
    try:
        from whois_client import _get_base_url
        base = _get_base_url()
        logger.info('WHOIS service resolved (%s:%d → %s)', WHOIS_HOSTNAME, WHOIS_PORT, base)
    except Exception as e:
        logger.warning('WHOIS service unreachable at %s:%d: %s (will fall back to DNS-only)', WHOIS_HOSTNAME, WHOIS_PORT, e)

    # Pre-load TLD registry (RDAP host list) so first lookup doesn't pay the DB cost
    from tld_registry import _get_registry
    registry = _get_registry()
    disabled = sum(1 for v in registry.values() if v['whois_disabled'])
    logger.info('TLD registry loaded: %d TLDs (%d with WHOIS disabled)', len(registry), disabled)

    # Recover any stale jobs from previous crash
    recover_stale_jobs()

    # Start RQ worker — handles SIGTERM gracefully, blocks until shutdown
    queue = get_rq_queue()
    rq_conn = get_rq_connection()
    worker = Worker([queue], connection=rq_conn)
    logger.info('Listening for jobs on queue "%s" via RQ...', queue.name)
    worker.work(with_scheduler=False)

    logger.info('Worker shutting down')


if __name__ == '__main__':
    main()
