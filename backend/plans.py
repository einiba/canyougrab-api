"""Plan configuration loaded from the plans table."""

import logging
import os
import time
from datetime import datetime, timezone

from queries import get_db_conn

logger = logging.getLogger(__name__)

def _is_stripe_test_mode() -> bool:
    """Detect Stripe mode from the secret key prefix."""
    key = os.environ.get('STRIPE_SECRET_KEY', '')
    return key.startswith('sk_test_')

_plans_cache = None
_cache_time = 0
CACHE_TTL = 300  # 5 minutes


def _load_plans() -> dict:
    price_col = 'stripe_price_id_test' if _is_stripe_test_mode() else 'stripe_price_id'
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT name, display_name, price_cents, monthly_limit,
                       minute_limit, domain_cap, requires_card, {price_col}, sort_order,
                       published_at, retired_at
                FROM plans ORDER BY sort_order
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    plans = {}
    for r in rows:
        plans[r[0]] = {
            'name': r[0],
            'display_name': r[1],
            'price_cents': r[2],
            'monthly_limit': r[3],
            'minute_limit': r[4],
            'domain_cap': r[5],
            'requires_card': r[6],
            'stripe_price_id': r[7],
            'sort_order': r[8],
            'published_at': r[9].isoformat() if r[9] else None,
            'retired_at': r[10].isoformat() if r[10] else None,
        }
    logger.info('Loaded %d plans using %s column', len(plans), price_col)
    return plans


def get_plans() -> dict:
    """Get all plans as a dict keyed by name. Cached for CACHE_TTL seconds."""
    global _plans_cache, _cache_time
    now = time.time()
    if _plans_cache is None or (now - _cache_time) > CACHE_TTL:
        _plans_cache = _load_plans()
        _cache_time = now
    return _plans_cache


def _is_published(plan: dict) -> bool:
    """Check if a plan is currently published and not retired."""
    now = datetime.now(timezone.utc)
    pub = plan.get('published_at')
    ret = plan.get('retired_at')
    if not pub:
        return False
    pub_dt = datetime.fromisoformat(pub) if isinstance(pub, str) else pub
    if pub_dt > now:
        return False
    if ret:
        ret_dt = datetime.fromisoformat(ret) if isinstance(ret, str) else ret
        if ret_dt <= now:
            return False
    return True


def get_published_plans() -> list[dict]:
    """Get plans that are currently published and not retired, sorted by sort_order."""
    return [p for p in sorted(get_plans().values(), key=lambda x: x['sort_order']) if _is_published(p)]


def get_plan(name: str) -> dict:
    """Get a single plan by name. Falls back to 'free' plan."""
    plans = get_plans()
    return plans.get(name, plans.get('free', {}))


def get_plan_by_stripe_price(price_id: str) -> dict | None:
    """Look up a plan by its Stripe price ID."""
    for plan in get_plans().values():
        if plan.get('stripe_price_id') == price_id:
            return plan
    return None
