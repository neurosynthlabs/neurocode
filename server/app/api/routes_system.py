"""The system screens: who the agents are, what has happened, and what the models have been asked.

Two of the old file's routes are not here. Liveness is answered by the application itself, and the
live feed is served by `stream.py`; both moved when the bus did.

What is left is read-only, and the answers are now the database's work rather than Python's. The
roster is derived from the runs, the steps and the ledger instead of read off stored sample text, and
the router screen is the gateway's own view of its lanes beside a day of its ledger. The usage report
is six aggregates over the ledger, so it costs the same on a year of calls as on a day of them — it
used to be six scans on a worker thread, because SQLite could not be asked anything without blocking.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import lanes
from ..ai.gateway import Gateway
from ..ai.lanes import REVIEW, WRITE
from ..data.base import utcnow
from ..models import ActivityEvent
from ..repositories.base import MAX_LIMIT
from ..repositories.usage import AgentRepository, InFlight, UsageRepository
from ..repositories.work import ActivityRepository
from ..schemas.system import (
    ENFORCED,
    ORDERING,
    PREFERENCE_TEXT,
    agent_json,
    fleet_json,
    pick_run,
    routes_json,
    totals_json,
    usage_json,
)
from ..schemas.work import activity_json
from ..services.identity import Person
from .deps import current_person, gateway, session

router = APIRouter()

#: The most events the feed hands out in one answer. It is MAX_LIMIT and not the old stack's 1000
#: because the repository's ceiling is the real one and no caller may raise it — a route that
#: advertised 1000 and returned 500 was telling the caller something untrue. `offset` walks past it.
FEED_CAP = MAX_LIMIT
#: The longest window the usage screen may ask for, and what it asks for when it says nothing.
MAX_DAYS, DEFAULT_DAYS = 365, 30


def _roster_lanes(gw: Gateway) -> dict[str, Any]:
    """What the router would try now for the two roles agents work in, and how many lanes are open.
    Blocking: every lane's allowance is read from the ledger and Ollama is asked whether it is up."""
    return {"chains": {WRITE: gw.chain(role=WRITE, limit=2), REVIEW: gw.chain(role=REVIEW, limit=2)},
            "open": len(gw.chain(limit=len(lanes.IDS)))}


def _on_disk(paths: list[str]) -> int:
    return sum(1 for path in paths if Path(path).is_dir())


@router.get("/agents", dependencies=[Depends(current_person)])
async def agents(open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """The roster, derived: a fixed number of queries for the whole list, never one per agent."""
    repo, ledger = AgentRepository(open_session), UsageRepository(open_session)
    roster = await repo.all_ordered()
    finished, records, flights = await repo.tasks_finished(), await repo.step_record(), await repo.in_flight()
    current = await repo.current_runs(sorted({f.run_id for f in flights}))
    spent = await ledger.spend_24h()
    answered = await ledger.last_answered([n for a in roster for n in (a.name, a.id)])
    kept = await repo.kept_worktrees()
    router_now = await asyncio.to_thread(_roster_lanes, gw)

    by_agent: dict[str, list[InFlight]] = {}
    for flight in flights:
        by_agent.setdefault(flight.agent_id, []).append(flight)
    out: list[dict[str, Any]] = []
    for agent in roster:
        mine = by_agent.get(agent.id, [])
        run_id = pick_run(mine)
        # The ledger names an agent the way the roster does — by name where a run had one, by id
        # otherwise — so both are looked up rather than assuming which the runtime wrote.
        tokens, cost = spent.get(agent.name) or spent.get(agent.id) or (0, 0.0)
        out.append(agent_json(
            agent, tasks_done=finished.get(agent.id, 0), records=[r for r in records if r.agent_id == agent.id],
            flights=mine, current=current.get(run_id) if run_id else None, chains=router_now["chains"],
            answered=answered.get(agent.name) or answered.get(agent.id), tokens_24h=tokens, cost_24h=cost))
    return {"agents": out, "lanesOpen": router_now["open"],
            "worktreesOnDisk": await asyncio.to_thread(_on_disk, kept), "enforced": list(ENFORCED)}


def _router(gw: Gateway) -> dict[str, Any]:
    """Everything the gateway knows about its lanes, in one hop to a worker thread: the ledger and
    Ollama are both asked, and neither may hold up the event loop."""
    return {"preference": gw.preference(), "locked": bool(os.environ.get("NEUROCODE_COMPILER")),
            "active": gw.status(), "report": gw.report(), "catalogue": gw.lanes(),
            # With the chain's own default limit — the one gateway.run and gateway.ask walk. Drawn longer,
            # the screen promised fallbacks a real call gives up before reaching.
            "chains": {role: gw.chain(role=role) for role in (None, *lanes.ROLES)},
            "embed": gw.embed_lane()}


@router.get("/models", dependencies=[Depends(current_person)])
async def models(open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """The lanes, what each feature asks them for, and their last day, from the gateway and its ledger.

    Open to anyone signed in: which lane answered and with which model is already on Runs and Sessions.
    What is not — a key's mask, where it came from, a lane's address — is removed here, not hidden in
    the screen.
    """
    ledger = UsageRepository(open_session)
    lane_lines, feature_lines = await ledger.by_lane(1), await ledger.by_feature(1)
    offline_reviews = await ledger.offline_reviews_24h()
    now = await asyncio.to_thread(_router, gw)
    fleet = fleet_json(now["report"], now["catalogue"], lane_lines)
    return {"preference": now["preference"], "preferenceLocked": now["locked"], "active": now["active"],
            "ordering": ORDERING, "preferences": PREFERENCE_TEXT, "lanes": fleet,
            "totals24h": totals_json(fleet, lane_lines),
            "routes": routes_json(now["chains"], now["embed"], feature_lines, offline_reviews)}


@router.get("/activity", dependencies=[Depends(current_person)])
async def activity(limit: int = 200, offset: int = 0,
                   open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The story of the workspace, newest first.

    Listed rather than paged: the answer is a plain array, so the COUNT(*) a page would run to fill in
    a total nobody reads is a full scan of the busiest table in the workspace on every poll.
    """
    events = await ActivityRepository(open_session).list(
        order_by=ActivityEvent.seq.desc(), limit=max(1, min(limit, FEED_CAP)), offset=offset)
    return [activity_json(event) for event in events]


@router.get("/activity/summary")
async def activity_summary(who: Person = Depends(current_person),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The log's figures over every row, not over the newest page a screen holds.

    "Today" is the UTC day, the same for everyone who asks. `through` is the newest event counted, so a
    screen following the stream adds only events after it.
    """
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    figures = await ActivityRepository(open_session).summary(person=who.name, day_start=day_start)
    return {**figures, "dayStart": day_start.isoformat()}


@router.get("/usage")
async def usage(days: int = DEFAULT_DAYS, who: Person = Depends(current_person),
                open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """What the AI gateway did, from its ledger: every model call and every offline answer.

    Which lane answered is the whole story now — the one called `rules` never left the machine, and
    every other one reached a model. Who made a call is an admin's business; everyone else reads the
    same totals with the names left out.
    """
    admin = who.can("workspace:admin")
    window = max(1, min(days, MAX_DAYS))
    return usage_json(await UsageRepository(open_session).report(window, admin=admin), admin=admin)
