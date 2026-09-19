"""The Git screen: worktrees, branches and what would merge cleanly, read from the repositories on disk.

A project with several sources has a repository per source: `?source=<label>` reads that one, and the
first source's is read when none is named. Reading needs only a session — the same exposure `/runs`
already has. Nothing here writes: merging is
`POST /runs/{ref}/merge`, behind `runs:merge`, and approving a gate is `/approvals`, behind its own.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.git_view import MAX_COMMITS, GitViewService
from .deps import current_person, session

router = APIRouter(prefix="/projects/{pid}/git")


@router.get("", dependencies=[Depends(current_person)])
async def overview(pid: str, source: str | None = None,
                   open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The checkout, every worktree beside it, and what merging each would do. A project with no code on
    this machine answers `available: false` with the reason, not an error. `sources` lists the labels
    that may be passed as `source`."""
    return await GitViewService(open_session).overview(pid, source)


@router.get("/diff", dependencies=[Depends(current_person)])
async def diff(pid: str, branch: str, against: Literal["base", "head"] = "base", source: str | None = None,
               open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """A branch's files. `base`: what it changed since it started. `head`: what merging it now would bring."""
    return await GitViewService(open_session).diff(pid, branch, against, source)


@router.get("/conflicts", dependencies=[Depends(current_person)])
async def conflicts(pid: str, source: str | None = None,
                    open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    return await GitViewService(open_session).conflicts(pid, source)


@router.get("/commits", dependencies=[Depends(current_person)])
async def commits(pid: str, limit: int = 200, source: str | None = None,
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await GitViewService(open_session).commits(pid, min(MAX_COMMITS, max(1, limit)), source)
