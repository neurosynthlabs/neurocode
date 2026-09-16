"""The Evals screen: suites, their cases, their runs and the results, with a person's overrides.

Reading needs a session. Writing a suite or a case, and overriding a verdict, needs `evals:write`.
Running one spends model calls, so it needs `ai:use` — the permission every other feature that calls a
model asks for — and saving a lesson writes memory, so it needs `memory:write`. A run answers 202 the
moment it is queued; the cases are answered in the background, one after another.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..schemas.evals import Check, case_json, run_json
from ..services import evals as eval_jobs
from ..services.evals import CaseDraft, EvalService, SuiteDraft
from ..services.identity import Person
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter(prefix="/evals")

Kind = Literal["regression", "capability", "safety", "cost"]
Target = Literal["compile", "ask", "retrieval", "review", "prompt"]
Category = Literal["human", "project", "architecture", "business_rules", "legacy", "database", "bugs",
                   "decisions", "incidents", "preferences", "code"]


class SuiteIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: Kind = "capability"
    targetKind: Target
    projectId: str | None = Field(default=None, max_length=40)
    lane: str | None = Field(default=None, max_length=40)
    systemPrompt: str = Field(default="", max_length=8000)
    threshold: int = Field(default=90, ge=0, le=100)
    allowOffline: bool = False
    description: str = Field(default="", max_length=2000)


class SuitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    kind: Kind | None = None
    projectId: str | None = Field(default=None, max_length=40)
    lane: str | None = Field(default=None, max_length=40)
    systemPrompt: str | None = Field(default=None, max_length=8000)
    threshold: int | None = Field(default=None, ge=0, le=100)
    allowOffline: bool | None = None
    description: str | None = Field(default=None, max_length=2000)


class CaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    input: str = Field(min_length=1, max_length=40_000)
    checks: list[Check] = Field(min_length=1, max_length=20)
    weight: int = Field(default=1, ge=1, le=100)
    sourcePlanId: str | None = Field(default=None, max_length=40)


class CasePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    input: str | None = Field(default=None, min_length=1, max_length=40_000)
    checks: list[Check] | None = Field(default=None, min_length=1, max_length=20)
    weight: int | None = Field(default=None, ge=1, le=100)


class FromPlanIn(BaseModel):
    planRef: str = Field(min_length=1, max_length=40)


class RunIn(BaseModel):
    lane: str | None = Field(default=None, max_length=40)


class RunAllIn(BaseModel):
    kind: Kind | None = None


class OverrideIn(BaseModel):
    #: None takes a person's verdict back, leaving the machine's on its own again.
    status: Literal["pass", "fail", "partial"] | None
    note: str = Field(default="", max_length=500)


class LessonIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    category: Category = "bugs"


#: Request fields → columns. A patch carries only what was sent, so `null` can clear a lane or project.
SUITE_FIELDS = {"name": "name", "kind": "kind", "projectId": "project_id", "lane": "lane",
                "systemPrompt": "system_prompt", "threshold": "threshold", "allowOffline": "allow_offline",
                "description": "description"}
#: The only suite fields a patch may set to null: no project, and no pinned lane.
NULLABLE = ("projectId", "lane")


@router.get("", dependencies=[Depends(current_person)])
async def overview(open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await EvalService(open_session, gw).overview()


@router.get("/{suite_id}", dependencies=[Depends(current_person)])
async def detail(suite_id: str, run: str | None = None, open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A suite and its cases, with what each produced in `run` — or in the latest run that started."""
    return await EvalService(open_session, gw).detail(suite_id, run)


@router.post("/suites", status_code=201)
async def create_suite(body: SuiteIn, who: Person = Depends(require("evals:write")),
                       open_session: AsyncSession = Depends(session),
                       gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    service = EvalService(open_session, gw)
    suite = await service.create_suite(SuiteDraft(
        name=body.name, kind=body.kind, target_kind=body.targetKind, project_id=body.projectId, lane=body.lane,
        system_prompt=body.systemPrompt, threshold=body.threshold, allow_offline=body.allowOffline,
        description=body.description), who)
    return await service.detail(suite.id)


@router.patch("/suites/{suite_id}")
async def update_suite(suite_id: str, body: SuitePatch, who: Person = Depends(require("evals:write")),
                       open_session: AsyncSession = Depends(session),
                       gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    service = EvalService(open_session, gw)
    sent = body.model_dump(exclude_unset=True)
    changes = {SUITE_FIELDS[k]: v for k, v in sent.items() if v is not None or k in NULLABLE}
    suite = await service.update_suite(suite_id, changes, who)
    return await service.detail(suite.id)


@router.delete("/suites/{suite_id}")
async def delete_suite(suite_id: str, who: Person = Depends(require("evals:write")),
                       open_session: AsyncSession = Depends(session),
                       gw: Gateway = Depends(gateway)) -> dict[str, bool]:
    await EvalService(open_session, gw).delete_suite(suite_id, who)
    return {"ok": True}


@router.post("/suites/{suite_id}/cases", status_code=201)
async def add_case(suite_id: str, body: CaseIn, who: Person = Depends(require("evals:write")),
                   open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    suite, case = await EvalService(open_session, gw).add_case(suite_id, CaseDraft(
        name=body.name, input=body.input, checks=body.checks, weight=body.weight,
        source_plan_id=body.sourcePlanId), who)
    return case_json(case, suite.name, None, moved=None, total_weight=0)


@router.post("/suites/{suite_id}/cases/from-plan")
async def case_from_plan(suite_id: str, body: FromPlanIn, _who: Person = Depends(require("evals:write")),
                         open_session: AsyncSession = Depends(session),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A draft case from a plan, for a person to edit and then save. Nothing is written."""
    return await EvalService(open_session, gw).case_from_plan(suite_id, body.planRef)


@router.patch("/cases/{case_id}")
async def update_case(case_id: str, body: CasePatch, who: Person = Depends(require("evals:write")),
                      open_session: AsyncSession = Depends(session),
                      gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    changes: dict[str, Any] = {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}
    suite, case = await EvalService(open_session, gw).update_case(case_id, changes, who)
    return case_json(case, suite.name, None, moved=None, total_weight=0)


@router.delete("/cases/{case_id}")
async def delete_case(case_id: str, who: Person = Depends(require("evals:write")),
                      open_session: AsyncSession = Depends(session),
                      gw: Gateway = Depends(gateway)) -> dict[str, bool]:
    await EvalService(open_session, gw).delete_case(case_id, who)
    return {"ok": True}


@router.post("/suites/{suite_id}/run", status_code=202)
async def run_suite(suite_id: str, jobs: BackgroundTasks, body: RunIn | None = None,
                    who: Person = Depends(require("ai:use")), open_session: AsyncSession = Depends(session),
                    db: Database = Depends(database), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    run = await EvalService(open_session, gw).start(suite_id, body.lane if body else None, who)
    await hand_off(open_session, jobs, eval_jobs.execute, db, gw, [run.ref])
    return run_json(run)


@router.post("/run-all", status_code=202)
async def run_all(jobs: BackgroundTasks, body: RunAllIn | None = None, who: Person = Depends(require("ai:use")),
                  open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                  gw: Gateway = Depends(gateway)) -> dict[str, list[str]]:
    """Every suite with cases, queued together and run one after another by a single job."""
    runs = await EvalService(open_session, gw).start_all(body.kind if body else None, who)
    refs = [r.ref for r in runs]
    await hand_off(open_session, jobs, eval_jobs.execute, db, gw, refs)
    return {"runRefs": refs}


@router.post("/runs/{ref}/cancel")
async def cancel(ref: str, who: Person = Depends(require("ai:use")), open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return run_json(await EvalService(open_session, gw).cancel(ref, who))


@router.post("/results/{result_id}/override")
async def override(result_id: int, body: OverrideIn, who: Person = Depends(require("evals:write")),
                   open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await EvalService(open_session, gw).override(result_id, body.status, body.note, who)


@router.post("/results/{result_id}/lesson", status_code=201)
async def lesson(result_id: int, body: LessonIn, who: Person = Depends(require("memory:write")),
                 open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, str]:
    return {"ref": await EvalService(open_session, gw).lesson(result_id, body.text, body.category, who)}
