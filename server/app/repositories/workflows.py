"""Workflows, and the runs their plans became, read back.

A workflow keeps no run history of its own. Its runs are the runs of the plans it produced, reached
through `plans.workflow_id` — null for a plan the compiler wrote — so every number here is an aggregate
over rows the runtime wrote while it worked, never a counter a workflow kept about itself.

"Lead" is the run a person thinks of as *the* run: a solo run, or the integration run that merges
several agents. The agent runs under an integration run are its children, not runs of their own.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, extract, func, or_, select
from sqlalchemy.orm import aliased

from ..models import Agent, AiCall, Plan, Run, RunConflict, RunStep, Task, WorkflowDefinition
from .base import MAX_LIMIT, Repository

#: The roles whose run is the one a person started.
LEAD_ROLES = ("solo", "integration")
#: A run that has reached a verdict, whatever it was.
FINISHED = ("done", "failed", "cancelled")
#: A run still in someone's hands: working, about to, or waiting on a person.
UNDER_WAY = ("queued", "running", "waiting")
#: The most workflows the library lists; a workspace past this has a naming problem, not a list.
MAX_WORKFLOWS = 200
#: How many lead runs the history shows.
HISTORY = 12


def _int(value: object) -> int:
    return int(value or 0)


@dataclass(slots=True)
class WorkflowStats:
    runs: int
    avg_agents: float | None
    avg_minutes: int | None
    last_run: datetime | None


@dataclass(slots=True)
class Spend:
    """What a run's own ledger lines add up to, per lane, so the cost uses each lane's own price."""

    lane: str
    feature: str
    tokens_in: int
    tokens_out: int
    #: A lane's price is for the model its catalogue names; a line under another model has no known price.
    model: str = ""


def _lead() -> ColumnElement[bool]:
    return and_(Run.role.in_(LEAD_ROLES), Run.plan_id.is_not(None))


def _agents_in(run: type[Run]) -> ColumnElement[int]:
    """How many agents a lead run put to work: one for a solo run, its children for a merge run."""
    child = aliased(Run)
    children = (select(func.count()).select_from(child).where(child.parent_id == run.id)
                .correlate(run).scalar_subquery())
    return func.coalesce(func.nullif(children, 0), 1)


class WorkflowRepository(Repository[WorkflowDefinition]):
    model = WorkflowDefinition

    # ── the definitions ──────────────────────────────────────────
    async def active(self) -> list[WorkflowDefinition]:
        return await self.list(WorkflowDefinition.archived.is_(False),
                               order_by=WorkflowDefinition.name, limit=MAX_WORKFLOWS)

    async def by_name(self, name: str) -> WorkflowDefinition | None:
        """`name` is citext, so this match ignores case exactly as the unique index does."""
        return await self.one(WorkflowDefinition.name == name)

    async def active_count(self) -> int:
        return await self.count(WorkflowDefinition.archived.is_(False))

    async def referenced_ids(self) -> set[str]:
        """Every workflow some plan came from — whether its button archives or deletes, for the whole
        library in one statement."""
        stmt = select(Plan.workflow_id).where(Plan.workflow_id.is_not(None)).distinct()
        return {wid for wid in (await self.session.execute(stmt)).scalars() if wid}

    async def referenced(self, workflow_id: str) -> bool:
        """Whether any plan came from it — the difference between archiving and deleting."""
        stmt = select(exists().where(Plan.workflow_id == workflow_id))
        return bool((await self.session.execute(stmt)).scalar_one())

    async def roster(self) -> list[str]:
        """Every agent name on the roster, in order."""
        stmt = select(Agent.name).order_by(Agent.name).limit(MAX_LIMIT)
        return list((await self.session.execute(stmt)).scalars())

    async def names(self, ids: list[str]) -> dict[str, str]:
        if not ids:
            return {}
        stmt = select(WorkflowDefinition.id, WorkflowDefinition.name).where(WorkflowDefinition.id.in_(ids))
        return {wid: str(name) for wid, name in (await self.session.execute(stmt)).all()}

    # ── what their runs add up to ────────────────────────────────
    async def stats(self) -> dict[str | None, WorkflowStats]:
        """workflow id (None: plans the compiler wrote) → runs, agents per run, minutes, last run."""
        minutes = func.round(func.avg(extract("epoch", Run.finished_at - Run.created_at) / 60.0)
                             .filter(Run.status.in_(("done", "failed"))))
        stmt = (select(Plan.workflow_id, func.count(), func.avg(_agents_in(Run)), minutes,
                       func.max(Run.created_at))
                .select_from(Run).join(Plan, Plan.id == Run.plan_id)
                .where(_lead()).group_by(Plan.workflow_id).limit(MAX_WORKFLOWS + 1))
        return {wid: WorkflowStats(runs=_int(n), avg_agents=float(agents) if agents is not None else None,
                                   avg_minutes=int(m) if m is not None else None, last_run=last)
                for wid, n, agents, m, last in (await self.session.execute(stmt)).all()}

    async def last_finished(self) -> dict[str | None, Run]:
        """workflow id → its newest lead run that reached a verdict. One DISTINCT ON, not one per row."""
        stmt = (select(Plan.workflow_id, Run).join(Plan, Plan.id == Run.plan_id)
                .where(_lead(), Run.status.in_(FINISHED))
                .order_by(Plan.workflow_id, Run.created_at.desc())
                .distinct(Plan.workflow_id).limit(MAX_WORKFLOWS + 1))
        return {wid: run for wid, run in (await self.session.execute(stmt)).unique().all()}

    async def blemishes(self, run_ids: list[str]) -> set[str]:
        """The lead runs that finished with something not done: a collision at the merge, an agent run
        that failed or was stopped, or a write step that failed or was skipped — theirs or an agent's."""
        if not run_ids:
            return set()
        child = aliased(Run)
        broken_edit = (RunStep.kind == "edit", RunStep.status.in_(("failed", "skipped")))
        # Each subquery is tied to the outer run by name. Left to infer it, SQLAlchemy put `runs` in the
        # inner FROM beside its alias — a cross join — so one flawed agent run anywhere in the database
        # marked every clean run as partial.
        flawed = or_(
            exists().where(RunConflict.run_id == Run.id).correlate(Run),
            exists().where(child.parent_id == Run.id, child.status.in_(("failed", "cancelled"))).correlate(Run),
            exists().where(RunStep.run_id == Run.id, *broken_edit).correlate(Run),
            exists().where(RunStep.run_id == child.id, child.parent_id == Run.id, *broken_edit).correlate(Run),
        )
        stmt = select(Run.id).where(Run.id.in_(run_ids), flawed)
        return set((await self.session.execute(stmt)).scalars())

    async def lead_count(self) -> int:
        return await self._count_runs(_lead())

    async def under_way_count(self) -> int:
        return await self._count_runs(_lead(), Run.status.in_(UNDER_WAY))

    async def running_agents(self) -> int:
        """Runs that are writing right now. A merge run is not an agent; it waits on them."""
        return await self._count_runs(Run.status == "running", Run.role != "integration")

    async def _count_runs(self, *where: ColumnElement[bool]) -> int:
        stmt = select(func.count()).select_from(Run).where(*where)
        return int((await self.session.execute(stmt)).scalar_one())

    async def live(self) -> Run | None:
        """The newest lead run still under way."""
        stmt = (select(Run).where(_lead(), Run.status.in_(UNDER_WAY))
                .order_by(Run.created_at.desc()).limit(1))
        return (await self.session.execute(stmt)).scalars().first()

    async def history(self, *, limit: int = HISTORY) -> list[tuple[Run, str | None, str | None, int]]:
        """The newest finished lead runs: the run, its plan's workflow, its task's ref, its agents."""
        stmt = (select(Run, Plan.workflow_id, Task.ref, _agents_in(Run))
                .join(Plan, Plan.id == Run.plan_id).join(Task, Task.id == Run.task_id, isouter=True)
                .where(_lead(), Run.status.in_(FINISHED))
                .order_by(Run.created_at.desc()).limit(min(limit, HISTORY)))
        return [(run, wid, ref, _int(agents))
                for run, wid, ref, agents in (await self.session.execute(stmt)).unique().all()]

    async def workflow_of(self, plan_id: str | None) -> str | None:
        if plan_id is None:
            return None
        stmt = select(Plan.workflow_id).where(Plan.id == plan_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def children(self, run_id: str) -> list[Run]:
        stmt = select(Run).where(Run.parent_id == run_id).order_by(Run.created_at).limit(MAX_LIMIT)
        return list((await self.session.execute(stmt)).scalars().unique())

    async def family_ids(self, run_ids: list[str]) -> dict[str, list[str]]:
        """lead run id → its own id and its agents' ids, so a run's spend can include what they spent."""
        if not run_ids:
            return {}
        out: dict[str, list[str]] = {rid: [rid] for rid in run_ids}
        stmt = select(Run.parent_id, Run.id).where(Run.parent_id.in_(run_ids))
        for parent, rid in (await self.session.execute(stmt)).all():
            out[parent].append(rid)
        return out

    # ── what they spent ──────────────────────────────────────────
    async def spend(self, run_ids: list[str]) -> dict[str, list[Spend]]:
        """run id → its ledger lines, summed per lane and feature. A run with no lines is absent."""
        if not run_ids:
            return {}
        stmt = (select(AiCall.run_id, AiCall.lane, AiCall.feature, AiCall.model,
                       func.coalesce(func.sum(AiCall.tokens_in), 0), func.coalesce(func.sum(AiCall.tokens_out), 0))
                .where(AiCall.run_id.in_(run_ids))
                .group_by(AiCall.run_id, AiCall.lane, AiCall.feature, AiCall.model).limit(MAX_LIMIT))
        out: dict[str, list[Spend]] = {}
        for rid, lane, feature, model, tin, tout in (await self.session.execute(stmt)).all():
            out.setdefault(rid, []).append(Spend(lane=lane, feature=feature, tokens_in=_int(tin),
                                                 tokens_out=_int(tout), model=model))
        return out

    async def spend_today(self) -> list[Spend]:
        """What agents and reviewers have spent since midnight, by the database's clock."""
        stmt = (select(AiCall.lane, AiCall.feature, AiCall.model, func.coalesce(func.sum(AiCall.tokens_in), 0),
                       func.coalesce(func.sum(AiCall.tokens_out), 0))
                .where(AiCall.feature.in_(("agent", "review")),
                       AiCall.at >= func.date_trunc("day", func.now()))
                .group_by(AiCall.lane, AiCall.feature, AiCall.model).limit(MAX_LIMIT))
        return [Spend(lane=lane, feature=feature, tokens_in=_int(tin), tokens_out=_int(tout), model=model)
                for lane, feature, model, tin, tout in (await self.session.execute(stmt)).all()]
