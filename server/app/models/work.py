"""The work itself: projects, the agents that do it, tasks, plans, approvals and the activity log.

What changed from the document store: the things that *are* relationships are relationships now. A
task belongs to a project by foreign key, so a task cannot point at a project that was deleted. A
plan's steps are rows, so "how many plans stopped at step 3" is a query instead of a script that
parses JSON. Counters that used to be stored and drift (a project's open task count) are gone: the
database can count.

What stayed JSON: genuinely shapeless lists that are read whole and never filtered on — a project's
language breakdown, an agent's guardrails. JSONB, not TEXT, so even those can be queried when the day
comes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..data.base import Base, Mixin
from .enums import (
    ActorKind,
    AgentStatus,
    ApprovalStatus,
    Autonomy,
    Level,
    PlanStatus,
    Priority,
    ProjectKind,
    ProjectStatus,
    Risk,
    SourceKind,
    StepState,
    TaskStatus,
    TestExpectationKind,
)


class Project(Base, Mixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    codename: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    kind: Mapped[str] = mapped_column(ProjectKind, nullable=False, server_default="platform")
    status: Mapped[str] = mapped_column(ProjectStatus, nullable=False, server_default="active")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    repo: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # Measured, not typed in: the onboarding scan and the code index write these.
    lines_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    files_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    modules: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    db_tables: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    stored_procs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The share of the files the scan found that the code index holds. Null until it has been indexed.
    understood_pct: Mapped[int | None] = mapped_column(Integer)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Where its code is on this machine, when it was onboarded from here.
    source_kind: Mapped[str | None] = mapped_column(SourceKind)
    source_repo: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_branch: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")

    #: Read whole, never filtered on: [{name, pct}], [{label, pct}], [{id, label, note}], ["node_modules/**"]
    languages: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    excluded: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    stack: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    tasks: Mapped[list[Task]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Agent(Base, Mixin):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    icon: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(AgentStatus, nullable=False, server_default="idle")
    autonomy: Mapped[str] = mapped_column(Autonomy, nullable=False, server_default="supervised")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    tools: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    skills: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    guardrails: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")


class Task(Base, Mixin):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_project_id_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(TaskStatus, nullable=False, server_default="backlog")
    priority: Mapped[str] = mapped_column(Priority, nullable=False, server_default="NORMAL")
    risk: Mapped[str] = mapped_column(Risk, nullable=False, server_default="LOW")
    epic: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    requirement: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    worktree: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    blocked_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    files: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tests: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    layers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    project: Mapped[Project] = relationship(back_populates="tasks")
    checklist: Mapped[list[ChecklistItem]] = relationship(back_populates="task", cascade="all, delete-orphan",
                                                          order_by="ChecklistItem.n", lazy="selectin")
    assignees: Mapped[list[TaskAgent]] = relationship(back_populates="task", cascade="all, delete-orphan",
                                                      lazy="selectin")


class ChecklistItem(Base):
    __tablename__ = "task_checklist"
    # Unnamed on purpose: the naming convention gives it a name unique across the whole schema,
    # which is what Postgres needs — a unique constraint's index lives in the schema, not the table.
    __table_args__ = (UniqueConstraint("task_id", "n"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    done: Mapped[bool] = mapped_column(nullable=False, server_default="false")

    task: Mapped[Task] = relationship(back_populates="checklist")


class TaskAgent(Base):
    """Which agents a task is on. A join table, so "what is this agent working on" is a query."""

    __tablename__ = "task_agents"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    #: The agent's name as the plan named it; agents may be renamed without orphaning history.
    agent: Mapped[str] = mapped_column(String(120), primary_key=True)

    task: Mapped[Task] = relationship(back_populates="assignees")


class Plan(Base, Mixin):
    __tablename__ = "plans"
    __table_args__ = (
        Index("ix_plans_project_id_created_at", "project_id", "created_at"),
        Index("ix_plans_workflow_id_created_at", "workflow_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(PlanStatus, nullable=False, server_default="draft")
    risk: Mapped[str] = mapped_column(Risk, nullable=False, server_default="LOW")
    #: What the model that wrote the plan said of it, or nothing when it did not say.
    confidence: Mapped[int | None] = mapped_column(Integer)

    raw_requirement: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    business_requirement: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    technical_requirement: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    architecture_impact: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    #: The workflow that produced this plan; null when it was compiled from a requirement. A workflow's
    #: runs are this plan's runs — there is no second table of runs to keep in step with the first.
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_definitions.id", ondelete="SET NULL"))

    affected_modules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    affected_files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    affected_db: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    test_plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: The memory facts the compiler was given, and which lane wrote the plan.
    cited: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: The code and documents the compiler was handed from retrieval — `[{kind, ref, path}]` — so a
    #: reader can see what a plan's files and modules were read from, the way `cited` shows the facts.
    grounding: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: What "done" means for this plan, one checkable sentence each — written by the compiler or by a
    #: person — which a goal run is judged against before it stops at the signature.
    acceptance_criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    compiler: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    steps: Mapped[list[PlanStep]] = relationship(back_populates="plan", cascade="all, delete-orphan",
                                                 order_by="PlanStep.n", lazy="selectin")
    questions: Mapped[list[PlanQuestion]] = relationship(back_populates="plan", cascade="all, delete-orphan",
                                                         order_by="PlanQuestion.n", lazy="selectin")
    # selectin, not lazy: touching an unloaded relationship inside async code raises instead of
    # quietly issuing a query, so every relationship a serialiser reads is loaded up front.
    task: Mapped[Task | None] = relationship(lazy="selectin")


class WorkflowDefinition(Base, Mixin):
    """A reusable recipe a person wrote: which agents do what, and the requirement it starts from.

    Running one compiles an ordinary plan from it, and the runtime executes that plan like any other.
    """

    __tablename__ = "workflow_definitions"
    # The template is the only place the run's input goes; one without the slot would ignore it.
    __table_args__ = (CheckConstraint("strpos(requirement_template, '{input}') > 0", name="template_takes_input"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: Null means every project may run it.
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    requirement_template: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    archived: Mapped[bool] = mapped_column(nullable=False, server_default="false")

    steps: Mapped[list[WorkflowStep]] = relationship(back_populates="workflow", cascade="all, delete-orphan",
                                                     order_by="WorkflowStep.n", lazy="selectin")


class WorkflowStep(Base):
    """One write step of a workflow. Copied into the plan when it runs, so editing a workflow later never
    rewrites what an earlier run was asked to do."""

    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("workflow_id", "n"), CheckConstraint("n >= 1", name="n_positive"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
                                             nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    workflow: Mapped[WorkflowDefinition] = relationship(back_populates="steps")


class TestExpectation(Base, Mixin):
    """A person's standing decision about one test in one project: red on purpose, or quarantined.

    It has a real effect, which is why it is a row and not a label: a hand-off whose every failure is
    covered by one of these does not raise its risk.
    """

    __tablename__ = "test_expectations"
    # One word per test per project. The key is also the lookup a run's failures are joined through.
    __table_args__ = (UniqueConstraint("project_id", "test_name"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    test_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(TestExpectationKind, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PlanStep(Base):
    __tablename__ = "plan_steps"
    __table_args__ = (UniqueConstraint("plan_id", "n"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    state: Mapped[str] = mapped_column(StepState, nullable=False, server_default="todo")
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    duration_s: Mapped[int | None] = mapped_column(Integer)

    plan: Mapped[Plan] = relationship(back_populates="steps")


class PlanQuestion(Base):
    """What the compiler could not decide. Answered or deferred — both are recorded, not lost."""

    __tablename__ = "plan_questions"
    __table_args__ = (UniqueConstraint("plan_id", "n"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    deferred: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped[Plan] = relationship(back_populates="questions")


class Approval(Base, Mixin):
    """A gate. Everything that needs a person's signature waits here."""

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_status_created_at", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    tool: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    risk: Mapped[str] = mapped_column(Risk, nullable=False, server_default="LOW")
    status: Mapped[str] = mapped_column(ApprovalStatus, nullable=False, server_default="pending")
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    payload: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    #: The run this gate belongs to, when it came from one, and which of its steps is waiting.
    run_ref: Mapped[str | None] = mapped_column(String(40))
    step: Mapped[int | None] = mapped_column(Integer)

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Decision(Base, Mixin):
    """A verdict a person gave: a review accepted, a gate opened, a direction chosen."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    verdict: Mapped[str] = mapped_column(String(60), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Pref(Base, Mixin):
    """A screen setting a person changed: a skill switched off, a filter kept."""

    __tablename__ = "prefs"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class ActivityEvent(Base):
    """The product's story: who moved what, which agent did what. Streams to every open tab."""

    __tablename__ = "activity"
    __table_args__ = (Index("ix_activity_project_id_at", "project_id", "at"),)

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False,
                                         index=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_kind: Mapped[str] = mapped_column(ActorKind, nullable=False, server_default="system")
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    level: Mapped[str] = mapped_column(Level, nullable=False, server_default="info")
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_ref: Mapped[str | None] = mapped_column(String(40))
