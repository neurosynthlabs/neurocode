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

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, Float, and_, case, cast, func, literal, null, select, text

from ..data import roster
from ..models import Agent, AiCall, Project, Run, RunStep, Task, TaskAgent, User
from .base import MAX_LIMIT, Repository, bounded

if TYPE_CHECKING:
    from ..ai.lanes import Price

#: The lane that answers from rules alone. Every other lane went to a model.
OFFLINE = "rules"
#: How many calls the screen's "recent" strip shows.
RECENT = 25
#: How many calls the "costliest" list holds.
COSTLIEST = 10
#: The run statuses that are a verdict on the agent. One still going says nothing about it yet, and
#: one a person cancelled says something about the person.
DECIDED = ("done", "failed")
#: What an unnamed caller is called, exactly as the old ledger called it.
NOBODY = "Nobody signed in"
#: What a run's review records as its reviewer when no model read the diff and the rules did.
OFFLINE_REVIEW = "offline rules"
#: The step kinds an agent does itself. A merge is git's work and a handoff is yours.
RECORDED_KINDS = ("edit", "test", "review")
#: A run, or a step, that somebody is working or waiting on.
LIVE = ("running", "waiting")
#: A step that will not run again, the same three the Runs screen counts towards progress.
FINISHED_STEPS = ("done", "skipped", "failed")


def _int(value: object) -> int:
    """An aggregate over no rows comes back as None, and Postgres counts in Decimal."""
    return int(value or 0)


def _ms(value: object) -> int:
    """Milliseconds, as a whole number — an average of integers is not one."""
    return round(float(value or 0))


def _rate(lane: str) -> ColumnElement[float]:
    """The share of the full price a call on this lane paid, by the hour it was made in (UTC): 1 in the
    provider's peak hours, its off-peak factor otherwise, and 1 all day on a lane with one price."""
    from ..ai.lanes import off_peak

    rule = off_peak(lane)
    if rule is None:
        return literal(1.0, Float)
    utc = func.timezone("UTC", AiCall.at)
    peak = and_(cast(func.extract("isodow", utc), Float).in_([float(d) for d in rule.weekdays]),
                cast(func.extract("hour", utc), Float).in_([float(h) for h in rule.hours]))
    return case((peak, literal(1.0, Float)), else_=literal(rule.factor, Float))


def _priced(value: Callable[[Price], ColumnElement[float]]
            ) -> list[tuple[ColumnElement[bool], ColumnElement[float]]]:
    """One CASE over every lane and model with a price: what a call cost, or what its cache saved.

    `value(price)` is the expression for one priced model at full rate; it is multiplied by the hour's
    rate here. A free lane is zero for the models its price holds for; a call on anything else is NULL."""
    from ..ai.lanes import IDS, price_table, priced, priced_models

    whens = []
    for lane in (*IDS, OFFLINE):
        if not priced(lane):
            continue
        table = price_table(lane)
        if table:
            rate = _rate(lane)
            for model, price in table.items():
                whens.append((and_(AiCall.lane == lane, AiCall.model == model), value(price) * rate))
            continue
        models = priced_models(lane)
        which = AiCall.lane == lane if models is None else and_(AiCall.lane == lane, AiCall.model.in_(models))
        whens.append((which, literal(0.0, Float)))
    return whens


def _cost() -> ColumnElement[float | None]:
    """What one ledger line cost in US dollars, worked out by the database, or NULL when its lane
    declares no price, or declares one for a model other than the one the call ran on.

    The prices are the lanes' own (`lanes.price_table`), and "known" is `lanes.priced_call`, written
    into the statement as a CASE, so a month of calls is summed and sorted in Postgres rather than
    carried back here a row at a time. The model is in the condition because a lane's price is per
    model: a free lane an admin pointed at a paid model must not sum to $0. An input token the provider
    served from its cache is billed at the cached price, and a call in a provider's off-peak hours at
    its off-peak rate — both only what the provider reported and published. Built on every call rather
    than once, because the catalogue is Python and a test that changes a price must see it.
    """
    per_million = cast(literal(1e6), Float)
    fresh = cast(AiCall.tokens_in - AiCall.tokens_cached, Float)
    cached = cast(AiCall.tokens_cached, Float)
    out = cast(AiCall.tokens_out, Float)
    whens = _priced(lambda p: (fresh * literal(p.per_m_in, Float) + cached * literal(p.per_m_cached, Float)
                               + out * literal(p.per_m_out, Float)) / per_million)
    # A call that used no tokens — refused, rate-limited, timed out before an answer — cost nothing on any
    # lane, so it is priced at zero rather than leaving the day's total unknown.
    return case((AiCall.tokens_in + AiCall.tokens_out == 0, literal(0.0, Float)), *whens, else_=null())


def _saved() -> ColumnElement[float | None]:
    """What the provider's prompt cache took off one call: its cached tokens at the fresh price, less
    what they cost cached. Zero on a free lane and with no cache hit; NULL where the cost is unknown."""
    per_million = cast(literal(1e6), Float)
    cached = cast(AiCall.tokens_cached, Float)
    whens = _priced(lambda p: cached * literal(p.per_m_in - p.per_m_cached, Float) / per_million)
    return case((AiCall.tokens_cached == 0, literal(0.0, Float)), *whens, else_=null())


def _dollars(total: object, calls: int, unpriced: int) -> float | None:
    """A sum of known costs. When every call in it ran on an unpriced lane nothing is known, and the
    answer is None rather than a zero that would read as "free"; when only some did, it is a floor,
    and `unpriced` is what says so."""
    if calls and unpriced >= calls:
        return None
    return round(float(total or 0), 4)


def _agent_key() -> ColumnElement[str]:
    """The ledger names an agent by name where a run had one and by id otherwise, so both spellings
    are folded onto the roster id inside the statement. A name the roster does not know stays as it
    was written: the ledger outlives a rename."""
    return case(roster.IDS_BY_NAME, value=AiCall.agent, else_=AiCall.agent)


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
    cost_usd: float | None
    #: How many calls ran on a lane with no declared price. Any at all makes `cost_usd` a floor.
    unpriced: int


@dataclass(slots=True)
class DayLine:
    day: str
    calls: int
    model: int
    offline: int
    tokens: int
    cost_usd: float | None
    unpriced: int


@dataclass(slots=True)
class AgentSpend:
    agent: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    unpriced: int


@dataclass(slots=True)
class ProjectSpend:
    project_id: str | None      # None: the call belonged to the workspace, not to one project
    project_name: str | None
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    unpriced: int


@dataclass(slots=True)
class CostlyCall:
    at: datetime
    lane: str
    model: str
    feature: str
    agent: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    run_ref: str | None
    task_ref: str | None


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
    tokens_cached: int = 0
    tokens_reasoning: int = 0
    cost_usd: float | None = 0.0
    saved_usd: float | None = 0.0
    #: Calls with tokens whose cost is unknown: the lane has no price for the model they ran on.
    unpriced: int = 0


@dataclass(slots=True)
class CacheLine:
    """What the prompt cache and reasoning came to, for one lane or one feature (`key`)."""

    key: str
    calls: int
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    tokens_reasoning: int
    cost_usd: float | None
    saved_usd: float | None
    unpriced: int


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
class StepRecord:
    agent_id: str
    kind: str
    decided: int
    good: int
    minutes: int | None


@dataclass(slots=True)
class InFlight:
    agent_id: str
    run_id: str
    run_status: str
    step_status: str | None     # None: the agent owns the whole run, which is between steps
    created_at: datetime


@dataclass(slots=True)
class CurrentRun:
    run_ref: str
    task_ref: str | None
    status: str
    branch: str
    worktree: str
    started_at: datetime
    files_changed: int
    progress: int
    step: str | None
    step_kind: str | None
    step_status: str | None
    tokens_in: int
    tokens_out: int


@dataclass(slots=True)
class Answered:
    lane: str
    model: str
    at: datetime


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
    by_agent: list[AgentSpend]
    by_project: list[ProjectSpend]
    costliest: list[CostlyCall]
    cache: CacheLine | None = None
    cache_by_lane: list[CacheLine] | None = None
    cache_by_feature: list[CacheLine] | None = None


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
        cost = _cost()
        stmt = select(
            func.count(),
            func.count().filter(AiCall.lane != OFFLINE),
            func.count().filter(AiCall.lane == OFFLINE),
            func.count().filter(AiCall.ok.is_(False)),
            func.coalesce(func.sum(AiCall.tokens_in), 0),
            func.coalesce(func.sum(AiCall.tokens_out), 0),
            func.coalesce(func.avg(AiCall.ms), 0),
            func.sum(cost),
            func.count().filter(cost.is_(None)),
        ).where(self._within(days))
        calls, model_calls, offline, failures, tokens_in, tokens_out, ms, dollars, unpriced = (
            await self.session.execute(stmt)).one()
        return Totals(calls=_int(calls), model_calls=_int(model_calls), offline=_int(offline),
                      failures=_int(failures), tokens_in=_int(tokens_in), tokens_out=_int(tokens_out),
                      avg_ms=_ms(ms), cost_usd=_dollars(dollars, _int(calls), _int(unpriced)),
                      unpriced=_int(unpriced))

    async def by_day(self, days: int) -> list[DayLine]:
        day, cost = func.date_trunc("day", AiCall.at), _cost()
        stmt = (select(func.to_char(day, "YYYY-MM-DD"), func.count(),
                       func.count().filter(AiCall.lane != OFFLINE),
                       func.count().filter(AiCall.lane == OFFLINE),
                       func.coalesce(func.sum(AiCall.tokens_in + AiCall.tokens_out), 0),
                       func.sum(cost), func.count().filter(cost.is_(None)))
                # Newest first, then turned back around: ordered ascending and capped, the row that
                # fell off the end was *today* whenever the window held a day more than `days`.
                .where(self._within(days)).group_by(day).order_by(day.desc()).limit(bounded(days)))
        rows = (await self.session.execute(stmt)).all()
        return [DayLine(day=d, calls=_int(c), model=_int(m), offline=_int(o), tokens=_int(t),
                        cost_usd=_dollars(usd, _int(c), _int(u)), unpriced=_int(u))
                for d, c, m, o, t, usd, u in reversed(rows)]

    async def by_agent(self, days: int) -> list[AgentSpend]:
        """What each agent's calls cost. Only calls an agent asked for: a person compiling or asking
        memory is on `by_person`, and folding them in here under "nobody" would hide the agents."""
        key, cost, calls = _agent_key(), _cost(), func.count()
        stmt = (select(key, calls, func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0),
                       func.sum(cost), func.count().filter(cost.is_(None)))
                .where(self._within(days), AiCall.agent != "")
                .group_by(key).order_by(calls.desc(), key).limit(MAX_LIMIT))
        return [AgentSpend(agent=a, calls=_int(c), tokens_in=_int(ti), tokens_out=_int(to),
                           cost_usd=_dollars(usd, _int(c), _int(u)), unpriced=_int(u))
                for a, c, ti, to, usd, u in (await self.session.execute(stmt)).all()]

    async def by_project(self, days: int) -> list[ProjectSpend]:
        """What each project's calls cost, with the calls that belonged to no project as one line of
        their own. A removed project's calls are already on that line: the ledger's foreign key is
        SET NULL, because the history outlives the project."""
        cost, calls = _cost(), func.count()
        stmt = (select(AiCall.project_id, Project.name, calls,
                       func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0),
                       func.sum(cost), func.count().filter(cost.is_(None)))
                .join(Project, Project.id == AiCall.project_id, isouter=True)
                .where(self._within(days)).group_by(AiCall.project_id, Project.name)
                .order_by(calls.desc(), Project.name.nulls_last()).limit(MAX_LIMIT))
        return [ProjectSpend(project_id=pid, project_name=name, calls=_int(c), tokens_in=_int(ti),
                             tokens_out=_int(to), cost_usd=_dollars(usd, _int(c), _int(u)), unpriced=_int(u))
                for pid, name, c, ti, to, usd, u in (await self.session.execute(stmt)).all()]

    async def costliest(self, days: int, *, limit: int = COSTLIEST) -> list[CostlyCall]:
        """The window's most expensive calls, with the run and task each was made for.

        A call on an unpriced lane has no cost to rank, so it comes after every priced one rather than
        being guessed into place; among equal costs — every free lane's is zero — the bigger call first.
        """
        cost, key = _cost(), _agent_key()
        stmt = (select(AiCall.at, AiCall.lane, AiCall.model, AiCall.feature, key, AiCall.tokens_in,
                       AiCall.tokens_out, cost, Run.ref, Task.ref)
                .join(Run, Run.id == AiCall.run_id, isouter=True)
                .join(Task, Task.id == Run.task_id, isouter=True)
                .where(self._within(days))
                .order_by(cost.desc().nulls_last(), (AiCall.tokens_in + AiCall.tokens_out).desc(),
                          AiCall.id.desc())
                .limit(bounded(limit)))
        return [CostlyCall(at=at, lane=lane, model=model, feature=f, agent=a or None, tokens_in=_int(ti),
                           tokens_out=_int(to), cost_usd=None if usd is None else round(float(usd), 6),
                           run_ref=run_ref, task_ref=task_ref)
                for at, lane, model, f, a, ti, to, usd, run_ref, task_ref
                in (await self.session.execute(stmt)).all()]

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
        """A lane and the model it reached. The old ledger called the first half of that a provider.
        What each line cost is the database's sum at the lane's price for that model, with its cache
        hits and its hour — the same `_cost` every other figure here is."""
        calls, cost, saved = func.count(), _cost(), _saved()
        stmt = (select(AiCall.lane, AiCall.model, calls,
                       func.count().filter(AiCall.ok.is_(False)),
                       func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0),
                       func.coalesce(func.avg(AiCall.ms), 0),
                       func.coalesce(func.sum(AiCall.tokens_cached), 0),
                       func.coalesce(func.sum(AiCall.tokens_reasoning), 0),
                       func.sum(cost), func.sum(saved), func.count().filter(cost.is_(None)))
                .where(self._within(days)).group_by(AiCall.lane, AiCall.model)
                .order_by(calls.desc(), AiCall.lane, AiCall.model).limit(MAX_LIMIT))
        return [LaneLine(lane=lane, model=model, calls=_int(c), failures=_int(x), tokens_in=_int(ti),
                         tokens_out=_int(to), avg_ms=_ms(ms), tokens_cached=_int(tc), tokens_reasoning=_int(tr),
                         cost_usd=_dollars(usd, _int(c), _int(u)), saved_usd=_dollars(sv, _int(c), _int(u)),
                         unpriced=_int(u))
                for lane, model, c, x, ti, to, ms, tc, tr, usd, sv, u in (await self.session.execute(stmt)).all()]

    async def cache(self, days: int, by: str | None = None) -> list[CacheLine]:
        """The prompt cache's share and what it saved, and the reasoning tokens apart from the answer's —
        over the whole window (`by` None), or per lane or per feature. Only what providers reported:
        a lane that reports no cache hits shows none, rather than an estimate."""
        column = {"lane": AiCall.lane, "feature": AiCall.feature}.get(by or "")
        key = column if column is not None else literal("all")
        calls, cost, saved = func.count(), _cost(), _saved()
        stmt = (select(key, calls, func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_cached), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0),
                       func.coalesce(func.sum(AiCall.tokens_reasoning), 0),
                       func.sum(cost), func.sum(saved), func.count().filter(cost.is_(None)))
                .where(self._within(days), AiCall.lane != OFFLINE))
        if column is not None:
            stmt = stmt.group_by(column).order_by(calls.desc(), column)
        rows = (await self.session.execute(stmt.limit(MAX_LIMIT))).all()
        return [CacheLine(key=k, calls=_int(c), tokens_in=_int(ti), tokens_cached=_int(tc), tokens_out=_int(to),
                          tokens_reasoning=_int(tr), cost_usd=_dollars(usd, _int(c), _int(u)),
                          saved_usd=_dollars(sv, _int(c), _int(u)), unpriced=_int(u))
                for k, c, ti, tc, to, tr, usd, sv, u in rows if _int(c)]

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

    async def spend_24h(self) -> dict[str, tuple[int, float | None]]:
        """agent → tokens it has spent in the last day, and what those cost.

        These two were a hardcoded 0 on every card, which reads as a measurement rather than as the
        absence of one. The ledger records which agent asked now, so this is simply a sum — and the
        cost is the lanes' own declared price, which for every free lane in the catalogue is really
        zero. A zero here means "free", not "we did not look".
        """
        since = func.now() - text("interval '24 hours'")
        cost = _cost()
        stmt = (select(AiCall.agent, func.coalesce(func.sum(AiCall.tokens_in + AiCall.tokens_out), 0),
                       func.sum(cost), func.count().filter(cost.is_(None)))
                .where(AiCall.at >= since, AiCall.agent != "")
                .group_by(AiCall.agent).limit(MAX_LIMIT))
        # One call to a lane with no declared price — or on a model its price is not for — makes the whole
        # figure unknown, not smaller. The cost is `_cost`, so cache hits and off-peak hours count here too.
        return {who: (_int(total), None if _int(unpriced) else round(float(usd or 0), 4))
                for who, total, usd, unpriced in (await self.session.execute(stmt)).all()}

    async def last_answered(self, agents: list[str]) -> dict[str, Answered]:
        """agent name or id → the last call a lane answered for it. What the router *would* pick is a
        snapshot that rotates; this is what really happened, whenever it did."""
        if not agents:
            return {}
        stmt = (select(AiCall.agent, AiCall.lane, AiCall.model, AiCall.at)
                .where(AiCall.agent.in_(agents), AiCall.ok.is_(True))
                .order_by(AiCall.agent, AiCall.id.desc()).distinct(AiCall.agent).limit(MAX_LIMIT))
        return {who: Answered(lane=lane, model=model, at=at)
                for who, lane, model, at in (await self.session.execute(stmt)).all()}

    async def offline_reviews_24h(self) -> int:
        """Reviews the rules wrote in the last day because no lane answered.

        They are not in the ledger: the review asks the gateway for a model and, when none answers,
        falls back on its own — so the only record of it is the run's review, and this counts those.
        A run waiting at its handoff has not finished, so its last change stands in for the time.
        """
        since = func.now() - text("interval '24 hours'")
        stmt = (select(func.count()).select_from(Run)
                .where(Run.review["by"].astext == OFFLINE_REVIEW,
                       func.coalesce(Run.finished_at, Run.updated_at) >= since))
        return _int((await self.session.execute(stmt)).scalar_one())

    async def report(self, days: int, *, admin: bool) -> Usage:
        """Everything the usage screen shows. Who made a call is an admin's business, so for anyone
        else that question is not asked at all rather than asked and thrown away."""
        return Usage(days=days, totals=await self.totals(days), by_day=await self.by_day(days),
                     by_feature=await self.by_feature(days), by_lane=await self.by_lane(days),
                     recent=await self.recent_calls(),
                     by_person=await self.by_person(days) if admin else None,
                     by_agent=await self.by_agent(days), by_project=await self.by_project(days),
                     costliest=await self.costliest(days),
                     cache=next(iter(await self.cache(days)), None),
                     cache_by_lane=await self.cache(days, "lane"),
                     cache_by_feature=await self.cache(days, "feature"))


class AgentRepository(Repository[Agent]):
    """The roster, and the two questions its card used to answer from stored numbers."""

    model = Agent

    async def all_ordered(self) -> list[Agent]:
        # By name, because nothing on the row records the order the roster was written in, and an
        # order that comes out of the storage engine is an order that changes under you.
        return await self.list(order_by=Agent.name, limit=200)

    async def tasks_finished(self) -> dict[str, int]:
        """agent id → how many finished tasks carry it. One GROUP BY for the whole roster.

        A task names its agents the way the plan that made it did — "Backend Engineer" — while a
        workspace imported from the old store names them by id. Both spellings can be in that column,
        so both are matched and the answer is keyed by the agent instead.
        """
        stmt = (select(Agent.id, func.count())
                .select_from(Agent)
                .join(TaskAgent, TaskAgent.agent.in_((Agent.id, Agent.name)))
                .join(Task, Task.id == TaskAgent.task_id)
                .where(Task.status == "done")
                .group_by(Agent.id).limit(MAX_LIMIT))
        return {agent_id: _int(n) for agent_id, n in (await self.session.execute(stmt)).all()}

    async def step_record(self) -> list[StepRecord]:
        """agent id × step kind → how many of its steps reached a verdict, how many went well, and how
        long one takes. One GROUP BY for the whole roster.

        Counted by step rather than by run, because a run's `agent` is empty for every solo and
        integration run: the QA Engineer and the Code Reviewer own steps inside those runs and never a
        run of their own, so a run-level record left them at nothing forever.

        "Went well" means something different for each kind, and says only what it can. An edit step
        is done when the agent wrote what the step needed. A test step is done when the *project's*
        tests passed, which measures the code, not the agent. A review step cannot fail — with no
        model the rules read the diff instead — so the only honest verdict on it is whether a model
        really read the diff, which the run records in `review.by`.
        """
        decided = RunStep.status.in_(DECIDED)
        model_read = func.coalesce(Run.review["by"].astext, "").not_in(("", OFFLINE_REVIEW))
        good = case((RunStep.kind == "review", and_(RunStep.status == "done", model_read)),
                    else_=RunStep.status == "done")
        stmt = (select(Agent.id, RunStep.kind, func.count().filter(decided), func.count().filter(decided, good),
                       func.avg(RunStep.ms).filter(decided))
                .select_from(Agent)
                .join(RunStep, RunStep.agent.in_((Agent.id, Agent.name)))
                .join(Run, Run.id == RunStep.run_id)
                .where(RunStep.kind.in_(RECORDED_KINDS))
                .group_by(Agent.id, RunStep.kind).limit(MAX_LIMIT))
        return [StepRecord(agent_id=agent_id, kind=kind, decided=_int(d), good=_int(g),
                           minutes=None if ms is None else round(float(ms) / 60000.0))
                for agent_id, kind, d, g, ms in (await self.session.execute(stmt)).all()]

    async def in_flight(self) -> list[InFlight]:
        """Every agent that is doing something right now, and the run it is doing it in.

        Two ways to be in one. An agent owns the step a running or waiting run is on; or, when several
        agents work at once, it owns a whole run that is between steps. Start-up fails whatever a dead
        process left `running`, so a row that says so here is really being worked.
        """
        by_step = (select(Agent.id, Run.id, Run.status, RunStep.status, Run.created_at)
                   .select_from(Agent)
                   .join(RunStep, RunStep.agent.in_((Agent.id, Agent.name)))
                   .join(Run, Run.id == RunStep.run_id)
                   .where(RunStep.status.in_(LIVE), Run.status.in_(LIVE))
                   .order_by(Run.created_at.desc()).limit(MAX_LIMIT))
        by_run = (select(Agent.id, Run.id, Run.status, literal(None), Run.created_at)
                  .select_from(Agent)
                  .join(Run, Run.agent.in_((Agent.id, Agent.name)))
                  .where(Run.role == "agent", Run.status.in_(LIVE))
                  .order_by(Run.created_at.desc()).limit(MAX_LIMIT))
        out: list[InFlight] = []
        for stmt in (by_step, by_run):
            out += [InFlight(agent_id=a, run_id=r, run_status=rs, step_status=ss, created_at=at)
                    for a, r, rs, ss, at in (await self.session.execute(stmt)).all()]
        return out

    async def current_runs(self, run_ids: list[str]) -> dict[str, CurrentRun]:
        """What an agent's card shows about the run it is in: where it is, how far, and what it cost.

        Tokens are summed over the ledger lines that name this run, and nothing else — an agent-wide
        total would fold in every other run it ever made. The step it is on is the first one that has
        not finished, which is the step `execute` is working or waiting on.
        """
        if not run_ids:
            return {}
        steps = (select(RunStep.run_id, func.count().label("total"),
                        func.count().filter(RunStep.status.in_(FINISHED_STEPS)).label("finished"))
                 .where(RunStep.run_id.in_(run_ids)).group_by(RunStep.run_id).subquery())
        spent = (select(AiCall.run_id, func.coalesce(func.sum(AiCall.tokens_in), 0).label("tin"),
                        func.coalesce(func.sum(AiCall.tokens_out), 0).label("tout"))
                 .where(AiCall.run_id.in_(run_ids)).group_by(AiCall.run_id).subquery())
        stmt = (select(Run.id, Run.ref, Task.ref, Run.status, Run.branch, Run.worktree, Run.created_at,
                       Run.diff_files, steps.c.total, steps.c.finished, spent.c.tin, spent.c.tout)
                .join(Task, Task.id == Run.task_id, isouter=True)
                .join(steps, steps.c.run_id == Run.id, isouter=True)
                .join(spent, spent.c.run_id == Run.id, isouter=True)
                .where(Run.id.in_(run_ids)).limit(MAX_LIMIT))
        rows = (await self.session.execute(stmt)).all()
        on = (select(RunStep.run_id, RunStep.n, RunStep.label, RunStep.kind, RunStep.status)
              .where(RunStep.run_id.in_(run_ids), RunStep.status.not_in(FINISHED_STEPS))
              .order_by(RunStep.run_id, RunStep.n).distinct(RunStep.run_id).limit(MAX_LIMIT))
        step_on = {run_id: (label, kind, status)
                   for run_id, _, label, kind, status in (await self.session.execute(on)).all()}
        out: dict[str, CurrentRun] = {}
        for run_id, ref, task_ref, status, branch, worktree, at, files, total, finished, tin, tout in rows:
            label, kind, step_status = step_on.get(run_id, (None, None, None))
            out[run_id] = CurrentRun(
                run_ref=ref, task_ref=task_ref, status=status, branch=branch, worktree=worktree, started_at=at,
                files_changed=_int(files), progress=round(100 * _int(finished) / max(1, _int(total))),
                step=label, step_kind=kind, step_status=step_status, tokens_in=_int(tin), tokens_out=_int(tout))
        return out

    async def kept_worktrees(self) -> list[str]:
        """The worktree paths of every run whose worktree nobody removed, newest first.

        No status filter: stopping a run leaves its worktree in place for a person to look at, and only
        a discard or a refused handoff removes one. Whether the directory is really there is the
        caller's question to the disk — a run that failed before its worktree opened has none.
        """
        stmt = (select(Run.worktree).where(Run.removed.is_(False))
                .order_by(Run.created_at.desc()).limit(MAX_LIMIT))
        return list((await self.session.execute(stmt)).scalars().all())
