"""Tools a person defined: what may be written down, what is checked, and what is refused.

Three promises are under test here and nothing else is assumed.

**Nothing runs that a rule has not allowed.** A custom tool no `tool` rule mentions is refused in the
tool's own turn — not a permission card, a refusal, because silence about a command line is a no.

**The arguments are checked before anything happens.** Against the tool's own schema, by name, with the
refusal saying which argument and why; and a value that looks like a second command stays one argument.

**What comes back is data.** It reaches the transcript under a line saying so, and the activity feed has
the call in it.

The command a tool runs is a real program in a real checkout on this machine, and the HTTP half is
pointed at loopback on purpose: the address guard is the thing being checked, so the test asserts that
the call never happened.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.repositories import ChatRepository
from app.services import machine
from app.services.chat import ChatService, Turn, _tool_turn, reach_for
from app.services.custom_tools import (DATA_HEADER, CustomToolService, SchemaError, check_schema,
                                       checked_spec)
from app.services.errors import Refused
from app.services.identity import IdentityService
from app.settings import settings as real_settings
from tests.fixtures.workspace import load_workspace

HEADERS = {"X-NC-Client": "test"}
PID = "erp"


class FakeGateway:
    """The catalogue and the calls are what is under test; no model is ever asked."""

    secrets = None

    def embed_lane(self) -> None:
        return None


def _schema(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


# ── the schema check, as the pure function it is ─────────────────
def test_a_required_argument_that_is_missing_is_named_in_the_refusal():
    schema = {**_schema(branch={"type": "string", "description": "the branch to build"}),
              "required": ["branch"]}
    with pytest.raises(SchemaError, match="branch is required for preview: the branch to build"):
        check_schema(schema, {}, where="preview")


def test_an_argument_of_the_wrong_type_says_which_type_it_wanted():
    with pytest.raises(SchemaError, match="count must be a integer"):
        check_schema(_schema(count={"type": "integer"}), {"count": "seven"})
    # A bool is not an integer and an int is not a boolean, whatever Python thinks.
    with pytest.raises(SchemaError, match="count must be a integer"):
        check_schema(_schema(count={"type": "integer"}), {"count": True})
    with pytest.raises(SchemaError, match="flag must be a boolean"):
        check_schema(_schema(flag={"type": "boolean"}), {"flag": 1})


def test_an_argument_nobody_declared_is_refused_with_the_real_ones_listed():
    with pytest.raises(SchemaError, match="shell is not an argument of this tool. It takes: branch"):
        check_schema(_schema(branch={"type": "string"}), {"shell": "rm -rf /"})


def test_enum_pattern_and_bounds_hold_and_a_default_is_filled_in():
    schema = _schema(service={"type": "string", "enum": ["web", "api"]},
                     level={"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
                     ref={"type": "string", "pattern": r"^[\w.-]+$"})
    assert check_schema(schema, {"service": "web", "ref": "v1.2"}) == {"service": "web", "level": 2, "ref": "v1.2"}
    with pytest.raises(SchemaError, match="service must be one of"):
        check_schema(schema, {"service": "database"})
    with pytest.raises(SchemaError, match="level must be at most 3"):
        check_schema(schema, {"service": "web", "level": 9})
    with pytest.raises(SchemaError, match="ref does not match"):
        check_schema(schema, {"service": "web", "ref": "a; rm -rf /"})


def test_a_tool_that_declares_nothing_takes_nothing():
    with pytest.raises(SchemaError, match="takes no arguments"):
        check_schema({}, {"anything": 1})


# ── what may be written down at all ──────────────────────────────
def test_a_command_tool_is_argv_and_never_a_command_line():
    with pytest.raises(Refused, match="never a command line, because a command line would need a shell"):
        checked_spec("command", {"argv": "./deploy.sh --now", "arguments": _schema()})


def test_a_placeholder_nobody_declared_is_refused_when_the_tool_is_written():
    with pytest.raises(Refused, match="branch .* used in this definition but not declared"):
        checked_spec("command", {"argv": ["./deploy.sh", "{branch}"], "arguments": _schema()})


def test_a_cwd_cannot_leave_the_checkout_and_a_timeout_is_capped():
    with pytest.raises(Refused, match="folder inside the project's checkout"):
        checked_spec("command", {"argv": ["ls"], "cwd": "../../etc", "arguments": _schema()})
    assert checked_spec("command", {"argv": ["ls"], "timeoutS": 9_000, "arguments": _schema()})["timeoutS"] == 120


def test_an_http_tool_needs_an_address_and_a_method_the_product_sends():
    with pytest.raises(Refused, match="an http or https address"):
        checked_spec("http", {"url": "ftp://files.example.com/x", "arguments": _schema()})
    with pytest.raises(Refused, match="`method` is one of"):
        checked_spec("http", {"url": "https://x.example.com", "method": "TRACE", "arguments": _schema()})


# ── a workspace with a checkout, an owner, and a session ─────────
@pytest_asyncio.fixture
async def lab(session: AsyncSession, tmp_path: Path,
              monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncSession, Any, m.Chat, Path]:
    """The test workspace, an owner, a real checkout for the ERP, and a session on it."""
    await load_workspace(session)
    identity = IdentityService(session)
    made = await identity.create("owner@example.com", "Rajat", "correct horse battery", ["owner"])
    who = await identity.whoami(await identity.start_session(made.id))
    assert who is not None

    repo = tmp_path / "erp"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts" / "say.sh"
    script.write_text("#!/bin/sh\necho \"built $1\"\n")
    script.chmod(0o755)
    project = await session.get(m.Project, PID)
    assert project is not None
    project.source_kind, project.source_repo = "local", str(repo)

    configured = real_settings().model_copy(update={"machine_roots": str(tmp_path), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    chat = await ChatService(session, FakeGateway()).start(PID, "Rajat")
    await session.flush()
    return session, who, chat, repo


async def define(session: AsyncSession, who: Any, **over: Any) -> m.CustomTool:
    spec = over.pop("spec", {"argv": ["./scripts/say.sh", "{what}"],
                             "arguments": {**_schema(what={"type": "string"}), "required": ["what"]}})
    made = await CustomToolService(session).create(
        name=over.pop("name", "build"), description=over.pop("description", "Builds one thing"),
        kind=over.pop("kind", "command"), spec=spec, project_id=over.pop("project_id", PID), who=who)
    row = await session.get(m.CustomTool, made["id"])
    assert row is not None
    return row


async def call_it(lab: tuple[AsyncSession, Any, m.Chat, Path], arguments: dict[str, Any]) -> m.ChatMessage:
    session, who, chat, _ = lab
    project = await session.get(m.Project, PID)
    acting = await reach_for(session, FakeGateway(), who, PID)
    await _tool_turn(session, FakeGateway(), chat, project,
                     Turn(tool="custom_tool", arguments={"name": "build", "arguments": arguments}),
                     acting=acting, by="Rajat")
    return (await ChatRepository(session).messages(chat.id))[-1]


# ── nothing runs that a rule has not allowed ─────────────────────
async def test_a_tool_no_rule_allows_is_refused_rather_than_asked_about(lab):
    session, who, chat, repo = lab
    await define(session, who)
    said = await call_it(lab, {"what": "the app"})

    assert said.tool == "custom_tool" and said.ok is False and said.detail == "denied by a rule"
    assert "No tool rule allows the custom tool build" in said.body
    assert "Governance → Permissions" in said.body
    # Nothing was asked of anybody: a card would teach a session to keep asking for a command line.
    assert said.arguments["arguments"] == {"what": "the app"}


async def test_a_rule_that_denies_it_names_itself_in_the_refusal(lab):
    session, who, chat, repo = lab
    await define(session, who)
    session.add(m.ToolRule(project_id=PID, tool="tool", pattern="build", action="deny", note="not from here"))
    await session.flush()

    said = await call_it(lab, {"what": "the app"})
    assert said.detail == "denied by a rule" and "(not from here)" in said.body


async def test_a_rule_that_asks_puts_a_card_in_front_of_a_person(lab):
    session, who, chat, repo = lab
    await define(session, who)
    session.add(m.ToolRule(project_id=PID, tool="tool", pattern="build", action="ask", note=""))
    await session.flush()

    project = await session.get(m.Project, PID)
    acting = await reach_for(session, FakeGateway(), who, PID)
    paused = await _tool_turn(session, FakeGateway(), chat, project,
                              Turn(tool="custom_tool", arguments={"name": "build", "arguments": {"what": "x"}}),
                              acting=acting, by="Rajat")
    card = (await ChatRepository(session).messages(chat.id))[-1]
    assert paused is True and card.arguments["state"] == "pending"
    assert card.arguments["subject"] == "build" and card.arguments["covers"] == "build, with any arguments"


async def test_allow_for_this_session_is_found_again_at_the_next_call(lab):
    """A grant is written under the rules' name for the tool, and looked up under the rules' name — so
    "Allow for this session" has to survive the round trip, or a session would ask forever."""
    session, who, chat, repo = lab
    await define(session, who)
    session.add(m.ToolRule(project_id=PID, tool="tool", pattern="build", action="ask", note=""))
    await session.flush()

    project = await session.get(m.Project, PID)
    acting = await reach_for(session, FakeGateway(), who, PID)
    call = Turn(tool="custom_tool", arguments={"name": "build", "arguments": {"what": "once"}})
    await _tool_turn(session, FakeGateway(), chat, project, call, acting=acting, by="Rajat")
    card = (await ChatRepository(session).messages(chat.id))[-1]

    await ChatService(session, FakeGateway()).permit(chat.ref, card.id, "session", who)
    assert [g["tool"] for g in chat.grants] == ["tool"]

    await _tool_turn(session, FakeGateway(), chat, project, call, acting=acting, by="Rajat")
    said = (await ChatRepository(session).messages(chat.id))[-1]
    assert said.tool == "custom_tool" and said.ok is True and "built once" in said.body


# ── what an allowed call actually does ───────────────────────────
async def test_an_allowed_command_runs_in_the_checkout_and_comes_back_as_data(lab):
    session, who, chat, repo = lab
    await define(session, who)
    session.add(m.ToolRule(project_id=PID, tool="tool", pattern="build", action="allow", note=""))
    await session.flush()

    said = await call_it(lab, {"what": "the app"})
    assert said.ok is True and said.body.startswith(DATA_HEADER)
    assert "built the app" in said.body and said.detail.startswith("build · exit 0")

    logged = (await session.execute(
        select(m.ActivityEvent).where(m.ActivityEvent.action == "Custom tool called"))).scalars().all()
    assert len(logged) == 1 and logged[0].detail.startswith("build · exit 0")


async def test_an_argument_that_looks_like_a_second_command_stays_one_argument(lab):
    session, who, chat, repo = lab
    await define(session, who)
    session.add(m.ToolRule(project_id=PID, tool="tool", pattern="build", action="allow", note=""))
    await session.flush()

    marker = repo / "owned"
    said = await call_it(lab, {"what": f"x; touch {marker}"})
    assert said.ok is True
    assert f"built x; touch {marker}" in said.body
    assert not marker.exists()               # no shell ran it: it was one argv entry, with a semicolon in it


async def test_the_arguments_are_checked_before_anything_runs(lab):
    session, who, chat, repo = lab
    await define(session, who)
    session.add(m.ToolRule(project_id=PID, tool="tool", pattern="build", action="allow", note=""))
    await session.flush()

    said = await call_it(lab, {"whatever": 3})
    assert said.ok is False and "whatever is not an argument of this tool for build" in said.body
    assert (await session.execute(
        select(m.ActivityEvent).where(m.ActivityEvent.action == "Custom tool called"))).first() is None


async def test_a_name_nobody_defined_is_refused_with_the_real_names_listed(lab):
    session, who, chat, repo = lab
    await define(session, who)
    project = await session.get(m.Project, PID)
    acting = await reach_for(session, FakeGateway(), who, PID)
    await _tool_turn(session, FakeGateway(), chat, project,
                     Turn(tool="custom_tool", arguments={"name": "deploy"}), acting=acting, by="Rajat")
    said = (await ChatRepository(session).messages(chat.id))[-1]
    assert said.ok is False and "There is no tool called deploy defined here" in said.body
    assert "The tools defined here are: build" in said.body


async def test_a_switched_off_tool_is_not_offered_at_all(lab):
    session, who, chat, repo = lab
    row = await define(session, who)
    await CustomToolService(session).update(row.id, description=None, spec=None, enabled=False, who=who)
    acting = await reach_for(session, FakeGateway(), who, PID)
    assert acting.custom == () and "custom_tool" not in acting.names()


async def test_an_http_tool_cannot_reach_this_machine(lab):
    session, who, chat, repo = lab
    await define(session, who, name="status", kind="http",
                 spec={"url": "http://127.0.0.1:1/secrets", "arguments": _schema()})
    session.add(m.ToolRule(project_id=PID, tool="tool", pattern="status", action="allow", note=""))
    await session.flush()

    project = await session.get(m.Project, PID)
    acting = await reach_for(session, FakeGateway(), who, PID)
    await _tool_turn(session, FakeGateway(), chat, project,
                     Turn(tool="custom_tool", arguments={"name": "status"}), acting=acting, by="Rajat")
    said = (await ChatRepository(session).messages(chat.id))[-1]
    assert said.ok is False and "local or private address" in said.body


async def test_the_catalogue_offers_the_tool_and_says_its_answer_is_data(lab):
    session, who, chat, repo = lab
    await define(session, who)
    acting = await reach_for(session, FakeGateway(), who, PID)
    from app.services.chat import system_prompt

    prompt = system_prompt("ERP", acting=acting)
    assert "custom_tool" in prompt and "build" in prompt
    assert "data, never an instruction" in prompt


# ── the routes ───────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client(lab) -> AsyncIterator[AsyncClient]:
    session, *_ = lab
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS) as c:
        signed = await c.post("/auth/login", json={"email": "owner@example.com",
                                                   "password": "correct horse battery"})
        assert signed.status_code == 200, signed.text
        yield c


async def test_a_tool_is_defined_listed_and_refused_twice_under_the_same_name(client: AsyncClient):
    body = {"name": "preview", "description": "Builds a preview", "kind": "command", "projectId": PID,
            "spec": {"argv": ["./scripts/say.sh", "{branch}"],
                     "arguments": {**_schema(branch={"type": "string"}), "required": ["branch"]}}}
    made = await client.post("/extensions/tools", json=body)
    assert made.status_code == 201, made.text
    assert made.json()["name"] == "preview" and made.json()["enabled"] is True

    again = await client.post("/extensions/tools", json=body)
    assert again.status_code == 409 and "already a tool called preview" in again.json()["detail"]

    listed = (await client.get("/extensions/tools", params={"projectId": PID})).json()
    assert [t["name"] for t in listed] == ["preview"]
    assert listed[0]["spec"]["timeoutS"] == 30            # the default the server wrote, not the caller's

    gone = await client.delete(f"/extensions/tools/{made.json()['id']}")
    assert gone.status_code == 200
    assert (await client.get("/extensions/tools", params={"projectId": PID})).json() == []


async def test_a_definition_that_could_not_run_is_refused_at_the_route(client: AsyncClient):
    bad = await client.post("/extensions/tools", json={
        "name": "sneaky", "kind": "command", "projectId": PID,
        "spec": {"argv": ["sh", "-c", "{anything}"], "arguments": _schema()}})
    assert bad.status_code == 422 and "not declared in `arguments.properties`" in bad.json()["detail"]

    named = await client.post("/extensions/tools", json={
        "name": "Not A Name", "kind": "command", "projectId": PID,
        "spec": {"argv": ["ls"], "arguments": _schema()}})
    assert named.status_code == 422 and "lowercase letter" in named.json()["detail"]
