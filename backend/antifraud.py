"""
Anti-fraud endpoints and utilities.
Handles Cloudflare Turnstile verification, device fingerprint tracking,
risk scoring, and card fingerprint deduplication.
"""

import logging
import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from auth import JWTUser, jwt_auth
from queries import get_db_conn

logger = logging.getLogger(__name__)

antifraud_router = APIRouter(prefix='/api/antifraud', tags=['Anti-Fraud'])

# ── Cloudflare Turnstile ──────────────────────────────────────────

TURNSTILE_SECRET = os.environ.get('TURNSTILE_SECRET_KEY', '')
TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


class TurnstileRequest(BaseModel):
    token: str


@antifraud_router.post('/turnstile/verify')
def verify_turnstile(body: TurnstileRequest, request: Request):
    """Verify a Cloudflare Turnstile token. Called from frontend at signup."""
    if not TURNSTILE_SECRET:
        # Turnstile not configured — pass through (dev mode)
        return {'success': True, 'dev_mode': True}

    client_ip = request.headers.get(
        'x-forwarded-for', request.client.host if request.client else ''
    ).split(',')[0].strip()

    resp = httpx.post(TURNSTILE_VERIFY_URL, data={
        'secret': TURNSTILE_SECRET,
        'response': body.token,
        'remoteip': client_ip,
    }, timeout=10)

    result = resp.json()
    if not result.get('success'):
        logger.warning('Turnstile verification failed: %s', result.get('error-codes', []))
        raise HTTPException(status_code=403, detail='Bot verification failed. Please try again.')

    return {'success': True}


# ── Device fingerprint tracking ───────────────────────────────────

class DeviceFingerprintRequest(BaseModel):
    visitor_id: str


@antifraud_router.post('/device/register')
def register_device_fingerprint(body: DeviceFingerprintRequest, user: JWTUser = Depends(jwt_auth)):
    """Store a device fingerprint (Fingerprint Pro visitorId) for the authenticated user.
    Used for multi-account detection — same visitorId across accounts = same device.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Check how many accounts share this visitor_id
            cur.execute("""
                SELECT COUNT(DISTINCT user_sub) FROM device_fingerprints
                WHERE visitor_id = %s AND user_sub != %s
            """, (body.visitor_id, user.sub))
            other_accounts = cur.fetchone()[0]

            # Upsert the fingerprint record
            cur.execute("""
                INSERT INTO device_fingerprints (user_sub, visitor_id)
                VALUES (%s, %s)
                ON CONFLICT (user_sub, visitor_id) DO UPDATE SET last_seen_at = NOW()
            """, (user.sub, body.visitor_id))
            conn.commit()

            # Update risk score if device is shared across multiple accounts
            if other_accounts > 0:
                _add_risk_signal(conn, user.sub, 'shared_device', 25,
                                 f'Device shared with {other_accounts} other account(s)')

    finally:
        conn.close()

    return {'registered': True, 'shared_accounts': other_accounts}


# ── Risk scoring ──────────────────────────────────────────────────

RISK_THRESHOLDS = {
    'normal': 0,
    'elevated': 30,
    'high': 50,
    'critical': 70,
}


def _add_risk_signal(conn, user_sub: str, signal_name: str, points: int, detail: str = ''):
    """Add a risk signal for a user, updating their composite score."""
    with conn.cursor() as cur:
        # Upsert account_risk row
        cur.execute("""
            INSERT INTO account_risk (user_sub, risk_score, risk_signals)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (user_sub) DO UPDATE SET
                risk_score = account_risk.risk_score + %s,
                risk_signals = account_risk.risk_signals || %s::jsonb,
                last_evaluated_at = NOW()
        """, (
            user_sub,
            points,
            _signal_json(signal_name, points, detail),
            points,
            _signal_json(signal_name, points, detail),
        ))
        conn.commit()


def _signal_json(name: str, points: int, detail: str) -> str:
    """Build a JSON object for a risk signal entry."""
    import json
    ts = datetime.now(timezone.utc).isoformat()
    return json.dumps({name: {'points': points, 'detail': detail, 'timestamp': ts}})


def get_risk_level(user_sub: str) -> dict:
    """Get the current risk level for a user."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT risk_score, risk_signals, action_taken
                FROM account_risk WHERE user_sub = %s
            """, (user_sub,))
            row = cur.fetchone()
            if not row:
                return {'score': 0, 'level': 'normal', 'action': 'none', 'signals': {}}

            score = row[0]
            level = 'normal'
            for name, threshold in sorted(RISK_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
                if score >= threshold:
                    level = name
                    break

            return {
                'score': score,
                'level': level,
                'action': row[2] or 'none',
                'signals': row[1] or {},
            }
    finally:
        conn.close()


@antifraud_router.get('/risk')
def get_user_risk(user: JWTUser = Depends(jwt_auth)):
    """Get risk assessment for the authenticated user (admin/debug)."""
    return get_risk_level(user.sub)


# ── Signup risk assessment ────────────────────────────────────────

class SignupRiskRequest(BaseModel):
    visitor_id: Optional[str] = None
    turnstile_token: Optional[str] = None


@antifraud_router.post('/assess-signup')
def assess_signup_risk(body: SignupRiskRequest, request: Request, user: JWTUser = Depends(jwt_auth)):
    """Run risk assessment at signup time. Combines IP, device, and email signals."""
    signals = []
    total_points = 0

    client_ip = request.headers.get(
        'x-forwarded-for', request.client.host if request.client else ''
    ).split(',')[0].strip()

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Check for normalized email duplicates (different user_sub, same normalized email)
            from email_utils import normalize_email
            norm = normalize_email(user.email)
            cur.execute("""
                SELECT COUNT(DISTINCT user_sub) FROM api_keys
                WHERE email_normalized = %s AND user_sub != %s AND revoked_at IS NULL
            """, (norm, user.sub))
            email_dupes = cur.fetchone()[0]
            if email_dupes > 0:
                pts = 30
                signals.append({'signal': 'email_duplicate', 'points': pts,
                                'detail': f'Normalized email matches {email_dupes} other account(s)'})
                total_points += pts

            # Check for device fingerprint overlap
            if body.visitor_id:
                cur.execute("""
                    SELECT COUNT(DISTINCT user_sub) FROM device_fingerprints
                    WHERE visitor_id = %s AND user_sub != %s
                """, (body.visitor_id, user.sub))
                device_dupes = cur.fetchone()[0]
                if device_dupes > 0:
                    pts = 25
                    signals.append({'signal': 'shared_device', 'points': pts,
                                    'detail': f'Device shared with {device_dupes} other account(s)'})
                    total_points += pts

            # Check for multiple signups from same IP in last 24 hours
            cur.execute("""
                SELECT COUNT(DISTINCT user_sub) FROM api_keys
                WHERE created_at > NOW() - INTERVAL '24 hours'
                  AND user_sub != %s
            """, (user.sub,))
            # Note: We'd need to track signup IPs to do this properly.
            # For now, this is a placeholder for future IP-signup tracking.

            # Store the risk assessment
            if total_points > 0:
                for sig in signals:
                    _add_risk_signal(conn, user.sub, sig['signal'], sig['points'], sig['detail'])

    finally:
        conn.close()

    level = 'normal'
    for name, threshold in sorted(RISK_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
        if total_points >= threshold:
            level = name
            break

    return {
        'risk_score': total_points,
        'risk_level': level,
        'signals': signals,
    }
