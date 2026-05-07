#!/usr/bin/env bash
# Single-request smoke test against OpenCode and/or Dynamo frontend.
#
# Usage:
#   scripts/curl_smoke.sh [opencode|dynamo|routes|swebench|all]   # default: opencode
#
# Env (with .env defaults):
#   OPENCODE_URL              http://127.0.0.1:4096
#   OPENCODE_SERVER_PASSWORD  (optional; sets HTTP basic auth)
#   DYNAMO_BASE_URL           http://127.0.0.1:8000/v1
#   MODEL_NAME                (required for opencode/dynamo/swebench)
#   PROMPT                    'List files...'  (used by `opencode`/`dynamo`)
#   SWE_SPLIT                 lite | verified | full   (default lite)
#   SWE_INDEX                 sample index in split    (default 0)
#   SEED_REPO                 1 to git-clone the SWE-bench repo at base_commit
#                             into the workspace before sending the prompt
#                             (default 0; matches runner.py which does NOT seed)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env" || true

: "${OPENCODE_URL:=http://127.0.0.1:4096}"
: "${DYNAMO_BASE_URL:=http://127.0.0.1:8000/v1}"
: "${PROMPT:=List files in the current directory and summarize what they do.}"
: "${SWE_SPLIT:=lite}"
: "${SWE_INDEX:=0}"
: "${SEED_REPO:=0}"

command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }

auth_args=()
if [[ -n "${OPENCODE_SERVER_PASSWORD:-}" ]]; then
  auth_args=(-u "opencode:${OPENCODE_SERVER_PASSWORD}")
fi

if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi

cmd="${1:-opencode}"

# Render the two views from a single GET response.
#   $1 session id   $2 workspace dir   $3 sample-info JSON (compact, optional)
_render_messages() {
  local session="$1" ws="$2" sample_json="${3:-}"
  local f
  f=$(mktemp -t oc-messages-XXXXXX.json)
  echo
  echo "==> fetching messages"
  curl -sS "${auth_args[@]}" \
    "$OPENCODE_URL/session/$session/message?directory=$ws" \
    -o "$f"
  local bytes msgs
  bytes=$(wc -c <"$f" | tr -d ' ')
  msgs=$(jq 'length' <"$f" 2>/dev/null || echo '?')
  echo "    saved=$f  bytes=$bytes  messages=$msgs"

  local sample_args=()
  [[ -n "$sample_json" ]] && sample_args=(--sample "$sample_json")

  echo
  echo "==> iteration summary (tokens / durations per LLM round-trip)"
  "$PY" -m testbed render-iterations -i "$f" "${sample_args[@]}"

  echo
  echo "==> iteration transcript (raw input / output text per LLM round-trip)"
  "$PY" -m testbed render-transcript -i "$f" "${sample_args[@]}"
}

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
    -d "$body" | jq '.info // .'

  local sample
  sample=$(jq -nc --arg p "$PROMPT" '{prompt:$p}')
  _render_messages "$session" "$ws" "$sample"
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

# swebench: build the EXACT prompt that src/testbed/runner.py would send
# for a real instance, optionally pre-seed the workspace with `git clone`
# at base_commit so we can isolate where the agent hangs:
#   SEED_REPO=0 (default) → matches runner.py, agent must clone itself
#   SEED_REPO=1           → repo already there at base_commit; tests pure code-edit path
smoke_swebench() {
  : "${MODEL_NAME:?MODEL_NAME required (set in .env or env)}"

  local ws="/tmp/oc-smoke-swe-$$"
  mkdir -p "$ws"
  echo "==> workspace=$ws  split=$SWE_SPLIT  index=$SWE_INDEX  seed_repo=$SEED_REPO"

  echo "==> loading SWE-bench sample"
  local meta_json
  meta_json=$(SWE_SPLIT="$SWE_SPLIT" SWE_INDEX="$SWE_INDEX" \
    PYTHONPATH="$ROOT/src" "$PY" - <<'PY'
import json, os
from testbed.swebench import load_samples, render_prompt

split = os.environ.get("SWE_SPLIT", "lite")
i = int(os.environ.get("SWE_INDEX", "0"))
samples = load_samples(split, i + 1)
s = samples[i]
print(json.dumps({
    "instance_id": s.instance_id,
    "repo": s.repo,
    "base_commit": s.base_commit,
    "prompt": render_prompt(s),
}))
PY
  )
  local instance_id repo base_commit prompt
  instance_id=$(echo "$meta_json" | jq -r .instance_id)
  repo=$(echo        "$meta_json" | jq -r .repo)
  base_commit=$(echo "$meta_json" | jq -r .base_commit)
  prompt=$(echo      "$meta_json" | jq -r .prompt)
  echo "    instance_id=$instance_id"
  echo "    repo=$repo  base_commit=$base_commit"

  if [[ "$SEED_REPO" == "1" ]]; then
    echo "==> seeding repo (git clone + checkout $base_commit)"
    if ! git clone --quiet --filter=blob:none "https://github.com/$repo.git" "$ws/repo"; then
      echo "git clone FAILED — this is likely the same hang you're seeing in the runner" >&2
      exit 1
    fi
    git -C "$ws/repo" -c advice.detachedHead=false checkout --quiet "$base_commit"
    echo "    seeded under $ws/repo"
  fi

  echo "==> create session"
  local session
  session=$(curl -sS "${auth_args[@]}" -X POST \
    "$OPENCODE_URL/session?directory=$ws" \
    -H 'content-type: application/json' \
    -d "{\"title\":\"curl-smoke-$instance_id\"}" | jq -r .id)
  echo "session=$session"

  echo "==> send message (model=$MODEL_NAME)"
  local body
  body=$(jq -n --arg m "$MODEL_NAME" --arg p "$prompt" '{
    model: {providerID:"local", modelID:$m},
    parts: [{type:"text", text:$p}]
  }')
  curl -sS "${auth_args[@]}" -X POST \
    "$OPENCODE_URL/session/$session/message?directory=$ws" \
    -H 'content-type: application/json' \
    -d "$body" | jq '.info // .'

  local sample
  sample=$(jq -nc --arg id "$instance_id" --arg r "$repo" --arg c "$base_commit" \
    --argjson seed "$([[ "$SEED_REPO" == "1" ]] && echo true || echo false)" '{
    instance_id: $id, repo: $r, base_commit: $c, seeded: $seed
  }')
  _render_messages "$session" "$ws" "$sample"
}

case "$cmd" in
  opencode) smoke_opencode ;;
  dynamo)   smoke_dynamo ;;
  routes)   smoke_routes ;;
  swebench) smoke_swebench ;;
  all)
    smoke_routes
    echo
    smoke_dynamo
    echo
    smoke_opencode
    ;;
  -h|--help|help)
    sed -n '2,18p' "$0"
    ;;
  *)
    echo "unknown subcommand: $cmd (use opencode|dynamo|routes|swebench|all)" >&2
    exit 2
    ;;
esac
