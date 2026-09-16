"""The requirement compiler over HTTP: compile, re-compile, and dispatch.

Dispatching is the moment the product stops talking and starts working: the plan's gate is checked
here, and the runtime takes it from there in a background task — its own worktree, its own branch,
your signature at the end. Nothing starts while a question is still open, and nothing starts at all
for someone whose role cannot run agents.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..schemas import plan_json, task_json
from ..services import runs as runtime
from ..services.identity import Person
from ..services.plans import PlanService
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter()


class CompileIn(BaseModel):
    requirement: str = Field(min_length=3, max_length=4000)
    projectId: str = Field(max_length=80)


@router.post("/plans/compile", status_code=201)
async def compile_requirement(body: CompileIn, who: Person = Depends(require("plans:compile")),
                              open_session: AsyncSession = Depends(session),
                              gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A requirement becomes a plan and the task that carries it. Open questions are kept, not guessed."""
    plan, task = await PlanService(open_session, gw).compile(
        body.projectId, body.requirement, by=who.name, by_id=who.id)
    return {**plan_json(plan, task_ref=task.ref), "task": task_json(task)}


@router.post("/plans/{ref}/recompile")
async def recompile(ref: str, who: Person = Depends(require("plans:decide")),
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Compile it again with everything that has been answered since. Refused once it is under way."""
    plan = await PlanService(open_session, gw).recompile(ref, by=who.name, by_id=who.id)
    return plan_json(plan)


@router.post("/plans/{ref}/dispatch")
async def dispatch(ref: str, jobs: BackgroundTasks, who: Person = Depends(require("plans:decide")),
                   open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Hand the plan to the agents. The runs are created here; they start after the response."""
    plan, made = await PlanService(open_session, gw).dispatch(
        ref, by=who.name, may_run=who.can("runs:run"))
    if made:
        lead = made[-1]
        starter = runtime.execute_batch if len(made) > 1 else runtime.execute
        await hand_off(open_session, jobs, starter, db, gw, lead.ref)
        return {**plan_json(plan), "runRef": lead.ref}
    return plan_json(plan)
