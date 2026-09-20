"""The Workflows screen: workflows a person wrote, and running one as an ordinary plan.

Reading needs a session. Writing a workflow needs `workflows:write`, and nothing runs when one is
written. Running one is dispatching a plan, so it asks exactly what the Plans screen asks — to compile
and to steer plans — and whether a run really starts depends on `runs:run`, as it does there.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..services import runs as runtime
from ..services.identity import Person
from ..services.workflows import MAX_STEPS, StepDraft, WorkflowDraft, WorkflowService
from .deps import current_person, database, gateway, hand_off, must_see, require, session, unseen_by

router = APIRouter(prefix="/workflows")


class StepIn(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    agent: str = Field(min_length=1, max_length=120)
    detail: str = Field(default="", max_length=2000)


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    projectId: str | None = Field(default=None, max_length=40)
    requirementTemplate: str = Field(min_length=7, max_length=4000)
    steps: list[StepIn] = Field(min_length=1, max_length=MAX_STEPS)

    def draft(self) -> WorkflowDraft:
        return WorkflowDraft(name=self.name, description=self.description, project_id=self.projectId,
                             requirement_template=self.requirementTemplate,
                             steps=[StepDraft(s.label, s.agent, s.detail) for s in self.steps])


class RunIn(BaseModel):
    projectId: str = Field(min_length=1, max_length=40)
    input: str = Field(min_length=3, max_length=4000)


@router.get("")
async def workflows(project: str | None = None, who: Person = Depends(current_person),
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> list[dict[str, Any]]:
    """The library. `project` is where the phases are drawn for: its test command decides the Test phase."""
    return await WorkflowService(open_session, gw).library(project, await unseen_by(who, open_session))


@router.get("/overview", dependencies=[Depends(current_person)])
async def overview(open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await WorkflowService(open_session, gw).overview()


@router.get("/{workflow_id}")
async def workflow(workflow_id: str, project: str | None = None, who: Person = Depends(current_person),
                   open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    found = await WorkflowService(open_session, gw).detail(workflow_id, project)
    await must_see(who, open_session, str(found.get("projectId") or ""), f"workflow {workflow_id}")
    return found


@router.post("", status_code=201)
async def create(body: WorkflowIn, who: Person = Depends(require("workflows:write")),
                 open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    service = WorkflowService(open_session, gw)
    made = await service.create(body.draft(), by=who.name, by_id=who.id)
    return await service.detail(made.id, made.project_id)


@router.patch("/{workflow_id}")
async def update(workflow_id: str, body: WorkflowIn, who: Person = Depends(require("workflows:write")),
                 open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    service = WorkflowService(open_session, gw)
    changed = await service.update(workflow_id, body.draft(), by=who.name)
    return await service.detail(changed.id, changed.project_id)


@router.delete("/{workflow_id}")
async def remove(workflow_id: str, who: Person = Depends(require("workflows:write")),
                 open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    archived = await WorkflowService(open_session, gw).remove(workflow_id, by=who.name)
    return {"ok": True, "archived": archived}


@router.post("/{workflow_id}/run")
async def run(workflow_id: str, body: RunIn, jobs: BackgroundTasks,
              who: Person = Depends(require("plans:compile", "plans:decide")),
              open_session: AsyncSession = Depends(session), db: Database = Depends(database),
              gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A plan from the workflow, dispatched. The runs are created here and start after the response."""
    started = await WorkflowService(open_session, gw).run(
        workflow_id, body.projectId, body.input, by=who.name, by_id=who.id, may_run=who.can("runs:run"))
    out = {"planRef": started.plan.ref, "taskRef": started.task.ref,
           "runRef": started.run.ref if started.run else None, "agents": started.agents,
           "openQuestions": started.open_questions, "note": started.note}
    if started.run is not None:
        starter = runtime.execute_batch if started.run.role == "integration" else runtime.execute
        await hand_off(open_session, jobs, starter, db, gw, started.run.ref)
    return out
