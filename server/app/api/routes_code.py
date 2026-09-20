"""The code index over HTTP: a summary, a lazy file tree, search, one file, impact and the graph.

Same paths and same JSON as before. Every route here is asked *inside* its project: reading needs a
session and a project this person may see — a restricted one they are not listed on answers 404, the
same as asking for the project itself — and rebuilding the index or the retrieval chunks needs
`projects:onboard` **there**, so a grant that narrows what somebody may do in one project narrows
this too. Both rebuilds read the checkout on disk and both take long enough that they answer 202 and
finish in the background — with a database session of their own, since this request's transaction is
closed by the time they run.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..ai.gateway import Gateway
from ..data.engine import Database
from ..repositories.work import ActivityRepository
from ..services import code as code_jobs
from ..services.code import INDEXING, CodeService, checkout
from ..services.errors import Refused
from ..services.identity import Person
from ..services.retrieval import BUILDING, RetrievalService
from .deps import database, gateway, hand_off, scoped, session

router = APIRouter(prefix="/projects/{pid}/code")

#: What one retrieval query may ask for, however the caller spells the number.
MAX_HITS = 20


@router.get("", dependencies=[Depends(scoped())])
async def summary(pid: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """What the index holds. A project nobody has indexed answers `indexed: false`, not an error."""
    return await CodeService(open_session).summary(pid)


@router.get("/files", dependencies=[Depends(scoped())])
async def files(pid: str, dir: str = "", open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await CodeService(open_session).tree(pid, dir)


@router.get("/search", dependencies=[Depends(scoped())])
async def search(pid: str, q: str = "",
                 open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    return await CodeService(open_session).search(pid, q)


@router.get("/file", dependencies=[Depends(scoped())])
async def file(pid: str, path: str, source: bool = False,
               open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """One file's relations, and its source only when asked for.

    The source is up to 400 KB read off disk, and the screen that opens a file wants its symbols and
    its edges — so sending the body on every click was a read and a payload nobody was using. A path
    that tries to leave the checkout is refused either way.
    """
    return await CodeService(open_session).file(pid, path, source=source)


@router.get("/impact", dependencies=[Depends(scoped())])
async def impact(pid: str, path: str | None = None, module: str | None = None,
                 obj: str | None = Query(default=None, alias="object"),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await CodeService(open_session).impact(pid, path=path, module=module, obj=obj)


@router.get("/graph", dependencies=[Depends(scoped())])
async def graph(pid: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await CodeService(open_session).graph(pid)


@router.get("/retrieval", dependencies=[Depends(scoped())])
async def retrieval(pid: str, q: str = "", limit: int = 8,
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """What retrieval holds for this project, and — with `q` — what it finds. Lexical always; by
    meaning too when a lane makes embeddings."""
    await CodeService(open_session).project(pid)
    service = RetrievalService(open_session, gw)
    if not q.strip():
        return {**await service.summary(pid), "q": q, "results": []}
    found, counts = await service.search_counted(pid, q, min(MAX_HITS, max(1, limit)))
    return {**await service.summary(pid), "q": q, "results": found, "counts": counts}


@router.get("/retrieval/docs", dependencies=[Depends(scoped())])
async def retrieval_docs(pid: str, open_session: AsyncSession = Depends(session),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """The repository's own writing: what retrieval holds of it, and what on disk it does not hold yet."""
    return await CodeService(open_session).docs(pid, gw)


@router.get("/retrieval/doc", dependencies=[Depends(scoped())])
async def retrieval_doc(pid: str, path: str, open_session: AsyncSession = Depends(session),
                        gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """One document, its sections, and the symbols, files and refs it names that really exist."""
    return await CodeService(open_session).doc(pid, path, gw)


@router.post("/retrieval/build", status_code=202)
async def build_retrieval(pid: str, jobs: BackgroundTasks,
                          who: Person = Depends(scoped("projects:onboard")),
                          open_session: AsyncSession = Depends(session),
                          db: Database = Depends(database),
                          gw: Gateway = Depends(gateway)) -> dict[str, bool]:
    project = await CodeService(open_session).project(pid)
    if pid in BUILDING:
        raise Refused(f"Retrieval for {project.name} is being built already")
    await ActivityRepository(open_session).record(
        actor=who.name, actor_kind="human", action="Building retrieval",
        detail=f"{project.name} · chunking, then embedding what it can", project_id=pid)
    # Marked before the answer, not when the job starts: a screen that asks the moment it hears 202
    # must already be told a build is running, or it stops waiting before the build begins.
    BUILDING.add(pid)
    await hand_off(open_session, jobs, _build_retrieval, db, gw, pid)
    return {"ok": True}


async def _build_retrieval(db: Database, gw: Gateway, pid: str) -> None:
    """The build, and the mark taken off however it ends — including a job that fails before it reaches
    the build itself."""
    try:
        await code_jobs.build_retrieval(db, gw, pid)
    finally:
        BUILDING.discard(pid)


@router.post("/reindex", status_code=202)
async def reindex(pid: str, jobs: BackgroundTasks, who: Person = Depends(scoped("projects:onboard")),
                  open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                  gw: Gateway = Depends(gateway)) -> dict[str, bool]:
    """Read the code again. Answered the moment the job is queued, not when it finishes."""
    project = await CodeService(open_session).project(pid)
    root = checkout(project)
    if root is None:
        raise Refused(f"{project.name} has no code on this machine. Onboard a repository to index one.")
    if not await asyncio.to_thread(root.is_dir):
        raise Refused(f"The code for {project.name} is no longer at {onboarding.redact(str(root))}")
    if pid in INDEXING:
        raise Refused(f"{project.name} is being indexed already")
    await ActivityRepository(open_session).record(
        actor=who.name, actor_kind="human", action="Re-indexing",
        detail=f"{project.name} · reading the code again", project_id=pid)
    await hand_off(open_session, jobs, code_jobs.reindex, db, gw, pid, root,
                   list(project.excluded or []))
    return {"ok": True}
