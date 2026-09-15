#!/usr/bin/env python
"""Make the databases this project needs, with the extensions only a superuser can enable.

Run once on a new machine. Everything after this — the schema, the seed, the tests — is Alembic's
job and needs no special rights, which is the point: the application's own role stays ordinary.

    uv run python scripts/bootstrap-db.py              # the live database and the test one
    uv run python scripts/bootstrap-db.py --workers 5  # plus a private test database per worker
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from app.settings import settings  # noqa: E402

EXTENSIONS = ("citext", "vector", "pg_trgm")


def plain(url: str) -> str:
    """A libpq URL: which driver SQLAlchemy meant to use is not psycopg's business."""
    return url.replace("+asyncpg", "").replace("+psycopg", "")


def superuser_dsn(sample: str) -> str:
    """The same server, as somebody who may CREATE DATABASE. Tried in the order that usually works on
    a laptop: the OS user — which is what Homebrew's Postgres makes a superuser — and then `postgres`."""
    host = urlsplit(plain(sample))
    where = f"{host.hostname or '127.0.0.1'}:{host.port or 5432}"
    for candidate in (getpass.getuser(), "postgres"):
        dsn = f"postgresql://{candidate}@{where}/postgres"
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                if conn.execute("SELECT usesuper FROM pg_user WHERE usename = current_user").fetchone()[0]:
                    return dsn
        except psycopg.Error:
            continue
    raise SystemExit(
        "No superuser connection found on this server.\n"
        "Connect as one and run, for each database:  CREATE EXTENSION vector;\n"
        "citext and pg_trgm are trusted and the migration enables those itself.")


def ensure(admin: psycopg.Connection, name: str, owner: str, at: str) -> None:
    if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(name), sql.Identifier(owner)))
        print(f"  created  {name}")
    with psycopg.connect(f"{at}/{name}", autocommit=True) as conn:
        for extension in EXTENSIONS:
            conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension)))
    print(f"  ready    {name}  ({', '.join(EXTENSIONS)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=0, metavar="N",
                        help="also make neurocode_test_1..N, so parallel test runs cannot collide")
    parser.add_argument("--reset", metavar="NAME", action="append", default=[],
                        help="empty this database first — every table, and the migration history with "
                             "them. The end-to-end run uses it to start from nothing each time.")
    args = parser.parse_args()

    cfg = settings()
    live, test = plain(cfg.database_url), plain(cfg.test_database_url)
    owner = urlsplit(live).username or "neurocode"
    admin_dsn = superuser_dsn(live)
    at = admin_dsn.rsplit("/", 1)[0]

    names = [urlsplit(live).path.lstrip("/"), urlsplit(test).path.lstrip("/")]
    names += [f"neurocode_test_{i}" for i in range(1, args.workers + 1)]
    names += args.reset

    print(f"as {urlsplit(admin_dsn).username} on {urlsplit(admin_dsn).hostname}:{urlsplit(admin_dsn).port or 5432}")
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        # So the owner can make its own databases from here on and never needs this script again.
        admin.execute(sql.SQL("ALTER ROLE {} CREATEDB").format(sql.Identifier(owner)))
        for name in dict.fromkeys(names):
            ensure(admin, name, owner, at)
        for name in args.reset:
            # Dropping the schema takes the tables, the enum types and alembic_version together, which
            # is what makes the next `upgrade head` a genuine first migration rather than a no-op.
            with psycopg.connect(f"{at}/{name}", autocommit=True) as conn:
                conn.execute("DROP SCHEMA public CASCADE")
                conn.execute("CREATE SCHEMA public")
                conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(owner)))
                for extension in EXTENSIONS:
                    conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension)))
            print(f"  emptied  {name}")
    print("\nDone.  Next:  cd server && uv run alembic upgrade head")


if __name__ == "__main__":
    main()
