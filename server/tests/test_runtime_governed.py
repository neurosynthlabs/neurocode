"""The runtime, grounded and governed — against real git repositories, with nothing mocked but the model.

Each agent step is handed the project's instructions for its files and retrieval's pieces for its words,
and the run keeps what it was handed. Every file an agent writes and every command a run executes goes
through the tool rules: a deny ends the step naming the rule, an ask stops the run at a gate whose
answers are "once", "for this run", "always in this project" or "refuse". An agent may stop to ask a
question; the answer is kept on the step and remembered. A run can be taken back to any step.

Two halves, as elsewhere: the pipeline runs for real against the test database with committed
transactions (`Database`), and the routes are driven over HTTP inside the rolled-back transaction, with
the background job recorded rather than run.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent
from app import models as m
from app.ai.gateway import Provider, Result
from app.api import deps, routes_runs, routes_work
from app.api.app import create_api
from app.data.engine import Database
from app.models import (
    Approval,
    Chunk,
    MemoryFact,
    Plan,
    PlanStep,
    Project,
    ProjectSource,
    Run,
    Setting,
    TasteRule,
    TasteSignal,
    ToolRule,
)
from app.repositories import ApprovalRepository, ProjectRepository, RunRepository
from app.schemas import approval_json, run_json
from app.services import runs as runtime
from app.services.errors import Refused
from app.services.gates import ApprovalService, literal_pattern
from app.services.runs import RunService, execute, resume
from tests.fixtures.workspace import load_workspace

PID, PLAN = "governed-project", "PLAN-9401"
ORIGINAL = "def total(x):\n    return x\n"
ROUNDED = "def total(x):\n    return round(x, 2)\n"
AGENTS = "# Shop rules\nEvery money value is rounded with round(x, 2).\n"
SQL_RULE = "---\npaths:\n  - migrations/**\n---\nMigrations are append-only.\n"
OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
APPROVER = {"email": "approver@example.com", "name": "Asha", "password": "another long passphrase"}
HEADERS = {"X-NC-Client": "test"}


class FakeGateway:
    """Answers with a script and keeps every prompt it was sent. No provider is ever called."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.asked: list[str] = []
        self.prompts: list[list[dict[str, str]]] = []

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def chain(self, role: str | None = None, limit: int = 20) -> list[Any]:
        return []

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Result[Any]:
        self.asked.append(kw.get("feature", ""))
        self.prompts.append(messages)
        raw = self.script.pop(0) if self.script else '{"summary": "nothing", "files": []}'
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 20)


def wrote(*files: tuple[str, str], summary: str = "Rounded.") -> str:
    return json.dumps({"summary": summary, "files": [{"path": p, "content": c} for p, c in files]})


REVIEWED = json.dumps({"findings": [], "verdict": "Reads fine."})


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    return root


# ── the pipeline, for real ───────────────────────────────────────
@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "shop", {
        "pkg/core.py": ORIGINAL, "pkg/tax.py": "RATE = 0.2\n", "AGENTS.md": AGENTS,
        ".claude/rules/sql.md": SQL_RULE,
        "Makefile": f"test:\n\t{sys.executable} -c \"print('1 passed')\"\n"})


@pytest_asyncio.fixture
async def live(schema: str, repo: Path) -> AsyncIterator[Database]:
    db = Database(url=schema)
    await _forget(db)
    async with db.session() as s:
        s.add(Project(id=PID, name="Governed", source_kind="local", source_repo=str(repo)))
        await s.flush()
        s.add(Plan(id="p-gov", ref=PLAN, project_id=PID, status="draft",
                   raw_requirement="Round the invoice total to two places", affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(PlanStep(id="p-gov-1", plan_id="p-gov", n=1, label="Fix rounding in pkg/core.py",
                       agent="Backend Engineer"))
        s.add(PlanStep(id="p-gov-2", plan_id="p-gov", n=2, label="Round the tax too",
                       agent="Backend Engineer"))
        # The project's tests are allowed once, as a person would have: the gates under test are the rules'.
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
        await s.execute(delete(MemoryFact).where(MemoryFact.project_id == PID))
        await s.execute(delete(Chunk).where(Chunk.project_id == PID))
        await s.execute(delete(TasteSignal).where(TasteSignal.project_id == PID))
        await s.execute(delete(TasteRule).where(TasteRule.project_id == PID))
        await s.execute(delete(ProjectSource).where(ProjectSource.project_id == PID))
        await s.execute(delete(Setting).where(Setting.key.like(f"runtime.%.{PID}%")))
        await s.execute(delete(Project).where(Project.id == PID))


async def one_step(db: Database) -> None:
    async with db.session() as s:
        await s.execute(delete(PlanStep).where(PlanStep.id == "p-gov-2"))


async def dispatch(db: Database, gateway: FakeGateway, *, step_gate: bool = False) -> str:
    async with db.session() as s:
        project = await ProjectRepository(s).get(PID)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        plan.step_gate = step_gate
        made = await RunService(s, gateway).plan_runs(plan, None, project, "Rajat")
        return made[-1].ref


async def rule(db: Database, tool: str, pattern: str, action: str) -> int:
    async with db.session() as s:
        made = ToolRule(project_id=PID, tool=tool, pattern=pattern, action=action, note="")
        s.add(made)
        await s.flush()
        return made.id


async def state(db: Database, ref: str) -> tuple[Run, Approval | None]:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        gate = await ApprovalRepository(s).waiting_on_person(ref)
        return run, gate


async def answer(db: Database, gate: Approval, decision: str, **options: Any) -> None:
    async with db.session() as s:
        await ApprovalService(s).decide(gate.ref, decision, by_id=None, by_name="Rajat", **options)


def edit_step(run: Run, n: int = 1):
    return next(x for x in run.steps if x.n == n)


async def test_each_step_is_handed_its_instructions_and_retrieval_and_the_run_keeps_what_it_was_handed(
        live: Database, repo: Path):
    # A piece the index holds that the plan never named: the step reads it because retrieval found it.
    async with live.session() as s:
        s.add(Chunk(project_id=PID, kind="code", ref="pkg/tax.py#RATE", path="pkg/tax.py", title="RATE", line=1,
                    body="RATE = 0.2  # the tax rate every invoice total is charged"))
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), REVIEWED, REVIEWED)
    await one_step(live)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run, _ = await state(live, ref)
    system, user = gateway.prompts[0][0]["content"], gateway.prompts[0][1]["content"]
    # The instructions come after the system text and before anything that changes per step.
    assert system.startswith(runtime.EDIT_SYSTEM) and "Every money value is rounded" in system
    assert "Migrations are append-only" not in system            # a rule for other paths is not handed
    assert user.startswith("You are the Backend Engineer.\nProject: governed-project\n")
    assert "--- pkg/tax.py\nRATE = 0.2" in user                   # read because retrieval found it
    assert "[code · pkg/tax.py#RATE]" in user

    handed = run.review["grounding"]["1"]
    assert [x["path"] for x in handed["instructions"]] == ["AGENTS.md"]
    assert handed["pieces"][0] == {"kind": "code", "ref": "pkg/tax.py#RATE", "path": "pkg/tax.py", "line": 1,
                                   "how": "lexical"}
    assert {"path": "pkg/tax.py", "via": "retrieval"} in handed["files"]
    assert {"path": "pkg/core.py", "via": "plan"} in handed["files"]

    # The reviewer is held to the same instructions, and the run says so.
    review_system = gateway.prompts[1][0]["content"]
    assert "Every money value is rounded" in review_system and run.review["instructions"][0]["path"] == "AGENTS.md"
    step = run_json(run)["steps"][0]
    assert step["grounding"]["instructions"][0]["path"] == "AGENTS.md" and step["commitSha"] == run.steps[0].commit_sha
    async with live.read() as s:
        lines = [x.line for x in await runtime.RunLogRepository(s).after(run.id)]
    assert any(x.startswith("given 1 instruction file: AGENTS.md") for x in lines)
    assert any(x.startswith("read from retrieval: pkg/tax.py:1") for x in lines)
    assert any(x.startswith("the reviewer was given AGENTS.md") for x in lines)


async def test_adopted_taste_is_handed_to_the_agent_and_the_reviewer_and_a_signature_is_a_signal(live: Database):
    async with live.session() as s:
        taste = TasteRule(project_id=PID, text="Keep money helpers pure: no printing, no globals.", status="active")
        s.add(taste)
        await s.flush()
        ref_ = f"TASTE-{taste.id}"
    await one_step(live)
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    run, gate = await state(live, ref)
    for prompt in gateway.prompts:                                  # the agent's, then the reviewer's
        assert f"- [{ref_}] Keep money helpers pure" in prompt[0]["content"]
    assert run.review["grounding"]["1"]["taste"] == [ref_] and run.review["taste"] == [ref_]

    await answer(live, gate, "approve")
    await resume(live, gateway, ref, gate.step, approved=True)
    async with live.read() as s:
        signals = (await s.execute(select(TasteSignal).where(TasteSignal.project_id == PID))).scalars().all()
    assert [(x.kind, x.payload["run"]) for x in signals] == [("accept", ref)]


async def test_a_rule_scoped_to_paths_is_handed_only_to_the_step_whose_files_fall_under_it(live: Database):
    gateway = FakeGateway(wrote(("migrations/001.sql", "create table t (id int);\n")), REVIEWED)
    async with live.session() as s:
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        plan.affected_files = ["migrations/001.sql"]
    await one_step(live)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    run, _ = await state(live, ref)
    assert "Migrations are append-only" in gateway.prompts[0][0]["content"]
    given = run.review["grounding"]["1"]["instructions"]
    assert [x["path"] for x in given] == ["AGENTS.md", ".claude/rules/sql.md"]
    assert given[1]["matched"] == "migrations/**"


async def test_a_deny_rule_ends_the_step_naming_the_rule_and_writes_nothing(live: Database, repo: Path):
    rule_id = await rule(live, "edit", "pkg/*", "deny")
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/tax.py", "RATE = 0.25\n")), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    run, _ = await state(live, ref)
    step = edit_step(run)
    assert step.status == "failed" and f"Rule #{rule_id}" in step.detail and "pkg/core.py" in step.detail
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ORIGINAL
    assert edit_step(run, 2).status == "failed" and run.diff_files == 0


async def test_an_ask_rule_stops_at_a_gate_and_allowing_it_for_the_run_writes_what_was_proposed(
        live: Database):
    rule_id = await rule(live, "edit", "pkg/*", "ask")
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/core.py", ROUNDED + "# tax\n")), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run, gate = await state(live, ref)
    assert run.status == "waiting" and gate is not None and gate.tool == "Edit(1 file)"
    shown = approval_json(gate)
    assert shown["kind"] == "edit" and shown["options"] == ["once", "run", "project", "deny"]
    assert f"Rule #{rule_id}" in gate.payload and "pkg/core.py" in gate.payload
    assert run.review["asks"]["1"] == {"tool": "edit", "subjects": ["pkg/core.py"], "rules": [rule_id]}
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ORIGINAL        # nothing written yet
    kept = runtime._proposal_file(run.worktree, ref, 1)
    assert kept.is_file()

    await answer(live, gate, "approve", scope="run")
    await resume(live, gateway, ref, gate.step, approved=True)

    run, gate = await state(live, ref)
    # The answer applied exactly what was asked about; the model was not asked again for step 1.
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ROUNDED + "# tax\n"
    assert gateway.asked == ["agent", "agent", "review"] and not kept.exists()
    assert run.grants[0]["scope"] == "run" and run.grants[0]["subject"] == "pkg/core.py"
    assert edit_step(run, 2).status == "done"                   # step 2 wrote under the run's grant, unasked
    assert gate is not None and gate.tool.startswith("Merge(")   # only the signature is left
    async with live.read() as s:
        lines = [x.line for x in await runtime.RunLogRepository(s).after(run.id)]
    assert any("allowed to write pkg/core.py · Rajat allowed it for this run" in x for x in lines)


async def test_allow_once_is_spent_and_refusing_writes_none_of_the_step(live: Database):
    await rule(live, "edit", "pkg/core.py", "ask")
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/core.py", ROUNDED + "# again\n")), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    _, gate = await state(live, ref)
    await answer(live, gate, "approve")                          # no scope: once
    await resume(live, gateway, ref, gate.step, approved=True)

    run, gate = await state(live, ref)
    assert run.grants == [{**run.grants[0], "scope": "once", "step": 1, "used": True}]
    assert gate is not None and gate.step == 2 and gate.tool == "Edit(1 file)"     # step 2 asks again
    await answer(live, gate, "deny")
    await resume(live, gateway, ref, gate.step, approved=False)
    run, _ = await state(live, ref)
    assert edit_step(run, 2).status == "failed" and "none of this step's files were written" in edit_step(run, 2).detail
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ROUNDED           # step 1's, not step 2's
    assert not runtime._proposal_file(run.worktree, ref, 2).exists()


async def test_an_allow_rule_skips_the_first_time_gate_and_the_feed_names_the_rule(live: Database):
    async with live.session() as s:
        await s.execute(delete(Setting).where(Setting.key == f"runtime.tests.{PID}"))   # never asked before
    rule_id = await rule(live, "command", "make test*", "allow")
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), REVIEWED, REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    run, gate = await state(live, ref)
    tests = next(x for x in run.steps if x.kind == "test")
    assert tests.status == "done" and run.tests_status == "passed"
    assert gate is not None and gate.tool.startswith("Merge(")    # no "Run `make test`" gate on the way
    async with live.read() as s:
        assert await s.get(Setting, f"runtime.tests.{PID}") is None    # no standing answer was written
        feed = (await s.execute(select(m.ActivityEvent).where(m.ActivityEvent.project_id == PID,
                                                              m.ActivityEvent.action == "Allowed by a tool rule")))
        details = [e.detail for e in feed.scalars()]
    assert any(f"Rule #{rule_id}" in d and "make test" in d for d in details)


async def test_a_deny_rule_skips_the_command_and_an_ask_rule_asks_every_run_until_allowed(live: Database):
    denied = await rule(live, "command", "make *", "deny")
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), REVIEWED, REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    run, _ = await state(live, ref)
    tests = next(x for x in run.steps if x.kind == "test")
    assert tests.status == "skipped" and f"Rule #{denied}" in tests.detail and run.tests_status == "not run"

    async with live.session() as s:
        (await s.get(ToolRule, denied)).action = "ask"    # the rule wins over the standing answer "allowed"
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), REVIEWED)
    again = await dispatch(live, gateway)
    await execute(live, gateway, again)
    run, gate = await state(live, again)
    assert gate is not None and gate.tool == "Command(make test)" and approval_json(gate)["kind"] == "command"
    await answer(live, gate, "approve", scope="once")
    await resume(live, gateway, again, gate.step, approved=True)
    run, _ = await state(live, again)
    assert run.tests_status == "passed" and run.grants[0]["used"] is True


async def test_an_agent_may_ask_and_the_answer_is_kept_remembered_and_handed_back(live: Database):
    gateway = FakeGateway(json.dumps({"question": "Round half up or half to even?"}),
                          wrote(("pkg/core.py", ROUNDED)), REVIEWED)
    await one_step(live)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)

    run, gate = await state(live, ref)
    assert run.status == "waiting" and gate.tool == "Ask(Backend Engineer)"
    assert approval_json(gate)["kind"] == "question" and approval_json(gate)["options"] == ["answer", "deny"]
    assert edit_step(run).question == "Round half up or half to even?"
    assert run_json(run)["steps"][0]["question"] == "Round half up or half to even?"
    with pytest.raises(Refused, match="Write the answer"):
        await answer(live, gate, "approve")                      # an empty answer is not an answer
    await answer(live, gate, "approve", answer="Half to even, as the ledger does.")
    await resume(live, gateway, ref, gate.step, approved=True)

    run, _ = await state(live, ref)
    assert edit_step(run).answer == "Half to even, as the ledger does." and edit_step(run).status == "done"
    assert "You asked: Round half up or half to even?\nThe person answered: Half to even" in \
        gateway.prompts[1][1]["content"]
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ROUNDED
    async with live.read() as s:
        fact = (await s.execute(select(MemoryFact).where(MemoryFact.project_id == PID))).scalar_one()
    assert fact.category == "decisions" and fact.title == "Round half up or half to even?"
    assert fact.body == "Half to even, as the ledger does." and fact.evidence[0].startswith(f"{ref} · step 1")


async def test_declining_a_question_skips_the_step(live: Database):
    gateway = FakeGateway(json.dumps({"question": "Which currency?"}), wrote(("pkg/tax.py", "RATE = 0.25\n")),
                          REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    _, gate = await state(live, ref)
    await answer(live, gate, "deny")
    await resume(live, gateway, ref, gate.step, approved=False)
    run, _ = await state(live, ref)
    assert edit_step(run).status == "skipped" and "chose not to answer" in edit_step(run).detail
    assert edit_step(run, 2).status == "done"                     # the run carried on with the next step


async def test_a_plan_dispatched_step_by_step_stops_before_each_edit_step_after_the_first(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/tax.py", "RATE = 0.25\n")), REVIEWED)
    ref = await dispatch(live, gateway, step_gate=True)
    await execute(live, gateway, ref)
    run, gate = await state(live, ref)
    assert edit_step(run).status == "done" and edit_step(run, 2).status == "waiting"
    assert gate.tool == "Step(2)" and approval_json(gate)["kind"] == "step" and "done: step 1" in gate.payload
    await answer(live, gate, "approve")
    await resume(live, gateway, ref, 2, approved=True)
    run, gate = await state(live, ref)
    assert edit_step(run, 2).status == "done" and gate.tool.startswith("Merge(")   # tests and review unasked

    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)))
    again = await dispatch(live, gateway, step_gate=True)
    await execute(live, gateway, again)
    _, gate = await state(live, again)
    await answer(live, gate, "deny")
    await resume(live, gateway, again, 2, approved=False)
    run, _ = await state(live, again)
    assert run.status == "cancelled" and "before step 2" in run.note and edit_step(run, 2).status == "skipped"
    assert not run.removed                                              # its branch stays to be read


async def test_a_merge_run_waits_for_an_agent_stopped_at_a_gate_and_starts_when_it_finishes(live: Database):
    async with live.session() as s:
        (await s.get(PlanStep, "p-gov-2")).agent = "Frontend Engineer"     # two agents: a worktree each
    await rule(live, "edit", "pkg/tax.py", "ask")
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/tax.py", "RATE = 0.25\n")), REVIEWED)
    ref = await dispatch(live, gateway)
    await runtime.execute_batch(live, gateway, ref)

    merge, gate = await state(live, ref)
    assert merge.role == "integration" and merge.status == "waiting" and gate is None
    async with live.read() as s:
        children = (await RunRepository(s).children_of([merge.id]))[merge.id]
        held = next(c for c in children if c.status == "waiting")
        child_gate = await ApprovalRepository(s).waiting_on_person(held.ref)
    assert merge.waiting_on == child_gate.ref and child_gate.tool == "Edit(1 file)"
    assert all(x.status == "todo" for x in merge.steps)                     # nothing merged without the agent

    await answer(live, child_gate, "approve")
    await resume(live, gateway, held.ref, child_gate.step, approved=True)
    merge, gate = await state(live, ref)
    assert [x.status for x in merge.steps if x.kind == "merge"] == ["done", "done"]
    assert gate is not None and gate.tool.startswith("Merge(") and merge.waiting_on == gate.ref
    assert (Path(merge.worktree) / "pkg" / "tax.py").read_text() == "RATE = 0.25\n"


async def test_reverting_takes_the_worktree_back_to_a_step_and_redo_runs_the_later_steps_again(
        live: Database, repo: Path):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/tax.py", "RATE = 0.25\n")), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    run, gate = await state(live, ref)
    first, second = edit_step(run).commit_sha, edit_step(run, 2).commit_sha
    assert first and second and first != second and run.review["commits"]["1"] == {"": first}

    async with live.session() as s:
        with pytest.raises(Refused, match="last step"):
            await RunService(s, gateway).revert(ref, len(run.steps), "Rajat")
        reverted = await RunService(s, gateway).revert(ref, 1, "Rajat")
        assert reverted.status == "cancelled" and reverted.note.startswith("Reverted to step 1 by Rajat")
    run, _ = await state(live, ref)
    tree = Path(run.worktree)
    assert agent.head(tree) == first and (tree / "pkg" / "tax.py").read_text() == "RATE = 0.2\n"
    assert (tree / "pkg" / "core.py").read_text() == ROUNDED
    assert (repo / "pkg" / "core.py").read_text() == ORIGINAL                # the checkout never moves
    assert all(x.status == "skipped" for x in run.steps if x.n > 1) and run.diff_files == 1
    async with live.read() as s:
        closed = await s.get(Approval, gate.id)
    assert closed.status == "denied"                                          # the stale signature is closed
    shown = run_json(run)
    assert shown["steps"][1]["takenBack"]["to"] == 1 and shown["reverts"][0]["steps"] == [2, 3, 4, 5]

    async with live.session() as s:
        again = await RunService(s, gateway).revert(ref, 1, "Rajat", redo=True)
        assert again.status == "queued" and all(x.status == "todo" for x in again.steps if x.n > 1)
    gateway.script = [wrote(("pkg/tax.py", "RATE = 0.3\n")), REVIEWED]
    await execute(live, gateway, ref, resume_from=2)
    run, gate = await state(live, ref)
    assert (tree / "pkg" / "tax.py").read_text() == "RATE = 0.3\n" and gate is not None and run.status == "waiting"


async def test_a_reference_source_gets_no_worktree_is_read_only_and_a_write_there_is_refused(
        live: Database, tmp_path: Path):
    docs = make_repo(tmp_path / "design", {"tokens.md": "# Tokens\nMoney is shown with two decimals.\n"})
    async with live.session() as s:
        s.add(ProjectSource(project_id=PID, label="design", kind="local", repo=str(docs), status="active",
                            role="reference", position=0))
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        plan.affected_files = ["pkg/core.py", "design/tokens.md"]
    await one_step(live)
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED), ("design/tokens.md", "# changed\n")), REVIEWED)
    ref = await dispatch(live, gateway)
    run, _ = await state(live, ref)
    assert "sources" not in run.review and [x["label"] for x in run.review["references"]] == ["design"]
    assert Path(run.review["references"][0]["root"]).resolve() == docs.resolve()
    await execute(live, gateway, ref)

    run, _ = await state(live, ref)
    assert "--- design/tokens.md (read only: a reference source)\n# Tokens" in gateway.prompts[0][1]["content"]
    step = edit_step(run)
    assert step.status == "failed" and "design is a reference source" in step.detail
    assert (docs / "tokens.md").read_text().startswith("# Tokens")
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ORIGINAL       # nothing half-applied
    assert run_json(run)["references"] == ["design"]


def test_an_always_rule_matches_exactly_the_subject_it_was_written_for():
    import fnmatch
    for subject in ("app/[id]/page.tsx", "src/*.ts", "a?b"):
        pattern = literal_pattern(subject)
        assert fnmatch.fnmatchcase(subject, pattern)
    assert not fnmatch.fnmatchcase("app/i/page.tsx", literal_pattern("app/[id]/page.tsx"))
    assert not fnmatch.fnmatchcase("src/other.ts", literal_pattern("src/*.ts"))


# ── over HTTP ────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(session: AsyncSession) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    return made


def _client(made: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=made), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """The jobs the routes hand off, recorded instead of run: the job and its arguments."""
    jobs: list[tuple[Any, ...]] = []

    async def record(open_session: AsyncSession, background: Any, job: Any, *args: Any) -> None:
        jobs.append((job, *args))

    monkeypatch.setattr(routes_work, "hand_off", record)
    monkeypatch.setattr(routes_runs, "hand_off", record)
    return jobs


@pytest_asyncio.fixture
async def lab(session: AsyncSession, client: AsyncClient, tmp_path: Path) -> dict[str, Any]:
    """A run whose worktree holds two agent steps' commits, stopped at a gate its second step raised."""
    root = make_repo(tmp_path / "shop", {"core.py": ORIGINAL, "tax.py": "RATE = 0.2\n"})
    base = run_git(["rev-parse", "HEAD"], root).strip()
    tree, branch = tmp_path / "worktrees" / "RUN-8401", "neurocode/task-8401"
    run_git(["worktree", "add", "-q", "-b", branch, str(tree), base], root)
    (tree / "core.py").write_text(ROUNDED)
    run_git(["commit", "-qam", "one"], tree)
    one = run_git(["rev-parse", "HEAD"], tree).strip()
    (tree / "tax.py").write_text("RATE = 0.25\n")
    run_git(["commit", "-qam", "two"], tree)
    two = run_git(["rev-parse", "HEAD"], tree).strip()

    session.add(m.Project(id="gov-lab", name="Gov Lab", source_kind="local", source_repo=str(root)))
    await session.flush()
    session.add(m.Run(
        id="r-RUN-8401", ref="RUN-8401", project_id="gov-lab", status="waiting", role="solo", branch=branch,
        worktree=str(tree), repo=str(root), base=base, requirement="Round", requested_by="Rajat",
        diff_files=2, diff_commits=2, waiting_on="APPR-8401",
        review={"findings": [], "verdict": "", "by": "", "commits": {"1": {"": one}, "2": {"": two}},
                "asks": {"3": {"tool": "command", "subjects": ["make test"], "rules": [1]}}},
        steps=[m.RunStep(n=1, kind="edit", label="Round the total", status="done", commit_sha=one),
               m.RunStep(n=2, kind="edit", label="Round the tax", status="done", commit_sha=two),
               m.RunStep(n=3, kind="test", label="Run the project's tests · make test", status="waiting"),
               m.RunStep(n=4, kind="review", label="Review the diff"),
               m.RunStep(n=5, kind="handoff", label="Your approval")],
        conflicts=[]))
    await session.flush()
    # `run_id` as well as `run_ref`: a gate is found by its link now, the way every gate the runtime
    # writes carries one, so a gate built by hand without it is one the inbox cannot see.
    session.add(m.Approval(id="ap-run-8401-3-8401", ref="APPR-8401", title="Run `make test` — a tool rule asks first",
                           tool="Command(make test)", risk="MEDIUM", status="pending", project_id="gov-lab",
                           run_id="r-RUN-8401", run_ref="RUN-8401", step=3, payload="command make test"))
    await session.flush()
    return {"root": root, "tree": tree, "one": one, "two": two}


async def test_the_inbox_says_what_each_gate_is_and_which_answers_it_takes(client: AsyncClient,
                                                                          lab: dict[str, Any]):
    pending = (await client.get("/approvals", params={"status": "pending"})).json()
    gate = next(a for a in pending if a["ref"] == "APPR-8401")
    assert gate["kind"] == "command" and gate["options"] == ["once", "run", "project", "deny"]
    assert gate["runRef"] == "RUN-8401" and gate["step"] == 3


async def test_allowing_a_command_for_the_run_grants_it_and_resumes_the_run(
        client: AsyncClient, lab: dict[str, Any], session: AsyncSession, started: list[tuple[Any, ...]]):
    refused = await client.post("/approvals/APPR-8401/approve", json={"answer": "yes"})
    assert refused.status_code == 422 and "not a question" in refused.json()["detail"]
    refused = await client.post("/approvals/APPR-8401/deny", json={"scope": "run"})
    assert refused.status_code == 422 and "refusal has no scope" in refused.json()["detail"]

    decided = await client.post("/approvals/APPR-8401/approve", json={"scope": "run"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved" and decided.json()["kind"] == "command"
    run = await session.get(m.Run, "r-RUN-8401")
    assert [(g["tool"], g["subject"], g["scope"], g["by"]) for g in run.grants] == [("command", "make test", "run", "Rajat")]
    [(job, _db, _gw, ref, step, approved)] = started
    assert job is runtime.resume and (ref, step, approved) == ("RUN-8401", 3, True)
    line = next(e for e in (await client.get("/activity")).json() if e["action"] == "Approved")
    assert "allowed for this run: make test" in line["detail"]
    assert (await client.post("/approvals/APPR-8401/approve")).status_code == 409   # a decision is final


async def test_always_allow_writes_an_audited_project_rule_and_needs_rules_manage(
        api: FastAPI, client: AsyncClient, lab: dict[str, Any], session: AsyncSession,
        started: list[tuple[Any, ...]]):
    await client.post("/admin/users", json={**APPROVER, "roles": ["approver"]})
    async with _client(api) as approver:
        await approver.post("/auth/login", json={"email": APPROVER["email"], "password": APPROVER["password"]})
        denied = await approver.post("/approvals/APPR-8401/approve", json={"scope": "project"})
        assert denied.status_code == 403
        assert (await session.get(m.Approval, "ap-run-8401-3-8401")).status == "pending"   # still asking
        assert (await approver.post("/approvals/APPR-8401/approve", json={"scope": "once"})).status_code == 200
    assert started and (await session.get(m.Run, "r-RUN-8401")).grants[0]["scope"] == "once"

    # The Owner's "always" on a second gate of the same kind.
    run = await session.get(m.Run, "r-RUN-8401")
    run.review = {**run.review, "asks": {"3": {"tool": "command", "subjects": ["make test"], "rules": []}}}
    session.add(m.Approval(id="ap-run-8401-3-8402", ref="APPR-8402", title="Run `make test` — a tool rule asks first",
                           tool="Command(make test)", risk="MEDIUM", status="pending", project_id="gov-lab",
                           run_id="r-RUN-8401", run_ref="RUN-8401", step=3))
    await session.flush()
    assert (await client.post("/approvals/APPR-8402/approve", json={"scope": "project"})).status_code == 200
    made = (await session.execute(select(ToolRule).where(ToolRule.project_id == "gov-lab"))).scalar_one()
    assert (made.tool, made.pattern, made.action) == ("command", "make test", "allow")
    audit = (await session.execute(select(m.AuditEntry).where(m.AuditEntry.action == "tool_rule.create"))).scalars()
    assert [a.target for a in audit] == [f"tool rule #{made.id}"]


async def test_answering_an_agent_s_question_over_http_keeps_the_answer_and_remembers_it(
        client: AsyncClient, lab: dict[str, Any], session: AsyncSession, started: list[tuple[Any, ...]]):
    step = next(x for x in (await session.get(m.Run, "r-RUN-8401")).steps if x.n == 3)
    step.question = "Which rounding mode?"
    session.add(m.Approval(id="ap-run-8401-3-8403", ref="APPR-8403", title="Backend Engineer asks: Which rounding mode?",
                           tool="Ask(Backend Engineer)", risk="LOW", status="pending", project_id="gov-lab",
                           run_id="r-RUN-8401", run_ref="RUN-8401", step=3, payload="Which rounding mode?"))
    await session.flush()
    empty = await client.post("/approvals/APPR-8403/approve", json={"answer": "   "})
    assert empty.status_code == 422
    decided = await client.post("/approvals/APPR-8403/approve", json={"answer": "Half to even."})
    assert decided.status_code == 200 and decided.json()["kind"] == "question"
    assert step.answer == "Half to even."
    fact = (await session.execute(select(MemoryFact).where(MemoryFact.project_id == "gov-lab"))).scalar_one()
    assert (fact.category, fact.title, fact.body) == ("decisions", "Which rounding mode?", "Half to even.")
    assert fact.evidence == ["RUN-8401 · step 3 · APPR-8403"]
    assert started[-1][0] is runtime.resume


async def test_reverting_over_http_resets_the_worktree_audits_it_and_redo_hands_the_rest_off(
        api: FastAPI, client: AsyncClient, lab: dict[str, Any], session: AsyncSession,
        started: list[tuple[Any, ...]]):
    run = await session.get(m.Run, "r-RUN-8401")
    run.status = "running"
    await session.flush()
    busy = await client.post("/runs/RUN-8401/steps/1/revert", json={})
    assert busy.status_code == 409 and "still working" in busy.json()["detail"]
    run.status = "waiting"
    await session.flush()
    assert (await client.post("/runs/RUN-8401/steps/9/revert")).status_code == 404

    done = await client.post("/runs/RUN-8401/steps/1/revert")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "cancelled" and body["reverts"][0]["to"] == 1
    assert [s["status"] for s in body["steps"]] == ["done", "skipped", "skipped", "skipped", "skipped"]
    assert body["steps"][1]["takenBack"]["by"] == "Rajat"
    assert agent.head(lab["tree"]) == lab["one"] and (lab["tree"] / "tax.py").read_text() == "RATE = 0.2\n"
    assert (lab["root"] / "core.py").read_text() == ORIGINAL
    assert (await session.get(m.Approval, "ap-run-8401-3-8401")).status == "denied"
    audit = (await session.execute(select(m.AuditEntry).where(m.AuditEntry.action == "run.revert"))).scalars().all()
    assert [a.target for a in audit] == ["RUN-8401 → step 1"] and audit[0].detail["steps"] == [2, 3, 4, 5]
    assert not started

    redo = await client.post("/runs/RUN-8401/steps/1/revert", json={"redo": True})
    assert redo.status_code == 200 and redo.json()["status"] == "queued"
    [(job, _db, _gw, ref, resume_from)] = started
    assert job is runtime.execute and (ref, resume_from) == ("RUN-8401", 2)

    async with _client(api) as stranger:
        assert (await stranger.post("/runs/RUN-8401/steps/1/revert")).status_code == 401
