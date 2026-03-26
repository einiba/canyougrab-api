"""
RQ task functions for domain availability checking.
Separated from worker.py to avoid import cycles — RQ serialises
function references as module.function_name.
"""

import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor

from valkey_client import claim_job, complete_job, complete_sub_job, fail_job, get_valkey
from dns_client import create_resolver
from lookup import check_domain

logger = logging.getLogger(__name__)

BATCH_CONCURRENCY = int(os.environ.get('BATCH_CONCURRENCY', '10'))

# Lazy singleton — created once per worker process
_resolver = None


def _get_resolver():
    global _resolver
    if _resolver is None:
        _resolver = create_resolver()
    return _resolver


def process_domain_job(job_key: str):
    """RQ entry point: claim a job, check all domains, store results.

    The job_key (e.g. 'job:<uuid>') references a hash in Valkey
    that holds the domain list and status.  Results are written
    back to the same hash by complete_job() / fail_job().
    """
    # Job keys: 'job:<uuid>' (regular) or 'job:rdap:<uuid>' / 'job:whois:<uuid>' (sub-job)
    parts = job_key.split(':')
    if len(parts) < 2:
        logger.error('Invalid job key: %s', job_key)
        return

    # For sub-jobs (job:rdap:uuid), job_id is the full key minus 'job:' prefix
    # For regular jobs (job:uuid), job_id is just the uuid
    job_id = ':'.join(parts[1:])
    is_sub_job = len(parts) == 3  # job:rdap:uuid or job:whois:uuid

    t_job_start = time.monotonic()

    t0 = time.monotonic()
    job_data = claim_job(job_key)
    t_claim = time.monotonic() - t0
    if job_data is None:
        logger.warning('Job %s expired or missing, skipping', job_key)
        return

    domains = job_data['domains']
    queued_at = job_data['queued_at']
    resolver = _get_resolver()

    logger.info('Processing job %s (%d domains, concurrency=%d)',
                job_id[:8], len(domains), BATCH_CONCURRENCY)

    try:
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as executor:
            futures = [
                executor.submit(check_domain, domain, resolver)
                for domain in domains
            ]
            results = [f.result() for f in futures]
        t_pool = time.monotonic() - t0

        t0 = time.monotonic()
        if is_sub_job:
            complete_sub_job(job_key, results, queued_at=queued_at)
        else:
            complete_job(job_id, results, queued_at=queued_at)
        t_complete = time.monotonic() - t0

        t_total = time.monotonic() - t_job_start

        # Profiling summary
        cache_hits = sum(1 for r in results if r.get('source') == 'cache')
        dns_only = sum(1 for r in results if r.get('source') == 'dns')
        whois_used = sum(1 for r in results if r.get('source') == 'whois')
        errors = sum(1 for r in results if r.get('available') is None)

        logger.info(
            'JOB_PROFILE %s domains=%d claim_ms=%.1f pool_ms=%.1f complete_ms=%.1f '
            'total_ms=%.1f cache=%d dns=%d whois=%d errors=%d',
            job_id[:8], len(domains), t_claim * 1000, t_pool * 1000,
            t_complete * 1000, t_total * 1000,
            cache_hits, dns_only, whois_used, errors,
        )

        # Push processing time to metrics list for the exporter to consume
        if not is_sub_job:
            r = get_valkey()
            response_time = r.hget(f'job:{job_id}', 'response_time_ms')
            if response_time:
                r.lpush('metrics:processing_times', response_time)
                r.ltrim('metrics:processing_times', 0, 9999)  # cap at 10k entries

    except Exception as e:
        logger.exception('Error processing job %s', job_id[:8])
        if is_sub_job:
            fail_job(job_id, str(e))  # Marks sub-job failed; parent stays pending
        else:
            fail_job(job_id, str(e))
        raise  # Re-raise so RQ marks the job as failed and can retry
