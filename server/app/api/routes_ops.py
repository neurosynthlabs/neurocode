"""The DevOps screen: the health of this machine's workspace, its logs, its secrets and its pipeline.

Every answer is worked out when it is asked for — probes run, git is asked, the database is read — and
nothing is stored. Reading needs `ops:read`; the container list also needs `machine:access`, because
what is running is the machine's business and not the workspace's; the secrets list needs
`workspace:admin`, like the AI providers screen it mirrors, even though it names keys and never shows
one. The actions this screen offers (accept, merge, discard, back up) are the routes that already own
them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..services.ops import MAX_LOGS, OpsService
from .deps import gateway, require, session

router = APIRouter(prefix="/ops")

MAX_DELIVERIES = 100


def _service(request: Request, open_session: AsyncSession, gw: Gateway) -> OpsService:
    return OpsService(open_session, gw, request.app.state.settings)


@router.get("/overview", dependencies=[Depends(require("ops:read"))])
async def overview(request: Request, open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _service(request, open_session, gw).overview(request.app.version)


@router.get("/deliveries", dependencies=[Depends(require("ops:read"))])
async def deliveries(request: Request, limit: int = Query(default=50, ge=1), offset: int = Query(default=0, ge=0),
                     open_session: AsyncSession = Depends(session),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _service(request, open_session, gw).deliveries(min(limit, MAX_DELIVERIES), offset)


@router.get("/pipeline", dependencies=[Depends(require("ops:read"))])
async def pipeline(request: Request, run: str | None = None, open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _service(request, open_session, gw).pipeline(run)


@router.get("/logs", dependencies=[Depends(require("ops:read"))])
async def logs(request: Request, level: Literal["info", "ok", "warn", "err", "debug"] | None = None,
               before: str | None = Query(default=None, max_length=120),
               limit: int = Query(default=200, ge=1, le=MAX_LOGS),
               open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _service(request, open_session, gw).logs(level, before, limit)


@router.get("/containers", dependencies=[Depends(require("ops:read", "machine:access"))])
async def containers(request: Request, open_session: AsyncSession = Depends(session),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _service(request, open_session, gw).containers()


@router.get("/secrets", dependencies=[Depends(require("workspace:admin"))])
async def secrets(request: Request, open_session: AsyncSession = Depends(session),
                  gw: Gateway = Depends(gateway)) -> list[dict[str, Any]]:
    return await _service(request, open_session, gw).secrets()
