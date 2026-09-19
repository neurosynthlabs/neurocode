"""The work: projects, tasks, plans, the gates, and the story of what happened.

The methods here are the questions the screens actually ask. Several of them were loops over parsed
JSON documents before — "how many tasks is this project carrying, by status" meant reading every task
in Python. Now they are one statement each, answered by the database with an index behind it.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Integer, cast, func, select

from ..models import (
    ActivityEvent,
    Approval,
    Decision,
    Plan,
    PlanQuestion,
    Pref,
    Project,
    Task,
    TaskAgent,
    User,
)
from .base import Page, Repository


class ProjectRepository(Repository[Project]):
    model = Project

    async def all_ordered(self) -> list[Project]:
        return await self.list(order_by=Project.name, limit=200)

    async def onboarded(self) -> list[Project]:
        """The ones whose code is on this machine — the only ones an agent can really work in."""
        return await self.list(Project.source_kind.is_not(None), order_by=Project.name)

    async def task_counts(self) -> dict[str, dict[str, int]]:
        """project id → {status: how many}. One GROUP BY instead of counting in Python."""
        stmt = (select(Task.project_id, Task.status, func.count())
                .group_by(Task.project_id, Task.status))
        out: dict[str, dict[str, int]] = {}
        for pid, status, n in (await self.session.execute(stmt)).all():
            out.setdefault(pid, {})[status] = int(n)
        return out


class TaskRepository(Repository[Task]):
    model = Task

    async def by_ref(self, ref: str) -> Task | None:
        return await self.one(Task.ref == ref)

    async def board(self, project_id: str | None = None, *, limit: int | None = None,
                    offset: int = 0) -> Page[Task]:
        where: list[ColumnElement[bool]] = [Task.project_id == project_id] if project_id else []
        return await self.page(*where, order_by=Task.created_at.desc(), limit=limit, offset=offset)

    async def counts_by_status(self, project_id: str | None = None) -> dict[str, int]:
        stmt = select(Task.status, func.count()).group_by(Task.status)
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)
        return {status: int(n) for status, n in (await self.session.execute(stmt)).all()}

    async def for_agent(self, agent: str, *, limit: int | None = None) -> list[Task]:
        """What one agent is carrying — a join now, rather than a scan of every task's agent list."""
        stmt = (select(Task).join(TaskAgent, TaskAgent.task_id == Task.id)
                .where(TaskAgent.agent == agent, Task.status.not_in(("done",)))
                .order_by(Task.created_at.desc()).limit(limit or 50))
        return list((await self.session.execute(stmt)).scalars().unique())

    async def next_ref(self, prefix: str = "TASK-") -> str:
        return await super().next_ref(Task.ref, prefix)


class PlanRepository(Repository[Plan]):
    model = Plan

    async def by_ref(self, ref: str) -> Plan | None:
        return await self.one(Plan.ref == ref)

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0) -> Page[Plan]:
        where: list[ColumnElement[bool]] = [Plan.project_id == project_id] if project_id else []
        return await self.page(*where, order_by=Plan.created_at.desc(), limit=limit, offset=offset)

    async def open_questions(self, plan_id: str) -> list[PlanQuestion]:
        stmt = (select(PlanQuestion)
                .where(PlanQuestion.plan_id == plan_id, PlanQuestion.answer == "",
                       PlanQuestion.deferred.is_(False))
                .order_by(PlanQuestion.n))
        return list((await self.session.execute(stmt)).scalars())

    async def settled(self, plan_id: str) -> bool:
        """Nothing left open: the condition for dispatching, asked of the database, not of a document."""
        return not await self.open_questions(plan_id)


class ApprovalRepository(Repository[Approval]):
    model = Approval

    async def by_ref(self, ref: str) -> Approval | None:
        return await self.one(Approval.ref == ref)

    #: Newest first, and then by reference, newest first too. Gates written in one transaction share a
    #: timestamp, so without the second key the order was whatever Postgres
    #: returned — and the full list and the pending list came back in different orders, which put a
    #: different gate under the first "Approve" button than the one a caller had just read.
    ORDER = (Approval.created_at.desc(),
             cast(func.nullif(func.regexp_replace(Approval.ref, r"\D", "", "g"), ""), Integer).desc())

    async def pending(self, *, limit: int | None = None, offset: int = 0) -> Page[Approval]:
        return await self.page(Approval.status == "pending", order_by=self.ORDER, limit=limit, offset=offset)

    async def newest(self, *, limit: int | None = None, offset: int = 0) -> Page[Approval]:
        return await self.page(order_by=self.ORDER, limit=limit, offset=offset)

    async def for_run(self, run_ref: str) -> list[Approval]:
        return await self.list(Approval.run_ref == run_ref, order_by=Approval.created_at)

    async def waiting_on_person(self, run_ref: str) -> Approval | None:
        return await self.one(Approval.run_ref == run_ref, Approval.status == "pending")

    async def next_ref(self, prefix: str = "APPR-", floor: int = 100) -> str:
        """Gates start numbering at 100, so the first one does not read like a task."""
        ref = await super().next_ref(Approval.ref, prefix)
        return ref if int(ref.removeprefix(prefix)) > floor else f"{prefix}{floor + 1}"


class ActivityRepository(Repository[ActivityEvent]):
    model = ActivityEvent

    async def recent(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0) -> Page[ActivityEvent]:
        where: list[ColumnElement[bool]] = [ActivityEvent.project_id == project_id] if project_id else []
        return await self.page(*where, order_by=ActivityEvent.seq.desc(), limit=limit, offset=offset)

    async def summary(self, *, person: str, day_start: datetime, top: int = 20) -> dict[str, Any]:
        """The log's figures over every row, counted in Postgres.

        The feed a screen holds is the newest few hundred rows, so any count taken from it is a count of
        that window, not of the log. `through` is the newest row these figures include, so a caller that
        also follows the stream can add only what arrived after it, without counting a row twice.
        """
        e = ActivityEvent
        totals = (await self.session.execute(select(
            func.count(),
            func.count().filter(e.at >= day_start),
            func.count().filter(e.actor_kind == "human", e.actor == person),
            func.coalesce(func.max(e.seq), 0),
        ).select_from(e))).one()
        kinds = (await self.session.execute(
            select(e.actor_kind, func.count()).group_by(e.actor_kind))).all()
        agents = (await self.session.execute(
            select(e.actor, func.count().label("n")).where(e.actor_kind == "agent")
            .group_by(e.actor).order_by(func.count().desc(), e.actor).limit(top))).all()
        return {"total": int(totals[0]), "today": int(totals[1]), "mine": int(totals[2]),
                "through": int(totals[3]),
                "byKind": {str(kind): int(n) for kind, n in kinds},
                "agents": [{"name": name, "events": int(n)} for name, n in agents]}

    async def since(self, at: datetime, *, limit: int | None = None) -> list[ActivityEvent]:
        return await self.list(ActivityEvent.at > at, order_by=ActivityEvent.seq, limit=limit)

    async def record(self, *, actor: str, actor_kind: str, action: str, detail: str, level: str = "info",
                     project_id: str | None = None, task_ref: str | None = None) -> ActivityEvent:
        event = await self.add(ActivityEvent(actor=actor, actor_kind=actor_kind, action=action,
                                             detail=detail, level=level, project_id=project_id,
                                             task_ref=task_ref))
        # Written and announced in one place, so an event can never be recorded without reaching the
        # open tabs. A session with no bus — a test, a script — simply publishes nothing.
        feed = self.session.info.get("bus")
        if feed is not None:
            from ..schemas.work import activity_json
            feed.publish("activity", activity_json(event))
        return event


class DecisionRepository(Repository[Decision]):
    model = Decision

    async def all_ordered(self) -> list[Decision]:
        return await self.list(order_by=Decision.created_at, limit=200)

    async def already(self, key: str) -> bool:
        """A decision is final, so the first question is always whether one was already made."""
        return await self.exists(Decision.id == key)

    async def names(self, decisions: Sequence[Decision]) -> dict[str, str]:
        """user id → name, for whoever made these decisions. One statement for a whole list."""
        ids = {d.by_user_id for d in decisions if d.by_user_id}
        if not ids:
            return {}
        rows = await self.session.execute(select(User.id, User.name).where(User.id.in_(ids)))
        return {uid: name for uid, name in rows.all()}


class PrefRepository(Repository[Pref]):
    model = Pref

    async def all_ordered(self) -> list[Pref]:
        return await self.list(order_by=Pref.id, limit=500)
