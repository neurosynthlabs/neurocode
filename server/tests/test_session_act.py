"""Sessions that can act, safely: the web and MCP tools, past the tool rules and a person's permission.

The answering loop runs as it does in production — as a background task with database sessions of its
own — so, like test_chat_loop.py, its data is committed for real and deleted afterwards. The model is a
stand-in that says what it is told; the web page is a standard-library server on this machine, counted
as the internet for the length of a test; the MCP server is a small Python program. Nothing leaves the
machine.
"""
from __future__ import annotations

import ipaddress
import json
import shlex
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import lanes
from app.ai.gateway import Provider, Result
from app.data.base import utcnow
from app.data.engine import Database
from app.models import ActivityEvent, Chat, ChatFile, ChatMessage, McpServer, McpTool, Project, ToolRule
from app.repositories import ChatRepository
from app.services import mcp as mcp_service
from app.services.chat import (PERMISSION, Acting, ChatService, Offered, Turn, _tool_turn, _wire, reach,
                               system_prompt, think)
from app.services.errors import Refused
from app.services.identity import IdentityService, Person
from tests.fixtures.workspace import load_workspace

PROJECT, CHAT = "act-test-project", "CHAT-9101"
ME = Person(id="u-act-test", email="act@example.com", name="Rajat", status="active", roles=("owner",),
            permissions=frozenset({"sessions:chat"}))


class FakeGateway:
    """Says exactly what it is told to, in order, and keeps what it was sent. Nothing leaves the machine."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.seen: list[list[dict[str, Any]]] = []
        self.lanes_asked: list[str | None] = []

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, Any]], parse: Any, **kwargs: Any) -> Result[Any]:
        self.seen.append(messages)
        self.lanes_asked.append(kwargs.get("lane"))
        raw = self.script.pop(0) if self.script else '{"answer": "Done."}'
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 12)


class Page(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 — the standard library's name
        Page.hits.append(self.path)
        body = b"<html><head><title>Tax rules</title></head><body><p>IGST applies across states.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        return


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A page on this machine that, for this test only, counts as the internet."""
    Page.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real = mcp_service._public
    monkeypatch.setattr(mcp_service, "_public", lambda ip: ip == ipaddress.ip_address("127.0.0.1") or real(ip))
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty Claude home, so no test reads the skills of whoever runs the suite."""
    monkeypatch.setenv("NEUROCODE_CLAUDE_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def live(schema: str) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Act Test"))
    yield db
    async with db.session() as s:
        await s.execute(delete(Chat).where(Chat.project_id == PROJECT))
        await s.execute(delete(ToolRule).where(ToolRule.project_id == PROJECT))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


async def start(db: Database, question: str) -> None:
    async with db.session() as s:
        chats = ChatRepository(s)
        chat = await chats.by_ref(CHAT)
        if chat is None:
            chat = await chats.add(Chat(id="act-1", ref=CHAT, project_id=PROJECT, title=question[:80],
                                        started_by="Rajat"))
        chat.status = "thinking"
        await chats.say(chat.id, role="you", body=question, by="Rajat")


async def turns_of(db: Database) -> list[ChatMessage]:
    async with db.read() as s:
        chat = await ChatRepository(s).by_ref(CHAT)
        assert chat is not None
        return await ChatRepository(s).messages(chat.id)


async def permit(db: Database, card: int, decision: str) -> None:
    async with db.session() as s:
        await ChatService(s, FakeGateway()).permit(CHAT, card, decision, ME)


async def rule(db: Database, tool: str, pattern: str, action: str) -> None:
    async with db.session() as s:
        s.add(ToolRule(project_id=PROJECT, tool=tool, pattern=pattern, action=action, note=""))


def fetch(url: str) -> str:
    return json.dumps({"tool": "web_fetch", "arguments": {"url": url}, "why": "read the tax page"})


# ── the permission card ───────────────────────────────────────────
async def test_a_fetch_no_rule_covers_waits_for_a_person_and_resumes_once_allowed(live: Database, site: str):
    await start(live, "what does the tax page say?")
    gateway = FakeGateway(fetch(f"{site}/tax"))

    await think(live, gateway, CHAT, "Rajat", ME)

    turns = await turns_of(live)
    card = turns[-1]
    assert (card.role, card.tool, card.arguments["state"]) == ("tool", PERMISSION, "pending")
    assert card.arguments["subject"] == f"{site}/tax" and card.arguments["covers"] == f"every page on {site[7:]}"
    assert Page.hits == []                                   # nothing was fetched while it waits
    async with live.read() as s:
        chat = await ChatRepository(s).by_ref(CHAT)
        assert chat is not None and chat.status == "idle"
        with pytest.raises(Refused, match="waiting for you to allow or refuse"):
            await ChatService(s, gateway).ask(CHAT, "another question", "Rajat")

    await permit(live, card.id, "once")
    gateway.script = ['{"answer": "IGST applies across states."}']
    await think(live, gateway, CHAT, "Rajat", ME)

    turns = await turns_of(live)
    assert [m.tool or m.role for m in turns] == ["you", PERMISSION, "web_fetch", "assistant"]
    assert turns[1].arguments["state"] == "allowed" and turns[1].arguments["decidedBy"] == "Rajat"
    fetched = turns[2]
    assert fetched.ok is True and "IGST applies across states." in fetched.body and "Tax rules" in fetched.body
    assert Page.hits == ["/tax"]
    # The model was sent the page as the result of its call; the card itself is not replayed.
    sent = json.dumps(gateway.seen[-1])
    assert "Result of web_fetch" in sent and "A person refused" not in sent
    async with live.read() as s:
        said = (await s.execute(select(ActivityEvent.action).where(ActivityEvent.project_id == PROJECT))).scalars()
        assert {"Session tool call allowed", "Web page fetched"} <= set(said)


async def test_a_refused_call_is_told_to_the_model_and_nothing_is_sent(live: Database, site: str):
    await start(live, "read the tax page")
    gateway = FakeGateway(fetch(f"{site}/tax"))
    await think(live, gateway, CHAT, "Rajat", ME)
    card = (await turns_of(live))[-1]

    await permit(live, card.id, "refuse")
    gateway.script = ['{"answer": "I could not read it."}']
    await think(live, gateway, CHAT, "Rajat", ME)

    turns = await turns_of(live)
    assert [m.tool or m.role for m in turns] == ["you", PERMISSION, "assistant"]
    assert turns[1].arguments["state"] == "refused" and turns[1].ok is False
    assert "A person refused this call to web_fetch" in json.dumps(gateway.seen[-1])
    assert Page.hits == []


async def test_allowed_for_the_session_the_same_host_is_not_asked_about_again(live: Database, site: str):
    await start(live, "read the tax page")
    gateway = FakeGateway(fetch(f"{site}/tax"))
    await think(live, gateway, CHAT, "Rajat", ME)
    await permit(live, (await turns_of(live))[-1].id, "session")
    gateway.script = ['{"answer": "Read."}']
    await think(live, gateway, CHAT, "Rajat", ME)
    async with live.read() as s:
        chat = await ChatRepository(s).by_ref(CHAT)
        assert chat is not None
        assert [(g["tool"], g["subject"], g["by"]) for g in chat.grants] == [("web_fetch", site, "Rajat")]

    await start(live, "and the other page?")
    gateway.script = [fetch(f"{site}/other"), '{"answer": "Both read."}']
    await think(live, gateway, CHAT, "Rajat", ME)

    tail = (await turns_of(live))[-3:]
    assert [m.tool or m.role for m in tail] == ["you", "web_fetch", "assistant"]    # no second card
    assert Page.hits == ["/tax", "/other"]


async def test_a_deny_rule_answers_the_model_and_an_allow_rule_asks_nobody(live: Database, site: str):
    await rule(live, "web_fetch", f"{site}/secret*", "deny")
    await rule(live, "web_fetch", f"{site}/*", "allow")
    await start(live, "read both")
    gateway = FakeGateway(fetch(f"{site}/secret/plan"), fetch(f"{site}/tax"), '{"answer": "One of two."}')

    await think(live, gateway, CHAT, "Rajat", ME)

    turns = await turns_of(live)
    assert [m.tool or m.role for m in turns] == ["you", "web_fetch", "web_fetch", "assistant"]
    denied, allowed = turns[1], turns[2]
    assert denied.ok is False and denied.detail == "denied by a rule" and "denies web_fetch" in denied.body
    assert allowed.ok is True and Page.hits == ["/tax"]


async def test_web_search_is_not_offered_until_a_key_is_set(live: Database):
    await start(live, "search the web")
    gateway = FakeGateway('{"tool": "web_search", "arguments": {"query": "gst"}}', '{"answer": "No search."}')
    await think(live, gateway, CHAT, "Rajat", ME)
    refused = (await turns_of(live))[1]
    assert refused.ok is False and "no tool called 'web_search'" in refused.body
    assert "web_fetch" in refused.body                       # what it may call is named instead
    prompt = gateway.seen[0][0]["content"]
    assert "web_fetch" in prompt and "web_search {" not in prompt


async def test_a_picture_with_no_lane_that_reads_images_is_named_not_dropped(live: Database):
    async with live.session() as s:
        chat = await ChatRepository(s).add(Chat(id="act-1", ref=CHAT, project_id=PROJECT, title="p",
                                                started_by="Rajat", status="thinking"))
        f = ChatFile(chat_id=chat.id, name="screen.png", mime="image/png", bytes=4, sha1="x", data=b"\x89PNG")
        s.add(f)
        await s.flush()
        await ChatRepository(s).say(chat.id, role="you", body="what is on this screen?", by="Rajat",
                                    attachments=[{"kind": "upload", "ref": str(f.id), "name": "screen.png",
                                                  "image": True, "mime": "image/png"}])
    gateway = FakeGateway('{"answer": "I was not shown it."}')
    await think(live, gateway, CHAT, "Rajat", ME)

    turns = await turns_of(live)
    note = next(m for m in turns if m.role == "note")
    assert note.detail == "image not sent" and "screen.png was not sent" in note.body
    user = [m for m in gateway.seen[0] if m["role"] == "user"][-1]
    assert isinstance(user["content"], str) and "was not shown it" in user["content"]


# ── what the model is told ───────────────────────────────────────
def test_the_prompt_groups_mcp_tools_under_their_server_and_says_calls_may_wait():
    acting = Acting({"ledger": Offered("ledger", "Ledger", "stdio", "ask",
                                       (("read_ledger", "Read a ledger row"), ("post_entry", "Post an entry")))},
                    search=True)
    prompt = system_prompt("ERP", (), "", acting)
    assert 'mcp {"server"' in prompt and "web_search {" in prompt and "web_fetch {" in prompt
    assert "ledger (Ledger):\n  - read_ledger — Read a ledger row\n  - post_entry — Post an entry" in prompt
    assert "waits for a person" in prompt
    assert "mcp {" not in system_prompt("ERP", (), "", Acting({}, search=False))
    assert "Tools that act outside" not in system_prompt("ERP")          # no acting tools: as before


def test_only_models_whose_provider_documents_image_input_read_images():
    assert lanes.reads_images("gemini", "gemini-2.5-flash")
    # Z.ai's GLM-4.6V-Flash is in its Vision Models table and costs nothing: the first free model here
    # that reads a picture. GitHub Models, which used to hold this line, was retired on 2026-07-30.
    assert lanes.reads_images("zai", "glm-4.6v-flash")
    assert lanes.reads_images("openrouter", "qwen/qwen3.8-27b:free")
    assert lanes.reads_images("deepseek", "deepseek-flash")
    assert not lanes.reads_images("deepseek", "deepseek-v4-pro")
    assert not lanes.reads_images("groq", "openai/gpt-oss-120b")
    assert not lanes.reads_images("gemini", "an-admins-own-model")
    assert lanes.describe(lanes.BY_ID["gemini"])["vision"] is True


# ── MCP, in the rolled-back session ──────────────────────────────
SPEAKS = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "l"}}
    elif msg["method"] == "tools/call":
        result = {"content": [{"type": "text", "text": "row for " + json.dumps(msg["params"]["arguments"])}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''


@pytest_asyncio.fixture
async def ledger(session: AsyncSession, tmp_path: Path) -> tuple[AsyncSession, Person, Chat]:
    """The test workspace, an owner, a checked ledger server and a session on the ERP."""
    await load_workspace(session)
    identity = IdentityService(session)
    made = await identity.create("owner@example.com", "Rajat", "correct horse battery", ["owner"])
    who = await identity.whoami(await identity.start_session(made.id))
    assert who is not None
    script = tmp_path / "ledger.py"
    script.write_text(SPEAKS)
    session.add(McpServer(id="ledger", name="Ledger", transport="stdio", status="connected",
                          command=shlex.join([sys.executable, str(script)]), checked_at=utcnow(),
                          default_effect="ask", tools=[McpTool(name="read_ledger", description="Read a row")]))
    chat = await ChatService(session, FakeGateway()).start("erp", "Rajat")
    await session.flush()
    return session, who, chat


async def test_an_mcp_tool_asks_then_runs_through_the_registry_once_allowed(ledger):
    session, who, chat = ledger
    project = await session.get(Project, "erp")
    acting = await reach(session, FakeGateway(), who)
    assert list(acting.servers) == ["ledger"] and acting.names() == ("web_fetch", "mcp")
    call = Turn(tool="mcp", arguments={"server": "ledger", "tool": "read_ledger", "arguments": {"row": 7}})

    paused = await _tool_turn(session, FakeGateway(), chat, project, call, acting=acting, by="Rajat")
    card = (await ChatRepository(session).messages(chat.id))[-1]
    assert paused is True and card.arguments["subject"] == "ledger/read_ledger"

    paused = await _tool_turn(session, FakeGateway(), chat, project, call, acting=acting, allowed=True, by="Rajat")
    done = (await ChatRepository(session).messages(chat.id))[-1]
    assert paused is False and done.tool == "mcp" and done.ok is True
    assert done.body == 'row for {"row": 7}' and done.detail.startswith("ledger/read_ledger")


async def test_an_mcp_server_that_is_not_offered_is_refused_in_the_tools_own_turn(ledger):
    session, who, chat = ledger
    project = await session.get(Project, "erp")
    # Someone who may not launch a stdio server is not offered its tools at all.
    reader = Person(id=who.id, email=who.email, name=who.name, status="active", roles=("engineer",),
                    permissions=frozenset({"sessions:chat"}))
    assert (await reach(session, FakeGateway(), reader)).servers == {}
    acting = await reach(session, FakeGateway(), who)
    await _tool_turn(session, FakeGateway(), chat, project,
                     Turn(tool="mcp", arguments={"server": "ledger", "tool": "drop_table"}), acting=acting)
    said = (await ChatRepository(session).messages(chat.id))[-1]
    assert said.ok is False and "did not list a tool called drop_table" in said.body

    session.add(ToolRule(project_id="erp", tool="mcp", pattern="ledger/*", action="deny", note="never"))
    await session.flush()
    await _tool_turn(session, FakeGateway(), chat, project,
                     Turn(tool="mcp", arguments={"server": "ledger", "tool": "read_ledger"}), acting=acting)
    said = (await ChatRepository(session).messages(chat.id))[-1]
    assert said.detail == "denied by a rule" and "(never)" in said.body


async def test_the_question_being_answered_carries_its_picture_to_a_lane_that_sees(ledger):
    session, _, chat = ledger
    f = ChatFile(chat_id=chat.id, name="a.png", mime="image/png", bytes=3, sha1="x", data=b"PNG")
    session.add(f)
    await session.flush()
    await ChatRepository(session).say(chat.id, role="you", body="what is this?", by="Rajat",
                                      attachments=[{"kind": "upload", "ref": str(f.id), "name": "a.png",
                                                    "image": True}])
    seen = await _wire(session, chat, "ERP", sees=True)
    parts = seen[-1]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,UE5H"}}
    blind = await _wire(session, chat, "ERP", sees=False)
    assert isinstance(blind[-1]["content"], str) and "a.png" in blind[-1]["content"]
