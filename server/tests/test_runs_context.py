"""Telling a step what the steps before it did.

A step's output reaches the next step as files in a git worktree, which is a better channel than a
serialised dict for code — it is inspectable, diffable and revertable — and an empty one for intent.
Step 4 had no way of knowing that step 2 had already added the column it was about to add.

`_so_far` is that channel, and it is deliberately not a summariser: every line is a summary the
runtime already stored or a result it already measured. A run on its first step gets nothing at all,
because a heading with nothing under it reads as a fact that went missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from sqlalchemy import delete, select

from app.ai.gateway import Provider, Result
from app.data.engine import Database
from app.models import Approval, Chunk, Plan, PlanStep, Project, ProjectSource, Run, RunStep, Setting, ToolRule
from app.repositories import ProjectRepository
from app.services import runs as runtime
from app.services.runs import SO_FAR_STEPS, RunService, _so_far, execute

PID, PLAN = "context-project", "PLAN-9701"
ORIGINAL = "def total(x):\n    return x\n"
ROUNDED = "def total(x):\n    return round(x, 2)\n"
TAXED = "RATE = 0.25\n"


def a_run(*steps: tuple[int, str, str, str, str], **run_fields: Any) -> Run:
    """A run held in memory only: `_so_far` is a pure function and needs no database to be read."""
    run = Run(id="r", ref="RUN-1", project_id=PID, branch="b", worktree="/w", repo="/r",
              review=run_fields.pop("review", {}), **run_fields)
    run.steps = [RunStep(run_id="r", n=n, kind=kind, label=label, agent=agent, status=status, detail=detail)
                 for n, kind, label, agent, status, detail in
                 [(n, k, la, ag, st, de) for n, k, la, ag, st, de in steps]]
    return run


def test_a_run_on_its_first_step_is_told_nothing_rather_than_an_empty_heading():
    run = a_run((1, "edit", "Write the migration", "Backend", "running", ""))
    assert _so_far(run, 1) == ""


def test_only_the_steps_that_ran_and_only_the_ones_before_this_one_are_named():
    run = a_run(
        (1, "edit", "Write the migration", "Backend", "done", "Added billing_lines, with a down migration."),
        (2, "edit", "Write the endpoint", "Backend", "done", "POST /billing/lines, 201."),
        (3, "edit", "Write the client", "Frontend", "skipped", "Not run: the plan named no file here."),
        (4, "edit", "Reconcile the types", "Backend", "failed", "The model answered with prose."),
        (5, "edit", "Fix what the checks found", "Backend", "todo", ""),
        (6, "edit", "Tidy up", "Backend", "running", ""))
    said = _so_far(run, 5)
    assert said.startswith("Earlier steps of this run, and what they did:\n")
    lines = said.splitlines()[1:]
    assert len(lines) == 4                                   # four ran; the todo one and step 5 itself are not there
    assert lines[0] == "1. Write the migration (Backend) — done: Added billing_lines, with a down migration."
    assert lines[2] == "3. Write the client (Frontend) — skipped: Not run: the plan named no file here."
    assert lines[3] == "4. Reconcile the types (Backend) — failed: The model answered with prose."
    assert "Fix what the checks found" not in said and "Tidy up" not in said


def test_a_test_step_contributes_the_runner_s_own_line_and_never_a_description():
    run = a_run((1, "test", "Run the project's tests · make test", "Tester", "done", "ignored"),
                tests_status="failed", tests_summary="140 passed, 2 failed")
    assert _so_far(run, 2).splitlines()[1] == \
        "1. Run the project's tests · make test (Tester) — failed (140 passed, 2 failed)"


def test_a_check_contributes_its_own_result_rather_than_the_run_s_tests():
    run = a_run((1, "test", "Run the project's tsc · npm run tsc", "Tester", "done", "ignored"),
                tests_status="passed", tests_summary="all good",
                review={"checks": [{"step": 1, "name": "tsc", "command": "npm run tsc",
                                    "status": "failed", "summary": "3 errors"}]})
    assert _so_far(run, 2).splitlines()[1].endswith("— failed (3 errors)")


def test_a_step_with_no_detail_still_says_what_became_of_it():
    run = a_run((1, "merge", "Merge what Backend wrote", "Orchestrator", "done", ""))
    assert _so_far(run, 2).splitlines()[1] == "1. Merge what Backend wrote (Orchestrator) — done"


def test_a_long_run_is_told_about_the_last_few_steps_and_nothing_is_cut_mid_summary():
    detail = "x" * 300                                       # already capped where it is written
    run = a_run(*[(n, "edit", f"Step {n}", "Backend", "done", detail) for n in range(1, 13)])
    lines = _so_far(run, 13).splitlines()[1:]
    assert len(lines) == SO_FAR_STEPS
    assert lines[0].startswith("5. Step 5")                  # the last eight, in order, oldest first
    assert lines[-1].startswith("12. Step 12")
    assert all(line.endswith(detail) for line in lines)      # no second truncation


# ── and what a step actually receives ────────────────────────────
class Scripted:
    def __init__(self, *script: str) -> None:
        self.script, self.prompts = list(script), []

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def chain(self, role: str | None = None, limit: int = 20) -> list[Any]:
        return []

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Any:
        self.prompts.append(messages)
        raw = self.script.pop(0) if self.script else '{"summary": "nothing", "files": []}'
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 20)


def wrote(summary: str, *files: tuple[str, str]) -> str:
    return json.dumps({"summary": summary, "files": [{"path": p, "content": c} for p, c in files]})


REVIEWED = json.dumps({"findings": [], "verdict": "Reads fine."})


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(ORIGINAL)
    (root / "pkg" / "tax.py").write_text("RATE = 0.2\n")
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
        s.add(Project(id=PID, name="Contextual", source_kind="local", source_repo=str(repo)))
        await s.flush()
        s.add(Plan(id="p-ctx", ref=PLAN, project_id=PID, status="draft",
                   raw_requirement="Round the invoice total", affected_files=["pkg/core.py", "pkg/tax.py"]))
        await s.flush()
        s.add(PlanStep(id="p-ctx-1", plan_id="p-ctx", n=1, label="Fix rounding in pkg/core.py",
                       agent="Backend Engineer"))
        s.add(PlanStep(id="p-ctx-2", plan_id="p-ctx", n=2, label="Raise the tax rate",
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


async def test_step_two_is_told_what_step_one_did_and_so_is_the_reviewer(live: Database):
    gateway = Scripted(wrote("Rounded the total to two places.", ("pkg/core.py", ROUNDED)),
                       wrote("Raised the rate to 0.25.", ("pkg/tax.py", TAXED)), REVIEWED)
    async with live.session() as s:
        project = await ProjectRepository(s).get(PID)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        ref = (await RunService(s, gateway).plan_runs(plan, None, project, "Rajat"))[-1].ref
    await execute(live, gateway, ref)

    first, second, reviewed = gateway.prompts[0][-1], gateway.prompts[1][-1], gateway.prompts[2][-1]
    assert "Earlier steps of this run" not in first["content"]
    assert "1. Fix rounding in pkg/core.py (Backend Engineer) — done: Rounded the total to two places." \
        in second["content"]
    assert "Raise the tax rate" not in second["content"].split("Earlier steps of this run")[1]
    # The reviewer is told the same thing: a diff of several steps is not readable without it.
    assert "2. Raise the tax rate (Backend Engineer) — done: Raised the rate to 0.25." in reviewed["content"]
