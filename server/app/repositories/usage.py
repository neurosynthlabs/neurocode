"""What has already happened, counted where it lives.

Two ledgers, really. `ai_calls` holds one row per model call and per offline answer, so the usage
screen is six aggregates rather than a scan — a year of calls costs no more to summarise than a week
of them. And an agent's throughput is counted from the tasks it finished and the runs it made, because
a roster that *stores* "418 tasks done" is a roster that is wrong by tomorrow.

Two things about the ledger changed when it moved to Postgres, and both are visible here. There is no
`provider` column any more: a call belongs to a lane, the lane called `rules` is the one that answered
offline, and every other lane reached a model. And `at` is a real `timestamptz`, so a day is
`date_trunc('day', at)` rather than the first ten characters of a string, and a window is an interval
the database works out rather than a date this process formatted from its own clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, extract, func, select, text

from ..models import Agent, AiCall, Run, Task, TaskAgent, User
from .base import MAX_LIMIT, Repository, bounded

#: The lane that answers from rules alone. Every other lane went to a model.
OFFLINE = "rules"
#: How many calls the screen's "recent" strip shows.
RECENT = 25
#: The run statuses that are a verdict on the agent. One still going says nothing about it yet, and
#: one a person cancelled says something about the person.
DECIDED = ("done", "failed")
#: What an unnamed caller is called, exactly as the old ledger called it.
NOBODY = "Nobody signed in"


def _int(value: object) -> int:
    """An aggregate over no rows comes back as None, and Postgres counts in Decimal."""
    return int(value or 0)


def _ms(value: object) -> int:
    """Milliseconds, as a whole number — an average of integers is not one."""
    return round(float(value or 0))


# ── what a ledger question answers with ──────────────────────────
@dataclass(slots=True)
class Totals:
    calls: int
    model_calls: int
    offline: int
    failures: int
    tokens_in: int
    tokens_out: int
    avg_ms: int


@dataclass(slots=True)
class DayLine:
    day: str
    calls: int
    model: int
    offline: int
    tokens: int


@dataclass(slots=True)
class FeatureLine:
    feature: str
    calls: int
    model: int
    offline: int
    failures: int
    tokens_in: int
    tokens_out: int
    avg_ms: int


@dataclass(slots=True)
class LaneLine:
    lane: str
    model: str
    calls: int
    failures: int
    tokens_in: int
    tokens_out: int
    avg_ms: int


@dataclass(slots=True)
class CallLine:
    at: datetime
    feature: str
    lane: str
    model: str
    ok: bool
    ms: int
    tokens_in: int
    tokens_out: int
    error: str
    by: str | None


@dataclass(slots=True)
class PersonLine:
    name: str
    calls: int
    tokens: int


@dataclass(slots=True)
class Usage:
    """The whole report. `by_person` is None for anyone who may not see who made a call."""

    days: int
    totals: Totals
    by_day: list[DayLine]
    by_feature: list[FeatureLine]
    by_lane: list[LaneLine]
    recent: list[CallLine]
    by_person: list[PersonLine] | None


class UsageRepository(Repository[AiCall]):
    model = AiCall

    # ── the window ───────────────────────────────────────────────
    def _within(self, days: int) -> ColumnElement[bool]:
        """The last `days` days, measured by the database's clock rather than by this process's.

        Two machines in two zones must agree on where the window starts, and the only clock they
        share is the one the rows were written against.
        """
        return AiCall.at >= text("now() - make_interval(days => :days)").bindparams(days=days)

    # ── the aggregates, one statement each ───────────────────────
    async def totals(self, days: int) -> Totals:
        stmt = select(
            func.count(),
            func.count().filter(AiCall.lane != OFFLINE),
            func.count().filter(AiCall.lane == OFFLINE),
            func.count().filter(AiCall.ok.is_(False)),
            func.coalesce(func.sum(AiCall.tokens_in), 0),
            func.coalesce(func.sum(AiCall.tokens_out), 0),
            func.coalesce(func.avg(AiCall.ms), 0),
        ).where(self._within(days))
        calls, model_calls, offline, failures, tokens_in, tokens_out, ms = (
            await self.session.execute(stmt)).one()
        return Totals(calls=_int(calls), model_calls=_int(model_calls), offline=_int(offline),
                      failures=_int(failures), tokens_in=_int(tokens_in), tokens_out=_int(tokens_out),
                      avg_ms=_ms(ms))

    async def by_day(self, days: int) -> list[DayLine]:
        day = func.date_trunc("day", AiCall.at)
        stmt = (select(func.to_char(day, "YYYY-MM-DD"), func.count(),
                       func.count().filter(AiCall.lane != OFFLINE),
                       func.count().filter(AiCall.lane == OFFLINE),
                       func.coalesce(func.sum(AiCall.tokens_in + AiCall.tokens_out), 0))
                # Newest first, then turned back around: ordered ascending and capped, the row that
                # fell off the end was *today* whenever the window held a day more than `days`.
                .where(self._within(days)).group_by(day).order_by(day.desc()).limit(bounded(days)))
        rows = (await self.session.execute(stmt)).all()
        return [DayLine(day=d, calls=_int(c), model=_int(m), offline=_int(o), tokens=_int(t))
                for d, c, m, o, t in reversed(rows)]

    async def by_feature(self, days: int) -> list[FeatureLine]:
        calls = func.count()
        stmt = (select(AiCall.feature, calls,
                       func.count().filter(AiCall.lane != OFFLINE),
                       func.count().filter(AiCall.lane == OFFLINE),
                       func.count().filter(AiCall.ok.is_(False)),
                       func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0),
                       func.coalesce(func.avg(AiCall.ms), 0))
                .where(self._within(days)).group_by(AiCall.feature)
                .order_by(calls.desc(), AiCall.feature).limit(MAX_LIMIT))
        return [FeatureLine(feature=f, calls=_int(c), model=_int(m), offline=_int(o), failures=_int(x),
                            tokens_in=_int(ti), tokens_out=_int(to), avg_ms=_ms(ms))
                for f, c, m, o, x, ti, to, ms in (await self.session.execute(stmt)).all()]

    async def by_lane(self, days: int) -> list[LaneLine]:
        """A lane and the model it reached. The old ledger called the first half of that a provider."""
        calls = func.count()
        stmt = (select(AiCall.lane, AiCall.model, calls,
                       func.count().filter(AiCall.ok.is_(False)),
                       func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0),
                       func.coalesce(func.avg(AiCall.ms), 0))
                .where(self._within(days)).group_by(AiCall.lane, AiCall.model)
                .order_by(calls.desc(), AiCall.lane, AiCall.model).limit(MAX_LIMIT))
        return [LaneLine(lane=lane, model=model, calls=_int(c), failures=_int(x), tokens_in=_int(ti),
                         tokens_out=_int(to), avg_ms=_ms(ms))
                for lane, model, c, x, ti, to, ms in (await self.session.execute(stmt)).all()]

    async def recent_calls(self, *, limit: int = RECENT) -> list[CallLine]:
        """The newest calls, whenever they happened — this strip is not inside the window, and never
        was: it is there to show what the gateway is doing right now, even on a quiet month."""
        stmt = (select(AiCall.at, AiCall.feature, AiCall.lane, AiCall.model, AiCall.ok, AiCall.ms,
                       AiCall.tokens_in, AiCall.tokens_out, AiCall.error, User.name)
                .join(User, User.id == AiCall.user_id, isouter=True)
                .order_by(AiCall.id.desc()).limit(bounded(limit)))
        return [CallLine(at=at, feature=f, lane=lane, model=model, ok=bool(ok), ms=_int(ms),
                         tokens_in=_int(ti), tokens_out=_int(to), error=error, by=name)
                for at, f, lane, model, ok, ms, ti, to, error, name
                in (await self.session.execute(stmt)).all()]

    async def by_person(self, days: int) -> list[PersonLine]:
        """Who spent what. Grouped by the account rather than by the name, so two people who happen
        to share one are still two rows — and the calls nobody was signed in for are their own."""
        calls = func.count()
        stmt = (select(func.coalesce(User.name, NOBODY), calls,
                       func.coalesce(func.sum(AiCall.tokens_in + AiCall.tokens_out), 0))
                .join(User, User.id == AiCall.user_id, isouter=True)
                .where(self._within(days)).group_by(AiCall.user_id, User.name)
                .order_by(calls.desc(), func.coalesce(User.name, NOBODY)).limit(MAX_LIMIT))
        return [PersonLine(name=name, calls=_int(c), tokens=_int(t))
                for name, c, t in (await self.session.execute(stmt)).all()]

    async def spend_24h(self) -> dict[str, tuple[int, float]]:
        """agent → tokens it has spent in the last day, and what those cost.

        These two were a hardcoded 0 on every card, which reads as a measurement rather than as the
        absence of one. The ledger records which agent asked now, so this is simply a sum — and the
        cost is the lanes' own declared price, which for every free lane in the catalogue is really
        zero. A zero here means "free", not "we did not look".
        """
        since = func.now() - text("interval '24 hours'")
        tokens = func.coalesce(func.sum(AiCall.tokens_in + AiCall.tokens_out), 0)
        stmt = (select(AiCall.agent, AiCall.lane,
                       func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0), tokens)
                .where(AiCall.at >= since, AiCall.agent != "")
                .group_by(AiCall.agent, AiCall.lane).limit(MAX_LIMIT))

        from ..ai.lanes import price_of
        out: dict[str, tuple[int, float]] = {}
        for who, lane, tin, tout, total in (await self.session.execute(stmt)).all():
            spent, cost = out.get(who, (0, 0.0))
            per_in, per_out = price_of(lane)
            out[who] = (spent + _int(total),
                        cost + _int(tin) / 1e6 * per_in + _int(tout) / 1e6 * per_out)
        return {who: (n, round(cost, 4)) for who, (n, cost) in out.items()}

    async def report(self, days: int, *, admin: bool) -> Usage:
        """Everything the usage screen shows. Who made a call is an admin's business, so for anyone
        else that question is not asked at all rather than asked and thrown away."""
        return Usage(days=days, totals=await self.totals(days), by_day=await self.by_day(days),
                     by_feature=await self.by_feature(days), by_lane=await self.by_lane(days),
                     recent=await self.recent_calls(),
                     by_person=await self.by_person(days) if admin else None)


class AgentRepository(Repository[Agent]):
    """The roster, and the two questions its card used to answer from stored numbers."""

    model = Agent

    async def all_ordered(self) -> list[Agent]:
        # By name, because nothing on the row records the order the roster was written in, and an
        # order that comes out of the storage engine is an order that changes under you.
        return await self.list(order_by=Agent.name, limit=200)

    async def tasks_finished(self) -> dict[str, int]:
        """agent id → how many finished tasks carry it. One GROUP BY for the whole roster.

        A task names its agents the way the plan that made it did — "Backend Engineer" — while the
        sample workspace names them by id. Both spellings are really in that column, so both are
        matched and the answer is keyed by the agent instead.
        """
        stmt = (select(Agent.id, func.count())
                .select_from(Agent)
                .join(TaskAgent, TaskAgent.agent.in_((Agent.id, Agent.name)))
                .join(Task, Task.id == TaskAgent.task_id)
                .where(Task.status == "done")
                .group_by(Agent.id).limit(MAX_LIMIT))
        return {agent_id: _int(n) for agent_id, n in (await self.session.execute(stmt)).all()}

    async def run_record(self) -> dict[str, tuple[int, int]]:
        """agent id → how often its runs end well, as a percentage, and how long one takes in minutes.

        Only runs that reached a verdict count towards the rate; dividing by NULL rather than by zero
        is what lets an agent that has never finished a run come back as nothing at all.
        """
        decided = func.count().filter(Run.status.in_(DECIDED))
        rate = func.round(100.0 * func.count().filter(Run.status == "done") / func.nullif(decided, 0))
        # Only the runs that ran to a verdict: a cancelled run's elapsed time measures how long
        # somebody took to stop it, which is not how long this agent's work takes.
        minutes = func.round(func.avg(extract("epoch", Run.finished_at - Run.created_at))
                             .filter(Run.status.in_(DECIDED)) / 60.0)
        stmt = (select(Agent.id, rate, minutes)
                .select_from(Agent)
                .join(Run, Run.agent.in_((Agent.id, Agent.name)))
                .group_by(Agent.id).limit(MAX_LIMIT))
        return {agent_id: (_int(r), _int(m))
                for agent_id, r, m in (await self.session.execute(stmt)).all()}
