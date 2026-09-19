"""One engine, one way to get a session, one way to close it.

The old store held a single SQLite connection behind a lock, so every writer queued behind every
other. This is a pooled async engine: readers run at the same time, a write holds a transaction only
as long as its unit of work, and a request that fails rolls back everything it did rather than
leaving half of it behind.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..settings import Settings, settings
from . import changes  # noqa: F401  — importing it is what registers the change listeners

log = logging.getLogger(__name__)

#: The zone every connection this application opens works in. It decides what a *day* is wherever the
#: database cuts time into days — `date_trunc('day', at)` for the spending chart, "today" for a lane's
#: free allowance — and the screens count days in UTC. Left to the server's default the database
#: answered in its own zone (Asia/Kolkata on the machine it was built on): for five and a half hours
#: every night today's calls were filed under a date the chart did not draw, and every day was off by
#: one against it. Stored times are unaffected; `timestamptz` is an instant whatever the zone.
TIMEZONE = "UTC"


def utc_connect_args(url: str) -> dict[str, Any]:
    """What to hand the driver so a new connection's session zone is `TIMEZONE`, for the async engine,
    the gateway's blocking pool and Alembic alike: asyncpg takes server settings, libpq (psycopg) takes
    an options string."""
    if make_url(url).get_driver_name() == "asyncpg":
        return {"server_settings": {"timezone": TIMEZONE}}
    return {"options": f"-c timezone={TIMEZONE}"}


class Database:
    """The engine and its session factory, held by the app and closed with it."""

    def __init__(self, url: str | None = None, config: Settings | None = None,
                 bus: Any | None = None) -> None:
        cfg = config or settings()
        self.url = url or cfg.database_url
        #: Carried on every session this engine opens, so whatever writes a row can also announce it
        #: without being handed a bus through five layers of constructor. None simply never publishes.
        self.bus = bus
        self.engine: AsyncEngine = create_async_engine(
            self.url,
            echo=cfg.echo_sql,
            pool_size=cfg.pool_size,
            max_overflow=cfg.pool_overflow,
            pool_recycle=cfg.pool_recycle_seconds,
            pool_pre_ping=True,          # a laptop that slept hands back live connections, not dead ones
            connect_args=utc_connect_args(self.url),
            future=True,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A unit of work: everything inside commits together, or none of it does."""
        async with self.sessions() as session:
            session.info["bus"] = self.bus
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        """A session that never writes. No commit, so a read cannot leave a transaction open."""
        async with self.sessions() as session:
            session.info["bus"] = self.bus
            yield session

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:                       # the caller reports it; liveness must not raise
            log.warning("database unreachable: %s", e)
            return False

    async def version(self) -> str:
        async with self.engine.connect() as conn:
            row = await conn.execute(text("SHOW server_version"))
            return str(row.scalar_one())

    async def close(self) -> None:
        await self.engine.dispose()


@asynccontextmanager
async def session_scope(db: Database) -> AsyncIterator[AsyncSession]:
    """For a background task, which has no request to hang a session on."""
    async with db.session() as session:
        yield session
