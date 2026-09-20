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
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import ColumnElement, and_, delete, func, insert, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import codeindex
from ..data.base import utcnow
from ..models import CodeEdge, CodeFile, CodeIndexRun, CodeSymbol, Project


def _moment(value: str | datetime | None) -> datetime | None:
    """The reader speaks git's `%cI` — ISO 8601 text, offset included — because it was written for a
    store that kept text. The column is a real timestamp now, and asyncpg will not guess: handed the
    string, it refused the whole insert, so every project that was a git repository failed to index
    while a plain folder, with no history to read, indexed fine."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


#: Rows per INSERT statement batch: bounded memory per round trip on a very large index.
WRITE_BATCH = 20_000
#: How wide a symbol name may be, asked of the column itself so the two cannot drift apart. Every
#: reader ends here — Python's `ast`, the T-SQL patterns, the component reader, tree-sitter — and
#: only tree-sitter cut its names to fit, so one long name from any of the others (a Python class
#: and method whose names together pass 200 characters, a procedure named at length) made Postgres
#: refuse the whole statement, rolled the index write back and put a raw database error in the feed.
NAME_WIDTH: int = CodeSymbol.name.type.length or 200


async def save_index(session: AsyncSession, project_id: str, root: str, idx: codeindex.Index,
                     scope: ColumnElement[bool] | None = None) -> None:
    """Replace a project's index — or, with `scope`, only the files it covers, so a source that could
    not be read this time keeps what it had. Files go first; symbols and edges point at them by id."""
    await session.execute(delete(CodeFile).where(CodeFile.project_id == project_id,
                                                 *([scope] if scope is not None else [])))
    await session.flush()

    session.add_all([CodeFile(project_id=project_id, path=f["path"], lang=f["lang"], module=f["module"],
                              lines=f["lines"], bytes=f["bytes"], sha1=f["sha1"],
                              complexity=f["complexity"], churn=f["churn"], changed_at=_moment(f["changed_at"]))
                     for f in idx.files])
    await session.flush()

    # Their ids come back in one query; symbols and edges point at files by id, not by path.
    ids = {path: file_id for path, file_id in (await session.execute(
        select(CodeFile.path, CodeFile.id).where(CodeFile.project_id == project_id))).all()}

    # Symbols and edges are written as plain rows, many to a statement, not as tracked objects: every
    # language is read now, and a large repository holds hundreds of thousands of them — the ORM's
    # bookkeeping per row was most of the time a re-index took. Nothing reads them back here.
    symbols = [{"project_id": project_id, "file_id": ids[path], "name": name[:NAME_WIDTH], "kind": kind,
                "line": line, "end_line": end or None, "exported": bool(exported)}
               for path, name, kind, line, exported, end in idx.symbols if path in ids]
    edges = [{"project_id": project_id, "from_file": ids[a], "to_file": ids[b] if b and b in ids else None,
              "target": target, "kind": kind}
             for a, b, target, kind in idx.edges if a in ids]
    for model, rows in ((CodeSymbol, symbols), (CodeEdge, edges)):
        for i in range(0, len(rows), WRITE_BATCH):
            await session.execute(insert(model), rows[i:i + WRITE_BATCH])

    run = await session.get(CodeIndexRun, project_id) or CodeIndexRun(project_id=project_id)
    stats = idx.stats()
    if scope is not None:
        # Part of the index was kept from before, so what it holds now is counted, not assumed.
        await session.flush()
        for name, model in (("files", CodeFile), ("symbols", CodeSymbol), ("edges", CodeEdge)):
            stats[name] = int((await session.execute(select(func.count()).select_from(model).where(
                model.project_id == project_id))).scalar_one())
    run.root, run.finished_at, run.ms = root, utcnow(), idx.ms
    run.files, run.symbols, run.edges = stats["files"], stats["symbols"], stats["edges"]
    run.unresolved, run.parsers = idx.unresolved, idx.parsers
    session.add(run)

    # How much of the project the index understands: the files it holds, out of the source files the
    # onboarding scan found. Measured here, where both numbers are known, and nowhere else — a
    # project that was never scanned has nothing to be a share of, and says so with no number.
    project = await session.get(Project, project_id)
    if project is not None:
        project.understood_pct = (min(100, round(100 * stats["files"] / project.files_count))
                                  if project.files_count else None)
    await session.flush()


#: Projects whose code is being read right now. One process serves one workspace, so a set is the
#: whole of it — and it is what stops a second re-index starting on top of the first.
#:
#: It lives here, next to the work, because it used to live next to one of the two callers: onboarding
#: indexed without ever entering the set, so the screen showed a project as idle while it was being
#: read and the re-index guard would happily start a second pass on top of the first.
INDEXING: set[str] = set()


async def build_index(session: AsyncSession, project_id: str, root: Path,
                      excluded: list[str]) -> codeindex.Index:
    """Read the code on a worker thread, then write what it found. Returns the index for the log line."""
    INDEXING.add(project_id)
    try:
        idx = await asyncio.to_thread(codeindex.build, root, excluded)
        await save_index(session, project_id, str(root), idx)
        return idx
    finally:
        INDEXING.discard(project_id)


class Checkout(Protocol):
    """What indexing needs to know of one of a project's sources (`services.code.Source`)."""

    @property
    def label(self) -> str: ...
    @property
    def root(self) -> Path: ...
    @property
    def primary(self) -> bool: ...
    @property
    def ready(self) -> bool: ...
    @property
    def prefix(self) -> str: ...


def _under(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def merge(parts: Sequence[tuple[str, codeindex.Index]]) -> codeindex.Index:
    """Several checkouts' indexes as one project's. A further source's paths are put under its label,
    and so are its modules — `api/billing` — so search, impact and the graph span the whole project
    without two sources' `src/main.py` ever becoming one file. `parts` are (prefix, index); the first
    source's prefix is empty. Edges stay inside the checkout they were read in: nothing here guesses
    that the web app's `fetch('/api/x')` is the API's handler.

    Two sources can still arrive at the same path, and that is refused here in words. A label may not
    name a top-level entry of the first checkout, but that is only checked the day the label is
    chosen: the first checkout can grow an `api/` folder afterwards, and then its `api/x.py` and the
    `api` source's `x.py` are one path. Left alone it fails at the unique constraint on code_files,
    which rolls the whole write back and posts a database error to the feed on every re-index."""
    if len(parts) == 1 and not parts[0][0]:
        return parts[0][1]
    seen: dict[str, str] = {}                 # path → the label of the source it came from
    files: list[dict[str, Any]] = []
    symbols: list[tuple[str, str, str, int, bool, int]] = []
    edges: list[tuple[str, str | None, str, str]] = []
    parsers: dict[str, str] = {}
    parsed = resolved = unresolved = db_objects = db_referenced = ms = 0
    for prefix, idx in parts:
        label = prefix.rstrip("/")
        for f in idx.files:
            module = f["module"]
            if prefix:
                module = label if module == "(root)" else f"{label}/{module}"
            path = _under(prefix, f["path"])
            if path in seen:
                clash = label or seen[path]
                raise RuntimeError(
                    f"two of this project's sources hold {path}: the first checkout now has a "
                    f"top-level {clash}/ folder, and {clash} is also a source's label. Rename the "
                    f"source's label or that folder, then read the project again.")
            seen[path] = label
            files.append({**f, "path": path, "module": module})
        symbols += [(_under(prefix, path), name, kind, line, exported, end)
                    for path, name, kind, line, exported, end in idx.symbols]
        edges += [(_under(prefix, a), _under(prefix, b) if b else b, target, kind)
                  for a, b, target, kind in idx.edges]
        parsers.update(idx.parsers)
        parsed += idx.parsed
        resolved += idx.resolved
        unresolved += idx.unresolved
        db_objects += idx.db_objects
        db_referenced += idx.db_referenced
        ms += idx.ms
    return codeindex.Index(files, symbols, edges, parsed, resolved, unresolved, db_objects, db_referenced,
                           dict(sorted(parsers.items())), ms)


def _scope(read: Sequence[Checkout], labels: Sequence[str]) -> ColumnElement[bool]:
    """The index rows that belong to the sources that were read: a further source's under its label,
    the first source's everything that is under no further source's label."""
    parts: list[ColumnElement[bool]] = []
    for source in read:
        if source.primary:
            others = [CodeFile.path.startswith(f"{label}/", autoescape=True) for label in labels]
            parts.append(not_(or_(*others)) if others else CodeFile.path.is_not(None))
        else:
            parts.append(CodeFile.path.startswith(source.prefix, autoescape=True))
    return or_(*parts) if parts else and_(CodeFile.path.is_(None))


async def build_project_index(session: AsyncSession, project_id: str, sources: Sequence[Checkout],
                              excluded: list[str]) -> codeindex.Index:
    """Read every source of a project that is on this machine, and write them as one index.

    A source whose folder is not there right now keeps the rows it had rather than vanishing from
    search: a project is better off with a stale answer about its API than with none. With nothing
    readable at all, it refuses in words and changes nothing.
    """
    INDEXING.add(project_id)
    try:
        ready = [x for x in sources if x.ready]

        def read() -> list[tuple[Checkout, codeindex.Index]]:
            return [(x, codeindex.build(x.root, excluded)) for x in ready if x.root.is_dir()]

        parts = await asyncio.to_thread(read)
        if not parts:
            raise RuntimeError("none of this project's checkouts is on this machine")
        idx = merge([(x.prefix, i) for x, i in parts])
        labels = [x.label for x in sources if not x.primary]
        first = next((x for x, _ in parts if x.primary), parts[0][0])
        everything = len(parts) == len(sources)
        await save_index(session, project_id, str(first.root), idx,
                         None if everything else _scope([x for x, _ in parts], labels))
        return idx
    finally:
        INDEXING.discard(project_id)


def index_fields(idx: codeindex.Index) -> dict[str, Any]:
    """What a project record can honestly say once its code has been read."""
    return {"coverage": idx.coverage(), "stats": idx.stats(), "describe": idx.describe()}
