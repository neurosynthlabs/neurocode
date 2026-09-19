"""Ask memory, argue an idea with itself, pull candidate facts out of pasted text.

The features themselves live in `ai/features.py` — the prompts, the validation, and the offline
versions of the two that have an honest one. What changed is where
the words come from and where the answer goes. A question is answered from facts in Postgres, ranked
by retrieval where the project has been indexed and by memory's own full-text search where it has
not; and a brainstorm is a row, with its stages, its case against it and its roadmap in columns,
rather than one document nobody could query.

Two things hold everywhere here. The gateway is blocking by nature, so every call into it crosses a
worker thread and never the event loop. And every answer says which model wrote it. Asking and
extracting answer with no key configured at all — the offline rules write it, and a lane that tried
and failed is recorded on the feed rather than passing unnoticed. A brainstorm needs a model, and
without one it is refused in words that say how to add one; nothing is kept.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import features
from ..ai.gateway import Gateway, Result
from ..models import Brainstorm, MemoryFact, Project
from ..repositories.base import NotFound
from ..repositories.knowledge import MemoryRepository
from ..repositories.platform import BrainstormRepository
from ..repositories.work import ActivityRepository, ProjectRepository
from ..schemas.ai import brainstorm_nodes
from .errors import needs_a_model
from .knowledge import MemoryService
from .retrieval import RetrievalService

#: How many facts an answer may lean on, and how many pieces retrieval is asked for to find them —
#: most of what it ranks for a project is code, and only a remembered fact can be cited.
FACTS = 6
RETRIEVED = 24


def _project_doc(project: Project | None) -> dict[str, Any] | None:
    """What a feature is told about the project, from the row rather than a stored document."""
    return None if project is None else {"id": project.id, "name": project.name,
                                         "stack": project.stack or []}


class AiFeatureService:
    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.memory = MemoryRepository(session)
        self.projects = ProjectRepository(session)
        self.brainstorms = BrainstormRepository(session)
        self.activity = ActivityRepository(session)

    # ── what all three need ──────────────────────────────────────
    async def _project(self, project_id: str | None) -> Project | None:
        """A project named has to be one that exists: its id is a foreign key now, so the dangling
        string the old store would happily have kept cannot be written at all."""
        if not project_id:
            return None
        project = await self.projects.get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        return project

    async def _fell_back(self, result: Result[Any], project_id: str | None) -> None:
        """A lane that could not answer is worth a line of its own: the answer still arrived, but it
        came from the rules, and a week later nobody remembers which."""
        if result.fallback:
            await self.activity.record(
                actor="NeuroCode", actor_kind="system", action="AI fell back",
                detail=f"{result.fallback}. The {result.provider.model} answered instead.",
                level="warn", project_id=project_id)

    # ── the facts a question is answered from ────────────────────
    async def _ranked(self, question: str, project_id: str) -> list[str]:
        """The refs retrieval would put first — the remembered ones only, since those are what an
        answer may cite. A project nobody has indexed has nothing to rank, and says so by being empty."""
        retrieval = RetrievalService(self.session, self.gateway)
        if not (await retrieval.summary(project_id))["chunks"]:
            return []
        found = await retrieval.search(project_id, question, limit=RETRIEVED)
        return [piece["ref"] for piece in found if piece["kind"] == "memory"][:FACTS]

    async def _searched(self, question: str, project_id: str | None) -> list[MemoryFact]:
        """Memory's own search. A fact that belongs to no project belongs to every project, so a
        question about one is answered from both — which is what the old store's filter meant."""
        found = await self.memory.search(question, project=project_id, limit=FACTS, mode="any")
        if project_id:
            found += await self.memory.search(question, project="global", limit=FACTS, mode="any")
        return found

    async def _facts(self, question: str, project_id: str | None) -> list[dict[str, Any]]:
        """The few facts that bear on the question, best first: retrieval where the project has been
        indexed — the same facts, found by meaning as well as by words — then search for the rest, so
        an un-indexed project still gets a real answer."""
        found: list[MemoryFact] = []
        if project_id and (refs := await self._ranked(question, project_id)):
            # Held facts only: an index built before a fact was archived may still name it.
            by_ref = {f.ref: f for f in await self.memory.by_refs(refs)}
            found = [by_ref[ref] for ref in refs if ref in by_ref]
        seen = {fact.ref for fact in found}
        for fact in await self._searched(question, project_id):
            if len(found) >= FACTS:
                break
            if fact.ref not in seen:
                found.append(fact)
                seen.add(fact.ref)
        return [{"ref": f.ref, "title": f.title, "body": f.body} for f in found[:FACTS]]

    # ── the three features ───────────────────────────────────────
    async def ask(self, question: str, project_id: str | None, *, by: str,
                  by_id: str | None = None) -> dict[str, Any]:
        """What memory holds on a question, citing only the facts it was actually handed."""
        await self._project(project_id)
        facts = await self._facts(question, project_id)
        result = await asyncio.to_thread(features.ask, self.gateway, question, facts, actor=by_id,
                                         project_id=project_id)
        await self._fell_back(result, project_id)
        cited = [f for f in facts if f["ref"] in result.data.citations]
        await MemoryService(self.session).recall([f["ref"] for f in cited], via="ask", context=None)
        await self.activity.record(
            actor=by, actor_kind="human", action="Asked memory",
            detail=f"“{question[:120]}” · {len(cited)} facts cited · {result.provider.model}",
            project_id=project_id)
        return {"answer": result.data.answer,
                "citations": [{"ref": f["ref"], "title": f["title"]} for f in cited],
                **result.meta()}

    async def brainstorm(self, idea: str, project_id: str | None, *, by: str,
                         by_id: str | None = None) -> Brainstorm:
        """An idea, expanded and then argued against by a model — and kept, so it can be read again."""
        project = await self._project(project_id)
        doc = _project_doc(project)
        result = await asyncio.to_thread(needs_a_model, lambda: features.brainstorm(
            self.gateway, idea, doc, actor=by_id, project_id=project_id))
        brief = result.data
        ref = await self.brainstorms.next_ref()
        # A random suffix beside the number, so the id is never a bare `b1` that a client may already
        # hold for something that is not this brainstorm.
        bid = f"b{ref.split('-')[-1]}-{uuid4().hex[:8]}"
        # `verdict` and `score` stay as the database left them: this feature expands an idea and puts
        # the case against it, and nothing in it has earned the right to score the idea for you.
        made = await self.brainstorms.add(Brainstorm(
            id=bid, ref=ref, project_id=project_id, idea=idea, requested_by=by,
            nodes=brainstorm_nodes(bid, title=brief.title, problem=brief.problem,
                                   audience=brief.audience, value=brief.value, metrics=brief.metrics,
                                   questions=brief.questions),
            devils_advocate=list(brief.risks), mvp=list(brief.mvp),
            roadmap=[{"phase": phase.phase, "items": list(phase.items)} for phase in brief.roadmap],
            compiler=result.meta()))
        await self.activity.record(actor=by, actor_kind="human", action="Brainstormed",
                                   detail=f"{ref} · {brief.title} · {result.provider.model}",
                                   level="ok", project_id=project_id)
        return made

    async def extract(self, text: str, project_id: str | None, *,
                      by_id: str | None = None) -> dict[str, Any]:
        """Candidates, not memory: nothing is remembered until a person keeps the ones worth keeping."""
        project = await self._project(project_id)
        result = await asyncio.to_thread(features.extract, self.gateway, text, _project_doc(project),
                                         actor=by_id, project_id=project_id)
        await self._fell_back(result, project_id)
        return {"facts": [candidate.model_dump() for candidate in result.data.facts], **result.meta()}
