"""Workflows over HTTP: written once, run as ordinary plans, and counted from the runs they became.

The runtime is real up to the moment a run would start: a throwaway git repository, a plan with the
workflow's steps copied in, runs made by the runtime's own `plan_runs`. The background job itself is
recorded rather than started — what it does is `test_runs.py`'s business — and the model is a stand-in
that never reaches the network.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Provider, Result
from app.api import deps
from app.api.app import create_api
from app.services import runs as runtime
from tests.fixtures.lanes import LANE_MODEL, PLAN

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ENGINEER = {"email": "dev@example.com", "name": "Dev", "password": "another long passphrase",
            "roles": ["engineer"]}            # may compile and run agents, but may not steer plans or write workflows
HEADERS = {"X-NC-Client": "test"}
CODE = "shop"
TAX = "Invoice tax is wrong: CGST and SGST come out reversed on interstate orders. Fix the TRANS_INVOICE table."

TWO_AGENTS = {
    "name": "api-and-screen", "description": "An endpoint and the screen that reads it.",
    "requirementTemplate": "Build {input} end to end.",
    "steps": [{"label": "Add the endpoint", "agent": "Backend Engineer", "detail": "FastAPI route"},
              {"label": "Add the screen", "agent": "Frontend Engineer"}],
}


class FakeGateway:
    """Lanes without providers: two lanes, one of them ready, and a model that writes one plan."""

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def report(self) -> list[dict[str, Any]]:
        return [{"id": "groq", "ready": True}, {"id": "gemini", "ready": False}]

    def ask(self, messages: Any, parse: Any, **kw: Any) -> Result[Any]:
        return Result(parse(json.dumps(PLAN)), Provider("groq", LANE_MODEL), 0)


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository with a commit and a Makefile test target — so a run has something to branch
    from, and the runtime finds a test command it would ask before running."""
    root = tmp_path / "shop"
    root.mkdir()
    (root / "app.py").write_text("def total(x):\n    return x\n")
    (root / "Makefile").write_text("test:\n\t@echo never run by these tests\n")
    run_git(["init", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-m", "first"], root)
    return root


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The jobs a route queued, by name and run ref, instead of the jobs themselves."""
    queued: list[tuple[str, str]] = []

    async def execute(db: Any, gw: Any, ref: str) -> None:
        queued.append(("execute", ref))

    async def execute_batch(db: Any, gw: Any, ref: str) -> None:
        queued.append(("execute_batch", ref))

    monkeypatch.setattr(runtime, "execute", execute)
    monkeypatch.setattr(runtime, "execute_batch", execute_batch)
    return queued


@pytest_asyncio.fixture
async def client(seeded: AsyncSession, repo: Path) -> AsyncIterator[AsyncClient]:
    seeded.add(m.Project(id=CODE, name="Shop", source_kind="local", source_repo=str(repo)))
    await seeded.flush()
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield seeded

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = FakeGateway
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def as_engineer(client: AsyncClient) -> None:
    made = await client.post("/admin/users", json=ENGINEER)
    assert made.status_code in (200, 201), made.text
    await client.post("/auth/logout")
    signed = await client.post("/auth/login", json={"email": ENGINEER["email"], "password": ENGINEER["password"]})
    assert signed.status_code == 200, signed.text


async def create(client: AsyncClient, body: dict[str, Any] = TWO_AGENTS) -> dict[str, Any]:
    made = await client.post("/workflows", json=body)
    assert made.status_code == 201, made.text
    return made.json()


# ── reading, before anything has run ─────────────────────────────

async def test_an_empty_workspace_shows_the_builtin_and_no_invented_numbers(client: AsyncClient):
    library = (await client.get("/workflows")).json()
    assert [w["id"] for w in library] == ["requirement-to-pr"]
    builtin = library[0]
    assert builtin["runs"] == 0 and builtin["avgAgents"] is None and builtin["avgMinutes"] is None
    assert builtin["lastRun"] is None and builtin["lastResult"] is None
    # No project named, so no test command is known, and no Test phase is promised.
    assert "Test" not in [p["title"] for p in builtin["phases"]]

    overview = (await client.get("/workflows/overview")).json()
    assert overview["stats"] == {"workflows": 1, "runsTotal": 0, "liveNow": 0, "agentsInFlight": 0,
                                 "lanesOpen": 1, "tokensToday": 0, "costToday": 0}
    assert overview["live"] is None and overview["history"] == []
    assert "Backend Engineer" in overview["writers"] and "AI Commander" not in overview["writers"]


async def test_the_test_phase_appears_only_where_the_project_has_a_test_command(client: AsyncClient):
    made = await create(client)
    assert [p["title"] for p in made["phases"]] == ["Write", "Merge", "Review", "Gate"]
    assert made["phases"][0]["mode"] == "parallel" and made["phases"][0]["agents"] == 2

    in_shop = (await client.get(f"/workflows/{made['id']}", params={"project": CODE})).json()
    assert [p["title"] for p in in_shop["phases"]] == ["Write", "Merge", "Test", "Review", "Gate"]
    assert in_shop["tests"] == {"project": "Shop", "command": "make test"}
    assert "make test" in in_shop["definitionText"]

    in_sample = (await client.get(f"/workflows/{made['id']}", params={"project": "erp"})).json()
    assert "Test" not in [p["title"] for p in in_sample["phases"]]
    assert "no test command was found" in in_sample["definitionText"]


# ── writing ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("change", "words"), [
    ({"requirementTemplate": "Always the same thing."}, "{input}"),
    ({"steps": [{"label": "Plan it", "agent": "AI Commander"}]}, "never run"),
    ({"steps": [{"label": "Get approval from the lead", "agent": "Backend Engineer"}]}, "approval"),
    ({"steps": [{"label": "Write it", "agent": "Nobody In Particular"}]}, "not on the roster"),
])
async def test_a_step_the_runtime_would_silently_drop_is_refused(client: AsyncClient, change: dict[str, Any],
                                                                 words: str):
    refused = await client.post("/workflows", json={**TWO_AGENTS, **change})
    assert refused.status_code == 422 and words in refused.json()["detail"]


async def test_names_are_unique_whatever_the_case_and_editing_replaces_the_steps(client: AsyncClient):
    made = await create(client)
    again = await client.post("/workflows", json={**TWO_AGENTS, "name": "API-AND-SCREEN"})
    assert again.status_code == 409 and "exists already" in again.json()["detail"]

    one_agent = {**TWO_AGENTS, "steps": [{"label": "Add the endpoint", "agent": "Backend Engineer"},
                                         {"label": "Add its test", "agent": "Backend Engineer"}]}
    changed = await client.patch(f"/workflows/{made['id']}", json=one_agent)
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert [s["label"] for s in body["steps"]] == ["Add the endpoint", "Add its test"]
    assert body["phases"][0]["mode"] == "pipeline" and "Merge" not in [p["title"] for p in body["phases"]]


async def test_only_workflows_write_may_write_one(client: AsyncClient):
    await as_engineer(client)
    denied = await client.post("/workflows", json=TWO_AGENTS)
    assert denied.status_code == 403 and "workflows:write" in denied.json()["detail"]


# ── running ──────────────────────────────────────────────────────

async def test_running_needs_the_same_permissions_as_dispatching_a_plan(client: AsyncClient):
    made = await create(client)
    await as_engineer(client)
    denied = await client.post(f"/workflows/{made['id']}/run", json={"projectId": CODE, "input": "invoices"})
    assert denied.status_code == 403 and "plans:decide" in denied.json()["detail"]


async def test_a_project_with_no_code_is_refused_before_anything_is_written(client: AsyncClient,
                                                                             seeded: AsyncSession,
                                                                             started: list[tuple[str, str]]):
    made = await create(client)
    plans_before = (await seeded.execute(select(func.count()).select_from(m.Plan))).scalar_one()
    tasks_before = (await seeded.execute(select(func.count()).select_from(m.Task))).scalar_one()

    refused = await client.post(f"/workflows/{made['id']}/run", json={"projectId": "erp", "input": "invoices"})
    assert refused.status_code == 409 and "no code on this machine" in refused.json()["detail"]
    assert (await seeded.execute(select(func.count()).select_from(m.Plan))).scalar_one() == plans_before
    assert (await seeded.execute(select(func.count()).select_from(m.Task))).scalar_one() == tasks_before
    assert started == []


async def test_a_run_is_a_plan_with_the_workflows_steps_and_is_counted_from_its_runs(
        client: AsyncClient, seeded: AsyncSession, started: list[tuple[str, str]]):
    made = await create(client)
    ran = await client.post(f"/workflows/{made['id']}/run", json={"projectId": CODE, "input": "a tax report"})
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["runRef"] and body["agents"] == 2 and body["openQuestions"] == 0
    assert started == [("execute_batch", body["runRef"])]

    plan = (await seeded.execute(select(m.Plan).where(m.Plan.ref == body["planRef"]))).scalar_one()
    assert plan.workflow_id == made["id"] and plan.status == "dispatched"
    assert (await client.get(f"/plans/{plan.ref}")).json()["workflowId"] == made["id"]
    assert plan.raw_requirement == "Build a tax report end to end."
    assert [(s.label, s.agent) for s in plan.steps] == [("Add the endpoint", "Backend Engineer"),
                                                        ("Add the screen", "Frontend Engineer")]

    listed = next(w for w in (await client.get("/workflows")).json() if w["id"] == made["id"])
    assert listed["runs"] == 1 and listed["avgAgents"] == 2.0 and listed["lastResult"] is None

    live = (await client.get("/workflows/overview")).json()["live"]
    assert live["ref"] == body["runRef"] and live["workflow"] == "api-and-screen"
    rows = [(r["label"], r["phase"]) for r in live["agents"]]
    assert ("Backend Engineer", "write") in rows and ("Frontend Engineer", "write") in rows
    assert [phase for _, phase in rows[2:]] == ["merge", "merge", "test", "review", "handoff"]
    assert all(r["tokens"] is None for r in live["agents"])     # nothing asked of a model yet: a dash

    # The run finishes, one agent having written nothing: done, but not a clean success.
    lead = (await seeded.execute(select(m.Run).where(m.Run.ref == body["runRef"]))).scalar_one()
    children = (await seeded.execute(select(m.Run).where(m.Run.parent_id == lead.id))).scalars().all()
    for run in [lead, *children]:
        run.status, run.finished_at = "done", func.now()
    children[0].steps[0].status = "skipped"
    seeded.add(m.AiCall(feature="agent", lane="groq", model="llama", tokens_in=900, tokens_out=100,
                        run_id=children[1].id))
    await seeded.flush()

    overview = (await client.get("/workflows/overview")).json()
    assert overview["live"] is None and overview["stats"]["runsTotal"] == 1
    assert overview["stats"]["tokensToday"] == 1000
    row = overview["history"][0]
    assert row["runRef"] == body["runRef"] and row["workflow"] == "api-and-screen"
    assert row["result"] == "partial" and row["tokens"] == 1000 and row["agents"] == 2
    assert row["trigger"] == f"Rajat · {body['taskRef']}"
    listed = next(w for w in (await client.get("/workflows")).json() if w["id"] == made["id"])
    assert listed["lastResult"] == "partial"



async def test_one_flawed_run_does_not_mark_a_clean_run_partial(
        client: AsyncClient, seeded: AsyncSession, started: list[tuple[str, str]]):
    """The flaw query once joined every run to every other, so a single skipped step anywhere flagged all of
    them. Two runs of two workflows, one clean and one not, must come back as exactly that."""
    clean = await create(client, {**TWO_AGENTS, "name": "clean-one"})
    flawed = await create(client, {**TWO_AGENTS, "name": "flawed-one"})
    refs = {}
    for made in (clean, flawed):
        ran = await client.post(f"/workflows/{made['id']}/run", json={"projectId": CODE, "input": "a tax report"})
        assert ran.status_code == 200, ran.text
        refs[made["id"]] = ran.json()["runRef"]

    for made in (clean, flawed):
        lead = (await seeded.execute(select(m.Run).where(m.Run.ref == refs[made["id"]]))).scalar_one()
        children = (await seeded.execute(select(m.Run).where(m.Run.parent_id == lead.id))).scalars().all()
        for run in [lead, *children]:
            run.status, run.finished_at = "done", func.now()
            for step in run.steps:
                step.status = "done"
        if made is flawed:
            children[0].steps[0].status = "skipped"
    await seeded.flush()

    history = {r["runRef"]: r["result"] for r in (await client.get("/workflows/overview")).json()["history"]}
    assert history[refs[clean["id"]]] == "success" and history[refs[flawed["id"]]] == "partial"
    library = {w["id"]: w["lastResult"] for w in (await client.get("/workflows")).json()}
    assert library[clean["id"]] == "success" and library[flawed["id"]] == "partial"

async def test_the_builtin_stops_at_open_questions_instead_of_guessing(client: AsyncClient,
                                                                        started: list[tuple[str, str]]):
    ran = await client.post("/workflows/requirement-to-pr/run", json={"projectId": CODE, "input": TAX})
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["runRef"] is None and body["openQuestions"] > 0 and "Plans" in body["note"]
    assert started == []
    plan = (await client.get(f"/plans/{body['planRef']}")).json()
    assert plan["status"] == "draft"


async def test_a_workflow_that_ran_is_archived_and_one_that_never_did_is_deleted(
        client: AsyncClient, started: list[tuple[str, str]]):
    never = await create(client, {**TWO_AGENTS, "name": "never-ran"})
    gone = await client.delete(f"/workflows/{never['id']}")
    assert gone.json() == {"ok": True, "archived": False}
    assert (await client.get(f"/workflows/{never['id']}")).status_code == 404

    ran = await create(client)
    assert (await client.post(f"/workflows/{ran['id']}/run", json={"projectId": CODE, "input": "x-ray"})).status_code == 200
    kept = await client.delete(f"/workflows/{ran['id']}")
    assert kept.json() == {"ok": True, "archived": True}
    assert ran["id"] not in [w["id"] for w in (await client.get("/workflows")).json()]
    refused = await client.post(f"/workflows/{ran['id']}/run", json={"projectId": CODE, "input": "again"})
    assert refused.status_code == 409 and "archived" in refused.json()["detail"]
    assert (await client.delete("/workflows/requirement-to-pr")).status_code == 409
