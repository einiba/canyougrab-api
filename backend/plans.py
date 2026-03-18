"""Plan configuration loaded from the plans table."""

import logging
import time

from queries import get_db_conn

logger = logging.getLogger(__name__)

_plans_cache = None
_cache_time = 0
CACHE_TTL = 300  # 5 minutes


def _load_plans() -> dict:
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT name, display_name, price_cents, monthly_limit,
                       minute_limit, domain_cap, requires_card, stripe_price_id, sort_order
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
        }
    return plans


def get_plans() -> dict:
    """Get all plans as a dict keyed by name. Cached for CACHE_TTL seconds."""
    global _plans_cache, _cache_time
    now = time.time()
    if _plans_cache is None or (now - _cache_time) > CACHE_TTL:
        _plans_cache = _load_plans()
        _cache_time = now
    return _plans_cache


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
