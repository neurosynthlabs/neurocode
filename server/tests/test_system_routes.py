"""The system routes on the new stack: the roster, the feed, and the AI gateway's ledger.

What is checked is the contract — the paths, the JSON the screens are already written against, and
the status codes — plus the two places the port had to decide something. The roster's throughput is
counted from the work now rather than read off the agent's row, and the ledger has a lane where it
used to have a provider, so `offline` is the lane called `rules` and nothing else.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.data.base import utcnow
from app.data.loader import load_seed, sync_roles
from app.models import ActivityEvent, AiCall, Run
from app.services.identity import IdentityService

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
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


# ── the roster ───────────────────────────────────────────────────
async def test_the_roster_keeps_the_shape_the_card_reads(client: AsyncClient):
    roster = (await client.get("/agents")).json()
    assert len(roster) == 12
    assert [a["name"] for a in roster] == sorted(a["name"] for a in roster)

    one = next(a for a in roster if a["id"] == "backend")
    assert set(one) == {"id", "name", "role", "icon", "model", "fallbackModel", "status", "tools",
                        "skills", "autonomy", "tasksDone", "successRate", "avgMinutes", "tokens24h",
                        "cost24h", "systemPrompt", "guardrails"}
    assert isinstance(one["tools"], list) and isinstance(one["guardrails"], list)
    assert isinstance(one["cost24h"], float) and isinstance(one["tasksDone"], int)


async def test_an_agents_tasks_done_is_counted_not_stored(client: AsyncClient):
    """The sample workspace says the Backend Engineer has finished 502 tasks. The board says two."""
    roster = {a["id"]: a for a in (await client.get("/agents")).json()}
    board = (await client.get("/tasks", params={"limit": 500})).json()
    for agent_id, card in roster.items():
        done = sum(1 for t in board if t["status"] == "done" and agent_id in t["agents"])
        assert card["tasksDone"] == done

    assert roster["backend"]["tasksDone"] == 2
    assert roster["commander"]["tasksDone"] == 0          # it never takes a task; it hands them out


async def test_the_success_rate_and_duration_come_from_the_runs(client: AsyncClient, session: AsyncSession):
    started = utcnow() - timedelta(minutes=20)
    for status, minutes in (("done", 10), ("failed", 30), ("running", 0)):
        session.add(Run(id=f"run-{status}", ref=f"RUN-{status}", project_id="erp", status=status,
                        role="agent", agent="Backend Engineer", branch="b", worktree="w", repo="r",
                        created_at=started,
                        finished_at=started + timedelta(minutes=minutes) if minutes else None))
    await session.flush()

    roster = {a["id"]: a for a in (await client.get("/agents")).json()}
    assert roster["backend"]["successRate"] == 50         # one verdict in two went the right way
    assert roster["backend"]["avgMinutes"] == 20          # the run still going is not an average yet
    assert roster["frontend"]["successRate"] == 0         # no runs at all says nothing, not zero well


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


async def test_none_of_the_three_answers_anyone_who_is_not_signed_in(client: AsyncClient):
    await client.post("/auth/logout")
    for path in ("/agents", "/activity", "/usage"):
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
    from app.models import AiCall

    roster = (await client.get("/agents")).json()
    who = roster[0]
    session.add_all([
        AiCall(feature="agent", lane="groq", model="llama-3.3-70b-versatile", ok=True, ms=800,
               tokens_in=1200, tokens_out=3400, agent=who["name"]),
        AiCall(feature="review", lane="cerebras", model="qwen-3-coder-480b", ok=True, ms=500,
               tokens_in=400, tokens_out=900, agent=who["name"]),
        AiCall(feature="agent", lane="groq", model="llama-3.3-70b-versatile", ok=True, ms=700,
               tokens_in=100, tokens_out=200, agent="Somebody Else"),
    ])
    await session.flush()

    again = next(a for a in (await client.get("/agents")).json() if a["id"] == who["id"])
    assert again["tokens24h"] == 1200 + 3400 + 400 + 900
    assert again["cost24h"] == 0.0            # every lane it used is free, and that is the real price
    untouched = [a for a in (await client.get("/agents")).json() if a["id"] != who["id"]]
    assert all(a["tokens24h"] == 0 for a in untouched)
