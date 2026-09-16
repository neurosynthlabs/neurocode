"""Retrieval, asked of the database instead of of Python.

The old hybrid search read every vector for a project into the process and scored them one at a time
in a loop. Here both halves are one statement: full text ranked by `ts_rank` over a generated
`tsvector`, nearest neighbours by cosine distance over an HNSW index, and the two fused by
**reciprocal rank** — each result carrying whether words found it, meaning found it, or both.

It still works with no embeddings at all. Without a vector the semantic half is simply not in the
statement, and every answer says `lexical` rather than pretending to be more than it is.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import CTE, ColumnElement, Float, Text, case, cast, func, literal, or_, select, union
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by, array_agg

from ..models import Chunk, CodeFile, CodeSymbol, MemoryFact, Plan, Task
from .base import Repository
from .words import Mode, tsquery

#: How many rows each half contributes before they are fused, and the constant that flattens the
#: gap between ranks — the usual reciprocal-rank fusion, where being 1st matters but not absurdly.
CANDIDATES = 60
RRF_K = 60
#: How many matching chunks the lexical count stops at: enough to say "a lot", never a full scan.
LEXICAL_COUNT_CAP = 1_000
#: The most documents one list shows, and the most pieces of one document read back.
MAX_DOCS = 500
#: Every document path a build can have written: one chunk each at the very least, and a build writes at most
#: services.retrieval.MAX_DOC_CHUNKS of them. Counting over fewer made the indexed and stale totals wrong
#: for any project with more documents than one list shows.
MAX_DOC_PATHS = 1_000
MAX_SECTIONS = 100
#: Every doc chunk a build can write (MAX_CHUNKS // 4 in services/retrieval.py), read for entity links.
MAX_DOC_BODIES = 1_000
MAX_LINKS = 200


def _rrf(rank: ColumnElement[Any]) -> ColumnElement[float]:
    """A rank becomes a score: 1/(k + rank), and nothing at all when this half did not find it."""
    return case((rank.is_(None), 0.0), else_=1.0 / (RRF_K + cast(rank, Float)))


class ChunkRepository(Repository[Chunk]):
    model = Chunk

    def _scope(self, project_id: str) -> ColumnElement[bool]:
        """A project's own chunks, plus the workspace's memory, which belongs to every project."""
        return or_(Chunk.project_id == project_id, Chunk.project_id.is_(None))

    async def counts(self, project_id: str) -> dict[str, int]:
        stmt = select(Chunk.kind, func.count()).where(self._scope(project_id)).group_by(Chunk.kind)
        return {kind: int(n) for kind, n in (await self.session.execute(stmt)).all()}

    async def embedded(self, project_id: str) -> int:
        return await self.count(self._scope(project_id), Chunk.embedding.is_not(None))

    async def lexical_count(self, project_id: str, q: str, *, mode: Mode = "all") -> int:
        """How many chunks the words of a question match, up to a cap — the "Words" line of the search."""
        if not q.strip():
            return 0
        matches = (select(literal(1)).where(self._scope(project_id), Chunk.search.op("@@")(tsquery(q, mode)))
                   .limit(LEXICAL_COUNT_CAP).subquery())
        return int((await self.session.execute(select(func.count()).select_from(matches))).scalar_one())

    # ── a project's documents ────────────────────────────────────
    # Only `project_id = :pid AND kind = 'doc'`: the workspace's memory lives in the same table with no
    # project, and would otherwise show up here as documents.
    async def docs(self, project_id: str, *, limit: int = MAX_DOCS) -> list[dict[str, Any]]:
        """One row per document path: its pieces, how many are embedded, and the start of its first piece."""
        stmt = (select(Chunk.path, func.count().label("chunks"), func.count(Chunk.embedding).label("embedded"),
                       func.max(Chunk.at).label("at"),
                       array_agg(aggregate_order_by(Chunk.title, Chunk.id), type_=ARRAY(Text))[1].label("title"),
                       array_agg(aggregate_order_by(func.left(Chunk.body, 600), Chunk.id), type_=ARRAY(Text))[1].label("head"))
                .where(Chunk.project_id == project_id, Chunk.kind == "doc")
                .group_by(Chunk.path).order_by(Chunk.path).limit(min(limit, MAX_DOC_PATHS)))
        return [dict(row._mapping) for row in (await self.session.execute(stmt)).all()]

    async def doc_sections(self, project_id: str, path: str) -> list[tuple[str, str, str, bool]]:
        """ref, title, body and whether it is embedded, for each piece of one document, in order."""
        stmt = (select(Chunk.ref, Chunk.title, Chunk.body, Chunk.embedding.is_not(None))
                .where(Chunk.project_id == project_id, Chunk.kind == "doc", Chunk.path == path)
                .order_by(Chunk.id).limit(MAX_SECTIONS))
        return [(ref, title, body, bool(embedded)) for ref, title, body, embedded in (await self.session.execute(stmt)).all()]

    async def doc_bodies(self, project_id: str) -> list[str]:
        stmt = (select(Chunk.body).where(Chunk.project_id == project_id, Chunk.kind == "doc")
                .order_by(Chunk.id).limit(MAX_DOC_BODIES))
        return list((await self.session.execute(stmt)).scalars())

    async def symbols_named(self, project_id: str, names: list[str], *,
                            limit: int = MAX_LINKS) -> list[tuple[str, str]]:
        """(symbol, the file that declares it) for the names this project's index really holds."""
        if not names:
            return []
        stmt = (select(CodeSymbol.name, CodeFile.path).distinct()
                .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
                .where(CodeSymbol.project_id == project_id, CodeSymbol.name.in_(names))
                .order_by(CodeSymbol.name, CodeFile.path).limit(limit))
        return [(name, path) for name, path in (await self.session.execute(stmt)).all()]

    async def count_symbols_named(self, project_id: str, names: list[str]) -> int:
        """How many distinct names from this list the index declares — "entities linked", counted by Postgres."""
        if not names:
            return 0
        stmt = (select(func.count(func.distinct(CodeSymbol.name)))
                .where(CodeSymbol.project_id == project_id, CodeSymbol.name.in_(names)))
        return int((await self.session.execute(stmt)).scalar_one())

    async def code_paths(self, project_id: str, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        stmt = (select(CodeFile.path).where(CodeFile.project_id == project_id, CodeFile.path.in_(paths))
                .limit(MAX_LINKS))
        return set((await self.session.execute(stmt)).scalars())

    async def existing_refs(self, project_id: str, refs: list[str]) -> set[str]:
        """Which of these references are a real task, remembered fact or plan of this project.

        Scoped like every other link a document gets: without it, a TASK-12 mentioned in one project's
        notes was shown as connected to another project's TASK-12. A fact with no project belongs to the
        workspace, and so to every project.
        """
        if not refs:
            return set()
        stmt = union(select(Task.ref).where(Task.ref.in_(refs), Task.project_id == project_id),
                     select(MemoryFact.ref).where(MemoryFact.ref.in_(refs),
                                                  or_(MemoryFact.project_id == project_id,
                                                      MemoryFact.project_id.is_(None))),
                     select(Plan.ref).where(Plan.ref.in_(refs), Plan.project_id == project_id)).limit(MAX_LINKS)
        return set((await self.session.execute(stmt)).scalars())

    def _lexical(self, project_id: str, q: str, mode: Mode) -> CTE:
        query = tsquery(q, mode)
        return (select(Chunk.id.label("id"),
                       func.row_number().over(order_by=func.ts_rank(Chunk.search, query).desc()).label("rank"))
                .where(self._scope(project_id), Chunk.search.op("@@")(query))
                .limit(CANDIDATES).cte("lexical"))

    def _semantic(self, project_id: str, vector: list[float]) -> CTE:
        return (select(Chunk.id.label("id"),
                       func.row_number().over(order_by=Chunk.embedding.cosine_distance(vector)).label("rank"))
                .where(self._scope(project_id), Chunk.embedding.is_not(None))
                .limit(CANDIDATES).cte("semantic"))

    async def search(self, project_id: str, q: str, *, vector: list[float] | None = None,
                     limit: int = 8, mode: Mode = "any") -> list[dict[str, Any]]:
        """The pieces that bear on a question, best first, each saying how it was found.

        "any" by default because nearly everything that calls this asks a question; the search box,
        where a person narrows on purpose, asks for "all".
        """
        if not q.strip():
            return []

        lexical = self._lexical(project_id, q.strip(), mode)
        score: ColumnElement[float] = _rrf(lexical.c.rank)
        by_words: ColumnElement[bool] = lexical.c.id.is_not(None)
        by_meaning: ColumnElement[bool] = literal(False)
        found = [lexical.c.id.is_not(None)]
        halves = [(lexical, lexical.c.id == Chunk.id)]

        if vector is not None:
            semantic = self._semantic(project_id, vector)
            score = score + _rrf(semantic.c.rank)
            by_meaning = semantic.c.id.is_not(None)
            found.append(semantic.c.id.is_not(None))
            halves.append((semantic, semantic.c.id == Chunk.id))

        stmt = select(Chunk, score.label("score"), by_words.label("by_words"),
                      by_meaning.label("by_meaning"))
        for half, on in halves:
            stmt = stmt.outerjoin(half, on)
        stmt = stmt.where(or_(*found)).order_by(score.desc()).limit(min(limit, 50))

        out: list[dict[str, Any]] = []
        for chunk, points, words, meaning in (await self.session.execute(stmt)).all():
            how = "both" if words and meaning else ("lexical" if words else "semantic")
            out.append({"ref": chunk.ref, "kind": chunk.kind, "path": chunk.path, "title": chunk.title,
                        "line": chunk.line, "text": chunk.body, "score": round(float(points), 5),
                        "how": how})
        return out
