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

from sqlalchemy import CTE, ColumnElement, Float, case, cast, func, literal, or_, select

from ..models import Chunk
from .base import Repository

#: How many rows each half contributes before they are fused, and the constant that flattens the
#: gap between ranks — the usual reciprocal-rank fusion, where being 1st matters but not absurdly.
CANDIDATES = 60
RRF_K = 60


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

    def _lexical(self, project_id: str, q: str) -> CTE:
        query = func.websearch_to_tsquery("english", q)
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
                     limit: int = 8) -> list[dict[str, Any]]:
        """The pieces that bear on a question, best first, each saying how it was found."""
        if not q.strip():
            return []

        lexical = self._lexical(project_id, q.strip())
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
