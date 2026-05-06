# Agent Scheduler Testbed

코딩 에이전트 워크로드를 위한 라우팅·스케줄링 테스트베드.

```
SWE-bench  →  query generator (Poisson)  →  OpenCode (per-request workspace)
                                                    │
                                                    ▼
                                  Dynamo frontend (router-mode 가변)
                                                    │
                          ┌─────────────────────────┴─────────────────────────┐
                          ▼                                                   ▼
                  vLLM prefill workers (N개, TP/PP 가능)        vLLM decode workers (M개)
                          │                                                   │
                          └────── OTLP traces (trace-id 공유) ──────────────────┘
                                                    │
                                                    ▼
                                       OTel Collector → Jaeger
```

요청별 prefill / decode 시간은 동일 trace-id 안에서 vLLM prefill 워커와 vLLM decode 워커
span의 총 duration으로 계산한다 (`src/testbed/jaeger.py`).

## 빠른 시작 (요약)

```bash
cp .env.example .env
cp deploy/workers.env.example deploy/workers.env
$EDITOR .env deploy/workers.env                   # MODEL_NAME 등 채우기

make up           # NATS, otel-collector, jaeger (docker-compose)
make workers      # workers.env 슬롯대로 prefill/decode vLLM 기동
make frontend     # Dynamo OpenAI 호환 프런트엔드 (--discovery-backend file)
make opencode     # opencode.json을 .env 값으로 렌더링 후 serve
make smoke N=5 QPS=0.2

# 정리 (자식 프로세스까지 SIGTERM → 유예 후 SIGKILL)
make kill-all     # opencode + frontend + workers 모두 종료
make down         # 인프라 컨테이너 종료
```

`opencode.json`은 `opencode.json.tmpl`을 `.env`의 `MODEL_NAME`/`DYNAMO_BASE_URL`/`DYNAMO_API_KEY`로
`envsubst` 렌더링한 결과물이다 (`make opencode` 시 자동, 또는 `bash deploy/launch_opencode.sh render`).
opencode가 `models` 객체 키에서는 `{env:VAR}` interpolation을 지원하지 않기 때문에 템플릿 단계에서 치환한다.

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

추가로 필요한 호스트 도구:
- `docker-compose` (인프라용)
- `vllm` 0.19.0 (Dynamo 1.1과 함께 동봉되는 버전; `vllm/vllm-openai:v0.19.0` 컨테이너 또는 직접 설치)
- `dynamo` 1.1 Python 패키지 (frontend·worker용; NVIDIA Dynamo 설치 가이드 참고)
- `opencode` CLI (`@sst/opencode`)

## 컴포넌트

| 모듈 | 역할 |
|---|---|
| `src/testbed/swebench.py` | SWE-bench Lite/Verified/Full 로드 + 인스턴스→prompt |
| `src/testbed/poisson.py` | 포아송 도착 스케줄러 (qps λ, num_samples N) |
| `src/testbed/opencode.py` | OpenCode HTTP/SSE 클라이언트, traceparent 부착, 세션 생성 시 workspace 전달 |
| `src/testbed/jaeger.py` | Jaeger HTTP API → 같은 trace-id의 prefill/decode 워커 span duration 합산 |
| `src/testbed/trace.py` | OpenCode 응답에서 assistant turn / tool call / token 추출 |
| `src/testbed/runner.py` | per-task 비동기 실행, `trace.jsonl` + `summary.json` 생성 |
| `src/testbed/cli.py` | `python -m testbed run|analyze` |

## CLI

```bash
python -m testbed run \
  --split lite \
  --num-samples 50 \
  --qps 1.0 \
  --seed 42 \
  --router kv \
  --out results/run_001
```

`results/run_001/`:
- `trace.jsonl` — task별 raw 레코드 (assistant turns, tool calls, tokens, prefill/decode μs)
- `summary.json` — 카운트, RTT/prefill/decode/tool 시간 p50·p95
- `config.json` — 실행 파라미터

## 워커 토폴로지 (`deploy/workers.env`)

슬롯 문법: `name:gpus:tp:pp[:extra_args]`

```
PREFILL_WORKERS="p0:0,1:2:1  p1:2,3:2:1"
DECODE_WORKERS="d0:4:1:1  d1:5:1:1  d2:6:1:1"
KV_CONNECTOR=NixlConnector
```

`launch_workers.sh`는 슬롯을 순회하며 각 워커를 `python3 -m dynamo.vllm`으로 띄운다 (vLLM을 직접
`vllm serve`로 띄우면 Dynamo frontend의 file discovery에 등록되지 않아 frontend가 모든 요청에 404를
반환함). 각 프로세스에 다음을 부여한다:
- `CUDA_VISIBLE_DEVICES`
- `--model` / `--disaggregation-mode prefill|decode` / `--discovery-backend file`
- `--tensor-parallel-size` / `--pipeline-parallel-size`
- `VLLM_NIXL_SIDE_CHANNEL_HOST=localhost` + `VLLM_NIXL_SIDE_CHANNEL_PORT=$((6000 + rank * 100))` —
  vLLM NIXL 커넥터의 side-channel 포트. 기본값(5600)이 워커가 여러 개일 때 충돌하므로
  슬롯마다 100 단위로 재할당.
- `--kv-transfer-config`(`kv_role=kv_producer|kv_consumer`, `kv_rank=<slot index>`,
  `kv_parallel_size=<total slots>`, `kv_connector`)
- `--otlp-traces-endpoint` + `OTEL_SERVICE_NAME=vllm-{prefill|decode}-<name>`
- 슬롯의 `extra_args` (멀티노드 PP 인자 등)

`dynamo.vllm`은 자체 HTTP 서버를 띄우지 않는다. 클라이언트 트래픽은 frontend(`:8000`)로만 들어가고,
워커는 discovery를 통해서만 frontend와 연결된다.

토폴로지 변경은 `workers.env` 수정 + `make workers-down && make workers` 만으로 적용.

## 라우팅 비교

```bash
make sweep ROUTERS="round-robin least-loaded kv" N=50 QPS=1.0
```

각 라우터마다 `results/sweep_<router>/`에 결과 저장. 라우터 모드는 `launch_frontend.sh`의
`--router-mode`로 적용되므로 `ROUTER_MODE`를 바꿔 `make frontend`를 다시 호출해야 한다.

## 측정 정의

- **Tool 시간**: OpenCode tool part `state.completed - state.started` 합.
- **LLM 시간 (assistant turn)**: OpenCode assistant 메시지 `time.completed - time.created`.
- **Prefill 시간**: 같은 trace-id의 `vllm-prefill-*` 워커 span duration 합 (μs).
- **Decode 시간**: 같은 trace-id의 `vllm-decode-*` 워커 span duration 합 (μs).
- **RTT**: 클라이언트에서 OpenCode `POST /session/:id/message` 호출의 wall-clock.

## 알려진 제약 / 비가정 사항

1. GPU 토폴로지·NIXL 사이드채널·KV connector 선택은 운영 환경 의존 → `workers.env`에서 외부화.
2. 모델/토크나이저는 placeholder. `MODEL_NAME` 미설정 시 모든 launcher가 실패함.
3. SWE-bench 정답 채점(테스트 패치 적용)은 본 testbed 범위 밖 — workload 생성·계측에 집중.
4. **OpenCode workspace API**: `OPENCODE_EXPERIMENTAL_WORKSPACES=true`일 때 workspace 경로는
   세션 생성·메시지 전송 모두에서 `?directory=<path>` query parameter로 전달한다
   (`@opencode-ai/sdk` types.gen.ts의 `SessionCreateData` / `SessionPromptData` 검증).
   message 본문의 `model`은 문자열이 아니라 `{providerID, modelID}` 객체.
5. **traceparent 전파**: OpenCode → Dynamo → vLLM 경로에서 W3C traceparent가 그대로 전파되는지
   설치 환경별로 검증 필요. 미전파 시 vLLM 워커 측 trace-id가 분리되어 prefill/decode 매칭이 실패할 수
   있다. fallback으로 prompt 내 unique tag 또는 provider 정적 헤더 옵션 사용 검토.
6. vLLM은 Dynamo 1.1과 호환되는 v0.19.0을 가정. 다른 버전에서는 PD CLI 플래그·`--otlp-traces-endpoint`
   동작이 다를 수 있음.
7. SWE-bench 인스턴스의 실제 repo 체크아웃은 본 runner가 수행하지 않음 — workspace 디렉터리만 격리해
   에이전트가 그 안에서 `git clone` 등 도구를 호출하도록 둠.

## 테스트

```bash
pytest -q
```

단위 테스트는 외부 시스템(GPU·Dynamo·OpenCode·Jaeger) 없이 실행되도록 작성됨.
