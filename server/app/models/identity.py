"""Who is here, what they may do, and what they did.

This was already relational in SQLite; Postgres lets it say what it means. Emails are `citext`, so
two accounts cannot differ by a capital letter. Statuses are real enum types, not free text with a
CHECK. Times are `timestamptz`, so "when" is unambiguous wherever the server runs. The audit log is
append-only, enforced by a trigger rather than by everyone remembering.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..data.base import Base, Mixin

UserStatus = Enum("active", "disabled", name="user_status", create_type=True)


class Workspace(Base, Mixin):
    """One row. The workspace this API serves."""

    __tablename__ = "workspace"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class User(Base, Mixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(UserStatus, nullable=False, server_default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[UserRole]] = relationship(back_populates="user", cascade="all, delete-orphan",
                                                 lazy="selectin")
    teams: Mapped[list[TeamMember]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def active(self) -> bool:
        return self.status == "active"


class Role(Base, Mixin):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: Built-in roles are re-synced from the permission catalogue on every start.
    builtin: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    #: Where it sits on the access screen. The built-in roles are a ladder — Owner, Admin, Approver,
    #: Engineer, Viewer — and that order is the catalogue's, not the alphabet's.
    rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")

    permissions: Mapped[list[RolePermission]] = relationship(back_populates="role",
                                                             cascade="all, delete-orphan", lazy="selectin")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    #: A permission id from the catalogue in app/data/catalogue.json, e.g. `runs:merge`.
    permission: Mapped[str] = mapped_column(String(60), primary_key=True)

    role: Mapped[Role] = relationship(back_populates="permissions")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # RESTRICT: a role still worn by someone cannot be deleted out from under them.
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True)

    user: Mapped[User] = relationship(back_populates="roles")
    role: Mapped[Role] = relationship(lazy="selectin")


class Team(Base, Mixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    members: Mapped[list[TeamMember]] = relationship(back_populates="team", cascade="all, delete-orphan",
                                                     lazy="selectin")


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="teams")


class Session(Base):
    """A sign-in. Only the SHA-256 of the token is kept; the token itself exists in the browser alone."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id_expires_at", "user_id", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    user: Mapped[User] = relationship(lazy="joined")


class AuditEntry(Base):
    """Security's record: sign-ins, and every change to access, keys or settings. Append-only."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_action_at", "action", "at"),)

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False,
                                         index=True)
    # The record outlives the account: deleting a user must not erase what they did.
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")


class LoginAttempt(Base):
    """Every sign-in tried, right or wrong.

    The lock-out used to live in a dictionary in the process, which meant it forgot everything on a
    restart and protected nothing at all once there were two workers. In a table it is simply true.
    """

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_email_at", "email", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ok: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")


class Setting(Base, Mixin):
    """Workspace settings: the AI routing choice, per-lane overrides, per-project answers."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
