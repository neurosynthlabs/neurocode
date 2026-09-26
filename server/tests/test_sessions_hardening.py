"""Sessions, held to the rules an adversarial review once found broken.

**The fence.** Every route that changes or copies a session reads it first — its turns go to a model, into
a copy, into a plan — so each stops where GET does: 404 for a session of a restricted project somebody is
not listed on, 403 for someone else's without `sessions:read`, and the rights it needs weighed inside the
project. A project this one references is read into a session only for someone who may see it.

**The gate.** A `read` rule is weighed on the file the reader opens, not on how the path was spelled, and
in the project that file belongs to. A `web_fetch` rule is weighed on the host in any letters, and a
redirect stops at a host no rule and no person allowed. A permission card is answered once and resumed
once, and Compact waits for it. A custom tool a person allowed is logged as theirs.

**A long session.** The model is sent the newest turns and the summary, however many came before, and a
tool rule is read however many were written before it.

The acting half runs as in test_session_act.py: real commits on the test database, a stand-in model, and
pages served from this machine that count as the internet for the length of a test.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Provider, Result
from app.api import deps, routes_sessions
from app.api.app import create_api
from app.data.engine import Database
from app.models import Chat, ChatMessage, Project, ToolRule
from app.repositories import ChatRepository
from app.services import chat as chat_service
from app.services import machine
from app.services import mcp as mcp_service
from app.services.chat import PERMISSION, Acting, ChatService, Turn, _gate, _tool_turn, _wire, think
from app.services.custom_tools import checked_spec
from app.services.errors import Refused
from app.services.gates import literal_pattern
from app.services.identity import Person
from app.services.tool_rules import decide
from app.settings import settings as real_settings
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PASSWORD = "a long enough password"
CLOSED, OPEN = "hims", "erp"
PROJECT, CHAT = "hard-gate-project", "CHAT-7721"
ME = Person(id="u-hard-gate", email="hard@example.com", name="Rajat", status="active", roles=("owner",),
            permissions=frozenset({"sessions:chat"}))
SECRET = "the-token-value-42"


# ── the HTTP half: two people, one app ────────────────────────────
@pytest_asyncio.fixture
async def api(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    await load_workspace(session)
    built = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    async def no_answer(*_: Any) -> None:          # the background answer is not what is under test
        return None

    built.dependency_overrides[deps.session] = use_the_test_session
    monkeypatch.setattr(routes_sessions, "_answer", no_answer)
    return built


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@asynccontextmanager
async def signed_in(api: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        assert (await c.post("/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200
        yield c


async def add(client: AsyncClient, email: str, name: str, roles: list[str]) -> dict[str, Any]:
    made = await client.post("/admin/users", json={"email": email, "name": name, "password": PASSWORD,
                                                   "roles": roles})
    assert made.status_code == 201, made.text
    return made.json()


async def role_with(client: AsyncClient, name: str, permissions: list[str]) -> str:
    made = await client.post("/admin/roles", json={"name": name, "permissions": permissions})
    assert made.status_code == 201, made.text
    return made.json()["id"]


@pytest_asyncio.fixture
async def closed_session(api: FastAPI, client: AsyncClient, session: AsyncSession) -> dict[str, Any]:
    """A session in the restricted project, waiting on a permission card; an Engineer who is not listed."""
    await add(client, "b@example.com", "Outsider", ["engineer"])
    project = await session.get(m.Project, CLOSED)
    assert project is not None
    project.restricted = True
    chat = m.Chat(id="hard-closed", ref="CHAT-7701", project_id=CLOSED, title="closed", started_by="Rajat")
    session.add(chat)
    await session.flush()
    card = m.ChatMessage(chat_id=chat.id, role="tool", tool=PERMISSION, body="web_fetch wants to act",
                         arguments={"tool": "web_fetch", "subject": "https://example.com/", "input": {},
                                    "grant": "https://example.com", "covers": "every page", "state": "pending"})
    session.add(card)
    await session.flush()
    return {"ref": chat.ref, "card": card.id}


async def referencing(session: AsyncSession, tmp_path: Path) -> None:
    """Open project A references restricted project B; each has a checkout with a file of its own."""
    for pid in ("hard-ref-a", "hard-ref-b"):
        root = tmp_path / pid
        (root / "secrets").mkdir(parents=True)
        (root / "secrets" / "token.txt").write_text(f"{pid}-only\n")
        session.add(m.Project(id=pid, name=pid, source_kind="local", source_repo=str(root),
                              restricted=pid == "hard-ref-b"))
    await session.flush()
    session.add(m.ProjectReference(project_id="hard-ref-a", referenced_id="hard-ref-b"))
    session.add(m.CodeFile(project_id="hard-ref-b", path="secrets/token.txt", lang="text", lines=1))
    await session.flush()


# ── 1. every session route stops at the fence GET stops at ───────
async def test_a_session_cannot_be_started_in_a_project_you_cannot_see(api: FastAPI, closed_session: dict):
    async with signed_in(api, "b@example.com") as outsider:
        assert (await outsider.get(f"/projects/{CLOSED}")).status_code == 404     # the fence, as it stands
        made = await outsider.post("/sessions", json={"projectId": CLOSED})
        imported = await outsider.post("/sessions/import", json={"projectId": CLOSED, "document": {}})
    assert made.status_code == 404, made.text
    assert imported.status_code == 404, imported.text


async def test_a_permission_card_in_a_project_you_cannot_see_is_not_yours_to_answer(
        api: FastAPI, closed_session: dict, session: AsyncSession):
    ref, card = closed_session["ref"], closed_session["card"]
    async with signed_in(api, "b@example.com") as outsider:
        assert (await outsider.get(f"/sessions/{ref}")).status_code == 404       # cannot read it
        answered = await outsider.post(f"/sessions/{ref}/permissions/{card}", json={"decision": "session"})
    assert answered.status_code == 404, answered.text
    row = await session.get(m.ChatMessage, card)
    assert row is not None and (row.arguments or {}).get("state") == "pending"


async def test_a_session_in_a_project_you_cannot_see_cannot_be_asked_stopped_compacted_or_forked(
        api: FastAPI, closed_session: dict):
    ref, card = closed_session["ref"], closed_session["card"]
    async with signed_in(api, "b@example.com") as outsider:
        assert (await outsider.post(f"/sessions/{ref}/cancel")).status_code == 404
        assert (await outsider.post(f"/sessions/{ref}/fork", json={"at": card})).status_code == 404
        assert (await outsider.post(f"/sessions/{ref}/messages", json={"text": "hello?"})).status_code == 404
        assert (await outsider.post(f"/sessions/{ref}/compact")).status_code == 404
        assert (await outsider.post(f"/sessions/{ref}/to-plan")).status_code == 404
        assert (await outsider.post(f"/sessions/{ref}/files", json={"name": "a.txt", "mime": "text/plain",
                                                                     "data": "aGk="})).status_code == 404


async def test_forking_someone_elses_session_needs_the_right_reading_it_does(
        api: FastAPI, client: AsyncClient, session: AsyncSession):
    """Dev holds `sessions:chat` only. GET on Rajat's session is 403; a fork must not hand Dev a copy."""
    mine_only = await role_with(client, "Contractor", ["sessions:chat"])
    await add(client, "dev@example.com", "Dev", [mine_only])
    chat = m.Chat(id="hard-rajat", ref="CHAT-7702", project_id=OPEN, title="private", started_by="Rajat")
    session.add(chat)
    await session.flush()
    said = m.ChatMessage(chat_id=chat.id, role="you", body="the private question", by="Rajat")
    session.add(said)
    await session.flush()

    async with signed_in(api, "dev@example.com") as dev:
        assert (await dev.get(f"/sessions/{chat.ref}")).status_code == 403
        forked = await dev.post(f"/sessions/{chat.ref}/fork", json={"at": said.id})
        asked = await dev.post(f"/sessions/{chat.ref}/messages", json={"text": "and what did you say?"})
    assert forked.status_code == 403, forked.text
    assert asked.status_code == 403, asked.text


async def test_a_grant_that_leaves_out_asking_leaves_out_starting_a_session_there(
        api: FastAPI, client: AsyncClient, session: AsyncSession):
    """Listed in the restricted project as a Viewer, an Engineer sees it — and asks only where they may."""
    made = await add(client, "listed@example.com", "Listed", ["engineer"])
    project = await session.get(m.Project, CLOSED)
    assert project is not None
    project.restricted = True
    session.add(m.ProjectRole(project_id=CLOSED, user_id=made["id"], role_id="viewer"))
    await session.flush()

    async with signed_in(api, "listed@example.com") as listed:
        assert (await listed.get(f"/projects/{CLOSED}")).status_code == 200
        inside = await listed.post("/sessions", json={"projectId": CLOSED})
        elsewhere = await listed.post("/sessions", json={"projectId": OPEN})
    assert inside.status_code == 403 and "sessions:chat" in inside.text, inside.text
    assert elsewhere.status_code == 201, elsewhere.text


async def test_a_referenced_project_you_cannot_see_is_not_read_into_your_session(
        api: FastAPI, client: AsyncClient, session: AsyncSession, tmp_path: Path):
    """Somebody not listed in B asks in A with B's file attached (the composer's `<id>:path` form, which
    read_file also takes from a model)."""
    await add(client, "b@example.com", "Outsider", ["engineer"])
    await referencing(session, tmp_path)

    async with signed_in(api, "b@example.com") as outsider:
        assert (await outsider.get("/projects/hard-ref-b")).status_code == 404       # B is not there for them
        made = await outsider.post("/sessions", json={"projectId": "hard-ref-a"})
        assert made.status_code == 201, made.text
        ref = made.json()["ref"]
        asked = await outsider.post(f"/sessions/{ref}/messages", json={
            "text": "what is in this file?",
            "attachments": [{"kind": "file", "ref": "hard-ref-b:secrets/token.txt"}]})
        shown = await outsider.get(f"/sessions/{ref}")
    assert "hard-ref-b-only" not in shown.text, "B's file was read into a session of someone B is closed to"
    assert asked.status_code == 404, asked.text
    assert "references" not in asked.text                  # not refused as a project: it is not there


async def test_the_composer_does_not_offer_a_referenced_project_you_cannot_see(
        api: FastAPI, client: AsyncClient, session: AsyncSession, tmp_path: Path):
    await add(client, "b@example.com", "Outsider", ["engineer"])
    await referencing(session, tmp_path)

    offered = (await client.get("/projects/hard-ref-a/mentions")).json()["items"]
    assert "hard-ref-b:secrets/token.txt" in [i["ref"] for i in offered]            # the owner sees it
    async with signed_in(api, "b@example.com") as outsider:
        found = (await outsider.get("/projects/hard-ref-a/mentions")).json()["items"]
    assert not [i for i in found if i["ref"].startswith("hard-ref-b:")], found


# ── 2. a `read` rule holds for every spelling of the path ─────────
class NoModel:
    """Nothing here calls a model; the tools only need something to hold."""

    def embed_lane(self) -> None:
        return None


def checkout(root: Path) -> Path:
    (root / "secrets").mkdir(parents=True)
    (root / "secrets" / "token.txt").write_text(SECRET + "\n")
    (root / "app.py").write_text("print('hi')\n")
    return root


async def reader(session: AsyncSession, tmp_path: Path, *, reference: bool = False) -> tuple[m.Chat, m.Project]:
    here = m.Project(id="hard-read-a", name="Reader A", source_kind="local",
                     source_repo=str(checkout(tmp_path / "a")))
    session.add(here)
    if reference:
        session.add(m.Project(id="hard-read-b", name="Reader B", source_kind="local",
                              source_repo=str(checkout(tmp_path / "b"))))
    await session.flush()
    if reference:
        session.add(m.ProjectReference(project_id="hard-read-a", referenced_id="hard-read-b"))
    chat = m.Chat(id="hard-read-chat", ref="CHAT-7711", project_id="hard-read-a", title="t", started_by="Rajat")
    session.add(chat)
    await session.flush()
    return chat, here


async def read(session: AsyncSession, chat: m.Chat, project: m.Project, path: str) -> m.ChatMessage:
    await _tool_turn(session, NoModel(), chat, project,  # type: ignore[arg-type]
                     Turn(tool="read_file", arguments={"path": path}))
    return (await ChatRepository(session).messages(chat.id))[-1]


@pytest.mark.parametrize("spelling", ["secrets\\token.txt", ".//secrets/token.txt", "secrets/./token.txt"])
async def test_a_denied_path_is_denied_however_it_is_spelled(session: AsyncSession, tmp_path: Path,
                                                             spelling: str):
    chat, project = await reader(session, tmp_path)
    session.add(m.ToolRule(project_id="hard-read-a", tool="read", pattern="secrets/*", action="deny", note=""))
    await session.flush()

    plain = await read(session, chat, project, "secrets/token.txt")
    assert plain.detail == "denied by a rule" and SECRET not in plain.body      # the rule works as written

    other = await read(session, chat, project, spelling)
    assert SECRET not in other.body, f"{spelling!r} read the denied file: {other.body[:120]!r}"
    assert other.ok is False and other.detail == "denied by a rule"
    assert "app.py" in (await read(session, chat, project, "app.py")).body         # the rest still reads


async def test_a_workspace_deny_holds_for_a_referenced_projects_file(session: AsyncSession, tmp_path: Path):
    chat, project = await reader(session, tmp_path, reference=True)
    session.add(m.ToolRule(project_id=None, tool="read", pattern="secrets/*", action="deny", note=""))
    await session.flush()

    assert SECRET not in (await read(session, chat, project, "secrets/token.txt")).body
    other = await read(session, chat, project, "hard-read-b:secrets/token.txt")
    assert SECRET not in other.body, f"the referenced copy was read: {other.body[:120]!r}"
    assert other.detail == "denied by a rule"


async def test_a_read_that_asks_is_carded_once_in_one_spelling(session: AsyncSession, tmp_path: Path):
    """The card and the grant carry the path as the reader opens it, so "Allow for this session" answers
    every spelling of that file — and a referenced project's file is named with its prefix."""
    chat, project = await reader(session, tmp_path, reference=True)
    session.add(m.ToolRule(project_id=None, tool="read", pattern="secrets/*", action="ask", note=""))
    await session.flush()

    await read(session, chat, project, ".\\secrets\\token.txt")
    card = (await ChatRepository(session).messages(chat.id))[-1]
    assert card.tool == PERMISSION and card.arguments["subject"] == "secrets/token.txt"
    await read(session, chat, project, "hard-read-b:secrets//token.txt")
    other = (await ChatRepository(session).messages(chat.id))[-1]
    assert other.arguments["grant"] == "hard-read-b:secrets/token.txt"


# ── the acting half: a live loop, a stand-in model, local pages ──
class FakeGateway:
    def __init__(self, *script: str) -> None:
        self.script = list(script)

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, Any]], parse: Any, **kwargs: Any) -> Result[Any]:
        raw = self.script.pop(0) if self.script else '{"answer": "Done."}'
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 12)


def serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class Landing(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        Landing.hits.append(self.path)
        body = b"<html><body><p>landed</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        return


class Bouncer(BaseHTTPRequestHandler):
    to = ""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(302)
        self.send_header("Location", Bouncer.to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_: Any) -> None:
        return


@pytest.fixture
def sites(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    """(a page that redirects, the page it redirects to) — both on this machine, both 'public' here."""
    Landing.hits = []
    landing, landing_url = serve(Landing)
    Bouncer.to = f"{landing_url}/elsewhere"
    bouncer, bouncer_url = serve(Bouncer)
    real = mcp_service._public
    monkeypatch.setattr(mcp_service, "_public", lambda ip: ip == ipaddress.ip_address("127.0.0.1") or real(ip))
    yield bouncer_url, landing_url
    for s in (landing, bouncer):
        s.shutdown()
        s.server_close()


@pytest.fixture(autouse=True)
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NEUROCODE_CLAUDE_HOME", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def live(schema: str) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Hard Gate"))
    yield db
    async with db.session() as s:
        await s.execute(delete(Chat).where(Chat.project_id == PROJECT))
        await s.execute(delete(ToolRule).where(ToolRule.project_id == PROJECT))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


async def start(db: Database, question: str, grants: list[dict[str, Any]] | None = None) -> None:
    async with db.session() as s:
        chats = ChatRepository(s)
        chat = await chats.by_ref(CHAT)
        if chat is None:
            chat = await chats.add(Chat(id="hard-gate-1", ref=CHAT, project_id=PROJECT, title="t",
                                        started_by="Rajat"))
        chat.status = "thinking"
        if grants is not None:
            chat.grants = grants
        await chats.say(chat.id, role="you", body=question, by="Rajat")


async def turns_of(db: Database) -> list[ChatMessage]:
    async with db.read() as s:
        chat = await ChatRepository(s).by_ref(CHAT)
        assert chat is not None
        return await ChatRepository(s).messages(chat.id)


def fetch(url: str) -> str:
    return json.dumps({"tool": "web_fetch", "arguments": {"url": url}})


async def card(live: Database, site: str) -> int:
    await start(live, "read the page")
    await think(live, FakeGateway(fetch(f"{site}/tax")), CHAT, "Rajat", ME)
    last = (await turns_of(live))[-1]
    assert last.tool == PERMISSION and last.arguments["state"] == "pending"
    return last.id


# ── 3. a web_fetch rule is weighed on the host in any letters ─────
async def test_a_denied_host_is_denied_whatever_case_it_is_written_in(session: AsyncSession):
    session.add(m.Project(id="hard-case", name="Case"))
    await session.flush()
    chat = m.Chat(id="hard-case-chat", ref="CHAT-7722", project_id="hard-case", title="t", started_by="Rajat")
    session.add_all([chat,
                     m.ToolRule(project_id="hard-case", tool="web_fetch", pattern="https://*", action="allow"),
                     m.ToolRule(project_id="hard-case", tool="web_fetch", pattern="https://blocked.example/*",
                                action="deny")])
    await session.flush()
    acting = Acting(servers={}, search=False)

    for url in ("https://blocked.example/page", "https://Blocked.Example/page", "HTTPS://BLOCKED.EXAMPLE./page",
                "https://blocked.example:443/page"):
        gate = await _gate(session, chat, "web_fetch", {"url": url}, acting)
        assert gate is not None and gate.action == "deny", (url, gate)
    other = await _gate(session, chat, "web_fetch", {"url": "https://fine.example/Page"}, acting)
    assert other is not None and other.action == "allow" and other.subject == "https://fine.example/Page"


async def test_a_rule_written_with_a_capitalised_host_still_denies_that_host(session: AsyncSession):
    """A rule stored before hosts were matched in lower case still means the host it names."""
    session.add(m.Project(id="hard-caps", name="Caps"))
    await session.flush()
    session.add(m.ToolRule(project_id="hard-caps", tool="web_fetch", pattern="https://Blocked.Example/*",
                           action="deny"))
    await session.flush()
    assert (await decide(session, "web_fetch", "https://blocked.example/x", "hard-caps")).action == "deny"


# ── 4. a redirect stops at a host nobody allowed ──────────────────
async def test_an_allowed_page_does_not_carry_the_fetch_to_a_host_nobody_allowed(
        live: Database, sites: tuple[str, str]):
    bouncer, landing = sites
    async with live.session() as s:
        s.add(ToolRule(project_id=PROJECT, tool="web_fetch", pattern=f"{bouncer}/*", action="allow", note=""))
    await start(live, "read the page")
    await think(live, FakeGateway(fetch(f"{bouncer}/start"), '{"answer": "Read."}'), CHAT, "Rajat", ME)

    assert Landing.hits == [], "the redirect reached a host no rule allowed and no person was asked about"
    stopped = next(t for t in await turns_of(live) if t.tool == "web_fetch")
    assert stopped.ok is False and f"It redirected to {landing}/elsewhere" in stopped.body
    assert "as a call of its own" in stopped.body          # the model is told how to ask for it


async def test_a_redirect_to_a_host_this_session_allowed_is_followed(live: Database, sites: tuple[str, str]):
    bouncer, landing = sites
    async with live.session() as s:
        s.add(ToolRule(project_id=PROJECT, tool="web_fetch", pattern=f"{bouncer}/*", action="allow", note=""))
    await start(live, "read the page", grants=[{"tool": "web_fetch", "subject": landing, "by": "Rajat"}])
    await think(live, FakeGateway(fetch(f"{bouncer}/start"), '{"answer": "Read."}'), CHAT, "Rajat", ME)

    assert Landing.hits == ["/elsewhere"]


# ── 5. one card, answered once and resumed once ───────────────────
async def test_a_permission_card_is_answered_once_even_when_two_answers_arrive_together(
        live: Database, sites: tuple[str, str], monkeypatch: pytest.MonkeyPatch):
    _, landing = sites
    card_id = await card(live, landing)

    real = chat_service.pending_permission
    both_read = asyncio.Barrier(2)

    async def read_then_wait(session: AsyncSession, chat_id: str) -> ChatMessage | None:
        found = await real(session, chat_id)
        await both_read.wait()                   # a double click: both requests have read the card
        return found

    monkeypatch.setattr(chat_service, "pending_permission", read_then_wait)

    async def answer() -> str:
        try:
            async with live.session() as s:
                await ChatService(s, FakeGateway()).permit(CHAT, card_id, "once", ME)
            return "allowed"
        except Refused:
            return "refused"

    outcomes = await asyncio.wait_for(asyncio.gather(answer(), answer()), timeout=30)
    assert sorted(outcomes) == ["allowed", "refused"], outcomes


async def test_an_allowed_call_resumed_twice_at_once_runs_once(live: Database, sites: tuple[str, str]):
    """What two accepted answers would hand off: two answers resuming the same allowed card together."""
    _, landing = sites
    card_id = await card(live, landing)
    async with live.session() as s:
        await ChatService(s, FakeGateway()).permit(CHAT, card_id, "once", ME)

    await asyncio.wait_for(asyncio.gather(think(live, FakeGateway(), CHAT, "Rajat", ME),
                                          think(live, FakeGateway(), CHAT, "Rajat", ME)), timeout=60)
    assert Landing.hits == ["/tax"], Landing.hits
    turns = await turns_of(live)
    answers = [t for t in turns if t.role == "assistant"]
    assert len(answers) == 1, [t.body for t in answers]           # one question, one answer
    taken = next(t for t in turns if t.id == card_id)
    assert taken.arguments["state"] == "allowed" and taken.arguments["resumed"] is True


async def test_compact_waits_for_a_permission_card_and_the_allowed_call_still_runs(
        live: Database, sites: tuple[str, str]):
    """Compact while a card waits would make its summary the newest turn, and the call a person then
    allowed would have nothing to resume from. It is refused until the card is answered."""
    _, landing = sites
    async with live.session() as s:
        chat = await ChatRepository(s).add(Chat(id="hard-gate-1", ref=CHAT, project_id=PROJECT, title="t",
                                                started_by="Rajat"))
        for n in range(10):
            await ChatRepository(s).say(chat.id, role="you" if n % 2 == 0 else "assistant", body=f"earlier {n}")
    card_id = await card(live, landing)

    with pytest.raises(Refused, match="waiting for you to allow or refuse"):
        async with live.session() as s:
            await ChatService(s, FakeGateway('{"summary": "earlier turns"}')).compact(CHAT, "Rajat")
    async with live.session() as s:
        await ChatService(s, FakeGateway()).permit(CHAT, card_id, "once", ME)
    await think(live, FakeGateway('{"answer": "Read."}'), CHAT, "Rajat", ME)

    assert Landing.hits == ["/tax"], "the call the person allowed was never made"


# ── 6. a custom tool a person allowed is logged as theirs ─────────
async def test_a_custom_tool_a_person_allowed_is_logged_as_theirs(
        live: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "checkout"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts" / "say.sh"
    script.write_text("#!/bin/sh\necho \"built $1\"\n")
    script.chmod(0o755)
    configured = real_settings().model_copy(update={"machine_roots": str(tmp_path), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    spec = checked_spec("command", {"argv": ["./scripts/say.sh", "{what}"],
                                    "arguments": {"type": "object", "properties": {"what": {"type": "string"}},
                                                  "required": ["what"]}})
    async with live.session() as s:
        project = await s.get(Project, PROJECT)
        assert project is not None
        project.source_kind, project.source_repo = "local", str(repo)
        s.add(m.CustomTool(id="ct-hard", project_id=PROJECT, name="build", kind="command", spec=spec))
        s.add(ToolRule(project_id=PROJECT, tool="tool", pattern="build", action="ask", note=""))

    await start(live, "build it")
    call = json.dumps({"tool": "custom_tool", "arguments": {"name": "build", "arguments": {"what": "the app"}}})
    await think(live, FakeGateway(call), CHAT, "Rajat", ME)
    waiting = (await turns_of(live))[-1]
    assert waiting.tool == PERMISSION and waiting.arguments["state"] == "pending"
    async with live.session() as s:
        await ChatService(s, FakeGateway()).permit(CHAT, waiting.id, "once", ME)
    await think(live, FakeGateway('{"answer": "Built."}'), CHAT, "Rajat", ME)

    async with live.read() as s:                  # the project's activity goes with it when `live` ends
        logged = (await s.execute(select(m.ActivityEvent.detail).where(
            m.ActivityEvent.project_id == PROJECT, m.ActivityEvent.action == "Custom tool called"))).scalars().all()
    assert len(logged) == 1 and "allowed once by Rajat" in logged[0], logged
    assert "allowed by a tool rule" not in logged[0]


# ── 7. a long session, and a long list of rules ──────────────────
async def test_the_newest_question_is_sent_however_long_the_session_is(session: AsyncSession):
    session.add(m.Project(id="hard-long", name="Long"))
    await session.flush()
    chat = m.Chat(id="hard-long-chat", ref="CHAT-7731", project_id="hard-long", title="t", started_by="Rajat")
    session.add(chat)
    await session.flush()
    # 520 earlier turns, every one already folded into a summary — exactly what "Compact" leaves behind.
    await session.execute(insert(m.ChatMessage), [
        {"chat_id": chat.id, "role": "you" if n % 2 == 0 else "assistant", "body": f"old turn {n}",
         "compacted": True} for n in range(520)])
    session.add(m.ChatMessage(chat_id=chat.id, role="summary", body="the summary of 520 turns"))
    session.add(m.ChatMessage(chat_id=chat.id, role="you", body="THE NEW QUESTION"))
    await session.flush()

    sent = json.dumps(await _wire(session, chat, "Long"))
    assert "THE NEW QUESTION" in sent, "the model was not sent the question it is answering"
    assert "the summary of 520 turns" in sent and "old turn" not in sent


async def test_regenerate_finds_its_question_however_long_the_session_is(session: AsyncSession):
    session.add(m.Project(id="hard-regen", name="Regen"))
    await session.flush()
    chat = m.Chat(id="hard-regen-chat", ref="CHAT-7732", project_id="hard-regen", title="t", started_by="Rajat")
    session.add(chat)
    await session.flush()
    await session.execute(insert(m.ChatMessage), [
        {"chat_id": chat.id, "role": "you" if n % 2 == 0 else "assistant", "body": f"turn {n}"}
        for n in range(1_100)])
    await session.flush()
    last = (await ChatRepository(session).tail(chat.id, 1))[-1]
    assert last.role == "assistant" and last.body == "turn 1099"

    out = await ChatService(session, FakeGateway()).regenerate(chat.ref, last.id, "Rajat")  # type: ignore[arg-type]
    assert out["message"].body == "turn 1098"          # the question just before it, not one from the start


async def test_a_deny_written_after_five_hundred_allows_still_denies(session: AsyncSession):
    session.add(m.Project(id="hard-cap", name="Cap"))
    await session.flush()
    # What 25 "Always allow in this project" answers of 20 paths each leave behind.
    await session.execute(insert(m.ToolRule), [
        {"project_id": "hard-cap", "tool": "edit", "pattern": literal_pattern(f"src/file_{n}.py"),
         "action": "allow", "note": ""} for n in range(500)])
    session.add(m.ToolRule(project_id="hard-cap", tool="edit", pattern="src/*", action="deny",
                           note="nobody edits src/ until the audit is done"))
    await session.flush()

    decision = await decide(session, "edit", "src/payments.py", "hard-cap")
    assert decision.action == "deny", decision.why
