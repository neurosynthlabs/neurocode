"""Carrying an interrupted run on, against real git repositories with nothing mocked but the model.

A run that was working when the process died used to have exactly one way forward: send it back, which
throws the worktree away and pays a model again for every step that had already finished. The commits
those steps made were there the whole time — each edit step writes its per-source sha into
`review.commits` in the same transaction that marks the step done — so what was missing was not the
record but the reading of it.

These tests interrupt a run the way the process dying interrupts one (an exception that is not an
`Exception`, so nothing in the runtime catches it), reconcile it exactly as start-up does, and then
assert what carrying it on does to the branch, to the worktree and to the model bill.
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
from app.api.app import reconcile_interrupted
from app.data.engine import Database
from app.models import Approval, Chunk, Plan, PlanStep, Project, ProjectSource, Run, Setting, ToolRule
from app.repositories import ApprovalRepository, RunRepository
from app.services import runs as runtime
from app.services.errors import Refused
from app.services.gates import ApprovalService
from app.services.runs import RunService, execute

PID, PLAN = "resume-project", "PLAN-9501"
ORIGINAL = "def total(x):\n    return x\n"
ROUNDED = "def total(x):\n    return round(x, 2)\n"
TAXED = "RATE = 0.25\n"


class ProcessDied(BaseException):
    """What the process being killed looks like from inside a step: nothing catches it, because
    `execute` catches `Exception` and this is deliberately not one."""


class FakeGateway:
    """Answers with a script and keeps every prompt it was sent. No provider is ever called.

    A script entry that is an exception is raised instead of answered, which is how a step is made to
    fail — or, with `ProcessDied`, how the process is made to die in the middle of one.
    """

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
        from app.ai.gateway import Provider, Result
        self.asked.append(kw.get("feature", ""))
        self.prompts.append(messages)
        raw = self.script.pop(0) if self.script else '{"summary": "nothing", "files": []}'
        if isinstance(raw, BaseException):
            raise raw
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 20)

    def asked_for_step(self, n: int) -> int:
        """How many times a model was asked to write step `n` — the bill, as the ledger would show it."""
        return sum(1 for p in self.prompts if f"Step {n}:" in p[-1]["content"])


def wrote(*files: tuple[str, str], summary: str = "Rounded.") -> str:
    return json.dumps({"summary": summary, "files": [{"path": p, "content": c} for p, c in files]})


REVIEWED = json.dumps({"findings": [], "verdict": "Reads fine."})


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    root.mkdir(parents=True)
    for rel, body in {"pkg/core.py": ORIGINAL, "pkg/tax.py": "RATE = 0.2\n",
                      "Makefile": f"test:\n\t{sys.executable} -c \"print('1 passed')\"\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    return root


@pytest_asyncio.fixture
async def live(schema: str, repo: Path) -> AsyncIterator[Database]:
    db = Database(url=schema)
    await _forget(db)
    async with db.session() as s:
        s.add(Project(id=PID, name="Resumable", source_kind="local", source_repo=str(repo)))
        await s.flush()
        s.add(Plan(id="p-res", ref=PLAN, project_id=PID, status="draft",
                   raw_requirement="Round the invoice total to two places",
                   affected_files=["pkg/core.py", "pkg/tax.py"]))
        await s.flush()
        s.add(PlanStep(id="p-res-1", plan_id="p-res", n=1, label="Fix rounding in pkg/core.py",
                       agent="Backend Engineer"))
        s.add(PlanStep(id="p-res-2", plan_id="p-res", n=2, label="Raise the tax rate",
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


async def dispatch(db: Database, gateway: FakeGateway) -> str:
    async with db.session() as s:
        from app.repositories import ProjectRepository
        project = await ProjectRepository(s).get(PID)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        made = await RunService(s, gateway).plan_runs(plan, None, project, "Rajat")
        return made[-1].ref


async def state(db: Database, ref: str) -> tuple[Run, Approval | None]:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        gate = await ApprovalRepository(s).waiting_on_person(ref)
        return run, gate


async def restart(db: Database) -> None:
    """What start-up does to the rows that claim to be in flight but cannot be."""
    async with db.session() as s:
        await reconcile_interrupted(s)


async def interrupt(db: Database, gateway: FakeGateway, ref: str) -> None:
    """Run it until the scripted death, then reconcile it as a restart would."""
    with pytest.raises(ProcessDied):
        await execute(db, gateway, ref)
    await restart(db)


async def plan_for(db: Database, ref: str) -> dict[str, Any]:
    async with db.read() as s:
        return await RunService(s, FakeGateway()).resume_plan(ref)


async def carry_on(db: Database, gateway: FakeGateway, ref: str, by: str = "Rajat") -> None:
    async with db.session() as s:
        made = await RunService(s, gateway).carry_on(ref, by)
        resume_from = made.review["resumes"][-1]["from"]
    await execute(db, gateway, ref, resume_from)


async def logs_of(db: Database, run_id: str) -> list[str]:
    async with db.read() as s:
        return [x.line for x in await runtime.RunLogRepository(s).after(run_id)]


# ── the clean boundary ───────────────────────────────────────────
async def test_an_interrupted_run_carries_on_and_the_finished_steps_are_not_paid_for_again(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)

    run, _ = await state(live, ref)
    assert run.status == "failed" and run.steps[0].status == "done" and run.steps[1].status == "failed"
    first_sha = run.review["commits"]["1"][""]
    assert first_sha and run.steps[0].commit_sha == first_sha

    plan = await plan_for(live, ref)
    assert plan["canResume"] is True and plan["from"] == 2 and plan["lastGood"] == 1
    assert plan["worktree"] == "present" and plan["parts"][0]["checkpoint"] == first_sha
    assert plan["parts"][0]["dirty"] is False and plan["steps"] == {"done": 1, "left": 4}
    assert "Step 2 (Raise the tax rate) runs again" in plan["said"] and "Steps 1–1 stand" in plan["said"]

    gateway.script = [wrote(("pkg/tax.py", TAXED)), REVIEWED]
    await carry_on(live, gateway, ref)

    run, gate = await state(live, ref)
    assert [x.status for x in run.steps] == ["done", "done", "done", "done", "waiting"]
    # Step 1's commit is still the one it made, and no model was asked to write step 1 a second time.
    assert run.review["commits"]["1"][""] == first_sha
    assert gateway.asked_for_step(1) == 1 and gateway.asked_for_step(2) == 2
    assert (Path(run.worktree) / "pkg" / "core.py").read_text() == ROUNDED
    assert (Path(run.worktree) / "pkg" / "tax.py").read_text() == TAXED
    assert gate is not None and gate.tool.startswith("Merge(")
    assert any("carrying on from step 2 by Rajat" in x for x in await logs_of(live, run.id))
    assert run.review["resumes"][-1]["from"] == 2 and run.review["resumes"][-1]["by"] == "Rajat"


async def test_a_run_interrupted_before_any_step_finished_starts_from_the_first_step(live: Database):
    gateway = FakeGateway(ProcessDied("killed at once"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)

    plan = await plan_for(live, ref)
    assert plan["from"] == 1 and plan["lastGood"] == 0
    assert "No step had finished, so it starts from the beginning." in plan["said"]
    run, _ = await state(live, ref)
    assert plan["parts"][0]["checkpoint"] == run.base


# ── a step killed between writing and committing ─────────────────
async def test_what_a_half_written_step_left_is_kept_and_never_lands_on_the_branch(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)
    run, _ = await state(live, ref)
    # Exactly the state a kill between `_apply` and `_commit_parts` leaves: files written, nothing committed.
    (Path(run.worktree) / "pkg" / "tax.py").write_text("RATE = 0.99  # half written\n")

    plan = await plan_for(live, ref)
    assert plan["canResume"] is True and plan["parts"][0]["dirty"] is True
    assert f"refs/neurocode/partial/{ref.lower()}/2" in plan["said"]

    gateway.script = [wrote(("pkg/tax.py", TAXED)), REVIEWED]
    await carry_on(live, gateway, ref)

    run, _ = await state(live, ref)
    tree = Path(run.worktree)
    kept = runtime._partial_ref(ref, 2)
    # The loose file is on a ref of the run's own, readable with git, and on no branch.
    shown = run_git(["show", f"{kept}:pkg/tax.py"], tree)
    assert shown == "RATE = 0.99  # half written\n"
    reachable = run_git(["log", "--format=%H", f"{run.base}..HEAD"], tree).split()
    assert run_git(["rev-parse", kept], tree).strip() not in reachable
    # And the branch holds what step 2 actually wrote when it ran again, not the half-written text.
    assert (tree / "pkg" / "tax.py").read_text() == TAXED
    assert any(kept in x and "never committed" in x for x in await logs_of(live, run.id))
    assert run.review["resumes"][-1]["kept"][""]


# ── a step that committed and was never written down ─────────────
async def test_a_commit_the_run_made_before_it_died_is_adopted_not_paid_for_again(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)
    run, _ = await state(live, ref)
    tree = Path(run.worktree)
    # Step 2 committed with the run's own trailer, and the process died before that was written down.
    (tree / "pkg" / "tax.py").write_text(TAXED)
    run_git(["add", "-A"], tree)
    run_git(["commit", "-qm", f"Raise the tax rate\n\nRaised it.\n\nNeuroCode {ref}"], tree)
    adopted = run_git(["rev-parse", "HEAD"], tree).strip()

    plan = await plan_for(live, ref)
    assert plan["canResume"] is True and plan["from"] == 3 and plan["parts"][0]["adopts"] == adopted
    assert "is not written again" in plan["said"]

    gateway.script = [REVIEWED]
    await carry_on(live, gateway, ref)

    run, _ = await state(live, ref)
    step = next(x for x in run.steps if x.n == 2)
    assert step.status == "done" and step.commit_sha == adopted
    assert run.review["commits"]["2"][""] == adopted
    assert "adopted when the run carried on" in step.detail
    assert gateway.asked_for_step(2) == 1               # asked once, before it died — never a second time
    assert (tree / "pkg" / "tax.py").read_text() == TAXED


async def test_a_worktree_that_moved_somewhere_this_run_did_not_make_is_refused(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)
    run, _ = await state(live, ref)
    tree = Path(run.worktree)
    checkpoint = run.review["commits"]["1"][""]
    (tree / "pkg" / "tax.py").write_text("RATE = 0.0\n")
    run_git(["add", "-A"], tree)
    run_git(["commit", "-qm", "I edited this branch by hand"], tree)
    moved = run_git(["rev-parse", "HEAD"], tree).strip()

    plan = await plan_for(live, ref)
    assert plan["canResume"] is False and plan["from"] is None
    assert moved[:7] in plan["reason"] and checkpoint[:7] in plan["reason"]
    assert "was not made by this run" in plan["reason"]

    with pytest.raises(Refused, match="was not made by this run"):
        async with live.session() as s:
            await RunService(s, gateway).carry_on(ref, "Rajat")
    # Nothing was touched: the branch still stands where the person left it.
    assert run_git(["rev-parse", "HEAD"], tree).strip() == moved
    run, _ = await state(live, ref)
    assert run.status == "failed" and not run.review.get("resumes")


async def test_a_worktree_that_was_deleted_is_opened_again_on_the_branch(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)
    run, _ = await state(live, ref)
    import shutil
    shutil.rmtree(run.worktree)

    plan = await plan_for(live, ref)
    assert plan["canResume"] is True and plan["worktree"] == "missing" and plan["from"] == 2

    gateway.script = [wrote(("pkg/tax.py", TAXED)), REVIEWED]
    await carry_on(live, gateway, ref)

    run, _ = await state(live, ref)
    tree = Path(run.worktree)
    assert tree.is_dir() and (tree / "pkg" / "core.py").read_text() == ROUNDED
    assert (tree / "pkg" / "tax.py").read_text() == TAXED
    assert any("worktree opened again" in x for x in await logs_of(live, run.id))


async def test_a_run_whose_branch_is_gone_says_so_rather_than_opening_anything(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)
    run, _ = await state(live, ref)
    import shutil
    shutil.rmtree(run.worktree)
    agent.git(["worktree", "prune"], Path(run.repo))
    agent.git(["branch", "-D", run.branch], Path(run.repo))

    plan = await plan_for(live, ref)
    assert plan["canResume"] is False and "branch" in plan["reason"] and "is gone" in plan["reason"]


# ── what resume refuses outright ─────────────────────────────────
async def test_a_run_waiting_on_a_person_is_not_carried_on_behind_their_back(live: Database):
    async with live.session() as s:
        s.add(ToolRule(project_id=PID, tool="edit", pattern="pkg/*", action="ask", note=""))
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    run, gate = await state(live, ref)
    assert run.status == "waiting" and gate is not None

    plan = await plan_for(live, ref)
    assert plan["canResume"] is False
    assert plan["reason"] == f"{ref} is waiting for your decision; answering it is how it carries on."


async def test_a_run_that_finished_has_nothing_to_carry_on(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/tax.py", TAXED)), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    _, gate = await state(live, ref)
    async with live.session() as s:
        await ApprovalService(s).decide(gate.ref, "approve", by_id=None, by_name="Rajat")
    await runtime.resume(live, gateway, ref, gate.step, approved=True)

    plan = await plan_for(live, ref)
    assert plan["canResume"] is False and "finished" in plan["reason"]


async def test_a_run_whose_every_step_ran_is_told_so_rather_than_offered_a_button(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), wrote(("pkg/tax.py", TAXED)), REVIEWED)
    ref = await dispatch(live, gateway)
    await execute(live, gateway, ref)
    # Force the run out of `waiting` without answering the gate: every step ran, none is left.
    async with live.session() as s:
        run = await RunRepository(s).by_ref(ref)
        run.status, run.waiting_on = "cancelled", None
        next(x for x in run.steps if x.kind == "handoff").status = "skipped"

    plan = await plan_for(live, ref)
    assert plan["canResume"] is False and "nothing left to carry on to" in plan["reason"]


# ── grants, and the race ─────────────────────────────────────────
async def test_carrying_on_widens_nothing_a_person_allowed(live: Database):
    async with live.session() as s:
        s.add(ToolRule(project_id=PID, tool="edit", pattern="pkg/tax.py", action="ask", note=""))
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)

    gateway.script = [wrote(("pkg/tax.py", TAXED))]
    await carry_on(live, gateway, ref)
    run, gate = await state(live, ref)
    # No grant survived a restart into existence: the rule asks again, exactly as it would have.
    assert run.status == "waiting" and gate is not None and gate.tool == "Edit(1 file)"
    assert run.grants == []

    async with live.session() as s:
        await ApprovalService(s).decide(gate.ref, "approve", by_id=None, by_name="Rajat")
    await runtime.resume(live, gateway, ref, gate.step, approved=True)
    run, _ = await state(live, ref)
    assert run.grants[0]["scope"] == "once" and run.grants[0]["used"] is True
    assert (Path(run.worktree) / "pkg" / "tax.py").read_text() == TAXED


async def test_two_people_cannot_carry_the_same_run_on_at_once(live: Database):
    gateway = FakeGateway(wrote(("pkg/core.py", ROUNDED)), ProcessDied("killed"))
    ref = await dispatch(live, gateway)
    await interrupt(live, gateway, ref)

    async with live.session() as s:
        await RunService(s, gateway).carry_on(ref, "Rajat")
    # The first one left the run queued; the second is told so rather than resetting the worktree again.
    with pytest.raises(Refused, match="already carrying on"):
        async with live.session() as s:
            await RunService(s, gateway).carry_on(ref, "Asha")
    run, _ = await state(live, ref)
    assert len(run.review["resumes"]) == 1


# ── over HTTP: the permission, the answer's shape and the audit line ──
@pytest.fixture
def api(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from app.api import deps
    from app.api.app import create_api

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made = create_api(db=None)
    made.dependency_overrides[deps.session] = use_the_test_session
    return made


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api",
                           headers={"X-NC-Client": "test"}) as c:
        await c.post("/auth/setup", json={"workspace": "Acme", "name": "Rajat",
                                          "email": "owner@example.com", "password": "correct horse battery"})
        yield c


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """The jobs the routes hand off, recorded instead of run."""
    from app.api import routes_runs
    jobs: list[tuple[Any, ...]] = []

    async def record(open_session: AsyncSession, background: Any, job: Any, *args: Any) -> None:
        jobs.append((job, *args))

    monkeypatch.setattr(routes_runs, "hand_off", record)
    return jobs


@pytest_asyncio.fixture
async def stranded(session: AsyncSession, client: AsyncClient, tmp_path: Path) -> dict[str, Any]:
    """A run whose first step committed and whose second never finished, exactly as a restart leaves one."""
    root = tmp_path / "shop"
    root.mkdir()
    (root / "core.py").write_text(ORIGINAL)
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    base = run_git(["rev-parse", "HEAD"], root).strip()
    tree, branch = tmp_path / "worktrees" / "RUN-8501", "neurocode/task-8501"
    run_git(["worktree", "add", "-q", "-b", branch, str(tree), base], root)
    (tree / "core.py").write_text(ROUNDED)
    run_git(["commit", "-qam", "one"], tree)
    one = run_git(["rev-parse", "HEAD"], tree).strip()

    session.add(m.Project(id="res-lab", name="Res Lab", source_kind="local", source_repo=str(root)))
    await session.flush()
    session.add(m.Run(
        id="r-RUN-8501", ref="RUN-8501", project_id="res-lab", status="failed", role="solo", branch=branch,
        worktree=str(tree), repo=str(root), base=base, requirement="Round", requested_by="Rajat",
        note="interrupted: the server restarted", diff_files=1, diff_commits=1,
        review={"findings": [], "verdict": "", "by": "", "commits": {"1": {"": one}}},
        steps=[m.RunStep(n=1, kind="edit", label="Round the total", status="done", commit_sha=one),
               m.RunStep(n=2, kind="edit", label="Round the tax", status="failed",
                         detail="interrupted: the server restarted"),
               m.RunStep(n=3, kind="review", label="Review the diff"),
               m.RunStep(n=4, kind="handoff", label="Your approval")],
        conflicts=[]))
    await session.flush()
    return {"root": root, "tree": tree, "one": one}


async def test_the_screen_is_told_what_carrying_on_would_do_without_anything_moving(
        client: AsyncClient, stranded: dict[str, Any]):
    answer = await client.get("/runs/RUN-8501/resume")
    assert answer.status_code == 200
    plan = answer.json()
    assert plan["canResume"] is True and plan["from"] == 2 and plan["lastGood"] == 1
    assert plan["parts"] == [{"label": "", "head": stranded["one"], "checkpoint": stranded["one"],
                              "dirty": False, "adopts": None, "foreign": None}]
    assert plan["grants"] == {"run": 0, "once": 0} and plan["steps"] == {"done": 1, "left": 3}
    assert "Step 2 (Round the tax) runs again" in plan["said"]
    # Read-only: the branch is where it was, and the run still says it failed.
    assert run_git(["rev-parse", "HEAD"], stranded["tree"]).strip() == stranded["one"]
    assert (await client.get("/runs/RUN-8501")).json()["status"] == "failed"


async def test_carrying_on_needs_the_permission_to_run_agents(api: FastAPI, client: AsyncClient,
                                                              stranded: dict[str, Any]):
    await client.post("/admin/users", json={"email": "reader@example.com", "name": "Asha",
                                            "password": "another long passphrase", "roles": ["viewer"]})
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api",
                           headers={"X-NC-Client": "test"}) as viewer:
        await viewer.post("/auth/login", json={"email": "reader@example.com",
                                               "password": "another long passphrase"})
        # Reading what would happen is not doing it, so a viewer may ask.
        assert (await viewer.get("/runs/RUN-8501/resume")).json()["canResume"] is True
        assert (await viewer.post("/runs/RUN-8501/resume")).status_code == 403


async def test_carrying_on_hands_the_run_to_the_runtime_and_is_audited(
        client: AsyncClient, session: AsyncSession, stranded: dict[str, Any], started: list[tuple[Any, ...]]):
    answer = await client.post("/runs/RUN-8501/resume")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] == "queued" and body["note"] == "Carried on from step 2 by Rajat."
    assert body["resumes"][-1]["from"] == 2 and body["resumes"][-1]["by"] == "Rajat"
    assert [s["status"] for s in body["steps"]] == ["done", "todo", "todo", "todo"]

    [(job, _db, _gw, ref, resume_from)] = started
    assert job is runtime.execute and (ref, resume_from) == ("RUN-8501", 2)
    entry = next(a for a in (await session.execute(select(m.AuditEntry))).scalars() if a.action == "run.resume")
    assert entry.target == "RUN-8501 → step 2" and entry.detail["from"] == 2


async def test_a_run_already_carrying_on_is_told_so_rather_than_started_twice(
        client: AsyncClient, stranded: dict[str, Any], started: list[tuple[Any, ...]]):
    assert (await client.post("/runs/RUN-8501/resume")).status_code == 200
    again = await client.post("/runs/RUN-8501/resume")
    assert again.status_code == 409 and again.json()["detail"] == "RUN-8501 is already carrying on."
    assert len(started) == 1
