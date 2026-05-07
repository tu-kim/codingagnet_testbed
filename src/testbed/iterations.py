"""Per-iteration analysis views built from an OpenCode session message list.

OpenCode bundles one user prompt's full agent loop into a single assistant
``Message`` whose ``parts`` array contains zero or more
``step-start ... step-finish`` ranges. Each such range is **one underlying
LLM round trip** — what we call an "iteration" here.

Two views, sharing the same step walk:

  build_iteration_summary    → token / duration distribution per iteration
  build_iteration_transcript → input messages & output text/tool trace per iteration

Both deliberately avoid prescribing fields that OpenCode does not actually
emit (e.g. step-start/step-finish carry no time field), so durations are
derived from the parts inside the step (text/reasoning ``time`` and
tool ``state.time``).
"""
from __future__ import annotations

import json
from typing import Any, Iterator


def _coerce_messages(obj: Any) -> list[dict]:
    if obj is None:
        return []
    if isinstance(obj, list):
        return [m for m in obj if isinstance(m, dict)]
    if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
        return [m for m in obj["messages"] if isinstance(m, dict)]
    return []


def _walk_steps(parts: list[dict]) -> Iterator[list[dict]]:
    """Yield each step's parts (between step-start and step-finish, inclusive).
    A step that has no step-finish is yielded only if it began with step-start.
    """
    current: list[dict] = []
    in_step = False
    for p in parts:
        t = p.get("type")
        if t == "step-start":
            if in_step and current:
                yield current
            current = [p]
            in_step = True
        elif t == "step-finish":
            if not in_step:
                # orphan step-finish (shouldn't happen; emit standalone)
                current.append(p)
                yield current
                current, in_step = [], False
                continue
            current.append(p)
            yield current
            current, in_step = [], False
        else:
            if in_step:
                current.append(p)
    if in_step and current:
        yield current


def _step_finish(step_parts: list[dict]) -> dict:
    for p in step_parts:
        if p.get("type") == "step-finish":
            return p
    return {}


def _step_start_id(step_parts: list[dict]) -> str | None:
    for p in step_parts:
        if p.get("type") == "step-start":
            return p.get("id")
    sf = _step_finish(step_parts)
    return sf.get("id") if sf else None


def _time_range(parts: list[dict], types: tuple[str, ...]) -> tuple[Any, Any]:
    starts: list[float] = []
    ends: list[float] = []
    for p in parts:
        if p.get("type") not in types:
            continue
        if p.get("type") == "tool":
            t = (p.get("state", {}) or {}).get("time", {}) or {}
        else:
            t = p.get("time", {}) or {}
        if t.get("start") is not None:
            starts.append(t["start"])
        if t.get("end") is not None:
            ends.append(t["end"])
    return (min(starts) if starts else None, max(ends) if ends else None)


def _conversation_roles(steps: list[list[dict]], idx: int) -> list[str]:
    """Approximate role list fed to the LLM at step `idx`. The LLM at step 1
    sees [system, user]; subsequent steps additionally see one ``assistant``
    plus one ``tool`` per tool call from each prior step."""
    roles = ["system", "user"]
    for k in range(idx):
        roles.append("assistant")
        n_tools = sum(1 for p in steps[k] if p.get("type") == "tool")
        roles.extend(["tool"] * n_tools)
    return roles


def _user_prompt_text(messages: list[dict]) -> str:
    out: list[str] = []
    for m in messages:
        info = m.get("info", m)
        if info.get("role") != "user":
            continue
        for p in m.get("parts", info.get("parts", [])) or []:
            if p.get("type") == "text":
                out.append(p.get("text", "") or "")
    return "".join(out)


def _system_prompt(messages: list[dict]) -> str | None:
    """Pick the system prompt off the earliest user message that carries one.

    OpenCode's user-message ``info.system`` is typed as ``string[]`` (one
    entry per system block: agent instructions, tool descriptions, etc.),
    but older fixtures and some builds use a plain string. Normalize both
    so transcript consumers always see a single string.
    """
    for m in messages:
        info = m.get("info", m)
        if info.get("role") != "user":
            continue
        sys = info.get("system")
        if not sys:
            continue
        if isinstance(sys, (list, tuple)):
            joined = "\n\n".join(str(s) for s in sys if s)
            if joined:
                return joined
            continue
        if isinstance(sys, str):
            return sys
        return json.dumps(sys)
    return None


def _collect_steps(messages: list[dict]) -> tuple[list[list[dict]], str | None]:
    """Flatten step ranges across all assistant messages, return (steps, session_id)."""
    steps: list[list[dict]] = []
    session_id: str | None = None
    for m in messages:
        info = m.get("info", m)
        if info.get("role") != "assistant":
            continue
        session_id = session_id or info.get("sessionID") or info.get("sessionId")
        parts = m.get("parts", info.get("parts", [])) or []
        for s in _walk_steps(parts):
            steps.append(s)
    return steps, session_id


def build_iteration_summary(
    messages: Any, sample_info: dict | None = None
) -> dict:
    """Token / duration distribution per iteration. No raw text."""
    msgs = _coerce_messages(messages)
    steps, session_id = _collect_steps(msgs)

    iterations: list[dict] = []
    for idx, step_parts in enumerate(steps):
        sf = _step_finish(step_parts)
        tokens = sf.get("tokens", {}) or {}
        cache = tokens.get("cache", {}) or {}

        text_start, text_end = _time_range(step_parts, ("text", "reasoning"))
        all_start, all_end = _time_range(step_parts, ("text", "reasoning", "tool"))
        llm_duration = (text_end - text_start) if (text_start is not None and text_end is not None) else None

        items: list[dict] = []
        # One LLM emission entry per step (text + reasoning collapsed).
        # vLLM's --reasoning-parser surfaces <think> content separately, but
        # the OpenAI-compatible usage.completion_tokens still counts every
        # generated token (reasoning + final answer). We don't expose a
        # separate `reasoning_tokens` field because OpenCode reports it as
        # 0 in that path — the reasoning is already inside `output_tokens`.
        items.append({
            "type": "text",
            "output_tokens": tokens.get("output"),
            "started_at": text_start,
            "completed_at": text_end,
            "llm_duration_ms": llm_duration,
        })
        # One entry per tool call
        for p in step_parts:
            if p.get("type") != "tool":
                continue
            state = p.get("state", {}) or {}
            t = state.get("time", {}) or {}
            dur = (t["end"] - t["start"]) if (t.get("start") is not None and t.get("end") is not None) else None
            items.append({
                "type": "tool",
                "tool": p.get("tool"),
                "started_at": t.get("start"),
                "completed_at": t.get("end"),
                "tool_duration_ms": dur,
            })

        iterations.append({
            "iteration_index": idx,
            "request_id": _step_start_id(step_parts),
            "session_id": session_id,
            "started_at": all_start,
            "completed_at": all_end,
            "input": {
                "token_count": tokens.get("input"),
                "roles": _conversation_roles(steps, idx),
            },
            "output": {
                "cache_tokens": {
                    "read": cache.get("read"),
                    "write": cache.get("write"),
                },
                "items": items,
            },
        })

    return {
        "sample": sample_info or {},
        "session_id": session_id,
        "iterations": iterations,
    }


def build_iteration_transcript(
    messages: Any, sample_info: dict | None = None
) -> dict:
    """Per-iteration input/output text trace."""
    msgs = _coerce_messages(messages)
    steps, session_id = _collect_steps(msgs)

    history: list[dict] = []
    sys = _system_prompt(msgs)
    if sys:
        history.append({"role": "system", "text": sys})
    history.append({"role": "user", "text": _user_prompt_text(msgs)})

    iterations: list[dict] = []
    for idx, step_parts in enumerate(steps):
        input_messages = list(history)

        items: list[dict] = []
        assistant_text: list[str] = []
        tool_history_entries: list[dict] = []
        for p in step_parts:
            t = p.get("type")
            if t == "reasoning":
                items.append({"type": "reason", "text": p.get("text", "") or ""})
            elif t == "text":
                items.append({"type": "text", "text": p.get("text", "") or ""})
                assistant_text.append(p.get("text", "") or "")
            elif t == "tool":
                state = p.get("state", {}) or {}
                items.append({
                    "type": "tool",
                    "tool": p.get("tool"),
                    "input": state.get("input"),
                    "output": state.get("output"),
                })
                tool_history_entries.append({
                    "role": "tool",
                    "text": (state.get("output") or "") if isinstance(state.get("output"), str) else "",
                })

        iterations.append({
            "iteration_index": idx,
            "request_id": _step_start_id(step_parts),
            "session_id": session_id,
            "input": {"messages": input_messages},
            "output": {"items": items},
        })

        history.append({"role": "assistant", "text": "".join(assistant_text)})
        history.extend(tool_history_entries)

    return {
        "sample": sample_info or {},
        "session_id": session_id,
        "iterations": iterations,
    }
