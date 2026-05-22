"""
User management: upsert and lookup for the canonical users table.
"""

import logging
from typing import Optional

from email_utils import normalize_email
from queries import get_db_conn
from slack import notify_new_user

logger = logging.getLogger(__name__)


def upsert_user(
    auth0_sub: str,
    email: str = '',
    name: str = '',
    picture_url: str = '',
    email_verified: bool = False,
    auth_provider: str = '',
    visitor_id: str = '',
) -> Optional[dict]:
    """Insert or update a user record.  Returns the user row as a dict.

    Called on every authenticated portal request so the record stays current.
    Uses ON CONFLICT to atomically create-or-update.
    """
    if not auth0_sub:
        return None

    email_norm = normalize_email(email) if email else ''

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (auth0_sub, email, email_normalized, email_verified,
                                   name, picture_url, auth_provider, last_login_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (auth0_sub) DO UPDATE SET
                    email           = CASE WHEN EXCLUDED.email != '' THEN EXCLUDED.email ELSE users.email END,
                    email_normalized= CASE WHEN EXCLUDED.email_normalized != '' THEN EXCLUDED.email_normalized ELSE users.email_normalized END,
                    email_verified  = EXCLUDED.email_verified,
                    name            = CASE WHEN EXCLUDED.name != '' THEN EXCLUDED.name ELSE users.name END,
                    picture_url     = CASE WHEN EXCLUDED.picture_url != '' THEN EXCLUDED.picture_url ELSE users.picture_url END,
                    auth_provider   = CASE WHEN EXCLUDED.auth_provider != '' THEN EXCLUDED.auth_provider ELSE users.auth_provider END,
                    updated_at      = NOW(),
                    last_login_at   = NOW()
                RETURNING id, auth0_sub, email, email_normalized, email_verified,
                          name, picture_url, auth_provider, created_at, updated_at, last_login_at,
                          tos_accepted_at, tos_version
            """, (auth0_sub, email, email_norm, email_verified, name, picture_url, auth_provider))
            row = cur.fetchone()
            conn.commit()

        if not row:
            return None

        # New user: created_at == updated_at (ON CONFLICT UPDATE didn't fire)
        is_new = row[8] is not None and row[9] is not None and row[8] == row[9]
        if is_new:
            notify_new_user(email=row[2], name=row[5], auth_provider=row[7])
            if visitor_id:
                try:
                    from name_gen import claim_anon_lists
                    claim_anon_lists(visitor_id, auth0_sub)
                except Exception as e:
                    logger.warning('Failed to claim anon name lists for new user %s: %s', auth0_sub, e)

        return {
            'id': str(row[0]),
            'auth0_sub': row[1],
            'email': row[2],
            'email_normalized': row[3],
            'email_verified': row[4],
            'name': row[5],
            'picture_url': row[6],
            'auth_provider': row[7],
            'created_at': row[8].isoformat() if row[8] else None,
            'updated_at': row[9].isoformat() if row[9] else None,
            'last_login_at': row[10].isoformat() if row[10] else None,
            'tos_accepted_at': row[11].isoformat() if row[11] else None,
            'tos_version': row[12],
        }
    except Exception as e:
        logger.error('Failed to upsert user %s: %s', auth0_sub, e)
        conn.rollback()
        return None
    finally:
        conn.close()


def get_user(auth0_sub: str) -> Optional[dict]:
    """Look up a user by Auth0 sub.  Returns dict or None."""
    if not auth0_sub:
        return None

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, auth0_sub, email, email_normalized, email_verified,
                       name, picture_url, auth_provider, created_at, updated_at, last_login_at,
                       tos_accepted_at, tos_version
                FROM users WHERE auth0_sub = %s
            """, (auth0_sub,))
            row = cur.fetchone()

        if not row:
            return None

        return {
            'id': str(row[0]),
            'auth0_sub': row[1],
            'email': row[2],
            'email_normalized': row[3],
            'email_verified': row[4],
            'name': row[5],
            'picture_url': row[6],
            'auth_provider': row[7],
            'created_at': row[8].isoformat() if row[8] else None,
            'updated_at': row[9].isoformat() if row[9] else None,
            'last_login_at': row[10].isoformat() if row[10] else None,
            'tos_accepted_at': row[11].isoformat() if row[11] else None,
            'tos_version': row[12],
        }
    finally:
        conn.close()


def get_user_email(auth0_sub: str) -> str:
    """Quick lookup for just the email.  Returns '' if not found."""
    user = get_user(auth0_sub)
    return user['email'] if user else ''


def get_marketing_preference(auth0_sub: str) -> dict:
    """Return the marketing-email preference for the given user.

    Always returns a dict so callers can render a stable shape; defaults to
    opt_in=False if the user row does not exist yet (caller will usually
    upsert before reaching this).
    """
    if not auth0_sub:
        return {'opt_in': False, 'opt_in_at': None, 'unsubscribed_at': None, 'source': None}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT marketing_opt_in,
                       marketing_opt_in_at,
                       marketing_opt_in_source,
                       marketing_unsubscribed_at
                FROM users WHERE auth0_sub = %s
            """, (auth0_sub,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return {'opt_in': False, 'opt_in_at': None, 'unsubscribed_at': None, 'source': None}

    return {
        'opt_in': bool(row[0]) and row[3] is None,
        'opt_in_at': row[1].isoformat() if row[1] else None,
        'source': row[2],
        'unsubscribed_at': row[3].isoformat() if row[3] else None,
    }


def set_marketing_preference(auth0_sub: str, opt_in: bool, source: str = '') -> dict:
    """Record a marketing opt-in or opt-out for the user.

    opt_in=True   → set marketing_opt_in=true, stamp marketing_opt_in_at=NOW(),
                    clear marketing_unsubscribed_at.
    opt_in=False  → set marketing_opt_in=false, stamp marketing_unsubscribed_at=NOW()
                    (preserving the prior marketing_opt_in_at for audit).

    Returns the resulting preference dict, same shape as get_marketing_preference.
    No-op when auth0_sub is empty; returns the empty default in that case.
    """
    if not auth0_sub:
        return {'opt_in': False, 'opt_in_at': None, 'unsubscribed_at': None, 'source': None}

    safe_source = (source or '')[:64]

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if opt_in:
                cur.execute("""
                    UPDATE users
                       SET marketing_opt_in           = true,
                           marketing_opt_in_at        = NOW(),
                           marketing_opt_in_source    = %s,
                           marketing_unsubscribed_at  = NULL,
                           updated_at                 = NOW()
                     WHERE auth0_sub = %s
                    RETURNING marketing_opt_in,
                              marketing_opt_in_at,
                              marketing_opt_in_source,
                              marketing_unsubscribed_at
                """, (safe_source, auth0_sub))
            else:
                cur.execute("""
                    UPDATE users
                       SET marketing_opt_in           = false,
                           marketing_unsubscribed_at  = NOW(),
                           marketing_opt_in_source    = COALESCE(NULLIF(%s, ''), marketing_opt_in_source),
                           updated_at                 = NOW()
                     WHERE auth0_sub = %s
                    RETURNING marketing_opt_in,
                              marketing_opt_in_at,
                              marketing_opt_in_source,
                              marketing_unsubscribed_at
                """, (safe_source, auth0_sub))
            row = cur.fetchone()
            conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error('Failed to set marketing preference for %s: %s', auth0_sub[:20], e)
        raise
    finally:
        conn.close()

    if not row:
        return {'opt_in': False, 'opt_in_at': None, 'unsubscribed_at': None, 'source': None}

    return {
        'opt_in': bool(row[0]) and row[3] is None,
        'opt_in_at': row[1].isoformat() if row[1] else None,
        'source': row[2],
        'unsubscribed_at': row[3].isoformat() if row[3] else None,
    }


def merge_user_data(primary_sub: str, secondary_sub: str) -> bool:
    """Reassign all data from secondary_sub to primary_sub after Auth0 account linking.

    When Auth0 links two accounts, the secondary ceases to exist. This function
    migrates orphaned api_keys and removes the secondary users row.
    Returns True if any rows were affected.
    """
    if not primary_sub or not secondary_sub or primary_sub == secondary_sub:
        return False

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Reassign API keys from secondary to primary
            cur.execute(
                "UPDATE api_keys SET user_sub = %s WHERE user_sub = %s",
                (primary_sub, secondary_sub),
            )
            keys_moved = cur.rowcount

            # Delete the orphaned secondary user row
            cur.execute(
                "DELETE FROM users WHERE auth0_sub = %s",
                (secondary_sub,),
            )
            user_deleted = cur.rowcount

            conn.commit()

        if keys_moved or user_deleted:
            logger.info(
                'Merged user data: %s → %s (keys=%d, user_deleted=%d)',
                secondary_sub, primary_sub, keys_moved, user_deleted,
            )
            return True
        return False
    except Exception as e:
        logger.error('Failed to merge user data %s → %s: %s', secondary_sub, primary_sub, e)
        conn.rollback()
        return False
    finally:
        conn.close()
