"""
Session endpoint — called by the portal after login.
Upserts the user record and returns profile + plan info.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, Request

from auth import AUTH0_DOMAIN, JWTUser, jwt_auth
from plans import get_plan
from queries import get_db_conn
from users import upsert_user, get_user, merge_user_data

logger = logging.getLogger(__name__)

router = APIRouter(tags=['Session'])


def _get_user_plan(user_sub: str) -> str:
    """Look up the user's current plan from their most recent active API key."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT plan FROM api_keys
                WHERE user_sub = %s AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
            """, (user_sub,))
            row = cur.fetchone()
            return row[0] if row else 'free'
    finally:
        conn.close()


@router.post('/api/auth/session')
async def create_session(request: Request, user: JWTUser = Depends(jwt_auth)):
    """Called by the portal after login.  Upserts user record, returns profile."""
    email = user.email
    name = user.name
    email_verified = user.email_verified

    # Fallback: if email is missing from the access token (cached old token),
    # call Auth0 /userinfo to get it.
    if not email:
        token = request.headers.get('Authorization', '')[7:]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f'https://{AUTH0_DOMAIN}/userinfo',
                    headers={'Authorization': f'Bearer {token}'},
                )
            if resp.status_code == 200:
                info = resp.json()
                email = info.get('email', '')
                name = name or info.get('name', '')
                email_verified = info.get('email_verified', False)
                logger.info('Fetched email from /userinfo for %s', user.sub)
        except Exception as e:
            logger.warning('Failed to fetch /userinfo for %s: %s', user.sub, e)

    # Derive auth provider from the sub (e.g. "google-oauth2|..." → "google-oauth2")
    auth_provider = user.sub.split('|')[0] if '|' in user.sub else ''

    db_user = upsert_user(
        auth0_sub=user.sub,
        email=email,
        name=name,
        email_verified=email_verified,
        auth_provider=auth_provider,
    )

    # Lazy migration: if this user has linked identities (from Auth0 account
    # linking), reassign any orphaned api_keys from secondary subs.
    try:
        token = request.headers.get('Authorization', '')[7:]
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f'https://{AUTH0_DOMAIN}/userinfo',
                headers={'Authorization': f'Bearer {token}'},
            )
        if resp.status_code == 200:
            info = resp.json()
            identities = info.get('identities', [])
            if len(identities) > 1:
                for identity in identities:
                    secondary_sub = f"{identity['provider']}|{identity['user_id']}"
                    if secondary_sub != user.sub:
                        merge_user_data(user.sub, secondary_sub)
    except Exception as e:
        logger.debug('Linked identity check skipped: %s', e)

    plan = _get_user_plan(user.sub)
    plan_info = get_plan(plan)

    return {
        'sub': user.sub,
        'email': db_user['email'] if db_user else email,
        'name': db_user['name'] if db_user else name,
        'email_verified': db_user['email_verified'] if db_user else email_verified,
        'plan': plan,
        'monthly_limit': plan_info['monthly_limit'],
        'created_at': db_user['created_at'] if db_user else None,
        'tos_accepted_at': db_user.get('tos_accepted_at') if db_user else None,
        'tos_version': db_user.get('tos_version') if db_user else None,
    }


@router.post('/api/auth/accept-tos')
async def accept_tos(user: JWTUser = Depends(jwt_auth)):
    """Record that the user has accepted the current Terms of Service."""
    TOS_VERSION = '1.0'

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users
                SET tos_accepted_at = NOW(), tos_version = %s, updated_at = NOW()
                WHERE auth0_sub = %s
                RETURNING tos_accepted_at, tos_version
            """, (TOS_VERSION, user.sub))
            row = cur.fetchone()
            conn.commit()

        if not row:
            return {'error': 'User not found'}, 404

        return {
            'tos_accepted_at': row[0].isoformat() if row[0] else None,
            'tos_version': row[1],
        }
    except Exception as e:
        logger.error('Failed to accept TOS for %s: %s', user.sub, e)
        conn.rollback()
        raise
    finally:
        conn.close()
