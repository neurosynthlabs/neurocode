"""The work over HTTP: tasks, the human gates, plans, final decisions and screen settings.

Same paths and same JSON as before. The difference is what is *not* here: no rules. Whether a task
may move, whether a gate can be answered twice, whether a decision can be re-recorded — all of that
lives in the services, so it can be read and tested without a web request.

The compiler and the agent runtime still belong to the old stack; their routes arrive with them.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi import Path as PathParam
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..repositories import (
    ApprovalRepository,
    DecisionRepository,
    NotFound,
    PlanRepository,
    PrefRepository,
    TaskRepository,
)
from ..schemas import approval_json, decision_json, plan_json, pref_json, task_json
from ..services.gates import ApprovalService, DecisionService, PrefService
from ..services.identity import Person
from ..services.knowledge import MemoryService, NewFact
from ..services.runs import resume as resume_run
from ..services.work import Actor, PlanQuestions, TaskService
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter()
TaskStatus = Literal["backlog", "planning", "in_progress", "review", "blocked", "done"]
KEY = r"^[A-Za-z][\w.:-]{1,80}$"


class StatusIn(BaseModel):
    status: TaskStatus


class DoneIn(BaseModel):
    done: bool


class AnswerIn(BaseModel):
    answer: str | None = Field(default=None, max_length=1000)
    defer: bool = False


class DecisionIn(BaseModel):
    value: str = Field(min_length=1, max_length=40)
    action: str = Field(min_length=1, max_length=80)
    detail: str = Field(default="", max_length=300)
    projectId: str = Field(default="aios", max_length=60)
    level: Literal["info", "ok", "warn", "err"] = "ok"


class PrefIn(BaseModel):
    value: Any
    detail: str = Field(default="", max_length=300)
    projectId: str = Field(default="aios", max_length=60)

    @field_validator("value")
    @classmethod
    def _small(cls, v: Any) -> Any:
        if len(json.dumps(v)) > 20_000:
            raise ValueError("a setting is limited to 20 KB")
        return v


def _actor(who: Person) -> Actor:
    return Actor(name=who.name, permissions=who.permissions)


# ── tasks ────────────────────────────────────────────────────────
@router.get("/tasks", dependencies=[Depends(current_person)])
async def tasks(project: str | None = None, limit: int | None = None, offset: int = 0,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    page = await TaskRepository(open_session).board(project, limit=limit, offset=offset)
    return [task_json(t) for t in page.items]


@router.get("/tasks/{ref}", dependencies=[Depends(current_person)])
async def task(ref: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    found = await TaskRepository(open_session).by_ref(ref)
    if found is None:
        raise NotFound(f"task {ref}")
    return task_json(found)


@router.patch("/tasks/{ref}")
async def move_task(ref: str, body: StatusIn, who: Person = Depends(require("tasks:write")),
                    open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return task_json(await TaskService(open_session).move(ref, body.status, _actor(who)))


@router.post("/tasks/{ref}/checklist/{item_id}")
async def check_item(ref: str, item_id: str, body: DoneIn, who: Person = Depends(require("tasks:write")),
                     open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return task_json(await TaskService(open_session).tick(ref, item_id, body.done, _actor(who)))


# ── approvals: the human gate ────────────────────────────────────
@router.get("/approvals", dependencies=[Depends(current_person)])
async def approvals(status: str | None = None, limit: int | None = None, offset: int = 0,
                    open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    repo = ApprovalRepository(open_session)
    page = (await repo.pending(limit=limit, offset=offset) if status == "pending"
            else await repo.newest(limit=limit, offset=offset))
    return [approval_json(a) for a in page.items]


@router.post("/approvals/{ref}/{decision}")
async def decide(ref: str, decision: Literal["approve", "deny"], jobs: BackgroundTasks,
                 who: Person = Depends(require("approvals:decide")),
                 open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A decision is final — and when an agent run was stopped at this gate, it carries on from here.

    This used to say the runtime would resume the run, and nothing did: the decision was written, and
    the run sat at "waiting" for ever. Every run stopped at its first gate — the first test run in a
    project, or your signature on the diff — and never went further.
    """
    answered = await ApprovalService(open_session).decide(ref, decision, by_id=who.id, by_name=who.name)
    if answered.run_ref:
        await hand_off(open_session, jobs, resume_run, db, gw, answered.run_ref, answered.step or 0,
                       decision == "approve")
    return approval_json(answered)


# ── plans ────────────────────────────────────────────────────────
@router.get("/plans", dependencies=[Depends(current_person)])
async def plans(project: str | None = None, limit: int | None = None, offset: int = 0,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    page = await PlanRepository(open_session).newest(project, limit=limit, offset=offset)
    return [plan_json(p) for p in page.items]


@router.get("/plans/{ref}", dependencies=[Depends(current_person)])
async def plan(ref: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    found = await PlanRepository(open_session).by_ref(ref)
    if found is None:
        raise NotFound(f"plan {ref}")
    return plan_json(found)


@router.post("/plans/{ref}/questions/{index}")
async def settle_question(ref: str, index: int, body: AnswerIn,
                          who: Person = Depends(require("plans:decide")),
                          open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The index is the position among the *open* questions, exactly as the screen shows them.

    An answer is not only recorded on the plan: it becomes a business rule the workspace remembers,
    so the next plan on this project is compiled knowing it.
    """
    plans_repo = PlanRepository(open_session)
    found = await plans_repo.by_ref(ref)
    if found is None:
        raise NotFound(f"plan {ref}")
    open_questions = await plans_repo.open_questions(found.id)
    if not 0 <= index < len(open_questions):
        raise NotFound(f"open question #{index} of {ref}")
    question = open_questions[index]

    answer = (body.answer or "").strip()
    settled = await PlanQuestions(open_session).answer(ref, question.n, answer, defer=body.defer)
    if not body.defer:
        await MemoryService(open_session).add(
            [NewFact(title=question.question, body=answer, category="business_rules", confidence="HIGH",
                     reason=f"{who.name} answered it while reviewing {ref}.")],
            project_id=found.project_id, by=who.name, source=f"{ref} · open question")
    return plan_json(settled)


# ── final decisions and screen settings ─────────────────────────
@router.get("/decisions", dependencies=[Depends(current_person)])
async def decisions(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    return [decision_json(d) for d in await DecisionRepository(open_session).all_ordered()]


@router.post("/decisions/{key}", status_code=201)
async def record_decision(body: DecisionIn, key: str = PathParam(pattern=KEY),
                          who: Person = Depends(require("decisions:make")),
                          open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    made = await DecisionService(open_session).record(
        key, verdict=body.value, action=body.action, detail=body.detail, project_id=body.projectId,
        level=body.level, by_id=who.id, by_name=who.name)
    return decision_json(made)


@router.get("/prefs", dependencies=[Depends(current_person)])
async def prefs(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    return [pref_json(p) for p in await PrefRepository(open_session).all_ordered()]


@router.put("/prefs/{key}")
async def set_pref(body: PrefIn, key: str = PathParam(pattern=KEY),
                   who: Person = Depends(require("settings:write")),
                   open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    saved = await PrefService(open_session).set(key, body.value, detail=body.detail,
                                                project_id=body.projectId, by=who.name)
    return pref_json(saved)
