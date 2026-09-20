"""The database chores an administrator can ask for: a picture of it, a copy of it, a check of it,
the housekeeping Postgres wants now and then, and emptying the workspace.

All of it was written for SQLite, where the whole database is one file: its size was that file's
size, a check was `PRAGMA integrity_check`, and compacting it was `VACUUM` on the single connection
everything shared. None of those answers survive the move, so none of them are kept. What is kept is
the *question* each one answered, asked again of Postgres — the catalogue for size and rows,
`pg_stat_user_tables` for what has gone stale, Alembic for whether the schema is where it should be,
and every foreign key walked to see that it still points at a row.

Two things here are not SQL and therefore block: `pg_dump`, which is another program, and reading the
backup directory, which is a disk. Both go to a worker thread. Nothing here commits — the unit of
work belongs to the request — except `pg_dump` and `VACUUM`, which run on connections of their own
because neither can live inside a transaction.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from ..models import (
    ActivityEvent,
    Agent,
    Approval,
    Blueprint,
    Brainstorm,
    Chunk,
    CustomAgent,
    Decision,
    EvalSuite,
    McpServer,
    MemoryConflict,
    MemoryFact,
    Plan,
    Pref,
    Project,
    Run,
    Setting,
    Task,
    TasteRule,
    TasteSignal,
    WorkflowDefinition,
)
from ..data.base import utcnow
from ..data.changes import announce
from ..data.engine import Database
from ..repositories.identity import AuditRepository
from ..settings import SERVER_DIR, Settings, settings as get_settings
from .errors import Refused

log = logging.getLogger(__name__)

#: The newest this many copies are kept; older ones are removed as a new one is made.
KEEP_BACKUPS = 20
#: Ceilings, so no answer here is ever "everything". A workspace has forty-odd tables; these are room.
MAX_TABLES = 200
MAX_INDEXES = 500
MAX_NAMES = 20
#: A table is called bloated when this much of it is dead rows — and only once it is big enough for
#: the ratio to mean anything. Five dead rows out of eight is nothing; five thousand is a vacuum.
BLOAT_RATIO = 0.4
BLOAT_FLOOR = 1_000
#: How long `REINDEX` may wait for its lock. An administrator pressing a button must never be able to
#: queue the whole application behind an ACCESS EXCLUSIVE lock; a busy table is reported, not waited on.
LOCK_WAIT = "2s"
BACKUP_TIMEOUT = 600
#: The most DELETE statements one prune of one table will issue. At the default chunk that is two
#: million rows in a pass — a year of arrears — and then it stops and says so, rather than running for
#: an hour because a setting was changed from "keep everything" to ninety days on a very old workspace.
MAX_PRUNE_STATEMENTS = 400
#: Where the daily prune records the day it last ran, so a restart does not prune twice and two API
#: processes do not both prune.
PRUNED_KEY = "maintenance.pruned"

#: What a workspace holds, under the names the screens have always counted it by. The left-hand side
#: is the old document store's vocabulary; the right is where those rows live now.
COLLECTIONS: tuple[tuple[str, Any], ...] = (
    ("projects", Project), ("agents", Agent), ("tasks", Task), ("approvals", Approval),
    ("memory", MemoryFact), ("plans", Plan), ("conflicts", MemoryConflict), ("mcp", McpServer),
    ("prefs", Pref), ("decisions", Decision), ("brainstorms", Brainstorm), ("runs", Run),
    ("activity", ActivityEvent),
)


#: What emptying the workspace deletes, in this order. Projects go first, and every row a project owns
#: goes with it through its foreign key — tasks, plans, runs, the code index, sessions, research, and
#: the facts, chunks, workflows and suites filed under it, its taste signals and rules, and every plan's
#: comments with their plan. What is left are the rows that belong to no project — the workspace's own
#: taste among them, blueprints and the workspace's custom agents. Kept: accounts, sessions, personal access
#: tokens, roles, teams, the agent roster, keys, the workspace's settings, a person's saved blueprint
#: templates (their library, not work), the audit log, and the usage ledger, which is history rather than work.
EMPTIED: tuple[Any, ...] = (Project, MemoryFact, Chunk, ActivityEvent, Approval, Decision, Pref, Brainstorm,
                            McpServer, WorkflowDefinition, EvalSuite, TasteSignal, TasteRule, Blueprint,
                            CustomAgent)
#: Settings that belong to one project, keyed `<prefix><project id>`. They have no foreign key to the
#: project, so they do not go with it on their own — and a project id is only the last segment of its
#: repository's path, so a different repository onboarded later under the same name got the old one's
#: standing "allowed" and ran its test command without anyone being asked. Emptying the workspace is
#: the only thing that deletes a project, and it takes every one of these with it.
PER_PROJECT_SETTINGS: tuple[str, ...] = ("runtime.tests.",)


@dataclass(frozen=True, slots=True)
class History:
    """One table that only ever grows, and the setting that says how long it is kept.

    What is *not* here is as deliberate as what is. Sessions and their turns are a conversation someone
    can still read, not a log; a test failure and an eval result are findings, and a screen lists them.
    And the audit log cannot be pruned at all, by anyone, at any setting: the database itself refuses a
    DELETE on it, which is the point of an append-only record.
    """

    table: str
    column: str
    setting: str
    label: str
    note: str


HISTORIES: tuple[History, ...] = (
    History("run_logs", "at", "run_log_days", "Run output", "every line the agents' runs printed"),
    History("activity", "at", "activity_days", "Activity", "the workspace's story, as the feed shows it"),
    History("ai_calls", "at", "ledger_days", "Usage ledger", "every model call, and what it cost"),
    History("schedule_fires", "at", "fire_days", "Routine fires", "each time a routine fired, and what came of it"),
    History("memory_hits", "at", "recall_days", "Recalls", "each time a remembered fact was used"),
    History("login_attempts", "at", "login_attempt_days", "Sign-in attempts", "every sign-in tried, right or wrong"),
)


def _ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _q(name: str) -> str:
    """A catalogue name, quoted for use in a statement. Every name here comes from the catalogue
    itself, so this is about tables called "order" or "user", not about untrusted input."""
    return '"' + name.replace('"', '""') + '"'


def _why(error: DBAPIError) -> str:
    """What the driver actually said, first line only — "canceling statement due to lock timeout"."""
    said = str(getattr(error, "orig", None) or error).strip()
    return said.splitlines()[0][:200] if said else type(error).__name__


@lru_cache(maxsize=1)
def _revisions() -> tuple[tuple[str, str], ...]:
    """Every migration in order, oldest first, read from Alembic's own scripts.

    Alembic keeps only *where the database is* in `alembic_version`; the history itself is the files,
    which is why this reads them rather than the database. They cannot change while the process runs,
    so it is read once. Reading a directory blocks — callers hand this to a thread.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    return tuple((rev.revision, rev.doc) for rev in reversed(list(script.walk_revisions())))


def _describe(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"name": path.name, "bytes": stat.st_size,
            "at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds")}


#: Where PostgreSQL's client tools are usually installed when they are not on PATH: Homebrew on both
#: architectures (its versioned formulae are keg-only), Postgres.app, and Debian's layout.
PG_BIN_GLOBS = ("/opt/homebrew/opt/postgresql@*/bin", "/opt/homebrew/opt/postgresql/bin",
                "/usr/local/opt/postgresql@*/bin", "/usr/local/opt/postgresql/bin",
                "/Applications/Postgres.app/Contents/Versions/*/bin", "/usr/lib/postgresql/*/bin")


def _major(program: str) -> int | None:
    try:
        out = subprocess.run([program, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"(\d+)(?:\.\d+)?", out)
    return int(found.group(1)) if found else None


def find_pg_dump(server_major: int, configured: Path | None = None) -> str | None:
    """A pg_dump that can actually dump this server, or None.

    Two things make this more than `shutil.which`. The common Mac install does not put pg_dump on PATH
    at all — Homebrew's versioned PostgreSQL is keg-only — so a backup that only looked there could
    never be taken on exactly the machine this runs on. And pg_dump refuses a server newer than itself,
    so any old client that does happen to be on PATH is not good enough. The closest version that is
    new enough wins; a folder named in settings is tried before anything else.
    """
    candidates: list[str] = []
    if configured is not None:
        candidates.append(str(Path(configured).expanduser() / "pg_dump"))
    on_path = shutil.which("pg_dump")
    if on_path:
        candidates.append(on_path)
    for pattern in PG_BIN_GLOBS:
        candidates += sorted(str(Path(d) / "pg_dump") for d in glob.glob(pattern))

    usable: list[tuple[int, int, str]] = []
    for order, program in enumerate(dict.fromkeys(candidates)):
        if not os.access(program, os.X_OK):
            continue
        major = _major(program)
        if major is not None and major >= server_major:
            usable.append((major, order, program))
    return min(usable)[2] if usable else None


class NoBackupTool(Refused):
    """pg_dump is not on this machine. Different in kind from a backup that was tried and failed:
    one means there is no way to take a copy, the other means taking it went wrong — and only the
    first is a reason to go ahead with something irreversible anyway."""


class MaintenanceService:
    def __init__(self, session: AsyncSession, config: Settings | None = None) -> None:
        self.session = session
        self.config = config or get_settings()

    async def _scalar(self, sql: str, **params: Any) -> Any:
        return (await self.session.execute(text(sql), params)).scalar_one()

    # ── the picture ──────────────────────────────────────────────
    async def stats(self) -> dict[str, Any]:
        """Everything the Database screen shows: which server, how big, how many rows, how the indexes
        are, which migrations it has had, and when a copy was last taken."""
        about = (await self.session.execute(text(
            "SELECT current_setting('block_size')::int AS block, "
            "       current_setting('server_version') AS version, "
            "       current_setting('wal_level') AS wal_level, "
            "       pg_database_size(current_database()) AS bytes"))).mappings().one()
        tables, free_pages = await self.tables(about["block"])
        health = await self.indexes()
        where = self.config.database_url.replace("+asyncpg", "").rsplit("@", 1)[-1]
        return {
            "path": where, "engine": "PostgreSQL", "version": about["version"],
            "pageSize": about["block"], "pages": about["bytes"] // about["block"],
            "freePages": free_pages, "walLevel": about["wal_level"],
            "sizeBytes": about["bytes"], "walBytes": await self.wal_bytes(),
            "tables": tables, "indexes": health["count"], "indexHealth": health,
            "migrations": await self.migrations(),
            "backups": await asyncio.to_thread(self._backups),
            "backupDir": str(self.config.backups_dir),
        }

    async def tables(self, block: int) -> tuple[list[dict[str, Any]], int]:
        """Every table with its exact row count and what it occupies, and the blocks a vacuum could
        give back. The planner's `n_live_tup` is an estimate and this screen is read by someone
        deciding whether to act, so the rows are counted for real; the dead ones can only be estimated.
        """
        rows = (await self.session.execute(text(
            "SELECT schemaname, relname, pg_total_relation_size(relid) AS bytes, "
            "       GREATEST(n_live_tup, 0) AS live, GREATEST(n_dead_tup, 0) AS dead "
            "FROM pg_stat_user_tables ORDER BY relname LIMIT :cap"), {"cap": MAX_TABLES})).mappings().all()
        tables, free = [], 0.0
        for row in rows:
            name = f"{_q(row['schemaname'])}.{_q(row['relname'])}"
            counted = await self._scalar(f"SELECT count(*) FROM {name}")
            tables.append({"name": row["relname"], "rows": int(counted), "bytes": int(row["bytes"])})
            if (held := row["live"] + row["dead"]):
                free += row["bytes"] / block * (row["dead"] / held)
        return tables, round(free)

    async def wal_bytes(self) -> int:
        """The write-ahead log on disk. It belongs to the whole server, not to this database, and only
        a member of pg_monitor may look — so the privilege is asked about first rather than found out
        by a failing statement, which would take the request's transaction down with it."""
        if not await self._scalar("SELECT pg_has_role(current_user, 'pg_monitor', 'member')"):
            return 0
        return int(await self._scalar("SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()"))

    async def indexes(self) -> dict[str, Any]:
        """Index health: how many there are, how much they cost, and the two ways one can be wrong —
        left invalid by a failed concurrent build, or never once used since the statistics were reset.
        """
        rows = (await self.session.execute(text(
            "SELECT c.relname AS name, i.indisvalid AS valid, "
            "       pg_relation_size(i.indexrelid) AS bytes, coalesce(s.idx_scan, 0) AS scans "
            "FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.indexrelid "
            "WHERE n.nspname = current_schema() ORDER BY c.relname LIMIT :cap"),
            {"cap": MAX_INDEXES})).mappings().all()
        since = await self._scalar(
            "SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()")
        invalid = [r["name"] for r in rows if not r["valid"]]
        unused = [r["name"] for r in rows if r["scans"] == 0]
        # The names are capped for the screen; the counts are not, so "41 never used" stays true when
        # only twenty of them are named.
        return {
            "count": len(rows), "bytes": sum(int(r["bytes"]) for r in rows),
            "scans": sum(int(r["scans"]) for r in rows),
            "invalid": invalid[:MAX_NAMES], "invalidCount": len(invalid),
            "unused": unused[:MAX_NAMES], "unusedCount": len(unused),
            "statsSince": since.isoformat(timespec="seconds") if since else None,
        }

    async def migrations(self) -> list[dict[str, Any]]:
        """The migrations this database has actually had — the history up to where it stands, and no
        further. Alembic does not record *when* one ran, so `appliedAt` is honestly empty."""
        at = await self.revision()
        if not at:
            return []           # never migrated: an empty history is the truth, not a missing one
        applied: list[dict[str, Any]] = []
        for n, (revision, name) in enumerate(await asyncio.to_thread(_revisions), start=1):
            applied.append({"version": n, "name": name or revision, "appliedAt": None,
                            "revision": revision})
            if revision == at:
                return applied
        return []       # standing on a revision this code has no script for: say nothing rather than guess

    async def revision(self) -> str:
        """Where the schema stands, or "" when it has never been migrated.

        A database with no `alembic_version` is exactly the state this screen exists to diagnose, so
        asking for it must not be the thing that breaks the screen.
        """
        if not await self._scalar("SELECT to_regclass('alembic_version') IS NOT NULL"):
            return ""
        return str(await self._scalar("SELECT version_num FROM alembic_version LIMIT 1") or "")

    async def size(self) -> int:
        return int(await self._scalar("SELECT pg_database_size(current_database())"))

    # ── copies ───────────────────────────────────────────────────
    def _backups(self) -> list[dict[str, Any]]:
        directory = self.config.backups_dir
        if not directory.is_dir():
            return []
        found = sorted(directory.glob("neurocode-*.dump"), key=lambda p: (p.stat().st_mtime, p.name),
                       reverse=True)
        return [_describe(p) for p in found[:KEEP_BACKUPS]]

    async def backup(self, reason: str = "manual") -> dict[str, Any]:
        """A copy of the whole database, taken by `pg_dump` while it stays in use.

        Another program, so a worker thread; a password, so the environment rather than the command
        line, where every process on the machine could read it. Only the account that runs the API can
        read what comes out, and the newest few are kept.
        """
        server = int(await self._scalar("SELECT current_setting('server_version_num')::int")) // 10000
        found = await asyncio.to_thread(find_pg_dump, server, self.config.pg_bin_dir)
        if found is None:
            raise NoBackupTool(
                f"No pg_dump for PostgreSQL {server} or newer was found on PATH or in the usual install "
                f"locations, so no backup can be taken. Install the client tools, or set "
                f"NEUROCODE_PG_BIN_DIR to the folder that holds pg_dump.")
        return await asyncio.to_thread(self._dump, found, reason)

    def _dump(self, program: str, reason: str) -> dict[str, Any]:
        url = make_url(self.config.sync_database_url)
        directory = self.config.backups_dir
        directory.mkdir(parents=True, exist_ok=True)
        tag = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")[:40] or "manual"
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        dest, n = directory / f"neurocode-{stamp}-{tag}.dump", 1
        while dest.exists():
            n += 1
            dest = directory / f"neurocode-{stamp}-{tag}-{n}.dump"

        argv = [program, "--format=custom", "--no-owner", "--no-privileges", "--file", str(dest)]
        for flag, value in (("--host", url.host), ("--port", url.port), ("--username", url.username)):
            if value:
                argv += [flag, str(value)]
        argv.append(url.database or "")
        # PGPASSWORD rather than the URL: a password in argv is readable by anyone who can run `ps`.
        env = {**os.environ, "PGPASSWORD": url.password or "", "PGCONNECT_TIMEOUT": "10"}
        try:
            done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=BACKUP_TIMEOUT)
        except subprocess.TimeoutExpired as slow:
            dest.unlink(missing_ok=True)
            raise Refused(f"pg_dump did not finish within {BACKUP_TIMEOUT} seconds.") from slow
        if done.returncode != 0:
            dest.unlink(missing_ok=True)
            raise Refused(f"pg_dump failed: {(done.stderr or '').strip()[-300:] or 'no reason given'}")
        os.chmod(dest, 0o600)
        for old in sorted(directory.glob("neurocode-*.dump"),
                          key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[KEEP_BACKUPS:]:
            old.unlink(missing_ok=True)
        return _describe(dest)

    # ── the check ────────────────────────────────────────────────
    async def check(self) -> dict[str, Any]:
        """What "is this database sound?" means once it is Postgres.

        SQLite's `integrity_check` read its one file for damage; there is nothing to port, because a
        Postgres file is not ours to read. So the question is asked the way it can be answered from
        outside: the server answers, the schema is where the code expects it, every foreign key still
        points at a row, and nothing has been left so full of dead rows that it should be vacuumed.
        `checked` says all four out loud; `ok` and `integrity` keep the shape the screen reads.
        """
        checked: list[dict[str, Any]] = []
        started = time.monotonic()
        version = await self._scalar("SELECT current_setting('server_version')")
        checked.append({"name": "connection", "ok": True,
                        "detail": f"PostgreSQL {version} answered in {_ms(started)} ms"})

        at, head = await self.revision(), (await asyncio.to_thread(_revisions))[-1][0]
        checked.append({"name": "schema", "ok": at == head,
                        "detail": f"at {at}, the latest migration" if at == head
                        else f"at {at}, but the latest migration is {head} — run alembic upgrade head"})

        orphans, violated, constraints = await self._orphans()
        checked.append({"name": "foreignKeys", "ok": not orphans,
                        "detail": f"{constraints} foreign keys walked, none violated" if not orphans
                        else f"{orphans} rows point at nothing, across {', '.join(violated[:MAX_NAMES])}"})

        bloated = await self._bloated()
        checked.append({"name": "bloat", "ok": not bloated,
                        "detail": "no table is mostly dead rows" if not bloated
                        else f"mostly dead rows, and worth optimizing: {', '.join(bloated[:MAX_NAMES])}"})

        problems = [c["detail"] for c in checked if not c["ok"]]
        return {"ok": not problems, "integrity": problems[:MAX_NAMES] or ["ok"],
                "foreignKeyProblems": orphans, "at": _now(), "checked": checked}

    async def _orphans(self) -> tuple[int, list[str], int]:
        """Every foreign key walked: how many child rows have no parent, and which keys they break.

        Postgres enforces these on write, so the honest expectation is zero — but a constraint added
        `NOT VALID`, or one dropped and rebuilt by hand, is exactly the kind of thing this check
        exists to find, and the only way to know is to go and look.
        """
        keys = (await self.session.execute(text(
            "SELECT c.conname AS name, n.nspname AS schema, ch.relname AS child, pa.relname AS parent, "
            "       (SELECT array_agg(a.attname ORDER BY k.ord) "
            "          FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
            "          JOIN pg_attribute a ON a.attrelid = c.conrelid "
            "                             AND a.attnum = k.attnum) AS child_cols, "
            "       (SELECT array_agg(a.attname ORDER BY k.ord) "
            "          FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord) "
            "          JOIN pg_attribute a ON a.attrelid = c.confrelid "
            "                             AND a.attnum = k.attnum) AS parent_cols "
            "FROM pg_constraint c "
            "JOIN pg_class ch ON ch.oid = c.conrelid "
            "JOIN pg_class pa ON pa.oid = c.confrelid "
            "JOIN pg_namespace n ON n.oid = ch.relnamespace "
            "WHERE c.contype = 'f' AND n.nspname = current_schema() "
            "ORDER BY ch.relname, c.conname LIMIT :cap"), {"cap": MAX_TABLES * 4})).mappings().all()

        total, violated = 0, []
        for key in keys:
            schema = _q(key["schema"])
            child, parent = f"{schema}.{_q(key['child'])}", f"{schema}.{_q(key['parent'])}"
            # A key with any column null is satisfied, so those rows are not asked about at all.
            present = " AND ".join(f"ch.{_q(col)} IS NOT NULL" for col in key["child_cols"])
            matches = " AND ".join(f"pa.{_q(p)} = ch.{_q(c)}"
                                   for c, p in zip(key["child_cols"], key["parent_cols"], strict=True))
            loose = int(await self._scalar(
                f"SELECT count(*) FROM {child} ch WHERE {present} "
                f"AND NOT EXISTS (SELECT 1 FROM {parent} pa WHERE {matches})"))
            if loose:
                total += loose
                violated.append(key["name"])
        return total, violated, len(keys)

    async def _bloated(self) -> list[str]:
        rows = (await self.session.execute(text(
            "SELECT relname FROM pg_stat_user_tables "
            "WHERE n_dead_tup > :floor AND n_dead_tup > :ratio * (n_live_tup + n_dead_tup) "
            "ORDER BY n_dead_tup DESC LIMIT :cap"),
            {"floor": BLOAT_FLOOR, "ratio": BLOAT_RATIO, "cap": MAX_NAMES})).scalars().all()
        return list(rows)

    # ── housekeeping ─────────────────────────────────────────────
    async def optimize(self, engine: AsyncEngine) -> dict[str, Any]:
        """Give back what dead rows are holding, refresh the planner's statistics, and rebuild the
        indexes.

        Neither statement may run inside a transaction, so neither can run on the request's session:
        they get a connection of their own in AUTOCOMMIT. `REINDEX` also waits on every *other* open
        transaction — the caller's included — so the route lets go of its own before calling this.
        Anything still holding a lock elsewhere gets a short deadline and is reported as a busy
        database rather than waited on.
        """
        before, started = await self.size(), time.monotonic()
        name = await self._scalar("SELECT current_database()")
        did: list[dict[str, Any]] = []
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("SELECT set_config('lock_timeout', :wait, false)"), {"wait": LOCK_WAIT})
            try:
                for label, statement in (("vacuum analyze", "VACUUM (ANALYZE)"),
                                         ("reindex", f"REINDEX DATABASE {_q(name)}")):
                    step = time.monotonic()
                    try:
                        await conn.execute(text(statement))
                    except DBAPIError as refused:
                        did.append({"step": label, "ok": False, "ms": _ms(step), "detail": _why(refused)})
                    else:
                        did.append({"step": label, "ok": True, "ms": _ms(step), "detail": ""})
            finally:
                # `set_config(..., false)` is for the whole session, and this connection goes back to
                # the pool — so without this, every later request inherited a two-second lock timeout.
                await conn.execute(text("RESET lock_timeout"))
        return {"beforeBytes": before, "afterBytes": await self.size(), "ms": _ms(started), "did": did}

    # ── keeping only as much history as was asked for ────────────
    def _cutoff(self, days: int) -> datetime | None:
        """The moment before which rows of this kind are past their keeping. `None` means keep them."""
        return datetime.now(UTC) - timedelta(days=days) if days else None

    async def retention(self) -> list[dict[str, Any]]:
        """Each history: how long it is kept, and how many rows are older than that.

        Counted for real, one statement per table — not estimated from the planner, which is describing
        a table as it was when it was last analysed. This is the figure a person reads on the button
        *before* pressing it, and the rule the whole product is held to is that a number on a screen was measured or is
        not there. It is also why the count and the deleting are two separate asks: what the button
        says is what was true when it was drawn, and what comes back afterwards is what really went.
        """
        out: list[dict[str, Any]] = []
        for history in HISTORIES:
            days = int(getattr(self.config, history.setting))
            cutoff = self._cutoff(days)
            rows = int(await self._scalar(
                f"SELECT count(*) FROM {_q(history.table)} WHERE {_q(history.column)} < :cutoff",
                cutoff=cutoff)) if cutoff else 0
            out.append({"table": history.table, "label": history.label, "note": history.note,
                        "setting": f"NEUROCODE_{history.setting.upper()}", "days": days,
                        "cutoff": cutoff.isoformat(timespec="seconds") if cutoff else None, "rows": rows})
        return out

    async def prune(self, engine: AsyncEngine) -> dict[str, Any]:
        """Remove what is past its keeping, a few thousand rows at a time.

        One `DELETE … WHERE at < cutoff` over a year of run output is a single statement holding row
        locks on millions of rows for minutes, inside one transaction, with everything that writes a
        log line queued behind it — which is how a tidying-up becomes an outage. So it is chunked by
        `ctid`, each chunk its own committed statement on a connection of its own, with the same short
        lock deadline the vacuum uses: anything holding a table is reported, not waited on.

        Nothing is vacuumed here. The rows are gone, but the space they held goes back to the operating
        system only when something vacuums, which is the other button on this screen and says so.
        """
        started = time.monotonic()
        plan = await self.retention()
        done: list[dict[str, Any]] = []
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("SELECT set_config('lock_timeout', :wait, false)"), {"wait": LOCK_WAIT})
            try:
                # `retention()` answers in the order of HISTORIES, so each row has its own table beside it
                # without the table's column name having to travel out to the screen and back.
                for history, row in zip(HISTORIES, plan, strict=True):
                    done.append(await self._prune_one(conn, history, row))
            finally:
                # As after a reindex: the connection goes back to the pool, and without this every
                # later request on it would inherit a two-second lock timeout.
                await conn.execute(text("RESET lock_timeout"))
        return {"at": _now(), "ms": _ms(started), "tables": done,
                "removed": sum(int(d["removed"]) for d in done)}

    async def _prune_one(self, conn: AsyncConnection, history: History, row: dict[str, Any]) -> dict[str, Any]:
        """One table's share of the prune. Never raises: a table that cannot be touched right now is a
        line on the screen saying why, not a failed chore that leaves the other five undone."""
        if not row["days"] or not row["rows"]:
            return {**row, "removed": 0, "note": "kept" if not row["days"] else "nothing older"}
        # Worked out again rather than read back off the plan, which carries it as words for the screen:
        # the database is asked with a moment, not with a string that happens to look like one.
        cutoff = self._cutoff(int(row["days"]))
        chunk, removed, statements = self.config.prune_rows, 0, 0
        # No ORDER BY: any `chunk` of the rows past the cutoff will do, and two of these tables have no
        # index on time alone — asking for the oldest first would make each statement sort the table.
        statement = text(
            f"DELETE FROM {_q(history.table)} WHERE ctid IN ("
            f"  SELECT ctid FROM {_q(history.table)} WHERE {_q(history.column)} < :cutoff LIMIT :chunk)")
        while statements < MAX_PRUNE_STATEMENTS:
            try:
                result = await conn.execute(statement, {"cutoff": cutoff, "chunk": chunk})
            except DBAPIError as refused:
                return {**row, "removed": removed, "note": _why(refused)}
            statements += 1
            removed += int(result.rowcount or 0)
            if int(result.rowcount or 0) < chunk:
                return {**row, "removed": removed, "note": ""}
        return {**row, "removed": removed,
                "note": f"stopped after {removed:,} rows — the rest goes on the next pass"}

    # ── emptying the workspace ───────────────────────────────────
    async def empty(self) -> dict[str, int]:
        """Delete the work, keep the people. Returns what is left, counted for real.

        Inside the request's transaction, so an emptying that fails half way leaves nothing behind.
        A fact's tags and the conflicts naming it go with the fact; a server's tools with the server.
        Every project's own settings go too — all of them, not only those of projects still here, so
        an answer left behind by an emptying from before this rule existed goes as well.

        Every open tab is told with one `reset` event once this commits: bulk deletes pass no
        document through the unit of work, so there is nothing to announce one by one.
        """
        for prefix in PER_PROJECT_SETTINGS:
            await self.session.execute(delete(Setting).where(Setting.key.startswith(prefix, autoescape=True)))
        for model in EMPTIED:
            await self.session.execute(delete(model))
        announce(self.session, "reset", {"at": _now()})
        return await self.counts()

    async def counts(self) -> dict[str, int]:
        """What the workspace holds, counted for real: this is read straight after emptying it, when the
        planner's estimates are still describing the workspace that was just deleted."""
        counted: dict[str, int] = {}
        for name, model in COLLECTIONS:
            counted[name] = int(await self._scalar(
                f"SELECT count(*) FROM {_q(model.__tablename__)}"))
        return counted


class Housekeeping:
    """The daily prune: the same chore the button on Admin → Database runs, taken once a day on its own.

    It has no loop of its own. The routines' scheduler already wakes every thirty seconds, and a second
    timer would be one more thing to start, stop and reason about; `tick()` is cheap enough to call on
    every one of those wakes and answers `None` on all but the first of a day.

    The day is claimed before anything is deleted, with one statement against the workspace's own
    settings: whoever's `INSERT … ON CONFLICT DO UPDATE … WHERE` actually changes the row is the one
    that prunes. That is what makes three API processes — or one restarted three times in a morning —
    prune once between them rather than once each.
    """

    def __init__(self, db: Database, *, config: Settings | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.db, self.config, self.clock = db, config or get_settings(), clock

    async def claim(self, session: AsyncSession, day: str) -> bool:
        """Today's prune, claimed for this process — true at most once a day, across every process."""
        claimed = (await session.execute(text(
            "INSERT INTO settings (key, value) VALUES (:key, jsonb_build_object('day', CAST(:day AS text))) "
            "ON CONFLICT (key) DO UPDATE SET value = jsonb_build_object('day', CAST(:day AS text)) "
            "WHERE coalesce(settings.value ->> 'day', '') < CAST(:day AS text) "
            "RETURNING key"), {"key": PRUNED_KEY, "day": day})).scalar_one_or_none()
        return claimed is not None

    async def tick(self) -> dict[str, Any] | None:
        """Prune if today's prune is still going; otherwise nothing at all, and nothing said."""
        if not self.config.prune_daily:
            return None
        day = self.clock().date().isoformat()
        async with self.db.session() as claiming:
            if not await self.claim(claiming, day):
                return None
        async with self.db.session() as working:
            pruned = await MaintenanceService(working, self.config).prune(self.db.engine)
            # In the audit log with no name against it, which is exactly what it is: nobody asked for
            # this one, the settings did. A prune that found nothing is not worth a line.
            if pruned["removed"]:
                await AuditRepository(working).record(
                    action="database.prune", user_id=None, target="history",
                    detail={"removed": pruned["removed"],
                            "tables": {t["table"]: t["removed"] for t in pruned["tables"] if t["removed"]}})
        log.info("housekeeping: removed %d row(s) of history", pruned["removed"])
        return pruned
