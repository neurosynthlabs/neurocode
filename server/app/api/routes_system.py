"""The system screens: who the agents are, what has happened, and what the models have been asked.

Two of the old file's routes are not here. Liveness is answered by the application itself, and the
live feed is served by `stream.py`; both moved when the bus did.

What is left is read-only, and all three answers are now the database's work rather than Python's. The
roster's throughput is counted from the tasks and the runs instead of read off a stored number that
drifted. The usage report is six aggregates over the ledger, so it costs the same on a year of calls
as on a day of them — it used to be six scans on a worker thread, because SQLite could not be asked
anything without blocking.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ActivityEvent
from ..repositories.base import MAX_LIMIT
from ..repositories.usage import AgentRepository, UsageRepository
from ..repositories.work import ActivityRepository
from ..schemas.system import agent_json, usage_json
from ..schemas.work import activity_json
from ..services.identity import Person
from .deps import current_person, session

router = APIRouter()

#: The most events the feed hands out in one answer. It is MAX_LIMIT and not the old stack's 1000
#: because the repository's ceiling is the real one and no caller may raise it — a route that
#: advertised 1000 and returned 500 was telling the caller something untrue. `offset` walks past it.
FEED_CAP = MAX_LIMIT
#: The longest window the usage screen may ask for, and what it asks for when it says nothing.
MAX_DAYS, DEFAULT_DAYS = 365, 30


@router.get("/agents", dependencies=[Depends(current_person)])
async def agents(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The roster, with its throughput counted from the work — two GROUP BYs for the whole list."""
    repo = AgentRepository(open_session)
    roster = await repo.all_ordered()
    finished, record = await repo.tasks_finished(), await repo.run_record()
    spent = await UsageRepository(open_session).spend_24h()
    out: list[dict[str, Any]] = []
    for agent in roster:
        rate, minutes = record.get(agent.id, (0, 0))
        # The ledger names an agent the way the roster does — by name where a run had one, by id
        # otherwise — so both are looked up rather than assuming which the runtime wrote.
        tokens, cost = spent.get(agent.name) or spent.get(agent.id) or (0, 0.0)
        out.append(agent_json(agent, tasks_done=finished.get(agent.id, 0), success_rate=rate,
                              avg_minutes=minutes, tokens_24h=tokens, cost_24h=cost))
    return out


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
