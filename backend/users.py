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
