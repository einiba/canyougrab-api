"""
FastAPI app for confidence-scored domain intelligence via DNS + WHOIS.
Includes Valkey domain cache, API key auth, rate limiting, billing, and key management.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Body, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import APIKeyUser, api_key_auth
from queries import (
    record_usage, get_usage,
    get_monthly_usage, get_monthly_detailed_usage,
    record_minute_usage, get_minute_usage, get_minute_detailed_usage,
)
from valkey_client import create_job, get_job_status, get_job_results, get_valkey
from keys import router as keys_router
from billing import billing_router, stripe_router
from antifraud import antifraud_router
from oauth import router as oauth_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

PLAN_MONTHLY_LIMITS = {
    'free': 500,
    'free_plus': 10_000,
    'basic': 20_000,
    'pro': 50_000,
    'business': 300_000,
}

PLAN_MINUTE_LIMITS = {
    'free': 30,
    'free_plus': 100,
    'basic': 300,
    'pro': 1_000,
    'business': 3_000,
}

PLAN_DOMAIN_CAPS = {
    'free': 30,
    'free_plus': 100,
    'basic': 100,
    'pro': 100,
    'business': 100,
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
app.include_router(antifraud_router)
app.include_router(oauth_router)


# ── Rate limiting via Valkey ───────────────────────────────────────

def _check_rate_limit(consumer_id: str, plan: str):
    """Check per-minute rate limit using Valkey counter."""
    limit = PLAN_MINUTE_LIMITS.get(plan, 0)
    if limit <= 0:
        return

    minute_key = datetime.now(timezone.utc).strftime('%Y%m%d%H%M')
    redis_key = f'ratelimit:{consumer_id}:{minute_key}'

    r = get_valkey()
    count = r.incr(redis_key)
    if count == 1:
        r.expire(redis_key, 60)

    if count > limit:
        now = datetime.now(timezone.utc)
        raise HTTPException(
            status_code=429,
            detail={
                'error': 'Per-minute rate limit exceeded',
                'message': f'You have made {count:,} requests this minute. Your {plan} plan allows {limit:,} per minute.',
                'retry_after_seconds': 60 - now.second,
            },
        )


# ── IP-based rate limiting ────────────────────────────────────────

IP_MINUTE_LIMIT = 20    # max lookups per IP per minute (across all accounts)
IP_DAILY_LIMIT = 1_000  # max lookups per IP per day

def _check_ip_rate_limit(ip: str):
    """Check IP-level rate limits to prevent multi-account abuse."""
    r = get_valkey()
    minute_key = datetime.now(timezone.utc).strftime('%Y%m%d%H%M')
    day_key = datetime.now(timezone.utc).strftime('%Y%m%d')

    # Per-minute IP limit
    ip_minute_key = f'iplimit:m:{ip}:{minute_key}'
    count_m = r.incr(ip_minute_key)
    if count_m == 1:
        r.expire(ip_minute_key, 60)
    if count_m > IP_MINUTE_LIMIT:
        raise HTTPException(status_code=429, detail={
            'error': 'IP per-minute rate limit exceeded',
            'message': 'Too many requests from this IP address. Please try again later.',
        })

    # Daily IP limit
    ip_day_key = f'iplimit:d:{ip}:{day_key}'
    count_d = r.incr(ip_day_key)
    if count_d == 1:
        r.expire(ip_day_key, 86400)
    if count_d > IP_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail={
            'error': 'IP daily rate limit exceeded',
            'message': 'Too many requests from this IP address today. Please try again tomorrow.',
        })


# ── Bulk domain check (long-poll) ─────────────────────────────────

POLL_INTERVAL = 0.3   # seconds between Valkey polls
POLL_TIMEOUT = 45.0   # max seconds to wait for results (increased for WHOIS lookups)


@app.post('/api/check/bulk')
async def api_check_bulk(
    request: Request,
    body: dict = Body(...),
    user: APIKeyUser = Depends(api_key_auth),
    verbose: bool = Query(False, description='Include internal timing and debug fields'),
):
    """Check availability of up to 100 domains. Holds connection open until results are ready."""
    domains = body.get('domains', [])
    if not isinstance(domains, list) or not domains:
        return JSONResponse({'error': 'Provide a domains array'}, status_code=400)

    consumer = user.consumer_id
    plan = user.plan

    # Per-plan domain cap
    domain_cap = PLAN_DOMAIN_CAPS.get(plan, 100)
    if len(domains) > domain_cap:
        return JSONResponse({
            'error': f'Maximum {domain_cap} domains per request on your {plan} plan',
            'limit': domain_cap,
        }, status_code=400)

    _check_rate_limit(consumer, plan)

    # IP-based rate limiting (anti-abuse)
    client_ip = request.headers.get('x-forwarded-for', request.client.host if request.client else '').split(',')[0].strip()
    if client_ip:
        _check_ip_rate_limit(client_ip)

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

    # Per-minute quota check
    minute_limit = PLAN_MINUTE_LIMITS.get(plan, 0)
    if minute_limit > 0:
        minute_used = get_minute_usage(consumer)
        if minute_used >= minute_limit:
            return JSONResponse({
                'error': 'Per-minute lookup limit exceeded',
                'message': f'You have used {minute_used:,} of your {minute_limit:,} per-minute domain lookups.',
                'usage': {'minute_lookups': minute_used, 'minute_limit': minute_limit},
            }, status_code=429)

    record_usage(consumer, len(domains))
    record_minute_usage(consumer, len(domains))

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
    minute = get_minute_detailed_usage(consumers)
    monthly['minute_by_consumer'] = minute['by_consumer']
    monthly['minute_total'] = minute['total']
    return monthly


@app.get('/api/account/quota-check')
def api_quota_check(user: APIKeyUser = Depends(api_key_auth)):
    """Lightweight quota check."""
    return {
        'consumer': user.consumer_id,
        'monthly_lookups': get_monthly_usage(user.consumer_id),
        'minute_lookups': get_minute_usage(user.consumer_id),
    }


@app.get('/health')
def health():
    return {'status': 'ok'}
