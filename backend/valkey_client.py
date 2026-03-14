"""
Valkey (Redis-compatible) client for async job queue.
Each job is a single unit of up to 100 domains — no chunking.
"""

import os
import json
import logging
from datetime import datetime, timezone

import redis

logger = logging.getLogger(__name__)

JOB_TTL = 3600  # 1 hour

_client = None


def get_valkey() -> redis.Redis:
    """Get a Valkey connection using a shared connection pool via URL."""
    global _client
    if _client is None:
        host = os.environ.get('VALKEY_HOST', 'localhost')
        port = os.environ.get('VALKEY_PORT', '25061')
        user = os.environ.get('VALKEY_USERNAME', 'default')
        pw = os.environ.get('VALKEY_PASSWORD', '')
        url = f'rediss://{user}:{pw}@{host}:{port}'
        _client = redis.from_url(url, decode_responses=True)
    return _client


def create_job(job_id: str, consumer: str, domains: list[str]) -> dict:
    """Create a job and enqueue it for worker processing."""
    r = get_valkey()
    now = datetime.now(timezone.utc).isoformat()
    job_key = f'job:{job_id}'

    pipe = r.pipeline(transaction=True)
    pipe.hset(job_key, mapping={
        'status': 'pending',
        'consumer': consumer,
        'domain_count': str(len(domains)),
        'domains': json.dumps(domains),
        'created_at': now,
    })
    pipe.expire(job_key, JOB_TTL)
    pipe.lpush('queue:jobs', job_key)
    pipe.execute()

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

    # Compute response_time_ms from queued_at to now
    response_time_ms = None
    if queued_at:
        try:
            queued_dt = datetime.fromisoformat(queued_at)
            response_time_ms = round((now - queued_dt).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    # Enrich each domain result with timing info
    for result in results:
        result['queued_at'] = queued_at
        result['completed_at'] = now_iso
        if response_time_ms is not None:
            result['response_time_ms'] = response_time_ms

    pipe = r.pipeline(transaction=True)
    pipe.hset(job_key, mapping={
        'status': 'completed',
        'results': json.dumps(results),
        'completed_at': now_iso,
    })
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
