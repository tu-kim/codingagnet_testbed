"""Trace extraction: combine OpenCode session payload with Jaeger spans.

We avoid prescribing the exact OpenCode message-part schema (it is owned by
``@opencode-ai/sdk``); instead we walk the structure defensively and pull out
recognisable fields. Unknown shapes are passed through as ``raw``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .jaeger import WorkerTiming


@dataclass
class ToolCall:
    tool: str
    call_id: str | None
    started_at: float | None
    completed_at: float | None
    status: str | None
    input: Any = None
    output: Any = None


@dataclass
class AssistantTurn:
    message_id: str | None
    created_at: float | None
    completed_at: float | None
    tokens: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class TaskRecord:
    instance_id: str
    session_id: str
    trace_id: str
    arrival_offset_s: float
    started_at: float
    completed_at: float
    rtt_s: float
    assistant_turns: list[AssistantTurn] = field(default_factory=list)
    prefill_us: int = 0
    decode_us: int = 0
    prefill_spans: int = 0
    decode_spans: int = 0
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["assistant_turns"] = [asdict(t) for t in self.assistant_turns]
        return d


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def extract_assistant_turns(session_or_messages: Any) -> list[AssistantTurn]:
    """Walk an OpenCode session / messages payload and emit assistant turns.

    The function recognises common shapes: a list of message dicts, a session
    dict with a ``messages`` field, or a single message dict. Unknown fields
    are ignored rather than guessed at.
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
            tokens=info.get("tokens", {}) or {},
        )
        for part in m.get("parts", info.get("parts", [])) or []:
            ptype = part.get("type")
            if ptype == "text":
                turn.text += part.get("text", "")
            elif ptype == "tool":
                state = part.get("state", {}) or {}
                turn.tool_calls.append(
                    ToolCall(
                        tool=part.get("tool", ""),
                        call_id=part.get("callID") or part.get("call_id"),
                        started_at=_as_float(state.get("started")),
                        completed_at=_as_float(state.get("completed")),
                        status=state.get("status"),
                        input=state.get("input"),
                        output=state.get("output"),
                    )
                )
        out.append(turn)
    return out


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
