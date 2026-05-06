#!/usr/bin/env bash
# Single-request smoke test against OpenCode and/or Dynamo frontend.
#
# Usage:
#   scripts/curl_smoke.sh [opencode|dynamo|routes|all]   # default: opencode
#
# Env (with .env defaults):
#   OPENCODE_URL          http://127.0.0.1:4096
#   OPENCODE_SERVER_PASSWORD  (optional; sets HTTP basic auth)
#   DYNAMO_BASE_URL       http://127.0.0.1:8000/v1
#   MODEL_NAME            (required for opencode/dynamo subcommands)
#   PROMPT                'List files in the current directory and summarize what they do.'
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env" || true

: "${OPENCODE_URL:=http://127.0.0.1:4096}"
: "${DYNAMO_BASE_URL:=http://127.0.0.1:8000/v1}"
: "${PROMPT:=List files in the current directory and summarize what they do.}"

command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }

auth_args=()
if [[ -n "${OPENCODE_SERVER_PASSWORD:-}" ]]; then
  auth_args=(-u "opencode:${OPENCODE_SERVER_PASSWORD}")
fi

cmd="${1:-opencode}"

smoke_opencode() {
  : "${MODEL_NAME:?MODEL_NAME required (set in .env or env)}"

  local ws="/tmp/oc-smoke-$$"
  mkdir -p "$ws"
  echo "==> workspace=$ws"

  echo "==> create session"
  local session
  session=$(curl -sS "${auth_args[@]}" -X POST \
    "$OPENCODE_URL/session?directory=$ws" \
    -H 'content-type: application/json' \
    -d '{"title":"curl-smoke"}' | jq -r .id)
  echo "session=$session"

  echo "==> send message (model=$MODEL_NAME)"
  local body
  body=$(jq -n --arg m "$MODEL_NAME" --arg p "$PROMPT" '{
    model: {providerID:"local", modelID:$m},
    parts: [{type:"text", text:$p}]
  }')
  curl -sS "${auth_args[@]}" -X POST \
    "$OPENCODE_URL/session/$session/message?directory=$ws" \
    -H 'content-type: application/json' \
    -d "$body" | jq .
}

smoke_dynamo() {
  : "${MODEL_NAME:?MODEL_NAME required (set in .env or env)}"

  echo "==> POST $DYNAMO_BASE_URL/chat/completions (model=$MODEL_NAME)"
  local body
  body=$(jq -n --arg m "$MODEL_NAME" --arg p "$PROMPT" '{
    model: $m,
    messages: [{role:"user", content:$p}],
    max_tokens: 128
  }')
  curl -sS -X POST "$DYNAMO_BASE_URL/chat/completions" \
    -H 'content-type: application/json' \
    -d "$body" | jq .
}

smoke_routes() {
  echo "==> OpenCode endpoints (/doc)"
  curl -sS "${auth_args[@]}" "$OPENCODE_URL/doc" \
    | jq -r '.paths | keys[]' | sort
}

case "$cmd" in
  opencode) smoke_opencode ;;
  dynamo)   smoke_dynamo ;;
  routes)   smoke_routes ;;
  all)
    smoke_routes
    echo
    smoke_dynamo
    echo
    smoke_opencode
    ;;
  -h|--help|help)
    sed -n '2,12p' "$0"
    ;;
  *)
    echo "unknown subcommand: $cmd (use opencode|dynamo|routes|all)" >&2
    exit 2
    ;;
esac
