"""SQLite storage for the NeuroCode API.

One file, no server process, full-text search through FTS5 — the right size for a single-operator,
local-first tool, and it runs on a laptop without a container. Each record is kept as the same JSON
document the frontend's mocks use; the few fields the API filters on or mutates are lifted into real
columns and kept in step with the document.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "seed.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects  (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents    (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks     (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, project_id TEXT, status TEXT, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, status TEXT NOT NULL, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory    (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, project_id TEXT, category TEXT,
                                      pinned INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, doc TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(ref, title, body, reason, tags, content='');
CREATE TABLE IF NOT EXISTS plans     (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, project_id TEXT,
                                      created REAL NOT NULL DEFAULT 0, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts (id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'open', doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mcp       (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prefs     (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity  (seq INTEGER PRIMARY KEY AUTOINCREMENT, position REAL NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS activity_position ON activity(position);
"""

# Seeded one table at a time, so a database made by an older version gains the tables it is missing
# and keeps every change it already holds.
TABLES = ("projects", "agents", "tasks", "approvals", "memory", "plans", "conflicts", "mcp", "prefs", "decisions", "activity")

# Words that carry no meaning for relevance, English and Hinglish alike.
STOP = set("""the and for with when that this from into are was were not but should must have has had then than
your you our they them its also only just like very what which who why how all any can could would will may might
been being does did done fix make need needs want please mein hai hain raha rahi rahe karo kar ko ka ki ke pe par se
aur nahi ho yeh woh tha thi abhi bhi jo kya kuch sab""".split())


def _j(doc: dict[str, Any]) -> str:
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
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
        empty = [t for t in TABLES if self.count(t) == 0]
        if empty:
            self.seed(tables=empty)

    # ── setup ────────────────────────────────────────────────────
    def count(self, table: str) -> int:
        with self.lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def seed(self, data: dict[str, Any] | None = None, tables: tuple[str, ...] | list[str] = TABLES) -> None:
        data = data or json.loads(SEED_PATH.read_text())
        with self.lock, self.conn:
            c = self.conn
            for t in tables:
                c.execute(f"DELETE FROM {t}")
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
                elif t == "plans":
                    c.executemany("INSERT INTO plans(id, ref, project_id, created, doc) VALUES (?, ?, ?, 0, ?)",
                                  [(x["id"], x["ref"], x["projectId"], _j(x)) for x in rows])
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

    # ── generic reads and writes ─────────────────────────────────
    def docs(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [json.loads(r["doc"]) for r in self.conn.execute(sql, params).fetchall()]

    def all(self, table: str) -> list[dict[str, Any]]:
        # plans: the newest compiled first, then the seed in its own order
        order = "created DESC, rowid" if table == "plans" else "rowid"
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
        with self.lock:
            refs = [r[0] for r in self.conn.execute("SELECT ref FROM tasks UNION ALL SELECT ref FROM plans")]
        return max((int(m.group()) for r in refs if (m := re.search(r"\d+$", r))), default=500) + 1

    def next_memory_number(self) -> int:
        with self.lock:
            refs = [r[0] for r in self.conn.execute("SELECT ref FROM memory")]
        return max((int(m.group()) for r in refs if (m := re.search(r"\d+$", r))), default=0) + 1

    # ── domain ───────────────────────────────────────────────────
    def insert_task(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.insert("tasks", doc, ref=doc["ref"], project_id=doc["projectId"], status=doc["status"])

    def save_task(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.save("tasks", doc, status=doc["status"])

    def save_approval(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.save("approvals", doc, status=doc["status"])

    def insert_plan(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self.insert("plans", doc, ref=doc["ref"], project_id=doc["projectId"], created=time.time())

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
