#!/usr/bin/env bash
# Switch dev-api.canyougrab.it DNS to point at a specific IP.
# Usage: bash switch-traffic.sh <target-ip> [--domain dev-api.canyougrab.it]
#
# Requires: DO_API_TOKEN env var
set -euo pipefail

TARGET_IP="${1:?Usage: switch-traffic.sh <target-ip>}"
DOMAIN="${2:-dev-api.canyougrab.it}"
DO_DOMAIN="canyougrab.it"

: "${DO_API_TOKEN:?Set DO_API_TOKEN env var}"

do_api() {
    curl -sf -X "$1" "https://api.digitalocean.com/v2/$2" \
        -H "Authorization: Bearer $DO_API_TOKEN" \
        -H "Content-Type: application/json" \
        "${@:3}"
}

# Extract subdomain (e.g., "dev-api" from "dev-api.canyougrab.it")
SUBDOMAIN="${DOMAIN%%.$DO_DOMAIN}"

echo "==> Switching $DOMAIN → $TARGET_IP"

# Find existing A record
RECORDS=$(do_api GET "domains/$DO_DOMAIN/records?type=A&name=$DOMAIN&per_page=200")
RECORD_ID=$(echo "$RECORDS" | python3 -c "
import sys, json
records = json.load(sys.stdin)['domain_records']
for r in records:
    if r['name'] == '$SUBDOMAIN' and r['type'] == 'A':
        print(r['id'])
        break
" 2>/dev/null || echo "")

if [ -n "$RECORD_ID" ]; then
    # Update existing record
    do_api PUT "domains/$DO_DOMAIN/records/$RECORD_ID" \
        -d "{\"data\": \"$TARGET_IP\"}" > /dev/null
    echo "==> Updated DNS record $RECORD_ID → $TARGET_IP"
else
    # Create new record
    do_api POST "domains/$DO_DOMAIN/records" \
        -d "{\"type\": \"A\", \"name\": \"$SUBDOMAIN\", \"data\": \"$TARGET_IP\", \"ttl\": 300}" > /dev/null
    echo "==> Created DNS record $SUBDOMAIN → $TARGET_IP"
fi

# Verify (may take a moment for DNS propagation)
echo "==> Waiting 10s for DNS propagation..."
sleep 10

RESOLVED=$(dig +short "$DOMAIN" @ns1.digitalocean.com 2>/dev/null || echo "unknown")
echo "==> DNS resolves to: $RESOLVED"

if [ "$RESOLVED" = "$TARGET_IP" ]; then
    echo "==> DNS switch confirmed!"
else
    echo "==> WARNING: DNS not yet propagated (expected $TARGET_IP, got $RESOLVED)"
    echo "    This may take up to 5 minutes with TTL=300"
fi

# Health check
echo -n "==> Health check: "
if curl -sf --max-time 10 "https://$DOMAIN/health" > /dev/null 2>&1; then
    echo "PASSED"
else
    echo "PENDING (may need DNS propagation or SSL setup)"
fi

echo "==> Traffic switch complete"
