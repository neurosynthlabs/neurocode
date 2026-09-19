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
    #: Dispatched "pause before each step": the runtime stops at an approval between steps.
    step_gate: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    #: Bumped each time the plan is revised from comments, so a comment knows which version it was on.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
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


class PlanComment(Base, Mixin):
    """A person's note on a plan before it is dispatched — on a step or on the plan as a whole. It never goes
    into the plan's text; "Revise with comments" hands the open ones to the compiler, which proposes a new
    revision."""

    __tablename__ = "plan_comments"
    __table_args__ = (CheckConstraint("kind IN ('comment', 'split', 'remove', 'why', 'risky')", name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("plan_steps.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default="comment")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved: Mapped[bool] = mapped_column(nullable=False, server_default="false")


class Schedule(Base, Mixin):
    """A routine: a workflow or a requirement that becomes a plan and a run on a cadence, by a webhook, or
    when a person presses "Run now". Every firing stops at the same signature a hand-made run does."""

    __tablename__ = "schedules"
    __table_args__ = (CheckConstraint("workflow_id IS NOT NULL OR requirement <> ''", name="what"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_definitions.id", ondelete="CASCADE"))
    requirement: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: A five-field cron expression, evaluated in UTC.
    cadence: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The webhook's secret, hashed like a session token; null when the routine has no webhook.
    token_hash: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ScheduleFire(Base):
    """One firing of a routine and what became of it."""

    __tablename__ = "schedule_fires"
    __table_args__ = (CheckConstraint("trigger IN ('schedule', 'manual', 'webhook')", name="trigger"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_ref: Mapped[str | None] = mapped_column(String(40))
    run_ref: Mapped[str | None] = mapped_column(String(40))
    #: fired | refused | failed — and why, in words.
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: The first part of a webhook's payload, kept as quoted data — never read as an instruction.
    payload_excerpt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class RunConfig(Base, Mixin):
    """How a person runs or debugs this project on this machine — the Workbench's Run menu. A `run` is a
    command in a folder of the checkout; a `debug` names a program and its arguments for a debugger. It
    is what a person chose to launch, never something a model ran on its own."""

    __tablename__ = "run_configs"
    __table_args__ = (CheckConstraint("kind IN ('run', 'debug')", name="kind"),
                      CheckConstraint("language IN ('python', 'node', 'shell')", name="language"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default="run")
    language: Mapped[str] = mapped_column(String(10), nullable=False, server_default="shell")
    #: For `run`: the command line. For `debug`: the program (a file in the checkout) or a module (`-m x`).
    command: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: A folder inside the checkout, relative to its root.
    cwd: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: Extra environment for this run only — shown masked on screen, because people put keys here.
    env: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ProjectSource(Base, Mixin):
    """A further folder or repository that belongs to a project — the API beside the web app, a shared
    library, a data repository — so one project can hold several checkouts and be worked on as one. The
    project's own `source_*` columns stay its first source; these are the rest, in the order shown."""

    __tablename__ = "project_sources"
    __table_args__ = (
        CheckConstraint("kind IN ('local', 'git')", name="kind"),
        CheckConstraint("role IN ('code', 'reference')", name="role"),
        UniqueConstraint("project_id", "label"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    #: How it is named inside the project, and the folder its files appear under there: `api`, `web`, `ml`.
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: A folder on this machine, or a clone URL with any credentials removed.
    repo: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: onboarding | active | failed — and why, when it failed.
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="onboarding")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: `code` is worked on: agents may change it in worktrees. `reference` is only read — documents, a design
    #: system, another team's repository — indexed for search and grounding, never written to.
    role: Mapped[str] = mapped_column(String(10), nullable=False, server_default="code")


class Blueprint(Base, Mixin):
    """A system being designed in the Blueprint wizard: the answers a person gave, the architecture it became
    (layers, technologies, services, environments), and what came of it — a project and its scaffold."""

    __tablename__ = "blueprints"
    __table_args__ = (CheckConstraint("status IN ('draft', 'final', 'scaffolded')", name="status"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: The catalogue template (a file id) or a person's own template it started from; empty for a blank start.
    template: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    #: The project it was scaffolded into, once it was.
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class BlueprintTemplate(Base, Mixin):
    """A person's own architecture template: saved from a blueprint or imported, beside the catalogue's."""

    __tablename__ = "blueprint_templates"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ProjectReference(Base, Mixin):
    """Another project this one reads from: its code, documents and memory are searched and handed to models as
    context for this project, and never written to from here. A library the app uses, the service it calls,
    last year's version of the same product."""

    __tablename__ = "project_references"
    __table_args__ = (
        UniqueConstraint("project_id", "referenced_id"),
        CheckConstraint("project_id <> referenced_id", name="not_itself"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    referenced_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Why it is referenced, in a person's words — shown to models beside what they read from it.
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class CustomAgent(Base, Mixin):
    """An agent a person defined — a name, a role, its own instructions, the lane it prefers, the tools it may
    use (capped by the tool rules: it cannot grant itself more) — for the workspace or one project. Agents
    defined as files in a repository (.neurocode/agents, .claude/agents) are read from disk, not stored here."""

    __tablename__ = "custom_agents"
    __table_args__ = (
        CheckConstraint("mode IN ('primary', 'subagent')", name="mode"),
        UniqueConstraint("project_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(160), nullable=False, server_default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    lane: Mapped[str | None] = mapped_column(String(40))
    tools: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8")
    mode: Mapped[str] = mapped_column(String(10), nullable=False, server_default="subagent")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class CodeReview(Base, Mixin):
    """A review a person asked for, of any diff — a branch against its base, the working tree, a pushed branch —
    read by a model lane other than the writer's, with the repository's REVIEW.md as the reviewer's brief."""

    __tablename__ = "code_reviews"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'done', 'failed')", name="status"),
        CheckConstraint("target IN ('branch', 'working-tree', 'commit-range')", name="target"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Which source of the project (its label); the first source when empty.
    source: Mapped[str] = mapped_column(String(60), nullable=False, server_default="")
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    base: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    head: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: The fingerprint of the patch the reviewer read, so the review is tied to exactly that diff.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    findings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    verdict: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="running")
    lane: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

