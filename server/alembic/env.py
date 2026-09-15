"""Alembic, wired to the application's own settings and models.

The database URL is never written in alembic.ini: it comes from `app.settings`, so a migration always
runs against the database the API itself would open, and no password is committed. Autogenerate reads
`Base.metadata`, so the schema in `app/models/` is the single source of truth — a drift between code
and database shows up as a migration, not as a surprise at runtime.
"""
from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.base import Base  # noqa: E402
from app.models import *  # noqa: E402, F401, F403  — importing them fills Base.metadata
from app.settings import settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A caller that has already chosen a database keeps it — the test fixtures set this option before
# running `upgrade`, and overwriting it here pointed every test run at the *live* database instead.
# Only when nobody has chosen does this fall back to the application's own URL.
if not (config.get_main_option("sqlalchemy.url", "") or "").strip():
    config.set_main_option("sqlalchemy.url", settings().database_url)
target_metadata = Base.metadata

# Tables the old SQLite-era runner made. They are not in the models, and autogenerate must not try to
# drop them while both schemas exist side by side.
LEGACY = {"schema_migrations", "sqlite_sequence"}


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ANN201
    return not (type_ == "table" and name in LEGACY)


def configure(**kw) -> None:  # noqa: ANN003
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,              # a column whose type changed becomes a migration, not a silent drift
        compare_server_default=True,
        render_as_batch=False,
        **kw,
    )


def run_migrations_offline() -> None:
    configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True,
              dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(config.get_section(config.config_ini_section, {}),
                                           prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
