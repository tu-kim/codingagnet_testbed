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

PY="${PYTHON:-python3}"

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
    -d "$body" | jq '.info // .'
  echo
  echo "==> all messages (full per-step / per-part metadata)"
  curl -sS "${auth_args[@]}" \
    "$OPENCODE_URL/session/$session/message?directory=$ws" \
    | jq '[.[] | {
        role: .info.role,
        id: .info.id,
        time: .info.time,
        # user-message fields
        user: (if .info.role == "user" then {
          agent: .info.agent,
          model: .info.model,
          system: .info.system,
          tools: .info.tools,
          text: ([.parts[]? | select(.type=="text") | .text] | join(""))
        } else null end),
        # assistant-message fields
        assistant: (if .info.role == "assistant" then {
          modelID: .info.modelID,
          providerID: .info.providerID,
          mode: .info.mode,
          cost: .info.cost,
          finish: .info.finish,
          error: .info.error,
          tokens: .info.tokens
        } else null end),
        parts: [.parts[]? | {
          type,
          # tool: command + description + title + status + timing
          tool: (if .type == "tool" then {
            name: .tool,
            callID: .callID,
            status: .state.status,
            title: .state.title,
            input: .state.input,
            output_preview: (.state.output? | tostring | .[0:300]),
            error: .state.error,
            time: .state.time
          } else null end),
          # step-finish: per-iteration tokens + cost + reason
          step_finish: (if .type == "step-finish" then {
            reason, cost, tokens
          } else null end),
          # text / reasoning previews
          text_preview: (if .type == "text" then (.text // "" | .[0:200]) else null end),
          reasoning_preview: (if .type == "reasoning" then (.text // "" | .[0:200]) else null end),
          # other event types
          patch: (if .type == "patch" then {hash, files} else null end),
          retry: (if .type == "retry" then {attempt, error} else null end),
          compaction: (if .type == "compaction" then {auto} else null end)
        }]
      }]'
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
  echo
  echo "==> all messages (full per-step / per-part metadata)"
  curl -sS "${auth_args[@]}" \
    "$OPENCODE_URL/session/$session/message?directory=$ws" \
    | jq '[.[] | {
        role: .info.role,
        id: .info.id,
        time: .info.time,
        # user-message fields
        user: (if .info.role == "user" then {
          agent: .info.agent,
          model: .info.model,
          system: .info.system,
          tools: .info.tools,
          text: ([.parts[]? | select(.type=="text") | .text] | join(""))
        } else null end),
        # assistant-message fields
        assistant: (if .info.role == "assistant" then {
          modelID: .info.modelID,
          providerID: .info.providerID,
          mode: .info.mode,
          cost: .info.cost,
          finish: .info.finish,
          error: .info.error,
          tokens: .info.tokens
        } else null end),
        parts: [.parts[]? | {
          type,
          # tool: command + description + title + status + timing
          tool: (if .type == "tool" then {
            name: .tool,
            callID: .callID,
            status: .state.status,
            title: .state.title,
            input: .state.input,
            output_preview: (.state.output? | tostring | .[0:300]),
            error: .state.error,
            time: .state.time
          } else null end),
          # step-finish: per-iteration tokens + cost + reason
          step_finish: (if .type == "step-finish" then {
            reason, cost, tokens
          } else null end),
          # text / reasoning previews
          text_preview: (if .type == "text" then (.text // "" | .[0:200]) else null end),
          reasoning_preview: (if .type == "reasoning" then (.text // "" | .[0:200]) else null end),
          # other event types
          patch: (if .type == "patch" then {hash, files} else null end),
          retry: (if .type == "retry" then {attempt, error} else null end),
          compaction: (if .type == "compaction" then {auto} else null end)
        }]
      }]'
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
