"""A step tried again to a stated policy — and the failures that are answers, not failures.

The gateway already moves to the next lane when a provider is down, so a provider outage was handled
and a provider that answers with prose instead of JSON was not: the parse raised, the step failed for
good, and a nine-step run ended with a hole in it. A step now carries how many times it has been tried
and how many times it may be, and `execute` tries it that many times with a widening wait.

Four failures are never tried again, and each says so in the run log: a tool rule's deny, because a
rule is an answer; `NoModel`, because the gateway already walked every lane; a path the runtime
refuses, because the same step would propose it again; and a person's stop. A fifth is refused by
measurement rather than by type — a step that had already written to the worktree, because taking
that back is a resume's job.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.ai.gateway import NoModel, Provider, Result
from app.data.engine import Database
from app.models import Approval, Chunk, Plan, PlanStep, Project, ProjectSource, Run, RunStep, Setting, ToolRule
from app.repositories import ProjectRepository, RunRepository
from app.schemas import run_json
from app.services import runs as runtime
from app.services.runs import DEFAULT_TRIES, MAX_TRIES, RunService, _tries, execute

PID, PLAN = "retry-project", "PLAN-9601"
ORIGINAL = "def total(x):\n    return x\n"
ROUNDED = "def total(x):\n    return round(x, 2)\n"
PROSE = "Sure! Here is what I would change: round the total to two places."


class Scripted:
    """Answers with a script; an entry that is an exception is raised instead of answered."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.asked: list[str] = []
        self.prompts: list[list[dict[str, str]]] = []

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def chain(self, role: str | None = None, limit: int = 20) -> list[Any]:
        return []

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Any:
        self.asked.append(kw.get("feature", ""))
        self.prompts.append(messages)
        raw = self.script.pop(0) if self.script else '{"summary": "nothing", "files": []}'
        if isinstance(raw, BaseException):
            raise raw
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 20)

    def writes(self) -> int:
        return sum(1 for f in self.asked if f == "agent")


def wrote(*files: tuple[str, str]) -> str:
    return json.dumps({"summary": "Rounded.", "files": [{"path": p, "content": c} for p, c in files]})


REVIEWED = json.dumps({"findings": [], "verdict": "Reads fine."})


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest_asyncio.fixture(autouse=True)
def quick_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait between tries is real and is measured in seconds. A test asserts that it happens and
    that a stop cuts it short, not how long a person would sit through it."""
    monkeypatch.setattr(runtime, "RETRY_BASE_S", 0.01)
    monkeypatch.setattr(runtime, "RETRY_CAP_S", 0.05)


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(ORIGINAL)
    (root / "Makefile").write_text(f"test:\n\t{sys.executable} -c \"print('1 passed')\"\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    return root


@pytest_asyncio.fixture
async def live(schema: str, repo: Path) -> AsyncIterator[Database]:
    db = Database(url=schema)
    await _forget(db)
    async with db.session() as s:
        s.add(Project(id=PID, name="Retried", source_kind="local", source_repo=str(repo)))
        await s.flush()
        s.add(Plan(id="p-ret", ref=PLAN, project_id=PID, status="draft",
                   raw_requirement="Round the invoice total", affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(PlanStep(id="p-ret-1", plan_id="p-ret", n=1, label="Fix rounding in pkg/core.py",
                       agent="Backend Engineer"))
        s.add(Setting(key=f"runtime.tests.{PID}", value="allowed"))
    yield db
    await _forget(db)
    await db.close()


async def _forget(db: Database) -> None:
    async with db.session() as s:
        for run in (await s.execute(select(Run).where(Run.project_id == PID))).scalars().unique():
            if Path(run.repo).is_dir():
                runtime._cleanup(run)
        await s.execute(delete(Approval).where(Approval.project_id == PID))
        await s.execute(delete(Run).where(Run.project_id == PID))
        await s.execute(delete(Plan).where(Plan.project_id == PID))
        await s.execute(delete(ToolRule).where(ToolRule.project_id == PID))
        await s.execute(delete(Chunk).where(Chunk.project_id == PID))
        await s.execute(delete(ProjectSource).where(ProjectSource.project_id == PID))
        await s.execute(delete(Setting).where(Setting.key.like(f"runtime.%.{PID}%")))
        await s.execute(delete(Project).where(Project.id == PID))


async def dispatch(db: Database, gateway: Scripted) -> str:
    async with db.session() as s:
        project = await ProjectRepository(s).get(PID)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        made = await RunService(s, gateway).plan_runs(plan, None, project, "Rajat")
        return made[-1].ref


async def state(db: Database, ref: str) -> Run:
    async with db.read() as s:
        return await RunRepository(s).by_ref(ref)


async def logs_of(db: Database, run_id: str) -> list[str]:
    async with db.read() as s:
        return [x.line for x in await runtime.RunLogRepository(s).after(run_id)]


def step_one(run: Run) -> RunStep:
    return next(x for x in run.steps if x.n == 1)


# ── the policy ───────────────────────────────────────────────────
def test_a_policy_is_bounded_and_falls_back_to_the_kind_s_own_default():
    assert _tries({"maxAttempts": 3}, "edit") == 3 and _tries({"maxAttempts": 1}, "edit") == 1
    # Outside the bound, or not a number at all, is the kind's default rather than a guess.
    assert _tries({"maxAttempts": 0}, "edit") == DEFAULT_TRIES["edit"]
    assert _tries({"maxAttempts": MAX_TRIES + 1}, "edit") == DEFAULT_TRIES["edit"]
    assert _tries({"maxAttempts": True}, "edit") == DEFAULT_TRIES["edit"]      # a bool is not a count
    assert _tries({"maxAttempts": "2"}, "edit") == DEFAULT_TRIES["edit"]
    assert _tries(None, "edit") == 2 and _tries(None, "review") == 2
    assert _tries(None, "test") == 1 and _tries(None, "merge") == 1 and _tries(None, "handoff") == 1


async def test_the_steps_a_run_is_made_with_carry_the_policy_their_kind_has(live: Database):
    ref = await dispatch(live, Scripted())
    run = await state(live, ref)
    tries = {x.kind: x.max_attempts for x in run.steps}
    assert tries == {"edit": 2, "test": 1, "review": 2, "handoff": 1}
    assert all(x.attempts == 0 for x in run.steps)          # nothing has been tried yet, and it says so


# ── what is tried again ──────────────────────────────────────────
async def test_a_model_that_answers_with_prose_is_asked_once_more_and_the_step_finishes(live: Database):
    gateway = Scripted(PROSE, wrote(("pkg/core.py", ROUNDED)), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run = await state(live, ref)
    step = step_one(run)
    assert step.status == "done" and step.attempts == 2 and step.max_attempts == 2
    assert gateway.writes() == 2                            # two calls, each its own line in the ledger
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ROUNDED
    lines = await logs_of(live, run.id)
    assert any("attempt 1 of 2 · ValueError: the answer holds no JSON object" in x for x in lines)
    assert any("trying step 1 again" in x and "attempt 2 of 2" in x for x in lines)
    # The screen reads it from the step, and only says so because there was more than one try to report.
    shown = run_json(run)["steps"][0]
    assert shown["attempts"] == 2 and shown["maxAttempts"] == 2


async def test_a_model_that_never_answers_properly_stops_at_the_bound(live: Database):
    gateway = Scripted(PROSE, PROSE, PROSE, REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run = await state(live, ref)
    step = step_one(run)
    assert step.status == "failed" and step.attempts == 2   # exactly max_attempts, never a third
    assert gateway.writes() == 2
    assert any("not tried again · it is tried at most 2 times" in x for x in await logs_of(live, run.id))
    assert run.status == "failed"


# ── what is never tried again, and why ───────────────────────────
async def test_no_lane_at_all_is_not_asked_twice(live: Database):
    gateway = Scripted(NoModel("No model is configured."), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run = await state(live, ref)
    step = step_one(run)
    # `_edit` handles NoModel itself and skips the step, which is the older and better answer: the loop
    # never sees it, so nothing is tried again and nothing pretends a model wrote anything.
    assert step.status == "skipped" and step.attempts == 1
    assert "will not pretend to write code it cannot write" in step.detail
    assert gateway.writes() == 1


async def test_a_tool_rule_s_deny_ends_the_step_on_the_first_try(live: Database):
    async with live.session() as s:
        made = ToolRule(project_id=PID, tool="edit", pattern="pkg/*", action="deny", note="")
        s.add(made)
        await s.flush()
        rule_id = made.id
    gateway = Scripted(wrote(("pkg/core.py", ROUNDED)), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run = await state(live, ref)
    step = step_one(run)
    assert step.status == "failed" and step.attempts == 1 and f"Rule #{rule_id}" in step.detail
    assert gateway.writes() == 1                            # a rule's answer is not asked again
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ORIGINAL


async def test_a_path_the_runtime_refuses_is_not_proposed_again(live: Database):
    gateway = Scripted(wrote(("../escape.py", "nope\n")), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run = await state(live, ref)
    step = step_one(run)
    assert step.status == "failed" and step.attempts == 1 and "refused to write" in step.detail
    assert gateway.writes() == 1


async def test_a_person_s_stop_ends_the_wait_instead_of_sitting_it_out(live: Database, monkeypatch):
    # Long enough that waiting it out would be obvious; the stop is what makes it not happen. `execute`
    # clears the flag when it starts, so the stop is pressed where a person would press it: while the
    # step is failing and the runtime is about to sit in a backoff.
    monkeypatch.setattr(runtime, "RETRY_BASE_S", 30.0)
    monkeypatch.setattr(runtime, "RETRY_CAP_S", 30.0)
    gateway = Scripted(PROSE, wrote(("pkg/core.py", ROUNDED)), REVIEWED)
    ref = await dispatch(live, gateway)
    asked = gateway.ask

    def stop_while_it_fails(*args: Any, **kw: Any) -> Any:
        runtime.stopped(ref).set()
        return asked(*args, **kw)

    gateway.ask = stop_while_it_fails
    started = time.monotonic()
    await execute(live, gateway, ref)
    waited = time.monotonic() - started

    run = await state(live, ref)
    assert step_one(run).attempts == 1 and gateway.writes() == 1
    assert waited < 10                                      # the backoff was cut short, not sat out
    assert run.status == "cancelled" and "Stopped by you" in run.note
