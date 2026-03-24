#!/usr/bin/env bash
# Destroy a dev-api droplet by ID or name.
# Usage: bash teardown.sh <droplet-id>
#        bash teardown.sh --name <droplet-name>
#
# Requires: DO_API_TOKEN env var
set -euo pipefail

# --- Auto-load config ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/deploy.env" ]; then
    set -a; source "$SCRIPT_DIR/deploy.env"; set +a
fi
if [ -z "${DO_API_TOKEN:-}" ]; then
    DOCTL_CONFIG="${HOME}/Library/Application Support/doctl/config.yaml"
    [ ! -f "$DOCTL_CONFIG" ] && DOCTL_CONFIG="$HOME/.config/doctl/config.yaml"
    [ -f "$DOCTL_CONFIG" ] && DO_API_TOKEN=$(grep 'access-token' "$DOCTL_CONFIG" | head -1 | awk '{print $2}')
fi

: "${DO_API_TOKEN:?Set DO_API_TOKEN — or install doctl and authenticate}"

do_api() {
    curl -sf -X "$1" "https://api.digitalocean.com/v2/$2" \
        -H "Authorization: Bearer $DO_API_TOKEN" \
        -H "Content-Type: application/json" \
        "${@:3}"
}

DROPLET_ID=""

if [ "${1:-}" = "--name" ]; then
    NAME="${2:?Usage: teardown.sh --name <droplet-name>}"
    echo "==> Looking up droplet: $NAME"
    DROPLET_ID=$(do_api GET "droplets?tag_name=canyougrab-api-dev&per_page=200" | python3 -c "
import sys, json
for d in json.load(sys.stdin)['droplets']:
    if d['name'] == '$NAME':
        print(d['id'])
        break
" 2>/dev/null || echo "")
    if [ -z "$DROPLET_ID" ]; then
        echo "ERROR: Droplet '$NAME' not found"
        exit 1
    fi
else
    DROPLET_ID="${1:?Usage: teardown.sh <droplet-id> or teardown.sh --name <name>}"
fi

# Get droplet info before destroying
INFO=$(do_api GET "droplets/$DROPLET_ID" 2>/dev/null || echo '{}')
DROPLET_NAME=$(echo "$INFO" | python3 -c "import sys,json; print(json.load(sys.stdin).get('droplet',{}).get('name','unknown'))" 2>/dev/null || echo "unknown")
DROPLET_IP=$(echo "$INFO" | python3 -c "
import sys, json
d = json.load(sys.stdin).get('droplet',{})
for net in d.get('networks',{}).get('v4',[]):
    if net['type'] == 'public':
        print(net['ip_address'])
        break
" 2>/dev/null || echo "unknown")

echo "==> Destroying droplet: $DROPLET_NAME (ID=$DROPLET_ID, IP=$DROPLET_IP)"
echo "    This is irreversible. Proceeding in 5 seconds..."
sleep 5

do_api DELETE "droplets/$DROPLET_ID" > /dev/null
echo "==> Droplet $DROPLET_ID destroyed"
echo ""
echo "Remember to:"
echo "  - Remove from Prometheus scrape targets if applicable"
echo "  - Verify traffic is NOT pointing at $DROPLET_IP"
