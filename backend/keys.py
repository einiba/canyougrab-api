"""
API Key CRUD endpoints.
Mounted as a FastAPI router at /api/keys.
"""

import hashlib
import secrets
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from auth import JWTUser, jwt_auth
from queries import get_db_conn

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
def create_key(body: CreateKeyRequest, user: JWTUser = Depends(jwt_auth)):
    """Create a new API key for the authenticated user."""
    raw, key_hash, prefix = _generate_key()

    # Look up user's current plan from their other keys or default to 'none'
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT plan, lookups_limit FROM api_keys
                WHERE user_sub = %s AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
            """, (user.sub,))
            existing = cur.fetchone()
            plan = existing[0] if existing else 'none'
            lookups_limit = existing[1] if existing else 0

            cur.execute("""
                INSERT INTO api_keys (user_sub, email, description, key_hash, key_prefix, plan, lookups_limit)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (user.sub, user.email, body.description, key_hash, prefix, plan, lookups_limit))
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
        'lookups_limit': lookups_limit,
        'created_at': row[1].isoformat() if row[1] else None,
    }


@router.get('')
def list_keys(user: JWTUser = Depends(jwt_auth)):
    """List all API keys for the authenticated user."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, key_prefix, description, plan, lookups_limit, created_at, revoked_at
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
            'lookups_limit': r[4],
            'created_at': r[5].isoformat() if r[5] else None,
            'revoked_at': r[6].isoformat() if r[6] else None,
            'active': r[6] is None,
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
                SELECT id, description, plan, lookups_limit
                FROM api_keys
                WHERE id = %s AND user_sub = %s AND revoked_at IS NULL
            """, (key_id, user.sub))
            old = cur.fetchone()
            if not old:
                raise HTTPException(status_code=404, detail='Key not found or already revoked')

            description = old[1]
            plan = old[2]
            lookups_limit = old[3]

            # Revoke old key
            cur.execute("""
                UPDATE api_keys SET revoked_at = NOW() WHERE id = %s
            """, (key_id,))

            # Create new key with same settings
            raw, key_hash, prefix = _generate_key()
            cur.execute("""
                INSERT INTO api_keys (user_sub, email, description, key_hash, key_prefix, plan, lookups_limit)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (user.sub, user.email, description, key_hash, prefix, plan, lookups_limit))
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
        'lookups_limit': lookups_limit,
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
