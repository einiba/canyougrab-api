"""
Per-user starred domains — the persistent shortlist surface that backs the
★ button across the marketing site and the portal /starred page.

Anonymous stars live in browser localStorage; this module never sees them
until they're POSTed to /api/portal/names/star/claim after signup.
"""

import logging
from typing import Optional

from queries import get_db_conn

logger = logging.getLogger(__name__)


def list_stars(user_sub: str) -> list[dict]:
    """Return all starred domains for the user, newest first."""
    if not user_sub:
        return []
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id, domain, base, tld, available_at_star, '
                '       source_list_id, notes, starred_at '
                'FROM starred_domains '
                'WHERE user_sub = %s '
                'ORDER BY starred_at DESC',
                (user_sub,),
            )
            return [
                {
                    'id': str(r[0]),
                    'domain': r[1],
                    'base': r[2],
                    'tld': r[3],
                    'available_at_star': r[4],
                    'source_list_id': str(r[5]) if r[5] else None,
                    'notes': r[6],
                    'starred_at': r[7].isoformat() if r[7] else None,
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def is_starred(user_sub: str, domain: str) -> bool:
    if not user_sub or not domain:
        return False
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM starred_domains WHERE user_sub = %s AND domain = %s LIMIT 1',
                (user_sub, domain),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def toggle_star(
    user_sub: str,
    domain: str,
    base: Optional[str] = None,
    tld: Optional[str] = None,
    available: Optional[bool] = None,
    source_list_id: Optional[str] = None,
) -> dict:
    """Idempotent toggle keyed by (user_sub, domain). Returns
    {starred: bool, total: int}.
    """
    if not user_sub or not domain:
        return {'starred': False, 'total': 0}
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM starred_domains WHERE user_sub = %s AND domain = %s '
                'RETURNING id',
                (user_sub, domain),
            )
            removed = cur.fetchone() is not None
            if not removed:
                cur.execute(
                    'INSERT INTO starred_domains '
                    '(user_sub, domain, base, tld, available_at_star, source_list_id) '
                    'VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (user_sub, domain) DO NOTHING',
                    (user_sub, domain, base, tld, available, source_list_id),
                )
            cur.execute(
                'SELECT COUNT(*) FROM starred_domains WHERE user_sub = %s',
                (user_sub,),
            )
            total = cur.fetchone()[0] or 0
            conn.commit()
            return {'starred': not removed, 'total': total}
    except Exception as e:
        conn.rollback()
        logger.warning('toggle_star failed for %s/%s: %s', user_sub[:12], domain, e)
        raise
    finally:
        conn.close()


def claim_anon_stars(user_sub: str, items: list[dict]) -> int:
    """Bulk-insert anon stars (carried over from localStorage on signup).
    Each item: {domain, base?, tld?, available_at_star?, source_list_id?}.
    Idempotent — relies on the (user_sub, domain) UNIQUE constraint to skip
    duplicates. Returns the number of new rows inserted.
    """
    if not user_sub or not items:
        return 0
    inserted = 0
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            for item in items:
                domain = (item.get('domain') or '').strip().lower()
                if not domain or len(domain) > 255:
                    continue
                cur.execute(
                    'INSERT INTO starred_domains '
                    '(user_sub, domain, base, tld, available_at_star, source_list_id) '
                    'VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (user_sub, domain) DO NOTHING',
                    (
                        user_sub,
                        domain,
                        item.get('base'),
                        item.get('tld'),
                        item.get('available'),
                        item.get('source_list_id'),
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            conn.commit()
            return inserted
    except Exception as e:
        conn.rollback()
        logger.warning('claim_anon_stars failed for %s: %s', user_sub[:12], e)
        return 0
    finally:
        conn.close()
