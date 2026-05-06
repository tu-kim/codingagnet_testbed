#!/usr/bin/env bash
# Launch NVIDIA Dynamo OpenAI-compatible frontend.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${WORKERS_ENV:-$ROOT/deploy/workers.env}"
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE" || true

: "${ROUTER_MODE:=kv}"
: "${DYNAMO_PORT:=8000}"
: "${ETCD_ADDR:=127.0.0.1:2379}"
: "${OTLP_GRPC_ENDPOINT:=grpc://127.0.0.1:4317}"

RUN_DIR="$ROOT/deploy/run"
mkdir -p "$RUN_DIR"

echo "[start] dynamo.frontend router=$ROUTER_MODE port=$DYNAMO_PORT"
PYTHONHASHSEED=0 \
OTEL_SERVICE_NAME="dynamo-frontend" \
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="$OTLP_GRPC_ENDPOINT" \
nohup python3 -m dynamo.frontend \
  --router-mode "$ROUTER_MODE" \
  --http-port "$DYNAMO_PORT" \
  --discovery-backend etcd \
  --discovery-etcd-addr "$ETCD_ADDR" \
  >"$RUN_DIR/frontend.log" 2>&1 &
echo $! > "$RUN_DIR/frontend.pid"
echo "log: $RUN_DIR/frontend.log"
