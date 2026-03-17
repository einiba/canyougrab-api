"""
FastAPI app for confidence-scored domain intelligence via DNS + WHOIS.
Includes Valkey domain cache, API key auth, rate limiting, billing, and key management.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Body, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import APIKeyUser, api_key_auth
from queries import (
    record_usage, get_usage,
    get_monthly_usage, get_monthly_detailed_usage,
    record_hourly_usage, get_hourly_usage, get_hourly_detailed_usage,
)
from valkey_client import create_job, get_job_status, get_job_results, get_valkey
from keys import router as keys_router
from billing import billing_router, stripe_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

PLAN_MONTHLY_LIMITS = {
    'starter': 100,
    'basic': 10_000,
    'pro': 50_000,
    'business': 300_000,
}

PLAN_HOURLY_LIMITS = {
    'starter': 100,
    'basic': 1_000,
    'pro': 5_000,
    'business': 30_000,
}

app = FastAPI(title='CanYouGrab API', version='7.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(keys_router)
app.include_router(billing_router)
app.include_router(stripe_router)


# ── Rate limiting via Valkey ───────────────────────────────────────

def _check_rate_limit(consumer_id: str, plan: str):
    """Check hourly rate limit using Valkey counter."""
    limit = PLAN_HOURLY_LIMITS.get(plan, 0)
    if limit <= 0:
        return

    hour_key = datetime.now(timezone.utc).strftime('%Y%m%d%H')
    redis_key = f'ratelimit:{consumer_id}:{hour_key}'

    r = get_valkey()
    count = r.incr(redis_key)
    if count == 1:
        r.expire(redis_key, 3600)

    if count > limit:
        now = datetime.now(timezone.utc)
        raise HTTPException(
            status_code=429,
            detail={
                'error': 'Hourly rate limit exceeded',
                'message': f'You have made {count:,} requests this hour. Your {plan} plan allows {limit:,} per hour.',
                'retry_after_seconds': 3600 - now.minute * 60 - now.second,
            },
        )


# ── Bulk domain check (long-poll) ─────────────────────────────────

POLL_INTERVAL = 0.3   # seconds between Valkey polls
POLL_TIMEOUT = 45.0   # max seconds to wait for results (increased for WHOIS lookups)


@app.post('/api/check/bulk')
async def api_check_bulk(
    body: dict = Body(...),
    user: APIKeyUser = Depends(api_key_auth),
    verbose: bool = Query(False, description='Include internal timing and debug fields'),
):
    """Check availability of up to 100 domains. Holds connection open until results are ready."""
    domains = body.get('domains', [])
    if not isinstance(domains, list) or not domains:
        return JSONResponse({'error': 'Provide a domains array'}, status_code=400)
    if len(domains) > 100:
        return JSONResponse({'error': 'Maximum 100 domains per request'}, status_code=400)

    consumer = user.consumer_id
    plan = user.plan

    _check_rate_limit(consumer, plan)

    # Monthly quota check
    monthly_limit = PLAN_MONTHLY_LIMITS.get(plan, 0)
    if monthly_limit > 0:
        monthly_used = get_monthly_usage(consumer)
        if monthly_used >= monthly_limit:
            return JSONResponse({
                'error': 'Monthly quota exceeded',
                'message': f'You have used {monthly_used:,} of your {monthly_limit:,} monthly domain lookups.',
                'usage': {'monthly_lookups': monthly_used, 'monthly_limit': monthly_limit},
            }, status_code=429)

    # Hourly quota check
    hourly_limit = PLAN_HOURLY_LIMITS.get(plan, 0)
    if hourly_limit > 0:
        hourly_used = get_hourly_usage(consumer)
        if hourly_used >= hourly_limit:
            return JSONResponse({
                'error': 'Hourly lookup limit exceeded',
                'message': f'You have used {hourly_used:,} of your {hourly_limit:,} hourly domain lookups.',
                'usage': {'hourly_lookups': hourly_used, 'hourly_limit': hourly_limit},
            }, status_code=429)

    record_usage(consumer, len(domains))
    record_hourly_usage(consumer, len(domains))

    # Enqueue job for worker processing
    job_id = str(uuid.uuid4())
    try:
        create_job(job_id, consumer, domains)
    except Exception as e:
        logger.error('Failed to enqueue job: %s', e)
        return JSONResponse({'error': 'Service temporarily unavailable'}, status_code=503)

    logger.info('Job %s created: %d domains (consumer=%s)', job_id[:8], len(domains), consumer)

    # Hold connection open — poll Valkey until worker completes the job
    max_polls = int(POLL_TIMEOUT / POLL_INTERVAL)
    for _ in range(max_polls):
        await asyncio.sleep(POLL_INTERVAL)
        job = get_job_status(job_id)
        if job is None:
            continue
        if job['status'] == 'completed':
            results = get_job_results(job_id)
            response = {'results': results}
            if verbose:
                response['job_id'] = job_id
                response['queued_at'] = job.get('created_at', '')
                response['completed_at'] = job.get('completed_at', '')
            return response
        if job['status'] == 'failed':
            return JSONResponse({
                'error': 'Job processing failed',
                'detail': job.get('error', 'Unknown error'),
            }, status_code=500)

    return JSONResponse({
        'error': 'Processing timeout',
        'message': 'Results were not ready within 45 seconds. Please retry.',
    }, status_code=504)


# ── Other API routes ──────────────────────────────────────────────

@app.get('/api/account/usage')
def api_account_usage(user: APIKeyUser = Depends(api_key_auth)):
    """Returns usage data for the authenticated consumer."""
    return get_usage(user.consumer_id)


@app.post('/api/account/usage/detailed')
def api_account_usage_detailed(body: dict = Body(...)):
    """Returns usage breakdown for multiple consumers (internal endpoint)."""
    consumers = body.get('consumers', [])
    if not isinstance(consumers, list):
        return {'error': 'consumers must be a list'}
    monthly = get_monthly_detailed_usage(consumers)
    hourly = get_hourly_detailed_usage(consumers)
    monthly['hourly_by_consumer'] = hourly['by_consumer']
    monthly['hourly_total'] = hourly['total']
    return monthly


@app.get('/api/account/quota-check')
def api_quota_check(user: APIKeyUser = Depends(api_key_auth)):
    """Lightweight quota check."""
    return {
        'consumer': user.consumer_id,
        'monthly_lookups': get_monthly_usage(user.consumer_id),
        'hourly_lookups': get_hourly_usage(user.consumer_id),
    }


@app.get('/health')
def health():
    return {'status': 'ok'}
