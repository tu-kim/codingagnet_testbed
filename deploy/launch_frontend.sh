#!/usr/bin/env bash
# Launch / stop NVIDIA Dynamo OpenAI-compatible frontend.
# Dynamo 1.1 on a single local node uses --discovery-backend file (no etcd).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${WORKERS_ENV:-$ROOT/deploy/workers.env}"
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
      echo "[skip] frontend already running pid=$(cat "$PIDF")"
      exit 0
    fi
    echo "[start] dynamo.frontend router=$ROUTER_MODE port=$DYNAMO_PORT discovery=$DISCOVERY_BACKEND"
    PYTHONHASHSEED=0 \
    OTEL_SERVICE_NAME="dynamo-frontend" \
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="$OTLP_GRPC_ENDPOINT" \
    nohup python3 -m dynamo.frontend \
      --router-mode "$ROUTER_MODE" \
      --http-port "$DYNAMO_PORT" \
      --discovery-backend "$DISCOVERY_BACKEND" \
      >"$LOG" 2>&1 &
    echo $! > "$PIDF"
    echo "log: $LOG"
    ;;
  stop)
    if [[ -f "$PIDF" ]]; then
      pid=$(cat "$PIDF")
      if kill -0 "$pid" 2>/dev/null; then
        echo "[stop] dynamo.frontend pid=$pid"
        kill "$pid" || true
      fi
      rm -f "$PIDF"
    else
      echo "[skip] no frontend.pid"
    fi
    ;;
  *)
    echo "usage: $0 {start|stop}" >&2
    exit 2
    ;;
esac
