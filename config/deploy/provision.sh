#!/usr/bin/env bash
# Provision a new dev-api droplet from scratch using only the git repo.
# The entire bootstrap runs via cloud-init user_data — no SSH needed until
# the final health check. This avoids SSH lockout issues completely.
#
# Usage: bash provision.sh --name NAME [--size SIZE] [--ref REF]
# Requires: DO_API_TOKEN, DO_VPC_UUID, DO_SSH_KEY_IDS env vars
set -euo pipefail

# --- Auto-load config ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/deploy.env"
if [ -f "$ENV_FILE" ]; then
    echo "==> Loading config from $ENV_FILE"
    set -a; source "$ENV_FILE"; set +a
fi

# Auto-detect DO API token from doctl if not set
if [ -z "${DO_API_TOKEN:-}" ]; then
    DOCTL_CONFIG="$HOME/Library/Application Support/doctl/config.yaml"
    if [ ! -f "$DOCTL_CONFIG" ]; then
        DOCTL_CONFIG="$HOME/.config/doctl/config.yaml"
    fi
    if [ -f "$DOCTL_CONFIG" ]; then
        DO_API_TOKEN=$(grep 'access-token' "$DOCTL_CONFIG" | head -1 | awk '{print $2}')
        echo "==> Using DO API token from doctl config"
    fi
fi

# --- Defaults (deploy.env values take precedence) ---
NAME=""
SIZE="${DO_SIZE:-s-1vcpu-1gb}"
REF="dev"
REGION="${DO_REGION:-nyc3}"
IMAGE="${DO_IMAGE:-ubuntu-24-04-x64}"
TAG="${DO_TAG:-canyougrab-api-dev}"
REPO_URL="git@github.com:ericismaking/canyougrab-api.git"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --name) NAME="$2"; shift 2;;
        --size) SIZE="$2"; shift 2;;
        --ref) REF="$2"; shift 2;;
        --region) REGION="$2"; shift 2;;
        *) echo "Unknown arg: $1"; exit 1;;
    esac
done

if [ -z "$NAME" ]; then
    echo "Usage: provision.sh --name <droplet-name> [--size s-1vcpu-1gb] [--ref dev]"
    exit 1
fi

: "${DO_API_TOKEN:?Set DO_API_TOKEN — or install doctl and authenticate}"
VPC_UUID="${DO_VPC_UUID:-}"
SSH_KEY_IDS="${DO_SSH_KEY_IDS:-}"

# Validate SSH key IDs are set
if [ -z "$SSH_KEY_IDS" ]; then
    echo "ERROR: DO_SSH_KEY_IDS is empty. Set it in deploy.env or as an env var."
    echo "  List keys: doctl compute ssh-key list"
    exit 1
fi

DEPLOY_KEY_FILE="$SCRIPT_DIR/../env/github-deploy-key"
if [ ! -f "$DEPLOY_KEY_FILE" ]; then
    echo "ERROR: GitHub deploy key not found at $DEPLOY_KEY_FILE"
    exit 1
fi
DEPLOY_KEY_B64=$(base64 < "$DEPLOY_KEY_FILE" | tr -d '\n')

do_api() {
    curl -sf -X "$1" "https://api.digitalocean.com/v2/$2" \
        -H "Authorization: Bearer $DO_API_TOKEN" \
        -H "Content-Type: application/json" \
        "${@:3}"
}

# --- Build cloud-init user_data ---
# This script runs as root on first boot via cloud-init. It does EVERYTHING:
# SSH hardening, VPC hosts, packages, deploy key, repo clone, virtualenv, deploy.
# No SSH connection is needed until the health check at the very end.
USER_DATA=$(cat <<CLOUD_INIT_EOF
#!/bin/bash
set -e
exec > /var/log/canyougrab-provision.log 2>&1
echo "=== canyougrab provision started at \$(date -u) ==="

# --- SSH hardening (prevent lockouts) ---
sed -i 's/^#\\?MaxStartups.*/MaxStartups 50:30:200/' /etc/ssh/sshd_config
grep -q '^MaxStartups' /etc/ssh/sshd_config || echo 'MaxStartups 50:30:200' >> /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# --- VPC internal hostnames ---
echo '10.108.0.5 unbound.canyougrab.internal' >> /etc/hosts
echo '10.108.0.8 rust-whois.canyougrab.internal' >> /etc/hosts

# --- System packages ---
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx git curl

# --- Node exporter ---
cd /tmp
curl -sLO https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz
tar xf node_exporter-1.8.2.linux-amd64.tar.gz
mv node_exporter-1.8.2.linux-amd64/node_exporter /usr/local/bin/
useradd -rs /bin/false node_exporter 2>/dev/null || true
cat > /etc/systemd/system/node_exporter.service <<'NODEEXP'
[Unit]
Description=Node Exporter
After=network.target
[Service]
User=node_exporter
ExecStart=/usr/local/bin/node_exporter
[Install]
WantedBy=multi-user.target
NODEEXP
systemctl daemon-reload
systemctl enable --now node_exporter

# --- GitHub deploy key ---
mkdir -p /root/.ssh
echo "$DEPLOY_KEY_B64" | base64 -d > /root/.ssh/canyougrab-deploy
chmod 600 /root/.ssh/canyougrab-deploy
cat > /root/.ssh/config <<'SSHCONF'
Host github.com
    IdentityFile /root/.ssh/canyougrab-deploy
    StrictHostKeyChecking no
SSHCONF

# --- Clone repo ---
git clone $REPO_URL /opt/canyougrab-repo
cd /opt/canyougrab-repo
git checkout $REF

# --- Create virtualenv and directories ---
python3 -m venv /opt/canyougrab/venv
mkdir -p /opt/canyougrab/{api,portal,scripts}

# --- Run deploy-app.sh ---
bash config/deploy/deploy-app.sh $REF

echo "=== canyougrab provision completed at \$(date -u) ==="

# --- Signal completion ---
touch /opt/canyougrab/.provision-complete
CLOUD_INIT_EOF
)

echo "==> Creating droplet: $NAME ($SIZE, $REGION, ref=$REF)"

# --- Build DO API payload ---
# Use python to build JSON properly (avoids shell escaping issues)
CREATE_JSON=$(python3 -c "
import json, sys
payload = {
    'name': '$NAME',
    'region': '$REGION',
    'size': '$SIZE',
    'image': '$IMAGE',
    'tags': ['$TAG'],
    'monitoring': True,
    'user_data': sys.stdin.read()
}
if '$VPC_UUID':
    payload['vpc_uuid'] = '$VPC_UUID'
if '$SSH_KEY_IDS':
    payload['ssh_keys'] = [int(k) for k in '$SSH_KEY_IDS'.split(',')]
print(json.dumps(payload))
" <<< "$USER_DATA")

RESPONSE=$(do_api POST "droplets" -d "$CREATE_JSON")
DROPLET_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['droplet']['id'])")
echo "==> Droplet created: ID=$DROPLET_ID"

# --- Wait for active ---
echo -n "==> Waiting for boot"
for i in $(seq 1 60); do
    sleep 5
    STATUS=$(do_api GET "droplets/$DROPLET_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['droplet']['status'])")
    echo -n "."
    if [ "$STATUS" = "active" ]; then
        echo " active!"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo " TIMEOUT"
        exit 1
    fi
done

# --- Get IPs ---
DROPLET_INFO=$(do_api GET "droplets/$DROPLET_ID")
PUBLIC_IP=$(echo "$DROPLET_INFO" | python3 -c "
import sys, json
d = json.load(sys.stdin)['droplet']
for net in d['networks']['v4']:
    if net['type'] == 'public':
        print(net['ip_address'])
        break
")
PRIVATE_IP=$(echo "$DROPLET_INFO" | python3 -c "
import sys, json
d = json.load(sys.stdin)['droplet']
for net in d['networks']['v4']:
    if net['type'] == 'private':
        print(net['ip_address'])
        break
" 2>/dev/null || echo "none")

echo "==> Public IP: $PUBLIC_IP, Private IP: $PRIVATE_IP"

# --- Wait for cloud-init provisioning to complete (no SSH needed) ---
# Poll via SSH for the sentinel file that cloud-init creates when done.
# Cloud-init does ALL the work — we just wait and check.
echo "==> Waiting for cloud-init provisioning to complete..."
echo "    (full bootstrap runs via user_data — no SSH needed during setup)"
echo "    Tail the log: ssh root@$PUBLIC_IP tail -f /var/log/canyougrab-provision.log"

PROVISION_DONE=false
for i in $(seq 1 90); do
    sleep 10
    # Single quick SSH check for the sentinel file
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o ServerAliveInterval=10 \
           -i "$SSH_KEY" root@"$PUBLIC_IP" \
           "test -f /opt/canyougrab/.provision-complete" 2>/dev/null; then
        PROVISION_DONE=true
        break
    fi
    # Show progress every 60 seconds
    if [ $((i % 6)) -eq 0 ]; then
        echo "    ... still provisioning (${i}0s elapsed)"
    fi
done

if [ "$PROVISION_DONE" = false ]; then
    echo "==> WARNING: Provisioning did not complete within 15 minutes"
    echo "    SSH in and check: tail /var/log/canyougrab-provision.log"
    echo "    Droplet ID: $DROPLET_ID, IP: $PUBLIC_IP"
    exit 1
fi

echo "==> Provisioning complete!"

# --- Final health check ---
HEALTHY=false
for i in $(seq 1 6); do
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -i "$SSH_KEY" root@"$PUBLIC_IP" \
           "curl -sf http://127.0.0.1:8000/health" 2>/dev/null; then
        echo ""
        HEALTHY=true
        break
    fi
    sleep 5
done

echo ""
echo "============================================"
echo "Droplet provisioned!"
echo "  Name:       $NAME"
echo "  ID:         $DROPLET_ID"
echo "  Public IP:  $PUBLIC_IP"
echo "  Private IP: $PRIVATE_IP"
echo "  Ref:        $REF"
echo "  Healthy:    $HEALTHY"
echo ""
echo "Next steps:"
echo "  1. Add to Prometheus: $PRIVATE_IP:9100"
echo "  2. Switch traffic: bash config/deploy/switch-traffic.sh $PUBLIC_IP"
echo "============================================"
