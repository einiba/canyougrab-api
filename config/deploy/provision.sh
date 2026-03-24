#!/usr/bin/env bash
# Provision a new dev-api droplet from scratch using only the git repo.
# Usage: bash provision.sh --name NAME [--size SIZE] [--ref REF]
#
# Requires: DO_API_TOKEN env var, SSH key accessible
# All secrets come from the git repo (config/env/dev-api.env).
set -euo pipefail

# --- Defaults ---
NAME=""
SIZE="s-1vcpu-1gb"
REF="dev"
REGION="nyc3"
IMAGE="ubuntu-24-04-x64"
VPC_UUID=""  # set via DO_VPC_UUID env var
SSH_KEY_IDS="" # comma-separated, set via DO_SSH_KEY_IDS env var
TAG="canyougrab-api-dev"
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

: "${DO_API_TOKEN:?Set DO_API_TOKEN env var}"
VPC_UUID="${DO_VPC_UUID:-}"
SSH_KEY_IDS="${DO_SSH_KEY_IDS:-}"

do_api() {
    curl -sf -X "$1" "https://api.digitalocean.com/v2/$2" \
        -H "Authorization: Bearer $DO_API_TOKEN" \
        -H "Content-Type: application/json" \
        "${@:3}"
}

echo "==> Creating droplet: $NAME ($SIZE, $REGION, ref=$REF)"

# --- Build create payload ---
CREATE_BODY=$(cat <<EOF
{
    "name": "$NAME",
    "region": "$REGION",
    "size": "$SIZE",
    "image": "$IMAGE",
    "tags": ["$TAG"],
    "monitoring": true
EOF
)

if [ -n "$VPC_UUID" ]; then
    CREATE_BODY="$CREATE_BODY, \"vpc_uuid\": \"$VPC_UUID\""
fi

if [ -n "$SSH_KEY_IDS" ]; then
    # Convert comma-separated IDs to JSON array
    SSH_KEYS_JSON=$(echo "$SSH_KEY_IDS" | tr ',' '\n' | sed 's/^/"/;s/$/"/' | paste -sd, -)
    CREATE_BODY="$CREATE_BODY, \"ssh_keys\": [$SSH_KEYS_JSON]"
fi

# Cloud-init user_data: harden SSH and set up VPC hosts before first boot
USER_DATA=$(cat <<'CLOUD_INIT'
#!/bin/bash
# Harden SSH to prevent lockouts from rapid connections
sed -i 's/^#\?MaxStartups.*/MaxStartups 30:50:80/' /etc/ssh/sshd_config
grep -q '^MaxStartups' /etc/ssh/sshd_config || echo 'MaxStartups 30:50:80' >> /etc/ssh/sshd_config
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# VPC internal hostnames
grep -q 'unbound.canyougrab.internal' /etc/hosts || echo '10.108.0.5 unbound.canyougrab.internal' >> /etc/hosts
grep -q 'rust-whois.canyougrab.internal' /etc/hosts || echo '10.108.0.8 rust-whois.canyougrab.internal' >> /etc/hosts
CLOUD_INIT
)
USER_DATA_B64=$(echo "$USER_DATA" | base64 | tr -d '\n')
CREATE_BODY="$CREATE_BODY, \"user_data\": \"$USER_DATA\""

CREATE_BODY="$CREATE_BODY }"

RESPONSE=$(do_api POST "droplets" -d "$CREATE_BODY")
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

# --- Wait for SSH (test with a simple connection) ---
echo -n "==> Waiting for SSH"
SSH_READY=false
for i in $(seq 1 60); do
    sleep 10
    if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o ServerAliveInterval=15 -i "$SSH_KEY" root@"$PUBLIC_IP" "echo ok" 2>/dev/null; then
        echo " connected!"
        SSH_READY=true
        break
    fi
    echo -n "."
done
if [ "$SSH_READY" = false ]; then
    echo " TIMEOUT — could not connect to $PUBLIC_IP via SSH"
    echo "  Droplet ID: $DROPLET_ID (not destroyed, fix manually)"
    exit 1
fi

# Wait a moment for cloud-init to apply SSH hardening from user_data
sleep 5

SSH_CMD="ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=15 -o ServerAliveCountMax=40 -i $SSH_KEY root@$PUBLIC_IP"

# --- Read deploy key locally ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_KEY="$SCRIPT_DIR/../env/github-deploy-key"
if [ ! -f "$DEPLOY_KEY" ]; then
    echo "ERROR: GitHub deploy key not found at $DEPLOY_KEY"
    exit 1
fi
DEPLOY_KEY_B64=$(base64 < "$DEPLOY_KEY" | tr -d '\n')

# --- Single SSH session: deploy key + cloud-init wait + full bootstrap ---
# Everything in ONE connection to avoid SSH lockouts.
echo "==> Bootstrapping droplet in a single SSH session..."
$SSH_CMD bash -s "$REF" "$REPO_URL" "$DEPLOY_KEY_B64" <<'REMOTE_BOOTSTRAP'
set -e
REF="$1"
REPO_URL="$2"
DEPLOY_KEY_B64="$3"

echo "[bootstrap] Installing GitHub deploy key"
mkdir -p /root/.ssh
echo "$DEPLOY_KEY_B64" | base64 -d > /root/.ssh/canyougrab-deploy
chmod 600 /root/.ssh/canyougrab-deploy
cat > /root/.ssh/config <<'GHCONF'
Host github.com
    IdentityFile /root/.ssh/canyougrab-deploy
    StrictHostKeyChecking no
GHCONF

echo "[bootstrap] Waiting for cloud-init..."
cloud-init status --wait 2>/dev/null || true
echo "[bootstrap] Cloud-init done"

echo "[bootstrap] Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx git curl 2>&1 | tail -3

echo "[bootstrap] Installing node_exporter"
cd /tmp
curl -sLO https://github.com/prometheus/node_exporter/releases/download/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz
tar xf node_exporter-1.8.2.linux-amd64.tar.gz
mv node_exporter-1.8.2.linux-amd64/node_exporter /usr/local/bin/
useradd -rs /bin/false node_exporter 2>/dev/null || true
cat > /etc/systemd/system/node_exporter.service <<'EOF'
[Unit]
Description=Node Exporter
After=network.target
[Service]
User=node_exporter
ExecStart=/usr/local/bin/node_exporter
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now node_exporter

echo "[bootstrap] Cloning repo"
git clone "$REPO_URL" /opt/canyougrab-repo
cd /opt/canyougrab-repo
git checkout "$REF"
echo "[bootstrap] Repo at: $(git log --oneline -1)"

echo "[bootstrap] Creating virtualenv and directories"
python3 -m venv /opt/canyougrab/venv
mkdir -p /opt/canyougrab/{api,portal,scripts}

echo "[bootstrap] Running deploy-app.sh"
bash config/deploy/deploy-app.sh "$REF"

echo "[bootstrap] SSL cert check"
mkdir -p /etc/ssl
if [ ! -f /etc/ssl/cloudflare-origin-cert.pem ]; then
    echo "WARNING: No SSL cert at /etc/ssl/cloudflare-origin-cert.pem"
fi

echo "[bootstrap] DONE"
REMOTE_BOOTSTRAP

echo ""
echo "============================================"
echo "Droplet provisioned successfully!"
echo "  Name:       $NAME"
echo "  ID:         $DROPLET_ID"
echo "  Public IP:  $PUBLIC_IP"
echo "  Private IP: $PRIVATE_IP"
echo "  Ref:        $REF"
echo ""
echo "Next steps:"
echo "  1. Set up SSL cert: /etc/ssl/cloudflare-origin-cert.pem"
echo "  2. Add to Prometheus: $PRIVATE_IP:9100"
echo "  3. Switch traffic: bash config/deploy/switch-traffic.sh $PUBLIC_IP"
echo "============================================"
