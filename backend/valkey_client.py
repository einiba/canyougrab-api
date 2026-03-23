"""
Valkey (Redis-compatible) client for async job queue.
Each job is a single unit of up to 100 domains — no chunking.
Uses RQ (Redis Queue) for reliable dispatch, retries, and worker lifecycle.
"""

import os
import json
import logging
from datetime import datetime, timezone

import redis
from rq import Queue, Retry

logger = logging.getLogger(__name__)

JOB_TTL = 3600  # 1 hour
QUEUE_NAME = os.environ.get('VALKEY_QUEUE_NAME', 'canyougrab-jobs')

_client = None
_rq_client = None
_rq_queue = None


def _build_valkey_url() -> str:
    host = os.environ.get('VALKEY_HOST', 'localhost')
    port = os.environ.get('VALKEY_PORT', '25061')
    user = os.environ.get('VALKEY_USERNAME', 'default')
    pw = os.environ.get('VALKEY_PASSWORD', '')
    return f'rediss://{user}:{pw}@{host}:{port}'


def get_valkey() -> redis.Redis:
    """Get a Valkey connection (decode_responses=True) for app-level hash operations."""
    global _client
    if _client is None:
        _client = redis.from_url(
            _build_valkey_url(),
            decode_responses=True,
            max_connections=200,
        )
    return _client


def get_rq_connection() -> redis.Redis:
    """Get a Valkey connection (decode_responses=False) for RQ internals.
    RQ pickles job data and requires raw bytes."""
    global _rq_client
    if _rq_client is None:
        _rq_client = redis.from_url(
            _build_valkey_url(),
            decode_responses=False,
            max_connections=200,
        )
    return _rq_client


def get_rq_queue() -> Queue:
    """Get the shared RQ queue instance."""
    global _rq_queue
    if _rq_queue is None:
        _rq_queue = Queue(QUEUE_NAME, connection=get_rq_connection())
    return _rq_queue


def create_job(job_id: str, consumer: str, domains: list[str]) -> dict:
    """Create a job hash and enqueue it via RQ for worker processing."""
    r = get_valkey()
    now = datetime.now(timezone.utc).isoformat()
    job_key = f'job:{job_id}'

    # Store job metadata in our own hash (unchanged from pre-RQ)
    pipe = r.pipeline(transaction=True)
    pipe.hset(job_key, mapping={
        'status': 'pending',
        'consumer': consumer,
        'domain_count': str(len(domains)),
        'domains': json.dumps(domains),
        'created_at': now,
    })
    pipe.expire(job_key, JOB_TTL)
    pipe.execute()

    # Enqueue via RQ for reliable dispatch with retries.
    # If enqueue fails, clean up the hash so it doesn't sit in 'pending' forever.
    try:
        q = get_rq_queue()
        q.enqueue(
            'rq_tasks.process_domain_job',
            job_key,
            job_timeout=120,
            result_ttl=0,
            failure_ttl=JOB_TTL,
            retry=Retry(max=2, interval=[5, 30]),
        )
    except Exception:
        r.delete(job_key)
        raise

    return {
        'job_id': job_id,
        'status': 'pending',
        'domain_count': len(domains),
    }


def get_job_status(job_id: str) -> dict | None:
    """Read job status. Returns None if job doesn't exist."""
    r = get_valkey()
    data = r.hgetall(f'job:{job_id}')
    if not data:
        return None
    return {
        'job_id': job_id,
        'status': data.get('status', 'unknown'),
        'consumer': data.get('consumer', ''),
        'domain_count': int(data.get('domain_count', '0')),
        'created_at': data.get('created_at', ''),
        'completed_at': data.get('completed_at', ''),
        'error': data.get('error', ''),
    }


def get_job_results(job_id: str) -> list:
    """Get the results array for a completed job."""
    r = get_valkey()
    results_json = r.hget(f'job:{job_id}', 'results')
    if not results_json:
        return []
    return json.loads(results_json)


def claim_job(job_key: str) -> dict | None:
    """Mark job as processing and return its domains and queued_at.
    Returns dict with 'domains' and 'queued_at', or None if expired."""
    r = get_valkey()
    if not r.exists(job_key):
        return None

    pipe = r.pipeline(transaction=True)
    pipe.hset(job_key, 'status', 'processing')
    pipe.hget(job_key, 'domains')
    pipe.hget(job_key, 'created_at')
    results = pipe.execute()
    domains_json = results[1]
    created_at = results[2]

    if not domains_json:
        return None
    return {
        'domains': json.loads(domains_json),
        'queued_at': created_at or '',
    }


def complete_job(job_id: str, results: list, queued_at: str = ''):
    """Store results and mark job as completed."""
    r = get_valkey()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    job_key = f'job:{job_id}'

    # Compute response_time_ms as job-level metadata
    response_time_ms = None
    if queued_at:
        try:
            queued_dt = datetime.fromisoformat(queued_at)
            response_time_ms = round((now - queued_dt).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    pipe = r.pipeline(transaction=True)
    job_mapping = {
        'status': 'completed',
        'results': json.dumps(results),
        'completed_at': now_iso,
    }
    if queued_at:
        job_mapping['queued_at'] = queued_at
    if response_time_ms is not None:
        job_mapping['response_time_ms'] = str(response_time_ms)
    pipe.hset(job_key, mapping=job_mapping)
    pipe.expire(job_key, JOB_TTL)
    pipe.execute()

    logger.info('Job %s completed (%d results, response_time=%sms)',
                job_id[:8], len(results), response_time_ms or '?')


def fail_job(job_id: str, error: str):
    """Mark a job as failed."""
    r = get_valkey()
    job_key = f'job:{job_id}'

    pipe = r.pipeline(transaction=True)
    pipe.hset(job_key, mapping={'status': 'failed', 'error': error})
    pipe.expire(job_key, JOB_TTL)
    pipe.execute()

    logger.error('Job %s failed: %s', job_id[:8], error)
