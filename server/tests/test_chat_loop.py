"""The answering loop itself: ground, read with a tool, answer — and never lose a turn.

This one does not use the rolled-back fixture. `think` runs as a background task and opens database
sessions of its own, which cannot see another transaction's uncommitted rows — so the data here is
committed for real, against the test database, and deleted afterwards.

No model is ever called: the gateway is a stand-in that hands back a scripted turn.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy import delete

from app.ai.gateway import NoModel, Provider, Result
from app.data.engine import Database
from app.models import Chat, Project
from app.repositories import ChatRepository
from app.services.chat import MAX_STEPS, think

PROJECT, CHAT = "loop-test-project", "CHAT-9001"


class FakeGateway:
    """Says exactly what it is told to, in order. Nothing leaves the machine."""

    def __init__(self, *script: str, raises: Exception | None = None) -> None:
        self.script = list(script)
        self.raises = raises
        self.calls = 0

    def embed_lane(self) -> None:
        return None                                   # no embeddings: retrieval stays lexical

    def ask(self, messages: list[dict[str, str]], parse: Any, **_: Any) -> Result[Any]:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        raw = self.script.pop(0) if self.script else '{"answer": "Done."}'
        return Result(parse(raw), Provider("groq", "llama-3.3-70b-versatile"), 12)


@pytest_asyncio.fixture
async def live(schema: str) -> AsyncIterator[Database]:
    """A committed project and an empty session, cleaned up afterwards."""
    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Loop Test"))
    yield db
    async with db.session() as s:
        await s.execute(delete(Chat).where(Chat.project_id == PROJECT))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


async def start(db: Database, question: str) -> None:
    async with db.session() as s:
        chats = ChatRepository(s)
        chat = await chats.add(Chat(id="loop-1", ref=CHAT, project_id=PROJECT, title=question[:80],
                                    started_by="Rajat", status="thinking"))
        await chats.say(chat.id, role="you", body=question, by="Rajat")


async def turns_of(db: Database) -> list[Any]:
    async with db.read() as s:
        chat = await ChatRepository(s).by_ref(CHAT)
        return await ChatRepository(s).messages(chat.id)


async def test_it_reaches_for_a_tool_then_answers(live: Database):
    await start(live, "where is tax handled?")
    gateway = FakeGateway('{"tool": "search_memory", "arguments": {"query": "tax"}, "why": "look it up"}',
                          '{"answer": "In pkg/tax.py."}')

    await think(live, gateway, CHAT, "Rajat")

    turns = await turns_of(live)
    assert [m.role for m in turns] == ["you", "tool", "assistant"]
    assert turns[1].tool == "search_memory" and turns[1].ok is True
    assert turns[2].body == "In pkg/tax.py." and turns[2].lane == "groq"
    async with live.read() as s:
        chat = await ChatRepository(s).by_ref(CHAT)
    assert chat.status == "idle" and chat.turns == 1 and chat.tool_calls == 1


async def test_a_tool_it_invented_is_reported_back_not_raised(live: Database):
    await start(live, "do something odd")
    gateway = FakeGateway('{"tool": "wander", "arguments": {}}', '{"answer": "I could not."}')

    await think(live, gateway, CHAT, "Rajat")

    turns = await turns_of(live)
    refused = next(m for m in turns if m.role == "tool")
    assert refused.ok is False and "no tool called" in refused.body
    assert turns[-1].role == "assistant"              # the session still ends in words


async def test_it_stops_reaching_for_tools_and_answers_with_what_it_has(live: Database):
    await start(live, "keep going forever")
    gateway = FakeGateway(*['{"tool": "project_summary", "arguments": {}}'] * 12)

    await think(live, gateway, CHAT, "Rajat")

    turns = await turns_of(live)
    tools = [m for m in turns if m.role == "tool" and m.tool != "grounding"]
    assert len(tools) == MAX_STEPS
    assert turns[-1].role == "assistant" and str(MAX_STEPS) in turns[-1].body


async def test_with_no_model_the_question_is_still_there(live: Database):
    await start(live, "kuch bhi poochh raha hoon")
    gateway = FakeGateway(raises=NoModel("No model is configured. Add a free key in Admin → AI providers."))

    await think(live, gateway, CHAT, "Rajat")

    turns = await turns_of(live)
    assert [m.role for m in turns] == ["you", "note"]
    assert turns[0].body == "kuch bhi poochh raha hoon"
    assert "Admin → AI providers" in turns[1].body
    async with live.read() as s:
        assert (await ChatRepository(s).by_ref(CHAT)).status == "idle"
