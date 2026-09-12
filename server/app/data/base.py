"""What every table shares: how its constraints are named, and how it records time.

The naming convention matters more than it looks. Without it, Alembic invents constraint names, and
the migration that drops a constraint on one machine cannot find it on another. With it, every index,
foreign key and check has a name derived from its columns, so a schema diff is readable and a
migration is portable.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Now, with its timezone said out loud. Every timestamp in this database is UTC."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENTION)
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: JSONB}
    # Fetch server-side defaults and `onupdate` values with RETURNING, in the statement that caused
    # them. Without this, an updated row leaves `updated_at` expired, and the next read of it is a
    # *synchronous* refresh — which in async code raises MissingGreenlet instead of loading anything.
    # Postgres supports RETURNING, so this costs no extra round trip.
    __mapper_args__ = {"eager_defaults": True}


class Mixin:
    """Created and updated, kept by the database itself so a forgotten `updated_at` cannot happen."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)
