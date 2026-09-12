"""Accounts, sign-in and the guards around them.

The rules that used to be scattered through route handlers are here, each with a name: the last
active Owner cannot be removed, nobody disables their own account, only an Owner grants the Owner
role, a new password ends every other session. A route's job is now to say who is asking and turn a
refusal into a status code.

Nothing in this file knows what HTTP is, and nothing in it writes SQL.
"""
from __future__ import annotations

import re
import secrets as pysecrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import LoginAttempt, Session as SessionRow, User as UserRow
from ..repositories import AuditRepository, RoleRepository, SessionRepository, UserRepository
from ..settings import Settings, settings as get_settings
from .errors import Denied, Refused
from .security import hash_password, new_token, token_hash, verify_password

MIN_PASSWORD = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Wrong attempts older than this are forgotten, so a slow trickle never adds up to a lock-out.
WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class Person:
    """Who is asking, as the rest of the app sees them. The same shape the screens already read."""

    id: str
    email: str
    name: str
    status: str
    roles: tuple[str, ...]
    permissions: frozenset[str]

    def can(self, *perms: str) -> bool:
        return all(p in self.permissions for p in perms)

    def must(self, permission: str, what: str = "") -> None:
        if permission not in self.permissions:
            raise Denied(permission, what)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name, "status": self.status,
                "roles": list(self.roles), "permissions": sorted(self.permissions)}


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
                      frozenset(await self.users.permissions(user_id)))

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
                                            name=name.strip(), password_hash=hash_password(password)))
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
        row.password_hash = hash_password(password)
        await self.session.flush()
        for open_session in await self.sessions.open_for(user_id):
            if not keep or open_session.token_hash != token_hash(keep):
                await self.sessions.end(open_session.token_hash)

    async def verify(self, user_id: str, password: str) -> bool:
        row = await self.users.get(user_id)
        return bool(row) and verify_password(password, row.password_hash)

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
        if row is None or not verify_password(password, row.password_hash):
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
