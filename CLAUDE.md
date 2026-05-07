# CLAUDE.md

Reference for future Claude Code sessions on this repo. Keep edits in sync with reality — readers trust this over the README.

## What this is

Testbed that drives **SWE-bench** problems through an **OpenCode** agent server pointed at a **NVIDIA Dynamo** OpenAI-compatible frontend whose backend is a **vLLM PD-disaggregated** worker pool. The goal is to measure router/scheduling decisions under realistic coding-agent workloads: per-iteration token mix, LLM vs tool wall-clock, and prefill/decode time pulled from Jaeger spans by trace-id.

```
SWE-bench sample
   └─ runner.py (Poisson)──┐
                           ▼
                 OpenCode server (:4096)
                  POST /session, POST /session/:id/message, GET /session/:id/message
                           │
                           ▼ (OpenAI Chat Completions)
                 Dynamo frontend (:8000/v1)
                  --router-mode {round-robin|least-loaded|kv}
                           │
                           ▼ (KV cache transfer over NIXL)
                 vLLM workers
                   prefill (kv_producer) ──► decode (kv_consumer)
                           │
   OTLP/HTTP ──► otel-collector ──► Jaeger (:16686)
                                       │
                                       ▼
                              jaeger.py: trace_id → prefill_us / decode_us
```

Each iteration of a tool-loop assistant message corresponds to **one underlying LLM round trip** (a `step-start … step-finish` range inside `parts`).

## Layout

```
src/testbed/
  cli.py              click entrypoint: run / analyze / render-iterations / render-transcript
  runner.py           Poisson workload driver; one workspace per request; joins OpenCode + Jaeger
  poisson.py          arrival_offsets / arrivals (rate=qps, seed-deterministic)
  swebench.py         load_samples(split) + render_prompt(sample) — used by runner & curl_smoke
  opencode.py         async HTTP client for /session, /session/:id/message, /event SSE
  iterations.py       step-walk → per-iteration views (summary + transcript)
  trace.py            dataclass extraction: AssistantTurn, UserMessage, ToolCall, TaskRecord
  jaeger.py           query Jaeger by trace_id; sum durations of vllm-prefill-* / vllm-decode-* spans
  config.py           pydantic Settings (.env loader)

deploy/
  launch_workers.sh   start/stop vLLM PD workers per workers.env (PREFILL_WORKERS / DECODE_WORKERS slots)
  launch_frontend.sh  start/stop dynamo.frontend (--router-mode, --discovery-backend file)
  launch_opencode.sh  start/stop opencode serve; renders opencode.json from .tmpl via envsubst
  _lib.sh             spawn_pgid / stop_pgid / kill_port — process-group lifecycle
  otel-collector-config.yaml   OTLP receivers (gRPC :4317, HTTP :4318) → Jaeger exporter

scripts/
  curl_smoke.sh       opencode | dynamo | routes | swebench | all — single-request smoke

tests/                pytest, no network. Uses mock OpenCode/Jaeger payloads.
```

## Data flow & key invariants

### Per-task (runner.py:_run_one)
1. mkdir per-instance workspace under `Settings.workspace_root` (isolation between concurrent agents).
2. `gen_traceparent()` — W3C Trace Context. Same `trace_id` is sent on every Dynamo/vLLM hop, so Jaeger lookup-by-id stitches the trace.
3. POST /session, then POST /session/:id/message (synchronous; blocks until the agent loop finishes). RTT measured with `time.monotonic()`.
4. **Always GET /session/:id/message after** — the synchronous POST response carries only the FINAL assistant message; intermediate tool-loop steps are only available via the list endpoint. (`opencode.py:list_messages` docstring documents this.)
5. Sleep `jaeger_lookup_delay_s` (≥2s default) before querying Jaeger so OTLP exporters flush.
6. Merge: `aggregate_worker_timing(trace)` sums prefill/decode span durations by service-name prefix (`vllm-prefill-*`, `vllm-decode-*`).

### Per-iteration (iterations.py)
- An iteration = one `step-start … step-finish` window inside an AssistantMessage's `parts`.
- `step-start` and `step-finish` carry **no time field**, so durations are always derived from inner parts:
  - text/reasoning use `part.time.{start,end}`
  - tool uses `part.state.time.{start,end}` (ToolStateRunning / Completed / Error)
- Token usage is on the `step-finish.tokens` dict: `{ input, output, reasoning, cache:{read,write} }`.
- `_time_range(parts, types)` → `(min(start), max(end))` wall-clock window. Used both for the iteration bounds and per-item start/end on the text item (which collapses text+reasoning).

### Two iteration views
- `build_iteration_summary` — token / duration distribution. Each iteration has `started_at`, `completed_at`, `input.token_count`, `input.roles`, `output.cache_tokens`, `output.items[]`. Each item carries its own `started_at` / `completed_at` (and convenience `*_duration_ms`).
- `build_iteration_transcript` — raw text trace. Each iteration has `input.messages[]` (system → user → prior assistant → prior tool results, growing with idx) and `output.items[]` (`reason` / `text` / `tool` with `input` and `output`).

`token_count` semantics: it is `step-finish.tokens.input` (= vLLM `usage.prompt_tokens`) — the **whole prompt** the model saw at that round trip (system + user + prior assistant + prior tool results), not just the newly added portion. `output.cache_tokens.read` is the prompt-cache hit. New-prefill ≈ `token_count − cache_tokens.read`.

`reasoning_tokens` is intentionally NOT exposed: with `--reasoning-parser qwen3` vLLM returns `<think>` content but `usage.completion_tokens` already counts those tokens, so reasoning is inside `output_tokens`.

### Output files (one run, `--out results/<dir>/`)
- `config.json` — invocation parameters (split, num_samples, qps, seed, router, model)
- `trace.jsonl` — one TaskRecord per task (no raw messages, has rtt_s, prefill_us, decode_us, user_messages, assistant_turns)
- `iterations.jsonl` — one `build_iteration_summary` blob per task
- `transcripts.jsonl` — one `build_iteration_transcript` blob per task
- `summary.json` — p50/p95 of rtt, prefill_ms, decode_ms, tool_time_s

## Configuration

### `.env` (copy from `.env.example`)
- `OPENCODE_URL`, `OPENCODE_PORT`, `OPENCODE_HOST`, `OPENCODE_SERVER_PASSWORD`
- `DYNAMO_BASE_URL` / `DYNAMO_PORT` / `DYNAMO_API_KEY` / `ROUTER_MODE`
- `MODEL_NAME` — required by runner, opencode launcher, and curl_smoke. Must match the model registered in opencode.json AND served by the vLLM workers.
- `JAEGER_QUERY_URL` — Jaeger HTTP query API (default `http://127.0.0.1:16686`)
- `OTLP_HTTP_ENDPOINT` — collector HTTP/protobuf endpoint (default `http://127.0.0.1:4318/v1/traces`). vLLM/Dynamo OTLP gRPC export is unreliable against our collector, so we force HTTP.
- `WORKSPACE_ROOT` — per-request OpenCode workspace root.

### `deploy/workers.env` (copy from `.example`)
- `MODEL_NAME` — same one served (HF id or local path).
- `KV_CONNECTOR` — vLLM kv-transfer connector (e.g. NIXL).
- `PREFILL_WORKERS`, `DECODE_WORKERS` — space-separated slots, format `name:gpus:tp:pp[:extra_args]` (use `%20` to escape spaces inside extra args).
- `PREFILL_*` / `DECODE_*` — per-role engine knobs (`MAX_MODEL_LEN`, `MAX_NUM_BATCHED_TOKENS`, `MAX_NUM_SEQS`).
- `EXTRA_VLLM_ARGS` — common extras for both roles.

### `opencode.json` (rendered from `opencode.json.tmpl`)
Single OpenAI-compatible provider `local` pointing at Dynamo. Rendered at opencode launch time via `envsubst` (only string-valued option fields, NOT object keys, get interpolated by OpenCode itself — that's why we render externally). Default `model: local/${MODEL_NAME}`.

OpenCode is launched with `OPENCODE_EXPERIMENTAL_WORKSPACES=true`; per-request workspace is carried in the `?directory=` query param of every session/message call.

### `docker-compose.yml`
NATS (jetstream) + otel-collector + jaeger all-in-one. Brought up via `make up`. The collector exposes OTLP gRPC :4317 and HTTP :4318 and forwards to jaeger:4317 (gRPC).

## Running

End-to-end one-shot:
```
make up              # NATS + otel-collector + jaeger
make workers         # vLLM PD per workers.env
make frontend        # dynamo.frontend on :8000
make opencode        # opencode serve on :4096
make smoke N=20 QPS=0.5
make sweep ROUTERS="round-robin least-loaded kv" N=20 QPS=0.5
make kill-all        # opencode + frontend + workers
make down            # infra
```

CLI direct:
```
.venv/bin/python -m testbed run \
  --split lite --num-samples 20 --qps 0.5 --seed 42 \
  --router kv --out results/run1
```

`--router` is **only recorded in summary.json**; the actual router mode is whatever `dynamo.frontend` was started with (env `ROUTER_MODE` → `launch_frontend.sh`).

### Smoke-testing slices (no full workload)
```
scripts/curl_smoke.sh routes        # list OpenCode endpoints
scripts/curl_smoke.sh dynamo        # one /v1/chat/completions to Dynamo
scripts/curl_smoke.sh opencode      # one full session+message+render
scripts/curl_smoke.sh swebench      # send a real SWE-bench prompt
SEED_REPO=1 scripts/curl_smoke.sh swebench   # pre-clone repo into workspace
```
The opencode/swebench paths fire POST /session/:id/message silently, then `_render_messages` GETs the message list and pipes it through `render-iterations` and `render-transcript`. Pass `--sample <json>` (already wired in curl_smoke) to embed sample metadata under `.sample`.

### Tests
`pytest -q` — no network, no GPU. Mocks live in `tests/test_*.py`.

## Conventions / gotchas

- **System prompt lives on the user message** (`info.system`), and OpenCode types it as `string[]`. iterations.py normalizes lists to `"\n\n".join(...)` before emitting transcript.
- **Each PD worker needs a unique NIXL side-channel port** — launch_workers.sh assigns `6000 + rank*100` because vLLM defaults all workers to 5600 and they collide on a single host.
- **PGID-based teardown** (`deploy/_lib.sh`) is necessary because OpenCode's `.opencode` worker and vLLM's TP/PP shards setsid out of the parent. opencode-down also runs `kill_port 4096` as a backstop.
- **Process backgrounding**: `spawn_pgid` uses `setsid bash -c 'echo $$ > pidf; exec env … cmd'` — the wrapper's own `$$` is the session leader because `exec` preserves PID. Don't replace with `&` + `$!` (off-by-one fork).
- **Trace propagation**: `runner._run_one` generates one `traceparent` per task and passes it on `POST /session` AND `POST /session/:id/message` (both via `OpenCodeClient`). OpenCode forwards it; Dynamo and vLLM workers stamp spans onto the same `trace_id`.
- **Jaeger flush latency**: spans are not queryable instantly. The default 2s `--jaeger-lookup-delay` is empirical for our otel-collector batch settings (`timeout: 1s`).
- **Per-iteration vs per-task time**: there is no per-step OpenCode timestamp. Per-iteration `started_at`/`completed_at` come from min/max over inner parts. The user-facing wall-clock RTT is `TaskRecord.rtt_s` from runner (POST monotonic delta).
- **roles[] in summary**: approximation of the message list the model saw at iteration N — `[system, user, (assistant, tool*) × N]`. One `tool` entry per tool call in each prior step.

## Editing rules

- README.md is user-facing; CLAUDE.md is for future agent sessions. Keep them in sync only on user-visible commands; internal invariants belong here.
- Don't add fields to `iterations.py` outputs without checking — downstream `runner.py` writes them straight to `iterations.jsonl` / `transcripts.jsonl` and consumers may parse them.
- `tests/test_iterations.py` fixture is the contract for both views. Update it when changing the schema.
- New CLI flags go into both `cli.py` and the relevant Makefile target.

## Branch

Active development branch: `claude/setup-agent-scheduler-tests-Mzol4`. Push directly here unless told otherwise.
