"""The agents people wrote: the workspace's own, and each project's.

An agent a repository declares in `.neurocode/agents` or `.claude/agents` is read from disk by the
service and never stored, so it has no questions here. What is here is the stored ones, and the two
counts the Agents screen shows beside every agent — sessions asked through it and run steps it owned —
answered by the database rather than counted in Python.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select

from ..models import Chat, CustomAgent, Run, RunStep, User
from .base import Repository

#: How many agents one scope may hold. A workspace with more than this is not reading its own roster.
MAX_AGENTS = 200


class CustomAgentRepository(Repository[CustomAgent]):
    model = CustomAgent

    async def visible(self, project_id: str | None) -> list[CustomAgent]:
        """The workspace's agents and, when a project is named, that project's — workspace first, then by
        name, so a screen lists them in the order it resolves them."""
        where = (or_(CustomAgent.project_id.is_(None), CustomAgent.project_id == project_id)
                 if project_id else CustomAgent.project_id.is_(None))
        return await self.list(where, order_by=[CustomAgent.project_id.is_not(None), func.lower(CustomAgent.name)],
                               limit=MAX_AGENTS * 2)

    async def named(self, project_id: str | None, name: str) -> CustomAgent | None:
        """The agent of exactly this scope called `name`, whatever its case. Postgres lets two workspace
        rows share a name (a null project is never equal to another), so the service asks here first."""
        scope = CustomAgent.project_id.is_(None) if project_id is None else CustomAgent.project_id == project_id
        return await self.one(scope, func.lower(CustomAgent.name) == name.strip().lower())

    async def in_scope(self, project_id: str | None) -> int:
        scope = CustomAgent.project_id.is_(None) if project_id is None else CustomAgent.project_id == project_id
        return await self.count(scope)

    async def sessions_asked(self, keys: Sequence[str],
                             project_id: str | None) -> dict[str, tuple[int, datetime | None]]:
        """agent key → how many sessions were asked through it, and when one of them last moved."""
        if not keys:
            return {}
        stmt = (select(Chat.agent, func.count(), func.max(Chat.last_at)).where(Chat.agent.in_(list(keys)))
                .group_by(Chat.agent))
        if project_id:
            stmt = stmt.where(Chat.project_id == project_id)
        return {key: (int(n), last) for key, n, last in (await self.session.execute(stmt)).all()}

    async def steps_owned(self, names: Sequence[str], project_id: str | None) -> dict[str, tuple[int, int]]:
        """agent name → the writing steps it owned in runs, and how many of them finished done. A step
        names its owner as the plan did, so the name is what is counted."""
        if not names:
            return {}
        stmt = (select(RunStep.agent, func.count(), func.count().filter(RunStep.status == "done"))
                .join(Run, Run.id == RunStep.run_id)
                .where(RunStep.agent.in_(list(names)), RunStep.kind == "edit").group_by(RunStep.agent))
        if project_id:
            stmt = stmt.where(Run.project_id == project_id)
        return {name: (int(n), int(done)) for name, n, done in (await self.session.execute(stmt)).all()}

    async def authors(self, user_ids: Sequence[str]) -> dict[str, str]:
        """user id → name, for whoever wrote these agents. One statement for the whole list."""
        wanted = sorted({u for u in user_ids if u})
        if not wanted:
            return {}
        rows = await self.session.execute(select(User.id, User.name).where(User.id.in_(wanted)))
        return {uid: name for uid, name in rows.all()}
