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
CREATE TABLE IF NOT EXISTS activity  (seq INTEGER PRIMARY KEY AUTOINCREMENT, position REAL NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS activity_position ON activity(position);
"""


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: every word must match, each as a prefix."""
    return " ".join(f'"{t}"*' for t in re.findall(r"[A-Za-z0-9]+", text))


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
        if self.count("projects") == 0:
            self.seed()

    # ── setup ────────────────────────────────────────────────────
    def count(self, table: str) -> int:
        with self.lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def seed(self, data: dict[str, Any] | None = None) -> None:
        data = data or json.loads(SEED_PATH.read_text())
        with self.lock, self.conn:
            c = self.conn
            for t in ("projects", "agents", "tasks", "approvals", "memory", "activity"):
                c.execute(f"DELETE FROM {t}")
            c.execute("INSERT INTO memory_fts(memory_fts) VALUES('delete-all')")
            c.executemany("INSERT INTO projects VALUES (?, ?)", [(p["id"], json.dumps(p)) for p in data["projects"]])
            c.executemany("INSERT INTO agents VALUES (?, ?)", [(a["id"], json.dumps(a)) for a in data["agents"]])
            c.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                          [(t["id"], t["ref"], t["projectId"], t["status"], json.dumps(t)) for t in data["tasks"]])
            c.executemany("INSERT INTO approvals VALUES (?, ?, ?, ?)",
                          [(a["id"], a["ref"], a["status"], json.dumps(a)) for a in data["approvals"]])
            for f in data["memory"]:
                cur = c.execute("INSERT INTO memory(id, ref, project_id, category, pinned, archived, doc) VALUES (?, ?, ?, ?, ?, 0, ?)",
                                (f["id"], f["ref"], f["projectId"], f["category"], int(f.get("pinned", False)), json.dumps(f)))
                c.execute("INSERT INTO memory_fts(rowid, ref, title, body, reason, tags) VALUES (?, ?, ?, ?, ?, ?)",
                          (cur.lastrowid, f["ref"], f["title"], f["body"], f["reason"], " ".join(f.get("tags", []))))
            c.executemany("INSERT INTO activity(position, doc) VALUES (?, ?)",
                          [(i, json.dumps(e)) for i, e in enumerate(data["activity"])])

    # ── generic reads ────────────────────────────────────────────
    def docs(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [json.loads(r["doc"]) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, table: str, ref: str) -> dict[str, Any] | None:
        rows = self.docs(f"SELECT doc FROM {table} WHERE ref = ?", (ref,))
        return rows[0] if rows else None

    def _write(self, table: str, ref: str, doc: dict[str, Any], **columns: Any) -> dict[str, Any]:
        sets = ", ".join(["doc = ?"] + [f"{k} = ?" for k in columns])
        with self.lock, self.conn:
            self.conn.execute(f"UPDATE {table} SET {sets} WHERE ref = ?", (json.dumps(doc), *columns.values(), ref))
        return doc

    # ── domain ───────────────────────────────────────────────────
    def save_task(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self._write("tasks", doc["ref"], doc, status=doc["status"])

    def save_approval(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self._write("approvals", doc["ref"], doc, status=doc["status"])

    def save_memory(self, doc: dict[str, Any], archived: bool | None = None) -> dict[str, Any]:
        cols: dict[str, Any] = {"pinned": int(doc.get("pinned", False))}
        if archived is not None:
            cols["archived"] = int(archived)
        return self._write("memory", doc["ref"], doc, **cols)

    def memory(self, q: str = "", category: str | None = None, project: str | None = None,
               include_archived: bool = False) -> list[dict[str, Any]]:
        match = _fts_query(q)
        if match:
            sql = "SELECT m.doc FROM memory_fts f JOIN memory m ON m.rowid = f.rowid"
            where, params, order = ["memory_fts MATCH ?"], [match], "ORDER BY f.rank"
        else:
            sql, where, params = "SELECT m.doc FROM memory m", [], []
            order = "ORDER BY m.pinned DESC, json_extract(m.doc, '$.strength') DESC"
        if category:
            where.append("m.category = ?"); params.append(category)
        if project:
            where.append("(m.project_id = ? OR m.project_id = 'global')"); params.append(project)
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
            self.conn.execute("UPDATE activity SET doc = ? WHERE seq = ?", (json.dumps(doc), cur.lastrowid))
        return doc
