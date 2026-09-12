"""The live stream: everything the API records, as it happens, to every open tab.

One process serves one workspace, so the fan-out is in memory — no broker, nothing to run. A tab that
stops reading loses events rather than blocking the API, and a reconnect catches up by asking for
what it missed (`?after=` on the run and session reads), so nothing is lost, only late.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..events import Bus
from .deps import current_person

router = APIRouter()

KEEPALIVE = 15          # seconds of quiet before a comment keeps the connection open


def bus(request: Request) -> Bus:
    return request.app.state.bus


def publish(bus_or_none: Bus | None, kind: str, data: Any) -> None:
    """Publishing is optional everywhere: a service without a bus simply does not stream."""
    if bus_or_none is not None:
        bus_or_none.publish(kind, data)


@router.get("/activity/stream", dependencies=[Depends(current_person)])
async def stream(feed: Bus = Depends(bus)) -> StreamingResponse:
    """Server-sent events: `activity`, `change`, `run` and `chat`, exactly as the screens expect."""
    queue = feed.subscribe()

    async def events():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE)
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            feed.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
