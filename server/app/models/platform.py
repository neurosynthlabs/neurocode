"""The platform around the work: tools it can reach, what the models cost, and what may run unasked.

The usage ledger earns its own table more than anything else here. Every model call and every offline
answer lands in it, which is how a lane knows what it has spent this minute and this day — the
budget that keeps free tiers inside their limits is a query over these rows, not a counter someone
remembers to increment.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..data.base import Base, Mixin
from .enums import EvalKind, EvalStatus, EvalTarget, McpStatus, McpTransport, Risk, RunStatus, Scope


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
        # Partial: most calls belong to no run (compiling, asking memory, sessions), and an index over
        # their nulls would be most of the index while answering nothing anyone asks.
        Index("ix_ai_calls_run_id", "run_id", postgresql_where=text("run_id IS NOT NULL")),
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
    #: The agent run the call was made for, so a run's tokens are a sum over its own lines rather than a
    #: guess by agent and time window. SET NULL: the ledger outlives the run, as it outlives users.
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class EvalSuite(Base, Mixin):
    """A named set of cases against one real target, and the score a run must reach to pass."""

    __tablename__ = "eval_suites"
    __table_args__ = (
        CheckConstraint("threshold BETWEEN 0 AND 100", name="threshold_is_a_percentage"),
        # The compiler, memory answers and retrieval all read one project's workspace; with no project
        # there is nothing for them to read, and the suite could only ever fail.
        CheckConstraint("target_kind NOT IN ('compile', 'ask', 'retrieval') OR project_id IS NOT NULL",
                        name="project_when_target_reads_one"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(EvalKind, nullable=False, server_default="capability", index=True)
    target_kind: Mapped[str] = mapped_column(EvalTarget, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    #: Null means the router chooses, as it would for the feature itself.
    lane: Mapped[str | None] = mapped_column(String(40))
    #: Only the `prompt` target reads it.
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    #: Whether an answer from the offline rules may count. Off by default: a suite that passes with no
    #: model has measured the rules, not the model.
    allow_offline: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    cases: Mapped[list[EvalCase]] = relationship(back_populates="suite", cascade="all, delete-orphan",
                                                 order_by="EvalCase.n", lazy="selectin")


class EvalCase(Base, Mixin):
    """One input and the checks its output must pass."""

    __tablename__ = "eval_cases"
    __table_args__ = (
        UniqueConstraint("suite_id", "n"),
        CheckConstraint("jsonb_typeof(checks) = 'array' AND jsonb_array_length(checks) >= 1",
                        name="at_least_one_check"),
        CheckConstraint("weight >= 1", name="weight_positive"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("eval_suites.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    #: JSONB because each kind of check has its own shape — {kind: contains, value},
    #: {kind: json_equals, path, value}, {kind: retrieves, ref, k}, {kind: judge, rubric} — and a case
    #: is always read whole. No default: an empty list is exactly what the check above refuses.
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    #: The plan a regression case was captured from, when it was.
    source_plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))

    suite: Mapped[EvalSuite] = relationship(back_populates="cases")


class EvalRun(Base, Mixin):
    """One real execution of a suite: on which lane, how each case went, and the score it came to."""

    __tablename__ = "eval_runs"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="score_is_a_percentage"),
        # "The latest finished run of each suite". Ascending on purpose: a B-tree is read backwards as
        # cheaply as forwards, so ORDER BY finished_at DESC uses it without a DESC in the definition.
        Index("ix_eval_runs_suite_id_finished_at", "suite_id", "finished_at",
              postgresql_where=text("status = 'done'")),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    suite_id: Mapped[str] = mapped_column(ForeignKey("eval_suites.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(RunStatus, nullable=False, server_default="queued", index=True)
    #: As requested; null means the router chose.
    lane: Mapped[str | None] = mapped_column(String(40))
    #: Null until the run finishes. A run that never scored has no score, not a zero.
    score: Mapped[int | None] = mapped_column(Integer)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    partial: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    errored: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list[EvalResult]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                     order_by="EvalResult.id", lazy="selectin")


class EvalResult(Base):
    """What one case produced in one run, how each of its checks scored, and a person's override."""

    __tablename__ = "eval_results"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id"),
        CheckConstraint("score BETWEEN 0 AND 1", name="score_is_a_fraction"),
        Index("ix_eval_results_case_id_at", "case_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("eval_cases.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(EvalStatus, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    #: Clipped by the runner before it is written.
    output: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: [{kind, ok, observed}] — one entry per check the case carries, in the same order.
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    lane: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    model: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    ms: Mapped[int | None] = mapped_column(Integer)
    offline: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    judge_model: Mapped[str | None] = mapped_column(String(120))
    judge_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: A person's verdict over the machine's. The machine's stays beside it, so both are visible.
    override_status: Mapped[str | None] = mapped_column(EvalStatus)
    override_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    override_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[EvalRun] = relationship(back_populates="results")
