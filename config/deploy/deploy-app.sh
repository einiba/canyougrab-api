#!/usr/bin/env bash
# Deploy canyougrab-api to the current host.
# Runs ON the server (via SSH or locally).
# Usage: bash deploy-app.sh [REF]
#   REF defaults to 'dev' branch. Can be a tag (v9.7.0) or branch.
set -euo pipefail

REF="${1:-dev}"
REPO_DIR="${REPO_DIR:-/opt/canyougrab-repo}"
API_DIR="/opt/canyougrab/api"
PORTAL_DIR="/opt/canyougrab/portal"
SCRIPTS_DIR="/opt/canyougrab/scripts"
VENV_DIR="/opt/canyougrab/venv"

echo "==> Deploying ref: $REF"

# --- Pull latest code ---
cd "$REPO_DIR"
git fetch origin
git checkout "$REF"
git pull origin "$REF" 2>/dev/null || true
echo "==> Code: $(git log --oneline -1)"

# --- Sync env files (split combined env into separate files for systemd) ---
ENV_SRC="$REPO_DIR/config/env/dev-api.env"
if [ -f "$ENV_SRC" ]; then
    grep '^POSTGRES_' "$ENV_SRC" > /opt/canyougrab/database.env
    grep -E '^(VALKEY_|WHOIS_)' "$ENV_SRC" > /opt/canyougrab/valkey.env
    grep -E '^(STRIPE_|AUTH0_|PORTAL_)' "$ENV_SRC" > /opt/canyougrab/stripe.env
    echo "==> Env files synced"
fi

# --- Rsync application code ---
rsync -a --delete --exclude="__pycache__" "$REPO_DIR/backend/" "$API_DIR/"
rsync -a --delete "$REPO_DIR/portal/dist/" "$PORTAL_DIR/" 2>/dev/null || echo "  (no portal dist, skipping)"
mkdir -p "$SCRIPTS_DIR"
rsync -a "$REPO_DIR/scripts/" "$SCRIPTS_DIR/"
echo "==> Code synced"

# --- Install Python dependencies ---
"$VENV_DIR/bin/pip" install -q -r "$API_DIR/requirements.txt"
echo "==> Dependencies installed"

# --- Sync systemd units ---
cp "$REPO_DIR/config/systemd/"*.service /etc/systemd/system/ 2>/dev/null || true
cp "$REPO_DIR/config/systemd/"*.timer /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload
echo "==> Systemd units synced"

# --- Sync SSL certs ---
if [ -f "$REPO_DIR/config/env/cloudflare-origin-cert.pem" ]; then
    cp "$REPO_DIR/config/env/cloudflare-origin-cert.pem" /etc/ssl/cloudflare-origin-cert.pem
    cp "$REPO_DIR/config/env/cloudflare-origin-key.pem" /etc/ssl/cloudflare-origin-key.pem
    chmod 600 /etc/ssl/cloudflare-origin-key.pem
    echo "==> SSL certs synced"
fi

# --- Sync nginx config ---
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
cp "$REPO_DIR/config/nginx/"*.conf /etc/nginx/sites-enabled/ 2>/dev/null || true
nginx -t 2>/dev/null && systemctl reload nginx || echo "  (nginx config test failed, not reloaded)"
echo "==> Nginx synced"

# --- Enable and restart services ---
systemctl enable canyougrab-api canyougrab-worker@1 canyougrab-worker@2 canyougrab-worker@3 canyougrab-watchdog.timer 2>/dev/null || true
systemctl restart canyougrab-api
systemctl restart canyougrab-worker@1 canyougrab-worker@2 canyougrab-worker@3
systemctl restart canyougrab-watchdog.timer

# --- Verify ---
sleep 5
if curl -sf http://127.0.0.1:8000/health > /dev/null; then
    echo "==> Health check passed"
else
    echo "==> WARNING: Health check failed!"
    systemctl status canyougrab-api --no-pager | head -10
    exit 1
fi

echo "==> Deploy complete: $(git log --oneline -1)"
