"""The code index, read back.

Every question the code screens ask is one statement here. That is the whole point of the move: the
old index answered "what depends on this, through anything" by pulling edges into Python and walking
them, and "what is in this folder" by scanning every path in the project. Both are now the database's
job — a recursive CTE for the first, a GROUP BY on the first path segment for the second — so a
directory's totals stay exact even though the rows the screen is handed are capped.

The hand-written search table is gone with it. A symbol is matched off its own row, by the `tsvector`
the database derives from its name and by a trigram index for the near-misses, so nothing can drift
out of step with the symbols it claims to describe.
"""
from __future__ import annotations

import posixpath
from typing import Any

from sqlalchemy import ColumnElement, func, literal, or_, select

from ..models import CodeEdge, CodeFile, CodeIndexRun, CodeSymbol
from .base import Repository, bounded

#: How far the dependency walk follows an edge backwards, and how many files it will name.
MAX_DEPTH = 8
MAX_REACHED = 2_000
#: What a directory listing and a search hand back at most.
MAX_ENTRIES = 500
#: Of a search's budget, the share paths may take before symbols fill the rest.
FILE_SHARE = 3
DB_KINDS = ("table", "procedure", "view", "function", "trigger")
DATA_KINDS = ("reads", "writes", "calls")


def _index_json(run: CodeIndexRun) -> dict[str, Any]:
    """The `codeIndex` field the project screens read. `at` changes with every index."""
    return {"files": run.files, "symbols": run.symbols, "edges": run.edges,
            "unresolved": run.unresolved, "ms": run.ms,
            "at": run.finished_at.isoformat(timespec="seconds")}


class CodeIndexRepository(Repository[CodeIndexRun]):
    model = CodeIndexRun

    async def summary(self, project_id: str) -> dict[str, Any] | None:
        run = await self.get(project_id)
        return _index_json(run) if run else None

    async def for_projects(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """project id → its last index. One statement, however many projects are on screen."""
        if not ids:
            return {}
        rows = (await self.session.execute(
            select(CodeIndexRun).where(CodeIndexRun.project_id.in_(ids)))).scalars()
        return {run.project_id: _index_json(run) for run in rows}

    # ── the rollups the index screen shows ───────────────────────
    async def languages(self, project_id: str) -> list[dict[str, Any]]:
        stmt = (select(CodeFile.lang, func.count(), func.coalesce(func.sum(CodeFile.lines), 0))
                .where(CodeFile.project_id == project_id).group_by(CodeFile.lang)
                .order_by(func.coalesce(func.sum(CodeFile.lines), 0).desc()).limit(MAX_ENTRIES))
        return [{"name": lang, "files": int(files), "lines": int(lines)}
                for lang, files, lines in (await self.session.execute(stmt)).all()]

    async def module_sizes(self, project_id: str) -> list[tuple[str, int, int, int]]:
        """module → files, lines, complexity, largest first. The graph and the summary share it."""
        stmt = (select(CodeFile.module, func.count(),
                       func.coalesce(func.sum(CodeFile.lines), 0),
                       func.coalesce(func.sum(CodeFile.complexity), 0))
                .where(CodeFile.project_id == project_id).group_by(CodeFile.module)
                .order_by(func.coalesce(func.sum(CodeFile.lines), 0).desc()).limit(MAX_ENTRIES))
        return [(module, int(files), int(lines), int(complexity))
                for module, files, lines, complexity in (await self.session.execute(stmt)).all()]

    async def module_count(self, project_id: str) -> int:
        """How many modules there are, not how many the graph could fit — the screen says the truth."""
        stmt = (select(func.count(func.distinct(CodeFile.module)))
                .where(CodeFile.project_id == project_id))
        return int((await self.session.execute(stmt)).scalar_one())

    async def symbols_per_module(self, project_id: str) -> dict[str, int]:
        stmt = (select(CodeFile.module, func.count())
                .join(CodeSymbol, CodeSymbol.file_id == CodeFile.id)
                .where(CodeSymbol.project_id == project_id).group_by(CodeFile.module))
        return {module: int(n) for module, n in (await self.session.execute(stmt)).all()}

    async def module_edges(self, project_id: str) -> list[tuple[str, str, int]]:
        """How many file dependencies cross from one module into another, both ways counted apart."""
        source, target = (CodeFile.__table__.alias("fa"), CodeFile.__table__.alias("fb"))
        stmt = (select(source.c.module, target.c.module, func.count())
                .select_from(CodeEdge)
                .join(source, source.c.id == CodeEdge.from_file)
                .join(target, target.c.id == CodeEdge.to_file)
                .where(CodeEdge.project_id == project_id, source.c.module != target.c.module)
                .group_by(source.c.module, target.c.module))
        return [(a, b, int(n)) for a, b, n in (await self.session.execute(stmt)).all()]

    async def hotspots(self, project_id: str, *, limit: int = 10) -> list[tuple[str, int, int, int, int]]:
        """The files most depended on: path, lines, complexity, churn and how many files reach them."""
        reached = func.count(func.distinct(CodeEdge.from_file)).label("reached")
        stmt = (select(CodeFile.path, CodeFile.lines, CodeFile.complexity, CodeFile.churn, reached)
                .select_from(CodeEdge).join(CodeFile, CodeFile.id == CodeEdge.to_file)
                .where(CodeEdge.project_id == project_id, CodeEdge.from_file != CodeEdge.to_file)
                .group_by(CodeFile.id).order_by(reached.desc(), CodeFile.complexity.desc())
                .limit(limit))
        return [(path, int(lines), int(complexity), int(churn), int(n))
                for path, lines, complexity, churn, n in (await self.session.execute(stmt)).all()]

    async def declared_objects(self, project_id: str) -> list[tuple[str, str, str]]:
        """Database objects this repository declares in its own T-SQL: name, kind, and where."""
        stmt = (select(CodeSymbol.name, CodeSymbol.kind, CodeFile.path)
                .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
                .where(CodeSymbol.project_id == project_id, CodeFile.lang == "T-SQL",
                       CodeSymbol.kind.in_(DB_KINDS))
                .order_by(CodeSymbol.name).limit(MAX_ENTRIES))
        return [(name, kind, path) for name, kind, path in (await self.session.execute(stmt)).all()]

    async def declared_object_count(self, project_id: str) -> int:
        """How many there really are. `declared_objects` is capped because it feeds a list; a count
        that inherits that cap stops being a count and becomes the cap, printed as if it were one."""
        stmt = (select(func.count(func.distinct(func.lower(CodeSymbol.name))))
                .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
                .where(CodeSymbol.project_id == project_id, CodeFile.lang == "T-SQL",
                       CodeSymbol.kind.in_(DB_KINDS)))
        return int((await self.session.execute(stmt)).scalar_one())

    async def object_usage(self, project_id: str) -> list[tuple[str, str, str, int]]:
        """Every read, write and call into a database object: target, kind, the module doing it, count."""
        stmt = (select(CodeEdge.target, CodeEdge.kind, CodeFile.module, func.count())
                .join(CodeFile, CodeFile.id == CodeEdge.from_file)
                .where(CodeEdge.project_id == project_id, CodeEdge.kind.in_(DATA_KINDS))
                .group_by(CodeEdge.target, CodeEdge.kind, CodeFile.module).limit(MAX_REACHED))
        return [(target, kind, module, int(n))
                for target, kind, module, n in (await self.session.execute(stmt)).all()]

    async def object_users(self, project_id: str) -> dict[str, dict[str, int]]:
        """object name, lowercased → how many files read it, write it and call it."""
        lowered = func.lower(CodeEdge.target).label("object")
        stmt = (select(lowered, CodeEdge.kind, func.count(func.distinct(CodeEdge.from_file)))
                .where(CodeEdge.project_id == project_id, CodeEdge.kind.in_(DATA_KINDS))
                .group_by(lowered, CodeEdge.kind).limit(MAX_ENTRIES * len(DATA_KINDS)))
        out: dict[str, dict[str, int]] = {}
        for target, kind, n in (await self.session.execute(stmt)).all():
            out.setdefault(target, dict.fromkeys(DATA_KINDS, 0))[kind] = int(n)
        return out


class CodeFileRepository(Repository[CodeFile]):
    model = CodeFile

    async def by_path(self, project_id: str, path: str) -> CodeFile | None:
        return await self.one(CodeFile.project_id == project_id, CodeFile.path == path)

    # ── the tree ─────────────────────────────────────────────────
    @staticmethod
    def _rest(prefix: str) -> ColumnElement[str]:
        """What is left of a path once the directory it is being listed under is taken off it."""
        return func.substr(CodeFile.path, len(prefix) + 1)

    async def folders(self, project_id: str, prefix: str) -> list[dict[str, Any]]:
        """The folders directly under a directory, each with everything beneath it counted.

        Rolled up by the database rather than by listing the subtree, so the totals stay true even
        though the files a screen is handed are capped.
        """
        rest = self._rest(prefix)
        name = func.split_part(rest, "/", 1).label("name")
        stmt = (select(name, func.count(), func.coalesce(func.sum(CodeFile.lines), 0))
                .where(CodeFile.project_id == project_id,
                       # autoescape, because `_` and `%` are ordinary characters in a folder name and
                       # wildcards to LIKE: "my_dir" was listing "myXdir"'s files inside itself.
                       CodeFile.path.startswith(prefix, autoescape=True),
                       func.strpos(rest, "/") > 0)
                .group_by(name).order_by(func.lower(name)).limit(MAX_ENTRIES))
        return [{"name": folder, "path": prefix + folder, "files": int(files), "lines": int(lines)}
                for folder, files, lines in (await self.session.execute(stmt)).all()]

    async def in_folder(self, project_id: str, prefix: str, *,
                        limit: int | None = None) -> list[CodeFile]:
        """The files of one directory, not of its subtree."""
        stmt = (select(CodeFile)
                .where(CodeFile.project_id == project_id,
                       CodeFile.path.startswith(prefix, autoescape=True),
                       func.strpos(self._rest(prefix), "/") == 0)
                # One more than asked for, so the caller can tell a full page from a truncated one
                # and say so — a file tree that silently stops is a file tree that is lying.
                .order_by(func.lower(CodeFile.path)).limit(bounded(limit or MAX_ENTRIES) + 1))
        return list((await self.session.execute(stmt)).scalars())

    async def fan_in(self, file_ids: list[int]) -> dict[int, int]:
        """file id → how many other files depend on it. One statement for a whole listing."""
        if not file_ids:
            return {}
        stmt = (select(CodeEdge.to_file, func.count(func.distinct(CodeEdge.from_file)))
                .where(CodeEdge.to_file.in_(file_ids), CodeEdge.from_file != CodeEdge.to_file)
                .group_by(CodeEdge.to_file))
        return {int(to_file): int(n) for to_file, n in (await self.session.execute(stmt)).all()}

    # ── search ───────────────────────────────────────────────────
    async def search(self, project_id: str, q: str, *, limit: int = 40) -> list[dict[str, Any]]:
        """Symbols by the words in their names, and files by their paths.

        Paths take a fixed share of the answer rather than competing with symbols for it: a repository
        has far more symbols than files, and a query that names a file would otherwise never show it.
        """
        q = q.strip()
        if not q:
            return []
        size = bounded(limit)
        paths = await self._by_path(project_id, q, limit=max(1, size // FILE_SHARE))
        return (paths + await self._by_symbol(project_id, q, limit=size - len(paths)))[:size]

    async def _by_path(self, project_id: str, q: str, *, limit: int) -> list[dict[str, Any]]:
        stmt = (select(CodeFile.path)
                .where(CodeFile.project_id == project_id, CodeFile.path.ilike(f"%{q}%"))
                .order_by(func.length(CodeFile.path), CodeFile.path).limit(limit))
        return [{"name": posixpath.basename(path), "path": path, "kind": "file", "line": 0}
                for (path,) in (await self.session.execute(stmt)).all()]

    async def _by_symbol(self, project_id: str, q: str, *, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        words = func.plainto_tsquery("simple", q)
        # An exact name is what the person almost always meant, so it is ranked ahead of the text
        # match rather than left to `ts_rank`, which cannot see that the whole name was typed.
        exact = func.lower(CodeSymbol.name) == q.lower()
        rank = func.ts_rank(CodeSymbol.search, words).label("rank")
        stmt = (select(CodeSymbol.name, CodeFile.path, CodeSymbol.kind, CodeSymbol.line)
                .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
                .where(CodeSymbol.project_id == project_id,
                       or_(CodeSymbol.search.op("@@")(words), CodeSymbol.name.ilike(f"%{q}%")))
                .order_by(exact.desc(), rank.desc(), func.length(CodeSymbol.name), CodeSymbol.name)
                .limit(limit))
        return [{"name": name, "path": path, "kind": kind, "line": int(line)}
                for name, path, kind, line in (await self.session.execute(stmt)).all()]

    # ── one file's relations ─────────────────────────────────────
    async def symbols(self, file_id: int) -> list[CodeSymbol]:
        stmt = (select(CodeSymbol).where(CodeSymbol.file_id == file_id)
                .order_by(CodeSymbol.line).limit(MAX_REACHED))
        return list((await self.session.execute(stmt)).scalars())

    async def depends_on(self, file_id: int) -> list[tuple[str, str, str | None]]:
        """What this file reaches: target, kind, and the file it resolved to — null outside the repo."""
        other = CodeFile.__table__.alias("t")
        stmt = (select(CodeEdge.target, CodeEdge.kind, other.c.path)
                .select_from(CodeEdge).outerjoin(other, other.c.id == CodeEdge.to_file)
                .where(CodeEdge.from_file == file_id)
                .order_by(other.c.path.is_(None), other.c.path, CodeEdge.target)
                .limit(MAX_REACHED))
        return [(target, kind, path) for target, kind, path in (await self.session.execute(stmt)).all()]

    async def used_by(self, file_id: int) -> list[tuple[str, str, str]]:
        """Which files reach this one, and what they reach it for: path, kind, target."""
        other = CodeFile.__table__.alias("s")
        stmt = (select(other.c.path, CodeEdge.kind, CodeEdge.target)
                .select_from(CodeEdge).join(other, other.c.id == CodeEdge.from_file)
                .where(CodeEdge.to_file == file_id, CodeEdge.from_file != CodeEdge.to_file)
                .order_by(other.c.path).limit(MAX_REACHED))
        return [(path, kind, target) for path, kind, target in (await self.session.execute(stmt)).all()]

    # ── the dependency walk ──────────────────────────────────────
    async def ids_of(self, project_id: str, *, path: str | None = None,
                     module: str | None = None) -> list[int]:
        where: list[ColumnElement[bool]] = [CodeFile.project_id == project_id]
        where.append(CodeFile.path == path if path is not None else CodeFile.module == module)
        stmt = select(CodeFile.id).where(*where).limit(MAX_REACHED)
        return [int(i) for (i,) in (await self.session.execute(stmt)).all()]

    async def reached(self, seeds: list[int]) -> list[dict[str, Any]]:
        """Every file that depends on one of these, directly or through others, with how far away.

        The edges are walked backwards by the database — a recursive CTE, eight steps deep — and each
        file is kept at the shortest distance any path to it found.
        """
        if not seeds:
            return []
        walk = (select(CodeFile.id.label("id"), literal(0).label("depth"))
                .where(CodeFile.id.in_(seeds)).cte("dep", recursive=True))
        deeper = (select(CodeEdge.from_file.label("id"), (walk.c.depth + 1).label("depth"))
                  .join(walk, CodeEdge.to_file == walk.c.id)
                  .where(walk.c.depth < MAX_DEPTH, CodeEdge.from_file != CodeEdge.to_file))
        walked = walk.union(deeper)
        depth = func.min(walked.c.depth).label("depth")
        stmt = (select(CodeFile.id, CodeFile.path, CodeFile.module, CodeFile.lang,
                       CodeFile.complexity, CodeFile.churn, depth)
                .join(walked, walked.c.id == CodeFile.id)
                .group_by(CodeFile.id).order_by(depth, CodeFile.path).limit(MAX_REACHED))
        return [{"id": int(i), "path": path, "module": module, "lang": lang,
                 "complexity": int(complexity), "churn": int(churn), "depth": int(d)}
                for i, path, module, lang, complexity, churn, d in (await self.session.execute(stmt)).all()]


class CodeEdgeRepository(Repository[CodeEdge]):
    model = CodeEdge

    async def users_of_object(self, project_id: str, obj: str) -> list[int]:
        """The files that read, write or call a database object — its direct dependents."""
        stmt = (select(func.distinct(CodeEdge.from_file))
                .where(CodeEdge.project_id == project_id, func.lower(CodeEdge.target) == obj.lower(),
                       CodeEdge.kind.in_(DATA_KINDS)).limit(MAX_REACHED))
        return [int(i) for (i,) in (await self.session.execute(stmt)).all()]

    async def object_is_declared(self, project_id: str, obj: str) -> bool:
        stmt = (select(literal(1)).select_from(CodeSymbol)
                .where(CodeSymbol.project_id == project_id,
                       func.lower(CodeSymbol.name) == obj.lower()).limit(1))
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def kinds_against(self, project_id: str, obj: str) -> list[str]:
        stmt = (select(func.distinct(CodeEdge.kind))
                .where(CodeEdge.project_id == project_id, func.lower(CodeEdge.target) == obj.lower()))
        return [kind for (kind,) in (await self.session.execute(stmt)).all()]

    async def data_touched(self, file_ids: list[int]) -> list[tuple[str, str]]:
        """The database objects a set of files touches, as (object, kind) pairs."""
        if not file_ids:
            return []
        stmt = (select(CodeEdge.target, CodeEdge.kind)
                .where(CodeEdge.from_file.in_(file_ids), CodeEdge.kind.in_(DATA_KINDS))
                .distinct().order_by(CodeEdge.target).limit(MAX_ENTRIES))
        return [(target, kind) for target, kind in (await self.session.execute(stmt)).all()]
