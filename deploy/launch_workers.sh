#!/usr/bin/env bash
# Launch / stop vLLM PD workers based on deploy/workers.env.
# Slot format: name:gpus:tp:pp[:extra_args]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${WORKERS_ENV:-$ROOT/deploy/workers.env}"
RUN_DIR="$ROOT/deploy/run"
mkdir -p "$RUN_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE (copy from workers.env.example)" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${MODEL_NAME:?MODEL_NAME required in $ENV_FILE}"
: "${KV_CONNECTOR:?KV_CONNECTOR required}"
# OTLP/HTTP — vLLM/dynamo's OTLP gRPC export does not work reliably against
# our otel-collector receiver, so we route over HTTP/protobuf to :4318/v1/traces.
: "${OTLP_HTTP_ENDPOINT:=http://127.0.0.1:4318/v1/traces}"
EXTRA_VLLM_ARGS="${EXTRA_VLLM_ARGS:-}"

cmd="${1:-start}"

# total parallel size = #prefill + #decode (rank assigned in order)
all_slots=()
for s in $PREFILL_WORKERS; do all_slots+=("prefill:$s"); done
for s in $DECODE_WORKERS;  do all_slots+=("decode:$s"); done
KV_PARALLEL_SIZE="${#all_slots[@]}"

start_one() {
  local role="$1" slot="$2" rank="$3"
  IFS=':' read -r name gpus tp pp extra <<<"$slot"
  : "${name:?slot missing name}" "${gpus:?slot missing gpus}" "${tp:?slot missing tp}" "${pp:?slot missing pp}"
  extra="${extra//%20/ }"

  local kv_role
  if [[ "$role" == "prefill" ]]; then kv_role="kv_producer"; else kv_role="kv_consumer"; fi

  # Per-slot NIXL side-channel port — defaults to 5600 in vLLM's NIXL
  # connector, so multiple workers on one host collide. Assign a unique
  # value per worker.
  local nixl_port=$((6000 + rank * 100))

  local kv_cfg
  kv_cfg=$(cat <<EOF
{"kv_connector":"${KV_CONNECTOR}","kv_role":"${kv_role}","kv_rank":${rank},"kv_parallel_size":${KV_PARALLEL_SIZE}}
EOF
)

  local svc="vllm-${role}-${name}"
  local log="$RUN_DIR/${svc}.log"
  local pidf="$RUN_DIR/${svc}.pid"

  echo "[start] $svc role=$role gpus=$gpus tp=$tp pp=$pp nixl_port=$nixl_port rank=$rank"
  CUDA_VISIBLE_DEVICES="$gpus" \
  VLLM_NIXL_SIDE_CHANNEL_HOST=localhost \
  VLLM_NIXL_SIDE_CHANNEL_PORT="$nixl_port" \
  OTEL_SERVICE_NAME="$svc" \
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="$OTLP_HTTP_ENDPOINT" \
  OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf \
  PYTHONHASHSEED=0 \
  nohup python3 -m dynamo.vllm \
    --model "$MODEL_NAME" \
    --disaggregation-mode "$role" \
    --discovery-backend file \
    --tensor-parallel-size "$tp" \
    --pipeline-parallel-size "$pp" \
    --otlp-traces-endpoint "$OTLP_HTTP_ENDPOINT" \
    --kv-transfer-config "$kv_cfg" \
    $EXTRA_VLLM_ARGS \
    $extra \
    >"$log" 2>&1 &
  echo $! > "$pidf"
}

stop_all() {
  shopt -s nullglob
  for pidf in "$RUN_DIR"/vllm-*.pid; do
    pid=$(cat "$pidf" 2>/dev/null || true)
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "[stop] $(basename "$pidf" .pid) pid=$pid"
      kill "$pid" || true
    fi
    rm -f "$pidf"
  done
}

case "$cmd" in
  start)
    rank=0
    for entry in "${all_slots[@]}"; do
      role="${entry%%:*}"
      slot="${entry#*:}"
      start_one "$role" "$slot" "$rank"
      rank=$((rank + 1))
    done
    echo "logs: $RUN_DIR/"
    ;;
  stop)
    stop_all
    ;;
  *)
    echo "usage: $0 {start|stop}" >&2
    exit 2
    ;;
esac
