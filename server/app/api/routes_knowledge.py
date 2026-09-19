"""Memory over HTTP: facts, search, pins, archives, and the conflicts between facts.

Search is now Postgres full text over a column the database generates from the fact itself, ranked
with pinned facts first. The route did not change; what it asks did.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.knowledge import HITS_CEILING, ConflictRepository, MemoryHitRepository, MemoryRepository
from ..schemas import conflict_json, fact_json
from ..schemas.knowledge import recall_json
from ..schemas.work import when
from ..services.identity import Person
from ..services.knowledge import MemoryService, NewFact
from .deps import current_person, require, session

router = APIRouter()
#: The window the screen's "retired" figure covers.
RETIRED_DAYS = 30
Category = Literal["human", "project", "architecture", "business_rules", "legacy", "database", "bugs",
                   "decisions", "incidents", "preferences", "code"]


class PinIn(BaseModel):
    pinned: bool


class ResolveIn(BaseModel):
    keep: Literal["a", "b"]


class ConflictIn(BaseModel):
    a: str = Field(min_length=1, max_length=40)
    b: str = Field(min_length=1, max_length=40)
    topic: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=2000)
    severity: Literal["low", "medium", "high"] = "medium"


class FactIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    category: Category = "project"
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    reason: str = Field(default="", max_length=500)


class FactsIn(BaseModel):
    #: None — or "global", as the screens have always spelled it — files the facts under the workspace.
    projectId: str | None = Field(default=None, max_length=60)
    facts: list[FactIn] = Field(min_length=1, max_length=20)


@router.get("/memory", dependencies=[Depends(current_person)])
async def memory(q: str = "", category: Category | None = None, project: str | None = None,
                 include_archived: bool = False,
                 open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    found = await MemoryService(open_session).search(q, category=category, project=project,
                                                     include_archived=include_archived)
    return [fact_json(f) for f in found]


@router.get("/memory/stats", dependencies=[Depends(current_person)])
async def memory_stats(project: str | None = Query(default=None, max_length=60),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The Memory screen's figures, counted by the database. They were counted from the list, which is
    a page of at most a few hundred facts, so a large memory read as a small one."""
    found = await MemoryRepository(open_session).stats(project=project, retired_days=RETIRED_DAYS)
    return {"held": found.held, "pinned": found.pinned, "global": found.workspace,
            "recalled24h": found.recalled_24h, "retired": found.retired, "retiredDays": RETIRED_DAYS,
            "byCategory": {category: {"held": c.held, "pinned": c.pinned, "recalled24h": c.recalled_24h,
                                      "lastUsedAt": when(c.last_used_at)}
                           for category, c in found.by_category.items()}}


@router.post("/memory/facts", status_code=201)
async def add_facts(body: FactsIn, who: Person = Depends(require("memory:write")),
                    open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    added = await MemoryService(open_session).add(
        [NewFact(title=f.title, body=f.body, category=f.category, confidence=f.confidence, reason=f.reason)
         for f in body.facts],
        project_id=body.projectId, by=who.name, source=f"Added from text by {who.name}")
    return [fact_json(f) for f in added]


@router.post("/memory/{ref}/pin")
async def pin(ref: str, body: PinIn, who: Person = Depends(require("memory:write")),
              open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return fact_json(await MemoryService(open_session).pin(ref, body.pinned, who.name))


@router.post("/memory/{ref}/archive")
async def archive(ref: str, who: Person = Depends(require("memory:write")),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Archived, never deleted — a fact that turned out to be wrong is still evidence."""
    return fact_json(await MemoryService(open_session).archive(ref, who.name))


@router.get("/memory/hits", dependencies=[Depends(current_person)])
async def hits(limit: int = Query(default=50, ge=1, le=HITS_CEILING),
               open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The latest recalls, newest first: which fact, which feature used it, for what, and when."""
    return [recall_json(h) for h in await MemoryHitRepository(open_session).recent(limit)]


@router.get("/memory/conflicts", dependencies=[Depends(current_person)])
async def conflicts(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    page = await ConflictRepository(open_session).open()
    return [conflict_json(c) for c in page.items]


@router.post("/memory/conflicts", status_code=201)
async def file_conflict(body: ConflictIn, who: Person = Depends(require("memory:write")),
                        open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Nothing detects a contradiction on its own; a person who notices one files it here."""
    made = await MemoryService(open_session).conflict(body.a, body.b, topic=body.topic, detail=body.detail,
                                                      severity=body.severity.upper(), by=who.name)
    return conflict_json(made)


@router.post("/memory/conflicts/{cid}/resolve")
async def resolve(cid: str, body: ResolveIn, who: Person = Depends(require("memory:write")),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await MemoryService(open_session).resolve(cid, body.keep, who.name)
