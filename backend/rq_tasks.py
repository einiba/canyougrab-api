"""
RQ task functions for domain availability checking.
Separated from worker.py to avoid import cycles — RQ serialises
function references as module.function_name.
"""

import os
import logging
from concurrent.futures import ThreadPoolExecutor

from valkey_client import claim_job, complete_job, fail_job
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
    parts = job_key.split(':', 1)
    if len(parts) != 2 or not parts[1]:
        logger.error('Invalid job key: %s', job_key)
        return

    job_id = parts[1]

    job_data = claim_job(job_key)
    if job_data is None:
        logger.warning('Job %s expired or missing, skipping', job_key)
        return

    domains = job_data['domains']
    queued_at = job_data['queued_at']
    resolver = _get_resolver()

    logger.info('Processing job %s (%d domains)', job_id[:8], len(domains))

    try:
        with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as executor:
            futures = [
                executor.submit(check_domain, domain, resolver)
                for domain in domains
            ]
            results = [f.result() for f in futures]

        complete_job(job_id, results, queued_at=queued_at)

    except Exception as e:
        logger.exception('Error processing job %s', job_id[:8])
        fail_job(job_id, str(e))
        raise  # Re-raise so RQ marks the job as failed and can retry
