"""Liveness, the agent roster, and the activity log with its live stream."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..auth import current_user
from ..context import Ctx, ctx
from ..db import TABLES

router = APIRouter()


@router.get("/health")
async def health(c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return {"ok": True, "db": c.store.path, "counts": {t: c.store.count(t) for t in TABLES},
            "needsSetup": c.accounts.count() == 0, "compiler": await asyncio.to_thread(c.gateway.status)}


@router.get("/agents", dependencies=[Depends(current_user)])
async def agents(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.all("agents")


@router.get("/activity", dependencies=[Depends(current_user)])
async def activity(limit: int = 200, c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.activity(max(1, min(limit, 1000)))


@router.get("/activity/stream", dependencies=[Depends(current_user)])
async def stream(c: Ctx = Depends(ctx)) -> StreamingResponse:
    q = c.bus.subscribe()

    async def events():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    kind, data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            c.bus.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
