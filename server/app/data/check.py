"""Is the database there, and is its schema the one this code expects?

Asked before the API starts, because the three ways this goes wrong on a new machine look identical
in a uvicorn log — no server, no database, no migrations — and each has a different one-line fix.

    uv run python -m app.data.check      # prints what is wrong, exits 1; silent and 0 when it is fine
"""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from ..settings import settings


def diagnose() -> str | None:
    """The one line an operator needs, or None when there is nothing to say."""
    cfg = settings()
    name = cfg.database_url.rsplit("/", 1)[-1]
    try:
        engine = create_engine(cfg.blocking_database_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            at = conn.execute(text(
                "SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except SQLAlchemyError as e:
        said = str(e.__cause__ or e).strip()
        detail = said.splitlines()[0][:120]           # shortened for the message, matched on in full
        if "does not exist" in said and name in said:
            return (f"there is no database called {name} yet.\n"
                    f"    uv run python scripts/bootstrap-db.py && cd server && uv run alembic upgrade head")
        if "alembic_version" in detail:
            return f"{name} has no schema yet.\n    cd server && uv run alembic upgrade head"
        return (f"cannot reach Postgres for {name}: {detail}\n"
                f"    is it running?  then: uv run python scripts/bootstrap-db.py")
    if at is None:
        return f"{name} has no schema yet.\n    cd server && uv run alembic upgrade head"
    return None


def main() -> None:
    problem = diagnose()
    if problem:
        print(problem)
        sys.exit(1)


if __name__ == "__main__":
    main()
