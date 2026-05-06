#!/usr/bin/env bash
# Launch / stop NVIDIA Dynamo OpenAI-compatible frontend.
# Dynamo 1.1 on a single local node uses --discovery-backend file (no etcd).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${WORKERS_ENV:-$ROOT/deploy/workers.env}"

# shellcheck source=deploy/_lib.sh
source "$ROOT/deploy/_lib.sh"
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE" || true

: "${ROUTER_MODE:=kv}"
: "${DYNAMO_PORT:=8000}"
: "${DISCOVERY_BACKEND:=file}"
: "${OTLP_GRPC_ENDPOINT:=grpc://127.0.0.1:4317}"

RUN_DIR="$ROOT/deploy/run"
PIDF="$RUN_DIR/frontend.pid"
LOG="$RUN_DIR/frontend.log"
mkdir -p "$RUN_DIR"

cmd="${1:-start}"

case "$cmd" in
  start)
    if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "[skip] frontend already running pgid=$(cat "$PIDF")"
      exit 0
    fi
    echo "[start] dynamo.frontend router=$ROUTER_MODE port=$DYNAMO_PORT discovery=$DISCOVERY_BACKEND"
    spawn_pgid "$PIDF" "$LOG" \
      PYTHONHASHSEED=0 \
      OTEL_SERVICE_NAME="dynamo-frontend" \
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="$OTLP_GRPC_ENDPOINT" \
      -- \
      python3 -m dynamo.frontend \
        --router-mode "$ROUTER_MODE" \
        --http-port "$DYNAMO_PORT" \
        --discovery-backend "$DISCOVERY_BACKEND"
    echo "log: $LOG"
    ;;
  stop)
    stop_pgid "$PIDF" dynamo-frontend
    ;;
  *)
    echo "usage: $0 {start|stop}" >&2
    exit 2
    ;;
esac
