"""Custom agents over HTTP, and what they change where they are used: a session asked through one, a plan
step one owns, and the compiler that may hand steps to them.

The routes are driven inside the rolled-back transaction with the real gateway on a scripted lane — no
provider is ever called. The answering loop and the runtime run for real against committed rows, with a
stand-in gateway that keeps every prompt it was sent, as their own tests do.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai import compiler
from app.ai.gateway import Gateway, Provider, Result
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.data.engine import Database
from app.repositories import ChatRepository, RunRepository
from app.secrets import Secrets
from app.services import custom_agents as agents
from app.services import runs as runtime
from app.services.chat import think
from app.services.runs import RunService, execute
from tests.fixtures.lanes import answering
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
AUDITOR = {"name": "Ledger Auditor", "role": "Reads money code for rounding and tax mistakes",
           "prompt": "Check every money value is rounded with round(x, 2). Quote the line you rely on.",
           "lane": "groq", "tools": ["read_file", "search_code", "edit"], "maxSteps": 3, "mode": "subagent"}


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


def checkout(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    return root


AGENT_FILES = {
    ".neurocode/agents/tax-scout.md": "---\nname: Tax Scout\ndescription: Finds tax code\n"
                                      "tools: Read, Grep, Bash(git log:*), mcp__github__search\nmodel: gemini/2.5\n"
                                      "maxSteps: 4\n---\nYou look for tax rules and cite them.\n",
    ".claude/agents/ledger-auditor.md": "---\nname: Ledger Auditor\ndescription: The repository's own auditor\n"
                                        "model: sonnet\n---\nA file version that a stored agent overrides.\n",
    ".claude/agents/backend.md": "---\nname: Backend Engineer\n---\nPretends to be a roster agent.\n",
    ".claude/agents/broken.md": "---\nname: Broken\nnever closed\n",
}


@pytest.fixture
def api_gateway(tmp_path: Path) -> Gateway:
    return Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))


@pytest_asyncio.fixture
async def api(session: AsyncSession, api_gateway: Gateway) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    made.dependency_overrides[deps.gateway] = lambda: api_gateway
    return made


def _client(made: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=made), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest_asyncio.fixture
async def shop(session: AsyncSession, tmp_path: Path) -> str:
    """A project whose checkout declares agents in both folders — one of them unreadable, one named like a
    roster agent."""
    root = checkout(tmp_path / "shop", {"pkg/core.py": "def total(x):\n    return x\n", **AGENT_FILES})
    session.add(m.Project(id="agent-shop", name="Agent Shop", source_kind="local", source_repo=str(root)))
    await session.flush()
    return "agent-shop"


# ── writing agents ───────────────────────────────────────────────
async def test_an_agent_is_written_changed_and_removed_with_both_logs(client: AsyncClient, session: AsyncSession):
    made = await client.post("/agents/custom", json=AUDITOR)
    assert made.status_code == 201
    body = made.json()
    assert body["key"] == f"custom:{body['id']}" and body["source"] == "workspace" and body["editable"] is True
    assert body["tools"] == ["search_code", "read_file", "edit"]           # in the catalogue's order
    assert body["lane"] == "groq" and body["maxSteps"] == 3 and body["createdBy"] == "Rajat"
    assert body["usage"] == {"sessions": 0, "lastSession": None, "steps": 0, "stepsDone": 0}

    listed = (await client.get("/agents/custom")).json()
    assert [a["name"] for a in listed["agents"]] == ["Ledger Auditor"]
    assert {t["name"] for t in listed["tools"]} >= {"edit", "web_fetch", "read_file"}
    assert listed["folders"] == [] and listed["checkout"] is None                # no project: no files to read

    changed = await client.patch(f"/agents/custom/{body['id']}", json={**AUDITOR, "maxSteps": 5, "tools": []})
    assert changed.status_code == 200 and changed.json()["maxSteps"] == 5 and changed.json()["tools"] == []

    audited = (await session.execute(select(m.AuditEntry.action).where(
        m.AuditEntry.action.like("custom_agent.%")).order_by(m.AuditEntry.seq))).scalars().all()
    assert audited == ["custom_agent.create", "custom_agent.update"]
    feed = (await session.execute(select(m.ActivityEvent.action).where(
        m.ActivityEvent.action.like("Custom agent%")))).scalars().all()
    assert set(feed) == {"Custom agent added", "Custom agent changed"}

    assert (await client.delete(f"/agents/custom/{body['id']}")).json() == {"ok": True, "id": body["id"]}
    assert (await client.get("/agents/custom")).json()["agents"] == []
    assert (await client.delete(f"/agents/custom/{body['id']}")).status_code == 404


async def test_what_an_agent_may_be_is_checked(client: AsyncClient, shop: str):
    assert (await client.post("/agents/custom", json=AUDITOR)).status_code == 201
    again = await client.post("/agents/custom", json={**AUDITOR, "name": "ledger AUDITOR"})
    assert again.status_code == 409 and "already an agent" in again.json()["detail"]
    # The same name in a project is another scope: it shadows the workspace's there.
    assert (await client.post("/agents/custom", json={**AUDITOR, "projectId": shop})).status_code == 201

    roster_name = await client.post("/agents/custom", json={**AUDITOR, "name": "Backend Engineer"})
    assert roster_name.status_code == 422 and "built-in" in roster_name.json()["detail"]
    assert (await client.post("/agents/custom", json={**AUDITOR, "name": "X", "tools": ["bash"]})).status_code == 422
    assert (await client.post("/agents/custom", json={**AUDITOR, "name": "Y", "lane": "nowhere"})).status_code == 422
    assert (await client.post("/agents/custom", json={**AUDITOR, "name": "Z", "maxSteps": 40})).status_code == 422
    assert (await client.post("/agents/custom", json={**AUDITOR, "name": "W", "prompt": "   "})).status_code == 422
    assert (await client.post("/agents/custom", json={**AUDITOR, "name": "V", "projectId": "nope"})).status_code == 404


async def test_writing_an_agent_needs_agents_manage(api: FastAPI, client: AsyncClient):
    await client.post("/admin/users", json=VIEWER)
    made = (await client.post("/agents/custom", json=AUDITOR)).json()
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/agents/custom")).status_code == 200            # reading is for everyone
        assert (await viewer.post("/agents/custom", json={**AUDITOR, "name": "Mine"})).status_code == 403
        assert (await viewer.patch(f"/agents/custom/{made['id']}", json=AUDITOR)).status_code == 403
        assert (await viewer.delete(f"/agents/custom/{made['id']}")).status_code == 403
    async with _client(api) as stranger:
        assert (await stranger.get("/agents/custom")).status_code == 401


# ── agents a repository declares ─────────────────────────────────
async def test_a_project_lists_its_files_beside_the_stored_ones_nearest_first(client: AsyncClient, shop: str):
    stored = (await client.post("/agents/custom", json=AUDITOR)).json()
    body = (await client.get("/agents/custom", params={"project": shop})).json()
    by = {(a["name"], a["source"]): a for a in body["agents"]}

    scout = by[("Tax Scout", ".neurocode/agents")]
    assert scout["key"] == "file:Tax Scout" and scout["editable"] is False
    assert scout["path"] == ".neurocode/agents/tax-scout.md" and scout["prompt"].startswith("You look for tax")
    assert scout["tools"] == ["read_file", "search_code", "find", "mcp"]       # Read, Grep and the MCP tool
    assert scout["ignored"] == ["Bash"] and scout["lane"] == "gemini" and scout["maxSteps"] == 4

    # A repository's file is nearer than the workspace: the file keeps the name, the stored one is shadowed.
    filed = by[("Ledger Auditor", ".claude/agents")]
    assert filed["shadowedBy"] is None and filed["lane"] is None
    assert filed["notes"] == ["sonnet is not a lane here, so the router chooses."]
    assert by[("Ledger Auditor", "workspace")]["shadowedBy"] == "file:Ledger Auditor"
    assert by[("Backend Engineer", ".claude/agents")]["shadowedBy"] == "built-in"
    assert body["unreadable"] == [".claude/agents/broken.md"]
    assert body["folders"] == [{"path": ".neurocode/agents", "read": True}, {"path": ".claude/agents", "read": True}]

    # A project's own stored agent is nearer still.
    await client.post("/agents/custom", json={**AUDITOR, "projectId": shop})
    again = (await client.get("/agents/custom", params={"project": shop})).json()
    winner = next(a for a in again["agents"] if a["name"] == "Ledger Auditor" and not a["shadowedBy"])
    assert winner["source"] == "project"
    assert stored["id"] != winner["id"]
    assert (await client.get("/agents/custom", params={"project": "nope"})).status_code == 404


def test_front_matter_tools_and_models_are_read_the_way_both_tools_write_them():
    assert agents.tools_of("Read, Glob, WebFetch, TodoWrite") == (("read_file", "list_files", "web_fetch"),
                                                                  ("TodoWrite",))
    kept, ignored = agents.tools_of("write: false webfetch: false bash: false")        # OpenCode's switches
    assert "edit" not in kept and "web_fetch" not in kept and "read_file" in kept and ignored == ()
    assert agents.tools_of("") == ((), ())                                             # nothing said: every tool
    assert agents.lane_of("groq") == ("groq", "") and agents.lane_of("cerebras/qwen-3-coder") == ("cerebras", "")
    assert agents.lane_of("inherit") == (None, "")


# ── trying one ───────────────────────────────────────────────────
async def test_a_dry_run_answers_as_the_agent_on_the_lane_it_prefers(client: AsyncClient, session: AsyncSession,
                                                                      monkeypatch: pytest.MonkeyPatch):
    stored = (await client.post("/agents/custom", json=AUDITOR)).json()
    sent = answering(monkeypatch, {"answer": "I would read pkg/tax.py first."})
    tried = await client.post("/agents/try", json={"agent": stored["key"], "question": "Where is rounding done?"})
    assert tried.status_code == 200
    body = tried.json()
    assert body["answer"] == "I would read pkg/tax.py first." and body["lane"] == "groq"
    assert body["preferred"] == "groq" and body["onPreferred"] is True
    system = sent[0][0]["content"]
    assert system.startswith("You are Ledger Auditor, Reads money code") and "round(x, 2)" in system
    assert "dry run" in system and sent[0][1]["content"] == "Where is rounding done?"
    assert (await session.execute(select(m.ActivityEvent).where(m.ActivityEvent.action == "Agent tried"))).first()

    assert (await client.post("/agents/try", json={"agent": "custom:nope", "question": "hi"})).status_code == 404


async def test_a_dry_run_with_no_model_says_so(client: AsyncClient):
    stored = (await client.post("/agents/custom", json=AUDITOR)).json()
    refused = await client.post("/agents/try", json={"agent": stored["key"], "question": "Anything?"})
    assert refused.status_code == 409 and "No model is configured" in refused.json()["detail"]


# ── Ask <agent>: a session answered by one ───────────────────────
async def test_a_session_can_be_asked_through_an_agent(client: AsyncClient, shop: str):
    stored = (await client.post("/agents/custom", json={**AUDITOR, "projectId": shop})).json()
    made = await client.post("/sessions", json={"projectId": shop, "agent": stored["key"]})
    assert made.status_code == 201
    assert made.json()["agent"] == stored["key"] and made.json()["title"] == "Ask Ledger Auditor"
    filed = await client.post("/sessions", json={"projectId": shop, "agent": "file:Tax Scout"})
    assert filed.status_code == 201 and filed.json()["agent"] == "file:Tax Scout"
    built_in = await client.post("/sessions", json={"projectId": shop, "agent": "security"})
    assert built_in.status_code == 201 and built_in.json()["title"].startswith("Ask ")
    assert (await client.post("/sessions", json={"projectId": shop, "agent": "custom:nope"})).status_code == 422
    # A project's agent is not another project's.
    assert (await client.post("/sessions", json={"projectId": "erp", "agent": stored["key"]})).status_code == 422
    plain = (await client.post("/sessions", json={"projectId": shop})).json()
    assert plain["agent"] is None and plain["title"] == "New session"

    ref = made.json()["ref"]
    await client.delete(f"/agents/custom/{stored['id']}")
    gone = await client.post(f"/sessions/{ref}/messages", json={"text": "Is rounding right?"})
    assert gone.status_code == 409 and "no longer there" in gone.json()["detail"]
    listed = (await client.get("/agents/custom", params={"project": shop})).json()
    scout = next(a for a in listed["agents"] if a["key"] == "file:Tax Scout")
    assert scout["usage"]["sessions"] == 1                          # counted from the sessions really asked


PROJECT = "agent-loop-project"


class FakeGateway:
    """Says what it is told, in order, and keeps every prompt and every lane asked for."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.seen: list[list[dict[str, Any]]] = []
        self.lanes: list[str | None] = []

    def embed_lane(self) -> None:
        return None

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["cerebras"] * n

    def chain(self, role: str | None = None, limit: int = 20) -> list[Any]:
        return []

    def ask(self, messages: list[dict[str, Any]], parse: Any, **kw: Any) -> Result[Any]:
        self.seen.append(messages)
        self.lanes.append(kw.get("lane"))
        raw = self.script.pop(0) if self.script else '{"answer": "Done."}'
        return Result(parse(raw), Provider(kw.get("lane") or "groq", "llama-3.3-70b-versatile"), 12)


@pytest_asyncio.fixture
async def live(schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Database]:
    monkeypatch.setenv("NEUROCODE_CLAUDE_HOME", str(tmp_path / "claude-home"))
    db = Database(url=schema)
    await _forget(db)
    async with db.session() as s:
        s.add(m.Project(id=PROJECT, name="Agent Loop"))
    yield db
    await _forget(db)
    await db.close()


async def _forget(db: Database) -> None:
    async with db.session() as s:
        await s.execute(delete(m.Chat).where(m.Chat.project_id == PROJECT))
        await s.execute(delete(m.CustomAgent).where(m.CustomAgent.project_id == PROJECT))
        await s.execute(delete(m.Project).where(m.Project.id == PROJECT))


async def _agent_session(db: Database, **agent: Any) -> str:
    async with db.session() as s:
        s.add(m.CustomAgent(id="ag-loop", project_id=PROJECT, name="Rounding Checker", role="checks rounding",
                            prompt="Only ever talk about rounding.", **agent))
        await s.flush()
        chats = ChatRepository(s)
        chat = await chats.add(m.Chat(id="loop-agent", ref="CHAT-9701", project_id=PROJECT, title="Ask",
                                      started_by="Rajat", status="thinking", agent="custom:ag-loop"))
        await chats.say(chat.id, role="you", body="is the total rounded?", by="Rajat")
    return "CHAT-9701"


async def test_an_agent_session_answers_with_its_prompt_its_lane_and_only_its_tools(live: Database):
    ref = await _agent_session(live, lane="mistral", tools=["search_memory"], max_steps=2)
    gateway = FakeGateway('{"tool": "project_summary", "arguments": {}}',
                          '{"tool": "search_memory", "arguments": {"query": "rounding"}}',
                          '{"answer": "Nothing is remembered about rounding."}')
    await think(live, gateway, ref, "Rajat")

    system = gateway.seen[0][0]["content"]
    assert system.startswith("You are Rounding Checker, checks rounding.\nOnly ever talk about rounding.")
    assert "- search_memory" in system and "- read_file" not in system and "web_fetch" not in system
    assert "at most 2 tools" in system
    assert gateway.lanes == ["mistral", "mistral", "mistral"]
    async with live.read() as s:
        turns = await ChatRepository(s).messages("loop-agent")
    tools = [(t.tool, t.ok, t.detail) for t in turns if t.role == "tool"]
    assert tools[0] == ("project_summary", False, "not this agent's")
    assert tools[1][0] == "search_memory" and tools[1][1] is True
    assert turns[-1].role == "assistant" and turns[-1].body == "Nothing is remembered about rounding."


async def test_an_agent_that_is_gone_ends_the_answer_in_words(live: Database):
    ref = await _agent_session(live)
    async with live.session() as s:
        await s.execute(delete(m.CustomAgent).where(m.CustomAgent.id == "ag-loop"))
    gateway = FakeGateway()
    await think(live, gateway, ref, "Rajat")
    assert gateway.seen == []                                     # no model was asked as anyone else
    async with live.read() as s:
        turns = await ChatRepository(s).messages("loop-agent")
        chat = await ChatRepository(s).by_ref(ref)
    assert turns[-1].role == "note" and "no longer there" in turns[-1].body and chat.status == "idle"


# ── plans and runs ───────────────────────────────────────────────
def test_the_compiler_is_offered_custom_agents_and_keeps_their_names():
    ctx = compiler.Context(project={"name": "Shop"}, facts=[],
                           agents=[{"name": "Ledger Auditor", "role": "rounding and tax"}])
    system, user = (x["content"] for x in compiler.messages("Round totals", ctx))
    assert "Ledger Auditor" in system and "- Ledger Auditor — rounding and tax" in user
    raw = json.dumps({"title": "Round", "businessRequirement": "b", "technicalRequirement": "t",
                      "steps": [{"label": "Fix rounding", "agent": "ledger auditor"},
                                {"label": "Invented", "agent": "Someone Else"}]})
    parsed = compiler.parse(raw, ["Ledger Auditor"])
    assert [s.agent for s in parsed.steps] == ["Ledger Auditor", "AI Commander"]
    assert compiler.parse(raw).steps[0].agent == "AI Commander"            # never offered: never an owner


async def test_a_person_may_give_a_plan_step_to_a_custom_agent(client: AsyncClient, session: AsyncSession):
    await client.post("/agents/custom", json=AUDITOR)
    session.add(m.Plan(id="p-own", ref="PLAN-9702", project_id="erp", status="draft", raw_requirement="Round"))
    await session.flush()
    session.add(m.PlanStep(id="p-own-1", plan_id="p-own", n=1, label="Round the total", agent="Backend Engineer"))
    await session.flush()

    changed = await client.patch("/plans/PLAN-9702/steps/p-own-1", json={"agent": "ledger auditor"})
    assert changed.status_code == 200
    assert changed.json()["steps"][0]["agent"] == "Ledger Auditor"          # the agent's own spelling
    nobody = await client.patch("/plans/PLAN-9702/steps/p-own-1", json={"agent": "Nobody"})
    assert nobody.status_code == 422 and "not an agent here" in nobody.json()["detail"]
    added = await client.post("/plans/PLAN-9702/steps", json={"label": "Audit it", "agent": "Ledger Auditor"})
    assert added.status_code == 201 and added.json()["steps"][-1]["agent"] == "Ledger Auditor"
    # One that may only read would be handed a step it must fail, so it is refused before the run.
    await client.post("/agents/custom", json={**AUDITOR, "name": "Ledger Reader", "tools": ["read_file"]})
    reader = await client.patch("/plans/PLAN-9702/steps/p-own-1", json={"agent": "Ledger Reader"})
    assert reader.status_code == 422 and "may write files" in reader.json()["detail"]


RUN_PROJECT, RUN_PLAN = "agent-run-project", "PLAN-9701"


@pytest_asyncio.fixture
async def workshop(schema: str, tmp_path: Path) -> AsyncIterator[Database]:
    repo = checkout(tmp_path / "ledger", {"pkg/core.py": "def total(x):\n    return x\n",
                                          "Makefile": f"test:\n\t{sys.executable} -c \"print('1 passed')\"\n"})
    db = Database(url=schema)
    await _forget_run(db)
    async with db.session() as s:
        s.add(m.Project(id=RUN_PROJECT, name="Agent Run", source_kind="local", source_repo=str(repo)))
        await s.flush()
        s.add(m.Plan(id="p-agent", ref=RUN_PLAN, project_id=RUN_PROJECT, status="draft",
                     raw_requirement="Round the total", affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(m.PlanStep(id="p-agent-1", plan_id="p-agent", n=1, label="Round the total", agent="Ledger Auditor"))
        s.add(m.Setting(key=f"runtime.tests.{RUN_PROJECT}", value="allowed"))
    yield db
    await _forget_run(db)
    await db.close()


async def _forget_run(db: Database) -> None:
    async with db.session() as s:
        for run in (await s.execute(select(m.Run).where(m.Run.project_id == RUN_PROJECT))).scalars().unique():
            if Path(run.repo).is_dir():
                runtime._cleanup(run)
        await s.execute(delete(m.Approval).where(m.Approval.project_id == RUN_PROJECT))
        await s.execute(delete(m.Run).where(m.Run.project_id == RUN_PROJECT))
        await s.execute(delete(m.Plan).where(m.Plan.project_id == RUN_PROJECT))
        await s.execute(delete(m.CustomAgent).where(m.CustomAgent.project_id == RUN_PROJECT))
        await s.execute(delete(m.Setting).where(m.Setting.key.like(f"runtime.%.{RUN_PROJECT}%")))
        await s.execute(delete(m.Project).where(m.Project.id == RUN_PROJECT))


async def _dispatch(db: Database, gateway: FakeGateway) -> str:
    async with db.session() as s:
        project = await s.get(m.Project, RUN_PROJECT)
        plan = (await s.execute(select(m.Plan).where(m.Plan.ref == RUN_PLAN))).scalar_one()
        return (await RunService(s, gateway).plan_runs(plan, None, project, "Rajat"))[-1].ref


async def test_a_step_a_custom_agent_owns_is_written_with_its_prompt_on_its_lane(workshop: Database):
    async with workshop.session() as s:
        s.add(m.CustomAgent(id="ag-run", project_id=RUN_PROJECT, name="Ledger Auditor", role="rounding",
                            prompt="Round money with round(x, 2).", lane="mistral", tools=["edit"]))
    gateway = FakeGateway(json.dumps({"summary": "Rounded.", "files": [
        {"path": "pkg/core.py", "content": "def total(x):\n    return round(x, 2)\n"}]}),
        json.dumps({"findings": [{"severity": "low", "file": "pkg/core.py", "line": "2-3", "note": "No test."}],
                    "verdict": "Fine."}))
    ref = await _dispatch(workshop, gateway)
    await execute(workshop, gateway, ref)

    async with workshop.read() as s:
        run = await RunRepository(s).by_ref(ref)
        logs = [x.line for x in await s.scalars(select(m.RunLog).where(m.RunLog.run_id == run.id))]
    assert run.lane == "mistral"                                  # the run keeps the lane its agent prefers
    edit = gateway.seen[0][0]["content"]
    assert edit.startswith("You are Ledger Auditor, rounding.\nRound money with round(x, 2).")
    assert "these rules of the runtime hold and win" in edit
    assert gateway.lanes[0] == "mistral"
    assert any(line.startswith("working as Ledger Auditor (project)") for line in logs)
    assert run.review["findings"] == [{"severity": "LOW", "file": "pkg/core.py", "line": 2, "note": "No test."}]


async def test_an_agent_without_edit_cannot_write_a_step(workshop: Database):
    async with workshop.session() as s:
        s.add(m.CustomAgent(id="ag-run", project_id=RUN_PROJECT, name="Ledger Auditor", role="reads only",
                            prompt="Read and report.", tools=["read_file"]))
    gateway = FakeGateway()
    ref = await _dispatch(workshop, gateway)
    await execute(workshop, gateway, ref)
    async with workshop.read() as s:
        run = await RunRepository(s).by_ref(ref)
    step = next(x for x in run.steps if x.n == 1)
    assert step.status == "failed" and "may not write files" in step.detail
    assert not any("You are Ledger Auditor" in str(p[0]["content"]) for p in gateway.seen)
