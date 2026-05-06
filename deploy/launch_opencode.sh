#!/usr/bin/env bash
# Launch opencode serve with experimental per-request workspaces enabled.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env" || true

: "${OPENCODE_PORT:=4096}"
: "${OPENCODE_HOST:=127.0.0.1}"

RUN_DIR="$ROOT/deploy/run"
mkdir -p "$RUN_DIR"

echo "[start] opencode serve port=$OPENCODE_PORT (workspaces=experimental)"
OPENCODE_EXPERIMENTAL_WORKSPACES=true \
OPENCODE_CONFIG="$ROOT/opencode.json" \
nohup opencode serve \
  --port "$OPENCODE_PORT" \
  --hostname "$OPENCODE_HOST" \
  >"$RUN_DIR/opencode.log" 2>&1 &
echo $! > "$RUN_DIR/opencode.pid"
echo "log: $RUN_DIR/opencode.log"
