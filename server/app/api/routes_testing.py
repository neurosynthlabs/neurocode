"""The Testing screen: a project's test runs, what failed and how often, coverage, and standing expectations.

Reading needs a session. Starting tests needs `runs:run`, because it is a run: a worktree, the project's
own command, and — the first time in a project — the approval every run's test step waits on. Saying a
test is red on purpose needs `decisions:make`, because it changes what raises a run's gate.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..models import Project, Run
from ..schemas import run_json
from ..services import runs as runtime
from ..services.identity import Person
from ..services.testing import TestingService
from .deps import current_person, database, gateway, hand_off, must_see, require, session, unseen_by

router = APIRouter()


class ExpectationIn(BaseModel):
    testName: str = Field(min_length=1, max_length=500)
    kind: Literal["legacy", "quarantine"]
    reason: str = Field(min_length=10, max_length=2000)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def _started(open_session: AsyncSession, jobs: BackgroundTasks, db: Database, gw: Gateway,
                   run: Run, project: Project) -> dict[str, Any]:
    """Serialise the new run while its steps are loaded, then commit it and let it start."""
    body = run_json(run, project_name=project.name)
    await hand_off(open_session, jobs, runtime.execute, db, gw, run.ref)
    return body


@router.get("/testing")
async def report(project: str | None = None, who: Person = Depends(current_person),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Every onboarded project's suite this person may see, the failures of its latest tested run,
    history and coverage — a failing test names a file and a line of the project's own code."""
    return await TestingService(open_session).report(project, hidden=await unseen_by(who, open_session))


@router.get("/testing/failures/{failure_id}")
async def failure(failure_id: int, who: Person = Depends(current_person),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """One failure, with its whole log. Of a project this person may not see, it is not there."""
    found = await TestingService(open_session).failure(failure_id)
    await must_see(who, open_session, str(found.get("projectId") or ""), f"test failure {failure_id}")
    return found


@router.post("/testing/failures/{failure_id}/rerun")
async def rerun(failure_id: int, jobs: BackgroundTasks, who: Person = Depends(require("runs:run")),
                open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """The suite again, on the branch that failed while it exists. Answered queued; it runs after."""
    run, project = await TestingService(open_session).rerun(failure_id, who, gw)
    return await _started(open_session, jobs, db, gw, run, project)


@router.post("/projects/{pid}/tests")
async def run_tests(pid: str, jobs: BackgroundTasks, who: Person = Depends(require("runs:run")),
                    open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Run the project's own tests in a throwaway worktree at HEAD. Answered queued; it runs after."""
    run, project = await TestingService(open_session).start(pid, who, gw)
    return await _started(open_session, jobs, db, gw, run, project)


@router.put("/projects/{pid}/tests/expectations")
async def expect(pid: str, body: ExpectationIn, request: Request,
                 who: Person = Depends(require("decisions:make")),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Mark a test legacy-expected or quarantined. It is still run and still reported."""
    return await TestingService(open_session).expect(pid, body.testName, body.kind, body.reason, who,
                                                     _ip(request))


@router.delete("/projects/{pid}/tests/expectations")
async def unexpect(pid: str, request: Request, test_name: str = Query(alias="testName", min_length=1),
                   who: Person = Depends(require("decisions:make")),
                   open_session: AsyncSession = Depends(session)) -> dict[str, bool]:
    """The test counts as a real failure again. It was never hidden: only the gate treated it apart."""
    await TestingService(open_session).unexpect(pid, test_name, who, _ip(request))
    return {"ok": True}
