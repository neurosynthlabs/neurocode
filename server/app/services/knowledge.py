"""What the workspace remembers, and what it does when two things it remembers disagree.

The rule that matters: **a fact is never deleted.** Archiving keeps it, because a belief that turned
out to be wrong is still the reason someone made a decision last year.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import MemoryConflict, MemoryFact, MemoryTag
from ..repositories import ActivityRepository, NotFound, ProjectRepository
from ..repositories.knowledge import ConflictRepository, MemoryHitRepository, MemoryRepository
from .errors import Refused

#: The features that recall facts: Ask memory's citations, a session's grounding and its memory tools,
#: research citations, and the facts a plan was compiled from.
Feature = Literal["ask", "chat", "retrieval", "research", "compile"]


@dataclass(slots=True)
class NewFact:
    title: str
    body: str
    category: str = "project"
    confidence: str = "MEDIUM"
    reason: str = ""
    #: What the fact rests on, when something concrete does — an eval result names its suite, run and
    #: case, so a lesson learned from a failure can be traced back to it.
    evidence: list[dict[str, Any]] = field(default_factory=list)


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.facts = MemoryRepository(session)
        self.conflicts = ConflictRepository(session)
        self.hits = MemoryHitRepository(session)
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def search(self, q: str = "", *, category: str | None = None, project: str | None = None,
                     include_archived: bool = False,
                     hidden: frozenset[str] = frozenset()) -> list[MemoryFact]:
        """`hidden` is passed by the Memory screen's route and nowhere else: what a model retrieves
        is already cut to the project it is working in, and has no person to be narrowed by."""
        return await self.facts.search(q, category=category, project=project,
                                       include_archived=include_archived, hidden=hidden)

    async def add(self, new: list[NewFact], *, project_id: str | None, by: str,
                  source: str = "") -> list[MemoryFact]:
        """Facts arrive together — from a plan's answer, or from pasted notes — so they are written
        together, under one reason, and the activity log says how many."""
        if not new:
            raise Refused("There is nothing to remember.", status=422)
        workspace = project_id in (None, "global")
        if not workspace and await self.projects.get(project_id) is None:
            raise NotFound(f"project {project_id}")
        added: list[MemoryFact] = []
        for item in new:
            ref = await self.facts.next_ref()
            # The tag is built into the fact rather than added beside it. Added beside it, the fact's own
            # `tags` collection was never loaded, and the first thing to read it — the JSON for the
            # response — lazy-loaded inside async code and raised. Every fact pasted from notes failed.
            fact = await self.facts.add(MemoryFact(
                id=f"m{ref.split('-')[-1]}", ref=ref, category=item.category, title=item.title.strip(),
                body=item.body.strip(), reason=item.reason.strip() or f"Added by {by}.",
                source=source or f"Added by {by}", confidence=item.confidence,
                project_id=None if workspace else project_id, evidence=list(item.evidence),
                tags=[MemoryTag(tag=item.category.replace("_", "-"))]))
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

    async def recall(self, refs: Iterable[str], *, via: Feature, context: str | None) -> list[MemoryFact]:
        """Record that these facts were used — cited, or put in front of a model — once each.

        Called with whatever refs a feature handled, memory's or not: a retrieval result mixes code,
        documents and facts, and only the refs that name a fact are kept. Each fact is counted once
        however many times the same answer met it, and its count is read back before the fact is
        flushed again, so the change streamed to open screens carries the new number, not the old.
        """
        wanted = list(dict.fromkeys(r for r in refs if r))
        facts = await self.facts.by_refs(wanted)
        if not facts:
            return []
        await self.hits.record(facts, feature=via, context=context)
        await self.session.execute(select(MemoryFact).where(MemoryFact.id.in_([f.id for f in facts]))
                                   .execution_options(populate_existing=True))
        now = utcnow()
        for fact in facts:
            fact.last_used_at = now
        await self.session.flush()
        return facts

    async def conflict(self, a_ref: str, b_ref: str, *, topic: str, detail: str, severity: str,
                       by: str) -> MemoryConflict:
        """A person says two facts cannot both be true. Both stay as they are until someone rules."""
        if a_ref == b_ref:
            raise Refused("A fact cannot contradict itself. Pick two different facts.", status=422)
        a, b = await self.facts.by_ref(a_ref), await self.facts.by_ref(b_ref)
        if a is None:
            raise NotFound(f"fact {a_ref}")
        if b is None:
            raise NotFound(f"fact {b_ref}")
        # An archived fact is no longer held, so it contradicts nothing — and filed anyway, "keeping" it
        # would archive the live side and leave neither.
        for side in (a, b):
            if side.archived:
                raise Refused(f"{side.ref} is archived, so it is no longer held and cannot contradict "
                              f"anything.")
        if await self.conflicts.open_between(a.id, b.id) is not None:
            raise Refused(f"{a.ref} and {b.ref} are already marked as contradicting each other.")
        made = await self.conflicts.add(MemoryConflict(
            id=f"cf-{uuid4().hex[:12]}", topic=topic.strip(), status="open", severity=severity,
            detail=detail.strip(), a=a.id, b=b.id))
        await self.activity.record(actor=by, actor_kind="human", action="Conflict recorded",
                                   detail=f"{made.topic} · {a.ref} against {b.ref}", level="warn",
                                   project_id=a.project_id or b.project_id)
        return made

    async def resolve(self, conflict_id: str, keep: str, by: str) -> dict[str, Any]:
        """Keep one side; the other is archived as superseded, never deleted."""
        conflict = await self.conflicts.get(conflict_id)
        if conflict is None:
            raise NotFound(f"conflict {conflict_id}")
        if conflict.status != "open":
            raise Refused(f"'{conflict.topic}' was already resolved.")
        sides = {"a": (conflict.a, conflict.b), "b": (conflict.b, conflict.a)}
        if keep not in sides:
            raise Refused("Keep 'a' or keep 'b'.", status=422)
        kept_id, lost_id = sides[keep]
        # Both sides are fact ids, and a foreign key says so — so the loser is simply fetched.
        # This read for a document with an "id" inside it, which a plain id never had, so the
        # losing fact was never actually archived and the conflict resolved to nothing.
        winner, loser = await self.facts.get(kept_id), await self.facts.get(lost_id)
        if winner is not None and winner.archived:
            # Archived since the conflict was filed: keeping it would archive the live side too.
            raise Refused(f"{winner.ref} has been archived since this conflict was filed, so it cannot be "
                          f"the one kept. Keep the other side.")
        if loser is not None and not loser.archived:
            await self.facts.archive(loser)
        await self.activity.record(
            actor=by, actor_kind="human", action="Conflict resolved",
            detail=f"{conflict.topic} · kept {winner.ref if winner else keep}"
                   f"{f', archived {loser.ref} as superseded' if loser else ''}", level="ok")
        conflict.status, conflict.resolution = "resolved", {"keep": keep, "by": by}
        await self.session.flush()
        return {"id": conflict.id, "status": conflict.status, "resolution": conflict.resolution}
