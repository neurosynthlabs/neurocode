"""Compiling a requirement, settling what it could not decide, and dispatching it.

No model is configured here, so the offline keyword planner answers — which is exactly the path worth
testing: it keeps your wording, says it is the offline planner, and still refuses to guess the
business decisions. Nothing in this file reaches the network.
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
TAX = "Invoice mein tax galat aa raha hai — CGST/SGST interstate orders pe reverse ho raha hai, TRANS_INVOICE table fix karo."


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
        await c.post("/auth/setup", json=OWNER)
        yield c


async def compile_tax(client: AsyncClient) -> dict:
    made = await client.post("/plans/compile", json={"requirement": TAX, "projectId": "erp"})
    assert made.status_code == 201, made.text
    return made.json()


async def test_a_requirement_becomes_a_plan_and_the_task_that_carries_it(client: AsyncClient):
    plan = await compile_tax(client)

    assert plan["ref"].startswith("PLAN-") and plan["projectId"] == "erp"
    assert plan["rawRequirement"] == TAX                      # your words are kept, not rewritten away
    assert plan["compiler"]["provider"] == "rules"            # honest about who wrote it
    assert plan["risk"] in ("HIGH", "CRITICAL")               # money and a table: not a small change
    assert len(plan["steps"]) >= 3 and plan["steps"][0]["state"] == "todo"
    assert plan["status"] == "draft" and plan["requestedBy"] == "Rajat"

    task = plan["task"]
    assert task["ref"].startswith("TASK-") and task["status"] == "planning"
    assert len(task["checklist"]) == len(plan["steps"])
    assert task["agents"] and task["projectId"] == "erp"

    listed = (await client.get("/plans")).json()
    assert plan["ref"] in [p["ref"] for p in listed]


async def test_it_refuses_to_guess_and_dispatch_waits_for_you(client: AsyncClient):
    plan = await compile_tax(client)
    assert plan["openQuestions"], "the offline planner should ask rather than guess"

    refused = await client.post(f"/plans/{plan['ref']}/dispatch")
    assert refused.status_code == 409 and "open question" in refused.json()["detail"]

    for _ in list(plan["openQuestions"]):
        answered = await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
        assert answered.status_code == 200

    dispatched = await client.post(f"/plans/{plan['ref']}/dispatch")
    assert dispatched.status_code == 200
    assert dispatched.json()["status"] == "dispatched"
    assert "runRef" not in dispatched.json()                  # this project has no code on this machine

    again = await client.post(f"/plans/{plan['ref']}/dispatch")
    assert again.status_code == 409 and "already under way" in again.json()["detail"]


async def test_dispatching_moves_the_task_it_carries(client: AsyncClient):
    plan = await compile_tax(client)
    for _ in list(plan["openQuestions"]):
        await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    await client.post(f"/plans/{plan['ref']}/dispatch")

    task = (await client.get(f"/tasks/{plan['task']['ref']}")).json()
    assert task["status"] == "in_progress"


async def test_recompiling_keeps_what_was_already_answered(client: AsyncClient):
    plan = await compile_tax(client)
    question = plan["openQuestions"][0]
    await client.post(f"/plans/{plan['ref']}/questions/0",
                      json={"answer": "Backfill the 41 affected invoices."})

    again = await client.post(f"/plans/{plan['ref']}/recompile")
    assert again.status_code == 200
    body = again.json()
    assert {"q": question, "a": "Backfill the 41 affected invoices."} in body["answered"]
    assert question not in body["openQuestions"]              # settled once, never asked again
    assert body["steps"] and body["compiler"]["provider"] == "rules"
