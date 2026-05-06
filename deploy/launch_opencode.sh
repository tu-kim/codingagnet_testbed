#!/usr/bin/env bash
# Launch / stop opencode serve with experimental per-request workspaces enabled.
# opencode.json is rendered from opencode.json.tmpl via envsubst because
# opencode does not interpolate {env:VAR} inside `models` object keys — only
# in string-valued options like baseURL/apiKey.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# shellcheck source=deploy/_lib.sh
source "$ROOT/deploy/_lib.sh"
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env" || true

: "${OPENCODE_PORT:=4096}"
: "${OPENCODE_HOST:=127.0.0.1}"
: "${MODEL_NAME:?MODEL_NAME required (set in .env)}"
: "${DYNAMO_BASE_URL:=http://127.0.0.1:8000/v1}"
: "${DYNAMO_API_KEY:=local}"

RUN_DIR="$ROOT/deploy/run"
PIDF="$RUN_DIR/opencode.pid"
LOG="$RUN_DIR/opencode.log"
TMPL="$ROOT/opencode.json.tmpl"
RENDERED="$ROOT/opencode.json"
mkdir -p "$RUN_DIR"

cmd="${1:-start}"

render_config() {
  if [[ ! -f "$TMPL" ]]; then
    echo "missing template $TMPL" >&2; exit 1
  fi
  command -v envsubst >/dev/null || {
    echo "envsubst not found (install gettext-base)" >&2; exit 1; }
  MODEL_NAME="$MODEL_NAME" DYNAMO_BASE_URL="$DYNAMO_BASE_URL" DYNAMO_API_KEY="$DYNAMO_API_KEY" \
    envsubst '${MODEL_NAME} ${DYNAMO_BASE_URL} ${DYNAMO_API_KEY}' \
      < "$TMPL" > "$RENDERED"
  echo "[render] $RENDERED (model=$MODEL_NAME baseURL=$DYNAMO_BASE_URL)"
}

case "$cmd" in
  start)
    if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "[skip] opencode already running pgid=$(cat "$PIDF")"
      exit 0
    fi
    render_config
    echo "[start] opencode serve port=$OPENCODE_PORT (workspaces=experimental)"
    spawn_pgid "$PIDF" "$LOG" \
      OPENCODE_EXPERIMENTAL_WORKSPACES=true \
      OPENCODE_CONFIG="$RENDERED" \
      -- \
      opencode serve --port "$OPENCODE_PORT" --hostname "$OPENCODE_HOST"
    echo "log: $LOG"
    ;;
  stop)
    stop_pgid "$PIDF" opencode
    ;;
  render)
    render_config
    ;;
  *)
    echo "usage: $0 {start|stop|render}" >&2
    exit 2
    ;;
esac
