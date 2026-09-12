"""What the workspace remembers, and what it does when two things it remembers disagree.

The rule that matters: **a fact is never deleted.** Archiving keeps it, because a belief that turned
out to be wrong is still the reason someone made a decision last year.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import MemoryFact
from ..repositories import ActivityRepository, NotFound, ProjectRepository
from ..repositories.knowledge import ConflictRepository, MemoryRepository
from .errors import Refused


@dataclass(slots=True)
class NewFact:
    title: str
    body: str
    category: str = "project"
    confidence: str = "MEDIUM"
    reason: str = ""


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.facts = MemoryRepository(session)
        self.conflicts = ConflictRepository(session)
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def search(self, q: str = "", *, category: str | None = None, project: str | None = None,
                     include_archived: bool = False) -> list[MemoryFact]:
        return await self.facts.search(q, category=category, project=project,
                                       include_archived=include_archived)

    async def add(self, new: list[NewFact], *, project_id: str, by: str,
                  source: str = "") -> list[MemoryFact]:
        """Facts arrive together — from a plan's answer, or from pasted notes — so they are written
        together, under one reason, and the activity log says how many."""
        if not new:
            raise Refused("There is nothing to remember.", status=422)
        if project_id != "global" and await self.projects.get(project_id) is None:
            raise NotFound(f"project {project_id}")
        added: list[MemoryFact] = []
        for item in new:
            ref = await self.facts.next_ref()
            fact = await self.facts.add(MemoryFact(
                id=f"m{ref.split('-')[-1]}", ref=ref, category=item.category, title=item.title.strip(),
                body=item.body.strip(), reason=item.reason.strip() or f"Added by {by}.",
                source=source or f"Added by {by}", confidence=item.confidence, strength=80,
                project_id=None if project_id == "global" else project_id))
            await self.facts.tag(fact.id, [item.category.replace("_", "-")])
            added.append(fact)
        await self.activity.record(actor=by, actor_kind="human", action="Memory added",
                                   detail=f"{len(added)} fact{'s' if len(added) > 1 else ''} · "
                                          f"{added[0].title[:80]}",
                                   level="ok", project_id=added[0].project_id)
        return added

    async def pin(self, ref: str, pinned: bool, by: str) -> MemoryFact:
        fact = await self.facts.by_ref(ref)
        if fact is None:
            raise NotFound(f"fact {ref}")
        fact.pinned = pinned
        await self.session.flush()
        await self.activity.record(actor=by, actor_kind="human",
                                   action="Memory pinned" if pinned else "Memory unpinned",
                                   detail=f"{ref} · {fact.title}", project_id=fact.project_id)
        return fact

    async def archive(self, ref: str, by: str) -> MemoryFact:
        fact = await self.facts.by_ref(ref)
        if fact is None:
            raise NotFound(f"fact {ref}")
        if fact.archived:
            raise Refused(f"{ref} is already archived.")
        await self.facts.archive(fact)
        await self.activity.record(actor=by, actor_kind="human", action="Memory archived",
                                   detail=f"{ref} · {fact.title} — recoverable, never deleted",
                                   level="warn", project_id=fact.project_id)
        return fact

    async def use(self, facts: list[MemoryFact]) -> None:
        """Recording that a fact was actually used is what makes "strength" mean something."""
        for fact in facts:
            fact.hits += 1
            fact.last_used_at = utcnow()
        await self.session.flush()

    async def resolve(self, conflict_id: str, keep: str, by: str) -> dict[str, Any]:
        """Keep one side, or escalate to an ADR and keep both until someone decides properly."""
        conflict = await self.conflicts.get(conflict_id)
        if conflict is None:
            raise NotFound(f"conflict {conflict_id}")
        if conflict.status != "open":
            raise Refused(f"'{conflict.topic}' was already resolved.")
        if keep == "adr":
            await self.activity.record(actor=by, actor_kind="human", action="Conflict escalated",
                                       detail=f"{conflict.topic} · written up as an ADR. Both facts stay "
                                              f"until it is decided.", level="warn")
        else:
            sides = {"a": (conflict.a, conflict.b), "b": (conflict.b, conflict.a)}
            if keep not in sides:
                raise Refused("Keep 'a', keep 'b', or escalate to an ADR.", status=422)
            kept, lost = sides[keep]
            loser = await self.facts.get(str(lost.get("id") or lost)) if isinstance(lost, dict) else None
            if loser is not None:
                await self.facts.archive(loser)
            await self.activity.record(
                actor=by, actor_kind="human", action="Conflict resolved",
                detail=f"{conflict.topic} · kept {kept.get('ref', keep) if isinstance(kept, dict) else keep}"
                       f"{f', archived {loser.ref} as superseded' if loser else ''}", level="ok")
        conflict.status, conflict.resolution = "resolved", {"keep": keep, "by": by}
        await self.session.flush()
        return {"id": conflict.id, "status": conflict.status, "resolution": conflict.resolution}
