#!/usr/bin/env bash
# Remove stale Tailscale nodes matching a hostname.
# Usage: TS_API_KEY=... bash ts-cleanup.sh <hostname>
#
# Run BEFORE creating a new droplet during blue-green deploy.
# Ensures the new node gets a clean hostname (not "prod-api-1").
set -euo pipefail

HOSTNAME="${1:?Usage: ts-cleanup.sh <hostname>}"
: "${TS_API_KEY:?Set TS_API_KEY}"

DEVICES=$(curl -sf -H "Authorization: Bearer $TS_API_KEY" \
    "https://api.tailscale.com/api/v2/tailnet/-/devices")

echo "$DEVICES" | python3 -c "
import sys, json, urllib.request

devices = json.load(sys.stdin)['devices']
api_key = '$TS_API_KEY'
hostname = '$HOSTNAME'
removed = 0

for d in devices:
    if d['hostname'] == hostname:
        req = urllib.request.Request(
            f'https://api.tailscale.com/api/v2/device/{d[\"id\"]}',
            method='DELETE',
            headers={'Authorization': f'Bearer {api_key}'}
        )
        urllib.request.urlopen(req)
        print(f'Removed stale node: {d[\"hostname\"]} (id={d[\"id\"]})')
        removed += 1

if removed == 0:
    print(f'No stale nodes found for hostname: {hostname}')
else:
    print(f'Cleaned up {removed} stale node(s)')
"
