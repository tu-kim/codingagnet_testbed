"""OpenCode HTTP client.

Endpoint shapes verified against @opencode-ai/sdk types.gen.ts:
  POST /session?directory=<ws>           body: { parentID?, title? }
  POST /session/{id}/message?directory=<ws>
       body: { parts, model?: {providerID, modelID}, agent?, system?, tools?, noReply? }
  GET  /event                            (SSE)

The workspace path is carried in the ``directory`` query parameter (enabled
when the server runs with OPENCODE_EXPERIMENTAL_WORKSPACES=true). The model
is an object, not a "provider/model" string.
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
        directory: Path | None = None,
        title: str | None = None,
        traceparent: str | None = None,
    ) -> str:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        params = {"directory": str(directory)} if directory is not None else None
        headers = {"traceparent": traceparent} if traceparent else None
        r = await self._http.post(
            "/session", json=body, params=params, headers=headers
        )
        r.raise_for_status()
        return r.json()["id"]

    async def send_message(
        self,
        session_id: str,
        prompt: str,
        *,
        provider_id: str,
        model_id: str,
        directory: Path | None = None,
        traceparent: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": prompt}],
        }
        params = {"directory": str(directory)} if directory is not None else None
        headers = {"traceparent": traceparent} if traceparent else None
        r = await self._http.post(
            f"/session/{session_id}/message",
            json=body,
            params=params,
            headers=headers,
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

    async def get_session(
        self, session_id: str, *, directory: Path | None = None
    ) -> dict[str, Any]:
        params = {"directory": str(directory)} if directory is not None else None
        r = await self._http.get(f"/session/{session_id}", params=params)
        r.raise_for_status()
        return r.json()

    async def list_messages(
        self,
        session_id: str,
        *,
        directory: Path | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """GET /session/:id/message → all messages including intermediate
        assistant turns from the agent tool loop. The synchronous response of
        POST /session/:id/message only contains the FINAL assistant message,
        so this call is needed to recover per-step token usage / tool calls.
        """
        params: dict[str, Any] = {}
        if directory is not None:
            params["directory"] = str(directory)
        if limit is not None:
            params["limit"] = limit
        r = await self._http.get(
            f"/session/{session_id}/message", params=params or None
        )
        r.raise_for_status()
        return r.json()
