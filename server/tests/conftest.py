"""Fixtures for the Postgres layer.

Every test runs inside a transaction that is rolled back when it ends, so tests share one database
without sharing state and without truncating tables between them. The schema is brought to head once
per session, from the same migrations production uses — a test that passes against a hand-made schema
proves nothing.

The older tests build their own SQLite app and are untouched by this file: nothing here is named
`app` or `client`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.data.loader import load_seed, sync_roles
from app.settings import Settings

SERVER_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def schema(settings: Settings) -> Iterator[str]:
    """The test database, at head. Migrated once, by Alembic, exactly as a real one would be."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.test_database_url)
    command.upgrade(config, "head")
    yield settings.test_database_url


@pytest_asyncio.fixture
async def engine(schema: str):
    """Made and disposed per test. A pooled connection belongs to the event loop that opened it, and
    every test gets a loop of its own — so an engine that outlives one test hands the next a socket
    the loop it is running on knows nothing about."""
    engine = create_async_engine(schema, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    """One test, one transaction, rolled back at the end — so no test can see another's writes."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
        async with maker() as session:
            yield session
        await transaction.rollback()


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    """The sample workspace and the built-in roles, inside this test's transaction."""
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
    return session
