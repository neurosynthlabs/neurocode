"""A project's own checks, and the review receipt, against a real git repository.

Nothing is mocked except the model. The repository's Makefile carries a lint target that really fails,
so the check really runs behind the same first-time gate as the tests; and a commit added to a branch
after its review really changes the patch, so merge refuses until the review is read again.
"""
from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app import agent
from app import models as m
from app.ai.gateway import Provider, Result
from app.data.engine import Database
from app.models import Approval, Plan, PlanStep, Project, Run
from app.repositories import ApprovalRepository, ProjectRepository, RunRepository
from app.schemas import run_json
from app.services import runs as runtime
from app.services.errors import Refused
from app.services.runs import RunService, check_key, execute, resume

PROJECT, PLAN = "checks-project", "PLAN-9101"
WROTE = "def total(x):\n    return round(x, 2)\n"


class FakeGateway:
    """Answers with a script and keeps every prompt it was handed."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.prompts: list[tuple[str, str]] = []

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Result[Any]:
        self.prompts.append((kw.get("feature", ""), messages[-1]["content"]))
        raw = self.script.pop(0) if self.script else json.dumps({"findings": [], "verdict": "Fine."})
        return Result(parse(raw), Provider("mistral", "mistral-small"), 20)


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


def edit(content: str = WROTE) -> str:
    return json.dumps({"summary": "Rounded.", "files": [{"path": "pkg/core.py", "content": content}]})


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> Path:
    """A repository whose own Makefile has a test target that passes and a lint target that fails."""
    root = tmp_path / "linted"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def total(x):\n    return x\n")
    (root / "Makefile").write_text("test:\n\t@echo '1 passed'\n\n"
                                   "lint:\n\t@echo 'pkg/core.py:2: E501 line too long'; exit 3\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    return root


@pytest_asyncio.fixture
async def live(schema: str, repo: Path) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Linted", source_kind="local", source_repo=str(repo)))
        await s.flush()
        s.add(m.Setting(key=f"runtime.tests.{PROJECT}", value="allowed"))
        s.add(Plan(id="p-checks", ref=PLAN, project_id=PROJECT, status="draft",
                   raw_requirement="Round the invoice total to two places", affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(PlanStep(id="p-checks-1", plan_id="p-checks", n=1, label="Fix rounding in pkg/core.py",
                       agent="Backend Engineer"))
    yield db
    async with db.session() as s:
        for run in (await s.execute(select(Run).where(Run.project_id == PROJECT))).scalars().unique():
            shutil.rmtree(Path(run.worktree), ignore_errors=True)
        await s.execute(delete(Approval).where(Approval.project_id == PROJECT))
        await s.execute(delete(Run).where(Run.project_id == PROJECT))
        await s.execute(delete(Plan).where(Plan.project_id == PROJECT))
        await s.execute(delete(m.Setting).where(m.Setting.key.startswith(f"runtime.tests.{PROJECT}")))
        await s.execute(delete(m.Setting).where(m.Setting.key.startswith(f"runtime.checks.{PROJECT}.")))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


async def dispatch(db: Database, gateway: FakeGateway) -> str:
    async with db.session() as s:
        project = await ProjectRepository(s).get(PROJECT)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        return (await RunService(s, gateway).plan_runs(plan, None, project, "Rajat"))[-1].ref


async def load(db: Database, ref: str) -> Run:
    async with db.read() as s:
        return await RunRepository(s).by_ref(ref)


async def settle(db: Database, gateway: FakeGateway, ref: str, step: int, approved: bool) -> None:
    """What POST /approvals/{ref}/{decision} does: settle the gate, then resume the run."""
    async with db.session() as s:
        gate = await ApprovalRepository(s).waiting_on_person(ref)
        gate.status = "approved" if approved else "denied"
    await resume(db, gateway, ref, step, approved=approved)


async def accepted(db: Database, gateway: FakeGateway) -> str:
    """A run whose lint check was allowed, which finished its work and was accepted by a person."""
    async with db.session() as s:
        s.add(m.Setting(key=check_key(PROJECT, "lint"), value="allowed"))
    ref = await dispatch(db, gateway)
    await execute(db, gateway, ref)
    run = await load(db, ref)
    handoff = next(x for x in run.steps if x.kind == "handoff")
    await settle(db, gateway, ref, handoff.n, True)
    return ref


# ── detection ────────────────────────────────────────────────────
def test_checks_are_the_project_s_own_commands_found_in_the_repository(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch):
    installed = {"make", "npm", "ruff", "go"}
    hands = importlib.import_module("app.agent.git")
    monkeypatch.setattr(hands.shutil, "which", lambda tool: f"/bin/{tool}" if tool in installed else None)

    node = tmp_path / "node"
    node.mkdir()
    (node / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint .", "type-check": "tsc --noEmit"}}))
    assert agent.detect_checks(node) == [
        {"name": "lint", "argv": ["npm", "run", "--silent", "lint"], "command": "npm run --silent lint"},
        {"name": "typecheck", "argv": ["npm", "run", "--silent", "type-check"],
         "command": "npm run --silent type-check"}]

    # A Makefile target says what the project runs, and wins; mypy is configured but not installed.
    py = tmp_path / "py"
    py.mkdir()
    (py / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n\n[tool.mypy]\nstrict = true\n")
    assert [c["command"] for c in agent.detect_checks(py)] == ["ruff check ."]
    (py / "Makefile").write_text("lint:\n\truff check .\n")
    assert [c["command"] for c in agent.detect_checks(py)] == ["make lint"]

    go = tmp_path / "go"
    go.mkdir()
    (go / "go.mod").write_text("module example.com/shop\n")
    assert agent.detect_checks(go) == [{"name": "lint", "argv": ["go", "vet", "./..."], "command": "go vet ./..."}]

    # Nothing configured, or a script file that is not JSON: no check, not a guess.
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "package.json").write_text("{not json")
    assert agent.detect_checks(bare) == []


# ── the gate, the result, and the review it feeds ────────────────
async def test_a_check_asks_first_then_runs_and_its_failure_reaches_the_review_and_the_signature(
        live: Database):
    gateway = FakeGateway(edit())
    ref = await dispatch(live, gateway)
    run = await load(live, ref)
    assert [(x.kind, x.label) for x in run.steps][1:] == [
        ("test", "Run the project's tests · make test"), ("test", "Run the project's lint · make lint"),
        ("review", "Review the diff"), ("handoff", "Your approval")]

    await execute(live, gateway, ref)
    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
        gate = await ApprovalRepository(s).waiting_on_person(ref)
    # The tests were allowed once already; the lint check has never been, so it waits for a person.
    assert run.status == "waiting" and gate.title.startswith("Run `make lint`") and gate.step == 3
    assert run.tests_status == "passed"

    await settle(live, gateway, ref, 3, True)
    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
        answer = await s.get(m.Setting, check_key(PROJECT, "lint"))
        gate = await ApprovalRepository(s).waiting_on_person(ref)
    [check] = run.review["checks"]
    assert answer.value == "allowed"
    assert (check["name"], check["status"], check["exit"]) == ("lint", "failed", 2)
    assert "E501 line too long" in check["output"][0] and check["summary"].startswith("failed (exit 2)")
    lint = next(x for x in run.steps if x.n == 3)
    assert lint.status == "done" and lint.detail.startswith("failed")
    # The reviewer was told, and so is the person signing.
    review_prompt = next(p for f, p in gateway.prompts if f == "review")
    assert "Check lint (make lint): failed" in review_prompt and "E501" in review_prompt
    assert "checks: lint failed" in gate.payload, gate.payload
    assert gate.risk == "HIGH"

    shown = run_json(run)
    assert shown["checks"][0]["status"] == "failed" and "checks" not in shown["review"]


async def test_a_refused_check_is_skipped_and_remembered(live: Database):
    gateway = FakeGateway(edit())
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    await settle(live, gateway, ref, 3, False)
    run = await load(live, ref)
    async with live.read() as s:
        answer = await s.get(m.Setting, check_key(PROJECT, "lint"))
    assert answer.value == "refused"
    assert run.review["checks"][0]["status"] == "skipped"
    assert next(x for x in run.steps if x.n == 3).detail == "You chose not to run the lint check in this project."

    # The next run does not ask again: the answer is the project's.
    second = FakeGateway(edit("def total(x):\n    return round(x, 3)\n"))
    async with live.session() as s:
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        project = await ProjectRepository(s).get(PROJECT)
        again = (await RunService(s, second).plan_runs(plan, None, project, "Rajat"))[-1].ref
    await execute(live, second, again)
    async with live.read() as s:
        gate = await ApprovalRepository(s).waiting_on_person(again)
    assert gate.tool.startswith("Merge(")                    # straight to the signature


# ── the receipt ──────────────────────────────────────────────────
async def test_the_review_keeps_a_receipt_of_exactly_the_patch_it_read(live: Database):
    gateway = FakeGateway(edit())
    ref = await accepted(live, gateway)
    run = await load(live, ref)
    patch, head = agent.branch_diff(Path(run.repo), run.base, run.branch)
    receipt = run.review["receipt"]
    assert receipt["sha256"] == agent.fingerprint(patch) and receipt["head"] == head
    assert receipt["by"] == "mistral-small" and receipt["truncated"] is False
    review_prompt = next(p for f, p in gateway.prompts if f == "review")
    assert f"Diff:\n{patch}\n\nTests:" in review_prompt      # what was fingerprinted is what was read


async def test_merge_refuses_a_branch_that_changed_after_its_review_until_it_is_read_again(
        live: Database, repo: Path):
    gateway = FakeGateway(edit())
    ref = await accepted(live, gateway)
    run = await load(live, ref)
    tree = Path(run.worktree)
    (tree / "pkg" / "core.py").write_text("def total(x):\n    return x  # changed after review\n")
    run_git(["commit", "-qam", "slipped in"], tree)

    async with live.session() as s:
        with pytest.raises(Refused, match="changed since it was reviewed") as refused:
            await RunService(s, gateway).merge(ref, "Rajat")
    assert "Review it again, then merge" in str(refused.value)
    assert "changed after review" not in (repo / "pkg" / "core.py").read_text()

    async with live.session() as s:
        marked, step_n = await RunService(s, gateway).review_again(ref, "Rajat")
        assert next(x for x in marked.steps if x.n == step_n).status == "running"
    async with live.session() as s:
        with pytest.raises(Refused, match="already asked for it to be read again"):
            await RunService(s, gateway).review_again(ref, "Asha")
    await runtime.reread(live, gateway, ref, step_n, "Rajat")

    run = await load(live, ref)
    step = next(x for x in run.steps if x.n == step_n)
    assert step.status == "done" and "reviewing" not in run.review
    assert "changed after review" in next(p for f, p in reversed(gateway.prompts) if f == "review")
    async with live.session() as s:
        result = await RunService(s, gateway).merge(ref, "Rajat")
    assert result["merged"] is True
    assert "changed after review" in (repo / "pkg" / "core.py").read_text()


async def test_reading_again_is_refused_while_a_run_works_and_for_a_run_that_only_tested(live: Database):
    gateway = FakeGateway(edit())
    ref = await dispatch(live, gateway)
    async with live.session() as s:
        with pytest.raises(Refused, match="still working"):
            await RunService(s, gateway).review_again(ref, "Rajat")
        run = await RunRepository(s).by_ref(ref)
        run.parent_id = None
        run.role = "check"
        await s.flush()
        with pytest.raises(Refused, match="only ran the project's tests"):
            await RunService(s, gateway).review_again(ref, "Rajat")
