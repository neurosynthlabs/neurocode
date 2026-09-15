"""The platform around the work: tools it can reach, what the models cost, and what may run unasked.

The usage ledger earns its own table more than anything else here. Every model call and every offline
answer lands in it, which is how a lane knows what it has spent this minute and this day — the
budget that keeps free tiers inside their limits is a query over these rows, not a counter someone
remembers to increment.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..data.base import Base, Mixin
from .enums import McpStatus, McpTransport, Risk, Scope


class McpServer(Base, Mixin):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    transport: Mapped[str] = mapped_column(McpTransport, nullable=False, server_default="stdio")
    status: Mapped[str] = mapped_column(McpStatus, nullable=False, server_default="disconnected")
    scope: Mapped[str] = mapped_column(Scope, nullable=False, server_default="global")
    command: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    resources: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    prompts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    calls_24h: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, server_default="0")
    #: Registered from this app: its output stays untrusted until a person promotes it.
    untrusted: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    default_effect: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ask")
    #: The config exactly as it was reviewed in the wizard.
    config: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    tools: Mapped[list[McpTool]] = relationship(back_populates="server", cascade="all, delete-orphan",
                                                lazy="selectin")


class McpTool(Base):
    __tablename__ = "mcp_tools"

    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    risk: Mapped[str] = mapped_column(Risk, nullable=False, server_default="LOW")

    server: Mapped[McpServer] = relationship(back_populates="tools")


class PermissionRule(Base, Mixin):
    """What a tool may do without asking. `ask` is the default, and the honest one."""

    __tablename__ = "permission_rules"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    effect: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ask")
    risk: Mapped[str] = mapped_column(Risk, nullable=False, server_default="LOW")
    scope: Mapped[str] = mapped_column(Scope, nullable=False, server_default="global")
    hits_24h: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class Brainstorm(Base, Mixin):
    """An idea argued with itself: the stages, the case against, the MVP, the roadmap."""

    __tablename__ = "brainstorms"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    idea: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="complete")
    verdict: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")

    nodes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    devils_advocate: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    mvp: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    roadmap: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    compiler: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class AiCall(Base):
    """One line per model call and per offline answer: the ledger the router budgets against."""

    __tablename__ = "ai_calls"
    __table_args__ = (
        Index("ix_ai_calls_lane_at", "lane", "at"),
        Index("ix_ai_calls_feature_at", "feature", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False,
                                         index=True)
    #: compile | ask | brainstorm | extract | agent | review | chat | embed | test
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    lane: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    model: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    ok: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The agent that asked for it, when one did — plain text, because the roster is editable and an
    #: entry in the ledger must outlive the agent it describes. Without it there was no honest answer
    #: to "what has this agent spent", so the screen showed a zero that looked like a measurement.
    agent: Mapped[str] = mapped_column(String(60), nullable=False, server_default="")
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
