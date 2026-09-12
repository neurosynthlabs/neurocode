"""Repositories: the only place that knows how a thing is stored.

A service asks for objects and gets objects. It never writes SQL, never holds a cursor, and never
learns that a project's task counts come from a GROUP BY rather than a field — which is exactly why a
service can be tested against a throwaway database without changing a line.
"""
from .base import DEFAULT_LIMIT, MAX_LIMIT, NotFound, Page, Repository
from .identity import (
    AuditRepository,
    RoleRepository,
    SessionRepository,
    UserRepository,
    WorkspaceRepository,
)
from .code import CodeIndexRepository
from .knowledge import ConflictRepository, MemoryRepository
from .retrieval import ChunkRepository
from .runtime import ChatRepository, RunLogRepository, RunRepository
from .work import (
    ActivityRepository,
    ApprovalRepository,
    DecisionRepository,
    PlanRepository,
    PrefRepository,
    ProjectRepository,
    TaskRepository,
)

__all__ = [
    "DEFAULT_LIMIT", "MAX_LIMIT",
    "ActivityRepository", "ApprovalRepository", "AuditRepository", "ChatRepository",
    "ChunkRepository", "CodeIndexRepository", "ConflictRepository", "DecisionRepository",
    "MemoryRepository", "NotFound", "Page", "PlanRepository", "PrefRepository", "ProjectRepository",
    "Repository", "RoleRepository", "RunLogRepository", "RunRepository", "SessionRepository",
    "TaskRepository", "UserRepository", "WorkspaceRepository",
]
