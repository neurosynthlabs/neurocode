"""The extension screens: skills, plugins, commands and hooks, discovered on disk and switched on or off.

Every route here reads. What is found comes off disk on each request (in a thread), so a skill written a
minute ago shows up without anything being imported; the switches themselves are prefs, written through
`PUT /prefs/{key}` like every other screen setting. A key the screen sends back is looked up among what
discovery found — it is never turned into a path.

Skills, commands and plugins are catalogues — a name and a description — and every role is meant to read
them. A hook is not: it is a command line configured on this machine, and the redaction that takes the
secret-looking parts out of one is a best effort, not a fence. So the hooks list alone asks for
`settings:write`, the right that already governs switching hooks on and off.

`projectId` may be left out. A new workspace has no project, and the Claude home on this machine is real
before one exists, so without it the screens read that home alone and count no session.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.extensions import ExtensionService
from .deps import current_person, require, session

router = APIRouter(prefix="/extensions", dependencies=[Depends(current_person)])

PROJECT = Query(default=None, alias="projectId", min_length=1, max_length=80)


@router.get("/skills")
async def skills(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Every skill this project's sessions can see, with the loads sessions really made."""
    return await ExtensionService(open_session).skills(project_id)


@router.get("/skills/detail")
async def skill(project_id: str | None = PROJECT, key: str = Query(min_length=1, max_length=400),
                open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ExtensionService(open_session).skill(project_id, key)


@router.get("/commands")
async def commands(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ExtensionService(open_session).commands(project_id)


@router.get("/hooks", dependencies=[Depends(require("settings:write"))])
async def hooks(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The hooks Claude Code would run here, with anything that looks like a secret taken out."""
    return await ExtensionService(open_session).hooks(project_id)


@router.get("/plugins")
async def plugins(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ExtensionService(open_session).plugins(project_id)
