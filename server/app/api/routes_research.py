"""The Research screen: a question sent across the workspace's retrieval, its angles and citations.

Starting one answers at once with the queued report; the investigation runs in the background with
sessions of its own, and the screen reads the report again while it is queued or running. Reading
needs a session; starting and stopping one spends model calls, so both need `ai:use`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..schemas.research import report_json, report_row_json
from ..services import research as research_jobs
from ..services.identity import Person
from ..services.research import MAX_QUESTION, ResearchService
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter(prefix="/research")

#: The most reports one page of the list holds, however the caller spells the number.
MAX_LIST = 100


class ResearchIn(BaseModel):
    question: str = Field(min_length=3, max_length=MAX_QUESTION)
    projectId: str = Field(max_length=80)
    kinds: list[str] = Field(default_factory=lambda: ["code", "doc", "memory"], min_length=1, max_length=3)


@router.get("", dependencies=[Depends(current_person)])
async def reports(project: str | None = None, limit: int = Query(default=50, ge=1),
                  offset: int = Query(default=0, ge=0),
                  open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    rows = await ResearchService(open_session).newest(project, limit=min(limit, MAX_LIST), offset=offset)
    return [report_row_json(row) for row in rows]


@router.post("", status_code=201)
async def start(body: ResearchIn, jobs: BackgroundTasks, who: Person = Depends(require("ai:use")),
                open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    row = await ResearchService(open_session).start(body.question, body.projectId, body.kinds, who)
    await hand_off(open_session, jobs, research_jobs.investigate, db, gw, row.report.ref, who.id)
    return report_row_json(row)


@router.get("/{ref}", dependencies=[Depends(current_person)])
async def report(ref: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return report_json(await ResearchService(open_session).detail(ref))


@router.post("/{ref}/cancel")
async def cancel(ref: str, who: Person = Depends(require("ai:use")),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Stop at the next checkpoint. The status changes when the worker gets there, not here."""
    return report_row_json(await ResearchService(open_session).cancel(ref, who))
