"""Sessions over HTTP: starting one, asking it something, reading it back, stopping it.

What is checked here is the promise the route itself makes — **your question is stored before the
model is ever called**. The thinking happens in a background task with a database session of its own,
so it is tested on its own; a route test that waited for a model would be testing the model.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def test_a_session_starts_on_a_project(client: AsyncClient):
    made = await client.post("/sessions", json={"projectId": "erp"})
    assert made.status_code == 201
    body = made.json()
    assert body["ref"].startswith("CHAT-") and body["title"] == "New session"
    assert body["projectId"] == "erp" and body["projectName"] == "Legacy ERP"
    assert body["status"] == "idle" and body["turns"] == 0 and body["toolCalls"] == 0
    assert body["startedBy"] == "Rajat"

    listed = (await client.get("/sessions")).json()
    assert [s["ref"] for s in listed] == [body["ref"]]
    assert (await client.post("/sessions", json={"projectId": "nope"})).status_code == 404


async def test_the_question_is_kept_before_anything_else_happens(client: AsyncClient):
    ref = (await client.post("/sessions", json={"projectId": "erp"})).json()["ref"]

    asked = await client.post(f"/sessions/{ref}/messages", json={"text": "tax kahan handle hota hai?"})
    assert asked.status_code == 201
    body = asked.json()
    assert body["message"]["role"] == "you" and body["message"]["text"] == "tax kahan handle hota hai?"
    assert body["session"]["title"] == "tax kahan handle hota hai?"      # the first question names it

    detail = (await client.get(f"/sessions/{ref}")).json()
    assert [m["role"] for m in detail["messages"]] == ["you"]
    assert detail["messages"][0]["by"] == "Rajat"

    after = (await client.get(f"/sessions/{ref}", params={"after": detail["messages"][0]["id"]})).json()
    assert after["messages"] == []                                      # catching up asks only for new turns


async def test_an_empty_question_is_refused_and_an_unknown_session_is_404(client: AsyncClient):
    ref = (await client.post("/sessions", json={"projectId": "erp"})).json()["ref"]
    assert (await client.post(f"/sessions/{ref}/messages", json={"text": "   "})).status_code == 422
    assert (await client.post("/sessions/CHAT-999/messages", json={"text": "hi"})).status_code == 404
    assert (await client.get("/sessions/CHAT-999")).status_code == 404


async def test_a_session_can_be_stopped(client: AsyncClient):
    ref = (await client.post("/sessions", json={"projectId": "erp"})).json()["ref"]
    stopped = await client.post(f"/sessions/{ref}/cancel")
    assert stopped.status_code == 200 and stopped.json()["ref"] == ref
    assert (await client.post("/sessions/CHAT-999/cancel")).status_code == 404
