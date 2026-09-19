"""Goal runs: "run until done", judged by a completion check that did not write the code.

Against a real git repository, with only the model scripted. The gateway here hands out lanes the way
the real router does for this purpose — the writer gets one, the check asks for the others — so the
test can see that the judge was never the lane that wrote the change.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Provider, Result
from app.api import deps, routes_plans
from app.api.app import create_api
from app.data.engine import Database
from app.models import Approval, Plan, PlanStep, Project, Run
from app.repositories import ApprovalRepository, ProjectRepository
from app.schemas import run_json
from app.services.runs import GOAL_CHECK, RunService, execute
from tests.fixtures.workspace import load_workspace

PROJECT, PLAN = "goal-project", "PLAN-9201"
CRITERIA = ["total() rounds to two decimal places", "a test covers the rounding"]
ROUNDED = "def total(x):\n    return round(x, 2)\n"
TESTED = "from pkg.core import total\n\n\ndef test_rounds():\n    assert total(1.005) == 1.0\n"


@dataclass
class FakeLane:
    id: str


class FakeGateway:
    """Writes with `groq`; any other lane it is told of may judge. Answers from a script, and keeps
    which lane each call was sent to."""

    def __init__(self, *script: str, lanes: tuple[str, ...] = ("groq", "mistral")) -> None:
        self.script = list(script)
        self.lanes = lanes
        self.asked: list[tuple[str, str | None, str]] = []

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def chain(self, role: str | None = None, lane: str | None = None, avoid: str | None = None,
              limit: int = 3) -> list[FakeLane]:
        return [FakeLane(x) for x in self.lanes][:limit]

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Result[Any]:
        lane = kw.get("lane") or "groq"
        self.asked.append((kw.get("feature", ""), kw.get("lane"), messages[-1]["content"]))
        raw = self.script.pop(0) if self.script else json.dumps({"findings": [], "verdict": "Fine."})
        return Result(parse(raw), Provider(lane, f"{lane}-model"), 20)


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


def edit(*files: tuple[str, str]) -> str:
    return json.dumps({"summary": "Changed.", "files": [{"path": p, "content": c} for p, c in files]})


REVIEWED = json.dumps({"findings": [], "verdict": "Fine."})


def judged(*verdicts: tuple[bool, str, str]) -> str:
    return json.dumps({"criteria": [{"criterion": c, "met": met, "evidence": ev, "file": f}
                                    for c, (met, ev, f) in zip(CRITERIA, verdicts, strict=False)],
                       "verdict": "Judged."})


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> Path:
    root = tmp_path / "goals"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def total(x):\n    return x\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    return root


@pytest_asyncio.fixture
async def live(schema: str, repo: Path) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Goals", source_kind="local", source_repo=str(repo)))
        await s.flush()
        s.add(Plan(id="p-goal", ref=PLAN, project_id=PROJECT, status="dispatched",
                   raw_requirement="Round the invoice total to two places", affected_files=["pkg/core.py"],
                   acceptance_criteria=CRITERIA))
        await s.flush()
        s.add(PlanStep(id="p-goal-1", plan_id="p-goal", n=1, label="Round in pkg/core.py", agent="Backend Engineer"))
    yield db
    async with db.session() as s:
        for run in (await s.execute(select(Run).where(Run.project_id == PROJECT))).scalars().unique():
            shutil.rmtree(Path(run.worktree), ignore_errors=True)
        await s.execute(delete(Approval).where(Approval.project_id == PROJECT))
        await s.execute(delete(Run).where(Run.project_id == PROJECT))
        await s.execute(delete(Plan).where(Plan.project_id == PROJECT))
        await s.execute(delete(m.ActivityEvent).where(m.ActivityEvent.project_id == PROJECT))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


async def start(db: Database, gateway: FakeGateway, budget: int) -> str:
    async with db.session() as s:
        project = await ProjectRepository(s).get(PROJECT)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        ref = (await RunService(s, gateway).plan_runs(plan, None, project, "Rajat", goal_budget=budget))[-1].ref
    await execute(db, gateway, ref)
    return ref


async def every_run(db: Database) -> list[Run]:
    async with db.read() as s:
        return list((await s.execute(select(Run).where(Run.project_id == PROJECT)
                                     .order_by(Run.created_at, Run.ref))).scalars().unique())


async def test_a_goal_run_that_misses_tries_again_on_its_own_and_stops_at_your_signature_once_met(live: Database):
    gateway = FakeGateway(
        edit(("pkg/core.py", "def total(x):\n    return round(x)\n")), REVIEWED,
        judged((False, "round(x) drops the cents", "pkg/core.py"), (False, "no test file in the diff", "")),
        edit(("pkg/core.py", ROUNDED), ("tests/test_core.py", TESTED)), REVIEWED,
        judged((True, "round(x, 2) in total()", "pkg/core.py"), (True, "test_rounds covers it", "tests/test_core.py")))
    first_ref = await start(live, gateway, budget=2)

    first, second = await every_run(live)
    assert first.ref == first_ref and [x.kind for x in first.steps] == ["edit", "review", "review", "handoff"]
    assert (first.attempt, first.goal_budget, second.attempt, second.goal_budget) == (1, 2, 2, 2)

    goal = first.review["goal"]
    assert goal["verdict"] == "not met" and goal["next"] == "rework" and goal["by"] == "mistral-model"
    assert [c["met"] for c in goal["criteria"]] == [False, False]
    assert first.status == "cancelled" and first.review["reworkedAs"] == second.ref and first.removed is True
    assert next(x for x in first.steps if x.kind == "handoff").status == "skipped"
    # The next attempt is told exactly what the check found.
    assert "Criterion 1 is not met" in second.requirement and "round(x) drops the cents" in second.requirement

    assert second.review["goal"]["verdict"] == "met" and second.review["goal"]["next"] == "sign"
    assert second.status == "waiting"
    async with live.read() as s:
        gate = await ApprovalRepository(s).waiting_on_person(second.ref)
        sent_back = (await s.execute(select(m.ActivityEvent).where(
            m.ActivityEvent.project_id == PROJECT, m.ActivityEvent.action == "Sent back for changes"))).scalar_one()
    assert "goal: met on attempt 2 of 2" in gate.payload and gate.risk == "MEDIUM"
    assert sent_back.actor == GOAL_CHECK and sent_back.actor_kind == "agent"

    # Every judgement came from a lane that did not write the code.
    judges = [(lane, p) for f, lane, p in gateway.asked if "Acceptance criteria" in p]
    assert [lane for lane, _ in judges] == ["mistral", "mistral"]
    prompt = judges[0][1]
    assert "1. total() rounds to two decimal places" in prompt and "Diff:" in prompt

    shown = run_json(second)
    assert shown["attempt"] == 2 and shown["goalBudget"] == 2 and shown["goal"]["verdict"] == "met"


async def test_met_without_evidence_or_citing_a_file_the_diff_never_touched_is_not_met(live: Database):
    gateway = FakeGateway(edit(("pkg/core.py", ROUNDED)), REVIEWED,
                          judged((True, "", "pkg/core.py"), (True, "tests cover it", "tests/test_core.py")))
    ref = await start(live, gateway, budget=1)

    [run] = await every_run(live)
    criteria = run.review["goal"]["criteria"]
    assert [c["why"] for c in criteria] == ["said met, but cited no evidence",
                                            "cites tests/test_core.py, which this diff does not touch"]
    # One attempt allowed, so it is the person's to decide — with the miss in front of them.
    assert run.ref == ref and run.status == "waiting" and run.review["goal"]["next"] == "sign"
    async with live.read() as s:
        gate = await ApprovalRepository(s).waiting_on_person(ref)
    assert gate.risk == "HIGH" and "goal: not met on attempt 1 of 1" in gate.payload


async def test_with_only_the_writer_s_lane_open_the_goal_is_left_unjudged_not_self_approved(live: Database):
    gateway = FakeGateway(edit(("pkg/core.py", ROUNDED)), REVIEWED, lanes=("groq",))
    await start(live, gateway, budget=3)

    [run] = await every_run(live)
    goal = run.review["goal"]
    assert goal["verdict"] == "unjudged" and "no lane other than the one that wrote it" in goal["why"].lower()
    assert [f for f, _, _ in gateway.asked] == ["agent", "review"]        # nobody was asked to judge
    assert run.status == "waiting" and len(await every_run(live)) == 1     # nothing to rework on its own


# ── dispatching "until done" ─────────────────────────────────────
OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}


@pytest_asyncio.fixture
async def client(session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    made: FastAPI = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    async def record(open_session: AsyncSession, jobs: Any, job: Any, *args: Any) -> None:
        await open_session.flush()

    made.dependency_overrides[deps.session] = use_the_test_session
    monkeypatch.setattr(routes_plans, "hand_off", record)
    root = tmp_path / "dispatched"
    root.mkdir()
    (root / "core.py").write_text("x = 1\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    session.add(m.Project(id="goal-http", name="Goal HTTP", source_kind="local", source_repo=str(root)))
    await session.flush()
    for ref, criteria in (("PLAN-9301", []), ("PLAN-9302", CRITERIA)):
        session.add(m.Plan(id=f"p-{ref}", ref=ref, project_id="goal-http", status="draft",
                           raw_requirement="Round it", acceptance_criteria=criteria,
                           steps=[m.PlanStep(id=f"p-{ref}-1", n=1, label="Round in core.py",
                                             agent="Backend Engineer")]))
    await session.flush()
    async with AsyncClient(transport=ASGITransport(app=made), base_url="http://api",
                           headers={"X-NC-Client": "test"}) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def test_run_until_done_needs_criteria_and_a_budget_of_one_to_five(client: AsyncClient, session: AsyncSession):
    refused = await client.post("/plans/PLAN-9301/dispatch", json={"goalBudget": 3})
    assert refused.status_code == 422 and "no acceptance criteria" in refused.json()["detail"]
    assert (await session.get(m.Plan, "p-PLAN-9301")).status == "draft"          # nothing moved
    for bad in (0, 6):
        assert (await client.post("/plans/PLAN-9302/dispatch", json={"goalBudget": bad})).status_code == 422

    sent = await client.post("/plans/PLAN-9302/dispatch", json={"goalBudget": 3})
    assert sent.status_code == 200, sent.text
    run = (await client.get(f"/runs/{sent.json()['runRef']}")).json()
    assert (run["attempt"], run["goalBudget"]) == (1, 3)
    assert run["goal"]["verdict"] == "not run" and run["goal"]["step"] == 3
    assert [s["label"] for s in run["steps"]][-2:] == ["Check the goal against the plan's acceptance criteria",
                                                        "Your approval"]
    line = next(e for e in (await client.get("/activity")).json() if e["action"] == "Run started")
    assert "until done, up to 3 attempts" in line["detail"]

    # Without a body it is an ordinary run, exactly as before.
    plain = await client.post("/plans/PLAN-9301/dispatch")
    assert plain.status_code == 200
    ordinary = (await client.get(f"/runs/{plain.json()['runRef']}")).json()
    assert ordinary["goalBudget"] is None and ordinary["goal"] is None
