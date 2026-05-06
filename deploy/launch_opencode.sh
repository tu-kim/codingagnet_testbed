#!/usr/bin/env bash
# Launch / stop opencode serve with experimental per-request workspaces enabled.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env" || true

: "${OPENCODE_PORT:=4096}"
: "${OPENCODE_HOST:=127.0.0.1}"

RUN_DIR="$ROOT/deploy/run"
PIDF="$RUN_DIR/opencode.pid"
LOG="$RUN_DIR/opencode.log"
mkdir -p "$RUN_DIR"

cmd="${1:-start}"

case "$cmd" in
  start)
    if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "[skip] opencode already running pid=$(cat "$PIDF")"
      exit 0
    fi
    echo "[start] opencode serve port=$OPENCODE_PORT (workspaces=experimental)"
    OPENCODE_EXPERIMENTAL_WORKSPACES=true \
    OPENCODE_CONFIG="$ROOT/opencode.json" \
    nohup opencode serve \
      --port "$OPENCODE_PORT" \
      --hostname "$OPENCODE_HOST" \
      >"$LOG" 2>&1 &
    echo $! > "$PIDF"
    echo "log: $LOG"
    ;;
  stop)
    if [[ -f "$PIDF" ]]; then
      pid=$(cat "$PIDF")
      if kill -0 "$pid" 2>/dev/null; then
        echo "[stop] opencode pid=$pid"
        kill "$pid" || true
      fi
      rm -f "$PIDF"
    else
      echo "[skip] no opencode.pid"
    fi
    ;;
  *)
    echo "usage: $0 {start|stop}" >&2
    exit 2
    ;;
esac
