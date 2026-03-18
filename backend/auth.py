"""
Authentication middleware for CanYouGrab API.

Two auth paths:
  1. API key auth — for public API consumers (Authorization: Bearer <key>)
  2. JWT auth — for portal/dashboard endpoints (Auth0 JWT)
"""

import hashlib
import logging
import os
import time
from functools import lru_cache
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request
from jose import jwt, JWTError

from queries import get_db_conn

logger = logging.getLogger(__name__)

AUTH0_DOMAIN = 'auth.canyougrab.it'
AUTH0_AUDIENCE = 'https://api.canyougrab.it'
AUTH0_ISSUER = f'https://{AUTH0_DOMAIN}/'
JWKS_URL = f'https://{AUTH0_DOMAIN}/.well-known/jwks.json'

# JWKS cache
_jwks_cache = None
_jwks_fetched_at = 0
JWKS_CACHE_TTL = 3600  # 1 hour


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _get_jwks() -> dict:
    """Fetch and cache Auth0 JWKS."""
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache is None or (now - _jwks_fetched_at) > JWKS_CACHE_TTL:
        resp = httpx.get(JWKS_URL, timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
    return _jwks_cache


def _find_rsa_key(token: str) -> Optional[dict]:
    """Find the RSA key matching the token's kid."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        return None

    jwks = _get_jwks()
    for key in jwks.get('keys', []):
        if key['kid'] == unverified_header.get('kid'):
            return {
                'kty': key['kty'],
                'kid': key['kid'],
                'use': key['use'],
                'n': key['n'],
                'e': key['e'],
            }
    return None


# ── API Key Auth (public API) ──────────────────────────────────────

class APIKeyUser:
    """Represents an authenticated API key consumer."""
    __slots__ = ('consumer_id', 'user_sub', 'plan', 'email')

    def __init__(self, consumer_id: str, user_sub: str, plan: str, email: str = ''):
        self.consumer_id = consumer_id
        self.user_sub = user_sub
        self.plan = plan
        self.email = email


def api_key_auth(request: Request) -> APIKeyUser:
    """FastAPI dependency — validates Bearer API key from Authorization header."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing or invalid Authorization header. Use: Bearer <api_key>')

    raw_key = auth_header[7:]
    if not raw_key:
        raise HTTPException(status_code=401, detail='Empty API key')

    key_hash = _hash_key(raw_key)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_sub, plan, email
                FROM api_keys
                WHERE key_hash = %s AND revoked_at IS NULL
            """, (key_hash,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=401, detail='Invalid or revoked API key')

    return APIKeyUser(
        consumer_id=str(row[0]),
        user_sub=row[1],
        plan=row[2],
        email=row[3] or '',
    )


# ── JWT Auth (portal/dashboard) ───────────────────────────────────

class JWTUser:
    """Represents an authenticated Auth0 JWT user."""
    __slots__ = ('sub', 'email')

    def __init__(self, sub: str, email: str = ''):
        self.sub = sub
        self.email = email


def jwt_auth(request: Request) -> JWTUser:
    """FastAPI dependency — validates Auth0 JWT from Authorization header."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing or invalid Authorization header')

    token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail='Empty token')

    rsa_key = _find_rsa_key(token)
    if not rsa_key:
        raise HTTPException(status_code=401, detail='Unable to find appropriate key')

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=['RS256'],
            audience=AUTH0_AUDIENCE,
            issuer=AUTH0_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Token expired')
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f'Token validation failed: {e}')

    return JWTUser(
        sub=payload.get('sub', ''),
        email=payload.get('email', payload.get('https://api.canyougrab.it/email', '')),
    )
