#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/canyougrab-repo}"
API_DIR="${API_DIR:-/opt/canyougrab/api}"
PORTAL_DIR="${PORTAL_DIR:-/opt/canyougrab/portal}"
API_VENV_DIR="${API_VENV_DIR:-/opt/canyougrab/venv}"

API_SERVICE="${API_SERVICE:-canyougrab-api}"
WORKER_SERVICE="${WORKER_SERVICE:-canyougrab-worker}"
MCP_SERVICE="${MCP_SERVICE:-canyougrab-mcp}"

MCP_DIR="${MCP_DIR:-/opt/canyougrab-mcp}"
MCP_VENV_DIR="${MCP_VENV_DIR:-/opt/canyougrab-mcp/.venv}"

require_path() {
  local path="$1"
  local description="$2"

  if [ ! -e "$path" ]; then
    echo "Missing ${description}: ${path}" >&2
    exit 1
  fi
}

has_service() {
  systemctl cat "$1" >/dev/null 2>&1
}

cd "$REPO_DIR"

require_path "$REPO_DIR/backend" "backend source directory"
require_path "$REPO_DIR/portal/dist" "built portal dist directory"
require_path "$API_VENV_DIR/bin/pip" "backend virtualenv pip"

rsync -a --delete --exclude="__pycache__" "$REPO_DIR/backend/" "$API_DIR/"
rsync -a --delete "$REPO_DIR/portal/dist/" "$PORTAL_DIR/"

"$API_VENV_DIR/bin/pip" install -q -r "$API_DIR/requirements.txt"

services_to_restart=("$API_SERVICE" "$WORKER_SERVICE")
services_to_status=("$API_SERVICE")

if has_service "$MCP_SERVICE"; then
  require_path "$REPO_DIR/mcp-server" "MCP source directory"
  require_path "$MCP_VENV_DIR/bin/python3" "MCP virtualenv python"

  rsync -a --delete \
    --exclude=".venv" \
    --exclude="dist" \
    --exclude="__pycache__" \
    "$REPO_DIR/mcp-server/" "$MCP_DIR/"

  "$MCP_VENV_DIR/bin/python3" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$MCP_VENV_DIR/bin/python3" -m pip install -q -e "$MCP_DIR[remote]"

  services_to_restart+=("$MCP_SERVICE")
  services_to_status+=("$MCP_SERVICE")
fi

systemctl restart "${services_to_restart[@]}"

echo "==> Deploy complete: $(git log --oneline -1)"
systemctl status "${services_to_status[@]}" --no-pager | head -10
