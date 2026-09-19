"""People, access and the record of what was changed — in the shape the admin screens read.

Nothing here counts anything for itself. A role's membership and a workspace's totals are handed in
by the caller, which asked the database for them: the old store kept those numbers in columns, and a
kept number is a number that drifts the first time someone is removed in a way nobody remembered to
subtract.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..models import AuditEntry, Role, Team, User, Workspace
from .work import when

#: What the workspace calls itself before anyone has named it.
UNNAMED = "NeuroCode"


def user_json(user: User, *, roles: Iterable[str], teams: Iterable[str]) -> dict[str, Any]:
    return {
        "id": user.id, "email": user.email, "name": user.name, "status": user.status,
        "roles": list(roles), "teams": list(teams), "lastLoginAt": when(user.last_login_at),
        "createdAt": when(user.created_at),
    }


def role_json(role: Role, *, permissions: Iterable[str], members: int) -> dict[str, Any]:
    """`permissions` arrives in catalogue order and `members` is counted, so this is only assembly."""
    return {
        "id": role.id, "name": role.name, "description": role.description, "builtin": role.builtin,
        "permissions": list(permissions), "members": members,
    }


def team_json(team: Team) -> dict[str, Any]:
    return {
        "id": team.id, "name": team.name, "description": team.description,
        "members": sorted(m.user_id for m in team.members), "createdAt": when(team.created_at),
    }


def audit_json(entry: AuditEntry, *, user: str | None = None) -> dict[str, Any]:
    """`user` is the name at the time of reading; `userId` is what the entry itself holds, and holds
    even after the account is gone."""
    return {
        "seq": entry.seq, "at": when(entry.at), "user": user, "userId": entry.user_id,
        "action": entry.action, "target": entry.target, "detail": entry.detail or {}, "ip": entry.ip,
    }


def workspace_json(workspace: Workspace | None, *, people: int, roles: int, builtin_roles: int,
                   teams: int, security: dict[str, int]) -> dict[str, Any]:
    """No workspace row at all is a real state — it is what makes the app show its setup wizard — so
    it answers with the placeholder name rather than a 404 the screens have no branch for.

    `security` is the rules sign-in really enforces, handed in by the caller from the settings and the
    constant the identity service reads, so the screen cannot describe a rule other than the one applied.
    """
    return {
        "name": workspace.name if workspace else UNNAMED,
        "createdAt": when(workspace.created_at) if workspace else None,
        "people": people, "roles": roles, "builtinRoles": builtin_roles, "teams": teams,
        "security": security,
    }
