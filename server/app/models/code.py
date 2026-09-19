"""The code index: what files exist, what they declare, and what depends on what.

A project's rows are replaced in one transaction on every index, so a reader never sees half an
index, and they go when the project goes.

The hand-rolled FTS5 table is gone. A symbol is searched two ways off its own row: a trigram index on
the name (so "tax" finds `TaxService` and survives a typo) and a `tsvector` over the name split at
camelCase and snake_case boundaries. Nothing to keep in step by hand.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

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
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..data.base import Base
from .enums import EdgeKind


class CodeFile(Base):
    __tablename__ = "code_files"
    __table_args__ = (
        UniqueConstraint("project_id", "path"),
        Index("ix_code_files_project_id_module", "project_id", "module"),
        Index("ix_code_files_path_trgm", "path", postgresql_using="gin",
              postgresql_ops={"path": "gin_trgm_ops"}),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    #: Relative to the project root, forward slashes.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    module: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    lines: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sha1: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    #: Decision points: if, for, while, case, catch, && and ||.
    complexity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Commits touching it in the last 90 days.
    churn: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    symbols: Mapped[list[CodeSymbol]] = relationship(back_populates="file", cascade="all, delete-orphan")


class CodeSymbol(Base):
    __tablename__ = "code_symbols"
    __table_args__ = (
        Index("ix_code_symbols_project_id_name", "project_id", "name"),
        Index("ix_code_symbols_name_trgm", "name", postgresql_using="gin",
              postgresql_ops={"name": "gin_trgm_ops"}),
        Index("ix_code_symbols_search", "search", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("code_files.id", ondelete="CASCADE"), nullable=False,
                                         index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    line: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The declaration's last line, where the parser knows it (tree-sitter and Python's ast do); null otherwise.
    end_line: Mapped[int | None] = mapped_column(Integer)
    exported: Mapped[bool] = mapped_column(nullable=False, server_default="false")

    #: The name as words: TaxService and tax_service both become "tax service", so either finds it.
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', regexp_replace(regexp_replace(name, '([a-z0-9])([A-Z])', "
                 "'\\1 \\2', 'g'), '[_.-]', ' ', 'g'))", persisted=True),
    )

    file: Mapped[CodeFile] = relationship(back_populates="symbols")


class CodeEdge(Base):
    """from_file depends on to_file (imports, uses) or on a database object (reads, writes, calls).
    to_file is null when the target lives outside the repository, such as a package."""

    __tablename__ = "code_edges"
    __table_args__ = (
        Index("ix_code_edges_project_id_target", "project_id", "target"),
        Index("ix_code_edges_to_file_kind", "to_file", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    from_file: Mapped[int] = mapped_column(ForeignKey("code_files.id", ondelete="CASCADE"), nullable=False,
                                           index=True)
    to_file: Mapped[int | None] = mapped_column(ForeignKey("code_files.id", ondelete="CASCADE"))
    target: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(EdgeKind, nullable=False)


class CodeIndexRun(Base):
    """The last index of a project: when, how long, and what it found."""

    __tablename__ = "code_index_runs"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    root: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  nullable=False)
    ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    symbols: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    edges: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unresolved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: language → the parser that read it.
    parsers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
