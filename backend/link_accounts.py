"""
Account linking page — served during Auth0 post-login redirect.

When a user logs in with a provider that shares an email with an existing
account, the Auth0 Action redirects here. The user must either link the
accounts or cancel login entirely. No duplicate emails allowed.
"""

import os
import logging
import time
import hmac
import hashlib
import base64
import json
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Account Linking"])

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "login.canyougrab.it")
LINK_SECRET = os.environ.get("ACCOUNT_LINK_SECRET", "")

PROVIDER_LABELS = {
    "google-oauth2": "Google",
    "apple": "Apple",
    "auth0": "Email & Password",
    "Username-Password-Authentication": "Email & Password",
}


def _decode_session_token(token: str) -> dict | None:
    """Decode and verify an HS256 JWT from the Auth0 Action."""
    if not LINK_SECRET:
        logger.error("ACCOUNT_LINK_SECRET not configured")
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            logger.warning("Session token expired")
            return None
        # Verify signature
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        expected_sig = base64.urlsafe_b64encode(
            hmac.new(LINK_SECRET.encode(), signing_input, hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        if not hmac.compare_digest(expected_sig, parts[2]):
            logger.warning("Session token signature mismatch")
            return None
        return payload
    except Exception as e:
        logger.error("Failed to decode session token: %s", e)
        return None


def _encode_link_token(action: str, payload: dict, state: str) -> str:
    """Create a signed JWT to send back to Auth0's /continue endpoint.

    Auth0's validateToken requires 'state' and 'sub' claims to match the session.
    """
    header = base64.urlsafe_b64encode(json.dumps(
        {"alg": "HS256", "typ": "JWT"}
    ).encode()).rstrip(b"=").decode()

    claims = {
        "action": action,
        "state": state,
        "sub": payload.get("sub", payload.get("current_user_id", "")),
        "current_user_id": payload.get("current_user_id", ""),
        "duplicate_user_id": payload.get("duplicate_user_id", ""),
        "iss": f"https://{AUTH0_DOMAIN}",
        "iat": int(time.time()),
        "exp": int(time.time()) + 120,  # 2 minute TTL
    }
    body = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).rstrip(b"=").decode()

    signing_input = f"{header}.{body}".encode()
    sig = base64.urlsafe_b64encode(
        hmac.new(LINK_SECRET.encode(), signing_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode()

    return f"{header}.{body}.{sig}"


def _provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


@router.get("/auth/link-accounts", response_class=HTMLResponse)
async def link_accounts_page(request: Request):
    """Serve the account linking decision page."""
    state = request.query_params.get("state", "")
    session_token = request.query_params.get("session_token", "")

    if not state or not session_token:
        return HTMLResponse(_error_page("Invalid request — missing parameters."), status_code=400)

    payload = _decode_session_token(session_token)
    if not payload:
        return HTMLResponse(_error_page("Invalid or expired session. Please try logging in again."), status_code=400)

    email = payload.get("email", "unknown")
    current_provider = _provider_label(payload.get("current_provider", ""))
    duplicate_provider = _provider_label(payload.get("duplicate_provider", ""))

    link_token = _encode_link_token("link", payload, state)
    cancel_token = _encode_link_token("cancel", payload, state)

    continue_url = f"https://{AUTH0_DOMAIN}/continue"
    link_url = f"{continue_url}?state={urllib.parse.quote(state)}&link_token={urllib.parse.quote(link_token)}"
    cancel_url = f"{continue_url}?state={urllib.parse.quote(state)}&link_token={urllib.parse.quote(cancel_token)}"

    return HTMLResponse(_linking_page(email, current_provider, duplicate_provider, link_url, cancel_url))


def _linking_page(email: str, current_provider: str, duplicate_provider: str,
                  link_url: str, cancel_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Link Your Account — canyougrab.it</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .card {{
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 12px;
            padding: 40px;
            max-width: 480px;
            width: 100%;
            text-align: center;
        }}
        .icon {{
            font-size: 48px;
            margin-bottom: 16px;
        }}
        h1 {{
            font-size: 22px;
            font-weight: 600;
            margin-bottom: 12px;
            color: #fff;
        }}
        .email {{
            color: #4ade80;
            font-weight: 600;
        }}
        .info {{
            color: #999;
            font-size: 14px;
            line-height: 1.6;
            margin: 16px 0 28px;
        }}
        .providers {{
            display: flex;
            justify-content: center;
            gap: 24px;
            margin: 20px 0;
        }}
        .provider {{
            background: #222;
            border: 1px solid #444;
            border-radius: 8px;
            padding: 12px 20px;
            font-size: 14px;
        }}
        .provider-label {{
            color: #888;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }}
        .provider-name {{
            color: #fff;
            font-weight: 600;
        }}
        .buttons {{
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-top: 28px;
        }}
        .btn {{
            display: block;
            padding: 14px 24px;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            border: none;
            transition: opacity 0.15s;
        }}
        .btn:hover {{ opacity: 0.85; }}
        .btn-link {{
            background: #4ade80;
            color: #000;
        }}
        .btn-cancel {{
            background: transparent;
            color: #999;
            border: 1px solid #444;
        }}
        .btn-cancel:hover {{ color: #fff; border-color: #666; }}
        .note {{
            margin-top: 20px;
            font-size: 12px;
            color: #666;
            line-height: 1.5;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">&#128279;</div>
        <h1>Account Already Exists</h1>
        <p class="info">
            An account with <span class="email">{email}</span> already exists
            using a different login method.
        </p>
        <div class="providers">
            <div class="provider">
                <div class="provider-label">Existing</div>
                <div class="provider-name">{duplicate_provider}</div>
            </div>
            <div class="provider">
                <div class="provider-label">Current</div>
                <div class="provider-name">{current_provider}</div>
            </div>
        </div>
        <p class="info">
            Link them to use either method to access the same account,
            API keys, and billing.
        </p>
        <div class="buttons">
            <a href="{link_url}" class="btn btn-link">Link Accounts</a>
            <a href="{cancel_url}" class="btn btn-cancel">Cancel Login</a>
        </div>
        <p class="note">
            After linking, both login methods will work interchangeably.
        </p>
    </div>
</body>
</html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Error — canyougrab.it</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }}
        .card {{
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 12px;
            padding: 40px;
            max-width: 400px;
            text-align: center;
        }}
        h1 {{ font-size: 20px; color: #ef4444; margin-bottom: 12px; }}
        p {{ color: #999; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Something went wrong</h1>
        <p>{message}</p>
    </div>
</body>
</html>"""
