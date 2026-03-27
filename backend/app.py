"""
FastAPI app for confidence-scored domain intelligence via DNS + WHOIS.
Includes Valkey domain cache, API key auth, rate limiting, billing, and key management.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Body, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import APIKeyUser, account_read_auth, domains_read_auth
from plans import get_plan, get_published_plans
from queries import (
    record_usage, get_usage,
    get_monthly_usage, get_monthly_detailed_usage,
    record_minute_usage, get_minute_usage, get_minute_detailed_usage,
)
from valkey_client import create_job, create_split_job, get_job_status, get_job_results, get_valkey
from keys import router as keys_router
from billing import billing_router, stripe_router
from antifraud import antifraud_router
from oauth import router as oauth_router
from session import router as session_router
from portal import router as portal_router
from link_accounts import router as link_accounts_router
from health import router as health_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
LOCAL_REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_REPO_ROOT = Path(os.environ.get('CANYOUGRAB_REPO_ROOT', '/opt/canyougrab-repo'))


def _resolve_repo_file(*relative_parts: str) -> Path:
    for root in (LOCAL_REPO_ROOT, SERVER_REPO_ROOT):
        candidate = root.joinpath(*relative_parts)
        if candidate.exists():
            return candidate
    return LOCAL_REPO_ROOT.joinpath(*relative_parts)


OPENAPI_TEMPLATE_PATH = _resolve_repo_file('portal', 'config', 'routes.oas.json')
MCP_SERVER_METADATA_PATH = _resolve_repo_file('mcp-server', 'server.json')

app = FastAPI(title='CanYouGrab API', version='7.0.0')


@app.on_event('startup')
def _startup():
    """Populate Valkey sets used by Go workers at startup."""
    try:
        from tld_registry import populate_covered_tlds_set
        populate_covered_tlds_set()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning('Failed to populate covered TLDs: %s', e)

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
app.include_router(session_router)
app.include_router(portal_router)
app.include_router(link_accounts_router)
app.include_router(health_router)


def _request_origin(request: Request) -> str:
    forwarded_proto = request.headers.get('x-forwarded-proto', '')
    forwarded_host = request.headers.get('x-forwarded-host', '')
    scheme = forwarded_proto.split(',', 1)[0].strip() or request.url.scheme or 'https'
    host = (
        forwarded_host.split(',', 1)[0].strip()
        or request.headers.get('host', '').split(',', 1)[0].strip()
        or request.url.netloc
    )
    return f'{scheme}://{host}' if host else str(request.base_url).rstrip('/')


def _load_json_file(path: Path) -> dict:
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


@app.get('/api-reference/openapi.json')
def openapi_document(request: Request):
    """Serve OpenAPI with a host-aware server URL for dev and prod."""
    data = _load_json_file(OPENAPI_TEMPLATE_PATH)
    data['servers'] = [{'url': _request_origin(request)}]
    return JSONResponse(data)


@app.get('/server.json')
def mcp_server_metadata(request: Request):
    """Serve MCP server metadata with a host-aware remote URL."""
    data = _load_json_file(MCP_SERVER_METADATA_PATH)
    origin = _request_origin(request)

    remotes = []
    for remote in data.get('remotes', []):
        remote_copy = dict(remote)
        if remote_copy.get('type') == 'streamable-http':
            remote_copy['url'] = f'{origin}/mcp'
        remotes.append(remote_copy)

    if remotes:
        data['remotes'] = remotes

    return JSONResponse(data)


# ── Rate limiting via Valkey ───────────────────────────────────────

def _check_rate_limit(consumer_id: str, plan: str):
    """Check per-minute rate limit using Valkey counter."""
    limit = get_plan(plan)['minute_limit']
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


# ── Bulk domain check (long-poll) ─────────────────────────────────

POLL_INTERVAL = 0.3   # seconds between Valkey polls
POLL_TIMEOUT = 45.0   # max seconds to wait for results (increased for WHOIS lookups)


async def do_bulk_check(
    consumer: str,
    plan: str,
    domains: list[str],
    verbose: bool = False,
    enrichment: bool = False,
):
    """Shared bulk-check logic: rate limit, quota, enqueue job, long-poll for results.

    Returns a dict or JSONResponse.  Called by both /api/check/bulk and
    /api/portal/check/bulk.
    """
    plan_info = get_plan(plan)

    # Per-plan domain cap
    domain_cap = plan_info['domain_cap']
    if len(domains) > domain_cap:
        return JSONResponse({
            'error': f'Maximum {domain_cap} domains per request on your {plan} plan',
            'limit': domain_cap,
        }, status_code=400)

    _check_rate_limit(consumer, plan)

    # Monthly quota check
    monthly_limit = plan_info['monthly_limit']
    if monthly_limit > 0:
        monthly_used = get_monthly_usage(consumer)
        if monthly_used >= monthly_limit:
            return JSONResponse({
                'error': 'Monthly quota exceeded',
                'message': f'You have used {monthly_used:,} of your {monthly_limit:,} monthly domain lookups.',
                'usage': {'monthly_lookups': monthly_used, 'monthly_limit': monthly_limit},
            }, status_code=429)

    # Per-minute quota check
    minute_limit = plan_info['minute_limit']
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
        create_split_job(job_id, consumer, domains)
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
            if enrichment:
                from enrichment import enrich_results_bulk
                results = await asyncio.get_event_loop().run_in_executor(
                    None, enrich_results_bulk, results
                )
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


@app.post('/api/check/bulk')
async def api_check_bulk(
    request: Request,
    body: dict = Body(...),
    user: APIKeyUser = Depends(domains_read_auth),
    verbose: bool = Query(False, description='Include internal timing and debug fields'),
    enrichment: bool = Query(False, alias='enrichment', description='Return enriched dns/whois/intelligence sections'),
):
    """Check availability of up to 100 domains. Holds connection open until results are ready."""
    domains = body.get('domains', [])
    if not isinstance(domains, list) or not domains:
        return JSONResponse({'error': 'Provide a domains array'}, status_code=400)

    return await do_bulk_check(user.consumer_id, user.plan, domains, verbose, enrichment)


# ── Other API routes ──────────────────────────────────────────────

@app.get('/api/account/usage')
def api_account_usage(user: APIKeyUser = Depends(account_read_auth)):
    """Returns usage data for the authenticated consumer."""
    plan_info = get_plan(user.plan)
    usage = get_usage(user.consumer_id)
    return {
        'plan': user.plan,
        'lookups_today': usage['lookups_today'],
        'lookups_limit': plan_info['monthly_limit'],
        'period': 'monthly',
    }


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
def api_quota_check(user: APIKeyUser = Depends(account_read_auth)):
    """Lightweight quota check."""
    return {
        'consumer': user.consumer_id,
        'monthly_lookups': get_monthly_usage(user.consumer_id),
        'minute_lookups': get_minute_usage(user.consumer_id),
    }


@app.get('/api/plans')
def api_plans():
    """Public endpoint: returns currently published plans."""
    plans = get_published_plans()
    return [
        {
            'name': p['name'],
            'display_name': p['display_name'],
            'price_cents': p['price_cents'],
            'monthly_limit': p['monthly_limit'],
            'minute_limit': p['minute_limit'],
            'domain_cap': p['domain_cap'],
            'requires_card': p['requires_card'],
            'sort_order': p['sort_order'],
        }
        for p in plans
    ]


@app.get('/health')
def health():
    return {'status': 'ok'}
