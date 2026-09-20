"""Accounts, sign-in and the guards around them.

The rules that used to be scattered through route handlers are here, each with a name: the last
active Owner cannot be removed, nobody disables their own account, only an Owner grants the Owner
role, a new password ends every other session. A route's job is now to say who is asking and turn a
refusal into a status code.

Nothing in this file knows what HTTP is. The two queries it does write — the sign-in lock-out window
and a person's project grants — are asked of the session directly because both are part of deciding
who is asking, which is this file's whole job.
"""
from __future__ import annotations

import asyncio

import re
import secrets as pysecrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import (LoginAttempt, Project, ProjectRole, RolePermission,
                      Session as SessionRow, User as UserRow)
from ..repositories import AuditRepository, RoleRepository, SessionRepository, UserRepository
from ..settings import Settings, settings as get_settings
from .errors import Denied, Refused
from .security import hash_password, new_token, token_hash, verify_password

#: scrypt is meant to be expensive — about a fifth of a second of pure CPU at these parameters, which
#: is the whole point — so it runs on a worker thread. On the event loop it stopped every other
#: request in the process for that long, on every sign-in and every password an admin set.
async def _hash(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def _verify(password: str, stored: str) -> bool:
    return await asyncio.to_thread(verify_password, password, stored)

MIN_PASSWORD = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Wrong attempts older than this are forgotten, so a slow trickle never adds up to a lock-out.
WINDOW = timedelta(minutes=5)


#: The rights a project grant can never take away. An Owner who restricts a project must still be able
#: to unrestrict it, and the audit log must still cover every project — otherwise a restricted project
#: becomes a room in the workspace that the workspace cannot get back into.
NEVER_NARROWED = frozenset({"workspace:admin", "users:manage", "roles:manage", "teams:manage",
                            "audit:read"})


@dataclass(frozen=True)
class Person:
    """Who is asking, as the rest of the app sees them. The same shape the screens already read."""

    id: str
    email: str
    name: str
    status: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    #: The restricted projects this person holds a grant in, and what that grant carries. Empty in
    #: every workspace until somebody restricts a project, which is the point of the flag.
    #: `compare=False`: a mapping is not hashable, and a Person is compared by who they are.
    project_rights: Mapping[str, frozenset[str]] = field(default_factory=dict, compare=False)

    def can(self, *perms: str) -> bool:
        return all(p in self.permissions for p in perms)

    def must(self, permission: str, what: str = "") -> None:
        if permission not in self.permissions:
            raise Denied(permission, what)

    def in_project(self, project_id: str, restricted: bool) -> frozenset[str]:
        """What this person may do inside one project. A grant narrows; it can never widen.

        An open project is the workspace set, unchanged — which is every project on the day this
        ships. A restricted one is that set cut to what the grant carries, so nobody ever gains a
        right from a project they did not already hold across the workspace, and reviewing somebody's
        access stays one screen. What `NEVER_NARROWED` names survives either way.
        """
        if not restricted:
            return self.permissions
        kept = self.permissions & NEVER_NARROWED
        granted = self.project_rights.get(project_id)
        return kept if granted is None else frozenset((self.permissions & granted) | kept)

    def may_see(self, project_id: str, restricted: bool) -> bool:
        """Whether this project exists at all for this person.

        A restricted project somebody holds no grant in answers 404 rather than 403, because "there
        is a project here you may not open" is itself something they were not told.
        """
        return (not restricted or project_id in self.project_rights
                or bool(self.permissions & NEVER_NARROWED))

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name, "status": self.status,
                "roles": list(self.roles), "permissions": sorted(self.permissions),
                # Only the restricted projects; an open one is absent, meaning "the workspace set".
                "projectRights": {pid: sorted(held) for pid, held in sorted(self.project_rights.items())}}


class IdentityService:
    def __init__(self, session: AsyncSession, config: Settings | None = None) -> None:
        self.session = session
        self.config = config or get_settings()
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.sessions = SessionRepository(session)
        self.audit = AuditRepository(session)

    # ── reading ──────────────────────────────────────────────────
    async def count(self) -> int:
        return await self.users.count()

    async def person(self, user_id: str) -> Person | None:
        row = await self.users.get(user_id)
        if row is None:
            return None
        return Person(row.id, row.email, row.name, row.status,
                      tuple(await self.users.role_ids(user_id)),
                      frozenset(await self.users.permissions(user_id)),
                      await self.project_rights(user_id))

    async def project_rights(self, user_id: str) -> dict[str, frozenset[str]]:
        """This person's grants in the restricted projects they are listed in.

        One indexed query, and in almost every workspace it returns nothing: a project is open to
        everyone until somebody restricts it, and only a restricted project narrows anything. The
        join is an outer one on purpose — a grant naming a role that carries no permissions is still
        a grant, and the person is still listed on that project.
        """
        stmt = (select(ProjectRole.project_id, RolePermission.permission)
                .join(Project, Project.id == ProjectRole.project_id)
                .outerjoin(RolePermission, RolePermission.role_id == ProjectRole.role_id)
                .where(ProjectRole.user_id == user_id, Project.restricted.is_(True)))
        found: dict[str, set[str]] = {}
        for project_id, permission in (await self.session.execute(stmt)).all():
            held = found.setdefault(project_id, set())
            if permission is not None:
                held.add(permission)
        return {pid: frozenset(held) for pid, held in found.items()}

    async def need(self, user_id: str) -> Person:
        found = await self.person(user_id)
        if found is None:
            raise Refused("That person is not here.", status=404)
        return found

    # ── making and changing people ───────────────────────────────
    @staticmethod
    def check(email: str | None = None, name: str | None = None, password: str | None = None) -> None:
        if email is not None and not EMAIL.match(email.strip()):
            raise Refused("That is not an email address.", status=422)
        if name is not None and not name.strip():
            raise Refused("A name is needed.", status=422)
        if password is not None and len(password) < MIN_PASSWORD:
            raise Refused(f"Use a password of at least {MIN_PASSWORD} characters.", status=422)

    async def _known_roles(self, roles: list[str]) -> None:
        for role_id in roles:
            if await self.roles.get(role_id) is None:
                raise Refused(f"Unknown role: {role_id}", status=422)

    async def create(self, email: str, name: str, password: str, roles: list[str]) -> Person:
        self.check(email, name, password)
        await self._known_roles(roles)
        if await self.users.by_email(email) is not None:
            raise Refused("Someone already uses that email.")
        user = await self.users.add(UserRow(id="u_" + pysecrets.token_hex(6), email=email.strip(),
                                            name=name.strip(), password_hash=await _hash(password)))
        await self.users.set_roles(user.id, roles)
        return await self.need(user.id)

    async def update(self, user_id: str, *, actor: Person, name: str | None = None,
                     status: str | None = None, roles: list[str] | None = None) -> Person:
        row = await self.users.require(user_id)
        person = await self.need(user_id)
        self.check(name=name)
        if status == "disabled" and user_id == actor.id:
            raise Refused("You cannot disable your own account.")
        if roles is not None:
            await self._known_roles(roles)
            if ("owner" in roles) != ("owner" in person.roles) and "owner" not in actor.roles:
                raise Refused("Only an Owner can grant or remove the Owner role.", status=403)
        losing_owner = "owner" in person.roles and (
            (roles is not None and "owner" not in roles) or status == "disabled")
        if losing_owner and await self._other_active_owners(user_id) == 0:
            raise Refused("This is the last active Owner. Make someone else an Owner first.")

        if name is not None:
            row.name = name.strip()
        if status is not None:
            row.status = status
            if status == "disabled":
                await self.sessions.end_all_for(user_id)   # a disabled account is signed out everywhere
        if roles is not None:
            await self.users.set_roles(user_id, roles)
        await self.session.flush()
        return await self.need(user_id)

    async def _other_active_owners(self, except_user: str) -> int:
        """The guard behind "this is the last Owner": one count, not a query per person."""
        return await self.users.count_active_with_role("owner", except_user=except_user)

    async def set_password(self, user_id: str, password: str, *, keep: str | None = None) -> None:
        """A new password ends every other session that person has open. `keep` spares this one."""
        self.check(password=password)
        row = await self.users.require(user_id)
        row.password_hash = await _hash(password)
        await self.session.flush()
        for open_session in await self.sessions.open_for(user_id):
            if not keep or open_session.token_hash != token_hash(keep):
                await self.sessions.end(open_session.token_hash)

    async def verify(self, user_id: str, password: str) -> bool:
        row = await self.users.get(user_id)
        return bool(row) and await _verify(password, row.password_hash)

    # ── signing in ───────────────────────────────────────────────
    async def _locked(self, email: str) -> bool:
        """Five wrong tries in five minutes locks that address until the last one is old enough."""
        since = utcnow() - WINDOW
        stmt = (select(func.count(), func.max(LoginAttempt.at))
                .where(LoginAttempt.email == email, LoginAttempt.ok.is_(False), LoginAttempt.at > since))
        tries, latest = (await self.session.execute(stmt)).one()
        if int(tries) < self.config.login_attempts or latest is None:
            return False
        return (utcnow() - latest).total_seconds() < self.config.lockout_seconds

    async def _attempt(self, email: str, ok: bool, ip: str = "") -> None:
        self.session.add(LoginAttempt(email=email.strip(), ok=ok, ip=ip))
        await self.session.flush()

    async def login(self, email: str, password: str, *, user_agent: str = "", ip: str = "") -> tuple[Person, str]:
        if await self._locked(email):
            raise Refused(f"Too many attempts. Wait {self.config.lockout_seconds} seconds and try again.",
                          status=429)
        row = await self.users.by_email(email)
        if row is None or not await _verify(password, row.password_hash):
            await self._attempt(email, ok=False, ip=ip)
            raise Refused("Wrong email or password.", status=401)
        if row.status != "active":
            await self._attempt(email, ok=False, ip=ip)
            raise Refused("This account is disabled. Ask an admin to turn it back on.", status=403)
        await self._attempt(email, ok=True, ip=ip)
        return await self.need(row.id), await self.start_session(row.id, user_agent)

    async def start_session(self, user_id: str, user_agent: str = "") -> str:
        """A new session. Only the token's hash is stored, so the database itself cannot sign in as
        anyone; the token exists in the browser alone."""
        token = new_token()
        row = await self.users.require(user_id)
        self.session.add(SessionRow(token_hash=token_hash(token), user_id=user_id,
                                    expires_at=utcnow() + timedelta(days=self.config.session_days),
                                    user_agent=user_agent[:200]))
        await self.users.touch_login(row)
        await self.session.flush()
        return token

    async def whoami(self, token: str) -> Person | None:
        """The person behind a token, or nobody — expired, signed out elsewhere, or disabled."""
        found = await self.sessions.by_token(token_hash(token))
        if found is None:
            return None
        person = await self.person(found.user_id)
        return person if person and person.status == "active" else None

    async def logout(self, token: str) -> None:
        await self.sessions.end(token_hash(token))
