"""What the agents are doing, and what was said to them.

A run was a JSON document with its steps, its diff and its children inside it. Now the steps are
rows, so "which step fails most often" is a query; the children point at their parent by foreign key,
so a batch cannot lose one; and the conflicts found at a merge are rows, not a list buried in a blob.

Chats are the same shape as runs on purpose: a header row and its turns, written as they happen.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..data.base import Base, Mixin
from .enums import ChatRole, ChatStatus, Level, RunRole, RunStatus, RunStepKind, RunStepStatus


class Run(Base, Mixin):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_project_id_created_at", "project_id", "created_at"),
        Index("ix_runs_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(RunStatus, nullable=False, server_default="queued")

    #: solo: one agent. agent: one of several working at once. integration: the run that merges them.
    role: Mapped[str] = mapped_column(RunRole, nullable=False, server_default="solo")
    agent: Mapped[str | None] = mapped_column(String(120))
    #: The model lane this run writes with, so parallel agents really are parallel.
    lane: Mapped[str | None] = mapped_column(String(40))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)

    # Where the work happens. Nothing here touches the checked-out tree.
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    worktree: Mapped[str] = mapped_column(Text, nullable=False)
    repo: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    base: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    requirement: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    removed: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    waiting_on: Mapped[str | None] = mapped_column(String(40))

    # What it changed, measured from git rather than claimed.
    diff_files: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    diff_insertions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    diff_deletions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    diff_commits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    tests_command: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tests_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="not run")
    tests_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    #: The review's findings and verdict, and the merge once it happened.
    review: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    merged: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    targets: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    # A step points at this run twice: the run it belongs to, and — for a merge step — the agent run
    # whose branch it brings in. Say which one is the parent, or the join is ambiguous.
    steps: Mapped[list[RunStep]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                order_by="RunStep.n", lazy="selectin",
                                                foreign_keys="RunStep.run_id")
    children: Mapped[list[Run]] = relationship(cascade="all, delete-orphan")
    conflicts: Mapped[list[RunConflict]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                        lazy="selectin")


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "n"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(RunStepKind, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(RunStepStatus, nullable=False, server_default="todo")
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ms: Mapped[int | None] = mapped_column(Integer)
    #: For a merge step: the agent run whose branch it brings in.
    child_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))

    run: Mapped[Run] = relationship(back_populates="steps", foreign_keys=[run_id])


class RunConflict(Base):
    """A branch that could not be merged, and the files that collided. Never half-applied."""

    __tablename__ = "run_conflicts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    run: Mapped[Run] = relationship(back_populates="conflicts")


class RunLog(Base):
    """A line a run wrote, as it wrote it. There are a lot of these, so they are their own table."""

    __tablename__ = "run_logs"
    __table_args__ = (Index("ix_run_logs_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    step: Mapped[int | None] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(Level, nullable=False, server_default="info")
    line: Mapped[str] = mapped_column(Text, nullable=False)


class Chat(Base, Mixin):
    """A conversation that can act: you ask, it reaches for a tool, it answers."""

    __tablename__ = "chats"
    __table_args__ = (Index("ix_chats_project_id_last_at", "project_id", "last_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(ChatStatus, nullable=False, server_default="idle")
    started_by: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                              nullable=False)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model: Mapped[str | None] = mapped_column(String(120))
    lane: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    messages: Mapped[list[ChatMessage]] = relationship(back_populates="chat", cascade="all, delete-orphan",
                                                       order_by="ChatMessage.id")


class ChatMessage(Base):
    """One turn: your question, a tool call with what it found, an answer, or a note."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_chat_id_id", "chat_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    role: Mapped[str] = mapped_column(ChatRole, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    by: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")

    # What answered, when a model did.
    model: Mapped[str | None] = mapped_column(String(120))
    lane: Mapped[str | None] = mapped_column(String(40))
    ms: Mapped[int | None] = mapped_column(Integer)

    # What the tool was, when this turn is a tool call.
    tool: Mapped[str | None] = mapped_column(String(60))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    why: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ok: Mapped[bool | None] = mapped_column()

    chat: Mapped[Chat] = relationship(back_populates="messages")
