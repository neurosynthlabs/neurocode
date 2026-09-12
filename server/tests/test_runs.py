"""The agent runtime on Postgres, against a real git repository.

Nothing is mocked except the model. A throwaway repository is made, a plan is dispatched, and the run
does what it claims: its own worktree on its own branch, a file written and committed there, the diff
measured with git, and then it stops at your signature — with your working tree untouched.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from sqlalchemy import delete, select

from app.ai.gateway import Provider, Result
from app.data.engine import Database
from app.models import Approval, Plan, PlanStep, Project, Run, RunStep
from app.repositories import ApprovalRepository, ProjectRepository, RunRepository
from app.services.runs import RunService, execute, resume

PROJECT, PLAN = "run-test-project", "PLAN-9001"
WROTE = "def total(x):\n    return round(x, 2)\n"


class FakeGateway:
    """Answers with a script. `spread` hands out lanes without asking any provider anything."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.asked: list[str] = []

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Result[Any]:
        self.asked.append(kw.get("feature", ""))
        raw = self.script.pop(0) if self.script else '{"summary": "nothing", "files": []}'
        return Result(parse(raw), Provider("groq", "llama-3.3-70b-versatile"), 20)


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> Path:
    """A real repository with one commit — what a run needs to branch from."""
    root = tmp_path / "shop"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def total(x):\n    return x\n")
    run_git(["init", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-m", "first"], root)
    return root


@pytest_asyncio.fixture
async def live(schema: str, repo: Path) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Run Test", source_kind="local", source_repo=str(repo)))
        # The parent first, on its own: INSERT order comes from ORM relationships, and a plan points
        # at its project by foreign key alone — so nothing tells the unit of work to sort them.
        await s.flush()
        s.add(Plan(id="p-run", ref=PLAN, project_id=PROJECT, status="draft",
                   raw_requirement="Round the invoice total to two places",
                   affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(PlanStep(id="p-run-1", plan_id="p-run", n=1, label="Fix rounding in pkg/core.py",
                       agent="Backend Engineer"))
    yield db
    async with db.session() as s:
        runs = (await s.execute(select(Run).where(Run.project_id == PROJECT))).scalars().unique()
        for run in runs:
            shutil.rmtree(Path(run.worktree), ignore_errors=True)
        await s.execute(delete(Approval).where(Approval.project_id == PROJECT))
        await s.execute(delete(Run).where(Run.project_id == PROJECT))
        await s.execute(delete(Plan).where(Plan.project_id == PROJECT))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


async def dispatch(db: Database, gateway: FakeGateway) -> str:
    async with db.session() as s:
        project = await ProjectRepository(s).get(PROJECT)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        made = await RunService(s, gateway).plan_runs(plan, None, project, "Rajat")
        return made[-1].ref


async def test_a_run_writes_in_its_own_worktree_and_stops_at_your_signature(live: Database, repo: Path):
    gateway = FakeGateway(
        json.dumps({"summary": "Rounded to two places.",
                    "files": [{"path": "pkg/core.py", "content": WROTE}]}),
        json.dumps({"findings": [], "verdict": "Reads fine."}))

    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
        steps = {x.kind: x for x in run.steps}
        gate = await ApprovalRepository(s).waiting_on_person(ref)

    # your own checkout is untouched: the change lives on the run's branch
    assert (repo / "pkg" / "core.py").read_text() == "def total(x):\n    return x\n"
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == WROTE
    assert run.branch.startswith("neurocode/plan-9001")

    assert steps["edit"].status == "done" and "Rounded" in steps["edit"].detail
    assert steps["review"].status == "done" and run.review["by"] == "llama-3.3-70b-versatile"
    assert run.diff_files == 1 and run.diff_commits == 1 and run.diff_insertions >= 1
    assert run.tests_status == "not run"                  # a bare repo has no test command to find

    assert run.status == "waiting" and run.waiting_on == gate.ref
    assert gate.status == "pending" and run.branch in gate.title
    assert gateway.asked == ["agent", "review"]


async def test_accepting_it_finishes_the_run_and_leaves_the_branch(live: Database, repo: Path):
    gateway = FakeGateway(
        json.dumps({"summary": "Rounded.", "files": [{"path": "pkg/core.py", "content": WROTE}]}),
        json.dumps({"findings": [], "verdict": "Fine."}))
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.kind == "handoff")
    await resume(live, gateway, ref, step.n, approved=True)

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
    assert run.status == "done" and "git merge" in run.note
    assert run.removed is False                            # the branch is yours until you discard it
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == WROTE


async def test_refusing_it_removes_the_branch_and_the_worktree(live: Database):
    gateway = FakeGateway(
        json.dumps({"summary": "Rounded.", "files": [{"path": "pkg/core.py", "content": WROTE}]}),
        json.dumps({"findings": [], "verdict": "Fine."}))
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.kind == "handoff")
        worktree = Path(run.worktree)
    await resume(live, gateway, ref, step.n, approved=False)

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
    assert run.status == "cancelled" and run.removed is True
    assert not worktree.exists()


async def test_a_path_that_escapes_the_worktree_is_refused(live: Database, repo: Path):
    """The rule that matters most: a model may propose file contents, never a place to put them."""
    gateway = FakeGateway(
        json.dumps({"summary": "…", "files": [{"path": "../../escaped.py", "content": "print('out')\n"}]}),
        json.dumps({"findings": [], "verdict": "Fine."}))
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
        edit = next(x for x in run.steps if x.kind == "edit")
    assert edit.status == "failed" and "outside the worktree" in edit.detail
    assert not (repo.parent / "escaped.py").exists()
    assert run.diff_files == 0
