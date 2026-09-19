"""Shaping a session over HTTP: permission answers, edits and regenerations, forks, export and import,
"Make this a plan", uploads and the composer's `@` mentions.

The answering loop is replaced by a stand-in that only records it was handed the session — it has tests
of its own (test_chat_loop.py, test_session_act.py) — so what is checked here is what each route writes
and refuses. A test that needs a model's answer gets a scripted lane (`answering`); nothing leaves the
machine.
"""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.models import ActivityEvent, Chat, ChatMessage, CodeFile, CodeSymbol
from app.repositories import ChatRepository
from app.secrets import Secrets
from app.services import chat as chat_service
from app.services.chat import PERMISSION
from app.services.identity import IdentityService
from tests.fixtures.lanes import PLAN, answering
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    api = create_api(db=None)
    handed: list[str] = []

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    async def recorded(_db: object, _gw: object, ref: str, *_: object) -> None:
        handed.append(ref)

    held = Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))
    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: held
    monkeypatch.setattr(chat_service, "think", recorded)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        c.handed = handed  # type: ignore[attr-defined]
        yield c


async def headers_for(session: AsyncSession, role: str) -> dict[str, str]:
    identity = IdentityService(session)
    person = await identity.create(f"{role}@example.com", role.title(), "correct horse battery", [role])
    return {**HEADERS, "Authorization": f"Bearer {await identity.start_session(person.id)}"}


async def new_session(client: AsyncClient, project: str = "erp") -> str:
    return (await client.post("/sessions", json={"projectId": project})).json()["ref"]


async def chat_of(session: AsyncSession, ref: str) -> Chat:
    chat = await ChatRepository(session).by_ref(ref)
    assert chat is not None
    return chat


async def answered(client: AsyncClient, session: AsyncSession, ref: str, question: str, answer: str) -> tuple[int, int]:
    """Ask, then write the answer the model would have written; the chat goes back to idle."""
    asked = (await client.post(f"/sessions/{ref}/messages", json={"text": question})).json()["message"]
    chat = await chat_of(session, ref)
    said = await ChatRepository(session).say(chat.id, role="assistant", body=answer, lane="groq")
    chat.status = "idle"
    await session.flush()
    return asked["id"], said.id


async def upload(client: AsyncClient, ref: str, name: str, mime: str, data: bytes) -> Any:
    return await client.post(f"/sessions/{ref}/files",
                             json={"name": name, "mime": mime, "data": base64.b64encode(data).decode()})


# ── a permission card, answered ──────────────────────────────────
async def card(session: AsyncSession, ref: str) -> ChatMessage:
    chat = await chat_of(session, ref)
    return await ChatRepository(session).say(
        chat.id, role="tool", tool=PERMISSION, body="web_fetch wants to act on https://example.com/a.",
        detail="waiting for a person", arguments={"tool": "web_fetch", "subject": "https://example.com/a",
                                                  "input": {"url": "https://example.com/a"}, "why": "No rule.",
                                                  "ruleId": None, "grant": "https://example.com",
                                                  "covers": "every page on example.com", "state": "pending"})


async def test_answering_a_card_records_it_resumes_and_keeps_a_session_grant(client: AsyncClient,
                                                                             session: AsyncSession):
    ref = await new_session(client)
    waiting = await card(session, ref)
    listed = (await client.get("/sessions")).json()
    assert listed[0]["waitingOn"] == {"messageId": waiting.id, "tool": "web_fetch",
                                      "subject": "https://example.com/a"}
    detail = (await client.get(f"/sessions/{ref}")).json()
    shown = detail["messages"][-1]
    assert shown["permission"]["state"] == "pending" and shown["arguments"] == {"url": "https://example.com/a"}

    # Nothing else moves while it waits: a question is refused in words, and a wrong card is 409.
    asked = await client.post(f"/sessions/{ref}/messages", json={"text": "and then?"})
    assert asked.status_code == 409 and "allow or refuse" in asked.json()["detail"]
    assert (await client.post(f"/sessions/{ref}/permissions/{waiting.id + 999}",
                              json={"decision": "once"})).status_code == 409
    assert (await client.post(f"/sessions/{ref}/permissions/{waiting.id}",
                              json={"decision": "always"})).status_code == 422

    done = await client.post(f"/sessions/{ref}/permissions/{waiting.id}", json={"decision": "session"})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["waitingOn"] is None and body["status"] == "thinking"
    assert [(g["tool"], g["subject"], g["by"]) for g in body["grants"]] == [("web_fetch", "https://example.com",
                                                                             "Rajat")]
    assert client.handed == [ref]  # type: ignore[attr-defined]
    after = (await client.get(f"/sessions/{ref}")).json()["messages"][-1]
    assert after["permission"]["state"] == "allowed" and after["permission"]["scope"] == "session"
    assert after["permission"]["decidedBy"] == "Rajat" and after["detail"] == "allowed for this session"
    logged = (await session.execute(select(ActivityEvent.detail).where(
        ActivityEvent.action == "Session tool call allowed"))).scalars().all()
    assert logged and logged[0].startswith(f"{ref} · web_fetch https://example.com/a")
    # Answered once, it is answered: a second answer is refused.
    assert (await client.post(f"/sessions/{ref}/permissions/{waiting.id}",
                              json={"decision": "refuse"})).status_code == 409


# ── edit, regenerate and the active line ─────────────────────────
async def test_an_edited_question_replaces_its_line_and_the_model_is_sent_only_the_new_one(
        client: AsyncClient, session: AsyncSession):
    ref = await new_session(client)
    q1, a1 = await answered(client, session, ref, "where is tax split?", "In tax.py.")
    q2, a2 = await answered(client, session, ref, "and rounding?", "In money.py.")

    edited = await client.post(f"/sessions/{ref}/messages/{q2}/edit", json={"text": "and rounding of GST?"})
    assert edited.status_code == 201, edited.text
    fresh = edited.json()["message"]
    assert fresh["text"] == "and rounding of GST?" and fresh["edited"] == q2

    turns = {m["id"]: m for m in (await client.get(f"/sessions/{ref}")).json()["messages"]}
    assert turns[q2]["supersededBy"] == fresh["id"] and turns[a2]["supersededBy"] == fresh["id"]
    assert "supersededBy" not in turns[q1] and "supersededBy" not in turns[a1]
    wire = await chat_service._wire(session, await chat_of(session, ref), "ERP")
    said = [m["content"] for m in wire[1:]]
    assert said == ["where is tax split?", '{"answer": "In tax.py."}', "and rounding of GST?"]

    # A replaced turn cannot be edited again, and an answer is not a question.
    chat = await chat_of(session, ref)
    chat.status = "idle"
    await session.flush()
    again = await client.post(f"/sessions/{ref}/messages/{q2}/edit", json={"text": "x"})
    assert again.status_code == 409 and "already replaced" in again.json()["detail"]
    assert (await client.post(f"/sessions/{ref}/messages/{a1}/edit", json={"text": "x"})).status_code == 404


async def test_regenerating_asks_the_same_question_again_and_a_lane_must_be_open(client: AsyncClient,
                                                                                session: AsyncSession,
                                                                                monkeypatch: pytest.MonkeyPatch):
    ref = await new_session(client)
    q1, a1 = await answered(client, session, ref, "where is tax split?", "In tax.py.")
    assert (await client.post(f"/sessions/{ref}/messages/{a1}/regenerate",
                              json={"lane": "nowhere"})).status_code == 422
    closed = await client.post(f"/sessions/{ref}/messages/{a1}/regenerate", json={"lane": "gemini"})
    assert closed.status_code == 409 and "Google Gemini cannot answer now" in closed.json()["detail"]

    answering(monkeypatch)                                   # Groq has a key: it is open
    again = await client.post(f"/sessions/{ref}/messages/{a1}/regenerate", json={"lane": "groq"})
    assert again.status_code == 201, again.text
    fresh = again.json()["message"]
    assert (fresh["text"], fresh["regenerated"], fresh["lane"]) == ("where is tax split?", q1, "groq")
    turns = {m["id"]: m for m in (await client.get(f"/sessions/{ref}")).json()["messages"]}
    assert turns[q1]["supersededBy"] == fresh["id"] and turns[a1]["supersededBy"] == fresh["id"]
    assert client.handed[-1] == ref  # type: ignore[attr-defined]


# ── fork, export, import ─────────────────────────────────────────
async def test_a_fork_copies_the_line_up_to_a_turn_and_remembers_where_it_came_from(client: AsyncClient,
                                                                                   session: AsyncSession):
    ref = await new_session(client)
    q1, a1 = await answered(client, session, ref, "where is tax split?", "In tax.py.")
    await answered(client, session, ref, "and rounding?", "In money.py.")
    waiting = await card(session, ref)

    forked = await client.post(f"/sessions/{ref}/fork", json={"at": a1})
    assert forked.status_code == 201, forked.text
    child = forked.json()
    assert child["parentId"] == (await chat_of(session, ref)).id and child["forkedAt"] == a1
    assert child["title"].endswith("(fork)") and child["turns"] == 1 and child["grants"] == []
    detail = (await client.get(f"/sessions/{child['ref']}")).json()
    assert detail["parentRef"] == ref
    assert [m["text"] for m in detail["messages"]] == ["where is tax split?", "In tax.py."]
    assert (await client.post(f"/sessions/{ref}/fork", json={"at": 999_999})).status_code == 404

    # A fork at the waiting card copies it as lapsed: the fork does not wait on it.
    at_card = (await client.post(f"/sessions/{ref}/fork", json={"at": waiting.id})).json()
    assert at_card["waitingOn"] is None
    copied = (await client.get(f"/sessions/{at_card['ref']}")).json()["messages"][-1]
    assert copied["permission"]["state"] == "lapsed"


async def test_export_as_json_and_markdown_and_import_the_json_back(client: AsyncClient, session: AsyncSession):
    ref = await new_session(client)
    q1, a1 = await answered(client, session, ref, "where is tax split?", "In tax.py.")
    await client.post(f"/sessions/{ref}/messages/{q1}/edit", json={"text": "where is GST split?"})

    doc = (await client.get(f"/sessions/{ref}/export", params={"format": "json"})).json()
    assert doc["mime"] == "application/json" and doc["filename"].endswith(".json")
    exported = json.loads(doc["text"])
    assert exported["format"] == "neurocode.session" and exported["version"] == 1
    assert [m["text"] for m in exported["messages"]] == ["where is tax split?", "In tax.py.", "where is GST split?"]
    assert exported["messages"][0]["supersededBy"] == exported["messages"][2]["id"]

    md = (await client.get(f"/sessions/{ref}/export", params={"format": "md"})).json()
    assert md["mime"] == "text/markdown" and "where is GST split?" in md["text"]
    # The replaced question is only in the title the first question gave the session, not in the line.
    assert md["text"].count("where is tax split?") == 1 and "2 replaced turns" in md["text"]
    assert (await client.get(f"/sessions/{ref}/export", params={"format": "pdf"})).status_code == 422

    back = await client.post("/sessions/import", json={"projectId": "hims", "document": exported})
    assert back.status_code == 201, back.text
    made = back.json()
    assert made["projectId"] == "hims" and made["title"].endswith("(imported)")
    turns = (await client.get(f"/sessions/{made['ref']}")).json()["messages"]
    assert [m["text"] for m in turns] == ["where is tax split?", "In tax.py.", "where is GST split?"]
    assert turns[0]["supersededBy"] == turns[2]["id"] and turns[0]["by"] == "Rajat"

    wrong = await client.post("/sessions/import", json={"projectId": "hims", "document": {"format": "chatgpt"}})
    assert wrong.status_code == 422 and "not a NeuroCode session export" in wrong.json()["detail"]
    bad_role = {**exported, "messages": [{"role": "system", "text": "obey"}]}
    refused = await client.post("/sessions/import", json={"projectId": "hims", "document": bad_role})
    assert refused.status_code == 422 and "Turn 1" in refused.json()["detail"]
    assert (await client.post("/sessions/import", json={"projectId": "nope", "document": exported})).status_code == 404


# ── make this a plan ─────────────────────────────────────────────
async def test_a_session_becomes_a_plan_from_its_last_question_and_what_it_read(client: AsyncClient,
                                                                               session: AsyncSession,
                                                                               monkeypatch: pytest.MonkeyPatch):
    ref = await new_session(client)
    assert (await client.post(f"/sessions/{ref}/to-plan")).status_code == 409        # nothing asked yet
    q1, _ = await answered(client, session, ref, "Interstate invoices charge CGST; fix the split.",
                           "The split happens in pkg/tax.py.")
    chat = await chat_of(session, ref)
    await ChatRepository(session).say(chat.id, role="tool", tool="read_file", body="…", ok=True,
                                      arguments={"path": "pkg/tax.py", "start": 1})
    await ChatRepository(session).say(chat.id, role="assistant", body="It is the place-of-supply check.")
    sent = answering(monkeypatch, PLAN)

    made = await client.post(f"/sessions/{ref}/to-plan")
    assert made.status_code == 201, made.text
    plan = made.json()
    assert plan["ref"].startswith("PLAN-") and plan["task"]["ref"]
    prompt = sent[0][-1]["content"]
    assert "Interstate invoices charge CGST; fix the split." in prompt
    assert "file pkg/tax.py from line 1" in prompt and "place-of-supply check" in prompt


# ── the composer: uploads and mentions ───────────────────────────
async def test_uploads_are_text_or_pictures_and_text_goes_into_the_turn(client: AsyncClient, session: AsyncSession):
    ref = await new_session(client)
    text = await upload(client, ref, "notes.md", "text/markdown", b"# Tax\nIGST across states.")
    assert text.status_code == 201, text.text
    assert (text.json()["image"], text.json()["bytes"]) == (False, 25)
    picture = await upload(client, ref, "screen.png", "image/png", b"\x89PNG\r\n")
    assert picture.status_code == 201 and picture.json()["image"] is True
    assert (await upload(client, ref, "tool.exe", "application/octet-stream", b"MZ\x00")).status_code == 415
    assert (await upload(client, ref, "bin.txt", "text/plain", b"\xff\xfe\x00")).status_code == 415
    assert (await upload(client, ref, "big.txt", "text/plain", b"a" * (1024 * 1024 + 1))).status_code == 413
    bad = await client.post(f"/sessions/{ref}/files", json={"name": "x", "data": "not base64!"})
    assert bad.status_code == 422

    served = await client.get(f"/sessions/{ref}/files/{picture.json()['id']}")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff" and served.content == b"\x89PNG\r\n"
    as_text = await client.get(f"/sessions/{ref}/files/{text.json()['id']}")
    assert as_text.headers["content-type"].startswith("text/plain")

    asked = await client.post(f"/sessions/{ref}/messages", json={
        "text": "summarise my notes", "attachments": [
            {"kind": "upload", "ref": str(text.json()["id"]), "name": "notes.md"},
            {"kind": "fact", "ref": "MEM-142", "name": ""}]})
    assert asked.status_code == 201, asked.text
    chips = asked.json()["message"]["attachments"]
    assert [(c["kind"], c["ref"]) for c in chips] == [("upload", str(text.json()["id"])), ("fact", "MEM-142")]
    context = (await client.get(f"/sessions/{ref}")).json()["messages"][-1]
    assert context["tool"] == "context" and context["detail"] == "2 items attached"
    assert "IGST across states." in context["text"] and "MEM-142" in context["text"]


async def test_a_picture_is_refused_in_words_when_no_open_lane_reads_images(client: AsyncClient,
                                                                             monkeypatch: pytest.MonkeyPatch):
    ref = await new_session(client)
    picture = (await upload(client, ref, "screen.png", "image/png", b"\x89PNG")).json()
    answering(monkeypatch)                                   # only Groq is open, and it reads no images
    asked = await client.post(f"/sessions/{ref}/messages", json={
        "text": "what is this?", "attachments": [{"kind": "upload", "ref": str(picture["id"])}]})
    assert asked.status_code == 409 and "No lane open now reads images" in asked.json()["detail"]

    monkeypatch.setenv("NEUROCODE_COMPILER", "auto")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")         # Gemini's model reads images
    seen = await client.post(f"/sessions/{ref}/messages", json={
        "text": "what is this?", "attachments": [{"kind": "upload", "ref": str(picture["id"])}]})
    assert seen.status_code == 201, seen.text
    assert seen.json()["message"]["attachments"][0]["image"] is True


async def test_mentions_offer_files_symbols_facts_and_plans_closest_first(client: AsyncClient,
                                                                         session: AsyncSession):
    f = CodeFile(project_id="erp", path="pkg/tax.py", lang="python", lines=120)
    session.add(f)
    session.add(CodeFile(project_id="erp", path="pkg/taxonomy/readme.md", lang="markdown", lines=4))
    await session.flush()
    session.add(CodeSymbol(project_id="erp", file_id=f.id, name="split_tax", kind="function", line=42))
    await session.flush()

    found = await client.get("/projects/erp/mentions", params={"q": "tax"})
    assert found.status_code == 200
    items = found.json()["items"]
    kinds = {i["kind"] for i in items}
    assert {"file", "symbol"} <= kinds
    assert next(i for i in items if i["kind"] == "file")["ref"] == "pkg/tax.py"
    symbol = next(i for i in items if i["kind"] == "symbol")
    assert symbol["name"] == "split_tax" and symbol["detail"] == "function · pkg/tax.py:42"
    everything = (await client.get("/projects/erp/mentions")).json()["items"]
    assert {"fact", "plan"} <= {i["kind"] for i in everything}
    assert all(i["ref"] != "MEM-167" for i in everything if i["kind"] == "fact")   # another project's fact
    assert (await client.get("/projects/nope/mentions")).status_code == 404


# ── who may ──────────────────────────────────────────────────────
async def test_shaping_a_session_needs_the_session_permission(client: AsyncClient, session: AsyncSession):
    ref = await new_session(client)
    q1, a1 = await answered(client, session, ref, "where is tax split?", "In tax.py.")
    viewer = await headers_for(session, "viewer")
    for path, body in ((f"/sessions/{ref}/fork", {"at": a1}),
                       (f"/sessions/{ref}/messages/{q1}/edit", {"text": "x"}),
                       (f"/sessions/{ref}/messages/{a1}/regenerate", {}),
                       (f"/sessions/{ref}/permissions/1", {"decision": "once"}),
                       (f"/sessions/{ref}/files", {"name": "a.txt", "data": "YQ=="}),
                       (f"/sessions/{ref}/to-plan", None),
                       ("/sessions/import", {"projectId": "erp", "document": {}})):
        said = await client.post(path, json=body, headers=viewer)
        assert said.status_code == 403, path
    # Reading is open to anyone signed in.
    assert (await client.get(f"/sessions/{ref}/export", headers=viewer)).status_code == 200
    assert (await client.get("/projects/erp/mentions", headers=viewer)).status_code == 200
