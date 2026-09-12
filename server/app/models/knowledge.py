"""What the workspace knows: remembered facts, the conflicts between them, and the chunks retrieval
searches.

Two things Postgres does here that SQLite could not. Full text is a **generated `tsvector` column**
with a GIN index — the search index cannot drift from the row, because the database derives it. And
embeddings are a real `vector` type with an **HNSW** index, so finding the nearest chunks is an index
scan rather than every vector being read into Python and scored one at a time.

Vectors are stored at a fixed width so one index covers them all; a model that returns fewer numbers
is padded with zeros, which changes neither the dot product nor the norm, so similarity is unaffected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..data.base import Base, Mixin
from .enums import ChunkKind, Confidence, ConflictStatus, MemoryCategory

#: Every embedding is stored at this width. 1536 covers every lane's model; shorter ones are padded.
EMBED_DIM = 1536


class MemoryFact(Base, Mixin):
    __tablename__ = "memory_facts"
    __table_args__ = (
        Index("ix_memory_facts_search", "search", postgresql_using="gin"),
        Index("ix_memory_facts_project_id_category", "project_id", "category"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(MemoryCategory, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: Null means the fact belongs to the workspace, not to one project.
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    confidence: Mapped[str] = mapped_column(Confidence, nullable=False, server_default="MEDIUM")
    strength: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    hits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pinned: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    archived: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    #: Derived by the database from the words themselves, so it can never be stale.
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', title || ' ' || body || ' ' || reason)", persisted=True),
    )

    tags: Mapped[list[MemoryTag]] = relationship(back_populates="fact", cascade="all, delete-orphan",
                                                 lazy="selectin")


class MemoryTag(Base):
    __tablename__ = "memory_tags"

    fact_id: Mapped[str] = mapped_column(ForeignKey("memory_facts.id", ondelete="CASCADE"), primary_key=True)
    tag: Mapped[str] = mapped_column(String(60), primary_key=True, index=True)

    fact: Mapped[MemoryFact] = relationship(back_populates="tags")


class MemoryConflict(Base, Mixin):
    """Two facts that cannot both be true. Kept until a person settles it."""

    __tablename__ = "memory_conflicts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(ConflictStatus, nullable=False, server_default="open")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="MEDIUM")
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    suggestion: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  nullable=False)
    #: The two sides, exactly as they were when the conflict was spotted.
    a: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    b: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    resolution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class Chunk(Base):
    """The smallest piece worth retrieving on its own: a symbol, a section of a document, a fact."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("project_id", "kind", "ref"),
        Index("ix_chunks_search", "search", postgresql_using="gin"),
        Index("ix_chunks_embedding", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64},
              postgresql_ops={"embedding": "vector_cosine_ops"}),
        Index("ix_chunks_project_id_kind", "project_id", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    #: Null means the workspace's own memory, searched alongside every project.
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(ChunkKind, nullable=False)
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    line: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    body: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    #: How many numbers the model actually returned, before padding.
    dim: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', title || ' ' || body)", persisted=True),
    )


class RetrievalRun(Base):
    """What the last build of a project's chunks did, and whether it could embed them."""

    __tablename__ = "retrieval_runs"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  nullable=False)
    ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    chunks: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    embedded: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    lane: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


# A chunk's text is searched by words as well as by meaning; both indexes above are on the same row,
# so a hybrid search never has to reconcile two stores that drifted apart.
__all__ = ["EMBED_DIM", "Chunk", "MemoryConflict", "MemoryFact", "MemoryTag", "RetrievalRun", "text"]
