"""The schema, as typed Python.

One module per part of the domain, so a table can be found by asking what it is about rather than by
scrolling. Importing this package imports them all, which is what fills `Base.metadata` for Alembic —
so a model that is not imported here does not exist as far as migrations are concerned.
"""
from .code import CodeEdge, CodeFile, CodeIndexRun, CodeSymbol
from .identity import (
    ApiToken,
    AuditEntry,
    LoginAttempt,
    Role,
    RolePermission,
    Session,
    Setting,
    Team,
    TeamMember,
    ProjectRole, RefCounter, User,
    UserRole,
    Workspace,
)
from .knowledge import (
    EMBED_DIM,
    Chunk,
    MemoryConflict,
    MemoryFact,
    MemoryHit,
    MemoryTag,
    ResearchAngle,
    ResearchCitation,
    ResearchReport,
    RetrievalRun,
    TasteRule,
    TasteSignal,
)
from .platform import AiCall, Brainstorm, CustomTool, EvalCase, EvalResult, EvalRun, EvalSuite, McpServer, McpTool, ToolRule
from .runtime import Chat, ChatFile, ChatMessage, Run, RunConflict, RunLog, RunStep, TestCoverage, TestFailure
from .work import (
    ActivityEvent,
    Agent,
    Approval,
    Blueprint,
    BlueprintTemplate,
    CodeReview,
    CustomAgent,
    ChecklistItem,
    Decision,
    Plan,
    PlanQuestion,
    PlanComment,
    PlanStep,
    Pref,
    Project,
    ProjectReference,
    ProjectSource,
    RunConfig,
    Schedule,
    ScheduleFire,
    Task,
    TaskAgent,
    TestExpectation,
    WorkflowDefinition,
    WorkflowStep,
)

__all__ = [
    "EMBED_DIM",
    "ActivityEvent", "Agent", "AiCall", "Approval", "AuditEntry", "Brainstorm", "Chat", "ChatMessage",
    "ChecklistItem", "Chunk", "CodeEdge", "CodeFile", "CodeIndexRun", "CodeSymbol", "Decision",
    "EvalCase", "EvalResult", "EvalRun", "EvalSuite",
    "LoginAttempt", "McpServer", "McpTool", "MemoryConflict", "MemoryFact", "MemoryHit", "MemoryTag",
    "Plan", "ToolRule", "ChatFile", "TasteRule", "TasteSignal", "PlanComment", "Schedule", "ScheduleFire", "RunConfig", "ProjectSource", "Blueprint", "BlueprintTemplate", "ProjectReference", "CodeReview", "CustomAgent", "CustomTool", "ApiToken",
    "PlanQuestion", "PlanStep", "Pref", "Project", "ResearchAngle", "ResearchCitation", "ResearchReport",
    "RetrievalRun", "Role", "RolePermission", "Run",
    "RunConflict", "RunLog", "RunStep", "Session", "Setting", "Task", "TaskAgent", "Team", "TeamMember",
    "TestCoverage", "TestExpectation", "TestFailure",
    "ProjectRole", "RefCounter", "User", "UserRole", "Workspace", "WorkflowDefinition", "WorkflowStep",
]
