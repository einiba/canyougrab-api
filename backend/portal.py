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
