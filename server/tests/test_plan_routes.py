"""Compiling a requirement, settling what it could not decide, and dispatching it.

A plan is only ever a model's, so the gateway here is a real one with one lane whose provider call
answers from a script (`tests/fixtures/lanes.py`) — the routing, the parsing and the ledger are all the
real ones. With no lane at all, compiling is refused and writes nothing. Nothing in this file reaches
the network.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import Gateway, ProviderError
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.models import Plan, Task
from app.secrets import Secrets
from app.services.errors import NO_MODEL
from tests.fixtures.lanes import LANE_MODEL, PLAN, answering, no_lane
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
TAX = "Invoice mein tax galat aa raha hai — CGST/SGST interstate orders pe reverse ho raha hai, TRANS_INVOICE table fix karo."


@pytest_asyncio.fixture
async def client(session: AsyncSession, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def compile_tax(client: AsyncClient) -> dict:
    made = await client.post("/plans/compile", json={"requirement": TAX, "projectId": "erp"})
    assert made.status_code == 201, made.text
    return made.json()


async def plan_count(session: AsyncSession) -> tuple[int, int]:
    return ((await session.execute(select(func.count()).select_from(Plan))).scalar_one(),
            (await session.execute(select(func.count()).select_from(Task))).scalar_one())


async def test_a_requirement_becomes_a_plan_and_the_task_that_carries_it(client: AsyncClient, monkeypatch):
    sent = answering(monkeypatch, PLAN)
    plan = await compile_tax(client)

    assert plan["ref"].startswith("PLAN-") and plan["projectId"] == "erp"
    assert plan["rawRequirement"] == TAX                      # your words are kept, not rewritten away
    assert plan["compiler"]["provider"] == "groq" and plan["compiler"]["model"] == LANE_MODEL
    assert TAX in sent[0][1]["content"]                       # the model was asked about this requirement
    assert plan["risk"] == "HIGH" and plan["confidence"] == 72     # the model's words, not a formula's
    assert [s["agent"] for s in plan["steps"]] == ["Architect", "Backend Engineer", "QA Engineer", "Code Reviewer"]
    assert plan["steps"][0]["state"] == "todo" and plan["workflowId"] is None
    assert plan["status"] == "draft" and plan["requestedBy"] == "Rajat"

    task = plan["task"]
    assert task["ref"].startswith("TASK-") and task["status"] == "planning"
    assert len(task["checklist"]) == len(plan["steps"])
    assert task["agents"] and task["projectId"] == "erp"

    listed = (await client.get("/plans")).json()
    assert plan["ref"] in [p["ref"] for p in listed]


async def test_a_model_that_gives_no_confidence_is_given_none(client: AsyncClient, monkeypatch):
    answering(monkeypatch, {k: v for k, v in PLAN.items() if k != "confidence"})
    assert (await compile_tax(client))["confidence"] is None


async def test_with_no_model_compiling_is_refused_and_writes_nothing(client: AsyncClient,
                                                                     session: AsyncSession, monkeypatch):
    no_lane(monkeypatch)
    before = await plan_count(session)
    refused = await client.post("/plans/compile", json={"requirement": TAX, "projectId": "erp"})
    assert refused.status_code == 409 and refused.json()["detail"] == NO_MODEL
    assert "Admin → AI providers" in NO_MODEL and "Groq, Cerebras or Gemini" in NO_MODEL
    assert await plan_count(session) == before


async def test_a_provider_that_fails_is_passed_through_with_its_reason(client: AsyncClient,
                                                                       session: AsyncSession, monkeypatch):
    answering(monkeypatch, ProviderError(503, "the lane is down"))
    before = await plan_count(session)
    failed = await client.post("/plans/compile", json={"requirement": TAX, "projectId": "erp"})
    assert failed.status_code == 502 and "the lane is down" in failed.json()["detail"]
    assert await plan_count(session) == before


@pytest.mark.parametrize(("why", "status"), [("no lane", 409), ("provider failed", 502)])
async def test_recompiling_without_an_answer_is_refused_and_leaves_the_plan_as_it_was(
        client: AsyncClient, monkeypatch, why: str, status: int):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    if why == "no lane":
        no_lane(monkeypatch)
    else:
        answering(monkeypatch, ProviderError(503, "the lane is down"))
    refused = await client.post(f"/plans/{plan['ref']}/recompile")
    assert refused.status_code == status
    kept = (await client.get(f"/plans/{plan['ref']}")).json()
    assert kept["steps"] == plan["steps"] and kept["compiler"] == plan["compiler"]


async def test_it_refuses_to_guess_and_dispatch_waits_for_you(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    assert plan["openQuestions"] == PLAN["openQuestions"]

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


async def test_dispatching_moves_the_task_it_carries(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    for _ in list(plan["openQuestions"]):
        await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    await client.post(f"/plans/{plan['ref']}/dispatch")

    task = (await client.get(f"/tasks/{plan['task']['ref']}")).json()
    assert task["status"] == "in_progress"


async def test_recompiling_keeps_what_was_already_answered(client: AsyncClient, monkeypatch):
    sent = answering(monkeypatch, PLAN, PLAN)
    plan = await compile_tax(client)
    question = plan["openQuestions"][0]
    await client.post(f"/plans/{plan['ref']}/questions/0",
                      json={"answer": "Backfill the 41 affected invoices."})

    again = await client.post(f"/plans/{plan['ref']}/recompile")
    assert again.status_code == 200
    body = again.json()
    assert {"q": question, "a": "Backfill the 41 affected invoices."} in body["answered"]
    assert question not in body["openQuestions"]              # settled once, never asked again
    assert body["steps"] and body["compiler"]["provider"] == "groq"
    assert "Backfill the 41 affected invoices." in sent[1][1]["content"]   # the answer went to the model
