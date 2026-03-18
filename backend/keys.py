"""
API Key CRUD endpoints.
Mounted as a FastAPI router at /api/keys.
"""

import hashlib
import secrets
import logging
import os
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from auth import JWTUser, jwt_auth
from plans import get_plan
from queries import get_db_conn
from email_utils import validate_signup_email, normalize_email
from users import get_user_email

TURNSTILE_SECRET = os.environ.get('TURNSTILE_SECRET_KEY', '')
TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/keys', tags=['API Keys'])

KEY_PREFIX_LEN = 12  # visible prefix for display


def _generate_key() -> tuple[str, str, str]:
    """Generate a raw key, its hash, and display prefix."""
    raw = 'cyg_' + secrets.token_urlsafe(40)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:KEY_PREFIX_LEN]
    return raw, key_hash, prefix


class CreateKeyRequest(BaseModel):
    description: Optional[str] = 'API Key'


class RotateKeyResponse(BaseModel):
    id: str
    key: str
    key_prefix: str
    description: str


@router.post('')
def create_key(body: CreateKeyRequest, request: Request, user: JWTUser = Depends(jwt_auth)):
    """Create a new API key for the authenticated user."""
    # Verify Turnstile token if configured
    turnstile_token = request.headers.get('x-turnstile-token', '')
    if TURNSTILE_SECRET and turnstile_token:
        client_ip = request.headers.get(
            'x-forwarded-for', request.client.host if request.client else ''
        ).split(',')[0].strip()
        resp = httpx.post(TURNSTILE_VERIFY_URL, data={
            'secret': TURNSTILE_SECRET,
            'response': turnstile_token,
            'remoteip': client_ip,
        }, timeout=10)
        result = resp.json()
        if not result.get('success'):
            logger.warning('Turnstile verification failed on key creation: %s', result.get('error-codes', []))
            raise HTTPException(status_code=403, detail='Bot verification failed. Please try again.')

    # Resolve email: prefer JWT claim, fall back to users table
    email = user.email or get_user_email(user.sub)

    # Validate email (disposable check + normalization)
    normalized_email = ''
    if email:
        email_check = validate_signup_email(email)
        if not email_check['valid']:
            raise HTTPException(status_code=400, detail=email_check['reason'])
        normalized_email = email_check['normalized']
    raw, key_hash, prefix = _generate_key()

    # Look up user's current plan from their other keys or default to 'free'
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT plan FROM api_keys
                WHERE user_sub = %s AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
            """, (user.sub,))
            existing = cur.fetchone()
            plan = existing[0] if existing else 'free'

            cur.execute("""
                INSERT INTO api_keys (user_sub, email, email_normalized, description, key_hash, key_prefix, plan)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (user.sub, email, normalized_email, body.description, key_hash, prefix, plan))
            row = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return {
        'id': str(row[0]),
        'key': raw,  # only returned once
        'key_prefix': prefix,
        'description': body.description,
        'plan': plan,
        'lookups_limit': get_plan(plan)['monthly_limit'],
        'created_at': row[1].isoformat() if row[1] else None,
    }


@router.get('')
def list_keys(user: JWTUser = Depends(jwt_auth)):
    """List all API keys for the authenticated user."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, key_prefix, description, plan, created_at, revoked_at
                FROM api_keys
                WHERE user_sub = %s
                ORDER BY created_at DESC
            """, (user.sub,))
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            'id': str(r[0]),
            'key_prefix': r[1],
            'description': r[2],
            'plan': r[3],
            'lookups_limit': get_plan(r[3])['monthly_limit'],
            'created_at': r[4].isoformat() if r[4] else None,
            'revoked_at': r[5].isoformat() if r[5] else None,
            'active': r[5] is None,
        }
        for r in rows
    ]


@router.post('/{key_id}/rotate')
def rotate_key(key_id: str, user: JWTUser = Depends(jwt_auth)):
    """Rotate an API key: revoke the old one and create a new one with the same settings."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Verify ownership and get current settings
            cur.execute("""
                SELECT id, description, plan
                FROM api_keys
                WHERE id = %s AND user_sub = %s AND revoked_at IS NULL
            """, (key_id, user.sub))
            old = cur.fetchone()
            if not old:
                raise HTTPException(status_code=404, detail='Key not found or already revoked')

            description = old[1]
            plan = old[2]

            # Revoke old key
            cur.execute("""
                UPDATE api_keys SET revoked_at = NOW() WHERE id = %s
            """, (key_id,))

            # Create new key with same settings
            normalized_email = normalize_email(user.email)
            raw, key_hash, prefix = _generate_key()
            cur.execute("""
                INSERT INTO api_keys (user_sub, email, email_normalized, description, key_hash, key_prefix, plan)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (user.sub, user.email, normalized_email, description, key_hash, prefix, plan))
            new_row = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return {
        'id': str(new_row[0]),
        'key': raw,
        'key_prefix': prefix,
        'description': description,
        'plan': plan,
        'lookups_limit': get_plan(plan)['monthly_limit'],
        'created_at': new_row[1].isoformat() if new_row[1] else None,
        'old_key_id': key_id,
        'old_key_revoked': True,
    }


@router.delete('/{key_id}')
def revoke_key(key_id: str, user: JWTUser = Depends(jwt_auth)):
    """Revoke (soft-delete) an API key."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE api_keys SET revoked_at = NOW()
                WHERE id = %s AND user_sub = %s AND revoked_at IS NULL
                RETURNING id
            """, (key_id, user.sub))
            row = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail='Key not found or already revoked')

    return {'id': key_id, 'revoked': True}
