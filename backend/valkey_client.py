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
RDAP_QUEUE_NAME = os.environ.get('VALKEY_RDAP_QUEUE_NAME', '')
WHOIS_QUEUE_NAME = os.environ.get('VALKEY_WHOIS_QUEUE_NAME', '')
SPLIT_PIPELINE = os.environ.get('SPLIT_PIPELINE', 'false').lower() == 'true'

_client = None
_rq_client = None
_rq_queue = None
_rdap_queue = None
_whois_queue = None


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


def get_rdap_queue() -> Queue:
    """Get the RDAP-specific RQ queue."""
    global _rdap_queue
    if _rdap_queue is None:
        name = RDAP_QUEUE_NAME or QUEUE_NAME
        _rdap_queue = Queue(name, connection=get_rq_connection())
    return _rdap_queue


def get_whois_queue() -> Queue:
    """Get the WHOIS-specific RQ queue."""
    global _whois_queue
    if _whois_queue is None:
        name = WHOIS_QUEUE_NAME or QUEUE_NAME
        _whois_queue = Queue(name, connection=get_rq_connection())
    return _whois_queue


# Lua script for atomic sub-job completion check + parent merge trigger
_COMPLETE_SUB_JOB_LUA = """
-- KEYS[1] = sub-job key
-- KEYS[2] = parent job key
-- ARGV[1] = results JSON
-- ARGV[2] = completed_at
-- ARGV[3] = indices JSON
-- ARGV[4] = queued_at
-- ARGV[5] = JOB_TTL

-- Mark sub-job completed
redis.call('HSET', KEYS[1], 'status', 'completed', 'results', ARGV[1], 'completed_at', ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))

-- Check if all sub-jobs are done
local sub_jobs_json = redis.call('HGET', KEYS[2], 'sub_jobs')
if not sub_jobs_json then return 0 end

local sub_jobs = cjson.decode(sub_jobs_json)
for _, sj_key in ipairs(sub_jobs) do
    local sj_status = redis.call('HGET', sj_key, 'status')
    if sj_status ~= 'completed' then
        return 0  -- Not all done yet
    end
end

-- All sub-jobs done — signal caller to merge
return 1
"""


def create_split_job(job_id: str, consumer: str, domains: list[str]) -> dict:
    """Create a job that may be split into RDAP and WHOIS sub-jobs.

    If SPLIT_PIPELINE is disabled or all domains route to the same queue,
    falls back to a single job on the appropriate queue.
    """
    if not SPLIT_PIPELINE:
        return create_job(job_id, consumer, domains)

    from rdap_router import classify_domains
    rdap_batch, whois_batch = classify_domains(domains)

    # Simple case: all domains go to one queue
    if not whois_batch:
        return _create_single_queue_job(job_id, consumer, domains, get_rdap_queue())
    if not rdap_batch:
        return _create_single_queue_job(job_id, consumer, domains, get_whois_queue())

    # Split case: create parent + two sub-jobs
    r = get_valkey()
    now = datetime.now(timezone.utc).isoformat()
    parent_key = f'job:{job_id}'
    rdap_key = f'job:rdap:{job_id}'
    whois_key = f'job:whois:{job_id}'

    rdap_indices = [idx for idx, _ in rdap_batch]
    whois_indices = [idx for idx, _ in whois_batch]
    rdap_domains = [d for _, d in rdap_batch]
    whois_domains = [d for _, d in whois_batch]

    pipe = r.pipeline(transaction=True)
    # Parent job
    pipe.hset(parent_key, mapping={
        'status': 'pending',
        'consumer': consumer,
        'domain_count': str(len(domains)),
        'domains': json.dumps(domains),
        'created_at': now,
        'sub_jobs': json.dumps([rdap_key, whois_key]),
    })
    pipe.expire(parent_key, JOB_TTL)
    # RDAP sub-job
    pipe.hset(rdap_key, mapping={
        'status': 'pending',
        'domains': json.dumps(rdap_domains),
        'indices': json.dumps(rdap_indices),
        'parent_job': parent_key,
        'created_at': now,
    })
    pipe.expire(rdap_key, JOB_TTL)
    # WHOIS sub-job
    pipe.hset(whois_key, mapping={
        'status': 'pending',
        'domains': json.dumps(whois_domains),
        'indices': json.dumps(whois_indices),
        'parent_job': parent_key,
        'created_at': now,
    })
    pipe.expire(whois_key, JOB_TTL)
    pipe.execute()

    # Enqueue sub-jobs directly — Go workers BLPOP from these lists.
    try:
        rdap_q = get_rdap_queue()
        r.rpush(rdap_q.name, rdap_key)
        whois_q = get_whois_queue()
        r.rpush(whois_q.name, whois_key)
    except Exception:
        r.delete(parent_key, rdap_key, whois_key)
        raise

    logger.info('Split job %s: %d RDAP + %d WHOIS domains',
                job_id[:8], len(rdap_domains), len(whois_domains))

    return {
        'job_id': job_id,
        'status': 'pending',
        'domain_count': len(domains),
        'split': True,
        'rdap_count': len(rdap_domains),
        'whois_count': len(whois_domains),
    }


def _create_single_queue_job(job_id: str, consumer: str, domains: list[str], queue: Queue) -> dict:
    """Create a job on a specific queue (no splitting needed)."""
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
    pipe.execute()

    try:
        # Push job key directly — Go worker BLPOPs from this list.
        r.rpush(queue.name, job_key)
    except Exception:
        r.delete(job_key)
        raise

    return {'job_id': job_id, 'status': 'pending', 'domain_count': len(domains)}


def complete_sub_job(sub_job_id: str, results: list, queued_at: str = ''):
    """Complete a sub-job and merge into parent if all siblings are done.

    Uses a Lua script for atomic sibling completion check.
    """
    r = get_valkey()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Extract sub-job key and parent key
    sub_job_key = f'job:{sub_job_id}' if not sub_job_id.startswith('job:') else sub_job_id
    parent_key = r.hget(sub_job_key, 'parent_job')

    if not parent_key:
        # Not a sub-job — use regular completion
        job_id = sub_job_id.split(':')[-1]
        return complete_job(job_id, results, queued_at)

    indices_json = r.hget(sub_job_key, 'indices')

    # Run Lua script to atomically mark complete and check siblings
    all_done = r.eval(
        _COMPLETE_SUB_JOB_LUA, 2,
        sub_job_key, parent_key,
        json.dumps(results), now_iso,
        indices_json or '[]', queued_at or '',
        str(JOB_TTL),
    )

    if all_done:
        _merge_sub_job_results(parent_key, queued_at)

    logger.info('Sub-job %s completed (%d results, all_done=%s)',
                sub_job_id[:20], len(results), bool(all_done))


def _merge_sub_job_results(parent_key: str, queued_at: str = ''):
    """Merge results from all sub-jobs into the parent job hash."""
    r = get_valkey()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    sub_jobs_json = r.hget(parent_key, 'sub_jobs')
    if not sub_jobs_json:
        return

    sub_jobs = json.loads(sub_jobs_json)
    domain_count = int(r.hget(parent_key, 'domain_count') or 0)

    # Build result array in original order using indices
    merged = [None] * domain_count
    has_partial_error = False

    for sj_key in sub_jobs:
        sj_data = r.hgetall(sj_key)
        if sj_data.get('status') != 'completed':
            has_partial_error = True
            continue
        sj_results = json.loads(sj_data.get('results', '[]'))
        sj_indices = json.loads(sj_data.get('indices', '[]'))
        for idx, result in zip(sj_indices, sj_results):
            if 0 <= idx < domain_count:
                merged[idx] = result

    # Fill any gaps (from failed sub-jobs) with error placeholders
    for i in range(domain_count):
        if merged[i] is None:
            domains_json = r.hget(parent_key, 'domains')
            all_domains = json.loads(domains_json) if domains_json else []
            merged[i] = {
                'domain': all_domains[i] if i < len(all_domains) else 'unknown',
                'available': None,
                'confidence': 'low',
                'error': 'sub-job failed or timed out',
                'source': 'error',
            }

    # Compute response time
    response_time_ms = None
    if queued_at:
        try:
            queued_dt = datetime.fromisoformat(queued_at)
            response_time_ms = round((now - queued_dt).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    pipe = r.pipeline(transaction=True)
    mapping = {
        'status': 'completed',
        'results': json.dumps(merged),
        'completed_at': now_iso,
    }
    if has_partial_error:
        mapping['partial'] = 'true'
    if response_time_ms is not None:
        mapping['response_time_ms'] = str(response_time_ms)
    pipe.hset(parent_key, mapping=mapping)
    pipe.expire(parent_key, JOB_TTL)
    pipe.execute()

    logger.info('Merged %d results into parent %s (partial=%s)',
                len(merged), parent_key, has_partial_error)


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
