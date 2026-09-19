"""The domain routes on the new stack: same paths, same JSON, rules that now live in services.

Signed in as the first Owner, against the tests' own workspace, inside one rolled-back transaction.
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps, routes_runs
from app.api.app import create_api
from app.services import runs as runtime
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


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
        await c.post("/auth/setup", json=OWNER)           # an Owner holds every permission
        yield c


async def test_tasks_come_back_in_the_shape_the_board_reads(client: AsyncClient):
    tasks = (await client.get("/tasks", params={"project": "erp"})).json()
    assert len(tasks) == 10
    one = next(t for t in tasks if t["checklist"])
    assert set(one) >= {"id", "ref", "title", "projectId", "status", "priority", "risk", "layers",
                        "agents", "progress", "checklist", "createdAt"}
    assert isinstance(one["agents"], list) and isinstance(one["checklist"][0]["done"], bool)


async def test_a_move_the_board_does_not_allow_is_refused_in_words(client: AsyncClient):
    waiting = next(t for t in (await client.get("/tasks")).json() if t["status"] == "backlog")
    refused = await client.patch(f"/tasks/{waiting['ref']}", json={"status": "done"})
    assert refused.status_code == 409 and "not to done" in refused.json()["detail"]

    moved = await client.patch(f"/tasks/{waiting['ref']}", json={"status": "in_progress"})
    assert moved.status_code == 200 and moved.json()["status"] == "in_progress"


async def test_ticking_the_checklist_moves_the_progress(client: AsyncClient):
    task = next(t for t in (await client.get("/tasks")).json() if len(t["checklist"]) > 1)
    item = next(i for i in task["checklist"] if not i["done"])
    after = (await client.post(f"/tasks/{task['ref']}/checklist/{item['id']}", json={"done": True})).json()
    assert next(i for i in after["checklist"] if i["id"] == item["id"])["done"] is True
    assert after["progress"] >= task["progress"]


async def test_a_gate_is_answered_once_and_only_once(client: AsyncClient):
    pending = (await client.get("/approvals", params={"status": "pending"})).json()
    assert pending and all(a["status"] == "pending" for a in pending)

    ref = pending[0]["ref"]
    approved = await client.post(f"/approvals/{ref}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert approved.json()["decidedBy"] and approved.json()["decidedAt"]

    again = await client.post(f"/approvals/{ref}/deny")
    assert again.status_code == 409 and "a decision is final" in again.json()["detail"]


async def test_answering_a_plan_question_writes_it_into_memory(client: AsyncClient):
    plans = (await client.get("/plans")).json()
    plan = next(p for p in plans if p["openQuestions"])
    question, before = plan["openQuestions"][0], len(plan["openQuestions"])

    answered = await client.post(f"/plans/{plan['ref']}/questions/0",
                                 json={"answer": "Per line item, then the invoice total."})
    assert answered.status_code == 200
    body = answered.json()
    assert len(body["openQuestions"]) == before - 1
    assert {"q": question, "a": "Per line item, then the invoice total."} in body["answered"]

    remembered = (await client.get("/memory", params={"q": question[:40]})).json()
    assert any(f["title"] == question and f["category"] == "business_rules" for f in remembered)


async def test_a_question_can_be_deferred_without_pretending_it_was_answered(client: AsyncClient):
    plan = next(p for p in (await client.get("/plans")).json() if p["openQuestions"])
    question = plan["openQuestions"][0]
    deferred = (await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})).json()
    assert question in deferred["deferred"] and question not in deferred["openQuestions"]
    assert all(a["q"] != question for a in deferred["answered"])


async def test_memory_is_searched_by_words_and_pinned_facts_come_first(client: AsyncClient):
    hits = (await client.get("/memory", params={"q": "tax rounding"})).json()
    assert hits and any("rounding" in f["title"].lower() for f in hits)
    assert [f["pinned"] for f in hits] == sorted((f["pinned"] for f in hits), reverse=True)

    ref = hits[-1]["ref"]
    assert (await client.post(f"/memory/{ref}/pin", json={"pinned": True})).json()["pinned"] is True


async def test_an_archived_fact_is_kept_but_stops_appearing(client: AsyncClient):
    fact = (await client.get("/memory")).json()[0]
    assert (await client.post(f"/memory/{fact['ref']}/archive")).status_code == 200
    assert all(f["ref"] != fact["ref"] for f in (await client.get("/memory")).json())
    kept = (await client.get("/memory", params={"include_archived": True})).json()
    assert any(f["ref"] == fact["ref"] for f in kept)               # kept, never deleted


async def test_a_decision_is_final_and_a_setting_is_not(client: AsyncClient):
    made = await client.post("/decisions/release.1.4", json={"value": "ship", "action": "Release signed off",
                                                             "detail": "after the regression run"})
    assert made.status_code == 201 and made.json()["value"] == "ship"
    again = await client.post("/decisions/release.1.4", json={"value": "hold", "action": "Changed my mind"})
    assert again.status_code == 409 and "final" in again.json()["detail"]

    # The person's name, as the screen writes it before this copy arrives — never their id.
    assert made.json()["decidedBy"] == "Rajat"
    listed = {d["id"]: d for d in (await client.get("/decisions")).json()}
    assert listed["release.1.4"]["decidedBy"] == "Rajat"

    assert (await client.put("/prefs/skills.rag", json={"value": False})).json()["value"] is False
    assert (await client.put("/prefs/skills.rag", json={"value": True})).json()["value"] is True
    assert any(p["id"] == "skills.rag" for p in (await client.get("/prefs")).json())


async def test_a_decision_or_a_setting_names_a_real_project_or_none(client: AsyncClient):
    """Both used to default to `aios`, a project only the sample workspace had — a foreign key now, so a
    workspace without it failed on the first decision. With no project they belong to the workspace."""
    made = await client.post("/decisions/gate.open", json={"value": "open", "action": "Gate opened"})
    assert made.status_code == 201
    feed = (await client.get("/activity", params={"limit": 5})).json()
    assert next(e for e in feed if e["action"] == "Gate opened")["projectId"] is None

    missing = await client.post("/decisions/gate.shut", json={"value": "shut", "action": "Gate shut",
                                                             "projectId": "aios"})
    assert missing.status_code == 404 and "aios" in missing.json()["detail"]
    unknown = await client.put("/prefs/models.router", json={"value": "auto", "detail": "Router set",
                                                             "projectId": "aios"})
    assert unknown.status_code == 404
    assert (await client.put("/prefs/models.router", json={"value": "auto", "detail": "Router set",
                                                           "projectId": "erp"})).status_code == 200


async def test_a_workspace_fact_has_no_project(client: AsyncClient):
    """A fact that belongs to no project belongs to the workspace, and says so with a null project."""
    fact = {"title": "Deploys stop on Friday", "body": "Nothing is deployed after Friday noon.",
            "category": "decisions"}
    added = await client.post("/memory/facts", json={"facts": [fact]})
    assert added.status_code == 201 and added.json()[0]["projectId"] is None
    spelled = await client.post("/memory/facts", json={"projectId": "global", "facts": [fact]})
    assert spelled.status_code == 201 and spelled.json()[0]["projectId"] is None
    workspace = (await client.get("/memory", params={"project": "global", "q": "Friday"})).json()
    assert {f["ref"] for f in workspace} == {added.json()[0]["ref"], spelled.json()[0]["ref"]}


# ── sending a run back for changes ───────────────────────────────
REWORK_PID, OLD_BRANCH = "rework-lab", "neurocode/task-7001"
ENGINEER = {"email": "dev@example.com", "name": "Dev", "password": "another long passphrase", "roles": ["engineer"]}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest_asyncio.fixture
async def reviewed(session: AsyncSession, client: AsyncClient, tmp_path: Path,
                   monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A plan whose run finished its work and stopped at your signature, on a real branch in a real
    worktree — and the runtime's start replaced by a recorder, so nothing actually runs."""
    root = tmp_path / "shop"
    root.mkdir()
    (root / "core.py").write_text("def total(x):\n    return x\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    base = run_git(["rev-parse", "HEAD"], root).strip()
    tree = tmp_path / "worktrees" / "RUN-7001"
    run_git(["worktree", "add", "-q", "-b", OLD_BRANCH, str(tree), base], root)
    (tree / "core.py").write_text("def total(x):\n    return round(x)\n")
    run_git(["commit", "-qam", "round"], tree)

    session.add(m.Project(id=REWORK_PID, name="Rework Lab", source_kind="local", source_repo=str(root)))
    await session.flush()
    session.add(m.Task(id="t-rw", ref="TASK-7001", title="Round the total", project_id=REWORK_PID,
                       status="review", requirement="Round the total to two places"))
    await session.flush()
    session.add(m.Plan(id="p-rw", ref="PLAN-7001", task_id="t-rw", project_id=REWORK_PID, status="dispatched",
                       raw_requirement="Round the total to two places", affected_files=["core.py"],
                       steps=[m.PlanStep(id="p-rw-1", n=1, label="Round in core.py", agent="Backend Engineer")]))
    await session.flush()
    session.add(m.Run(
        id="r-RUN-7001", ref="RUN-7001", project_id=REWORK_PID, task_id="t-rw", plan_id="p-rw", status="waiting",
        role="solo", branch=OLD_BRANCH, worktree=str(tree), repo=str(root), base=base,
        requirement="Round the total to two places", requested_by="Rajat", diff_files=1, waiting_on="APPR-7001",
        review={"findings": [{"severity": "HIGH", "file": "core.py", "note": "round() drops the cents"}],
                "verdict": "Rounds to whole units.", "by": "llama-3.3-70b-versatile"},
        steps=[m.RunStep(n=1, kind="edit", label="Round in core.py", status="done"),
               m.RunStep(n=2, kind="review", label="Review the diff", status="done"),
               m.RunStep(n=3, kind="handoff", label="Your approval", status="waiting")],
        conflicts=[]))
    await session.flush()
    session.add(m.Approval(id="ap-rw", ref="APPR-7001", title="Accept RUN-7001", tool=f"Merge({OLD_BRANCH})",
                           risk="HIGH", status="pending", project_id=REWORK_PID, run_ref="RUN-7001", step=3))
    await session.flush()

    started: list[tuple[Any, ...]] = []

    async def record(open_session: AsyncSession, jobs: Any, job: Any, *args: Any) -> None:
        started.append((job, *args))

    monkeypatch.setattr(routes_runs, "hand_off", record)
    return {"root": root, "tree": tree, "started": started}


def branches(repo: Path) -> list[str]:
    return run_git(["branch", "--format=%(refname:short)"], repo).split()


async def test_sending_a_run_back_starts_it_again_with_the_notes_and_removes_the_old_one(
        client: AsyncClient, reviewed: dict[str, Any], session: AsyncSession):
    sent = await client.post("/runs/RUN-7001/rework", json={"notes": "Keep the cents: round to two places."})
    assert sent.status_code == 200, sent.text
    new = sent.json()
    assert new["ref"] != "RUN-7001" and new["status"] == "queued" and new["planRef"] == "PLAN-7001"
    assert new["taskRef"] == "TASK-7001" and [s["kind"] for s in new["steps"]] == ["edit", "review", "handoff"]
    # the brief carries what was asked for and what the review found
    assert "Round the total to two places" in new["requirement"]
    assert "Keep the cents: round to two places." in new["requirement"]
    assert "HIGH core.py: round() drops the cents" in new["requirement"]
    [(job, _db, _gw, ref)] = reviewed["started"]
    assert job is runtime.execute and ref == new["ref"]

    logs = (await client.get(f"/runs/{new['ref']}")).json()["logs"]
    assert logs[0]["line"].startswith("rework of RUN-7001, sent back by Rajat")

    old = (await client.get("/runs/RUN-7001")).json()
    assert old["status"] == "cancelled" and old["removed"] is True and "waitingOn" not in old
    assert old["review"]["reworkedAs"] == new["ref"] and new["ref"] in old["note"]
    assert not reviewed["tree"].exists() and OLD_BRANCH not in branches(reviewed["root"])

    gate = next(a for a in (await client.get("/approvals")).json() if a["ref"] == "APPR-7001")
    assert gate["status"] == "denied" and gate["decidedAt"]
    assert (await client.get("/tasks/TASK-7001")).json()["status"] == "in_progress"
    line = next(e for e in (await client.get("/activity")).json() if e["action"] == "Sent back for changes")
    assert line["actor"] == "Rajat" and "RUN-7001 →" in line["detail"] and "APPR-7001 refused" in line["detail"]

    again = await client.post("/runs/RUN-7001/rework", json={"notes": "once more"})
    assert again.status_code == 409 and new["ref"] in again.json()["detail"]


async def test_a_run_still_working_or_already_merged_is_not_sent_back(
        client: AsyncClient, reviewed: dict[str, Any], session: AsyncSession):
    run = await session.get(m.Run, "r-RUN-7001")
    for status in ("queued", "running"):
        run.status = status
        await session.flush()
        refused = await client.post("/runs/RUN-7001/rework", json={"notes": "change it"})
        assert refused.status_code == 409 and "still working" in refused.json()["detail"]

    run.status, run.merged = "done", {"into": "main", "commit": "abc1234", "at": "", "by": "Rajat", "undo": ""}
    await session.flush()
    refused = await client.post("/runs/RUN-7001/rework", json={"notes": "change it"})
    assert refused.status_code == 409 and "already merged into main" in refused.json()["detail"]

    assert (await client.post("/runs/RUN-7001/rework", json={"notes": ""})).status_code == 422
    assert (await client.post("/runs/RUN-7001/rework", json={"notes": "x" * 4001})).status_code == 422
    assert (await client.post("/runs/RUN-0/rework", json={"notes": "change it"})).status_code == 404
    assert reviewed["started"] == [] and reviewed["tree"].exists()


async def test_a_run_waiting_on_its_tests_is_answered_there_first(
        client: AsyncClient, reviewed: dict[str, Any], session: AsyncSession):
    gate = (await session.execute(select(m.Approval).where(m.Approval.ref == "APPR-7001"))).scalar_one()
    gate.step = 2
    step = next(s for s in (await session.get(m.Run, "r-RUN-7001")).steps if s.n == 2)
    step.kind = "test"
    await session.flush()
    refused = await client.post("/runs/RUN-7001/rework", json={"notes": "change it"})
    assert refused.status_code == 409 and "running its tests" in refused.json()["detail"]
    assert reviewed["started"] == []


async def test_sending_back_needs_runs_run_and_refusing_the_signature_needs_approvals_decide(
        api, client: AsyncClient, reviewed: dict[str, Any], session: AsyncSession):
    for person in (VIEWER, ENGINEER):
        assert (await client.post("/admin/users", json=person)).status_code in (200, 201)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.post("/runs/RUN-7001/rework", json={"notes": "change it"})).status_code == 403
    async with _client(api) as engineer:
        await engineer.post("/auth/login", json={"email": ENGINEER["email"], "password": ENGINEER["password"]})
        denied = await engineer.post("/runs/RUN-7001/rework", json={"notes": "change it"})
        assert denied.status_code == 403 and "approvals:decide" in denied.json()["detail"]

        # Once nothing is waiting for a signature, sending it back is the engineer's to do.
        run = await session.get(m.Run, "r-RUN-7001")
        run.status, run.waiting_on = "cancelled", None
        gate = (await session.execute(select(m.Approval).where(m.Approval.ref == "APPR-7001"))).scalar_one()
        gate.status = "denied"
        await session.flush()
        assert (await engineer.post("/runs/RUN-7001/rework", json={"notes": "change it"})).status_code == 200
    async with _client(api) as stranger:
        assert (await stranger.post("/runs/RUN-7001/rework", json={"notes": "change it"})).status_code == 401
