"""The requirement compiler over HTTP: compile, re-compile, edit what "done" means, and dispatch.

Dispatching is the moment the product stops talking and starts working: the plan's gate is checked
here, and the runtime takes it from there in a background task — its own worktree, its own branch,
your signature at the end. Nothing starts while a question is still open, and nothing starts at all
for someone whose role cannot run agents.

Drafting a project's first AGENTS.md lives here too, because it is a compile: the requirement is written
from the code index, and the plan it makes is dispatched and signed like any other.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.compiler import MAX_CRITERIA
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


class PlanPatch(BaseModel):
    """What a person may change on a plan before it is under way. Only its acceptance criteria, today."""

    acceptanceCriteria: list[Annotated[str, Field(max_length=2_000)]] = Field(max_length=MAX_CRITERIA)


@router.post("/plans/compile", status_code=201)
async def compile_requirement(body: CompileIn, who: Person = Depends(require("plans:compile")),
                              open_session: AsyncSession = Depends(session),
                              gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A requirement becomes a plan and the task that carries it. Open questions are kept, not guessed.
    Needs a model: 409 when none is configured, 502 with the provider's reason when every lane failed."""
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


@router.patch("/plans/{ref}")
async def edit_plan(ref: str, body: PlanPatch, who: Person = Depends(require("plans:decide")),
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Set the plan's acceptance criteria — the sentences a goal run is judged against. Refused once
    the plan is under way. Blank lines are dropped; a re-compile keeps what a person wrote."""
    plan = await PlanService(open_session, gw).set_criteria(ref, body.acceptanceCriteria, by=who.name)
    return plan_json(plan)


@router.post("/projects/{pid}/instructions/draft", status_code=201)
async def draft_instructions(pid: str, who: Person = Depends(require("plans:compile")),
                             open_session: AsyncSession = Depends(session),
                             gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Compile a plan that writes the project's first AGENTS.md from what the code index measured.
    409 when the project has no checkout here, already has one, or was never indexed; otherwise the
    same answers as compiling."""
    plan, task = await PlanService(open_session, gw).draft_instructions(pid, by=who.name, by_id=who.id)
    return {**plan_json(plan, task_ref=task.ref), "task": task_json(task)}


class DispatchIn(BaseModel):
    #: "Run until done": how many attempts in all, the first included. Left out, an ordinary run.
    goalBudget: int | None = Field(default=None, ge=1, le=5)


@router.post("/plans/{ref}/dispatch")
async def dispatch(ref: str, jobs: BackgroundTasks, body: DispatchIn | None = None,
                   who: Person = Depends(require("plans:decide")),
                   open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Hand the plan to the agents. The runs are created here; they start after the response. With a
    `goalBudget` the run ends with a completion check and tries again on its own while it misses."""
    plan, made = await PlanService(open_session, gw).dispatch(
        ref, by=who.name, may_run=who.can("runs:run"), goal_budget=body.goalBudget if body else None)
    if made:
        lead = made[-1]
        starter = runtime.execute_batch if len(made) > 1 else runtime.execute
        await hand_off(open_session, jobs, starter, db, gw, lead.ref)
        return {**plan_json(plan), "runRef": lead.ref}
    return plan_json(plan)
