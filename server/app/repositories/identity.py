"""Who is here, what they may do, and what they did.

The permission lookup is the one worth pointing at. It used to be: load the user, load their roles,
load each role's permissions, union them in Python. It is one statement now — a join with DISTINCT —
which is both faster and harder to get subtly wrong when someone wears three roles.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select

from ..data.base import utcnow
from ..models import AuditEntry, Role, RolePermission, Session, User, UserRole, Workspace
from .base import Page, Repository


class UserRepository(Repository[User]):
    model = User

    async def by_email(self, email: str) -> User | None:
        """Case-insensitive without `lower()` anywhere: the column itself is `citext`."""
        return await self.one(User.email == email.strip())

    async def active(self, *, limit: int | None = None, offset: int = 0) -> Page[User]:
        return await self.page(User.status == "active", order_by=User.name, limit=limit, offset=offset)

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

    async def all_ordered(self) -> list[Role]:
        return await self.list(order_by=Role.name, limit=200)

    async def permissions(self, role_id: str) -> set[str]:
        stmt = select(RolePermission.permission).where(RolePermission.role_id == role_id)
        return set((await self.session.execute(stmt)).scalars())

    async def set_permissions(self, role_id: str, permissions: list[str]) -> None:
        await self.session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        for permission in dict.fromkeys(permissions):
            self.session.add(RolePermission(role_id=role_id, permission=permission))
        await self.session.flush()

    async def worn_by(self, role_id: str) -> int:
        """How many people wear it — the check before a role is deleted."""
        stmt = select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)
        return int((await self.session.execute(stmt)).scalar_one())


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

    async def since(self, at: datetime, *, limit: int | None = None) -> list[AuditEntry]:
        return await self.list(AuditEntry.at > at, order_by=AuditEntry.seq, limit=limit)
