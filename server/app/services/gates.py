"""The gates: approvals, final decisions, and the small settings a person changes on a screen.

One rule runs through all of it — **a decision is final**. An approval that was already answered
cannot be answered again, and a decision that was recorded cannot be quietly re-recorded. Those were
`if` statements in two different route handlers before; here each is one method, said once.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import Decision, Pref
from ..repositories import ActivityRepository, ApprovalRepository, NotFound, ProjectRepository
from ..repositories.runtime import ResultsRepository
from ..schemas.work import test_rule_json, when
from .errors import Refused
from .testing import _commands


async def _known_project(session: AsyncSession, project_id: str | None) -> None:
    """A decision or a setting is about the workspace unless it names a project, and a project it
    names has to exist: the log line it writes points at it, and a dangling id would be a 500."""
    if project_id and await ProjectRepository(session).get(project_id) is None:
        raise NotFound(f"project {project_id}")


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


class RuleService:
    """The standing rules the runtime really applies, and nothing it does not.

    There is one kind today: a project's answer to its first test run. It is kept as a setting the
    test step reads before it runs anything, so listing those settings is listing the rule itself —
    not a description of it that could drift. There used to be a table of tool rules beside it that
    nothing in the runtime consulted; it was dropped, because a rule that is shown but never enforced is
    a false promise.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.results = ResultsRepository(session)

    async def rules(self, *, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        answers = await self.results.standing_answers(limit=limit, offset=offset)
        projects = [project for project, _ in answers]
        # Only a project whose code is on this machine has a command to find; asking for one with no
        # source would look in whatever directory the server happens to run from.
        commands = await asyncio.to_thread(_commands, [p for p in projects if p.source_kind])
        deciders = await self.results.gate_deciders([p.id for p in projects])
        rows = []
        for project, setting in answers:
            answer = "allowed" if setting.value == "allowed" else "refused"
            decided = deciders.get((project.id, "approved" if answer == "allowed" else "denied"))
            row = test_rule_json(project, setting, answer=answer, command=commands.get(project.id),
                                 decided_by=decided[0] if decided else None)
            if decided:
                # Who and when from the same approval. The setting's time is only the fallback, for an
                # answer no person's decision wrote.
                row["decidedAt"] = when(decided[1])
            rows.append(row)
        return rows


class DecisionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activity = ActivityRepository(session)

    async def record(self, key: str, *, verdict: str, action: str, detail: str = "",
                     project_id: str | None = None, level: str = "ok", by_id: str | None = None,
                     by_name: str = "") -> Decision:
        if await self.session.get(Decision, key) is not None:
            raise Refused(f"{key} was already decided — a decision is final.")
        await _known_project(self.session, project_id)
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
        await _known_project(self.session, project_id)
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
