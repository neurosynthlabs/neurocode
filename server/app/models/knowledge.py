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
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from ..data.base import Base, Mixin
from .enums import ChunkKind, Confidence, ConflictStatus, MemoryCategory, RunStatus, RunStepStatus

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
    #: When a feature last put this fact in front of a model or cited it — written with each recall,
    #: so it is the newest row in `memory_hits` without a query per fact.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pinned: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    archived: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class MemoryHit(Base):
    """One fact recalled once: cited by an answer, or put in front of a model to answer from.

    A row per recall rather than a counter on the fact. The fact used to carry `hits` and a `strength`
    of 80 that nothing ever changed, so the screen showed numbers that looked measured and were not;
    a row says which feature used the fact, for what, and when — and "in the last day" is a count.
    """

    __tablename__ = "memory_hits"
    __table_args__ = (
        CheckConstraint("feature IN ('ask', 'chat', 'retrieval', 'research', 'compile')", name="known_feature"),
        Index("ix_memory_hits_fact_id_at", "fact_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fact_id: Mapped[str] = mapped_column(ForeignKey("memory_facts.id", ondelete="CASCADE"), nullable=False)
    feature: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What it was recalled for — a session, a research or a plan ref. Null for a question asked of
    #: memory itself, which leaves nothing behind to point at.
    ref: Mapped[str | None] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False,
                                         index=True)


#: Counted in the same statement that loads the fact, so a list of facts is one query, not one per fact.
#: Not expired on flush: pinning a fact must not turn its count into a lazy load inside async code.
MemoryFact.hits_24h = column_property(
    select(func.count(MemoryHit.id))
    .where(MemoryHit.fact_id == MemoryFact.id, MemoryHit.at > func.now() - text("interval '24 hours'"))
    .correlate_except(MemoryHit)
    .scalar_subquery(),
    expire_on_flush=False,
)


class MemoryConflict(Base, Mixin):
    """Two facts that cannot both be true. Kept until a person settles it."""

    __tablename__ = "memory_conflicts"
    __table_args__ = (
        CheckConstraint("a <> b", name="two_different_facts"),
        # One open conflict per pair, whichever way round it was filed. The service says so in words
        # first; this is what holds when two people file the same pair at the same moment.
        Index("uq_memory_conflicts_open_pair", func.least(text("a"), text("b")), func.greatest(text("a"), text("b")),
              unique=True, postgresql_where=text("status = 'open'")),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(ConflictStatus, nullable=False, server_default="open")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="MEDIUM")
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    suggestion: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  nullable=False)
    #: The two facts that disagree. They were JSON before, and were always plain fact ids inside it —
    #: which meant nothing checked that the ids pointed at anything, and the resolver, written for
    #: documents, never found the fact it was supposed to archive. A column and a key say what is true.
    a: Mapped[str] = mapped_column(ForeignKey("memory_facts.id", ondelete="CASCADE"), nullable=False)
    b: Mapped[str] = mapped_column(ForeignKey("memory_facts.id", ondelete="CASCADE"), nullable=False)
    resolution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class Chunk(Base):
    """The smallest piece worth retrieving on its own: a symbol, a section of a document, a fact."""

    __tablename__ = "chunks"
    __table_args__ = (
        # NULLS NOT DISTINCT because a null project_id is a real scope here — the workspace's own
        # memory — not an unknown. Left to its default, two identical workspace chunks were both
        # unique as far as Postgres was concerned, so the one rule meant to dedupe them never applied.
        UniqueConstraint("project_id", "kind", "ref", postgresql_nulls_not_distinct=True),
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


class ResearchReport(Base, Mixin):
    """One research a person asked for: the question, what it was allowed to read, where it stands, and
    what the synthesis concluded. Written as it runs, so a half-finished research is visible as one."""

    __tablename__ = "research_reports"
    __table_args__ = (
        CheckConstraint("char_length(question) BETWEEN 3 AND 2000", name="question_length"),
        CheckConstraint("cardinality(kinds) >= 1", name="reads_something"),
        CheckConstraint("finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
                        name="finishes_after_it_starts"),
        Index("ix_research_reports_project_id_created_at", "project_id", "created_at"),
        # What start-up reads to find research that claims to be in flight after a restart.
        Index("ix_research_reports_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which kinds of chunk it may read. An array of the chunk vocabulary itself, so a kind retrieval
    #: does not have cannot be asked for.
    kinds: Mapped[list[str]] = mapped_column(ARRAY(ChunkKind), nullable=False,
                                             server_default="{code,doc,memory}")
    status: Mapped[str] = mapped_column(RunStatus, nullable=False, server_default="queued")
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    recommendation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    architecture: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    risks: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    gaps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    #: The model's own comparison, [{name, pros[], cons[], verdict}]: read whole, shaped by the answer.
    alternatives: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    lane: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    #: Why the offline rules stood in, when they did.
    fallback: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    angles: Mapped[list[ResearchAngle]] = relationship(back_populates="report", cascade="all, delete-orphan",
                                                       order_by="ResearchAngle.n", lazy="selectin")


class ResearchAngle(Base):
    """One sub-question, answered on its own from its own retrieval."""

    __tablename__ = "research_angles"
    __table_args__ = (
        UniqueConstraint("report_id", "n"),
        CheckConstraint("n BETWEEN 1 AND 8", name="n_in_range"),
        CheckConstraint("hits >= 0", name="hits_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(RunStepStatus, nullable=False, server_default="todo")
    hits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    hits_lexical: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    hits_semantic: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    finding: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    lane: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    report: Mapped[ResearchReport] = relationship(back_populates="angles")
    #: Appended through the angle, which fills in angle_id; report_id is the caller's to set, because a
    #: citation belongs to both and only one of them can be the parent it is added through.
    citations: Mapped[list[ResearchCitation]] = relationship(back_populates="angle", cascade="all, delete-orphan",
                                                             order_by="ResearchCitation.n", lazy="selectin")


class ResearchCitation(Base):
    """A piece an angle actually cited, snapshotted with an excerpt.

    Deliberately no foreign key to `chunks`: every retrieval build deletes and re-inserts a project's
    chunks, so a key would either cascade the citations away or block re-indexing. The excerpt is what
    keeps a citation readable after that. Two angles may cite the same piece; a report lists it once.
    """

    __tablename__ = "research_citations"
    __table_args__ = (
        UniqueConstraint("angle_id", "kind", "ref"),
        Index("ix_research_citations_report_id_n", "report_id", "n"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False)
    angle_id: Mapped[int] = mapped_column(ForeignKey("research_angles.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[str] = mapped_column(ChunkKind, nullable=False)
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    line: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    angle: Mapped[ResearchAngle] = relationship(back_populates="citations")


# A chunk's text is searched by words as well as by meaning; both indexes above are on the same row,
# so a hybrid search never has to reconcile two stores that drifted apart.
__all__ = ["EMBED_DIM", "Chunk", "MemoryConflict", "MemoryFact", "MemoryHit", "MemoryTag", "ResearchAngle", "ResearchCitation",
           "ResearchReport", "RetrievalRun", "text"]


class TasteSignal(Base):
    """Something a person did that says how they like the work done: accepted or refused a run, sent it back
    with notes, changed what an agent wrote, edited a plan. Captured where it happens, with no model."""

    __tablename__ = "taste_signals"
    __table_args__ = (CheckConstraint("kind IN ('accept', 'refuse', 'rework_note', 'edit_delta', 'plan_edit')",
                                      name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What the signal says: the notes, the refused finding, the hunk before and after.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    #: Set once a distillation has read it, so the next one reads only what is new.
    distilled: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TasteRule(Base, Mixin):
    """One sentence of how this person or team likes the work done, proposed from signals and active only once
    a person adopted it. Its confidence is counted — signals that support it against those that contradict
    it — never taken from a model."""

    __tablename__ = "taste_rules"
    __table_args__ = (CheckConstraint("status IN ('proposed', 'active', 'retired')", name="status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="proposed")
    support: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    contradict: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The signal ids behind it.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: Which lane and model proposed it.
    proposed_by: Mapped[str] = mapped_column(String(160), nullable=False, server_default="")
    adopted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

