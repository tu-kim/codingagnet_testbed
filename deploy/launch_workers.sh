#!/usr/bin/env bash
# Launch / stop vLLM PD workers based on deploy/workers.env.
# Slot format: name:gpus:tp:pp[:extra_args]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${WORKERS_ENV:-$ROOT/deploy/workers.env}"
RUN_DIR="$ROOT/deploy/run"
mkdir -p "$RUN_DIR"

# shellcheck source=deploy/_lib.sh
source "$ROOT/deploy/_lib.sh"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE (copy from workers.env.example)" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${MODEL_NAME:?MODEL_NAME required in $ENV_FILE}"
: "${KV_CONNECTOR:?KV_CONNECTOR required}"
: "${OTLP_GRPC_ENDPOINT:=grpc://127.0.0.1:4317}"
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

  # Per-slot HTTP port (avoid collisions). Convention: 9000 + rank.
  local port=$((9000 + rank))
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

  echo "[start] $svc gpus=$gpus tp=$tp pp=$pp http=$port nixl_port=$nixl_port rank=$rank"
  # shellcheck disable=SC2086 # we want word-splitting on $EXTRA_VLLM_ARGS / $extra
  spawn_pgid "$pidf" "$log" \
    CUDA_VISIBLE_DEVICES="$gpus" \
    VLLM_NIXL_SIDE_CHANNEL_HOST=localhost \
    VLLM_NIXL_SIDE_CHANNEL_PORT="$nixl_port" \
    OTEL_SERVICE_NAME="$svc" \
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="$OTLP_GRPC_ENDPOINT" \
    PYTHONHASHSEED=0 \
    -- \
    bash -c "exec vllm serve \"\$0\" \
      --host 0.0.0.0 --port \"\$1\" \
      --tensor-parallel-size \"\$2\" --pipeline-parallel-size \"\$3\" \
      --otlp-traces-endpoint \"\$4\" \
      --kv-transfer-config \"\$5\" \
      $EXTRA_VLLM_ARGS $extra" \
    "$MODEL_NAME" "$port" "$tp" "$pp" "$OTLP_GRPC_ENDPOINT" "$kv_cfg"
}

stop_all() {
  shopt -s nullglob
  for pidf in "$RUN_DIR"/vllm-*.pid; do
    stop_pgid "$pidf" "$(basename "$pidf" .pid)"
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
