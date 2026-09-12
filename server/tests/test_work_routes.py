"""The domain routes on the new stack: same paths, same JSON, rules that now live in services.

Signed in as the first Owner, against the sample workspace, inside one rolled-back transaction.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.data.loader import load_seed, sync_roles

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
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

    assert (await client.put("/prefs/skills.rag", json={"value": False})).json()["value"] is False
    assert (await client.put("/prefs/skills.rag", json={"value": True})).json()["value"] is True
    assert any(p["id"] == "skills.rag" for p in (await client.get("/prefs")).json())
