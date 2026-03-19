"""
OAuth 2.0 Authorization Code Flow for MCP clients (Claude, ChatGPT, etc.).

Bridges Auth0 authentication with CanYouGrab API keys. The flow:
  1. MCP client redirects user to /oauth/authorize
  2. We redirect to Auth0 for login
  3. Auth0 redirects back to /oauth/callback
  4. We look up/create an API key for the user, generate an auth code
  5. Redirect to the MCP client's callback with the code
  6. Client exchanges code for an opaque access token at POST /oauth/token
  7. If offline_access is granted, the client can refresh that access token
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from queries import get_db_conn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OAuth"])

# Auth0 configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "auth.canyougrab.it")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "xeaTguUBeoeZg2PmetPVrnQmkud8Ikyq")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "")
AUTH0_AUDIENCE = "https://api.canyougrab.it"

# OAuth server configuration
OAUTH_ISSUER_OVERRIDE = os.environ.get("OAUTH_ISSUER", "").rstrip("/")
AUTH_CODE_TTL = 300  # 5 minutes
OAUTH_SESSION_TTL = 600  # 10 minutes
OAUTH_CLIENT_TTL = int(os.environ.get("OAUTH_CLIENT_TTL", str(30 * 24 * 60 * 60)))
OAUTH_RESOURCE_OVERRIDE = os.environ.get("OAUTH_RESOURCE", "").rstrip("/")
PROTECTED_RESOURCE_METADATA_URL_OVERRIDE = os.environ.get(
    "OAUTH_PROTECTED_RESOURCE_METADATA_URL",
    "",
).rstrip("/")
RESOURCE_DOCUMENTATION_URL_OVERRIDE = os.environ.get(
    "OAUTH_RESOURCE_DOCUMENTATION_URL",
    "",
).rstrip("/")
DEFAULT_PUBLIC_ISSUER = "https://api.canyougrab.it"
DEFAULT_PORTAL_DOCS_URL = "https://portal.canyougrab.it/docs"
DEFAULT_DEV_PORTAL_DOCS_URL = "https://dev-portal.canyougrab.it/docs"
RESOURCE_SCOPES = ["domains.read", "account.read"]
OFFLINE_ACCESS_SCOPE = "offline_access"
SUPPORTED_SCOPES = RESOURCE_SCOPES + [OFFLINE_ACCESS_SCOPE]
DEFAULT_OAUTH_SCOPE = " ".join(SUPPORTED_SCOPES)
OAUTH_ACCESS_TOKEN_TTL = int(os.environ.get("OAUTH_ACCESS_TOKEN_TTL", str(60 * 60)))
OAUTH_REFRESH_TOKEN_TTL = int(os.environ.get("OAUTH_REFRESH_TOKEN_TTL", str(30 * 24 * 60 * 60)))
ACCESS_TOKEN_PREFIX = "cgoa_"
REFRESH_TOKEN_PREFIX = "cgor_"


def _get_valkey():
    from valkey_client import get_valkey
    return get_valkey()


def _get_registered_client(client_id: str) -> dict[str, Any] | None:
    if not client_id:
        return None

    raw = _get_valkey().get(f"oauth:client:{client_id}")
    return json.loads(raw) if raw else None


def _save_registered_client(client_id: str, data: dict[str, Any]) -> None:
    _get_valkey().setex(
        f"oauth:client:{client_id}",
        OAUTH_CLIENT_TTL,
        json.dumps(data),
    )


def _parse_scope_param(raw_scope: str | None) -> list[str]:
    if not raw_scope:
        return []
    return [scope for scope in raw_scope.split() if scope]


def _validate_requested_scopes(raw_scope: str | None) -> str | None:
    scopes = _parse_scope_param(raw_scope)
    invalid = [scope for scope in scopes if scope not in SUPPORTED_SCOPES]
    if invalid:
        return f"Unsupported scope(s): {' '.join(invalid)}"
    return None


def _refresh_token_requested(raw_scope: str | None) -> bool:
    return OFFLINE_ACCESS_SCOPE in _parse_scope_param(raw_scope)


def _access_token_key(token: str) -> str:
    return f"oauth:access:{token}"


def _refresh_token_key(token: str) -> str:
    return f"oauth:refresh:{token}"


def _issue_access_token(*, api_key: str, client_id: str, scope: str, resource: str) -> tuple[str, int]:
    token = ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)
    payload = {
        "api_key": api_key,
        "client_id": client_id,
        "scope": scope,
        "resource": resource,
        "issued_at": int(time.time()),
    }
    _get_valkey().setex(_access_token_key(token), OAUTH_ACCESS_TOKEN_TTL, json.dumps(payload))
    return token, OAUTH_ACCESS_TOKEN_TTL


def _issue_refresh_token(*, api_key: str, client_id: str, scope: str, resource: str) -> str:
    token = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    payload = {
        "api_key": api_key,
        "client_id": client_id,
        "scope": scope,
        "resource": resource,
        "issued_at": int(time.time()),
    }
    _get_valkey().setex(_refresh_token_key(token), OAUTH_REFRESH_TOKEN_TTL, json.dumps(payload))
    return token


def _token_response(
    *,
    api_key: str,
    client_id: str,
    scope: str,
    resource: str,
    include_refresh_token: bool,
    refresh_token: str | None = None,
) -> JSONResponse:
    access_token, expires_in = _issue_access_token(
        api_key=api_key,
        client_id=client_id,
        scope=scope,
        resource=resource,
    )
    body: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "scope": scope,
        "expires_in": expires_in,
    }
    if include_refresh_token:
        body["refresh_token"] = refresh_token or _issue_refresh_token(
            api_key=api_key,
            client_id=client_id,
            scope=scope,
            resource=resource,
        )
        body["refresh_token_expires_in"] = OAUTH_REFRESH_TOKEN_TTL
    return JSONResponse(body)


def _request_origin(request: Request | None = None) -> str:
    if request is not None:
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        forwarded_host = request.headers.get("x-forwarded-host", "")
        scheme = forwarded_proto.split(",", 1)[0].strip() or request.url.scheme or "https"
        host = (
            forwarded_host.split(",", 1)[0].strip()
            or request.headers.get("host", "").split(",", 1)[0].strip()
            or request.url.netloc
        )
        if host:
            return f"{scheme}://{host}"
    return OAUTH_ISSUER_OVERRIDE or DEFAULT_PUBLIC_ISSUER


def _oauth_issuer(request: Request | None = None) -> str:
    return _request_origin(request)


def _mcp_resource_url(request: Request | None = None) -> str:
    return OAUTH_RESOURCE_OVERRIDE or f"{_oauth_issuer(request)}/mcp"


def _protected_resource_metadata_url(request: Request | None = None) -> str:
    return (
        PROTECTED_RESOURCE_METADATA_URL_OVERRIDE
        or f"{_oauth_issuer(request)}/.well-known/oauth-protected-resource/mcp"
    )


def _resource_documentation_url(request: Request | None = None) -> str:
    if RESOURCE_DOCUMENTATION_URL_OVERRIDE:
        return RESOURCE_DOCUMENTATION_URL_OVERRIDE
    host = urllib.parse.urlparse(_oauth_issuer(request)).netloc.lower()
    if host.startswith("dev-") or host.startswith("dev."):
        return DEFAULT_DEV_PORTAL_DOCS_URL
    return DEFAULT_PORTAL_DOCS_URL


def _protected_resource_metadata(request: Request | None = None) -> dict[str, Any]:
    return {
        "resource": _mcp_resource_url(request),
        "authorization_servers": [_oauth_issuer(request)],
        "scopes_supported": RESOURCE_SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_name": "CanYouGrab.it MCP",
        "resource_documentation": _resource_documentation_url(request),
    }


# ── OAuth Metadata ────────────────────────────────────────────────

@router.get("/.well-known/oauth-authorization-server")
def oauth_metadata(request: Request):
    """RFC 8414 OAuth 2.0 Authorization Server Metadata."""
    issuer = _oauth_issuer(request)
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": SUPPORTED_SCOPES,
    }


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata_root(request: Request):
    """Compatibility route for clients that probe the root well-known path."""
    return _protected_resource_metadata(request)


@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata_mcp(request: Request):
    """RFC 9728 OAuth 2.0 Protected Resource Metadata for the /mcp resource."""
    return _protected_resource_metadata(request)


@router.post("/oauth/register")
async def register_client(request: Request):
    """Minimal Dynamic Client Registration endpoint for public OAuth clients."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    redirect_uris = payload.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            {
                "error": "invalid_client_metadata",
                "error_description": "redirect_uris must be a non-empty array",
            },
            status_code=400,
        )

    if any(not isinstance(uri, str) or not uri for uri in redirect_uris):
        return JSONResponse(
            {
                "error": "invalid_redirect_uri",
                "error_description": "Each redirect URI must be a non-empty string",
            },
            status_code=400,
        )

    raw_scope = payload.get("scope")
    scope_error = _validate_requested_scopes(raw_scope)
    if scope_error:
        return JSONResponse(
            {"error": "invalid_scope", "error_description": scope_error},
            status_code=400,
        )

    client_id = "cgpt_" + secrets.token_urlsafe(24)
    issued_at = int(time.time())
    record = {
        "client_id": client_id,
        "client_name": payload.get("client_name", "CanYouGrab OAuth client"),
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": raw_scope or DEFAULT_OAUTH_SCOPE,
        "created_at": issued_at,
    }
    _save_registered_client(client_id, record)

    return {
        **record,
        "client_id_issued_at": issued_at,
    }


# ── Authorization Endpoint ────────────────────────────────────────

@router.get("/oauth/authorize")
def authorize(request: Request):
    """Start the OAuth authorization flow. Redirects to Auth0 for login."""
    params = request.query_params
    oauth_issuer = _oauth_issuer(request)
    mcp_resource_url = _mcp_resource_url(request)

    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "")
    response_type = params.get("response_type", "")
    requested_scope = params.get("scope", DEFAULT_OAUTH_SCOPE)
    resource = params.get("resource", mcp_resource_url)

    if response_type != "code":
        return JSONResponse(
            {"error": "unsupported_response_type", "error_description": "Only 'code' is supported"},
            status_code=400,
        )

    if not redirect_uri:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "redirect_uri is required"},
            status_code=400,
        )

    scope_error = _validate_requested_scopes(requested_scope)
    if scope_error:
        return JSONResponse(
            {"error": "invalid_scope", "error_description": scope_error},
            status_code=400,
        )

    if resource != mcp_resource_url:
        return JSONResponse(
            {
                "error": "invalid_target",
                "error_description": f"Only resource={mcp_resource_url} is supported",
            },
            status_code=400,
        )

    registered_client = _get_registered_client(client_id) if client_id else None
    if client_id and not registered_client:
        return JSONResponse(
            {"error": "invalid_client", "error_description": "Unknown client_id"},
            status_code=400,
        )

    if registered_client and redirect_uri not in registered_client.get("redirect_uris", []):
        return JSONResponse(
            {
                "error": "invalid_request",
                "error_description": "redirect_uri must match the registered client metadata",
            },
            status_code=400,
        )

    # Store OAuth session in Valkey
    session_id = secrets.token_urlsafe(32)
    session_data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": requested_scope,
        "resource": resource,
    }

    r = _get_valkey()
    r.setex(f"oauth:session:{session_id}", OAUTH_SESSION_TTL, json.dumps(session_data))

    # Build the callback URL for Auth0 to redirect back to us
    our_callback = f"{oauth_issuer}/oauth/callback"

    # Redirect to Auth0 with our session_id in the state
    auth0_params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": our_callback,
        "scope": "openid email profile",
        "audience": AUTH0_AUDIENCE,
        "state": session_id,
    })

    return RedirectResponse(f"https://{AUTH0_DOMAIN}/authorize?{auth0_params}")


# ── Auth0 Callback ────────────────────────────────────────────────

@router.get("/oauth/callback")
async def callback(request: Request):
    """Handle Auth0 callback, generate authorization code, redirect to MCP client."""
    params = request.query_params
    auth0_code = params.get("code", "")
    session_id = params.get("state", "")
    error = params.get("error", "")

    if error:
        error_desc = params.get("error_description", "Authentication failed")
        logger.warning("Auth0 returned error: %s — %s", error, error_desc)
        return HTMLResponse(
            f"<h2>Authentication failed</h2><p>{error_desc}</p><p>Please close this window and try again.</p>",
            status_code=400,
        )

    if not auth0_code or not session_id:
        return HTMLResponse("<h2>Invalid callback</h2><p>Missing parameters.</p>", status_code=400)

    # Retrieve stored OAuth session
    r = _get_valkey()
    session_raw = r.get(f"oauth:session:{session_id}")
    if not session_raw:
        return HTMLResponse("<h2>Session expired</h2><p>Please try connecting again.</p>", status_code=400)

    session = json.loads(session_raw)
    r.delete(f"oauth:session:{session_id}")

    # Exchange Auth0 code for tokens
    oauth_issuer = _oauth_issuer(request)
    our_callback = f"{oauth_issuer}/oauth/callback"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": AUTH0_CLIENT_ID,
                    "client_secret": AUTH0_CLIENT_SECRET,
                    "code": auth0_code,
                    "redirect_uri": our_callback,
                },
            )
    except httpx.HTTPError as e:
        logger.error("Auth0 token exchange failed: %s", e)
        return HTMLResponse("<h2>Authentication error</h2><p>Please try again.</p>", status_code=500)

    if token_resp.status_code != 200:
        logger.error("Auth0 token exchange returned %d: %s", token_resp.status_code, token_resp.text)
        return HTMLResponse("<h2>Authentication error</h2><p>Please try again.</p>", status_code=500)

    tokens = token_resp.json()
    access_token = tokens.get("access_token", "")

    # Get user info from Auth0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            userinfo_resp = await client.get(
                f"https://{AUTH0_DOMAIN}/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        userinfo = userinfo_resp.json()
    except Exception as e:
        logger.error("Failed to get userinfo: %s", e)
        return HTMLResponse("<h2>Authentication error</h2><p>Please try again.</p>", status_code=500)

    user_sub = userinfo.get("sub", "")
    user_email = userinfo.get("email", "")

    if not user_sub:
        return HTMLResponse("<h2>Authentication error</h2><p>Could not identify user.</p>", status_code=500)

    # Upsert user record
    try:
        from users import upsert_user
        auth_provider = user_sub.split('|')[0] if '|' in user_sub else ''
        upsert_user(
            auth0_sub=user_sub,
            email=user_email,
            name=userinfo.get('name', ''),
            email_verified=userinfo.get('email_verified', False),
            auth_provider=auth_provider,
        )
    except Exception as e:
        logger.warning('Failed to upsert user during OAuth callback: %s', e)

    # Look up or create an API key for this user
    api_key = _get_or_create_api_key(user_sub, user_email)
    if not api_key:
        return HTMLResponse(
            "<h2>Account error</h2><p>Could not provision API key. Please try again.</p>",
            status_code=500,
        )

    # Generate authorization code and store in Valkey
    auth_code = secrets.token_urlsafe(48)
    code_data = {
        "api_key": api_key,
        "client_id": session.get("client_id", ""),
        "redirect_uri": session["redirect_uri"],
        "code_challenge": session.get("code_challenge", ""),
        "code_challenge_method": session.get("code_challenge_method", ""),
        "scope": session.get("scope", DEFAULT_OAUTH_SCOPE),
        "resource": session.get("resource", _mcp_resource_url(request)),
    }
    r.setex(f"oauth:code:{auth_code}", AUTH_CODE_TTL, json.dumps(code_data))

    # Redirect to MCP client's callback
    redirect_params = {"code": auth_code}
    if session.get("state"):
        redirect_params["state"] = session["state"]

    separator = "&" if "?" in session["redirect_uri"] else "?"
    redirect_url = session["redirect_uri"] + separator + urllib.parse.urlencode(redirect_params)

    return RedirectResponse(redirect_url)


# ── Token Endpoint ────────────────────────────────────────────────

@router.post("/oauth/token")
async def token(request: Request):
    """Exchange authorization code for access token."""
    # Accept both form-encoded and JSON
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        raw_body = (await request.body()).decode("utf-8")
        body = dict(urllib.parse.parse_qsl(raw_body, keep_blank_values=True))

    grant_type = body.get("grant_type", "")
    code = body.get("code", "")
    refresh_token = body.get("refresh_token", "")
    client_id = body.get("client_id", "")
    redirect_uri = body.get("redirect_uri", "")
    code_verifier = body.get("code_verifier", "")
    resource = body.get("resource", "")
    requested_scope = body.get("scope", "")

    if grant_type not in {"authorization_code", "refresh_token"}:
        return JSONResponse(
            {"error": "unsupported_grant_type"},
            status_code=400,
        )

    r = _get_valkey()
    if grant_type == "authorization_code":
        if not code:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "code is required"},
                status_code=400,
            )

        code_raw = r.get(f"oauth:code:{code}")
        if not code_raw:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"},
                status_code=400,
            )

        r.delete(f"oauth:code:{code}")
        code_data = json.loads(code_raw)

        if redirect_uri and redirect_uri != code_data["redirect_uri"]:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "redirect_uri mismatch"},
                status_code=400,
            )

        if code_data.get("client_id") and client_id and client_id != code_data["client_id"]:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "client_id mismatch"},
                status_code=400,
            )

        if resource and resource != code_data.get("resource", _mcp_resource_url(request)):
            return JSONResponse(
                {"error": "invalid_target", "error_description": "resource mismatch"},
                status_code=400,
            )

        if code_data.get("code_challenge"):
            if not code_verifier:
                return JSONResponse(
                    {"error": "invalid_request", "error_description": "code_verifier is required"},
                    status_code=400,
                )
            digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
            computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            if computed != code_data["code_challenge"]:
                return JSONResponse(
                    {"error": "invalid_grant", "error_description": "code_verifier validation failed"},
                    status_code=400,
                )

        granted_scope = code_data.get("scope", DEFAULT_OAUTH_SCOPE)
        return _token_response(
            api_key=code_data["api_key"],
            client_id=code_data.get("client_id", ""),
            scope=granted_scope,
            resource=code_data.get("resource", _mcp_resource_url(request)),
            include_refresh_token=_refresh_token_requested(granted_scope),
        )

    if not refresh_token:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "refresh_token is required"},
            status_code=400,
        )

    refresh_raw = r.get(_refresh_token_key(refresh_token))
    if not refresh_raw:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "Invalid or expired refresh token"},
            status_code=400,
        )

    refresh_data = json.loads(refresh_raw)

    if refresh_data.get("client_id") and client_id and client_id != refresh_data["client_id"]:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "client_id mismatch"},
            status_code=400,
        )

    if resource and resource != refresh_data.get("resource", _mcp_resource_url(request)):
        return JSONResponse(
            {"error": "invalid_target", "error_description": "resource mismatch"},
            status_code=400,
        )

    scope_error = _validate_requested_scopes(requested_scope or None)
    if scope_error:
        return JSONResponse(
            {"error": "invalid_scope", "error_description": scope_error},
            status_code=400,
        )

    original_scope_set = set(_parse_scope_param(refresh_data.get("scope", DEFAULT_OAUTH_SCOPE)))
    if requested_scope:
        requested_scope_set = set(_parse_scope_param(requested_scope))
        if not requested_scope_set.issubset(original_scope_set):
            return JSONResponse(
                {"error": "invalid_scope", "error_description": "Requested scope exceeds original grant"},
                status_code=400,
            )
        granted_scope = requested_scope
    else:
        granted_scope = refresh_data.get("scope", DEFAULT_OAUTH_SCOPE)

    r.expire(_refresh_token_key(refresh_token), OAUTH_REFRESH_TOKEN_TTL)
    return _token_response(
        api_key=refresh_data["api_key"],
        client_id=refresh_data.get("client_id", ""),
        scope=granted_scope,
        resource=refresh_data.get("resource", _mcp_resource_url(request)),
        include_refresh_token=True,
        refresh_token=refresh_token,
    )


# ── Helpers ───────────────────────────────────────────────────────

def _get_or_create_api_key(user_sub: str, email: str) -> str | None:
    """Find the user's active API key, or create one labeled 'Claude MCP'."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Look for an existing active key
            cur.execute("""
                SELECT id, key_hash FROM api_keys
                WHERE user_sub = %s AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
            """, (user_sub,))
            existing = cur.fetchone()

            if existing:
                # We need the raw key, but we only store hashes.
                # Check if we have a cached raw key in Valkey for OAuth.
                # If not, we need to create a new key for the MCP connection.
                r = _get_valkey()
                cached = r.get(f"oauth:rawkey:{existing[0]}")
                if cached:
                    return cached

            # Create a new key specifically for MCP connections
            raw = "cyg_" + secrets.token_urlsafe(40)
            key_hash = hashlib.sha256(raw.encode()).hexdigest()
            prefix = raw[:12]

            # Get user's current plan
            plan = "free"
            if existing:
                cur.execute("SELECT plan FROM api_keys WHERE id = %s", (existing[0],))
                plan_row = cur.fetchone()
                if plan_row:
                    plan = plan_row[0]

            cur.execute("""
                INSERT INTO api_keys (user_sub, email, description, key_hash, key_prefix, plan)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (user_sub, email, "Claude MCP", key_hash, prefix, plan))
            key_id = cur.fetchone()[0]
            conn.commit()
            _get_valkey().setex(f"oauth:rawkey:{key_id}", OAUTH_REFRESH_TOKEN_TTL, raw)

            return raw
    except Exception as e:
        logger.error("Failed to get/create API key for OAuth: %s", e)
        conn.rollback()
        return None
    finally:
        conn.close()
