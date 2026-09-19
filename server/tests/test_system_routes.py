"""The system routes on the new stack: the roster, the router, the feed, and the AI gateway's ledger.

What is checked is the contract — the paths, the JSON the screens read, and the status codes — plus the
places where a plausible answer would be a false one. An agent's status, lanes, record and current run
are derived from the runs, the router and the ledger; the roster row holds only what the catalogue
declares, and no stored status or model may come back. The router screen must say what the gateway really does, so the map of
feature to role is checked against the call sites themselves. And the ledger has a lane where it used
to have a provider, so `offline` is the lane called `rules` and nothing else.
"""
from __future__ import annotations

import ast
import threading
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
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
from app.data import catalogue
from app.data.base import utcnow
from app.models import ActivityEvent, AiCall, Run, RunStep, Task
from app.schemas.system import ROUTES
from app.secrets import Secrets
from app.services.identity import IdentityService
from app.services.runs import EDIT_SYSTEM, REVIEW_SYSTEM
from tests.fixtures.workspace import load_workspace, rows

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
    await load_workspace(session)
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
async def test_the_roster_is_the_catalogue_and_everything_else_is_derived(client: AsyncClient):
    """The old sample said every agent was `running` on a model no lane serves. The roster is written
    from the catalogue now, with no status or model on it: with no run anywhere, every agent is idle,
    and no model is in the answer at all."""
    answer = (await client.get("/agents")).json()
    assert set(answer) == {"agents", "lanesOpen", "worktreesOnDisk", "enforced"}
    cards = answer["agents"]
    assert [a["id"] for a in sorted(cards, key=lambda a: a["name"])] == \
        [a.id for a in sorted(catalogue.AGENTS, key=lambda a: a.name)] and [a["name"] for a in cards] == sorted(a["name"] for a in cards)
    assert {a["status"] for a in cards} == {"idle"}
    assert answer["worktreesOnDisk"] == 0 and answer["lanesOpen"] == 3 and answer["enforced"]

    one = next(a for a in cards if a["id"] == "backend")
    assert set(one) == {"id", "name", "role", "icon", "status", "tasksDone", "outcomes", "tokens24h", "cost24h",
                        "callsAs", "noCall", "lanes", "prompt", "current", "declared"}
    assert "model" not in one and "successRate" not in one
    assert set(one["declared"]) == {"autonomy", "tools", "skills", "guardrails", "systemPrompt"}
    assert one["outcomes"] == [] and one["current"] is None


async def test_an_agents_tasks_done_is_counted_not_stored(client: AsyncClient):
    """The old sample said the Backend Engineer had finished 502 tasks. The board is what counts."""
    cards = await roster(client)
    board = (await client.get("/tasks", params={"limit": 500})).json()
    for agent_id, card in cards.items():
        done = sum(1 for t in board if t["status"] == "done" and agent_id in t["agents"])
        assert card["tasksDone"] == done

    finished = rows("tasks", status="done")
    assert cards["backend"]["tasksDone"] == sum(1 for t in finished if "backend" in t["agents"]) > 0
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
async def test_a_lane_that_ran_a_model_its_price_is_not_for_has_no_known_cost_that_day(
        client: AsyncClient, session: AsyncSession):
    # groq is free for its catalogue model. The same lane pointed at another model has no price: the day's
    # cost is unknown, not $0. A call that used no tokens (a 429) cost nothing either way.
    session.add_all([
        AiCall(feature="agent", lane="groq", model="llama-3.3-70b-versatile", tokens_in=100, tokens_out=40),
        AiCall(feature="agent", lane="groq", model="a-paid-model-an-admin-chose", tokens_in=500, tokens_out=90),
        AiCall(feature="agent", lane="cerebras", model="a-refused-model", ok=False, error="429"),
    ])
    await session.flush()
    lanes_by_id = {lane["id"]: lane for lane in (await client.get("/models")).json()["lanes"]}
    assert lanes_by_id["groq"]["cost24h"] is None
    assert lanes_by_id["cerebras"]["cost24h"] == 0.0


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
    assert set(asked) == {"compile", "ask", "brainstorm", "extract", "agent", "review", "chat", "compact"}
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
    assert set(feed[0]) >= {"id", "t", "at", "actor", "actorKind", "action", "detail", "projectId", "level"}
    assert len(feed[0]["t"]) == len("14:21:05")
    at = datetime.fromisoformat(feed[0]["at"])               # the whole moment, with its zone
    assert at.tzinfo is not None and at.strftime("%H:%M:%S") == feed[0]["t"]


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


async def test_the_summary_counts_the_whole_log_not_the_page_a_screen_holds(client: AsyncClient,
                                                                           session: AsyncSession):
    me = (await client.get("/auth/me")).json()["user"]["name"]
    before = (await client.get("/activity/summary")).json()
    long_ago = utcnow() - timedelta(days=3)
    session.add_all(
        # More than the feed's ceiling, so a count taken from the feed would come out short.
        [ActivityEvent(actor="Orchestrator", actor_kind="system", action=f"Step {n}", detail="",
                       level="info", project_id="erp") for n in range(600)]
        + [ActivityEvent(actor="Summary Coder", actor_kind="agent", action="Wrote", detail="", level="ok")
           for _ in range(7)]
        + [ActivityEvent(actor="Summary Reviewer", actor_kind="agent", action="Read", detail="", level="ok")
           for _ in range(4)]
        + [ActivityEvent(actor=me, actor_kind="human", action="Moved", detail="", level="info"),
           ActivityEvent(actor=me, actor_kind="human", action="Moved", detail="", level="info", at=long_ago),
           ActivityEvent(actor="Someone Else", actor_kind="human", action="Moved", detail="", level="info"),
           # An agent that shares my name is not me.
           ActivityEvent(actor=me, actor_kind="agent", action="Echo", detail="", level="info")])
    await session.flush()

    after = (await client.get("/activity/summary")).json()
    assert after["total"] - before["total"] == 615
    assert after["today"] - before["today"] == 614            # the one from three days ago is not today
    assert after["mine"] - before["mine"] == 2                 # both of mine, whenever they were
    delta = {k: after["byKind"].get(k, 0) - before["byKind"].get(k, 0) for k in after["byKind"]}
    assert delta == {"system": 600, "agent": 12, "human": 3} | {
        k: 0 for k in after["byKind"] if k not in {"system", "agent", "human"}}
    agents = {a["name"]: a["events"] for a in after["agents"]}
    assert agents["Summary Coder"] == 7 and agents["Summary Reviewer"] == 4
    ranked = [a["events"] for a in after["agents"]]
    assert ranked == sorted(ranked, reverse=True)
    newest = (await client.get("/activity", params={"limit": 1})).json()[0]
    assert after["through"] == int(newest["id"])               # the stream adds only what comes after it
    day = datetime.fromisoformat(after["dayStart"])
    assert day.utcoffset() == timedelta(0) and (day.hour, day.minute, day.second) == (0, 0, 0)


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
    # `reason` is no lane in the catalogue, so its two calls have no price: the total is the rules' $0,
    # and it says it is a floor.
    assert report["totals"] == {"calls": 3, "modelCalls": 2, "offline": 1, "failures": 1,
                                "tokensIn": 200, "tokensOut": 100, "avgMs": 534,
                                "costUsd": 0.0, "costComplete": False}

    assert len(report["byDay"]) == 1                      # all three landed on the same day
    day = report["byDay"][0]
    assert date.fromisoformat(day.pop("day"))             # a real date, not a sliced timestamp
    assert day == {"calls": 3, "model": 2, "offline": 1, "tokens": 300, "costUsd": 0.0, "costComplete": False}

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


async def test_usage_is_priced_per_lane_and_grouped_by_agent_project_and_call(
        client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """Every dollar on the cost screen comes from here. DeepSeek is given a price for the test, because
    every lane in the catalogue is either free (priced at zero) or paid with no price declared, and a
    report that only ever sums zeros would pass whatever it multiplied."""
    # One price all day and no other models, so the sums below do not depend on the hour the test runs in.
    monkeypatch.setattr(lanes, "LANES", tuple(
        replace(x, usd_per_m_in=1.0, usd_per_m_out=2.0, off_peak=None, prices=()) if x.id == "deepseek" else x
        for x in lanes.LANES))
    task = (await session.execute(select(Task).where(Task.ref == "TASK-492"))).scalar_one()
    run = await a_run(session, "RUN-7", status="done", steps=[], agent="Backend Engineer", task_id=task.id)

    def line(**kw: object) -> AiCall:
        return AiCall(ok=True, ms=100, **kw)

    session.add_all([
        # Today: the backend agent by name on the priced lane, for a run in a project — $1 + $1 = $2.
        line(at=utcnow(), feature="agent", lane="deepseek", model="deepseek-flash", tokens_in=1_000_000,
             tokens_out=500_000, agent="Backend Engineer", project_id="erp", run_id=run.id),
        # Today: another agent on a lane nobody priced, in the same project.
        line(at=utcnow(), feature="review", lane="mystery", model="m", tokens_in=3000, tokens_out=1000,
             agent="reviewer", project_id="erp"),
        # Yesterday: the backend agent again, by id this time, on a free lane, for the workspace.
        line(at=utcnow() - timedelta(days=1), feature="agent", lane="groq", model="llama-3.3-70b-versatile",
             tokens_in=500,
             tokens_out=500, agent="backend"),
        # Yesterday: a person's call, answered by the rules, for the workspace.
        line(at=utcnow() - timedelta(days=1), feature="ask", lane="rules", model="", tokens_in=0, tokens_out=0),
        # Outside the week: counted nowhere below.
        line(at=utcnow() - timedelta(days=20), feature="agent", lane="deepseek", model="deepseek-flash",
             tokens_in=9_000_000, tokens_out=0, agent="Backend Engineer", project_id="erp"),
    ])
    await session.flush()

    report = (await client.get("/usage", params={"days": 7})).json()
    assert report["totals"]["calls"] == 4
    assert report["totals"]["costUsd"] == 2.0 and report["totals"]["costComplete"] is False

    yesterday, today = report["byDay"]
    assert (yesterday["calls"], yesterday["costUsd"], yesterday["costComplete"]) == (2, 0.0, True)
    assert (today["calls"], today["costUsd"], today["costComplete"]) == (2, 2.0, False)

    agents = {a["agent"]: a for a in report["byAgent"]}
    assert set(agents) == {"backend", "reviewer"}          # a person's call is not an agent's
    assert agents["backend"] == {"agent": "backend", "name": "Backend Engineer", "calls": 2,
                                 "tokensIn": 1_000_500, "tokensOut": 500_500, "costUsd": 2.0,
                                 "costComplete": True}
    # Every call it made was unpriced, so nothing is known — not "free".
    assert agents["reviewer"]["costUsd"] is None and agents["reviewer"]["costComplete"] is False
    assert agents["reviewer"]["name"] == "Code Reviewer"

    projects = {p["projectId"]: p for p in report["byProject"]}
    assert set(projects) == {"erp", None}
    assert projects["erp"] == {"projectId": "erp", "projectName": "Legacy ERP", "calls": 2,
                               "tokensIn": 1_003_000, "tokensOut": 501_000, "costUsd": 2.0,
                               "costComplete": False}
    assert projects[None] == {"projectId": None, "projectName": None, "calls": 2, "tokensIn": 500,
                              "tokensOut": 500, "costUsd": 0.0, "costComplete": True}

    costliest = report["costliest"]
    assert len(costliest) == 4
    top = costliest[0]
    assert date.fromisoformat(top.pop("at")[:10])
    assert top == {"lane": "deepseek", "model": "deepseek-flash", "feature": "agent", "agent": "backend",
                   "tokensIn": 1_000_000, "tokensOut": 500_000, "costUsd": 2.0, "runRef": "RUN-7",
                   "taskRef": "TASK-492"}
    # Free calls by size, and the one with no price last rather than guessed into place.
    assert [c["lane"] for c in costliest[1:]] == ["groq", "rules", "mystery"]
    assert costliest[-1]["costUsd"] is None and costliest[1]["agent"] == "backend"
    assert costliest[2]["agent"] is None and costliest[2]["runRef"] is None


async def test_a_free_lane_pointed_at_another_model_is_unpriced_not_free(client: AsyncClient,
                                                                         session: AsyncSession):
    """A lane's price is its catalogue model's. An admin (or NEUROCODE_OPENROUTER_MODEL) can point the
    free OpenRouter lane at a paid model, and those calls then have no known cost — not $0."""
    session.add_all([
        AiCall(feature="agent", lane="openrouter", model="anthropic/claude-sonnet-4", ok=True, ms=100,
               tokens_in=100_000, tokens_out=20_000, agent="Backend Engineer"),
        AiCall(feature="agent", lane="groq", model="llama-3.3-70b-versatile", ok=True, ms=100,
               tokens_in=10, tokens_out=10, agent="Backend Engineer"),
        AiCall(feature="embed", lane="gemini", model="text-embedding-004", ok=True, ms=100, tokens_in=10),
        AiCall(feature="chat", lane="ollama", model="llama3.1:8b", ok=True, ms=100, tokens_in=10),
    ])
    await session.flush()

    report = (await client.get("/usage", params={"days": 7})).json()
    assert report["totals"]["calls"] == 4
    assert report["totals"]["costUsd"] == 0.0 and report["totals"]["costComplete"] is False
    by_lane = {c["lane"]: c["costUsd"] for c in report["costliest"]}
    # The catalogue model, the lane's own embedding model, and any model on this machine are priced.
    assert by_lane["groq"] == 0.0 and by_lane["gemini"] == 0.0 and by_lane["ollama"] == 0.0
    assert by_lane["openrouter"] is None
    agent = next(a for a in report["byAgent"] if a["agent"] == "backend")
    assert agent["costUsd"] == 0.0 and agent["costComplete"] is False
    assert lanes.priced_call("openrouter", "deepseek/deepseek-chat-v3.1:free")
    assert not lanes.priced_call("openrouter", "anthropic/claude-sonnet-4")


async def test_the_costliest_list_is_capped_at_ten(client: AsyncClient, session: AsyncSession):
    session.add_all([AiCall(feature="ask", lane="groq", model="llama", ok=True, ms=10, tokens_in=n,
                            tokens_out=0) for n in range(1, 15)])
    await session.flush()
    costliest = (await client.get("/usage")).json()["costliest"]
    assert [c["tokensIn"] for c in costliest] == list(range(14, 4, -1))


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
    for path in ("/agents", "/activity", "/activity/summary", "/usage", "/models"):
        refused = await client.get(path)
        assert refused.status_code == 401 and refused.json()["detail"] == "Sign in to continue."


async def test_an_empty_ledger_answers_with_zeroes_rather_than_nothing(client: AsyncClient):
    report = (await client.get("/usage")).json()
    assert report["totals"] == {"calls": 0, "modelCalls": 0, "offline": 0, "failures": 0,
                                "tokensIn": 0, "tokensOut": 0, "avgMs": 0, "costUsd": 0.0, "costComplete": True}
    assert report["byDay"] == [] and report["byFeature"] == [] and report["byProvider"] == []
    assert report["recent"] == [] and report["byPerson"] == []
    assert report["byAgent"] == [] and report["byProject"] == [] and report["costliest"] == []


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


async def test_a_paid_lane_with_no_price_is_unpriced_not_free(client: AsyncClient, session: AsyncSession,
                                                               monkeypatch: pytest.MonkeyPatch):
    """A paid lane that declares no price: its $0 was a missing number rendered as "free". DeepSeek
    declares its prices now, so they are taken away here to keep the rule under test."""
    monkeypatch.setattr(lanes, "LANES", tuple(
        replace(x, usd_per_m_in=0.0, usd_per_m_out=0.0, usd_per_m_cached=None, prices=()) if x.id == "deepseek"
        else x for x in lanes.LANES))
    session.add(AiCall(feature="agent", lane="deepseek", model="deepseek-flash", ms=500,
                       tokens_in=1000, tokens_out=500, agent="Backend Engineer"))
    await session.flush()

    report = (await client.get("/models")).json()
    deepseek = next(lane for lane in report["lanes"] if lane["id"] == "deepseek")
    assert deepseek["priced"] is False and deepseek["cost24h"] is None
    assert report["totals24h"]["costComplete"] is False

    agent = next(a for a in (await client.get("/agents")).json()["agents"] if a["name"] == "Backend Engineer")
    assert agent["cost24h"] is None and agent["tokens24h"] == 1500


# ── cache, the clock, and reasoning ──────────────────────────────
def _at(peak: bool) -> datetime:
    """A moment in the last six days inside DeepSeek's peak (a weekday, 02:30 UTC) or outside it (12:30)."""
    now = datetime.now(UTC)
    for back in range(1, 7):
        day = now - timedelta(days=back)
        if day.isoweekday() <= 5:
            return day.replace(hour=2 if peak else 12, minute=30, second=0, microsecond=0)
    raise AssertionError("a week always has a weekday")


async def test_a_cache_hit_is_priced_as_one_and_off_peak_costs_half(client: AsyncClient, session: AsyncSession):
    """DeepSeek's published prices (api-docs.deepseek.com/quick_start/pricing): flash $0.30 a million
    fresh input tokens at peak, $0.006 cached, $1.20 out; everything half off-peak. A million input
    tokens of which 400k were cached, and 100k out:
    peak = 0.6 × 0.30 + 0.4 × 0.006 + 0.1 × 1.20 = 0.3024, and the cache saved 0.4 × (0.30 − 0.006) = 0.1176."""
    session.add_all([
        AiCall(at=_at(peak=True), feature="chat", lane="deepseek", model="deepseek-flash", ok=True, ms=10,
               tokens_in=1_000_000, tokens_cached=400_000, tokens_out=100_000, tokens_reasoning=60_000),
        AiCall(at=_at(peak=False), feature="compile", lane="deepseek", model="deepseek-flash", ok=True, ms=10,
               tokens_in=1_000_000, tokens_cached=400_000, tokens_out=100_000, tokens_reasoning=0),
        AiCall(at=_at(peak=True), feature="chat", lane="groq", model="llama-3.3-70b-versatile", ok=True, ms=10,
               tokens_in=1000, tokens_out=10),
        AiCall(at=_at(peak=True), feature="compile", lane="rules", model="offline planner", ok=True, ms=1),
    ])
    await session.flush()

    report = (await client.get("/usage", params={"days": 7})).json()
    assert report["totals"]["costUsd"] == round(0.3024 + 0.1512, 4) and report["totals"]["costComplete"]
    cache = report["cache"]
    totals = cache["totals"]
    assert (totals["calls"], totals["tokensIn"], totals["tokensCached"]) == (3, 2_001_000, 800_000)
    assert totals["tokensReasoning"] == 60_000 and totals["savedUsd"] == round(0.1176 + 0.0588, 4)
    assert totals["cacheShare"] == round(800_000 / 2_001_000, 4)

    lanes_ = {line["lane"]: line for line in cache["byLane"]}
    assert set(lanes_) == {"deepseek", "groq"}                    # the rules read no prompt
    assert lanes_["deepseek"]["costUsd"] == round(0.3024 + 0.1512, 4) and lanes_["groq"]["savedUsd"] == 0.0
    assert lanes_["groq"]["cacheShare"] == 0.0
    features = {line["feature"]: line for line in cache["byFeature"]}
    assert features["chat"]["tokensReasoning"] == 60_000 and features["chat"]["savedUsd"] == 0.1176
    assert features["compile"]["costUsd"] == 0.1512 and features["compile"]["savedUsd"] == 0.0588


async def test_a_retired_or_unpriced_model_on_the_paid_lane_is_unknown_not_cheap(client: AsyncClient,
                                                                                 session: AsyncSession):
    session.add(AiCall(feature="chat", lane="deepseek", model="deepseek-chat", ok=True, ms=10,
                       tokens_in=1000, tokens_cached=500, tokens_out=100))
    await session.flush()
    cache = (await client.get("/usage", params={"days": 7})).json()["cache"]
    line = cache["byLane"][0]
    assert line["costUsd"] is None and line["savedUsd"] is None and line["costComplete"] is False


async def test_the_router_shows_each_lanes_window_retirement_and_cache(client: AsyncClient, session: AsyncSession,
                                                                     lane_gateway: Gateway):
    lane_gateway.store.save_setting("ai.lane.deepseek", {"model": "deepseek-chat"})
    session.add(AiCall(feature="chat", lane="deepseek", model="deepseek-flash", ok=True, ms=10,
                       tokens_in=2000, tokens_cached=1000, tokens_out=100, tokens_reasoning=40))
    await session.flush()

    report = (await client.get("/models")).json()
    deepseek = next(lane for lane in report["lanes"] if lane["id"] == "deepseek")
    assert deepseek["model"] == "deepseek-chat" and "retired" in deepseek["retired"]
    assert deepseek["window"] is None                    # the window is the flash model's, not this one's
    assert deepseek["models"] == ["deepseek-flash", "deepseek-v4-pro"] and deepseek["thinks"] == "deepseek"
    assert deepseek["offPeak"]["factor"] == 0.5
    assert (deepseek["tokensCached24h"], deepseek["tokensReasoning24h"]) == (1000, 40)
    assert deepseek["cost24h"] is not None and deepseek["saved24h"] > 0
    groq = next(lane for lane in report["lanes"] if lane["id"] == "groq")
    assert groq["window"] == 131_072 and groq["retired"] is None and groq["thinks"] is None

    admin = (await client.get("/admin/ai")).json()
    assert "retired" in admin["deepseek"]["retired"]


async def test_thinking_is_set_per_feature_by_an_admin_and_shown_on_the_router(client: AsyncClient,
                                                                               session: AsyncSession,
                                                                               lane_gateway: Gateway):
    routes = {r["feature"]: r for r in (await client.get("/models")).json()["routes"]}
    assert (routes["compile"]["thinking"], routes["agent"]["thinking"], routes["chat"]["thinking"]) == \
        ("high", "off", "low")
    assert routes["compact"]["role"] == "chat" and routes["embed"]["thinking"] is None

    saved = await client.put("/admin/ai", json={"thinking": {"agent": "high", "chat": "off"}})
    assert saved.status_code == 200
    assert lane_gateway.thinking("agent") == "high" and lane_gateway.thinking("chat") == "off"
    routes = {r["feature"]: r for r in (await client.get("/models")).json()["routes"]}
    assert routes["agent"]["thinking"] == "high" and routes["agent"]["thinkingDefault"] == "off"

    assert (await client.put("/admin/ai", json={"thinking": {"agent": "default"}})).status_code == 200
    assert lane_gateway.thinking("agent") == "off"
    assert (await client.put("/admin/ai", json={"thinking": {"agent": "turbo"}})).status_code == 400
    assert (await client.put("/admin/ai", json={"thinking": {"nope": "low"}})).status_code == 400
    assert lane_gateway.thinking("chat") == "off"            # a refused change changes nothing

    refused = await client.put("/admin/ai", json={"thinking": {"chat": "high"}}, headers=await engineer(session))
    assert refused.status_code == 403
