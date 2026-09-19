#!/usr/bin/env python
"""Carry a running NeuroCode from the old SQLite file into Postgres.

The old store kept most things as JSON documents and identity as real columns. The new one is
relational throughout — and `app.data.loader.load_seed` knows how to turn exactly those documents into
rows. So the documents go through the loader, and only identity — accounts, roles, teams, sessions,
the audit log — is copied across by hand here. The agent roster is not copied at all: it is the
product's catalogue, and is written from there.

The old store also wrote a sample workspace into itself on every start, so an old file almost always
holds it beside the real work — five projects, and the facts, gates, conflicts, MCP servers and
activity that came with them. Those rows are named below, one by one, and skipped: imported, they would sit in
Postgres looking exactly like work somebody did. The run says how many it left behind.

Passwords come over untouched: both stacks store `scrypt$n$r$p$salt$digest`, parameters and all, so
everyone signs in afterwards with the password they already have.

    uv run python scripts/import-sqlite.py --dry-run      # say what would move
    uv run python scripts/import-sqlite.py                # move it

It refuses to run against a Postgres database that already holds accounts, unless you say --replace.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from sqlalchemy import delete, select, text  # noqa: E402

from app.data.engine import Database  # noqa: E402
from app.data.loader import load_seed, sync_agents, sync_roles  # noqa: E402
from app.models import (  # noqa: E402
    AuditEntry,
    Role,
    RolePermission,
    Session,
    Team,
    TeamMember,
    User,
    UserRole,
    Workspace,
)
from app.settings import settings  # noqa: E402

#: SQLite table → the key `load_seed` reads it under. Everything else in that file is either derived
#: (the FTS shadow tables), identity (copied below), the catalogue (the agents), or empty by design
#: (the runtime's own tables, which hold worktree paths that mean nothing on another machine).
DOCUMENTS = {
    "projects": "projects",
    "tasks": "tasks",
    "plans": "plans",
    "approvals": "approvals",
    "memory": "memory",
    "conflicts": "conflicts",
    "mcp": "mcp",
    "activity": "activity",
}
#: Tables the old file may hold that this import does not carry, and why — said in every run, so a
#: row that is not coming across is never a surprise afterwards.
NOT_CARRIED = {
    "decisions": "final decisions are made again on the new stack",
    "prefs": "screen settings start from their defaults",
    "brainstorms": "a brief is rewritten by a model on the new stack",
}

# ── the sample the old store seeded itself with ─────────────────
#: The sample projects. Everything that belongs to one — tasks, plans, gates, facts, activity — is
#: sample too, and is skipped with it.
SAMPLE_PROJECTS = frozenset({"erp", "hims", "taxi", "fifa", "aios"})
#: The sample facts that belong to no project.
SAMPLE_FACTS = frozenset({"m001", "m008", "m015", "m022", "m029", "m036", "m043", "m902", "m909", "m916"})
SAMPLE_APPROVALS = frozenset({"ap1", "ap2", "ap3", "ap4", "ap5", "ap6"})
SAMPLE_CONFLICTS = frozenset({"cf-1", "cf-2", "cf-3"})
SAMPLE_MCP = frozenset({"filesystem", "git", "github", "sqlserver", "postgres", "playwright", "docker", "browser",
                        "memory", "qdrant", "jira", "linear", "sentry", "slack", "fetch", "time"})


def at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def documents(db: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Every JSON document in the old file, in the shape the loader understands."""
    out: dict[str, list[dict[str, Any]]] = {}
    for table, key in DOCUMENTS.items():
        try:
            order = "ORDER BY seq" if table == "activity" else ""
            rows = db.execute(f"SELECT doc FROM {table} {order}").fetchall()
        except sqlite3.OperationalError:
            continue                                   # a table this old file never had
        out[key] = [json.loads(r[0]) for r in rows]
    return out


def without_sample(docs: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """The documents with the old store's sample taken out, and how many of each were taken.

    A row is sample when it is named in the lists above or belongs to a sample project. A conflict is
    sample when either fact it names is — it could not be written without them anyway.
    """
    def sample(key: str, row: dict[str, Any]) -> bool:
        if row.get("projectId") in SAMPLE_PROJECTS:
            return True
        return row.get("id") in {"projects": SAMPLE_PROJECTS, "memory": SAMPLE_FACTS,
                                 "approvals": SAMPLE_APPROVALS, "mcp": SAMPLE_MCP}.get(key, frozenset())

    kept: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, int] = {}
    for key, rows in docs.items():
        kept[key] = [row for row in rows if not sample(key, row)]
    gone_facts = {row.get("id") for row in docs.get("memory", [])} - {row.get("id") for row in kept.get("memory", [])}
    if "conflicts" in kept:
        kept["conflicts"] = [row for row in kept["conflicts"] if row.get("id") not in SAMPLE_CONFLICTS
                             and row.get("a") not in gone_facts and row.get("b") not in gone_facts]
    for key, rows in docs.items():
        if len(rows) != len(kept[key]):
            skipped[key] = len(rows) - len(kept[key])
    return kept, skipped


async def identity(db: sqlite3.Connection, session: Any) -> dict[str, int]:
    """Accounts and everything that grants them anything, copied column for column."""
    moved: dict[str, int] = {}

    # The audit log refuses UPDATE and DELETE — a trigger enforces it, which is the point of the
    # table. Replacing a workspace wholesale is the one thing that legitimately has to get past it,
    # so the rule is lifted deliberately, by name, and put back inside the same transaction: if this
    # import fails, the trigger is restored by the rollback along with everything else.
    await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    try:
        for table, model in (("sessions", Session), ("user_roles", UserRole), ("team_members", TeamMember),
                             ("audit_log", AuditEntry), ("role_permissions", RolePermission),
                             ("users", User), ("teams", Team), ("roles", Role)):
            await session.execute(delete(model))
        await session.flush()
    finally:
        await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))

    row = db.execute("SELECT name, created_at FROM workspace LIMIT 1").fetchone()
    if row:
        found = await session.get(Workspace, 1)
        if found is None:
            session.add(Workspace(id=1, name=row["name"]))
        else:
            found.name = row["name"]
        moved["workspace"] = 1

    for r in db.execute("SELECT * FROM roles"):
        session.add(Role(id=r["id"], name=r["name"], description=r["description"] or "",
                         builtin=bool(r["builtin"]), permissions=[]))
    await session.flush()
    moved["roles"] = int(db.execute("SELECT COUNT(*) FROM roles").fetchone()[0])

    for r in db.execute("SELECT * FROM role_permissions"):
        session.add(RolePermission(role_id=r["role_id"], permission=r["permission"]))
    moved["permissions"] = int(db.execute("SELECT COUNT(*) FROM role_permissions").fetchone()[0])

    for r in db.execute("SELECT * FROM users"):
        session.add(User(id=r["id"], email=r["email"], name=r["name"],
                         password_hash=r["password_hash"], status=r["status"],
                         created_at=at(r["created_at"]) or datetime.now(),
                         last_login_at=at(r["last_login_at"]), roles=[]))
    await session.flush()
    moved["users"] = int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    for r in db.execute("SELECT * FROM user_roles"):
        session.add(UserRole(user_id=r["user_id"], role_id=r["role_id"]))

    try:
        for r in db.execute("SELECT * FROM teams"):
            session.add(Team(id=r["id"], name=r["name"], description=r["description"] or "",
                             members=[]))
        await session.flush()
        for r in db.execute("SELECT * FROM team_members"):
            session.add(TeamMember(team_id=r["team_id"], user_id=r["user_id"]))
        moved["teams"] = int(db.execute("SELECT COUNT(*) FROM teams").fetchone()[0])
    except sqlite3.OperationalError:
        pass

    # Sessions come too, so nobody is signed out by the migration. The token itself was never stored
    # — only its hash — so the cookie a browser is already holding keeps working.
    kept = 0
    for r in db.execute("SELECT * FROM sessions"):
        expires = at(r["expires_at"])
        if expires and expires < datetime.now():
            continue
        session.add(Session(token_hash=r["token_hash"], user_id=r["user_id"],
                            created_at=at(r["created_at"]) or datetime.now(), expires_at=expires,
                            user_agent=r["user_agent"] or ""))
        kept += 1
    moved["sessions"] = kept

    for r in db.execute("SELECT * FROM audit_log ORDER BY seq"):
        # `detail` was a JSON string in a TEXT column and is a JSONB column now, so it is parsed
        # rather than carried across as text — otherwise every entry would arrive quoted.
        try:
            detail = json.loads(r["detail"] or "{}")
        except (TypeError, ValueError):
            detail = {"note": r["detail"]}
        session.add(AuditEntry(
            at=at(r["at"]) or datetime.now(), user_id=r["user_id"], action=r["action"],
            target=r["target"] or "", detail=detail, ip=r["ip"] or ""))
    moved["audit"] = int(db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
    return moved


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, default=None,
                        help="the SQLite file (default: the one in settings)")
    parser.add_argument("--dry-run", action="store_true", help="say what would move, write nothing")
    parser.add_argument("--replace", action="store_true",
                        help="overwrite a Postgres database that already holds accounts")
    args = parser.parse_args()

    cfg = settings()
    source = args.source or cfg.legacy_sqlite_path
    if not source.exists():
        raise SystemExit(f"No SQLite file at {source}")

    old = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    docs, skipped = without_sample(documents(old))

    print(f"from {source}")
    for key, rows in sorted(docs.items()):
        if rows:
            print(f"  {len(rows):>5}  {key}")
    for table in ("users", "roles", "role_permissions", "sessions", "audit_log"):
        try:
            n = old.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            continue
        print(f"  {n:>5}  {table}")

    if skipped:
        print(f"\nskipped {sum(skipped.values())} sample row(s) the old store seeded itself with:")
        for key, n in sorted(skipped.items()):
            print(f"  {n:>5}  {key}")
    for table, why in NOT_CARRIED.items():
        try:
            n = old.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            continue                                   # a table this old file never had
        if n:
            print(f"  not carried: {n} {table} — {why}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    db = Database(config=cfg)
    try:
        async with db.session() as session:
            standing = (await session.execute(select(User.id))).scalars().all()
            if standing and not args.replace:
                raise SystemExit(
                    f"\n{cfg.database_url.rsplit('@', 1)[-1]} already holds {len(standing)} account(s). "
                    "Pass --replace to overwrite it, or point --from somewhere else.")

            written = await load_seed(session, docs)
            moved = await identity(old, session)
            # The built-in roles and the roster are defined by the application, not by whatever the old
            # file happened to contain, so they are reconciled after the copy rather than trusted from it.
            await sync_roles(session)
            await sync_agents(session)

        print("\nwritten to Postgres:")
        for key, n in sorted({**written, **moved}.items()):
            print(f"  {n:>5}  {key}")
        if skipped:
            print(f"  ({sum(skipped.values())} sample row(s) skipped)")
        print(f"\nDone. {source.name} is no longer read by anything; keep it until you are sure.")
    finally:
        await db.close()
        old.close()


if __name__ == "__main__":
    asyncio.run(main())
