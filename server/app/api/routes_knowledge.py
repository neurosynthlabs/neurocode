"""Memory over HTTP: facts, search, pins, archives, and the conflicts between facts.

Search is now Postgres full text over a column the database generates from the fact itself, ranked
with pinned facts first. The route did not change; what it asks did.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.knowledge import ConflictRepository
from ..schemas import conflict_json, fact_json
from ..services.identity import Person
from ..services.knowledge import MemoryService, NewFact
from .deps import current_person, require, session

router = APIRouter()
Category = Literal["human", "project", "architecture", "business_rules", "legacy", "database", "bugs",
                   "decisions", "incidents", "preferences", "code"]


class PinIn(BaseModel):
    pinned: bool


class ResolveIn(BaseModel):
    keep: Literal["a", "b", "adr"]


class FactIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    category: Category = "project"
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    reason: str = Field(default="", max_length=500)


class FactsIn(BaseModel):
    projectId: str = Field(default="global", max_length=60)
    facts: list[FactIn] = Field(min_length=1, max_length=20)


@router.get("/memory", dependencies=[Depends(current_person)])
async def memory(q: str = "", category: Category | None = None, project: str | None = None,
                 include_archived: bool = False,
                 open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    found = await MemoryService(open_session).search(q, category=category, project=project,
                                                     include_archived=include_archived)
    return [fact_json(f) for f in found]


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


@router.get("/memory/conflicts", dependencies=[Depends(current_person)])
async def conflicts(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    page = await ConflictRepository(open_session).open()
    return [conflict_json(c) for c in page.items]


@router.post("/memory/conflicts/{cid}/resolve")
async def resolve(cid: str, body: ResolveIn, who: Person = Depends(require("memory:write")),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await MemoryService(open_session).resolve(cid, body.keep, who.name)
