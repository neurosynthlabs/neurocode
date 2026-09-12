"""The gates: approvals, final decisions, and the small settings a person changes on a screen.

One rule runs through all of it — **a decision is final**. An approval that was already answered
cannot be answered again, and a decision that was recorded cannot be quietly re-recorded. Those were
`if` statements in two different route handlers before; here each is one method, said once.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import Decision, Pref
from ..repositories import ActivityRepository, ApprovalRepository, NotFound
from .errors import Refused


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.approvals = ApprovalRepository(session)
        self.activity = ActivityRepository(session)

    async def decide(self, ref: str, decision: str, *, by_id: str, by_name: str):
        """Approve or deny, once. Returns the approval; the caller resumes whatever was waiting on it."""
        if decision not in ("approve", "deny"):
            raise Refused("A gate is approved or denied.", status=422)
        approval = await self.approvals.by_ref(ref)
        if approval is None:
            raise NotFound(f"approval {ref}")
        if approval.status != "pending":
            raise Refused(f"{ref} was already {approval.status} — a decision is final.")
        approval.status = "approved" if decision == "approve" else "denied"
        approval.decided_at, approval.decided_by = utcnow(), by_id
        await self.session.flush()
        await self.activity.record(actor=by_name, actor_kind="human",
                                   action="Approved" if decision == "approve" else "Denied",
                                   detail=f"{ref} · {approval.title}", project_id=approval.project_id,
                                   level="ok" if decision == "approve" else "warn")
        return approval


class DecisionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activity = ActivityRepository(session)

    async def record(self, key: str, *, verdict: str, action: str, detail: str = "",
                     project_id: str | None = None, level: str = "ok", by_id: str | None = None,
                     by_name: str = "") -> Decision:
        if await self.session.get(Decision, key) is not None:
            raise Refused(f"{key} was already decided — a decision is final.")
        made = Decision(id=key, subject=action, verdict=verdict, note=detail, project_id=project_id,
                        by_user_id=by_id)
        self.session.add(made)
        await self.session.flush()
        await self.activity.record(actor=by_name or "System", actor_kind="human", action=action,
                                   detail=detail, project_id=project_id, level=level)
        return made


class PrefService:
    """A screen setting. Written every time; only a deliberate change is worth a line in the log."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activity = ActivityRepository(session)

    async def set(self, key: str, value: Any, *, detail: str = "", project_id: str | None = None,
                  by: str = "") -> Pref:
        pref = await self.session.get(Pref, key)
        if pref is None:
            pref = Pref(id=key, value=value)
            self.session.add(pref)
        else:
            pref.value = value
        await self.session.flush()
        if detail:
            await self.activity.record(actor=by or "System", actor_kind="human",
                                       action="Setting changed", detail=detail, project_id=project_id)
        return pref
