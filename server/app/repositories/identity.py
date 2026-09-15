"""Who is here, what they may do, and what they did.

The permission lookup is the one worth pointing at. It used to be: load the user, load their roles,
load each role's permissions, union them in Python. It is one statement now — a join with DISTINCT —
which is both faster and harder to get subtly wrong when someone wears three roles.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select

from ..data.base import utcnow
from ..models import (
    AuditEntry,
    Role,
    RolePermission,
    Session,
    Team,
    TeamMember,
    User,
    UserRole,
    Workspace,
)
from .base import MAX_LIMIT, Page, Repository, bounded


class UserRepository(Repository[User]):
    model = User

    async def by_email(self, email: str) -> User | None:
        """Case-insensitive without `lower()` anywhere: the column itself is `citext`."""
        return await self.one(User.email == email.strip())

    async def active(self, *, limit: int | None = None, offset: int = 0) -> Page[User]:
        return await self.page(User.status == "active", order_by=User.name, limit=limit, offset=offset)

    async def directory(self, *, limit: int | None = None, offset: int = 0) -> Page[User]:
        """Everyone, oldest account first — the order the people screen has always listed them in.
        A workspace with more people than the ceiling is paged, not silently cut in half."""
        return await self.page(order_by=[User.created_at, User.id], limit=limit or MAX_LIMIT,
                               offset=offset)

    async def existing(self, user_ids: Sequence[str]) -> set[str]:
        """Which of these people are really here — the check before a team is given its members."""
        if not user_ids:
            return set()
        stmt = select(User.id).where(User.id.in_(list(dict.fromkeys(user_ids))))
        return set((await self.session.execute(stmt)).scalars())

    async def roles_by_user(self, user_ids: Sequence[str]) -> dict[str, list[str]]:
        """Every listed person's roles in one statement, rather than one round trip each."""
        if not user_ids:
            return {}
        stmt = (select(UserRole.user_id, UserRole.role_id)
                .where(UserRole.user_id.in_(list(user_ids))).order_by(UserRole.role_id))
        out: dict[str, list[str]] = {}
        for user_id, role_id in (await self.session.execute(stmt)).all():
            out.setdefault(user_id, []).append(role_id)
        return out

    async def permissions(self, user_id: str) -> set[str]:
        """Everything this person may do, from every role they wear, in one statement."""
        stmt = (select(RolePermission.permission)
                .join(UserRole, UserRole.role_id == RolePermission.role_id)
                .where(UserRole.user_id == user_id).distinct())
        return set((await self.session.execute(stmt)).scalars())

    async def role_ids(self, user_id: str) -> list[str]:
        stmt = select(UserRole.role_id).where(UserRole.user_id == user_id).order_by(UserRole.role_id)
        return list((await self.session.execute(stmt)).scalars())

    async def with_permission(self, permission: str) -> list[User]:
        """Who could sign this? Asked of the database, so an empty approval queue can say why."""
        stmt = (select(User).join(UserRole, UserRole.user_id == User.id)
                .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                .where(RolePermission.permission == permission, User.status == "active")
                .order_by(User.name).distinct())
        return list((await self.session.execute(stmt)).scalars().unique())

    async def count_active_with(self, permission: str) -> int:
        stmt = (select(func.count(func.distinct(User.id)))
                .select_from(User).join(UserRole, UserRole.user_id == User.id)
                .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                .where(RolePermission.permission == permission, User.status == "active"))
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_active_with_role(self, role_id: str, *, except_user: str | None = None) -> int:
        """How many active people wear this role, optionally not counting one of them."""
        stmt = (select(func.count(func.distinct(User.id)))
                .select_from(User).join(UserRole, UserRole.user_id == User.id)
                .where(UserRole.role_id == role_id, User.status == "active"))
        if except_user:
            stmt = stmt.where(User.id != except_user)
        return int((await self.session.execute(stmt)).scalar_one())

    async def set_roles(self, user_id: str, role_ids: list[str]) -> None:
        await self.session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role_id in dict.fromkeys(role_ids):
            self.session.add(UserRole(user_id=user_id, role_id=role_id))
        await self.session.flush()

    async def touch_login(self, user: User) -> None:
        user.last_login_at = utcnow()
        await self.session.flush()


class WorkspaceRepository(Repository[Workspace]):
    """One row, or none at all — and "none at all" is what makes the app show its setup wizard."""

    model = Workspace

    async def current(self) -> Workspace | None:
        return await self.get(1)

    async def name(self) -> str | None:
        found = await self.current()
        return found.name if found else None

    async def name_it(self, name: str) -> Workspace:
        """Set on first run. Called again, it renames rather than making a second workspace."""
        found = await self.current()
        if found is None:
            return await self.add(Workspace(id=1, name=name.strip()))
        found.name = name.strip()
        await self.session.flush()
        return found


class RoleRepository(Repository[Role]):
    model = Role

    async def builtin_first(self, *, limit: int | None = None, offset: int = 0) -> Page[Role]:
        """The order the access screen reads: what the workspace ships with, then what it grew."""
        # `rank` and not `created_at`: the built-in roles are written in one transaction, so every
        # one of them carries the same timestamp and the tie fell through to the id — which put Admin
        # above Owner on a screen whose whole meaning is that Owner comes first.
        return await self.page(order_by=[Role.builtin.desc(), Role.rank, Role.id],
                               limit=limit or MAX_LIMIT, offset=offset)

    async def members_by_role(self) -> dict[str, int]:
        """role id → how many people wear it. One GROUP BY behind every "12 people" on the screen."""
        stmt = select(UserRole.role_id, func.count()).group_by(UserRole.role_id)
        return {role_id: int(n) for role_id, n in (await self.session.execute(stmt)).all()}

    async def permissions(self, role_id: str) -> set[str]:
        stmt = select(RolePermission.permission).where(RolePermission.role_id == role_id)
        return set((await self.session.execute(stmt)).scalars())

        await self.session.flush()

    async def replace_permissions(self, role: Role, permissions: Sequence[str]) -> None:
        """What a role may do, rewritten whole.

        Through the relationship rather than a bare DELETE, so what the role carries in memory and
        what the database holds cannot disagree — the answer a screen gets back is the one that was
        stored. The removals are flushed first: re-granting a permission would otherwise insert the
        row that is still on its way out.
        """
        role.permissions.clear()
        await self.session.flush()
        for permission in dict.fromkeys(permissions):
            role.permissions.append(RolePermission(permission=permission))
        await self.session.flush()

    async def worn_by(self, role_id: str) -> int:
        """How many people wear it — the check before a role is deleted."""
        stmt = select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)
        return int((await self.session.execute(stmt)).scalar_one())


class TeamRepository(Repository[Team]):
    model = Team

    async def all_ordered(self, *, limit: int | None = None, offset: int = 0) -> Page[Team]:
        return await self.page(order_by=Team.name, limit=limit or MAX_LIMIT, offset=offset)

    async def by_name(self, name: str) -> Team | None:
        """Case-insensitive, like the column: two teams cannot differ by a capital letter."""
        return await self.one(Team.name == name.strip())

    async def teams_of(self, user_id: str) -> list[str]:
        stmt = (select(TeamMember.team_id).where(TeamMember.user_id == user_id)
                .order_by(TeamMember.team_id))
        return list((await self.session.execute(stmt)).scalars())

    async def teams_by_user(self, user_ids: Sequence[str]) -> dict[str, list[str]]:
        """Every listed person's teams in one statement — the people screen shows them per row."""
        if not user_ids:
            return {}
        stmt = (select(TeamMember.user_id, TeamMember.team_id)
                .where(TeamMember.user_id.in_(list(user_ids))).order_by(TeamMember.team_id))
        out: dict[str, list[str]] = {}
        for user_id, team_id in (await self.session.execute(stmt)).all():
            out.setdefault(user_id, []).append(team_id)
        return out

    async def set_members(self, team: Team, user_ids: Sequence[str]) -> None:
        """Membership replaced whole, through the relationship and in two flushes — the departures
        reach the database before the arrivals, so re-adding someone who is already there cannot
        collide with the row being deleted."""
        team.members.clear()
        await self.session.flush()
        for user_id in dict.fromkeys(user_ids):
            team.members.append(TeamMember(user_id=user_id))
        await self.session.flush()


class SessionRepository(Repository[Session]):
    model = Session

    async def by_token(self, token_hash: str) -> Session | None:
        """A sign-in that is still valid. An expired one is simply not there."""
        return await self.one(Session.token_hash == token_hash, Session.expires_at > utcnow())

    async def open_for(self, user_id: str) -> list[Session]:
        return await self.list(Session.user_id == user_id, Session.expires_at > utcnow(),
                               order_by=Session.created_at.desc())

    async def end(self, token_hash: str) -> None:
        await self.session.execute(delete(Session).where(Session.token_hash == token_hash))

    async def end_all_for(self, user_id: str) -> int:
        result = await self.session.execute(delete(Session).where(Session.user_id == user_id))
        return int(result.rowcount or 0)

    async def purge_expired(self) -> int:
        result = await self.session.execute(delete(Session).where(Session.expires_at <= utcnow()))
        return int(result.rowcount or 0)


class AuditRepository(Repository[AuditEntry]):
    model = AuditEntry

    async def record(self, *, action: str, user_id: str | None, target: str = "",
                     detail: dict[str, Any] | None = None, ip: str = "") -> AuditEntry:
        return await self.add(AuditEntry(action=action, user_id=user_id, target=target,
                                         detail=detail or {}, ip=ip))

    async def recent(self, *, action: str | None = None, limit: int | None = None,
                     offset: int = 0) -> Page[AuditEntry]:
        where = [AuditEntry.action == action] if action else []
        return await self.page(*where, order_by=AuditEntry.seq.desc(), limit=limit, offset=offset)

    async def newest(self, *, before: int | None = None,
                     limit: int | None = None) -> list[tuple[AuditEntry, str | None]]:
        """The log with each entry's author, newest first, paged by the last `seq` a screen has seen.

        A cursor rather than an offset, because the log grows while it is being read: a sign-in
        during the scroll would shift every later page by one and quietly hide an entry. The join is
        an outer one — the record outlives the account, so a deleted person leaves a nameless line
        rather than none at all.
        """
        stmt = (select(AuditEntry, User.name)
                .join(User, User.id == AuditEntry.user_id, isouter=True)
                .order_by(AuditEntry.seq.desc()).limit(bounded(limit)))
        if before is not None:
            stmt = stmt.where(AuditEntry.seq < before)
        return [(entry, name) for entry, name in (await self.session.execute(stmt)).all()]

    async def since(self, at: datetime, *, limit: int | None = None) -> list[AuditEntry]:
        return await self.list(AuditEntry.at > at, order_by=AuditEntry.seq, limit=limit)
