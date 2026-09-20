"""Agent runs over HTTP: what they did, their output, the real diff, and stopping, merging, pushing,
discarding, reviewing again, sending one back for changes or taking it back to one of its steps.

Same paths and same JSON as before. What changed underneath: a run's steps, logs, children and
collisions are rows now, so this file reads them in a fixed number of queries however many runs are
on screen — not one query per run.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..models import Plan, Project, Run, Task
from ..repositories import AuditRepository, NotFound, RunLogRepository, RunRepository
from ..schemas import run_json, run_log_json
from ..services.identity import Person
from ..services import runs as runtime
from ..services.runs import RunService
from .deps import current_person, database, gateway, hand_off, must_see, require, session, unseen_by

router = APIRouter(prefix="/runs")


class ReworkIn(BaseModel):
    notes: str = Field(min_length=1, max_length=4000)


class RevertIn(BaseModel):
    #: False: the run ends at that step, cancelled, its branch as it stood then. True: the later steps run again.
    redo: bool = False


class PushIn(BaseModel):
    #: Which of the repository's remotes; left out, `origin` — or the only one there is.
    remote: str | None = Field(default=None, min_length=1, max_length=200)


async def _context(open_session: AsyncSession, runs: list[Run]) -> dict[str, dict[str, Any]]:
    """Everything the serialiser needs for a list of runs, in three queries rather than three per run."""
    if not runs:
        return {"projects": {}, "tasks": {}, "plans": {}, "children": {}}
    project_ids = {r.project_id for r in runs}
    task_ids = {r.task_id for r in runs if r.task_id}
    plan_ids = {r.plan_id for r in runs if r.plan_id}
    projects = {p.id: p.name for p in (await open_session.execute(
        select(Project).where(Project.id.in_(project_ids)))).scalars()}
    tasks = {t.id: t.ref for t in (await open_session.execute(
        select(Task).where(Task.id.in_(task_ids)))).scalars()} if task_ids else {}
    plans = {p.id: p.ref for p in (await open_session.execute(
        select(Plan).where(Plan.id.in_(plan_ids)))).scalars()} if plan_ids else {}
    children = await RunRepository(open_session).children_of([r.id for r in runs])
    return {"projects": projects, "tasks": tasks, "plans": plans, "children": children}


def _one(run: Run, context: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return run_json(run, project_name=context["projects"].get(run.project_id, ""),
                    children=context["children"].get(run.id, []),
                    task_ref=context["tasks"].get(run.task_id or ""),
                    plan_ref=context["plans"].get(run.plan_id or ""))


@router.get("")
async def runs(project: str | None = None, limit: int | None = None, offset: int = 0,
               who: Person = Depends(current_person),
               open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """Every run this person may see — a run names its project, its task and its branch."""
    page = await RunRepository(open_session).newest(project, limit=limit, offset=offset,
                                                    hidden=await unseen_by(who, open_session))
    context = await _context(open_session, page.items)
    return [_one(r, context) for r in page.items]


@router.get("/{ref}")
async def run(ref: str, after: int = 0, who: Person = Depends(current_person),
              open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The run and its output. `after` is the last log id you hold, for catching up after a reconnect."""
    found = await RunRepository(open_session).by_ref(ref)
    if found is None:
        raise NotFound(f"run {ref}")
    await must_see(who, open_session, found.project_id, f"run {ref}")
    context = await _context(open_session, [found])
    logs = await RunLogRepository(open_session).after(found.id, after)
    return {**_one(found, context), "logs": [run_log_json(line) for line in logs]}


@router.get("/{ref}/diff")
async def diff(ref: str, who: Person = Depends(current_person),
               open_session: AsyncSession = Depends(session),
               gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """The real diff — the project's own code. Closed with the run it belongs to."""
    found = await RunRepository(open_session).by_ref(ref)
    if found is None:
        raise NotFound(f"run {ref}")
    await must_see(who, open_session, found.project_id, f"run {ref}")
    return await RunService(open_session, gw).diff(ref)


@router.post("/{ref}/cancel")
async def cancel(ref: str, who: Person = Depends(require("runs:run")),
                 open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Stop it. The worktree stays where it is, for you to look at."""
    stopped = await RunService(open_session, gw).cancel(ref, who.name)
    return _one(stopped, await _context(open_session, [stopped]))


@router.post("/{ref}/merge")
async def merge(ref: str, request: Request, who: Person = Depends(require("runs:merge")),
                open_session: AsyncSession = Depends(session),
                gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Merge an accepted run into the branch your repository has checked out."""
    service = RunService(open_session, gw)
    result = await service.merge(ref, who.name)
    merged = await RunRepository(open_session).by_ref(ref)
    if result["merged"]:
        await AuditRepository(open_session).record(
            action="run.merge", user_id=who.id, target=f"{merged.branch} → {result['into']}",
            detail={"commit": result["commit"], "run": ref},
            ip=request.client.host if request.client else "")
    return {**result, "run": _one(merged, await _context(open_session, [merged]))}


@router.post("/{ref}/push")
async def push(ref: str, request: Request, body: PushIn | None = None,
               who: Person = Depends(require("runs:merge")), open_session: AsyncSession = Depends(session),
               gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Push an accepted run's own branch to the project's remote with your git credentials, never forced.
    Refused while the branch is not exactly what was reviewed. Answers the run, whose `pushed` carries
    the compare link where you open the pull request yourself."""
    pushed = await RunService(open_session, gw).push(ref, who.name, body.remote if body else None)
    await AuditRepository(open_session).record(
        action="run.push", user_id=who.id, target=f"{pushed.branch} → {pushed.pushed['remote']}",
        detail={"run": ref, "sha": pushed.pushed["sha"], "compareUrl": pushed.pushed["compareUrl"]},
        ip=request.client.host if request.client else "")
    return _one(pushed, await _context(open_session, [pushed]))


@router.post("/{ref}/review")
async def review_again(ref: str, jobs: BackgroundTasks, who: Person = Depends(require("runs:run")),
                       open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                       gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Read the run's diff again, as the branch is now, for a new review and a new receipt. The reading
    happens after the response; what comes back is the run with its review step running."""
    run, step = await RunService(open_session, gw).review_again(ref, who.name)
    answer = _one(run, await _context(open_session, [run]))
    await hand_off(open_session, jobs, runtime.reread, db, gw, ref, step, who.name)
    return answer


@router.post("/{ref}/discard")
async def discard(ref: str, who: Person = Depends(require("runs:run")),
                  open_session: AsyncSession = Depends(session),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Remove the worktree and the branch. Only once the run has stopped."""
    removed = await RunService(open_session, gw).discard(ref, who.name)
    return _one(removed, await _context(open_session, [removed]))


@router.post("/{ref}/rework")
async def rework(ref: str, body: ReworkIn, jobs: BackgroundTasks, who: Person = Depends(require("runs:run")),
                 open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Send it back: the same plan, done again as a new run that is told your notes and the review's
    findings. The old run's worktree and branch go, and a signature it was waiting for is refused. The
    new run is made here and starts after the response; what comes back is the new run."""
    made = await RunService(open_session, gw).rework(ref, body.notes, by=who.name, by_id=who.id,
                                                    may_decide=who.can("approvals:decide"))
    lead = made[-1]
    answer = _one(lead, await _context(open_session, [lead]))
    starter = runtime.execute_batch if len(made) > 1 else runtime.execute
    await hand_off(open_session, jobs, starter, db, gw, lead.ref)
    return answer


@router.post("/{ref}/steps/{n}/revert")
async def revert(ref: str, n: int, request: Request, jobs: BackgroundTasks, body: RevertIn | None = None,
                 who: Person = Depends(require("runs:run")), open_session: AsyncSession = Depends(session),
                 db: Database = Depends(database), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Take the run's worktree back to how it stood after step `n` — its own worktree, never your checkout —
    and mark the later steps taken back. With `redo`, they run again from step n + 1, after the response.
    Refused while the run is working. Audited: it rewrites a branch."""
    redo = bool(body and body.redo)
    run = await RunService(open_session, gw).revert(ref, n, who.name, redo=redo)
    last = (run.review or {}).get("reverts", [{}])[-1]
    await AuditRepository(open_session).record(
        action="run.revert", user_id=who.id, target=f"{run.ref} → step {n}",
        detail={"branch": run.branch, "steps": last.get("steps", []), "sha": last.get("sha", {}), "redo": redo},
        ip=request.client.host if request.client else "")
    answer = _one(run, await _context(open_session, [run]))
    if redo:
        await hand_off(open_session, jobs, runtime.execute, db, gw, ref, n + 1)
    return answer
