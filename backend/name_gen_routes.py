"""
FastAPI router for /api/names/generate.

Anonymous endpoint: trial-gated by visitor_id + fingerprint + salted IP hash.
Authenticated requests bypass the trial gate (still subject to plan limits
since they go through the existing bulk-check pipeline).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, Request
from fastapi.responses import JSONResponse

from auth import JWTUser, jwt_auth
from name_gen import (
    BYOK_DAILY_LIMIT,
    CooldownError,
    SIGNUP_URL,
    aggregate_usage,
    check_domains_anon,
    claim_anon_lists,
    generate_for_visitor,
    get_saved_list,
    hash_ip,
    list_user_generations,
    record_usage,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/names', tags=['Names'])


def _client_ip(request: Request) -> str:
    fwd = request.headers.get('x-forwarded-for', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.client.host if request.client else ''


@router.post('/generate')
async def generate_names(
    request: Request,
    body: dict = Body(...),
    x_visitor_id: Optional[str] = Header(default=None, alias='X-Visitor-Id'),
    x_visitor_fingerprint: Optional[str] = Header(default=None, alias='X-Visitor-Fingerprint'),
):
    """Generate brandable name candidates for a business description and check
    live availability. Trial-gated for anonymous callers.

    Body: {description: str, styles: [str], tld_preference: str, count: int}
    """
    description = (body.get('description') or '').strip()
    if len(description) < 10:
        return JSONResponse(
            {'detail': 'Description must be at least 10 characters.'},
            status_code=400,
        )

    styles = body.get('styles') or []
    if not isinstance(styles, list):
        styles = []
    tld_pref = body.get('tld_preference') or 'any'
    if tld_pref not in ('com_only', 'tech', 'global', 'any'):
        tld_pref = 'any'

    if not x_visitor_id:
        return JSONResponse(
            {'detail': 'Missing X-Visitor-Id header.'},
            status_code=400,
        )

    ip = _client_ip(request)
    ip_hash = hash_ip(ip) if ip else None

    try:
        return await generate_for_visitor(
            description=description,
            styles=[s for s in styles if isinstance(s, str)],
            tld_pref=tld_pref,
            visitor_id=x_visitor_id,
            fingerprint=x_visitor_fingerprint,
            ip_hash=ip_hash,
        )
    except CooldownError as e:
        return JSONResponse(
            {
                'detail': 'Please wait before generating again.',
                'signup_url': SIGNUP_URL,
                'retry_after_ms': e.retry_after_ms,
            },
            status_code=429,
        )
    except Exception as e:
        logger.exception('Name generation failed: %s', e)
        return JSONResponse(
            {'detail': 'Internal error during name generation.'},
            status_code=500,
        )


@router.post('/check')
async def check_only(
    request: Request,
    body: dict = Body(...),
    x_visitor_id: Optional[str] = Header(default=None, alias='X-Visitor-Id'),
    x_visitor_fingerprint: Optional[str] = Header(default=None, alias='X-Visitor-Fingerprint'),
):
    """Availability-only endpoint for BYOK callers. The browser generates names
    against the user's own LLM key and sends just the candidate domains here for
    a live DNS+WHOIS check. Trial-gated by the same anon identity tuple, but with
    a more permissive daily limit (we aren't paying for the LLM call).
    """
    domains = body.get('domains') or []
    if not isinstance(domains, list) or not domains:
        return JSONResponse({'detail': 'Provide a non-empty domains array.'}, status_code=400)
    domains = [d for d in domains if isinstance(d, str) and 3 <= len(d) <= 255][:50]
    if not domains:
        return JSONResponse({'detail': 'No valid domains supplied.'}, status_code=400)
    if not x_visitor_id:
        return JSONResponse({'detail': 'Missing X-Visitor-Id header.'}, status_code=400)

    ip = _client_ip(request)
    ip_hash = hash_ip(ip) if ip else None

    pre = aggregate_usage(x_visitor_id, x_visitor_fingerprint, ip_hash)
    if pre['count'] >= BYOK_DAILY_LIMIT:
        return JSONResponse(
            {
                'detail': f'BYOK daily limit reached ({BYOK_DAILY_LIMIT}). Sign up to continue.',
                'signup_url': SIGNUP_URL,
            },
            status_code=429,
        )
    record_usage(x_visitor_id, x_visitor_fingerprint, ip_hash)

    raw = await check_domains_anon(domains)
    by_domain = {r['domain']: r for r in raw}
    results = [
        {
            'domain': d,
            'available': by_domain.get(d, {}).get('available'),
        }
        for d in domains
    ]
    return {
        'results': results,
        'mode': 'byok',
        'daily_limit': BYOK_DAILY_LIMIT,
        'used': pre['count'] + 1,
    }


@router.post('/claim')
def claim_lists(
    body: dict = Body(...),
    user: JWTUser = Depends(jwt_auth),
):
    """Attach all anonymous generated lists for the given visitor_id to the
    authenticated user. Idempotent. Called by the front-end immediately after
    signup so the user keeps their pre-account work."""
    visitor_id = (body.get('visitor_id') or '').strip()
    if not visitor_id:
        return JSONResponse({'detail': 'visitor_id is required'}, status_code=400)
    if not user.sub:
        return JSONResponse({'detail': 'User not authenticated'}, status_code=401)

    claimed = claim_anon_lists(visitor_id, user.sub)
    return {'claimed': claimed}


@router.get('/list/{share_id}')
def get_list(share_id: str):
    """Public read of a saved generation list. No auth — shareable URL."""
    data = get_saved_list(share_id)
    if not data:
        return JSONResponse({'detail': 'List not found'}, status_code=404)
    return data


@router.get('/mine')
def list_mine(user: JWTUser = Depends(jwt_auth)):
    """Return saved generation lists owned by the authenticated user."""
    if not user.sub:
        return JSONResponse({'detail': 'User not authenticated'}, status_code=401)
    return {'lists': list_user_generations(user.sub)}
