"""Data files opened in the Workbench — CSV, TSV, Parquet, JSON Lines and SQLite: paged rows, column statistics and a
read-only SQL box over the file.

A data file is a file on this machine, so these routes use the machine's door (`operator`: the setting, a session,
`machine:access`) and its resolver. A path is absolute, or a project path when `projectId` names the project it belongs
to. Everything here reads; nothing writes, so nothing lands in the activity log.

Wave 2d, builder L4.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..services import dataview, machine
from .deps import session
from .routes_machine import MAX_PATH, operator

router = APIRouter(prefix="/data", tags=["data"], dependencies=[Depends(operator)])

PathQ = Query(min_length=1, max_length=MAX_PATH)
ProjectQ = Query(default=None, max_length=40)
TableQ = Query(default=None, max_length=512)


class QueryIn(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH)
    projectId: str | None = Field(default=None, max_length=40)
    #: For a SQLite file only a statement names its tables itself; for the rest the file is the view `data`.
    sql: str = Field(min_length=1, max_length=dataview.MAX_SQL)


async def _answer(call: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any] | JSONResponse:
    """The work on a worker thread, with macOS's privacy refusal answered in full, as the machine routes do."""
    try:
        return await asyncio.to_thread(call, *args, **kwargs)
    except machine.NeedsOsPermission as blocked:
        return JSONResponse(blocked.as_json(), status_code=blocked.status)


@router.get("/open")
async def open_data(path: str = PathQ, projectId: str | None = ProjectQ, table: str | None = TableQ,
                    open_session: AsyncSession = Depends(session)) -> Any:
    """The file: format, size, modified, columns ({name, type, kind}) and row count; a SQLite file also lists its
    tables and views ({name, kind, rows}) and says which one `table` (default: the first) is."""
    where = await dataview.resolve(open_session, path, projectId)
    return await _answer(dataview.describe, where, table)


@router.get("/rows")
async def data_rows(path: str = PathQ, projectId: str | None = ProjectQ, table: str | None = TableQ,
                    offset: int = Query(default=0, ge=0, le=10**12),
                    limit: int = Query(default=100, ge=1, le=dataview.MAX_PAGE),
                    sort: str | None = Query(default=None, max_length=512), desc: bool = False,
                    open_session: AsyncSession = Depends(session)) -> Any:
    """One page of rows as arrays in column order, sorted on the server (nulls last) when `sort` names a column;
    `total` is every row."""
    where = await dataview.resolve(open_session, path, projectId)
    return await _answer(dataview.rows, where, table=table, offset=offset, limit=limit, sort=sort, desc=desc)


@router.get("/stats")
async def data_stats(path: str = PathQ, projectId: str | None = ProjectQ, table: str | None = TableQ,
                     open_session: AsyncSession = Depends(session)) -> Any:
    """Per column: type, kind, nulls, distinct (`distinctApprox` above a million rows), min, max, mean for numbers."""
    where = await dataview.resolve(open_session, path, projectId)
    return await _answer(dataview.stats, where, table=table)


@router.get("/plot")
async def data_plot(path: str = PathQ, column: str = Query(min_length=1, max_length=512),
                    projectId: str | None = ProjectQ, table: str | None = TableQ,
                    bins: int = Query(default=20, ge=2, le=dataview.MAX_BINS),
                    y: str | None = Query(default=None, max_length=512),
                    open_session: AsyncSession = Depends(session)) -> Any:
    """What to draw for one column: `histogram` (numbers), `top` (text, yes/no) or `line` (dates: the count per
    period, or the mean of `y`)."""
    where = await dataview.resolve(open_session, path, projectId)
    return await _answer(dataview.plot, where, column, table=table, bins=bins, y=y)


@router.post("/query")
async def data_query(body: QueryIn, open_session: AsyncSession = Depends(session)) -> Any:
    """One read-only SELECT over the file, at most 1 000 rows back (`truncated` says there were more), in 15 s."""
    where = await dataview.resolve(open_session, body.path, body.projectId)
    return await _answer(dataview.query, where, body.sql)
