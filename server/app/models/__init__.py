"""The schema, as typed Python.

One module per part of the domain, so a table can be found by asking what it is about rather than by
scrolling. Importing this package imports them all, which is what fills `Base.metadata` for Alembic —
so a model that is not imported here does not exist as far as migrations are concerned.
"""
from .code import CodeEdge, CodeFile, CodeIndexRun, CodeSymbol
from .identity import (
    AuditEntry,
    LoginAttempt,
    Role,
    RolePermission,
    Session,
    Setting,
    Team,
    TeamMember,
    User,
    UserRole,
    Workspace,
)
from .knowledge import EMBED_DIM, Chunk, MemoryConflict, MemoryFact, MemoryTag, RetrievalRun
from .platform import AiCall, Brainstorm, McpServer, McpTool, PermissionRule
from .runtime import Chat, ChatMessage, Run, RunConflict, RunLog, RunStep
from .work import (
    ActivityEvent,
    Agent,
    Approval,
    ChecklistItem,
    Decision,
    Plan,
    PlanQuestion,
    PlanStep,
    Pref,
    Project,
    Task,
    TaskAgent,
)

__all__ = [
    "EMBED_DIM",
    "ActivityEvent", "Agent", "AiCall", "Approval", "AuditEntry", "Brainstorm", "Chat", "ChatMessage",
    "ChecklistItem", "Chunk", "CodeEdge", "CodeFile", "CodeIndexRun", "CodeSymbol", "Decision",
    "LoginAttempt", "McpServer", "McpTool", "MemoryConflict", "MemoryFact", "MemoryTag",
    "PermissionRule", "Plan",
    "PlanQuestion", "PlanStep", "Pref", "Project", "RetrievalRun", "Role", "RolePermission", "Run",
    "RunConflict", "RunLog", "RunStep", "Session", "Setting", "Task", "TaskAgent", "Team", "TeamMember",
    "User", "UserRole", "Workspace",
]
