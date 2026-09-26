"""Fixtures for the Postgres layer.

Every test runs inside a transaction that is rolled back when it ends, so tests share one database
without sharing state and without truncating tables between them. The schema is brought to head once
per session, from the same migrations production uses — a test that passes against a hand-made schema
proves nothing.

The older tests build their own SQLite app and are untouched by this file: nothing here is named
`app` or `client`.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

# Before anything imports the app: runs make worktrees and onboarding clones under these two folders,
# and a test run must never leave its projects in the server's own `.worktrees` / `.repos`, where
# DevOps counts them as real. setdefault, so a run that names its own folders keeps them.
_SCRATCH = tempfile.mkdtemp(prefix="neurocode-tests-")
os.environ.setdefault("NEUROCODE_WORKTREES_DIR", os.path.join(_SCRATCH, "worktrees"))
os.environ.setdefault("NEUROCODE_REPOS_DIR", os.path.join(_SCRATCH, "repos"))
# Routines fire only when a test fires them: an app whose lifespan runs must not start the scheduler.
os.environ.setdefault("NEUROCODE_SCHEDULER", "false")
os.environ.setdefault("NEUROCODE_CHANNELS", "false")
# A model running on this machine (Ollama) must never answer a test: a test that expects "no model" would
# find one, and one that expects a stub would wait on real generation. Port 9 answers nothing.
os.environ.setdefault("NEUROCODE_OLLAMA_URL", "http://127.0.0.1:9")
# An app a test builds without a database of its own (`create_api(db=None)`) opens the default one, and a
# route that takes the database straight from the app state reads it. That default must be the test database:
# on a developer's machine it is their real workspace, and on CI it was never migrated.
os.environ["NEUROCODE_DATABASE_URL"] = os.environ.get(
    "NEUROCODE_TEST_DATABASE_URL", "postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode_test")
atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)

import pytest  # noqa: E402 — the environment above must be in place first
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.data.loader import sync_agents, sync_roles  # noqa: E402
from app.settings import Settings  # noqa: E402
from tests.fixtures.workspace import load_workspace  # noqa: E402

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
async def catalogued(session: AsyncSession) -> AsyncSession:
    """What a brand-new workspace holds — the built-in roles and the agent roster — and nothing else."""
    await sync_roles(session)
    await sync_agents(session)
    await session.flush()
    return session


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    """The tests' own workspace (`tests/fixtures/workspace.json`) and the catalogue, inside this
    test's transaction."""
    await load_workspace(session)
    return session
