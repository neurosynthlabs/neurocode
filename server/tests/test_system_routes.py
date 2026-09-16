"""The system routes on the new stack: the roster, the router, the feed, and the AI gateway's ledger.

What is checked is the contract — the paths, the JSON the screens read, and the status codes — plus the
places where a plausible answer would be a false one. An agent's status, lanes, record and current run
are derived from the runs, the router and the ledger; the seed's stored status and model are sample
text and must never come back. The router screen must say what the gateway really does, so the map of
feature to role is checked against the call sites themselves. And the ledger has a lane where it used
to have a provider, so `offline` is the lane called `rules` and nothing else.
"""
from __future__ import annotations

import ast
import threading
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import lanes
from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.data.base import utcnow
from app.data.loader import load_seed, sync_roles
from app.models import ActivityEvent, AiCall, Run, RunStep, Task
from app.schemas.system import ROUTES
from app.secrets import Secrets
from app.services.identity import IdentityService
from app.services.runs import EDIT_SYSTEM, REVIEW_SYSTEM

SERVER = Path(__file__).resolve().parent.parent
OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest.fixture
def lane_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gateway:
    """Three free lanes with keys, and Ollama switched off so no test depends on this Mac running it.

    Every lane's environment variable is taken away first: a machine that exports GROQ_API_KEY would
    otherwise open a lane here that is closed everywhere else.
    """
    monkeypatch.delenv("NEUROCODE_COMPILER", raising=False)
    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    secrets = Secrets(tmp_path / "secrets.json")
    for name in ("groq_api_key", "cerebras_api_key", "gemini_api_key"):
        secrets.set(name, "test-key")
    return Gateway(MemoryLedger({"ai.lane.ollama": {"enabled": False}}), secrets)


@pytest_asyncio.fixture
async def client(session: AsyncSession, lane_gateway: Gateway) -> AsyncIterator[AsyncClient]:
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: lane_gateway
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)           # an Owner holds every permission
        yield c


async def engineer(session: AsyncSession) -> dict[str, str]:
    """An Engineer's bearer token. Signed in, but not an admin — the distinction /usage turns on."""
    identity = IdentityService(session)
    who = await identity.create("dev@example.com", "Dev", "another long password", ["engineer"])
    return {"Authorization": f"Bearer {await identity.start_session(who.id)}"}


async def call(session: AsyncSession, *, feature: str = "compile", lane: str = "reason",
               model: str = "deepseek-v3", ok: bool = True, ms: int = 400, tokens_in: int = 100,
               tokens_out: int = 50, user_id: str | None = None, ago: timedelta = timedelta(0),
               error: str = "") -> AiCall:
    row = AiCall(at=utcnow() - ago, feature=feature, lane=lane, model=model, ok=ok, ms=ms,
                 tokens_in=tokens_in, tokens_out=tokens_out, user_id=user_id, error=error)
    session.add(row)
    await session.flush()
    return row


async def a_run(session: AsyncSession, ref: str, *, status: str, steps: list[tuple[str, str, str]],
                role: str = "solo", agent: str | None = None, task_id: str | None = None,
                review_by: str = "", worktree: str = "w", removed: bool = False, files: int = 0) -> Run:
    """A run and its steps — (kind, agent, status) each. Flushed between the two, because the models
    carry a plain foreign key and SQLAlchemy orders inserts by relationships alone."""
    run = Run(id=f"run-{ref}", ref=ref, project_id="erp", status=status, role=role, agent=agent,
              task_id=task_id, branch=f"neurocode/{ref.lower()}", worktree=worktree, repo="r",
              review={"findings": [], "verdict": "", "by": review_by}, removed=removed, diff_files=files)
    session.add(run)
    await session.flush()
    session.add_all([RunStep(run_id=run.id, n=n, kind=kind, label=f"{kind} {n}", agent=who, status=state,
                             ms=60_000 * n if state in ("done", "failed") else None)
                     for n, (kind, who, state) in enumerate(steps, start=1)])
    await session.flush()
    return run


async def roster(client: AsyncClient) -> dict[str, dict]:
    answer = await client.get("/agents")
    assert answer.status_code == 200
    return {a["id"]: a for a in answer.json()["agents"]}


# ── the roster ───────────────────────────────────────────────────
async def test_the_roster_is_derived_and_never_echoes_the_seed(client: AsyncClient):
    """Every seeded agent says `running` and names a model no lane serves. With no run anywhere, all
    of them are idle, and the stored model is not in the answer at all."""
    answer = (await client.get("/agents")).json()
    assert set(answer) == {"agents", "lanesOpen", "worktreesOnDisk", "enforced"}
    cards = answer["agents"]
    assert len(cards) == 12 and [a["name"] for a in cards] == sorted(a["name"] for a in cards)
    assert {a["status"] for a in cards} == {"idle"}
    assert answer["worktreesOnDisk"] == 0 and answer["lanesOpen"] == 3 and answer["enforced"]

    one = next(a for a in cards if a["id"] == "backend")
    assert set(one) == {"id", "name", "role", "icon", "status", "tasksDone", "outcomes", "tokens24h", "cost24h",
                        "callsAs", "noCall", "lanes", "prompt", "current", "declared"}
    assert "model" not in one and "successRate" not in one
    assert set(one["declared"]) == {"autonomy", "tools", "skills", "guardrails", "systemPrompt"}
    assert one["outcomes"] == [] and one["current"] is None


async def test_an_agents_tasks_done_is_counted_not_stored(client: AsyncClient):
    """The sample workspace says the Backend Engineer has finished 502 tasks. The board says two."""
    cards = await roster(client)
    board = (await client.get("/tasks", params={"limit": 500})).json()
    for agent_id, card in cards.items():
        done = sum(1 for t in board if t["status"] == "done" and agent_id in t["agents"])
        assert card["tasksDone"] == done

    assert cards["backend"]["tasksDone"] == 2
    assert cards["commander"]["tasksDone"] == 0          # it never takes a task; it hands them out


async def test_status_and_the_current_run_come_from_the_runs(client: AsyncClient, session: AsyncSession):
    task = (await session.execute(select(Task).where(Task.id == "t492"))).scalar_one()
    live = await a_run(session, "RUN-7", status="running", task_id=task.id, files=3, steps=[
        ("edit", "Backend Engineer", "done"), ("edit", "Backend Engineer", "running"),
        ("test", "QA Engineer", "todo"), ("review", "Code Reviewer", "todo"), ("handoff", "You", "todo")])
    parked = await a_run(session, "RUN-8", status="waiting", steps=[
        ("edit", "Frontend Engineer", "done"), ("test", "QA Engineer", "waiting")])
    await a_run(session, "RUN-9", status="done", steps=[("edit", "Database Engineer", "done")])
    session.add_all([
        AiCall(feature="agent", lane="groq", model="m", tokens_in=1000, tokens_out=400, agent="Backend Engineer",
               run_id=live.id),
        AiCall(feature="agent", lane="groq", model="m", tokens_in=7, tokens_out=3, agent="Backend Engineer",
               run_id=live.id),
        # The same agent, in another run: not this run's tokens, however recent.
        AiCall(feature="agent", lane="groq", model="m", tokens_in=9999, tokens_out=9999, agent="Backend Engineer",
               run_id=parked.id),
    ])
    await session.flush()

    cards = await roster(client)
    backend = cards["backend"]
    assert backend["status"] == "running"
    assert backend["current"] == {
        "runRef": "RUN-7", "taskRef": task.ref, "status": "running", "branch": "neurocode/run-7", "worktree": "w",
        "startedAt": backend["current"]["startedAt"], "progress": 20, "step": "edit 2", "stepKind": "edit",
        "stepStatus": "running", "filesChanged": 3, "tokensIn": 1007, "tokensOut": 403}
    assert cards["qa"]["status"] == "waiting"                        # parked on its first-time approval
    assert cards["qa"]["current"]["runRef"] == "RUN-8"
    assert cards["qa"]["current"]["stepKind"] == "test"
    assert cards["reviewer"]["status"] == "idle"                     # its step in RUN-7 has not come up
    assert cards["database"]["status"] == "idle"                     # its run is over
    assert cards["frontend"]["status"] == "idle"                     # its step is done; QA holds the run


async def test_an_agent_of_a_parallel_batch_is_running_between_its_steps(client: AsyncClient,
                                                                        session: AsyncSession):
    await a_run(session, "RUN-11", status="running", role="agent", agent="Frontend Engineer",
                steps=[("edit", "Frontend Engineer", "done"), ("edit", "Frontend Engineer", "todo")])
    card = (await roster(client))["frontend"]
    assert card["status"] == "running" and card["current"]["progress"] == 50
    assert card["current"]["step"] == "edit 2" and card["current"]["tokensIn"] == 0


async def test_a_record_says_what_each_kind_of_step_really_measures(client: AsyncClient, session: AsyncSession):
    """A review step cannot fail — the rules stand in — so its rate is how often a model really read the
    diff. A test step's verdict is the project's tests, not the QA Engineer's work."""
    await a_run(session, "RUN-1", status="done", review_by="qwen-3-coder-480b", steps=[
        ("edit", "Backend Engineer", "done"), ("test", "QA Engineer", "done"), ("review", "Code Reviewer", "done")])
    await a_run(session, "RUN-2", status="failed", review_by="offline rules", steps=[
        ("edit", "Backend Engineer", "failed"), ("test", "QA Engineer", "failed"),
        ("review", "Code Reviewer", "done")])
    await a_run(session, "RUN-3", status="done", steps=[
        ("edit", "Backend Engineer", "skipped"), ("review", "Code Reviewer", "skipped")])

    cards = await roster(client)
    assert cards["reviewer"]["outcomes"] == [
        {"kind": "review", "label": "model-read reviews", "decided": 2, "good": 1, "rate": 50, "avgMinutes": 3}]
    assert cards["qa"]["outcomes"] == [
        {"kind": "test", "label": "project tests passed", "decided": 2, "good": 1, "rate": 50, "avgMinutes": 2}]
    edits = cards["backend"]["outcomes"]
    assert edits == [{"kind": "edit", "label": "edits written", "decided": 2, "good": 1, "rate": 50,
                      "avgMinutes": 1}]                             # the skipped edit is no verdict
    assert cards["frontend"]["outcomes"] == []                      # nothing done says nothing, not 0%


async def test_lanes_and_prompt_are_the_runtimes_not_the_rows(client: AsyncClient, session: AsyncSession):
    session.add(AiCall(feature="review", lane="gemini", model="gemini-2.5-flash", agent="Code Reviewer"))
    session.add(AiCall(feature="review", lane="cerebras", model="qwen", ok=False, agent="Code Reviewer"))
    await session.flush()
    cards = await roster(client)

    writer = cards["backend"]
    assert writer["callsAs"] == "write" and writer["noCall"] is None
    assert writer["lanes"]["primary"] == {"lane": "groq", "model": "llama-3.3-70b-versatile"}
    assert writer["lanes"]["fallback"] == {"lane": "cerebras", "model": "qwen-3-coder-480b"}
    assert writer["prompt"]["system"] == EDIT_SYSTEM
    assert writer["prompt"]["user"].startswith("You are the Backend Engineer.")
    assert writer["prompt"]["system"] != writer["declared"]["systemPrompt"]

    reviewer = cards["reviewer"]
    assert reviewer["callsAs"] == "review" and reviewer["prompt"]["system"] == REVIEW_SYSTEM
    assert [reviewer["lanes"]["primary"]["lane"], reviewer["lanes"]["fallback"]["lane"]] == ["cerebras", "gemini"]
    last = reviewer["lanes"]["lastAnswered"]
    assert last["lane"] == "gemini" and last["model"] == "gemini-2.5-flash"   # the failed call answered nothing

    for quiet in ("commander", "qa"):                                # no model call is ever made as them
        assert cards[quiet]["callsAs"] is None and cards[quiet]["prompt"] is None
        assert cards[quiet]["lanes"]["primary"] is None and cards[quiet]["noCall"]


async def test_with_no_lane_open_nobody_has_a_primary(client: AsyncClient, lane_gateway: Gateway):
    for name in ("groq_api_key", "cerebras_api_key", "gemini_api_key"):
        lane_gateway.secrets.set(name, None)
    answer = (await client.get("/agents")).json()
    assert answer["lanesOpen"] == 0
    assert all(a["lanes"]["primary"] is None for a in answer["agents"])


async def test_worktrees_on_disk_are_counted_on_the_disk(client: AsyncClient, session: AsyncSession,
                                                         tmp_path: Path):
    """A stopped run keeps its worktree for you to look at; a discarded one does not; a run that failed
    before its worktree opened never had one."""
    kept, discarded = tmp_path / "RUN-21", tmp_path / "RUN-22"
    kept.mkdir()
    discarded.mkdir()
    await a_run(session, "RUN-21", status="cancelled", worktree=str(kept), steps=[])
    await a_run(session, "RUN-22", status="done", worktree=str(discarded), removed=True, steps=[])
    await a_run(session, "RUN-23", status="failed", worktree=str(tmp_path / "never-opened"), steps=[])
    assert (await client.get("/agents")).json()["worktreesOnDisk"] == 1


async def test_the_router_is_asked_off_the_event_loop(client: AsyncClient, lane_gateway: Gateway,
                                                      monkeypatch: pytest.MonkeyPatch):
    """The gateway reads the ledger and pings Ollama, both blocking. On the loop's own thread, one slow
    Ollama would stall every other request."""
    loop_thread, seen = threading.get_ident(), []
    real = lane_gateway.chain

    def watched(*args, **kwargs):
        seen.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(lane_gateway, "chain", watched)
    await client.get("/agents")
    await client.get("/models")
    assert seen and loop_thread not in seen


# ── the router ───────────────────────────────────────────────────
async def test_the_router_answers_with_its_lanes_and_no_key_material(client: AsyncClient, session: AsyncSession):
    session.add_all([
        AiCall(feature="agent", lane="groq", model="llama-3.3-70b-versatile", ms=300, tokens_in=100, tokens_out=40),
        AiCall(feature="agent", lane="groq", model="an-older-model", ms=100, ok=False, error="429"),
        AiCall(feature="compile", lane="rules", model="offline planner", ms=2),
        AiCall(feature="chat", lane="ollama", model="qwen2.5-coder:7b", ms=900, tokens_in=10, tokens_out=5),
        AiCall(feature="compile", lane="groq", model="llama-3.3-70b-versatile", at=utcnow() - timedelta(days=2)),
    ])
    await a_run(session, "RUN-31", status="waiting", review_by="offline rules", steps=[])
    await a_run(session, "RUN-32", status="done", review_by="gemini-2.5-flash", steps=[])
    await session.flush()

    report = (await client.get("/models")).json()
    assert set(report) == {"preference", "preferenceLocked", "active", "ordering", "preferences", "lanes",
                           "totals24h", "routes"}
    assert report["preference"] == "auto" and report["preferenceLocked"] is False
    assert [lane["id"] for lane in report["lanes"]] == list(lanes.IDS)
    for lane in report["lanes"]:
        assert not {"keyMask", "keySource", "baseUrl", "signup"} & set(lane)

    groq = next(lane for lane in report["lanes"] if lane["id"] == "groq")
    assert groq["ready"] is True and groq["hosting"] == "remote" and groq["embed"] is None
    assert (groq["calls24h"], groq["failures24h"], groq["avgMs24h"]) == (2, 1, 200)   # both models, one lane
    assert (groq["tokensIn24h"], groq["tokensOut24h"], groq["cost24h"]) == (100, 40, 0.0)
    ollama = next(lane for lane in report["lanes"] if lane["id"] == "ollama")
    assert ollama["hosting"] == "local" and ollama["blocked"] == "switched off"
    assert ollama["embed"] == "nomic-embed-text"

    assert report["totals24h"] == {"calls": 4, "local": 1, "offline": 1, "remote": 2, "failures": 1,
                                   "tokensIn": 110, "tokensOut": 45, "costUsd": 0.0, "costComplete": True}

    routes = {r["feature"]: r for r in report["routes"]}
    assert [c["lane"] for c in routes["agent"]["chain"]] == ["groq", "cerebras", "gemini"]
    assert [c["lane"] for c in routes["review"]["chain"]] == ["cerebras", "gemini", "groq"]
    assert routes["review"]["avoidsWriter"] and routes["review"]["offline"]
    assert routes["review"]["offline24h"] == 1                     # from the run, since no ledger line exists
    assert routes["compile"]["offline24h"] == 1 and routes["compile"]["calls24h"] == 1
    assert routes["agent"]["calls24h"] == 2 and routes["agent"]["failures24h"] == 1
    assert [c["lane"] for c in routes["embed"]["chain"]] == ["gemini"]
    assert routes["embed"]["chain"][0]["model"] == "text-embedding-004"


async def test_anyone_signed_in_reads_the_router_and_nobody_else(client: AsyncClient, session: AsyncSession):
    theirs = await client.get("/models", headers=await engineer(session))
    assert theirs.status_code == 200 and theirs.json()["lanes"]
    await client.post("/auth/logout")
    assert (await client.get("/models")).status_code == 401


def _asked_roles() -> dict[str, str | None]:
    """feature → the role its call site passes to the gateway, read from the source itself."""
    names = {"WRITE": "write", "REVIEW": "review", "PLAN": "plan", "CHAT": "chat"}
    found: dict[str, str | None] = {}
    for rel in ("app/ai/compiler.py", "app/ai/features.py", "app/services/runs.py", "app/services/chat.py"):
        for node in ast.walk(ast.parse((SERVER / rel).read_text())):
            if not isinstance(node, ast.Call):
                continue
            words = {k.arg: k.value for k in node.keywords if k.arg}
            feature = words.get("feature")
            if isinstance(feature, ast.Constant) and isinstance(feature.value, str):
                role = words.get("role")
                found[feature.value] = names[role.id] if isinstance(role, ast.Name) else None
    return found


def test_the_route_map_is_what_the_call_sites_really_ask_for():
    """The screen's map of feature → role is a claim about code elsewhere. Compile asks for no role at
    all, whatever a plan might suggest, and this is what catches the day one of them changes."""
    asked = _asked_roles()
    assert set(asked) == {"compile", "ask", "brainstorm", "extract", "agent", "review", "chat"}
    for route in ROUTES:
        if route.feature in asked:
            assert route.role == asked[route.feature], route.feature


def test_the_prompt_preambles_are_the_ones_the_runtime_writes():
    source = (SERVER / "app/services/runs.py").read_text()
    assert 'f"You are the {step.agent}.\\nProject: {run.project_id}\\n"' in source
    assert 'f"Requirement: {run.requirement}\\nStep {step.n}: {step.label}\\n"' in source
    assert 'f"Requirement: {requirement}\\n\\nDiff:\\n{diff}"' in source


# ── the feed ─────────────────────────────────────────────────────
async def test_the_feed_comes_back_in_the_shape_the_log_reads(client: AsyncClient):
    feed = (await client.get("/activity")).json()
    assert feed
    assert set(feed[0]) >= {"id", "t", "actor", "actorKind", "action", "detail", "projectId", "level"}
    assert len(feed[0]["t"]) == len("14:21:05")


async def test_the_feed_is_paged_and_no_limit_gets_past_the_ceiling(client: AsyncClient,
                                                                    session: AsyncSession):
    session.add_all([ActivityEvent(actor="Orchestrator", actor_kind="system", action=f"Step {n}",
                                   detail="", level="info", project_id="erp") for n in range(600)])
    await session.flush()

    assert len((await client.get("/activity", params={"limit": 3})).json()) == 3
    assert (await client.get("/activity", params={"limit": 0})).status_code == 200

    most = (await client.get("/activity", params={"limit": 1000})).json()
    assert len(most) == 500                               # the repository's ceiling, not the caller's
    second = (await client.get("/activity", params={"limit": 3, "offset": 3})).json()
    assert [e["id"] for e in second] == [e["id"] for e in most[3:6]]


# ── the ledger ───────────────────────────────────────────────────
async def test_usage_answers_in_the_shape_the_screen_was_written_against(client: AsyncClient,
                                                                         session: AsyncSession):
    me = (await client.get("/auth/me")).json()["user"]["id"]
    await call(session, feature="compile", lane="reason", model="deepseek-v3", ms=400, user_id=me)
    await call(session, feature="compile", lane="rules", model="", ms=2, tokens_in=0, tokens_out=0)
    await call(session, feature="ask", lane="reason", model="deepseek-v3", ok=False, ms=1200,
               error="upstream said no", user_id=me)

    report = (await client.get("/usage", params={"days": 7})).json()
    assert report["days"] == 7
    assert report["totals"] == {"calls": 3, "modelCalls": 2, "offline": 1, "failures": 1,
                                "tokensIn": 200, "tokensOut": 100, "avgMs": 534}

    assert len(report["byDay"]) == 1                      # all three landed on the same day
    day = report["byDay"][0]
    assert date.fromisoformat(day.pop("day"))             # a real date, not a sliced timestamp
    assert day == {"calls": 3, "model": 2, "offline": 1, "tokens": 300}

    compile_line = next(f for f in report["byFeature"] if f["feature"] == "compile")
    assert compile_line == {"feature": "compile", "calls": 2, "model": 1, "offline": 1, "failures": 0,
                            "tokensIn": 100, "tokensOut": 50, "avgMs": 201}

    reason = next(p for p in report["byProvider"] if p["provider"] == "reason")
    assert reason == {"provider": "reason", "model": "deepseek-v3", "calls": 2, "failures": 1,
                      "tokensIn": 200, "tokensOut": 100, "avgMs": 800}
    assert any(p["provider"] == "rules" for p in report["byProvider"])

    newest = report["recent"][0]
    assert set(newest) == {"at", "feature", "provider", "model", "ok", "ms", "tokensIn", "tokensOut",
                           "error", "by"}
    assert newest["feature"] == "ask" and newest["ok"] is False
    assert newest["error"] == "upstream said no" and newest["by"] == "Rajat"


async def test_the_window_bounds_the_totals_but_not_the_recent_strip(client: AsyncClient,
                                                                    session: AsyncSession):
    """The edge that would actually bite: a call from last month still belongs in "what is it doing
    right now", and must not be added into a week's totals."""
    await call(session, feature="ask", ago=timedelta(days=40))
    await call(session, feature="compile")

    week = (await client.get("/usage", params={"days": 7})).json()
    assert week["totals"]["calls"] == 1 and [f["feature"] for f in week["byFeature"]] == ["compile"]
    assert {c["feature"] for c in week["recent"]} == {"ask", "compile"}

    year = (await client.get("/usage", params={"days": 365})).json()
    assert year["totals"]["calls"] == 2 and len(year["byDay"]) == 2

    assert (await client.get("/usage", params={"days": 9999})).json()["days"] == 365
    assert (await client.get("/usage", params={"days": -3})).json()["days"] == 1


async def test_only_an_admin_is_told_who_made_a_call(client: AsyncClient, session: AsyncSession):
    me = (await client.get("/auth/me")).json()["user"]["id"]
    await call(session, user_id=me)
    await call(session, user_id=None)

    mine = (await client.get("/usage")).json()
    assert {p["name"] for p in mine["byPerson"]} == {"Rajat", "Nobody signed in"}
    assert sum(p["calls"] for p in mine["byPerson"]) == 2
    assert next(p for p in mine["byPerson"] if p["name"] == "Rajat")["tokens"] == 150
    assert any(c["by"] == "Rajat" for c in mine["recent"])

    theirs = (await client.get("/usage", headers=await engineer(session))).json()
    assert "byPerson" not in theirs
    assert all(c["by"] is None for c in theirs["recent"])
    assert theirs["totals"] == mine["totals"]             # the numbers are nobody's secret


async def test_none_of_them_answers_anyone_who_is_not_signed_in(client: AsyncClient):
    await client.post("/auth/logout")
    for path in ("/agents", "/activity", "/usage", "/models"):
        refused = await client.get(path)
        assert refused.status_code == 401 and refused.json()["detail"] == "Sign in to continue."


async def test_an_empty_ledger_answers_with_zeroes_rather_than_nothing(client: AsyncClient):
    report = (await client.get("/usage")).json()
    assert report["totals"] == {"calls": 0, "modelCalls": 0, "offline": 0, "failures": 0,
                                "tokensIn": 0, "tokensOut": 0, "avgMs": 0}
    assert report["byDay"] == [] and report["byFeature"] == [] and report["byProvider"] == []
    assert report["recent"] == [] and report["byPerson"] == []


async def test_an_agents_spend_is_summed_from_the_ledger(client: AsyncClient, session: AsyncSession):
    """Both figures were a hardcoded 0, which on a card reads as a measurement rather than a gap."""
    who = (await client.get("/agents")).json()["agents"][0]
    session.add_all([
        AiCall(feature="agent", lane="groq", model="llama-3.3-70b-versatile", ok=True, ms=800,
               tokens_in=1200, tokens_out=3400, agent=who["name"]),
        AiCall(feature="review", lane="cerebras", model="qwen-3-coder-480b", ok=True, ms=500,
               tokens_in=400, tokens_out=900, agent=who["name"]),
        AiCall(feature="agent", lane="groq", model="llama-3.3-70b-versatile", ok=True, ms=700,
               tokens_in=100, tokens_out=200, agent="Somebody Else"),
    ])
    await session.flush()

    cards = (await client.get("/agents")).json()["agents"]
    again = next(a for a in cards if a["id"] == who["id"])
    assert again["tokens24h"] == 1200 + 3400 + 400 + 900
    assert again["cost24h"] == 0.0            # every lane it used is free, and that is the real price
    untouched = [a for a in cards if a["id"] != who["id"]]
    assert all(a["tokens24h"] == 0 for a in untouched)



async def test_the_chain_on_screen_is_the_chain_a_call_walks(client: AsyncClient, lane_gateway: Gateway):
    """Drawn with every open lane, the screen promised fallbacks that gateway.run and gateway.ask never
    reach: they stop at the chain's default length and hand the call to the rules."""
    for name in ("mistral_api_key", "openrouter_api_key"):
        lane_gateway.secrets.set(name, "test-key")
    open_lanes = [x for x in lane_gateway.lanes() if lane_gateway.why_not(x) is None]
    assert len(open_lanes) >= 5

    routes = {r["feature"]: r for r in (await client.get("/models")).json()["routes"]}
    walked = [x.id for x in lane_gateway.chain(role="write")]
    assert [c["lane"] for c in routes["agent"]["chain"]] == walked and len(walked) < len(open_lanes)


async def test_a_paid_lane_with_no_price_is_unpriced_not_free(client: AsyncClient, session: AsyncSession):
    """DeepSeek is paid and declares no price, so its $0 was a missing number rendered as "free"."""
    session.add(AiCall(feature="agent", lane="deepseek", model="deepseek-chat", ms=500,
                       tokens_in=1000, tokens_out=500, agent="Backend Engineer"))
    await session.flush()

    report = (await client.get("/models")).json()
    deepseek = next(lane for lane in report["lanes"] if lane["id"] == "deepseek")
    assert deepseek["priced"] is False and deepseek["cost24h"] is None
    assert report["totals24h"]["costComplete"] is False

    agent = next(a for a in (await client.get("/agents")).json()["agents"] if a["name"] == "Backend Engineer")
    assert agent["cost24h"] is None and agent["tokens24h"] == 1500
