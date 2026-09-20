"""The requirement compiler over HTTP: compile, re-compile, edit what "done" means, and dispatch.

Dispatching is the moment the product stops talking and starts working: the plan's gate is checked
here, and the runtime takes it from there in a background task — its own worktree, its own branch,
your signature at the end. Nothing starts while a question is still open, and nothing starts at all
for someone whose role cannot run agents.

Drafting a project's first AGENTS.md lives here too, because it is a compile: the requirement is written
from the code index, and the plan it makes is dispatched and signed like any other.

Before dispatch a plan is shaped here: its steps edited, added, removed and reordered, comments left on it,
and a revision written from those comments. All of it needs plans:compile — shaping a plan is writing it —
and all of it is refused once the plan is under way.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.compiler import MAX_CRITERIA
from ..ai.gateway import Gateway
from ..data.engine import Database
from ..schemas import plan_json, task_json
from ..schemas.work import comment_json
from ..services import runs as runtime
from ..services.identity import Person
from ..services.plans import MAX_DETAIL, MAX_LABEL, MAX_STEPS, PlanService
from .deps import current_person, database, gateway, hand_off, require, scoped, session

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
async def draft_instructions(pid: str, who: Person = Depends(scoped("plans:compile")),
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
    #: "Pause before each step": the runtime waits for a person's approval between steps.
    stepGate: bool = False


@router.post("/plans/{ref}/dispatch")
async def dispatch(ref: str, jobs: BackgroundTasks, body: DispatchIn | None = None,
                   who: Person = Depends(require("plans:decide")),
                   open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Hand the plan to the agents. The runs are created here; they start after the response. With a
    `goalBudget` the run ends with a completion check and tries again on its own while it misses; with
    `stepGate` it pauses for an approval before each step after the first."""
    plan, made = await PlanService(open_session, gw).dispatch(
        ref, by=who.name, may_run=who.can("runs:run"), goal_budget=body.goalBudget if body else None,
        step_gate=body.stepGate if body else False)
    if made:
        lead = made[-1]
        starter = runtime.execute_batch if len(made) > 1 else runtime.execute
        await hand_off(open_session, jobs, starter, db, gw, lead.ref)
        return {**plan_json(plan), "runRef": lead.ref}
    return plan_json(plan)


# ── shaping a plan before dispatch ───────────────────────────────
class StepPatch(BaseModel):
    label: str | None = Field(default=None, max_length=MAX_LABEL)
    agent: str | None = Field(default=None, max_length=120)
    detail: str | None = Field(default=None, max_length=MAX_DETAIL)


class StepIn(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_LABEL)
    agent: str = Field(min_length=1, max_length=120)
    detail: str = Field(default="", max_length=MAX_DETAIL)
    #: Where it goes, 1 for first; left out, it goes last.
    at: int | None = Field(default=None, ge=1, le=MAX_STEPS + 1)


class OrderIn(BaseModel):
    #: Every step's id, each once, in the order wanted.
    order: list[Annotated[str, Field(max_length=40)]] = Field(min_length=1, max_length=MAX_STEPS)


class CommentIn(BaseModel):
    kind: Literal["comment", "split", "remove", "why", "risky"] = "comment"
    body: str = Field(min_length=1, max_length=4_000)
    #: The step it is on; left out, it is on the whole plan.
    stepId: str | None = Field(default=None, max_length=40)


class ResolveIn(BaseModel):
    resolved: bool = True


@router.patch("/plans/{ref}/steps/{step_id}")
async def edit_step(ref: str, step_id: str, body: StepPatch, who: Person = Depends(require("plans:compile")),
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Change a step's label, owner (a roster name) or detail. 409 once the plan is under way; 422 for an
    owner who is not an agent here."""
    plan = await PlanService(open_session, gw).edit_step(ref, step_id, label=body.label, agent=body.agent,
                                                         detail=body.detail, by=who.name, by_id=who.id)
    return plan_json(plan)


@router.post("/plans/{ref}/steps", status_code=201)
async def add_step(ref: str, body: StepIn, who: Person = Depends(require("plans:compile")),
                   open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A step of a person's own, at `at` (1 is first) or last. The task's checklist follows."""
    plan = await PlanService(open_session, gw).add_step(ref, label=body.label, agent=body.agent, detail=body.detail,
                                                        at=body.at, by=who.name, by_id=who.id)
    return plan_json(plan)


@router.delete("/plans/{ref}/steps/{step_id}")
async def remove_step(ref: str, step_id: str, who: Person = Depends(require("plans:compile")),
                      open_session: AsyncSession = Depends(session),
                      gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Drop a step; the rest are numbered again. A plan's only step is refused (409)."""
    plan = await PlanService(open_session, gw).remove_step(ref, step_id, by=who.name, by_id=who.id)
    return plan_json(plan)


@router.patch("/plans/{ref}/steps")
async def reorder_steps(ref: str, body: OrderIn, who: Person = Depends(require("plans:compile")),
                        open_session: AsyncSession = Depends(session),
                        gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Put the steps in this order. 422 unless it names every step exactly once."""
    plan = await PlanService(open_session, gw).reorder_steps(ref, body.order, by=who.name, by_id=who.id)
    return plan_json(plan)


@router.get("/plans/{ref}/comments", dependencies=[Depends(current_person)])
async def comments(ref: str, open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Every comment on the plan, oldest first, open and resolved, each with the revision it was on."""
    plan, found, names = await PlanService(open_session, gw).comments(ref)
    items = [comment_json(c, plan, by=names.get(c.by_user_id or "")) for c in found]
    return {"items": items, "open": sum(1 for c in found if not c.resolved), "revision": plan.revision}


@router.post("/plans/{ref}/comments", status_code=201)
async def comment(ref: str, body: CommentIn, who: Person = Depends(require("plans:compile")),
                  open_session: AsyncSession = Depends(session),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A note on the plan or one step: comment, split, remove, why or risky. It never changes the plan by
    itself; revising hands the open ones to the compiler."""
    plan, made = await PlanService(open_session, gw).comment(ref, kind=body.kind, body=body.body,
                                                             step_id=body.stepId, by=who.name, by_id=who.id)
    return comment_json(made, plan, by=who.name)


@router.post("/plans/{ref}/comments/{comment_id}/resolve")
async def resolve_comment(ref: str, comment_id: int, body: ResolveIn | None = None,
                          who: Person = Depends(require("plans:compile")),
                          open_session: AsyncSession = Depends(session),
                          gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Resolve a comment — or reopen it with `{"resolved": false}`. A resolved comment is not handed to
    the next revision."""
    plans = PlanService(open_session, gw)
    plan, found = await plans.resolve_comment(ref, comment_id, resolved=body.resolved if body else True,
                                              by=who.name)
    return comment_json(found, plan, by=(await plans.names([found.by_user_id])).get(found.by_user_id or ""))


@router.post("/plans/{ref}/revise")
async def revise(ref: str, who: Person = Depends(require("plans:compile")),
                 open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Hand the open comments to the compiler and write the next revision. Answers the plan and what
    changed, step by step. 409 with no open comment, or no model; 502 when every lane failed."""
    plan, changes = await PlanService(open_session, gw).revise(ref, by=who.name, by_id=who.id)
    return {**plan_json(plan), "changes": changes}
