#!/usr/bin/env python3
"""
Background worker for processing bulk domain availability jobs.
Pulls job keys from Valkey queue, processes domains, stores results.
Each job is a single unit of up to 100 domains — no chunking.
"""

import os
import sys
import signal
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg2.pool

from valkey_client import get_valkey, claim_job, complete_job, fail_job
from queries import check_domain_pooled

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [worker] %(message)s',
)
logger = logging.getLogger(__name__)

BATCH_CONCURRENCY = int(os.environ.get('BATCH_CONCURRENCY', '10'))
BRPOP_TIMEOUT = 5  # seconds to wait for a job before looping

running = True


def handle_signal(signum, frame):
    global running
    logger.info('Received signal %d, shutting down gracefully...', signum)
    running = False


def create_db_pool():
    """Create a threaded PostgreSQL connection pool for the worker."""
    return psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=BATCH_CONCURRENCY,
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        dbname=os.environ.get('POSTGRES_DB', 'canyougrab'),
        user=os.environ.get('POSTGRES_USER', 'canyougrab'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
        sslmode=os.environ.get('POSTGRES_SSLMODE', 'require'),
    )


def process_job(job_key: str, db_pool):
    """Process a single job: check all domains and store results."""
    # Parse job key: job:{job_id}
    parts = job_key.split(':', 1)
    if len(parts) != 2 or not parts[1]:
        logger.error('Invalid job key: %s', job_key)
        return

    job_id = parts[1]

    # Claim the job (sets status=processing, returns domains)
    job_data = claim_job(job_key)
    if job_data is None:
        logger.warning('Job %s expired or missing, skipping', job_key)
        return

    domains = job_data['domains']
    queued_at = job_data['queued_at']

    logger.info('Processing job %s (%d domains)', job_id[:8], len(domains))

    try:
        # Process domains concurrently using thread pool
        with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as executor:
            futures = [
                executor.submit(check_domain_pooled, domain, db_pool)
                for domain in domains
            ]
            results = [f.result() for f in futures]

        complete_job(job_id, results, queued_at=queued_at)

    except Exception as e:
        logger.exception('Error processing job %s', job_id[:8])
        fail_job(job_id, str(e))


def recover_stale_jobs():
    """On startup, find jobs stuck in 'processing' state and mark them failed."""
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
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info('Worker starting (concurrency=%d)', BATCH_CONCURRENCY)

    # Test Valkey connection
    r = get_valkey()
    r.ping()
    logger.info('Valkey connected')

    # Create DB connection pool
    db_pool = create_db_pool()
    logger.info('PostgreSQL pool created (max=%d)', BATCH_CONCURRENCY)

    # Recover any stale jobs from previous crash
    recover_stale_jobs()

    logger.info('Listening for jobs on queue:jobs...')

    while running:
        try:
            # BRPOP blocks for up to BRPOP_TIMEOUT seconds
            result = r.brpop('queue:jobs', timeout=BRPOP_TIMEOUT)
            if result is None:
                continue  # timeout, loop to check running flag

            queue_name, job_key = result
            process_job(job_key, db_pool)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.exception('Unexpected error in worker loop')
            time.sleep(1)

    logger.info('Worker shutting down')
    db_pool.closeall()


if __name__ == '__main__':
    main()
