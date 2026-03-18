"""
Stripe billing endpoints.
Mounted as a FastAPI router at /api/billing + /api/stripe.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth import JWTUser, jwt_auth
from queries import get_db_conn

logger = logging.getLogger(__name__)

STRIPE_API = 'https://api.stripe.com/v1'
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

PLAN_PRICE_MAP = {
    'basic':   {'priceId': os.environ.get('STRIPE_PRICE_BASIC',   'price_1TAggjH8ksFkvmqRNEE6UHx3'), 'limit': 10_000},
    'pro':     {'priceId': os.environ.get('STRIPE_PRICE_PRO',     'price_1TAggkH8ksFkvmqRUx9kVWf9'), 'limit': 50_000},
    'business':{'priceId': os.environ.get('STRIPE_PRICE_BUSINESS','price_1TAggkH8ksFkvmqRn7c63MZE'), 'limit': 300_000},
}

# Free tiers don't have Stripe prices — they're managed internally
FREE_PLAN_LIMITS = {
    'free': 50,
    'free_plus': 200,
}

PRICE_TO_PLAN = {}
for name, info in PLAN_PRICE_MAP.items():
    PRICE_TO_PLAN[info['priceId']] = {'name': name, 'limit': info['limit']}

PLAN_LIMITS = {name: info['limit'] for name, info in PLAN_PRICE_MAP.items()}
PLAN_LIMITS.update(FREE_PLAN_LIMITS)

billing_router = APIRouter(prefix='/api/billing', tags=['Billing'])
stripe_router = APIRouter(prefix='/api/stripe', tags=['Stripe'])


# ── Stripe helpers ─────────────────────────────────────────────────

def _stripe_headers() -> dict:
    return {'Authorization': f'Bearer {STRIPE_SECRET_KEY}'}


def _encode_body(obj: dict, prefix: str = '') -> str:
    """Encode nested dict to Stripe's form-urlencoded format."""
    from urllib.parse import urlencode
    params = []

    def flatten(o, p):
        if o is None:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                flatten(v, f'{p}[{k}]' if p else k)
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                flatten(v, f'{p}[{i}]')
        else:
            params.append((p, str(o)))

    flatten(obj, prefix)
    return urlencode(params)


def _stripe_request(method: str, path: str, body: dict = None) -> dict:
    """Make a request to the Stripe API."""
    url = f'{STRIPE_API}/{path}'
    headers = _stripe_headers()

    kwargs = {'headers': headers, 'timeout': 30}
    if body and method in ('POST', 'PATCH'):
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        kwargs['content'] = _encode_body(body)

    resp = httpx.request(method, url, **kwargs)
    return resp.json()


def _find_or_create_customer(auth0_sub: str, email: str = '') -> str:
    """Find existing Stripe customer by auth0_sub metadata, or create one."""
    from urllib.parse import quote
    query = f"metadata['auth0_sub']:'{auth0_sub}'"
    search = _stripe_request('GET', f'customers/search?query={quote(query)}')
    if search.get('data'):
        return search['data'][0]['id']

    customer = _stripe_request('POST', 'customers', {
        'email': email,
        'metadata': {'auth0_sub': auth0_sub},
    })
    return customer['id']


def _get_active_subscription(customer_id: str) -> Optional[dict]:
    """Get the user's active subscription from Stripe."""
    subs = _stripe_request('GET', f'subscriptions?customer={customer_id}&status=active&limit=1')
    data = subs.get('data', [])
    return data[0] if data else None


def _update_user_plan(user_sub: str, plan_name: str, lookups_limit: int):
    """Update plan on all active API keys for a user."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE api_keys
                SET plan = %s, lookups_limit = %s
                WHERE user_sub = %s AND revoked_at IS NULL
            """, (plan_name, lookups_limit, user_sub))
            updated = cur.rowcount
            conn.commit()
        logger.info('Updated %d keys for user %s to plan=%s limit=%d',
                     updated, user_sub[:20], plan_name, lookups_limit)
    finally:
        conn.close()


def _verify_webhook_signature(payload: str, sig_header: str, secret: str) -> bool:
    """Verify Stripe webhook signature (v1 HMAC-SHA256)."""
    parts = {}
    for part in sig_header.split(','):
        kv = part.split('=', 1)
        if len(kv) == 2:
            parts[kv[0].strip()] = kv[1]

    timestamp = parts.get('t')
    signature = parts.get('v1')
    if not timestamp or not signature:
        return False

    # Check timestamp tolerance (5 minutes)
    age = abs(int(time.time()) - int(timestamp))
    if age > 300:
        return False

    signed_payload = f'{timestamp}.{payload}'
    expected = hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ── Billing endpoints ─────────────────────────────────────────────

PORTAL_URL = os.environ.get('PORTAL_URL', 'https://portal.canyougrab.it')


class CheckoutRequest(BaseModel):
    plan: str


@billing_router.post('/checkout')
def create_checkout(body: CheckoutRequest, user: JWTUser = Depends(jwt_auth)):
    """Create a Stripe Checkout session for subscribing to a plan."""
    plan = body.plan
    if plan not in PLAN_PRICE_MAP:
        raise HTTPException(status_code=400, detail=f'Invalid plan: {plan}. Valid: {list(PLAN_PRICE_MAP.keys())}')

    price_id = PLAN_PRICE_MAP[plan]['priceId']
    customer_id = _find_or_create_customer(user.sub, user.email)

    session = _stripe_request('POST', 'checkout/sessions', {
        'customer': customer_id,
        'mode': 'subscription',
        'line_items': [{'price': price_id, 'quantity': 1}],
        'success_url': f'{PORTAL_URL}/pricing?checkout=success',
        'cancel_url': f'{PORTAL_URL}/pricing?checkout=cancel',
        'subscription_data': {
            'metadata': {'auth0_sub': user.sub},
        },
    })

    return {'url': session.get('url', '')}


@billing_router.post('/portal')
def create_portal(user: JWTUser = Depends(jwt_auth)):
    """Create a Stripe Customer Portal session."""
    customer_id = _find_or_create_customer(user.sub, user.email)

    session = _stripe_request('POST', 'billing_portal/sessions', {
        'customer': customer_id,
        'return_url': f'{PORTAL_URL}/usage',
    })

    return {'url': session.get('url', '')}


# ── Card on file (Free+ upgrade via SetupIntent) ──────────────────

@billing_router.post('/setup-card')
def setup_card(user: JWTUser = Depends(jwt_auth)):
    """Create a Stripe SetupIntent to collect a card for Free+ tier.
    The card is validated but not charged. Returns client_secret for Stripe Elements."""
    customer_id = _find_or_create_customer(user.sub, user.email)

    setup_intent = _stripe_request('POST', 'setup_intents', {
        'customer': customer_id,
        'usage': 'off_session',
        'metadata': {'auth0_sub': user.sub, 'purpose': 'free_plus_upgrade'},
    })

    return {
        'client_secret': setup_intent.get('client_secret', ''),
        'setup_intent_id': setup_intent.get('id', ''),
    }


@billing_router.post('/confirm-free-plus')
def confirm_free_plus(user: JWTUser = Depends(jwt_auth)):
    """After SetupIntent succeeds, verify card fingerprint and upgrade to Free+.
    Enforces one Free+ account per card fingerprint."""
    customer_id = _find_or_create_customer(user.sub, user.email)

    # Get the customer's payment methods to find the card fingerprint
    pms = _stripe_request('GET', f'payment_methods?customer={customer_id}&type=card&limit=1')
    pm_data = pms.get('data', [])
    if not pm_data:
        raise HTTPException(status_code=400, detail='No card found. Please add a card first.')

    card = pm_data[0].get('card', {})
    fingerprint = card.get('fingerprint', '')
    if not fingerprint:
        raise HTTPException(status_code=400, detail='Unable to verify card. Please try a different card.')

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Check if this card fingerprint is already used for a free account
            cur.execute("""
                SELECT user_sub FROM card_fingerprints
                WHERE stripe_fingerprint = %s AND user_sub != %s
            """, (fingerprint, user.sub))
            existing = cur.fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail='This card is already associated with another free account. '
                           'Each card can only be used for one free account.'
                )

            # Store the card fingerprint
            cur.execute("""
                INSERT INTO card_fingerprints (user_sub, stripe_fingerprint)
                VALUES (%s, %s)
                ON CONFLICT (user_sub, stripe_fingerprint) DO NOTHING
            """, (user.sub, fingerprint))

            # Upgrade all active keys from free to free_plus
            cur.execute("""
                UPDATE api_keys
                SET plan = 'free_plus', lookups_limit = %s
                WHERE user_sub = %s AND revoked_at IS NULL AND plan = 'free'
            """, (FREE_PLAN_LIMITS['free_plus'], user.sub))
            upgraded = cur.rowcount
            conn.commit()

        logger.info('User %s upgraded to free_plus (card fp: %s..., %d keys updated)',
                     user.sub[:20], fingerprint[:8], upgraded)
    finally:
        conn.close()

    return {
        'plan': 'free_plus',
        'lookups_limit': FREE_PLAN_LIMITS['free_plus'],
        'keys_upgraded': upgraded,
    }


@billing_router.get('/card-status')
def card_status(user: JWTUser = Depends(jwt_auth)):
    """Check if the user has a card on file."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM card_fingerprints WHERE user_sub = %s
            """, (user.sub,))
            has_card = cur.fetchone()[0] > 0
    finally:
        conn.close()

    return {'has_card': has_card}


# ── Stripe webhook ─────────────────────────────────────────────────

@stripe_router.post('/webhook')
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for subscription lifecycle."""
    payload = await request.body()
    payload_str = payload.decode()
    sig_header = request.headers.get('stripe-signature', '')

    if not sig_header:
        return JSONResponse({'error': 'Missing signature'}, status_code=400)

    if not _verify_webhook_signature(payload_str, sig_header, STRIPE_WEBHOOK_SECRET):
        logger.error('Invalid webhook signature')
        return JSONResponse({'error': 'Invalid signature'}, status_code=400)

    import json
    event = json.loads(payload_str)
    event_type = event.get('type', '')
    logger.info('Stripe webhook: %s', event_type)

    if event_type == 'checkout.session.completed':
        session = event['data']['object']
        subscription_id = session.get('subscription')
        if not subscription_id:
            logger.warning('checkout.session.completed missing subscription')
            return {'received': True}

        sub = _stripe_request('GET', f'subscriptions/{subscription_id}')
        price_id = sub.get('items', {}).get('data', [{}])[0].get('price', {}).get('id')

        auth0_sub = (
            sub.get('metadata', {}).get('auth0_sub')
            or session.get('metadata', {}).get('auth0_sub')
        )

        if auth0_sub and price_id and price_id in PRICE_TO_PLAN:
            plan_info = PRICE_TO_PLAN[price_id]
            _update_user_plan(auth0_sub, plan_info['name'], plan_info['limit'])

    elif event_type == 'customer.subscription.updated':
        subscription = event['data']['object']
        auth0_sub = subscription.get('metadata', {}).get('auth0_sub')
        price_id = subscription.get('items', {}).get('data', [{}])[0].get('price', {}).get('id')

        if auth0_sub and price_id and price_id in PRICE_TO_PLAN:
            plan_info = PRICE_TO_PLAN[price_id]
            _update_user_plan(auth0_sub, plan_info['name'], plan_info['limit'])

    elif event_type == 'customer.subscription.deleted':
        subscription = event['data']['object']
        auth0_sub = subscription.get('metadata', {}).get('auth0_sub')
        if auth0_sub:
            # Downgrade to free_plus if they have a card on file, otherwise free
            conn = get_db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM card_fingerprints WHERE user_sub = %s", (auth0_sub,))
                    has_card = cur.fetchone()[0] > 0
            finally:
                conn.close()
            if has_card:
                _update_user_plan(auth0_sub, 'free_plus', FREE_PLAN_LIMITS['free_plus'])
            else:
                _update_user_plan(auth0_sub, 'free', FREE_PLAN_LIMITS['free'])

    return {'received': True}


# ── Usage details (portal) ────────────────────────────────────────

@billing_router.get('/usage/detailed')
def get_usage_detailed(user: JWTUser = Depends(jwt_auth)):
    """Get detailed usage data for the portal dashboard."""
    from queries import get_monthly_detailed_usage, get_hourly_detailed_usage

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, key_prefix, description, plan, lookups_limit, created_at
                FROM api_keys
                WHERE user_sub = %s AND revoked_at IS NULL
                ORDER BY created_at
            """, (user.sub,))
            user_keys = cur.fetchall()
    finally:
        conn.close()

    if not user_keys:
        # No keys — check Stripe for subscription as fallback
        plan_name = 'free'
        plan_limit = FREE_PLAN_LIMITS['free']
        has_sub = False
        try:
            customer_id = _find_or_create_customer(user.sub, user.email)
            active_sub = _get_active_subscription(customer_id)
            if active_sub:
                price_id = active_sub.get('items', {}).get('data', [{}])[0].get('price', {}).get('id')
                if price_id and price_id in PRICE_TO_PLAN:
                    plan_name = PRICE_TO_PLAN[price_id]['name']
                    plan_limit = PRICE_TO_PLAN[price_id]['limit']
                    has_sub = True
        except Exception as e:
            logger.warning('Stripe fallback lookup failed: %s', e)

        # Check card on file for free_plus
        if plan_name == 'free':
            try:
                conn2 = get_db_conn()
                with conn2.cursor() as cur2:
                    cur2.execute("SELECT COUNT(*) FROM card_fingerprints WHERE user_sub = %s", (user.sub,))
                    if cur2.fetchone()[0] > 0:
                        plan_name = 'free_plus'
                        plan_limit = FREE_PLAN_LIMITS['free_plus']
                conn2.close()
            except Exception:
                pass

        return {
            'plan': {'name': plan_name, 'lookups_limit': plan_limit, 'period': 'monthly'},
            'has_subscription': has_sub,
            'usage': {
                'total_lookups_this_month': 0,
                'total_lookups_this_hour': 0,
                'lookups_remaining': plan_limit,
                'by_key': [],
            },
        }

    # Get plan from first key
    plan_name = user_keys[0][3]
    plan_limit = user_keys[0][4]
    has_sub = plan_name not in ('none', 'free', 'free_plus')

    # Get usage for all consumer IDs
    consumer_ids = [str(k[0]) for k in user_keys]
    monthly = get_monthly_detailed_usage(consumer_ids)
    hourly = get_hourly_detailed_usage(consumer_ids)

    by_key = []
    for k in user_keys:
        kid = str(k[0])
        by_key.append({
            'consumer_id': kid,
            'description': k[2] or 'API Key',
            'lookups_this_month': monthly['by_consumer'].get(kid, 0),
            'lookups_this_hour': hourly['by_consumer'].get(kid, 0),
            'created_at': k[5].isoformat() if k[5] else None,
        })

    total_monthly = sum(bk['lookups_this_month'] for bk in by_key)
    total_hourly = sum(bk['lookups_this_hour'] for bk in by_key)

    return {
        'plan': {'name': plan_name, 'lookups_limit': plan_limit, 'period': 'monthly'},
        'has_subscription': has_sub,
        'usage': {
            'total_lookups_this_month': total_monthly,
            'total_lookups_this_hour': total_hourly,
            'lookups_remaining': max(0, plan_limit - total_monthly),
            'by_key': by_key,
        },
    }
