"""The agent runtime on Postgres, against a real git repository.

Nothing is mocked except the model. A throwaway repository is made, a plan is dispatched, and the run
does what it claims: its own worktree on its own branch, a file written and committed there, the diff
measured with git, and then it stops at your signature — with your working tree untouched.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app import models as m
from app.ai.gateway import Provider, Result
from app.data.engine import Database
from app.models import Approval, Plan, PlanStep, Project, Run, RunStep
from app.repositories import ApprovalRepository, ProjectRepository, RunRepository
from app.services.errors import Refused
from app.services.runs import RunService, all_expected, execute, resume

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


# ── the test step, and a run that is only tests ──────────────────

CHECKED = "check-test-project"
#: A cobertura report the Makefile copies into place after the tests, so it is written by the step.
COBERTURA = """<?xml version="1.0" ?>
<coverage version="7.16.1" lines-valid="4" lines-covered="3">
  <sources><source>.</source></sources>
  <packages><package name="pkg"><classes>
    <class name="core.py" filename="pkg/core.py">
      <lines><line number="1" hits="1"/><line number="2" hits="1"/><line number="4" hits="1"/><line number="5" hits="0"/></lines>
    </class>
  </classes></package></packages>
</coverage>
"""


@pytest_asyncio.fixture
async def tested_repo(tmp_path: Path) -> Path:
    """A repository whose own `make test` runs pytest — one test passes, one fails — and writes coverage.

    It also carries a stale lcov.info in its history: a report that was not written by the run must
    never be read as if it were.
    """
    root = tmp_path / "ledger"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "fixtures").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "core.py").write_text("def total(x):\n    return round(x, 1)\n")
    (root / "tests" / "test_core.py").write_text(
        "from pkg.core import total\n\n\ndef test_small():\n    assert total(1.0) == 1.0\n\n\n"
        "def test_total():\n    assert total(1.05) == 1.05\n")
    (root / "fixtures" / "coverage.xml").write_text(COBERTURA)
    (root / "lcov.info").write_text("SF:pkg/core.py\nLF:100\nLH:100\nend_of_record\n")
    (root / "Makefile").write_text(
        "test:\n"
        f"\t{sys.executable} -m pytest -q -p no:cacheprovider tests; status=$$?; "
        "cp fixtures/coverage.xml coverage.xml; exit $$status\n")
    run_git(["init", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-m", "first"], root)
    return root


@pytest_asyncio.fixture
async def checked(schema: str, tested_repo: Path) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(m.Project(id=CHECKED, name="Ledger", source_kind="local", source_repo=str(tested_repo)))
    yield db
    async with db.session() as s:
        runs = (await s.execute(select(Run).where(Run.project_id == CHECKED))).scalars().unique()
        for run in runs:
            shutil.rmtree(Path(run.worktree), ignore_errors=True)
        await s.execute(delete(Approval).where(Approval.project_id == CHECKED))
        await s.execute(delete(Run).where(Run.project_id == CHECKED))
        await s.execute(delete(m.Setting).where(m.Setting.key == f"runtime.tests.{CHECKED}"))
        await s.execute(delete(m.Project).where(m.Project.id == CHECKED))
    await db.close()


async def start_check(db: Database) -> str:
    async with db.session() as s:
        project = await ProjectRepository(s).get(CHECKED)
        run = await RunService(s, FakeGateway()).check_run(project, "Rajat")
        assert run.role == "check" and [x.kind for x in run.steps] == ["test"]
        return run.ref


def branches(repo: Path) -> list[str]:
    out = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo, capture_output=True,
                         text=True, check=True).stdout
    return [b for b in out.splitlines() if b.strip()]


async def test_a_check_run_asks_first_then_records_what_the_runner_said_and_leaves_nothing(
        checked: Database, tested_repo: Path):
    gateway = FakeGateway()
    ref = await start_check(checked)
    await execute(checked, gateway, ref)

    async with checked.read() as s:
        run = await RunRepository(s).by_ref(ref)
        gate = await ApprovalRepository(s).waiting_on_person(ref)
        worktree = Path(run.worktree)
    # The first time in a project, the command waits for a person — a check run does not go around it.
    assert run.status == "waiting" and gate is not None and "make test" in gate.title
    assert worktree.exists() and run.branch in branches(tested_repo)

    await resume(checked, gateway, ref, 1, approved=True)

    async with checked.read() as s:
        run = await RunRepository(s).by_ref(ref)
        failures = (await s.execute(select(m.TestFailure).where(m.TestFailure.run_id == run.id))).scalars().all()
        coverage = (await s.execute(select(m.TestCoverage).where(m.TestCoverage.run_id == run.id))).scalars().all()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tested_repo, capture_output=True, text=True).stdout.strip()

    assert run.status == "failed" and run.tests_status == "failed"
    assert (run.tests_runner, run.tests_passed, run.tests_failed, run.tests_total) == ("pytest", 1, 1, 2)
    assert run.tests_sha == head
    assert [(f.name, f.file, f.line) for f in failures] == [("tests/test_core.py::test_total", "tests/test_core.py", 9)]
    assert "assert 1.1 == 1.05" in failures[0].message
    # Only the report the command wrote: the committed lcov.info claims 100% and is not read.
    assert [(c.path, c.covered, c.total, c.source) for c in coverage] == [("pkg", 3, 4, "cobertura")]
    # However it ended, a check run leaves no worktree and no branch.
    assert run.removed is True and not worktree.exists()
    assert run.branch not in branches(tested_repo)


async def test_stopping_a_check_run_at_its_gate_removes_it_and_answers_the_gate(checked: Database, tested_repo: Path):
    gateway = FakeGateway()
    ref = await start_check(checked)
    await execute(checked, gateway, ref)

    async with checked.session() as s:
        stopped_run = await RunService(s, gateway).cancel(ref, "Rajat")
        worktree, branch = Path(stopped_run.worktree), stopped_run.branch
    async with checked.read() as s:
        run = await RunRepository(s).by_ref(ref)
        gates = await ApprovalRepository(s).for_run(ref)

    assert run.status == "cancelled" and run.removed is True and run.waiting_on is None
    assert not worktree.exists() and branch not in branches(tested_repo)
    # The inbox does not keep asking to run a command for a run that is gone.
    assert [g.status for g in gates] == ["denied"]


async def test_a_second_check_run_is_refused_while_one_is_working(checked: Database):
    await start_check(checked)
    with pytest.raises(Refused, match="already running"):
        await start_check(checked)


async def test_a_project_that_refused_its_tests_starts_no_check_run(checked: Database):
    async with checked.session() as s:
        s.add(m.Setting(key=f"runtime.tests.{CHECKED}", value="refused"))
    with pytest.raises(Refused, match="chose not to run tests"):
        await start_check(checked)


def test_only_failures_every_one_of_which_is_expected_keep_the_gate_calm():
    assert all_expected(counted=2, recorded=2, expected=2)
    assert not all_expected(counted=2, recorded=2, expected=1)       # one real failure among them
    assert not all_expected(counted=3, recorded=2, expected=2)       # a failure nobody recorded
    assert not all_expected(counted=None, recorded=2, expected=2)    # output nobody could read
    assert not all_expected(counted=0, recorded=0, expected=0)       # red with nothing named


async def test_a_legacy_failure_does_not_raise_the_signature_but_a_new_one_does(
        checked: Database, tested_repo: Path):
    async with checked.session() as s:
        s.add(m.Setting(key=f"runtime.tests.{CHECKED}", value="allowed"))
        await s.flush()
        s.add(m.Plan(id="p-check", ref="PLAN-9002", project_id=CHECKED, status="draft",
                     raw_requirement="Keep the ledger total", affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(PlanStep(id="p-check-1", plan_id="p-check", n=1, label="Touch pkg/core.py", agent="Backend Engineer"))

    async def gate_risk() -> tuple[str, str]:
        edit = json.dumps({"summary": "Comment.", "files": [
            {"path": "pkg/core.py", "content": "# ledger\ndef total(x):\n    return round(x, 1)\n"}]})
        gateway = FakeGateway(edit, json.dumps({"findings": [], "verdict": "Fine."}))
        async with checked.session() as s:
            project = await ProjectRepository(s).get(CHECKED)
            plan = (await s.execute(select(Plan).where(Plan.ref == "PLAN-9002"))).scalar_one()
            ref = (await RunService(s, gateway).plan_runs(plan, None, project, "Rajat"))[-1].ref
        await execute(checked, gateway, ref)
        async with checked.read() as s:
            gate = await ApprovalRepository(s).waiting_on_person(ref)
        return gate.risk, gate.payload

    risk, payload = await gate_risk()
    assert risk == "HIGH" and "tests failed" in payload

    async with checked.session() as s:
        s.add(m.TestExpectation(id="te-ledger", project_id=CHECKED, test_name="tests/test_core.py::test_total",
                                kind="legacy", reason="Rounds half-down since 2019; finance signs this off."))
    risk, payload = await gate_risk()
    assert risk == "MEDIUM" and "1 failure, all expected" in payload
