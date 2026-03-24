#!/usr/bin/env python3
"""
Auth0 Team Manager — Manage DigitalOcean team access via Auth0

Programmatically add/remove/list users in your Auth0 tenant.
Users added here can SSO into DigitalOcean once SSO is configured.

Setup:
  1. Go to Auth0 Dashboard → Applications → APIs → Auth0 Management API
  2. Create a Machine-to-Machine application with these scopes:
     - read:users, create:users, update:users, delete:users
     - read:roles, create:role_members
  3. Set the env vars below (or create a .env file in this directory)

Usage:
  python3 auth0-team-manager.py list
  python3 auth0-team-manager.py add --email alice@example.com --name "Alice Smith" --role admin
  python3 auth0-team-manager.py remove --email alice@example.com
  python3 auth0-team-manager.py create-role --name "DO Admin" --description "DigitalOcean administrator"

Environment variables:
  AUTH0_DOMAIN        Your Auth0 tenant domain (e.g. myapp.us.auth0.com)
  AUTH0_CLIENT_ID     M2M application client ID
  AUTH0_CLIENT_SECRET M2M application client secret
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "")

# Try loading from a .env file next to this script if env vars aren't set
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_file) and not AUTH0_DOMAIN:
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")
    AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
    AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "")
    AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "")


def _check_config():
    missing = []
    if not AUTH0_DOMAIN:
        missing.append("AUTH0_DOMAIN")
    if not AUTH0_CLIENT_ID:
        missing.append("AUTH0_CLIENT_ID")
    if not AUTH0_CLIENT_SECRET:
        missing.append("AUTH0_CLIENT_SECRET")
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print(f"   Set them or create {_env_file}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Auth0 API helpers (stdlib only — no extra dependencies)
# ---------------------------------------------------------------------------

_token_cache: str | None = None


def _get_management_token() -> str:
    """Get an Auth0 Management API access token using client credentials."""
    global _token_cache
    if _token_cache:
        return _token_cache

    payload = json.dumps({
        "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
        "grant_type": "client_credentials",
    }).encode()

    req = Request(
        f"https://{AUTH0_DOMAIN}/oauth/token",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req) as resp:
            data = json.loads(resp.read())
            _token_cache = data["access_token"]
            return _token_cache
    except HTTPError as e:
        body = e.read().decode()
        print(f"❌ Failed to get management token: {e.code} {body}")
        sys.exit(1)


def _api(method: str, path: str, body: dict | None = None) -> dict | list | None:
    """Make an authenticated request to the Auth0 Management API."""
    token = _get_management_token()
    url = f"https://{AUTH0_DOMAIN}/api/v2{path}"

    data = json.dumps(body).encode() if body else None
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )

    try:
        with urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except HTTPError as e:
        body_text = e.read().decode()
        if e.code == 404:
            return None
        print(f"❌ API error {method} {path}: {e.code}")
        try:
            err = json.loads(body_text)
            print(f"   {err.get('message', body_text)}")
        except json.JSONDecodeError:
            print(f"   {body_text}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args):
    """List all users in the Auth0 tenant."""
    users = _api("GET", "/users?per_page=100&include_totals=true")

    total = users.get("total", 0) if isinstance(users, dict) else 0
    user_list = users.get("users", []) if isinstance(users, dict) else users

    print(f"\n👥 Users in Auth0 tenant ({total} total)\n")
    print(f"  {'Email':<35} {'Name':<25} {'Created':<12} {'Logins'}")
    print(f"  {'─' * 35} {'─' * 25} {'─' * 12} {'─' * 7}")

    for u in user_list:
        email = u.get("email", "—")
        name = u.get("name", "—")
        created = u.get("created_at", "—")[:10]
        logins = u.get("logins_count", 0)
        print(f"  {email:<35} {name:<25} {created:<12} {logins}")

    print()


def cmd_add(args):
    """Create a new user and optionally assign a role."""
    email = args.email
    name = args.name or email.split("@")[0]

    # Check if user already exists
    existing = _api("GET", f"/users-by-email?email={email}")
    if existing:
        print(f"⚠️  User {email} already exists (user_id: {existing[0]['user_id']})")
        sys.exit(1)

    # Create the user
    import secrets
    import string
    temp_password = "".join(
        secrets.choice(string.ascii_letters + string.digits + "!@#$%")
        for _ in range(20)
    )

    user = _api("POST", "/users", {
        "email": email,
        "name": name,
        "password": temp_password,
        "connection": "Username-Password-Authentication",
        "verify_email": True,
    })

    user_id = user["user_id"]
    print(f"✅ Created user: {email} (id: {user_id})")
    print(f"   Temp password: {temp_password}")
    print(f"   ℹ️  A verification email will be sent to {email}")

    # Assign role if specified
    if args.role:
        _assign_role(user_id, args.role)


def cmd_remove(args):
    """Delete a user by email."""
    email = args.email

    existing = _api("GET", f"/users-by-email?email={email}")
    if not existing:
        print(f"❌ User {email} not found")
        sys.exit(1)

    user_id = existing[0]["user_id"]

    if not args.force:
        confirm = input(f"⚠️  Delete user {email} ({user_id})? [y/N] ")
        if confirm.lower() != "y":
            print("Cancelled.")
            return

    _api("DELETE", f"/users/{user_id}")
    print(f"✅ Deleted user: {email}")


def cmd_create_role(args):
    """Create a new role."""
    role = _api("POST", "/roles", {
        "name": args.name,
        "description": args.description or f"Role: {args.name}",
    })
    print(f"✅ Created role: {role['name']} (id: {role['id']})")


def cmd_list_roles(args):
    """List all roles."""
    roles = _api("GET", "/roles?per_page=100")

    print(f"\n🔑 Roles\n")
    print(f"  {'Name':<25} {'Description':<40} {'ID'}")
    print(f"  {'─' * 25} {'─' * 40} {'─' * 30}")

    for r in roles:
        print(f"  {r['name']:<25} {r.get('description', '—'):<40} {r['id']}")

    print()


def _assign_role(user_id: str, role_name: str):
    """Find a role by name and assign it to a user."""
    roles = _api("GET", f"/roles?name_filter={role_name}")
    if not roles:
        print(f"⚠️  Role '{role_name}' not found. Create it first with: create-role --name \"{role_name}\"")
        return

    role_id = roles[0]["id"]
    _api("POST", f"/users/{user_id}/roles", {"roles": [role_id]})
    print(f"✅ Assigned role '{role_name}' to user")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Manage Auth0 users for DigitalOcean SSO team access",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="List all users")

    # add
    add_p = sub.add_parser("add", help="Add a new user")
    add_p.add_argument("--email", required=True, help="User's email address")
    add_p.add_argument("--name", help="User's full name (defaults to email prefix)")
    add_p.add_argument("--role", help="Role name to assign (must exist already)")

    # remove
    rm_p = sub.add_parser("remove", help="Remove a user")
    rm_p.add_argument("--email", required=True, help="User's email address")
    rm_p.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # create-role
    cr_p = sub.add_parser("create-role", help="Create a new role")
    cr_p.add_argument("--name", required=True, help="Role name")
    cr_p.add_argument("--description", help="Role description")

    # list-roles
    sub.add_parser("list-roles", help="List all roles")

    args = parser.parse_args()
    _check_config()

    commands = {
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "create-role": cmd_create_role,
        "list-roles": cmd_list_roles,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
