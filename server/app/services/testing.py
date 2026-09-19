"""The Testing screen: every onboarded project's own test command, what its runs found, and the word a
person keeps on a test that is red on purpose.

Nothing here is a model's opinion. A suite is the command the runtime detected in the repository; its
numbers are what the runner printed the last time that command really ran; "failed in 3 of the last
10 runs" is counted from those runs; coverage is whatever report the project's own command wrote. A
project whose tests never ran shows that, not a plausible panel.

Starting tests is a run like any other — its own worktree, the same first-time approval, the same
log — with no agent in it, and it removes its branch when it ends.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import agent
from ..ai.gateway import Gateway
from ..data import roster
from ..models import Project, Run
from ..repositories import ActivityRepository, AuditRepository, NotFound, ProjectRepository, RunLogRepository
from ..repositories.runtime import ExpectationRepository, ResultsRepository
from ..schemas.runtime import run_log_json
from ..schemas.testing import coverage_json, expectation_json, failure_json, history_json, suite_json
from .code import checkout
from .errors import Refused
from .identity import Person
from .runs import RunService

#: How many recent tested runs "failed in N of the last M" looks back over.
WINDOW = 10
HISTORY = 50
KINDS = ("legacy", "quarantine")


def _commands(projects: list[Project]) -> dict[str, str | None]:
    """The test command each project would run now, detected in its checkout. Blocking."""
    out: dict[str, str | None] = {}
    for project in projects:
        root = checkout(project)
        found = agent.detect_tests(root) if root is not None and root.is_dir() else None
        out[project.id] = found["command"] if found else None
    return out


def _trigger(run: Run) -> str:
    """Who set a test run going: the person for a check, the agent for one of several agents' runs,
    and the QA Engineer — whose step it is — for a solo or merge run."""
    if run.role == "check":
        return run.requested_by or "Someone"
    return run.agent or roster.TESTER


class TestingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.results = ResultsRepository(session)
        self.expectations = ExpectationRepository(session)
        self.projects = ProjectRepository(session)

    async def project(self, project_id: str) -> Project:
        found = await self.projects.get(project_id)
        if found is None:
            raise NotFound(f"project {project_id}")
        return found

    # ── reading ──────────────────────────────────────────────────
    async def report(self, project_id: str | None = None) -> dict[str, Any]:
        if project_id:
            await self.project(project_id)
        projects = await self.results.onboarded(project_id)
        ids = [p.id for p in projects]
        names = {p.id: p.name for p in projects}

        commands = await asyncio.to_thread(_commands, projects)
        answers = await self.results.answers(ids)
        latest = await self.results.latest(ids)
        checking = await self.results.checking(ids)
        history = await self.results.history(ids, limit=HISTORY)
        sizes, recurrence = await self.results.recurrence(ids, WINDOW)
        latest_runs = {run.id: run for run, _ in latest.values()}
        rows = await self.results.failures(list(latest_runs))
        coverage = await self.results.coverage(list(latest_runs))
        expectations = await self.expectations.of_projects(ids)

        recorded: dict[str, int] = {}
        expected: dict[str, int] = {}
        for _f, ref, _pid, e, _by in rows:
            recorded[ref] = recorded.get(ref, 0) + 1
            expected[ref] = expected.get(ref, 0) + (1 if e is not None else 0)

        def suite(p: Project) -> dict[str, Any]:
            found = latest.get(p.id)
            ref = found[0].ref if found else ""
            return suite_json(p, command=commands.get(p.id), allowed=answers.get(p.id), latest=found,
                              checking=checking.get(p.id), recorded=recorded.get(ref, 0),
                              expected=expected.get(ref, 0))

        return {
            "suites": [suite(p) for p in projects],
            "failures": [failure_json(f, run_ref=ref, project_id=pid, project_name=names.get(pid, pid),
                                      expectation=e, by=by, recurrence=recurrence.get((pid, f.name)),
                                      window=sizes.get(pid, 1))
                         for f, ref, pid, e, by in rows],
            "history": [history_json(run, ms, project_name=names.get(run.project_id, run.project_id),
                                     trigger=_trigger(run))
                        for run, ms in history],
            "coverage": [coverage_json(c, run_ref=latest_runs[c.run_id].ref,
                                       project_id=latest_runs[c.run_id].project_id) for c in coverage],
            "expectations": [expectation_json(e, by) for e, by in expectations],
            "window": WINDOW,
        }

    async def failure(self, failure_id: int) -> dict[str, Any]:
        """One failure, with every line its test step kept — for when the excerpt is not enough."""
        found = await self.results.failure(failure_id)
        if found is None:
            raise NotFound(f"test failure {failure_id}")
        f, ref, pid, e, by = found
        project = await self.project(pid)
        sizes, recurrence = await self.results.recurrence([pid], WINDOW)
        lines = await RunLogRepository(self.session).of_step(f.run_id, f.step_n)
        return {**failure_json(f, run_ref=ref, project_id=pid, project_name=project.name, expectation=e,
                               by=by, recurrence=recurrence.get((pid, f.name)), window=sizes.get(pid, 1)),
                "log": [run_log_json(line) for line in lines]}

    # ── running ──────────────────────────────────────────────────
    async def start(self, project_id: str, who: Person, gateway: Gateway) -> tuple[Run, Project]:
        project = await self.project(project_id)
        run = await RunService(self.session, gateway).check_run(project, who.name)
        return run, project

    async def rerun(self, failure_id: int, who: Person, gateway: Gateway) -> tuple[Run, Project]:
        """The whole suite again, on the branch that failed while it still exists, else on HEAD.

        The whole command, not the one test: the detected commands have no filter that works the same
        way across make, pytest, npm, go and dotnet, and a filter that silently runs nothing is worse.
        """
        found = await self.results.failure(failure_id)
        if found is None:
            raise NotFound(f"test failure {failure_id}")
        _, ref, pid, _, _ = found
        project = await self.project(pid)
        runtime = RunService(self.session, gateway)
        failed_run = await runtime.runs.by_ref(ref)
        branch = failed_run.branch if failed_run is not None and not failed_run.removed else None
        run = await runtime.check_run(project, who.name, at_branch=branch)
        return run, project

    # ── what a person expects ────────────────────────────────────
    async def expect(self, project_id: str, test_name: str, kind: str, reason: str, who: Person,
                     ip: str = "") -> dict[str, Any]:
        project = await self.project(project_id)
        name, why = test_name.strip(), reason.strip()
        if not name:
            raise Refused("Name the test.", status=422)
        if kind not in KINDS:
            raise Refused("A test is expected as legacy, or quarantined.", status=422)
        if len(why) < 10:
            raise Refused("Say why, in a sentence: the next person to see this test red will read it.", status=422)
        kept = await self.expectations.put(expectation_id=f"te-{uuid.uuid4().hex[:16]}", project_id=project.id,
                                           test_name=name, kind=kind, reason=why, by_user_id=who.id)
        await AuditRepository(self.session).record(action="test.expect", user_id=who.id, target=name,
                                                   detail={"project": project.id, "kind": kind}, ip=ip)
        await ActivityRepository(self.session).record(
            actor=who.name, actor_kind="human", action=f"Test marked {kind}",
            detail=f"{name} · {why[:160]}", project_id=project.id)
        return expectation_json(kept, who.name)

    async def unexpect(self, project_id: str, test_name: str, who: Person, ip: str = "") -> None:
        project = await self.project(project_id)
        if not await self.expectations.remove_for(project.id, test_name):
            raise NotFound(f"an expectation on {test_name}")
        await AuditRepository(self.session).record(action="test.expect.remove", user_id=who.id,
                                                   target=test_name, detail={"project": project.id}, ip=ip)
        await ActivityRepository(self.session).record(
            actor=who.name, actor_kind="human", action="Test expectation removed",
            detail=f"{test_name} counts as a real failure again", project_id=project.id)
