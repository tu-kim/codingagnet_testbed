"""Trace extraction: combine OpenCode session payload with Jaeger spans.

We avoid prescribing the exact OpenCode message-part schema (it is owned by
``@opencode-ai/sdk``); instead we walk the structure defensively and pull out
recognisable fields. Unknown shapes are skipped rather than guessed at.

The shapes captured here mirror the SDK's public types as of dev (2026-05):
  AssistantMessage: id, modelID, providerID, mode, cost, finish, error,
                    tokens{input,output,reasoning,cache.{read,write}},
                    time{created,completed?}
  Part union:       text, reasoning, tool, step-start, step-finish,
                    snapshot, patch, retry, compaction, file, agent, subtask
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .jaeger import WorkerTiming


@dataclass
class ToolCall:
    tool: str
    call_id: str | None
    status: str | None
    started_at: float | None
    completed_at: float | None
    title: str | None = None        # short human-readable label set by the tool
    input: Any = None               # the raw arguments the model emitted
    output: Any = None              # tool stdout/return value (only when completed)
    error: str | None = None        # populated when status == "error"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepFinish:
    """A step-finish boundary inside a single AssistantMessage. Each represents
    one underlying LLM round trip with its own token / cost accounting."""
    reason: str | None
    cost: float | None = None
    tokens: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssistantTurn:
    message_id: str | None
    created_at: float | None
    completed_at: float | None
    model_id: str | None = None
    provider_id: str | None = None
    mode: str | None = None
    cost: float | None = None
    finish: str | None = None       # finish reason (stop / length / tool_use / ...)
    error: Any = None               # typed error object if the turn failed
    tokens: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    reasoning_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    steps: list[StepFinish] = field(default_factory=list)
    retries: int = 0
    compactions: int = 0
    patched_files: list[str] = field(default_factory=list)


@dataclass
class UserMessage:
    """A user-role message in the OpenCode session — typically the initial
    prompt and any subsequent follow-ups. Captures the system prompt and
    tool allow-list since those govern the agent's behavior."""
    message_id: str | None
    created_at: float | None
    text: str = ""                  # concatenated TextPart contents
    agent: str | None = None        # agent name OpenCode dispatched to
    model_id: str | None = None
    provider_id: str | None = None
    system: str | None = None       # system prompt sent to the model
    tools: dict[str, bool] = field(default_factory=dict)
    summary: dict[str, Any] | None = None


@dataclass
class TaskRecord:
    instance_id: str
    session_id: str
    trace_id: str
    arrival_offset_s: float
    started_at: float
    completed_at: float
    rtt_s: float
    user_messages: list[UserMessage] = field(default_factory=list)
    assistant_turns: list[AssistantTurn] = field(default_factory=list)
    prefill_us: int = 0
    decode_us: int = 0
    prefill_spans: int = 0
    decode_spans: int = 0
    error: str | None = None
    # Raw OpenCode messages list, kept for downstream views
    # (build_iteration_summary / build_iteration_transcript). Not serialized
    # into trace.jsonl to keep that file compact.
    messages_raw: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("messages_raw", None)
        d["user_messages"] = [asdict(u) for u in self.user_messages]
        d["assistant_turns"] = [asdict(t) for t in self.assistant_turns]
        return d


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def extract_assistant_turns(session_or_messages: Any) -> list[AssistantTurn]:
    """Walk an OpenCode session / messages payload and emit assistant turns.

    Recognises (a) a list of message dicts, (b) a session dict with a
    ``messages`` field, or (c) a single message dict. Unknown fields are
    ignored rather than guessed at.
    """
    msgs = _coerce_messages(session_or_messages)
    out: list[AssistantTurn] = []
    for m in msgs:
        info = m.get("info", m)
        if info.get("role") != "assistant":
            continue
        time_obj = info.get("time", {}) or {}
        turn = AssistantTurn(
            message_id=info.get("id"),
            created_at=_as_float(time_obj.get("created")),
            completed_at=_as_float(time_obj.get("completed")),
            model_id=info.get("modelID"),
            provider_id=info.get("providerID"),
            mode=info.get("mode"),
            cost=_as_float(info.get("cost")),
            finish=info.get("finish"),
            error=info.get("error"),
            tokens=info.get("tokens", {}) or {},
        )
        for part in m.get("parts", info.get("parts", [])) or []:
            _absorb_part(turn, part)
        out.append(turn)
    return out


def extract_user_messages(session_or_messages: Any) -> list[UserMessage]:
    """Walk an OpenCode session / messages payload and emit user messages
    along with their system prompt and tool allow-list."""
    msgs = _coerce_messages(session_or_messages)
    out: list[UserMessage] = []
    for m in msgs:
        info = m.get("info", m)
        if info.get("role") != "user":
            continue
        time_obj = info.get("time", {}) or {}
        model = info.get("model") or {}
        u = UserMessage(
            message_id=info.get("id"),
            created_at=_as_float(time_obj.get("created")),
            agent=info.get("agent"),
            model_id=model.get("modelID") if isinstance(model, dict) else None,
            provider_id=model.get("providerID") if isinstance(model, dict) else None,
            system=info.get("system"),
            tools=info.get("tools") or {},
            summary=info.get("summary"),
        )
        for part in m.get("parts", info.get("parts", [])) or []:
            if part.get("type") == "text":
                u.text += part.get("text", "") or ""
        out.append(u)
    return out


def _absorb_part(turn: AssistantTurn, part: dict[str, Any]) -> None:
    ptype = part.get("type")
    if ptype == "text":
        turn.text += part.get("text", "") or ""
    elif ptype == "reasoning":
        turn.reasoning_text += part.get("text", "") or ""
    elif ptype == "tool":
        state = part.get("state", {}) or {}
        time_obj = state.get("time", {}) or {}
        # SDK uses time.start/time.end (ToolStateRunning/Completed/Error);
        # earlier code paths used "started"/"completed" — accept both.
        started = time_obj.get("start", state.get("started"))
        ended = time_obj.get("end", state.get("completed"))
        turn.tool_calls.append(
            ToolCall(
                tool=part.get("tool", "") or "",
                call_id=part.get("callID") or part.get("call_id"),
                status=state.get("status"),
                started_at=_as_float(started),
                completed_at=_as_float(ended),
                title=state.get("title"),
                input=state.get("input"),
                output=state.get("output"),
                error=state.get("error"),
                metadata=state.get("metadata") or {},
            )
        )
    elif ptype == "step-finish":
        turn.steps.append(
            StepFinish(
                reason=part.get("reason"),
                cost=_as_float(part.get("cost")),
                tokens=part.get("tokens", {}) or {},
            )
        )
    elif ptype == "retry":
        turn.retries += 1
    elif ptype == "compaction":
        turn.compactions += 1
    elif ptype == "patch":
        for f in part.get("files", []) or []:
            if isinstance(f, str):
                turn.patched_files.append(f)
    # step-start / snapshot / file / agent / subtask: not currently captured
    # but easy to add here as new fields if a use case appears.


def _coerce_messages(obj: Any) -> list[dict[str, Any]]:
    if obj is None:
        return []
    if isinstance(obj, list):
        return [m for m in obj if isinstance(m, dict)]
    if isinstance(obj, dict):
        if "messages" in obj and isinstance(obj["messages"], list):
            return [m for m in obj["messages"] if isinstance(m, dict)]
        # single message
        if "role" in obj or "info" in obj:
            return [obj]
    return []


def merge_timing(record: TaskRecord, t: WorkerTiming) -> None:
    record.prefill_us = t.prefill_us
    record.decode_us = t.decode_us
    record.prefill_spans = t.prefill_spans
    record.decode_spans = t.decode_spans
