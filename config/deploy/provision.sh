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
REPO_URL="https://github.com/ericismaking/canyougrab-api.git"
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

# --- Wait for SSH ---
echo -n "==> Waiting for SSH"
for i in $(seq 1 30); do
    sleep 5
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -i "$SSH_KEY" root@"$PUBLIC_IP" "echo ok" 2>/dev/null; then
        echo " connected!"
        break
    fi
    echo -n "."
done

SSH_CMD="ssh -o StrictHostKeyChecking=no -i $SSH_KEY root@$PUBLIC_IP"

# --- Bootstrap ---
echo "==> Installing system packages"
$SSH_CMD "apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip nginx git curl" 2>&1 | tail -3

echo "==> Installing node_exporter"
$SSH_CMD bash <<'NODEEXP'
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
NODEEXP

echo "==> Cloning repo"
$SSH_CMD "git clone $REPO_URL /opt/canyougrab-repo && cd /opt/canyougrab-repo && git checkout $REF"

echo "==> Creating virtualenv"
$SSH_CMD "python3 -m venv /opt/canyougrab/venv"

echo "==> Creating directories"
$SSH_CMD "mkdir -p /opt/canyougrab/{api,portal,scripts}"

echo "==> Running deploy-app.sh"
$SSH_CMD "cd /opt/canyougrab-repo && bash config/deploy/deploy-app.sh $REF"

echo "==> Setting up SSL (Cloudflare origin cert placeholder)"
$SSH_CMD "mkdir -p /etc/ssl && test -f /etc/ssl/cloudflare-origin-cert.pem || (echo 'WARNING: No SSL cert found. Set up Cloudflare Origin cert at /etc/ssl/cloudflare-origin-cert.pem')"

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
