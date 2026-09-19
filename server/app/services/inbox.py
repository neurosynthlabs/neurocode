"""The inbox: what needs you, what is working, and what finished since you last looked.

Nothing here is stored. Every entry is read from the rows that already say it — a pending approval, a
permission card a session waits on, a run's status, a fire still being compiled — so the inbox cannot
drift from the screens those rows belong to, and clearing it changes nothing but the moment it counts
from. That moment is `users.last_seen_at`, set by `POST /inbox/seen`. A person who has never looked is
shown the last day, and told so, rather than everything since the workspace began.

Three parts, each capped at `SHOWN` entries with the true count beside it:

- **Needs you** — approvals waiting (split by what they ask: an agent's question, a run at its
  signature, any other gate) and sessions waiting on a tool permission.
- **Working** — runs queued or running (a batch counts once, by its lead run), sessions thinking, and
  routines whose fire is still being compiled.
- **Done since** — runs that finished, plans compiled, routines that fired or were refused, and the
  reviews, research and evals that finished.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import (
    Approval,
    Chat,
    ChatMessage,
    CodeReview,
    EvalRun,
    EvalSuite,
    Plan,
    ResearchReport,
    Run,
    Schedule,
    ScheduleFire,
    Task,
    User,
)
from ..schemas.work import gate_kind, when
from .chat import PERMISSION
from .schedules import FIRING_FOR

#: The most entries each part lists. The counts beside them are always the whole truth.
SHOWN = 20
#: How far back "done since" reaches for a person who has never marked the inbox seen.
FIRST_LOOK = timedelta(days=1)
FINISHED = ("done", "failed", "cancelled")


def _item(kind: str, ref: str, title: str, *, at: datetime | None, project_id: str | None = None,
          detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "ref": ref, "title": title, "detail": detail, "projectId": project_id,
            "at": when(at), **{k: v for k, v in extra.items() if v is not None}}


def _newest(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda x: x["at"] or "", reverse=True)[:SHOWN]


class InboxService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.session = session
        self.clock = clock

    async def _count(self, stmt: Select[Any]) -> int:
        return int((await self.session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())

    async def _rows(self, stmt: Select[Any]) -> list[Any]:
        return list((await self.session.execute(stmt.limit(SHOWN))).all())

    # ── needs you ────────────────────────────────────────────────
    async def _needs_you(self) -> tuple[list[dict[str, Any]], int]:
        gates = select(Approval).where(Approval.status == "pending")
        items = []
        for (a,) in await self._rows(gates.order_by(Approval.created_at.desc(), Approval.id.desc())):
            kind = {"question": "question", "signature": "signature"}.get(gate_kind(a.tool), "approval")
            items.append(_item(kind, a.ref, a.title, at=a.created_at, project_id=a.project_id,
                               detail=a.agent, runRef=a.run_ref, risk=a.risk))
        cards = (select(ChatMessage, Chat.ref, Chat.title, Chat.project_id)
                 .join(Chat, Chat.id == ChatMessage.chat_id)
                 .where(ChatMessage.tool == PERMISSION, ChatMessage.superseded_by.is_(None),
                        ChatMessage.arguments["state"].astext == "pending"))
        for message, ref, title, project_id in await self._rows(cards.order_by(ChatMessage.id.desc())):
            asked = message.arguments or {}
            subject = str(asked.get("subject") or "")[:160]
            items.append(_item("permission", ref, title or ref, at=message.at, project_id=project_id,
                               detail=f"{asked.get('tool') or 'a tool'}{f' · {subject}' if subject else ''}",
                               sessionRef=ref))
        return _newest(items), await self._count(gates) + await self._count(cards)

    # ── working ──────────────────────────────────────────────────
    async def _working(self) -> tuple[list[dict[str, Any]], int]:
        runs = select(Run).where(Run.parent_id.is_(None), Run.status.in_(("queued", "running")))
        items = [_item("run", r.ref, r.requirement[:200] or r.ref, at=r.created_at, project_id=r.project_id,
                       detail=f"{r.status} · {r.agent or r.role}", runRef=r.ref)
                 for (r,) in await self._rows(runs.order_by(Run.created_at.desc(), Run.id.desc()))]
        chats = select(Chat).where(Chat.status == "thinking")
        items += [_item("session", c.ref, c.title or c.ref, at=c.last_at, project_id=c.project_id,
                        detail="thinking", sessionRef=c.ref)
                  for (c,) in await self._rows(chats.order_by(Chat.last_at.desc()))]
        # A fire older than FIRING_FOR was left by a process that stopped; it is not working on anything.
        fires = (select(ScheduleFire, Schedule.name, Schedule.project_id)
                 .join(Schedule, Schedule.id == ScheduleFire.schedule_id)
                 .where(ScheduleFire.outcome == "firing", ScheduleFire.at > self.clock() - FIRING_FOR))
        items += [_item("routine", f.schedule_id, name, at=f.at, project_id=project_id,
                        detail=f"firing · {f.trigger}", scheduleId=f.schedule_id)
                  for f, name, project_id in await self._rows(fires.order_by(ScheduleFire.at.desc()))]
        return _newest(items), await self._count(runs) + await self._count(chats) + await self._count(fires)

    # ── done since ───────────────────────────────────────────────
    async def _done(self, since: datetime) -> tuple[list[dict[str, Any]], int]:
        runs = select(Run).where(Run.parent_id.is_(None), Run.status.in_(FINISHED), Run.finished_at > since)
        items = [_item("run", r.ref, r.requirement[:200] or r.ref, at=r.finished_at, project_id=r.project_id,
                       detail=r.status, runRef=r.ref, status=r.status)
                 for (r,) in await self._rows(runs.order_by(Run.finished_at.desc(), Run.id.desc()))]
        plans = (select(Plan.ref, Plan.created_at, Plan.project_id, Plan.status, Task.title)
                 .outerjoin(Task, Task.id == Plan.task_id).where(Plan.created_at > since))
        items += [_item("plan", ref, title or ref, at=at, project_id=project_id, detail=f"compiled · {status}")
                  for ref, at, project_id, status, title in
                  await self._rows(plans.order_by(Plan.created_at.desc(), Plan.id.desc()))]
        fires = (select(ScheduleFire, Schedule.name, Schedule.project_id)
                 .join(Schedule, Schedule.id == ScheduleFire.schedule_id)
                 .where(ScheduleFire.outcome.in_(("fired", "refused", "failed")), ScheduleFire.at > since))
        items += [_item("routine", f.schedule_id, name, at=f.at, project_id=project_id,
                        detail=f"{f.outcome} · {f.detail}"[:240], scheduleId=f.schedule_id, runRef=f.run_ref,
                        status=f.outcome)
                  for f, name, project_id in await self._rows(fires.order_by(ScheduleFire.at.desc()))]
        reviews = select(CodeReview).where(CodeReview.status.in_(("done", "failed")), CodeReview.finished_at > since)
        items += [_item("review", c.ref, c.source or c.ref, at=c.finished_at, project_id=c.project_id,
                        detail=c.status, status=c.status)
                  for (c,) in await self._rows(reviews.order_by(CodeReview.finished_at.desc()))]
        research = select(ResearchReport).where(ResearchReport.status.in_(FINISHED),
                                                ResearchReport.finished_at > since)
        items += [_item("research", r.ref, r.question[:200], at=r.finished_at, project_id=r.project_id,
                        detail=r.status, status=r.status)
                  for (r,) in await self._rows(research.order_by(ResearchReport.finished_at.desc()))]
        evals = (select(EvalRun, EvalSuite.name).join(EvalSuite, EvalSuite.id == EvalRun.suite_id)
                 .where(EvalRun.status.in_(FINISHED), EvalRun.finished_at > since))
        items += [_item("eval", e.ref, str(name), at=e.finished_at, detail=e.status, status=e.status)
                  for e, name in await self._rows(evals.order_by(EvalRun.finished_at.desc()))]
        total = sum([await self._count(q) for q in (runs, plans, fires, reviews, research, evals)])
        return _newest(items), total

    async def read(self, user_id: str) -> dict[str, Any]:
        seen = (await self.session.execute(select(User.last_seen_at).where(User.id == user_id))).scalar_one_or_none()
        since = seen or self.clock() - FIRST_LOOK
        needs, needs_n = await self._needs_you()
        working, working_n = await self._working()
        done, done_n = await self._done(since)
        return {"needsYou": needs, "working": working, "doneSince": done,
                "counts": {"needsYou": needs_n, "working": working_n, "doneSince": done_n},
                "since": when(since), "sinceVisit": seen is not None}

    async def seen(self, user_id: str) -> datetime:
        """From now on, "done since" counts from this moment."""
        now = self.clock()
        user = await self.session.get(User, user_id)
        if user is not None:
            user.last_seen_at = now
            await self.session.flush()
        return now
