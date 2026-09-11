"""Liveness, the agent roster, and the activity log with its live stream."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..auth import User, current_user
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


@router.get("/usage")
async def usage(days: int = 30, user: User = Depends(current_user), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """What the AI gateway did, from its ledger: every model call and every offline answer."""
    return await asyncio.to_thread(_usage, c, max(1, min(days, 365)), user.can("workspace:admin"))


def _usage(c: Ctx, days: int, admin: bool) -> dict[str, Any]:
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    s = c.store
    t = s.row("SELECT COUNT(*), COALESCE(SUM(provider != 'rules'), 0), COALESCE(SUM(provider = 'rules'), 0), "
              "COALESCE(SUM(ok = 0), 0), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0), COALESCE(AVG(ms), 0) "
              "FROM ai_calls WHERE at >= ?", (since,))
    out: dict[str, Any] = {
        "days": days,
        "totals": {"calls": t[0], "modelCalls": t[1], "offline": t[2], "failures": t[3], "tokensIn": t[4],
                   "tokensOut": t[5], "avgMs": round(t[6])},
        "byDay": [{"day": r[0], "calls": r[1], "model": r[2], "offline": r[3], "tokens": r[4]} for r in s.rows(
            "SELECT substr(at, 1, 10) AS day, COUNT(*), SUM(provider != 'rules'), SUM(provider = 'rules'), "
            "SUM(tokens_in + tokens_out) FROM ai_calls WHERE at >= ? GROUP BY day ORDER BY day", (since,))],
        "byFeature": [{"feature": r[0], "calls": r[1], "model": r[2], "offline": r[3], "failures": r[4], "tokensIn": r[5],
                       "tokensOut": r[6], "avgMs": round(r[7])} for r in s.rows(
            "SELECT feature, COUNT(*), SUM(provider != 'rules'), SUM(provider = 'rules'), SUM(ok = 0), SUM(tokens_in), "
            "SUM(tokens_out), AVG(ms) FROM ai_calls WHERE at >= ? GROUP BY feature ORDER BY COUNT(*) DESC", (since,))],
        "byProvider": [{"provider": r[0], "model": r[1], "calls": r[2], "failures": r[3], "tokensIn": r[4], "tokensOut": r[5],
                        "avgMs": round(r[6])} for r in s.rows(
            "SELECT provider, model, COUNT(*), SUM(ok = 0), SUM(tokens_in), SUM(tokens_out), AVG(ms) FROM ai_calls "
            "WHERE at >= ? GROUP BY provider, model ORDER BY COUNT(*) DESC", (since,))],
        "recent": [{"at": r[0], "feature": r[1], "provider": r[2], "model": r[3], "ok": bool(r[4]), "ms": r[5],
                    "tokensIn": r[6], "tokensOut": r[7], "error": r[8], "by": r[9] if admin else None} for r in s.rows(
            "SELECT a.at, a.feature, a.provider, a.model, a.ok, a.ms, a.tokens_in, a.tokens_out, a.error, u.name "
            "FROM ai_calls a LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT 25")],
    }
    if admin:  # who uses what is for admins; everyone else sees the totals
        out["byPerson"] = [{"name": r[0], "calls": r[1], "tokens": r[2]} for r in s.rows(
            "SELECT COALESCE(u.name, 'Nobody signed in'), COUNT(*), SUM(a.tokens_in + a.tokens_out) FROM ai_calls a "
            "LEFT JOIN users u ON u.id = a.user_id WHERE a.at >= ? GROUP BY a.user_id ORDER BY COUNT(*) DESC", (since,))]
    return out


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
