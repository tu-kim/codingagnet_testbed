"""Jaeger query helpers.

Per the testbed contract, prefill_time / decode_time are derived from the
total span durations of the prefill and decode vLLM workers respectively,
correlated by shared trace-id. Service names follow the convention
``vllm-prefill-<name>`` and ``vllm-decode-<name>`` set by deploy/launch_workers.sh.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

PREFIX_PREFILL = "vllm-prefill"
PREFIX_DECODE = "vllm-decode"


@dataclass
class WorkerTiming:
    """Per-trace timing summed across all spans of a given worker class."""

    prefill_us: int = 0       # microseconds; sum of prefill-worker span durations
    decode_us: int = 0        # microseconds; sum of decode-worker span durations
    prefill_spans: int = 0
    decode_spans: int = 0


def _service_name(span: dict, processes: dict) -> str:
    pid = span.get("processID")
    if pid and pid in processes:
        return processes[pid].get("serviceName", "")
    return ""


def aggregate_worker_timing(trace: dict) -> WorkerTiming:
    """Aggregate prefill/decode durations from a Jaeger trace JSON.

    Jaeger's HTTP API returns a trace with ``spans`` (each ``duration``
    in microseconds) and ``processes`` keyed by processID with
    ``serviceName``.
    """
    spans = trace.get("spans", []) or []
    processes = trace.get("processes", {}) or {}
    out = WorkerTiming()
    for sp in spans:
        svc = _service_name(sp, processes)
        dur = int(sp.get("duration", 0) or 0)
        if svc.startswith(PREFIX_PREFILL):
            out.prefill_us += dur
            out.prefill_spans += 1
        elif svc.startswith(PREFIX_DECODE):
            out.decode_us += dur
            out.decode_spans += 1
    return out


class JaegerClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def get_trace(self, trace_id: str) -> dict | None:
        """Return the first trace dict for ``trace_id`` or None if missing."""
        r = await self._http.get(f"/api/traces/{trace_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json().get("data") or []
        return data[0] if data else None
