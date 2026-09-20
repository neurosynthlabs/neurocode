"""A session's answer as it is written, its reasoning, its context, and folding its older turns.

Like the loop's own tests, the answering here runs as `think` does in life — sessions of its own over a
committed database — so the data is committed and removed afterwards. The model is either a local HTTP
server speaking the provider's streaming shape (so the real gateway streams) or a scripted stand-in;
nothing leaves the machine.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.ai import lanes
from app.ai.gateway import Gateway, Provider, Result
from app.ai.ledger import MemoryLedger
from app.data.engine import Database
from app.events import Bus
from app.models import Chat, Project
from app.repositories import ChatRepository
from app.schemas.runtime import chat_json, chat_message_json
from app.services import chat as chat_service
from app.services.chat import KEEP_RECENT, ChatService, _wire, think
from app.services.errors import Refused
from app.secrets import Secrets
from tests.test_gateway import Provider as FakeProvider
from tests.test_gateway import sse

PROJECT, CHAT = "live-session-project", "CHAT-9101"


@pytest.fixture(autouse=True)
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty Claude home, so no test reads the skills of whoever runs the suite."""
    monkeypatch.setenv("NEUROCODE_CLAUDE_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def live(schema: str) -> AsyncIterator[Database]:
    db = Database(url=schema, bus=Bus())
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Live Session"))
    yield db
    async with db.session() as s:
        await s.execute(delete(Chat).where(Chat.project_id == PROJECT))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


@pytest.fixture
def provider() -> Iterator[FakeProvider]:
    server = FakeProvider()
    yield server
    server.close()


@pytest.fixture
def deepseek(provider: FakeProvider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gateway:
    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    monkeypatch.setenv("NEUROCODE_COMPILER", "deepseek")
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("deepseek_api_key", "test-key")
    return Gateway(MemoryLedger({"ai.lane.deepseek": {"baseUrl": provider.url}}), secrets)


async def start(db: Database, *turns: tuple[str, str], **fields: Any) -> None:
    """A session holding these turns — (role, text) — with the last question unanswered."""
    async with db.session() as s:
        chats = ChatRepository(s)
        chat = await chats.add(Chat(id="live-1", ref=CHAT, project_id=PROJECT, title="t", started_by="Rajat",
                                    **{"status": "thinking", **fields}))
        for role, text in turns:
            await chats.say(chat.id, role=role, body=text, by="Rajat" if role == "you" else "")


async def turns_of(db: Database) -> list[Any]:
    async with db.read() as s:
        chat = await ChatRepository(s).by_ref(CHAT)
        return await ChatRepository(s).messages(chat.id)


async def chat_row(db: Database) -> Chat:
    async with db.read() as s:
        found = await ChatRepository(s).by_ref(CHAT)
        assert found is not None
        return found


def streamed(events: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [data["stream"] for kind, data in events if kind == "chat" and "stream" in data]


# ── streaming ────────────────────────────────────────────────────
async def test_the_answer_streams_to_open_tabs_and_is_written_once(live: Database, deepseek: Gateway,
                                                                   provider: FakeProvider):
    provider.replies.append((200, sse(
        {"choices": [{"delta": {"reasoning_content": "The question is "}}]},
        {"choices": [{"delta": {"reasoning_content": "about tax."}}]},
        {"choices": [{"delta": {"content": '{"answer": "Tax is in '}}]},
        {"choices": [{"delta": {"content": 'pkg/tax.py, \\"split\\"."}'}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 4321, "completion_tokens": 50, "prompt_cache_hit_tokens": 4096,
                                  "completion_tokens_details": {"reasoning_tokens": 20}}})))
    events: list[tuple[str, Any]] = []
    live.bus.listen(lambda kind, data: events.append((kind, data)))
    await start(live, ("you", "where is tax handled?"))

    await think(live, deepseek, CHAT, "Rajat")

    deltas = streamed(events)
    assert deltas and all(d["step"] == 0 for d in deltas)
    shown = "".join(d.get("answer", "") for d in deltas)
    assert shown == 'Tax is in pkg/tax.py, "split".'              # the answer inside the JSON, unescaped
    assert "".join(d.get("reasoning", "") for d in deltas) == "The question is about tax."
    # Appended by position: each piece says where it goes.
    at = 0
    for d in deltas:
        if "answer" in d:
            assert d["answerAt"] == at
            at += len(d["answer"])

    turns = await turns_of(live)
    answers = [m for m in turns if m.role == "assistant"]
    assert len(answers) == 1 and answers[0].body == 'Tax is in pkg/tax.py, "split".'
    assert answers[0].reasoning == "The question is about tax."
    shaped = chat_message_json(answers[0])
    assert shaped["reasoning"] == "The question is about tax." and shaped["thought"]["tokens"] == 20
    assert isinstance(shaped["thought"]["ms"], int)
    chat = await chat_row(live)
    assert chat.context_tokens == 4321 and chat.lane == "deepseek" and chat.status == "idle"
    assert chat_json(chat)["contextWindow"] == 1_000_000
    assert provider.sent[0]["stream"] is True
    assert provider.sent[0]["thinking"] == {"type": "enabled"}      # a session thinks a little by default
    assert provider.sent[0]["reasoning_effort"] == "low"
    # The reasoning is never sent back: the model is sent the conversation, not its working.
    async with live.read() as s:
        wire = await _wire(s, chat, "Live Session")
    assert not any("about tax." in m["content"] for m in wire)


async def test_a_stop_mid_answer_keeps_what_was_written_marked_stopped(live: Database, deepseek: Gateway,
                                                                       provider: FakeProvider):
    provider.replies.append((200, sse(
        {"choices": [{"delta": {"content": '{"answer": "It is in'}}]},
        *({"choices": [{"delta": {"content": " more"}}]} for _ in range(40)),
        {"choices": [{"delta": {"content": '"}'}}]})))

    def stop_on_first_words(kind: str, data: Any) -> None:
        if kind == "chat" and "stream" in data and data["stream"].get("answer"):
            chat_service.stop(CHAT)                  # what the Stop button's route does

    live.bus.listen(stop_on_first_words)
    await start(live, ("you", "where?"))

    await think(live, deepseek, CHAT, "Rajat")

    turns = [m for m in await turns_of(live) if m.tool != "grounding"]
    assert [m.role for m in turns] == ["you", "assistant", "note"]
    assert turns[1].detail == "stopped" and turns[1].body.startswith("It is in")
    assert turns[2].body == "You stopped this answer."
    assert (await chat_row(live)).status == "idle" and (await chat_row(live)).turns == 0
    assert deepseek.store.calls[-1]["error"] == "stopped by a person"


# ── compaction ───────────────────────────────────────────────────
class Scripted:
    """A stand-in gateway: answers each feature from its own script, and remembers what it was sent."""

    def __init__(self, **by_feature: list[str]) -> None:
        self.by_feature = by_feature
        self.seen: list[tuple[str, list[dict[str, str]]]] = []

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, str]], parse: Any, *, feature: str = "", **_: Any) -> Result[Any]:
        self.seen.append((feature, messages))
        raw = self.by_feature[feature].pop(0)
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 7, reasoning="",
                      usage={"in": 900, "out": 30})


def conversation(n: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i in range(n):
        out += [("you", f"question {i}"), ("assistant", f"answer {i}")]
    return out


async def test_compacting_folds_the_older_turns_and_the_model_is_sent_the_summary(live: Database):
    await start(live, *conversation(6), status="idle")
    gateway = Scripted(compact=[json.dumps({"summary": "They asked about tax; it is in pkg/tax.py."})])

    async with live.session() as s:
        summary = await ChatService(s, gateway).compact(CHAT, "Rajat")   # type: ignore[arg-type]
        shaped = chat_message_json(summary)
    assert shaped["role"] == "summary" and shaped["folded"]["turns"] == 12 - KEEP_RECENT
    assert shaped["lane"] == "groq"
    feature, sent = gateway.seen[0]
    assert feature == "compact" and "question 0" in sent[1]["content"] and "question 5" not in sent[1]["content"]

    turns = await turns_of(live)
    assert len(turns) == 13                                     # nothing deleted: 12 turns and the summary
    folded = [m for m in turns if m.compacted]
    assert [m.body for m in folded] == [b for _, b in conversation(6)][:12 - KEEP_RECENT]
    assert all(chat_message_json(m)["compacted"] for m in folded)

    async with live.read() as s:
        wire = await _wire(s, await ChatRepository(s).by_ref(CHAT), "Live Session")
    text = [m["content"] for m in wire]
    assert "pkg/tax.py" in text[1] and "summary" in text[1]
    assert not any("question 0" == t for t in text) and "question 5" in text
    assert len(wire) == 2 + KEEP_RECENT

    # Folding again folds the summary with the rest; too little left to fold is refused.
    async with live.session() as s:
        with pytest.raises(Refused, match="nothing to compact"):
            await ChatService(s, gateway).compact(CHAT, "Rajat")   # type: ignore[arg-type]


async def test_a_session_near_its_window_folds_before_it_answers(live: Database):
    window = lanes.window_for("groq", "openai/gpt-oss-120b")
    assert window
    await start(live, *conversation(6), ("you", "and now?"), lane="groq", model="openai/gpt-oss-120b",
                context_tokens=int(window * 0.9))
    gateway = Scripted(compact=[json.dumps({"summary": "Six questions about tax."})],
                       chat=[json.dumps({"answer": "Still pkg/tax.py."})])

    await think(live, gateway, CHAT, "Rajat")   # type: ignore[arg-type]

    assert [f for f, _ in gateway.seen] == ["compact", "chat"]
    turns = await turns_of(live)
    assert any(m.role == "summary" for m in turns) and turns[-1].body == "Still pkg/tax.py."
    chat = await chat_row(live)
    assert chat.context_tokens == 900                          # what the provider counted on this call
    sent = gateway.seen[1][1]
    assert any("Six questions about tax." in m["content"] for m in sent)
    assert not any(m["content"] == "question 0" for m in sent)


async def test_a_session_well_inside_its_window_is_not_folded(live: Database):
    await start(live, *conversation(6), ("you", "and now?"), lane="groq", model="openai/gpt-oss-120b",
                context_tokens=2_000)
    gateway = Scripted(chat=[json.dumps({"answer": "Here."})])
    await think(live, gateway, CHAT, "Rajat")   # type: ignore[arg-type]
    assert [f for f, _ in gateway.seen] == ["chat"]
    assert not any(m.compacted for m in await turns_of(live))
