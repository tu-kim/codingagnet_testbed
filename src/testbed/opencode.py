"""OpenCode HTTP client.

We deliberately keep the wire layer thin and pass through opaque payloads:
the exact field names for the workspace key and the message-part shape are
defined by ``@opencode-ai/sdk`` types.gen.ts; we forward whatever the caller
provides without inventing missing fields.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from httpx_sse import aconnect_sse


def gen_traceparent() -> tuple[str, str]:
    """Return (traceparent_header, trace_id_hex) per W3C Trace Context."""
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01", trace_id


@dataclass
class SessionResult:
    session_id: str
    trace_id: str
    started_at: float
    completed_at: float
    final_response: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)


class OpenCodeClient:
    def __init__(self, base_url: str, password: str = "", timeout: float = 600.0):
        auth = httpx.BasicAuth("opencode", password) if password else None
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=auth,
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def create_session(
        self,
        *,
        workspace: Path | None = None,
        workspace_field: str = "workspace",
        title: str | None = None,
        traceparent: str | None = None,
    ) -> str:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        if workspace is not None:
            body[workspace_field] = str(workspace)
        headers = {"traceparent": traceparent} if traceparent else None
        r = await self._http.post("/session", json=body, headers=headers)
        r.raise_for_status()
        return r.json()["id"]

    async def send_message(
        self,
        session_id: str,
        prompt: str,
        *,
        model: str,
        traceparent: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "model": model,
            "parts": [{"type": "text", "text": prompt}],
        }
        headers = {"traceparent": traceparent} if traceparent else None
        r = await self._http.post(
            f"/session/{session_id}/message", json=body, headers=headers
        )
        r.raise_for_status()
        return r.json()

    @contextlib.asynccontextmanager
    async def subscribe_events(self) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue()
        stop = asyncio.Event()

        async def reader() -> None:
            async with aconnect_sse(self._http, "GET", "/event") as src:
                async for sse in src.aiter_sse():
                    if stop.is_set():
                        return
                    try:
                        ev = json.loads(sse.data)
                    except json.JSONDecodeError:
                        ev = {"raw": sse.data}
                    await queue.put(ev)

        task = asyncio.create_task(reader())
        try:
            yield queue
        finally:
            stop.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def export_session(self, session_id: str) -> dict[str, Any]:
        """Best-effort: fetch full session via GET /session/:id; the exact
        representation is server-defined."""
        r = await self._http.get(f"/session/{session_id}")
        r.raise_for_status()
        return r.json()
