"""Sessions over HTTP: starting one, asking it something, reading it back, stopping it.

What is checked here is the promise the route itself makes — **your question is stored before the
model is ever called**. The thinking happens in a background task with a database session of its own,
so it is tested on its own; a route test that waited for a model would be testing the model.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.repositories import ChatRepository
from app.secrets import Secrets
from app.services.chat import KEEP_RECENT
from tests.fixtures.lanes import answering, no_lane
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest.fixture
def api_gateway(tmp_path: Path) -> Gateway:
    """The real gateway over a ledger in memory: which lane answers is up to each test."""
    return Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))


@pytest_asyncio.fixture
async def client(session: AsyncSession, api_gateway: Gateway) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: api_gateway
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


async def test_a_session_says_how_full_its_context_is(client: AsyncClient, session: AsyncSession):
    ref = (await client.post("/sessions", json={"projectId": "erp"})).json()["ref"]
    fresh = (await client.get(f"/sessions/{ref}")).json()
    assert fresh["contextTokens"] is None and fresh["contextWindow"] is None    # nothing has answered yet
    # The ERP's code is not on this machine, so there were no instruction files to read.
    assert fresh.get("instructions") is None

    chat = await ChatRepository(session).by_ref(ref)
    assert chat is not None
    chat.context_tokens, chat.lane, chat.model = 65_536, "groq", "llama-3.3-70b-versatile"
    await session.flush()
    body = (await client.get(f"/sessions/{ref}")).json()
    assert (body["contextTokens"], body["contextWindow"]) == (65_536, 131_072)
    assert 0 < body["autoCompactAt"] < 1


async def test_compacting_folds_older_turns_through_the_route(client: AsyncClient, session: AsyncSession,
                                                              monkeypatch: pytest.MonkeyPatch, api_gateway: Gateway):
    ref = (await client.post("/sessions", json={"projectId": "erp"})).json()["ref"]
    assert (await client.post(f"/sessions/{ref}/compact")).status_code == 409     # nothing to fold yet
    assert (await client.post("/sessions/CHAT-999/compact")).status_code == 404

    chat = await ChatRepository(session).by_ref(ref)
    assert chat is not None
    for n in range(8):
        await ChatRepository(session).say(chat.id, role="you", body=f"question {n}", by="Rajat")
        await ChatRepository(session).say(chat.id, role="assistant", body=f"answer {n}")
    sent = answering(monkeypatch, {"summary": "Eight questions about the ERP's tax."})

    folded = await client.post(f"/sessions/{ref}/compact")
    assert folded.status_code == 201
    body = folded.json()
    assert body["summary"]["role"] == "summary" and body["summary"]["text"] == "Eight questions about the ERP's tax."
    assert body["summary"]["folded"]["turns"] == 16 - KEEP_RECENT and body["summary"]["lane"] == "groq"
    assert "question 0" in sent[0][1]["content"]

    detail = (await client.get(f"/sessions/{ref}")).json()["messages"]
    assert len(detail) == 17                                       # every turn is still there to read
    assert sum(1 for m in detail if m.get("compacted")) == 16 - KEEP_RECENT
    assert api_gateway.store.calls[-1]["feature"] == "compact"

    chat.status = "thinking"
    await session.flush()
    busy = await client.post(f"/sessions/{ref}/compact")
    assert busy.status_code == 409 and "still answering" in busy.json()["detail"]


async def test_with_no_model_nothing_is_folded(client: AsyncClient, session: AsyncSession,
                                               monkeypatch: pytest.MonkeyPatch):
    ref = (await client.post("/sessions", json={"projectId": "erp"})).json()["ref"]
    chat = await ChatRepository(session).by_ref(ref)
    assert chat is not None
    for n in range(12):
        await ChatRepository(session).say(chat.id, role="you", body=f"q{n}", by="Rajat")
    no_lane(monkeypatch)
    refused = await client.post(f"/sessions/{ref}/compact")
    assert refused.status_code == 409 and "Nothing was folded" in refused.json()["detail"]
    assert not any(m.get("compacted") for m in (await client.get(f"/sessions/{ref}")).json()["messages"])
