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

# --- Detect environment (dev or prod) ---
# Set via CANYOUGRAB_ENV env var, or auto-detect from hostname/ref
CANYOUGRAB_ENV="${CANYOUGRAB_ENV:-}"
if [ -z "$CANYOUGRAB_ENV" ]; then
    HOSTNAME_STR=$(hostname)
    if echo "$HOSTNAME_STR" | grep -qi 'dev'; then
        CANYOUGRAB_ENV="dev"
    elif echo "$REF" | grep -qi 'dev'; then
        CANYOUGRAB_ENV="dev"
    else
        CANYOUGRAB_ENV="prod"
    fi
fi
echo "==> Environment: $CANYOUGRAB_ENV"

# --- Sync env files (split combined env into separate files for systemd) ---
ENV_SRC="$REPO_DIR/config/env/${CANYOUGRAB_ENV}-api.env"
if [ ! -f "$ENV_SRC" ]; then
    echo "WARNING: $ENV_SRC not found, falling back to dev-api.env"
    ENV_SRC="$REPO_DIR/config/env/dev-api.env"
fi
if [ -f "$ENV_SRC" ]; then
    grep '^POSTGRES_' "$ENV_SRC" > /opt/canyougrab/database.env
    grep -E '^(VALKEY_|WHOIS_|DNS_RESOLVER)' "$ENV_SRC" > /opt/canyougrab/valkey.env
    grep -E '^(STRIPE_|AUTH0_|PORTAL_|BATCH_)' "$ENV_SRC" > /opt/canyougrab/stripe.env
    echo "==> Env files synced from $ENV_SRC"
fi

# --- Install only the right nginx config for this environment ---
rm -f /etc/nginx/sites-enabled/dev-api.conf /etc/nginx/sites-enabled/prod-api.conf 2>/dev/null || true

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
# If cloud-init (Pulumi) already wrote api.conf with LE cert paths, don't
# overwrite with repo configs that use different SSL paths.
if [ -f /etc/nginx/sites-enabled/api.conf ]; then
    echo "==> Nginx: Pulumi-managed configs detected, skipping repo sync"
else
    # Standalone deploy (no Pulumi) — use repo configs
    for f in "$REPO_DIR/config/nginx/${CANYOUGRAB_ENV}-"*.conf; do
        [ -f "$f" ] && cp "$f" /etc/nginx/sites-enabled/
    done
    for f in "$REPO_DIR/config/nginx/"*.conf; do
        basename="$(basename "$f")"
        case "$basename" in dev-*|prod-*) continue;; esac
        cp "$f" /etc/nginx/sites-enabled/
    done
    nginx -t 2>/dev/null && systemctl reload nginx || echo "  (nginx config test failed, not reloaded)"
    echo "==> Nginx synced ($CANYOUGRAB_ENV)"
fi

# --- Enable and restart services ---
systemctl enable canyougrab-api canyougrab-worker@1 canyougrab-worker@2 canyougrab-worker@3 canyougrab-watchdog.timer 2>/dev/null || true
systemctl restart canyougrab-api
systemctl restart canyougrab-worker@1 canyougrab-worker@2 canyougrab-worker@3
systemctl restart canyougrab-watchdog.timer

# --- Verify (retry up to 30s — uvicorn needs time to fork workers) ---
HEALTH_OK=false
for i in $(seq 1 6); do
    sleep 5
    if curl -sf http://127.0.0.1:8000/health > /dev/null; then
        HEALTH_OK=true
        break
    fi
done

if [ "$HEALTH_OK" = true ]; then
    echo "==> Health check passed"
else
    echo "==> WARNING: Health check failed after 30s (services may still be starting)"
    systemctl status canyougrab-api --no-pager | head -10
    # Don't exit 1 — let cloud-init continue so sentinel file gets created
fi

echo "==> Deploy complete: $(git log --oneline -1)"
