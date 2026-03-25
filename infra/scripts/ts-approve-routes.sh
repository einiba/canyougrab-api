#!/usr/bin/env bash
# Approve advertised Tailscale routes for a node.
# Usage: TS_API_KEY=... bash ts-approve-routes.sh <hostname>
#
# Run AFTER a droplet is created and Tailscale is connected.
# Approves whatever routes the node is advertising.
set -euo pipefail

HOSTNAME="${1:?Usage: ts-approve-routes.sh <hostname>}"
: "${TS_API_KEY:?Set TS_API_KEY}"

# Wait for the device to appear (may take a few seconds after boot)
for i in $(seq 1 30); do
    DEVICES=$(curl -sf -H "Authorization: Bearer $TS_API_KEY" \
        "https://api.tailscale.com/api/v2/tailnet/-/devices")

    DEVICE_ID=$(echo "$DEVICES" | python3 -c "
import sys, json
for d in json.load(sys.stdin)['devices']:
    if d['hostname'] == '$HOSTNAME':
        print(d['id'])
        break
" 2>/dev/null || echo "")

    if [ -n "$DEVICE_ID" ]; then
        break
    fi
    sleep 5
done

if [ -z "$DEVICE_ID" ]; then
    echo "WARNING: Device '$HOSTNAME' not found in tailnet after 150s"
    exit 0  # Don't fail the deploy
fi

# Get advertised routes
ROUTES=$(curl -sf -H "Authorization: Bearer $TS_API_KEY" \
    "https://api.tailscale.com/api/v2/device/$DEVICE_ID/routes")

ADVERTISED=$(echo "$ROUTES" | python3 -c "
import sys, json
routes = json.load(sys.stdin).get('advertisedRoutes', [])
print(json.dumps(routes))
")

if [ "$ADVERTISED" = "[]" ]; then
    echo "No routes advertised by $HOSTNAME"
    exit 0
fi

# Approve all advertised routes
curl -sf -X POST -H "Authorization: Bearer $TS_API_KEY" \
    -H "Content-Type: application/json" \
    "https://api.tailscale.com/api/v2/device/$DEVICE_ID/routes" \
    -d "{\"routes\": $ADVERTISED}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Routes approved for $HOSTNAME: {d[\"enabledRoutes\"]}')
"
