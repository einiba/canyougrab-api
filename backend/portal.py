"""
Portal-specific endpoints — JWT-authenticated, for the logged-in portal UI.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from auth import JWTUser, jwt_auth
from queries import get_db_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/portal', tags=['Portal'])


def _get_user_consumer(user_sub: str) -> tuple[str, str]:
    """Return (consumer_id, plan) from the user's most recent active API key.

    Raises 403 if no active key exists.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, plan FROM api_keys
                WHERE user_sub = %s AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
            """, (user_sub,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(
            status_code=403,
            detail={
                'error': 'No API key found',
                'message': 'Create an API key first to use the interactive checker.',
            },
        )

    return str(row[0]), row[1]


@router.post('/check/bulk')
async def portal_check_bulk(
    request: Request,
    body: dict = Body(...),
    user: JWTUser = Depends(jwt_auth),
):
    """JWT-authenticated bulk domain check for the portal interactive page."""
    domains = body.get('domains', [])
    if not isinstance(domains, list) or not domains:
        return JSONResponse({'error': 'Provide a domains array'}, status_code=400)

    consumer_id, plan = _get_user_consumer(user.sub)

    # Import here to avoid circular import (app imports portal)
    from app import do_bulk_check
    return await do_bulk_check(consumer_id, plan, domains)


# ── Starred domains ─────────────────────────────────────────────────────────

@router.get('/names/starred')
def portal_list_starred(user: JWTUser = Depends(jwt_auth)):
    """Return all domains the user has starred, newest first."""
    from name_starred import list_stars
    return {'stars': list_stars(user.sub)}


@router.post('/names/star')
def portal_toggle_star(
    body: dict = Body(...),
    user: JWTUser = Depends(jwt_auth),
):
    """Idempotent toggle. Body: {domain, base?, tld?, available?, source_list_id?}."""
    domain = (body.get('domain') or '').strip().lower()
    if not domain or len(domain) > 255:
        return JSONResponse({'detail': 'domain is required'}, status_code=400)

    from name_starred import toggle_star
    try:
        result = toggle_star(
            user_sub=user.sub,
            domain=domain,
            base=body.get('base'),
            tld=body.get('tld'),
            available=body.get('available'),
            source_list_id=body.get('source_list_id'),
        )
    except Exception as e:
        logger.exception('toggle_star failed: %s', e)
        return JSONResponse({'detail': 'Failed to toggle star'}, status_code=500)
    return result


@router.post('/names/star/claim')
def portal_claim_stars(
    body: dict = Body(...),
    user: JWTUser = Depends(jwt_auth),
):
    """Bulk-attach anonymous stars (carried over from browser localStorage)
    to the now-authenticated user. Idempotent.
    Body: {items: [{domain, base?, tld?, available?, source_list_id?}, ...]}.
    """
    items = body.get('items') or []
    if not isinstance(items, list):
        return JSONResponse({'detail': 'items must be a list'}, status_code=400)

    from name_starred import claim_anon_stars
    inserted = claim_anon_stars(user.sub, items[:200])  # hard cap on batch size
    return {'claimed': inserted}
