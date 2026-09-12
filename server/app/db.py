"""SQLite storage for the NeuroCode API.

One file, no server process, full-text search through FTS5: the right size for a local-first tool.
The schema lives in numbered SQL migrations (app/migrations). This module applies them, then offers two
kinds of access: documents (domain records kept as JSON shaped like the frontend's types, with the
fields the API filters on lifted into columns) and plain rows (identity, access, audit and settings,
which are strictly relational).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "seed.json"
MIGRATIONS = Path(__file__).resolve().parent / "migrations"
KEEP_BACKUPS = 20

# Document tables, seeded from seed.json one at a time, so a database made by an older version gains
# the tables it is missing and keeps every change it already holds.
TABLES = ("projects", "agents", "tasks", "approvals", "memory", "plans", "conflicts", "mcp", "prefs", "decisions",
          "brainstorms", "runs", "activity")

# Words that carry no meaning for relevance, English and Hinglish alike.
STOP = set("""the and for with when that this from into are was were not but should must have has had then than
your you our they them its also only just like very what which who why how all any can could would will may might
been being does did done fix make need needs want please mein hai hain raha rahi rahe karo kar ko ka ki ke pe par se
aur nahi ho yeh woh tha thi abhi bhi jo kya kuch sab""".split())


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_seed() -> dict[str, Any]:
    return json.loads(SEED_PATH.read_text())


def _j(doc: Any) -> str:
    return json.dumps(doc, ensure_ascii=False)


def _fts_query(text: str, mode: str = "all") -> str:
    """Free text as a safe FTS5 query. `all`: every word must match, as a prefix (what a search box
    wants). `any`: the meaningful words, OR-ed and ranked (what "find facts relevant to this" wants)."""
    words = re.findall(r"[A-Za-z0-9]+", text)
    if mode == "any":
        picked = list(dict.fromkeys(w.lower() for w in words if len(w) >= 3 and w.lower() not in STOP))
        return " OR ".join(f'"{w}"*' for w in picked[:24])
    return " ".join(f'"{t}"*' for t in words)


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.backup_dir: Path | None = None if path == ":memory:" else Path(path).resolve().with_name("backups")
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self.conn.execute("PRAGMA temp_store = MEMORY")
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode = WAL")    # readers never wait for the writer
                self.conn.execute("PRAGMA synchronous = NORMAL")  # safe under WAL, and far fewer fsyncs
        self.migrate()
        empty = [t for t in TABLES if self.count(t) == 0]
        if empty:
            self.seed(tables=empty)

    # ── schema ───────────────────────────────────────────────────
    def migrate(self) -> list[str]:
        """Apply every migration not applied yet, each in its own transaction. Returns what ran."""
        ran: list[str] = []
        with self.lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                              "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
            self.conn.commit()
            done = {r[0] for r in self.conn.execute("SELECT version FROM schema_migrations")}
            pending = [f for f in sorted(MIGRATIONS.glob("[0-9]*.sql")) if int(f.name.split("_", 1)[0]) not in done]
            # A database that already holds data is copied aside before its schema changes.
            if pending and self.backup_dir is not None and self.conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT IN ('schema_migrations', 'sqlite_sequence')").fetchone()[0]:
                self.backup(f"before {pending[0].stem}")
            for f in pending:
                version = int(f.name.split("_", 1)[0])
                try:
                    self.conn.executescript(f"BEGIN;\n{f.read_text()}\n"
                                            f"INSERT INTO schema_migrations VALUES ({version}, '{f.stem}', '{now_iso()}');\nCOMMIT;")
                except sqlite3.Error:
                    self.conn.execute("ROLLBACK")
                    raise
                ran.append(f.stem)
        return ran

    def count(self, table: str) -> int:
        with self.lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def seed(self, data: dict[str, Any] | None = None, tables: tuple[str, ...] | list[str] = TABLES) -> None:
        data = data or load_seed()
        with self.lock, self.conn:
            c = self.conn
            for t in tables:
                c.execute(f"DELETE FROM {t}")
                if t == "projects":  # the code index went with the projects; its search table is not a foreign key
                    c.execute("DELETE FROM code_fts")
                rows = data.get(t, [])
                if t == "memory":
                    c.execute("INSERT INTO memory_fts(memory_fts) VALUES('delete-all')")
                    for f in rows:
                        self._insert_memory(c, f)
                elif t == "tasks":
                    c.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                                  [(x["id"], x["ref"], x["projectId"], x["status"], _j(x)) for x in rows])
                elif t == "approvals":
                    c.executemany("INSERT INTO approvals VALUES (?, ?, ?, ?)",
                                  [(x["id"], x["ref"], x["status"], _j(x)) for x in rows])
                elif t in ("plans", "brainstorms"):
                    col = "ref, " if t == "plans" else ""
                    c.executemany(f"INSERT INTO {t}(id, {col}project_id, created, doc) VALUES (?, {'?, ' if col else ''}?, 0, ?)",
                                  [((x["id"], x["ref"]) if col else (x["id"],)) + (x.get("projectId"), _j(x)) for x in rows])
                elif t == "runs":
                    pass  # nothing to seed: a run only exists for work that really happened
                elif t == "conflicts":
                    c.executemany("INSERT INTO conflicts(id, status, doc) VALUES (?, 'open', ?)", [(x["id"], _j(x)) for x in rows])
                elif t == "activity":
                    c.executemany("INSERT INTO activity(position, doc) VALUES (?, ?)", [(i, _j(e)) for i, e in enumerate(rows)])
                else:  # projects, agents, mcp, prefs, decisions
                    c.executemany(f"INSERT INTO {t} VALUES (?, ?)", [(x["id"], _j(x)) for x in rows])

    @staticmethod
    def _insert_memory(c: sqlite3.Connection, f: dict[str, Any]) -> None:
        cur = c.execute("INSERT INTO memory(id, ref, project_id, category, pinned, archived, doc) VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (f["id"], f["ref"], f["projectId"], f["category"], int(f.get("pinned", False)), _j(f)))
        c.execute("INSERT INTO memory_fts(rowid, ref, title, body, reason, tags) VALUES (?, ?, ?, ?, ?, ?)",
                  (cur.lastrowid, f["ref"], f["title"], f["body"], f["reason"], " ".join(f.get("tags", []))))

    # ── rows: identity, access, audit, settings ─────────────────
    def rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def row(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.lock, self.conn:
            return self.conn.execute(sql, params).lastrowid or 0

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Several statements as one transaction: all of them happen, or none do."""
        with self.lock, self.conn:
            yield self.conn

    def setting(self, key: str, default: Any = None) -> Any:
        r = self.row("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(r[0]) if r else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, _j(value)))

    # ── documents ────────────────────────────────────────────────
    def docs(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return [json.loads(r["doc"]) for r in self.rows(sql, params)]

    def all(self, table: str) -> list[dict[str, Any]]:
        # plans and brainstorms: the newest first, then the seed in its own order
        order = "created DESC, rowid" if table in ("plans", "brainstorms") else "rowid"
        return self.docs(f"SELECT doc FROM {table} ORDER BY {order}")

    def get(self, table: str, id: str) -> dict[str, Any] | None:
        rows = self.docs(f"SELECT doc FROM {table} WHERE id = ?", (id,))
        return rows[0] if rows else None

    def one(self, table: str, ref: str) -> dict[str, Any] | None:
        rows = self.docs(f"SELECT doc FROM {table} WHERE ref = ?", (ref,))
        return rows[0] if rows else None

    def insert(self, table: str, doc: dict[str, Any], **columns: Any) -> dict[str, Any]:
        cols = ["id", *columns, "doc"]
        with self.lock, self.conn:
            self.conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                              (doc["id"], *columns.values(), _j(doc)))
        return doc

    def save(self, table: str, doc: dict[str, Any], **columns: Any) -> dict[str, Any]:
        sets = ", ".join(["doc = ?"] + [f"{k} = ?" for k in columns])
        with self.lock, self.conn:
            self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (_j(doc), *columns.values(), doc["id"]))
        return doc

    def upsert(self, table: str, doc: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.conn:
            self.conn.execute(f"INSERT INTO {table}(id, doc) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET doc = excluded.doc",
                              (doc["id"], _j(doc)))
        return doc

    def unique_id(self, table: str, base: str) -> str:
        n, candidate = 1, base
        while self.get(table, candidate) is not None:
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    def next_number(self) -> int:
        """The next TASK-/PLAN- number. A compiled plan and its task share it."""
        refs = [r[0] for r in self.rows("SELECT ref FROM tasks UNION ALL SELECT ref FROM plans")]
        return max((int(m.group()) for r in refs if (m := re.search(r"\d+$", r))), default=500) + 1

    def next_memory_number(self) -> int:
        return self._next("SELECT ref FROM memory", 0)

    def next_run_number(self) -> int:
        return self._next("SELECT ref FROM runs", 0)

    def next_approval_number(self) -> int:
        return self._next("SELECT ref FROM approvals", 100)

    def _next(self, sql: str, floor: int) -> int:
        refs = [r[0] for r in self.rows(sql)]
        return max((int(m.group()) for r in refs if (m := re.search(r"\d+$", r))), default=floor) + 1

    # ── domain ───────────────────────────────────────────────────
    def insert_task(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.insert("tasks", doc, ref=doc["ref"], project_id=doc["projectId"], status=doc["status"])

    def save_task(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.save("tasks", doc, status=doc["status"])

    def save_approval(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.save("approvals", doc, status=doc["status"])

    def insert_plan(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.insert("plans", doc, ref=doc["ref"], project_id=doc["projectId"], created=time.time())

    def insert_brainstorm(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.insert("brainstorms", doc, project_id=doc.get("projectId"), created=time.time())

    def insert_run(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.insert("runs", doc, ref=doc["ref"], project_id=doc["projectId"], status=doc["status"], started=time.time())

    def save_run(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.save("runs", doc, status=doc["status"])

    def add_run_log(self, run_id: str, step: int | None, level: str, line: str, cap: int = 4000) -> dict[str, Any]:
        """One line of a run's output. The oldest lines go when a run has written too many."""
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO run_logs(run_id, at, step, level, line) VALUES (?, ?, ?, ?, ?)",
                                    (run_id, now_iso(), step, level, line[:2000]))
            self.conn.execute("DELETE FROM run_logs WHERE run_id = ? AND id <= "
                              "(SELECT MAX(id) - ? FROM run_logs WHERE run_id = ?)", (run_id, cap, run_id))
        return {"id": cur.lastrowid, "at": now_iso(), "step": step, "level": level, "line": line[:2000]}

    def run_logs(self, run_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        return [{"id": r[0], "at": r[1], "step": r[2], "level": r[3], "line": r[4]} for r in self.rows(
            "SELECT id, at, step, level, line FROM run_logs WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
            (run_id, after, limit))]

    def next_chat_number(self) -> int:
        return self._next("SELECT ref FROM chats", 0)

    def insert_chat(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.insert("chats", doc, ref=doc["ref"], project_id=doc["projectId"], status=doc["status"],
                           started=time.time())

    def save_chat(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.save("chats", doc, status=doc["status"])

    def add_message(self, chat_id: str, doc: dict[str, Any], cap: int = 2000) -> dict[str, Any]:
        """One turn of a conversation. The oldest turns go when a chat has grown past the cap."""
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO chat_messages(chat_id, at, role, doc) VALUES (?, ?, ?, ?)",
                                    (chat_id, doc["at"], doc["role"], _j(doc)))
            self.conn.execute("DELETE FROM chat_messages WHERE chat_id = ? AND id <= "
                              "(SELECT MAX(id) - ? FROM chat_messages WHERE chat_id = ?)",
                              (chat_id, cap, chat_id))
        return {**doc, "id": cur.lastrowid}

    def messages(self, chat_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return [{**json.loads(r[1]), "id": r[0]} for r in self.rows(
            "SELECT id, doc FROM chat_messages WHERE chat_id = ? AND id > ? ORDER BY id LIMIT ?",
            (chat_id, after, limit))]

    def insert_memory(self, doc: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.conn:
            self._insert_memory(self.conn, doc)
        return doc

    def save_memory(self, doc: dict[str, Any], archived: bool | None = None) -> dict[str, Any]:
        cols: dict[str, Any] = {"pinned": int(doc.get("pinned", False))}
        if archived is not None:
            cols["archived"] = int(archived)
        return self.save("memory", doc, **cols)

    def memory(self, q: str = "", category: str | None = None, project: str | None = None,
               include_archived: bool = False, mode: str = "all") -> list[dict[str, Any]]:
        match = _fts_query(q, mode)
        if match:
            sql = "SELECT m.doc FROM memory_fts f JOIN memory m ON m.rowid = f.rowid"
            where, params, order = ["memory_fts MATCH ?"], [match], "ORDER BY f.rank"
        else:
            sql, where, params = "SELECT m.doc FROM memory m", [], []
            order = "ORDER BY m.pinned DESC, json_extract(m.doc, '$.strength') DESC"
        if category:
            where.append("m.category = ?")
            params.append(category)
        if project:
            where.append("(m.project_id = ? OR m.project_id = 'global')")
            params.append(project)
        if not include_archived:
            where.append("m.archived = 0")
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        return self.docs(f"{sql}{clause} {order}", tuple(params))

    def activity(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.docs("SELECT doc FROM activity ORDER BY position ASC LIMIT ?", (limit,))

    def add_event(self, doc: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.conn:
            top = self.conn.execute("SELECT COALESCE(MIN(position), 0) FROM activity").fetchone()[0]
            cur = self.conn.execute("INSERT INTO activity(position, doc) VALUES (?, ?)", (top - 1, "{}"))
            doc = {"id": f"live-{cur.lastrowid}", **doc}
            self.conn.execute("UPDATE activity SET doc = ? WHERE seq = ?", (_j(doc), cur.lastrowid))
        return doc

    # ── care: backups, checks, compaction ───────────────────────
    def backup(self, reason: str = "manual") -> dict[str, Any]:
        """A consistent copy of the whole database, taken while it stays in use (SQLite's online backup).
        Only the account that runs the API can read it; the newest KEEP_BACKUPS are kept."""
        if self.backup_dir is None:
            raise RuntimeError("An in-memory database has no file to back up")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        tag = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")[:40] or "manual"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest, n = self.backup_dir / f"neurocode-{stamp}-{tag}.db", 1
        while dest.exists():
            n += 1
            dest = self.backup_dir / f"neurocode-{stamp}-{tag}-{n}.db"
        with self.lock:
            target = sqlite3.connect(dest)
            try:
                self.conn.backup(target)
            finally:
                target.close()
        os.chmod(dest, 0o600)
        for old in self._backup_files()[KEEP_BACKUPS:]:
            old.unlink(missing_ok=True)
        return self._describe(dest)

    def _backup_files(self) -> list[Path]:
        if self.backup_dir is None or not self.backup_dir.is_dir():
            return []
        return sorted(self.backup_dir.glob("neurocode-*.db"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)

    @staticmethod
    def _describe(p: Path) -> dict[str, Any]:
        st = p.stat()
        return {"name": p.name, "bytes": st.st_size, "at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}

    def backups(self) -> list[dict[str, Any]]:
        return [self._describe(p) for p in self._backup_files()]

    def _sizes(self) -> tuple[int, int]:
        if self.path == ":memory:":
            return 0, 0
        main, wal = Path(self.path), Path(self.path + "-wal")
        return (main.stat().st_size if main.exists() else 0), (wal.stat().st_size if wal.exists() else 0)

    def stats(self) -> dict[str, Any]:
        """The file, its pages, every table with its row count, and the migrations applied."""
        with self.lock:
            def one(sql: str) -> Any:
                return self.conn.execute(sql).fetchone()[0]
            virtual = [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL TABLE%'")]
            names = [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            tables = [t for t in names if t not in virtual and not any(t.startswith(f"{v}_") for v in virtual)]
            counts = [{"name": t, "rows": one(f'SELECT COUNT(*) FROM "{t}"')} for t in tables]
            pages = {"pageSize": one("PRAGMA page_size"), "pages": one("PRAGMA page_count"),
                     "freePages": one("PRAGMA freelist_count"), "journalMode": one("PRAGMA journal_mode")}
            migrations = [{"version": r[0], "name": r[1], "appliedAt": r[2]} for r in self.conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version")]
        size, wal = self._sizes()
        return {"path": self.path, "sqlite": sqlite3.sqlite_version, **pages, "sizeBytes": size, "walBytes": wal,
                "tables": counts, "indexes": len(virtual), "migrations": migrations, "backups": self.backups(),
                "backupDir": str(self.backup_dir) if self.backup_dir else None}

    def check(self) -> dict[str, Any]:
        """SQLite's own integrity check, and every row whose foreign key points at nothing."""
        with self.lock:
            integrity = [r[0] for r in self.conn.execute("PRAGMA quick_check")]
            orphans = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        return {"ok": integrity == ["ok"] and not orphans, "integrity": integrity[:20], "foreignKeyProblems": len(orphans),
                "at": now_iso()}

    def optimize(self) -> dict[str, Any]:
        """Refresh the planner's statistics, merge the search indexes, rebuild the file without its free
        pages, and fold the write-ahead log back in."""
        before, t0 = sum(self._sizes()), time.monotonic()
        with self.lock:
            with self.conn:
                self.conn.execute("INSERT INTO memory_fts(memory_fts) VALUES ('optimize')")
                self.conn.execute("INSERT INTO code_fts(code_fts) VALUES ('optimize')")
            self.conn.execute("PRAGMA optimize")
            self.conn.execute("VACUUM")
            if self.path != ":memory:":
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"beforeBytes": before, "afterBytes": sum(self._sizes()), "ms": round((time.monotonic() - t0) * 1000)}
