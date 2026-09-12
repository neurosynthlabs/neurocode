"""The vocabularies the domain actually has.

These were TEXT columns with a CHECK, or nothing at all. As real Postgres enum types they are one
definition each: the database refuses a status nobody defined, every table that uses a vocabulary
uses the *same* one, and adding a value is a migration you can see rather than a string someone typed
differently in a second place.
"""
from __future__ import annotations

from sqlalchemy import Enum

# How dangerous a thing is. Shared by tasks, plans, approvals and MCP tools.
Risk = Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="risk")
Confidence = Enum("LOW", "MEDIUM", "HIGH", name="confidence")
#: `tool` is what a run's own output is logged as — the command it ran, the file it wrote, the model
#: answering. The activity log never uses it; run logs use little else.
Level = Enum("info", "ok", "warn", "err", "tool", name="level")

# Work
ProjectKind = Enum("legacy", "greenfield", "platform", name="project_kind")
ProjectStatus = Enum("active", "onboarding", "paused", "archived", name="project_status")
SourceKind = Enum("git", "local", name="source_kind")
TaskStatus = Enum("backlog", "planning", "in_progress", "review", "blocked", "done", name="task_status")
Priority = Enum("LOW", "NORMAL", "HIGH", "URGENT", name="priority")
StepState = Enum("done", "active", "todo", "failed", "skipped", name="step_state")
PlanStatus = Enum("draft", "dispatched", name="plan_status")
ApprovalStatus = Enum("pending", "approved", "denied", name="approval_status")
ActorKind = Enum("human", "agent", "system", "hook", "tool", name="actor_kind")

# Agents
AgentStatus = Enum("running", "idle", "waiting", "blocked", "error", "disabled", name="agent_status")
Autonomy = Enum("supervised", "semi", "autonomous", name="autonomy")

# Runs and sessions
RunStatus = Enum("queued", "running", "waiting", "done", "failed", "cancelled", name="run_status")
RunRole = Enum("solo", "agent", "integration", name="run_role")
RunStepKind = Enum("edit", "merge", "test", "review", "handoff", name="run_step_kind")
RunStepStatus = Enum("todo", "running", "waiting", "done", "failed", "skipped", name="run_step_status")
ChatStatus = Enum("idle", "thinking", name="chat_status")
ChatRole = Enum("you", "assistant", "tool", "note", name="chat_role")

# Code index
EdgeKind = Enum("imports", "uses", "reads", "writes", "calls", name="edge_kind")

# Knowledge
MemoryCategory = Enum("human", "project", "architecture", "business_rules", "legacy", "database",
                      "bugs", "decisions", "incidents", "preferences", "code", name="memory_category")
ConflictStatus = Enum("open", "resolved", name="conflict_status")
ChunkKind = Enum("code", "doc", "memory", name="chunk_kind")

# Platform
McpTransport = Enum("stdio", "http", "sse", name="mcp_transport")
McpStatus = Enum("connected", "disconnected", "error", "auth_required", name="mcp_status")
Scope = Enum("global", "project", "local", name="scope")
