"""
Authentication middleware for CanYouGrab API.

Two auth paths:
  1. API key auth — for public API consumers (Authorization: Bearer <key>)
  2. JWT auth — for portal/dashboard endpoints (Auth0 JWT)
"""

import hashlib
import json
import logging
import os
import time
from functools import lru_cache
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request
from jose import jwt, JWTError

from queries import get_db_conn
from valkey_client import get_valkey

logger = logging.getLogger(__name__)

AUTH0_DOMAIN = os.environ.get('AUTH0_DOMAIN', 'login.canyougrab.it')
AUTH0_AUDIENCE = 'https://api.canyougrab.it'
AUTH0_ISSUER = f'https://{AUTH0_DOMAIN}/'
JWKS_URL = f'https://{AUTH0_DOMAIN}/.well-known/jwks.json'
RESOURCE_SCOPES = frozenset({'domains.read', 'account.read'})

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
    __slots__ = ('consumer_id', 'user_sub', 'plan', 'email', 'scopes', 'auth_type')

    def __init__(
        self,
        consumer_id: str,
        user_sub: str,
        plan: str,
        email: str = '',
        scopes: frozenset[str] | None = None,
        auth_type: str = 'api_key',
    ):
        self.consumer_id = consumer_id
        self.user_sub = user_sub
        self.plan = plan
        self.email = email
        self.scopes = scopes or RESOURCE_SCOPES
        self.auth_type = auth_type


def _lookup_api_key_user(raw_key: str, *, scopes: frozenset[str], auth_type: str) -> APIKeyUser:
    key_hash = _hash_key(raw_key)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_sub, plan, email, disabled_at
                FROM api_keys
                WHERE key_hash = %s AND revoked_at IS NULL
            """, (key_hash,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=401, detail='Invalid or revoked API key')

    if row[4] is not None:
        raise HTTPException(status_code=403, detail='API key is disabled')

    return APIKeyUser(
        consumer_id=str(row[0]),
        user_sub=row[1],
        plan=row[2],
        email=row[3] or '',
        scopes=scopes,
        auth_type=auth_type,
    )


def _oauth_access_payload(raw_token: str) -> dict | None:
    payload = get_valkey().get(f'oauth:access:{raw_token}')
    return json.loads(payload) if payload else None


def _authenticate_api_bearer(raw_bearer: str, required_scopes: frozenset[str] | None = None) -> APIKeyUser:
    required_scopes = required_scopes or frozenset()

    oauth_payload = _oauth_access_payload(raw_bearer)
    if oauth_payload:
        granted_scopes = frozenset(scope for scope in oauth_payload.get('scope', '').split() if scope)
        if not required_scopes.issubset(granted_scopes):
            raise HTTPException(
                status_code=401,
                detail='OAuth token is missing a required scope',
                headers={'WWW-Authenticate': 'Bearer error="insufficient_scope"'},
            )
        return _lookup_api_key_user(
            oauth_payload['api_key'],
            scopes=granted_scopes,
            auth_type='oauth_access_token',
        )

    return _lookup_api_key_user(raw_bearer, scopes=RESOURCE_SCOPES, auth_type='api_key')


def api_key_auth(request: Request) -> APIKeyUser:
    """FastAPI dependency — validates Bearer API key from Authorization header."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing or invalid Authorization header. Use: Bearer <api_key>')

    raw_key = auth_header[7:]
    if not raw_key:
        raise HTTPException(status_code=401, detail='Empty API key')

    return _authenticate_api_bearer(raw_key)


def _scoped_api_key_auth(*required_scopes: str):
    required = frozenset(required_scopes)

    def dependency(request: Request) -> APIKeyUser:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            raise HTTPException(status_code=401, detail='Missing or invalid Authorization header. Use: Bearer <api_key>')

        raw_key = auth_header[7:]
        if not raw_key:
            raise HTTPException(status_code=401, detail='Empty API key')

        return _authenticate_api_bearer(raw_key, required)

    return dependency


domains_read_auth = _scoped_api_key_auth('domains.read')
account_read_auth = _scoped_api_key_auth('account.read')


# ── JWT Auth (portal/dashboard) ───────────────────────────────────

NS = 'https://api.canyougrab.it/'


class JWTUser:
    """Represents an authenticated Auth0 JWT user."""
    __slots__ = ('sub', 'email', 'name', 'email_verified')

    def __init__(self, sub: str, email: str = '', name: str = '',
                 email_verified: bool = False):
        self.sub = sub
        self.email = email
        self.name = name
        self.email_verified = email_verified


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
        email=payload.get('email', payload.get(f'{NS}email', '')),
        name=payload.get('name', payload.get(f'{NS}name', '')),
        email_verified=payload.get('email_verified', payload.get(f'{NS}email_verified', False)),
    )
