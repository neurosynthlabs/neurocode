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
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
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
from .deps import current_person, gateway, session, unseen_by

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
            "embed": gw.embed_lane(), "thinking": gw.thinking_levels()}


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
            "routes": routes_json(now["chains"], now["embed"], feature_lines, offline_reviews, now["thinking"])}


def _readable(hidden: Collection[str]) -> Any:
    """Every event but the ones belonging to a project this person may not see.

    A row with no project at all is the workspace's own story — someone signed in, a role changed —
    and belongs to everyone. `project_id NOT IN (…)` alone would drop those, because in SQL a null is
    not "not in" anything.
    """
    return ActivityEvent.project_id.is_(None) | ActivityEvent.project_id.not_in(sorted(hidden))


@router.get("/activity")
async def activity(limit: int = 200, offset: int = 0, who: Person = Depends(current_person),
                   open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The story of the workspace, newest first — of the part of it this person may read.

    A restricted project's lines are cut in the query rather than out of the answer, so a page of 200
    is 200 lines and not "200 minus the ones you cannot see", and `offset` keeps walking the same
    list it started on.

    Listed rather than paged: the answer is a plain array, so the COUNT(*) a page would run to fill in
    a total nobody reads is a full scan of the busiest table in the workspace on every poll.
    """
    hidden = await unseen_by(who, open_session)
    where = [_readable(hidden)] if hidden else []
    events = await ActivityRepository(open_session).list(
        *where, order_by=ActivityEvent.seq.desc(), limit=max(1, min(limit, FEED_CAP)), offset=offset)
    return [activity_json(event) for event in events]


async def _figures_without(open_session: AsyncSession, hidden: Collection[str], *, person: str,
                           day_start: datetime, top: int = 20) -> dict[str, Any]:
    """The log's figures, counted over what this person may read.

    `ActivityRepository.summary` counts every row, which is the right answer for everyone who may
    read every row. For somebody a project is closed to it is not: a total that includes lines they
    will never be shown is a number they cannot reconcile with the feed in front of them, and it
    tells them how busy a project they were never told about is. Same single pass, same grouping
    sets, one WHERE more — and only taken when something really is hidden.
    """
    e = ActivityEvent
    readable = _readable(hidden)
    rows = (await open_session.execute(select(
        e.actor_kind,
        func.count(),
        func.count().filter(e.at >= day_start),
        func.count().filter(e.actor_kind == "human", e.actor == person),
        func.coalesce(func.max(e.seq), 0),
    ).select_from(e).where(readable).group_by(text("GROUPING SETS ((), (actor_kind))")))).all()
    whole = next((r for r in rows if r[0] is None), (None, 0, 0, 0, 0))
    agents = (await open_session.execute(
        select(e.actor, func.count().label("n")).where(readable, e.actor_kind == "agent")
        .group_by(e.actor).order_by(func.count().desc(), e.actor).limit(top))).all()
    return {"total": int(whole[1]), "today": int(whole[2]), "mine": int(whole[3]),
            "through": int(whole[4]),
            "byKind": {str(r[0]): int(r[1]) for r in rows if r[0] is not None},
            "agents": [{"name": name, "events": int(n)} for name, n in agents]}


@router.get("/activity/summary")
async def activity_summary(who: Person = Depends(current_person),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The log's figures over every row this person may read, not over the newest page a screen holds.

    "Today" is the UTC day, the same for everyone who asks. `through` is the newest event counted, so a
    screen following the stream adds only events after it — and the stream drops what these figures
    leave out, so the two agree.
    """
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hidden = await unseen_by(who, open_session)
    figures = (await _figures_without(open_session, hidden, person=who.name, day_start=day_start)
               if hidden else
               await ActivityRepository(open_session).summary(person=who.name, day_start=day_start))
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
