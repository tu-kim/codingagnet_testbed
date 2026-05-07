"""Workload runner.

Drives a Poisson stream of SWE-bench samples into OpenCode, isolating each
request into its own workspace, and joins the resulting OpenCode session
record with vLLM PD spans collected from Jaeger by trace-id.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from .config import Settings
from .jaeger import JaegerClient, aggregate_worker_timing
from .opencode import OpenCodeClient, gen_traceparent
from .poisson import arrivals
from .swebench import Sample, load_samples, render_prompt
from .trace import (
    TaskRecord,
    extract_assistant_turns,
    extract_user_messages,
    merge_timing,
)


async def _run_one(
    sample: Sample,
    arrival_offset: float,
    *,
    settings: Settings,
    oc: OpenCodeClient,
    jg: JaegerClient,
    workspace_root: Path,
    provider_id: str,
    jaeger_lookup_delay_s: float,
) -> TaskRecord:
    workspace = workspace_root / f"{sample.instance_id}-{uuid.uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=True)

    traceparent, trace_id = gen_traceparent()
    started = time.time()
    rec = TaskRecord(
        instance_id=sample.instance_id,
        session_id="",
        trace_id=trace_id,
        arrival_offset_s=arrival_offset,
        started_at=started,
        completed_at=started,
        rtt_s=0.0,
    )
    try:
        session_id = await oc.create_session(
            directory=workspace,
            title=sample.instance_id,
            traceparent=traceparent,
        )
        rec.session_id = session_id
        prompt = render_prompt(sample)
        t0 = time.monotonic()
        await oc.send_message(
            session_id,
            prompt,
            provider_id=provider_id,
            model_id=settings.model_name,
            directory=workspace,
            traceparent=traceparent,
        )
        rec.rtt_s = time.monotonic() - t0
        rec.completed_at = time.time()
        # The synchronous POST /session/:id/message response only carries the
        # FINAL assistant message. Fetch the full message list so we capture
        # every step of the agent tool loop with its own token usage.
        messages = await oc.list_messages(session_id, directory=workspace)
        rec.user_messages = extract_user_messages(messages)
        rec.assistant_turns = extract_assistant_turns(messages)

        if jaeger_lookup_delay_s > 0:
            await asyncio.sleep(jaeger_lookup_delay_s)
        trace = await jg.get_trace(trace_id)
        if trace is not None:
            merge_timing(rec, aggregate_worker_timing(trace))
    except Exception as e:  # surface but don't kill the run
        rec.completed_at = time.time()
        rec.error = f"{type(e).__name__}: {e}"
    return rec


async def run(
    *,
    split: str,
    num_samples: int,
    qps: float,
    seed: int,
    out_dir: Path,
    settings: Settings,
    provider_id: str = "local",
    jaeger_lookup_delay_s: float = 2.0,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = settings.workspace_root / out_dir.name
    workspace_root.mkdir(parents=True, exist_ok=True)

    samples = load_samples(split, num_samples)

    oc = OpenCodeClient(settings.opencode_url, settings.opencode_password)
    jg = JaegerClient(settings.jaeger_query_url)
    trace_path = out_dir / "trace.jsonl"

    tasks: list[asyncio.Task[TaskRecord]] = []
    try:
        async for idx, sample, off in arrivals(samples, qps=qps, seed=seed):
            t = asyncio.create_task(
                _run_one(
                    sample,
                    off,
                    settings=settings,
                    oc=oc,
                    jg=jg,
                    workspace_root=workspace_root,
                    provider_id=provider_id,
                    jaeger_lookup_delay_s=jaeger_lookup_delay_s,
                )
            )
            tasks.append(t)
        results = await asyncio.gather(*tasks)
    finally:
        await oc.close()
        await jg.close()

    with trace_path.open("w") as f:
        for rec in results:
            f.write(json.dumps(rec.to_json()) + "\n")

    summary = _summarize(results)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return trace_path


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def _summarize(results: list[TaskRecord]) -> dict:
    rtts = [r.rtt_s for r in results if r.error is None]
    prefill_ms = [r.prefill_us / 1000.0 for r in results if r.prefill_us]
    decode_ms = [r.decode_us / 1000.0 for r in results if r.decode_us]
    tool_total_s = []
    for r in results:
        s = 0.0
        for t in r.assistant_turns:
            for c in t.tool_calls:
                if c.started_at and c.completed_at:
                    s += max(0.0, c.completed_at - c.started_at)
        tool_total_s.append(s)
    return {
        "count": len(results),
        "errors": sum(1 for r in results if r.error),
        "rtt_s": {"p50": _percentile(rtts, 50), "p95": _percentile(rtts, 95)},
        "prefill_ms": {
            "p50": _percentile(prefill_ms, 50),
            "p95": _percentile(prefill_ms, 95),
        },
        "decode_ms": {
            "p50": _percentile(decode_ms, 50),
            "p95": _percentile(decode_ms, 95),
        },
        "tool_time_s": {
            "p50": _percentile(tool_total_s, 50),
            "p95": _percentile(tool_total_s, 95),
        },
    }
