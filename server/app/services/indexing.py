"""Writing the code index into Postgres.

Reading the code has not changed: `codeindex.build` walks the tree, parses what it can and returns
files, symbols and edges — pure, blocking, and unaware of any database. Only the writing moved here.

Two things are better for the move. The project's rows are replaced inside the caller's transaction,
so a reader sees the old index or the new one and never half of one. And the search index is gone as
a *table*: symbols carry a generated `tsvector` and a trigram index on the name, derived by the
database from the row itself, so it cannot drift out of step the way a hand-written shadow table did.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import codeindex
from ..data.base import utcnow
from ..models import CodeEdge, CodeFile, CodeIndexRun, CodeSymbol


async def save_index(session: AsyncSession, project_id: str, root: str, idx: codeindex.Index) -> None:
    """Replace a project's index. Files go first; symbols and edges point at them by id."""
    await session.execute(delete(CodeFile).where(CodeFile.project_id == project_id))
    await session.flush()

    session.add_all([CodeFile(project_id=project_id, path=f["path"], lang=f["lang"], module=f["module"],
                              lines=f["lines"], bytes=f["bytes"], sha1=f["sha1"],
                              complexity=f["complexity"], churn=f["churn"], changed_at=f["changed_at"])
                     for f in idx.files])
    await session.flush()

    # Their ids come back in one query; symbols and edges point at files by id, not by path.
    ids = {path: file_id for path, file_id in (await session.execute(
        select(CodeFile.path, CodeFile.id).where(CodeFile.project_id == project_id))).all()}

    session.add_all([CodeSymbol(project_id=project_id, file_id=ids[path], name=name, kind=kind,
                                line=line, exported=bool(exported))
                     for path, name, kind, line, exported in idx.symbols if path in ids])
    session.add_all([CodeEdge(project_id=project_id, from_file=ids[a], to_file=ids[b] if b and b in ids else None,
                              target=target, kind=kind)
                     for a, b, target, kind in idx.edges if a in ids])

    run = await session.get(CodeIndexRun, project_id) or CodeIndexRun(project_id=project_id)
    stats = idx.stats()
    run.root, run.finished_at, run.ms = root, utcnow(), idx.ms
    run.files, run.symbols, run.edges = stats["files"], stats["symbols"], stats["edges"]
    run.unresolved, run.parsers = idx.unresolved, idx.parsers
    session.add(run)
    await session.flush()


async def build_index(session: AsyncSession, project_id: str, root: Path,
                      excluded: list[str]) -> codeindex.Index:
    """Read the code on a worker thread, then write what it found. Returns the index for the log line."""
    idx = await asyncio.to_thread(codeindex.build, root, excluded)
    await save_index(session, project_id, str(root), idx)
    return idx


def index_fields(idx: codeindex.Index) -> dict[str, Any]:
    """What a project record can honestly say once its code has been read."""
    return {"coverage": idx.coverage(), "stats": idx.stats(), "describe": idx.describe()}
