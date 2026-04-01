#!/usr/bin/env python3
"""
Provision a test account for marketplace reviewers.

Creates a dedicated API key and seeds lookup history so reviewers can see
realistic usage data. The account has no MFA and generous quota.

Usage:
    # Against production DB (from API server)
    python3 scripts/create_test_account.py

    # Against a specific DB
    DATABASE_URL=postgresql://... python3 scripts/create_test_account.py

    # Custom plan
    python3 scripts/create_test_account.py --plan starter

Output:
    Prints the raw API key and account details. Store securely —
    the raw key is only shown once.
"""

import argparse
import hashlib
import secrets
import sys
import os
from datetime import datetime, timezone

# Add backend to path so we can reuse existing modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from queries import get_db_conn


TEST_USER_SUB = 'auth0|marketplace-reviewer'
TEST_EMAIL = 'reviewer@canyougrab.it'
TEST_DESCRIPTION = 'Marketplace Reviewer — Test Account'
KEY_PREFIX_LEN = 12


def generate_key():
    raw = 'cyg_' + secrets.token_urlsafe(40)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:KEY_PREFIX_LEN]
    return raw, key_hash, prefix


def seed_lookup_history(conn, consumer_id: str, count: int = 150):
    """Insert synthetic lookup records so the reviewer sees realistic usage."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # Record monthly usage
        month_key = now.strftime('%Y-%m')
        cur.execute(
            """
            INSERT INTO usage_monthly (consumer_id, month, lookups)
            VALUES (%s, %s, %s)
            ON CONFLICT (consumer_id, month)
            DO UPDATE SET lookups = usage_monthly.lookups + EXCLUDED.lookups
            """,
            (consumer_id, month_key, count),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description='Create a marketplace reviewer test account')
    parser.add_argument('--plan', default='starter', help='Plan to assign (default: starter)')
    parser.add_argument('--seed-lookups', type=int, default=150, help='Number of fake lookups to seed (default: 150)')
    args = parser.parse_args()

    conn = get_db_conn()
    raw_key, key_hash, prefix = generate_key()

    with conn.cursor() as cur:
        # Delete any existing reviewer keys
        cur.execute(
            "DELETE FROM api_keys WHERE user_sub = %s",
            (TEST_USER_SUB,),
        )

        # Create the test key
        cur.execute(
            """
            INSERT INTO api_keys (user_sub, email, description, key_hash, key_prefix, plan)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (TEST_USER_SUB, TEST_EMAIL, TEST_DESCRIPTION, key_hash, prefix, args.plan),
        )
        key_id = cur.fetchone()[0]

    conn.commit()

    # Seed some lookup history
    if args.seed_lookups > 0:
        try:
            seed_lookup_history(conn, TEST_USER_SUB, args.seed_lookups)
        except Exception as e:
            print(f'Warning: Could not seed lookup history: {e}', file=sys.stderr)

    print()
    print('=== Marketplace Reviewer Test Account ===')
    print()
    print(f'  API Key:      {raw_key}')
    print(f'  Key Prefix:   {prefix}')
    print(f'  Key ID:       {key_id}')
    print(f'  Plan:         {args.plan}')
    print(f'  Email:        {TEST_EMAIL}')
    print(f'  User Sub:     {TEST_USER_SUB}')
    print(f'  Seeded Usage: {args.seed_lookups} lookups')
    print()
    print('Store this API key securely — it is only shown once.')
    print('Include it in marketplace submission forms as the test credential.')
    print()


if __name__ == '__main__':
    main()
