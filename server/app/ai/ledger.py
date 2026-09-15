"""Where the gateway keeps its settings and its ledger.

The gateway is a blocking thing by nature: it opens sockets to model providers and waits on them, so
it always runs on a worker thread and can never hold the request's async session. Giving it one
anyway is how you get a deadlock. It is handed this narrow port instead — four questions, answered by
whatever store the deployment runs on.

Four, and no more. The port is deliberately not "a database": everything the gateway needs is a
setting to read, a setting to write, how many calls a lane has made today, and a line to append. A
port that small can be implemented against anything, and *is* — Postgres for the real stack, the old
SQLite file for as long as the old one is still serving, and a dict in the tests.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@runtime_checkable
class Ledger(Protocol):
    """The whole of what the AI layer asks of a database."""

    def setting(self, key: str, default: Any = None) -> Any: ...

    def save_setting(self, key: str, value: Any) -> None: ...

    def calls_today(self, lane_id: str) -> int: ...

    def record(self, *, feature: str, lane: str, model: str, ok: bool, ms: int, tokens_in: int,
               tokens_out: int, user_id: str | None, project_id: str | None, agent: str,
               error: str) -> None: ...


class PostgresLedger:
    """The real one. A small blocking pool of its own, because its caller is blocking.

    Two connections is the whole pool on purpose: the gateway spends its time waiting on a provider,
    not on this database, and a thread that is waiting on an HTTP response is not holding a
    connection — `record` opens one, writes its line, and gives it straight back.
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: Engine = create_engine(
            url, echo=echo, pool_size=2, max_overflow=3, pool_pre_ping=True, pool_recycle=900,
            future=True)

    # ── settings ─────────────────────────────────────────────────
    def setting(self, key: str, default: Any = None) -> Any:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key}).first()
        # JSONB comes back already decoded; a value stored as a JSON string by the old stack does not.
        return row[0] if row else default

    def save_setting(self, key: str, value: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO settings(key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
                {"k": key, "v": json.dumps(value)})

    # ── the ledger ───────────────────────────────────────────────
    def calls_today(self, lane_id: str) -> int:
        """What this lane has spent against its daily free-tier allowance, counted in the database.

        Counted by the *server's* day, not this machine's: `at` is stored with its zone, so the
        comparison is against `current_date` at the database's timezone and two processes in different
        zones agree on when the allowance resets.
        """
        with self.engine.connect() as conn:
            n = conn.execute(text(
                "SELECT COUNT(*) FROM ai_calls WHERE lane = :lane AND at >= date_trunc('day', now())"),
                {"lane": lane_id}).scalar_one()
        return int(n)

    def record(self, *, feature: str, lane: str, model: str, ok: bool, ms: int, tokens_in: int,
               tokens_out: int, user_id: str | None, project_id: str | None, agent: str = "",
               error: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO ai_calls(at, feature, lane, model, ok, ms, tokens_in, tokens_out, "
                "user_id, project_id, agent, error) VALUES (now(), :feature, :lane, :model, :ok, :ms, "
                ":tin, :tout, :user_id, :project_id, :agent, :error)"),
                {"feature": feature, "lane": lane, "model": model, "ok": ok, "ms": ms,
                 "tin": tokens_in, "tout": tokens_out, "user_id": user_id,
                 "project_id": project_id, "agent": (agent or "")[:60], "error": error[:300]})

    def close(self) -> None:
        self.engine.dispose()


class MemoryLedger:
    """For tests and for a gateway built before its database exists. Remembers, persists nothing."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self._settings: dict[str, Any] = dict(settings or {})
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def setting(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def save_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._settings[key] = value

    def calls_today(self, lane_id: str) -> int:
        today = datetime.now(timezone.utc).date()
        return sum(1 for c in self.calls if c["lane"] == lane_id and c["at"].date() == today)

    def record(self, **line: Any) -> None:
        with self._lock:
            self.calls.append({**line, "at": datetime.now(timezone.utc)})


class SqliteLedger:
    """The old file, behind the same four questions. It exists so the stack that is still serving
    keeps working while the new one is built; the cutover is what deletes it."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def setting(self, key: str, default: Any = None) -> Any:
        return self.store.setting(key, default)

    def save_setting(self, key: str, value: Any) -> None:
        self.store.set_setting(key, value)

    def calls_today(self, lane_id: str) -> int:
        row = self.store.row("SELECT COUNT(*) FROM ai_calls WHERE lane = ? AND substr(at, 1, 10) = ?",
                             (lane_id, datetime.now().strftime("%Y-%m-%d")))
        return int(row[0]) if row else 0

    def record(self, *, feature: str, lane: str, model: str, ok: bool, ms: int, tokens_in: int,
               tokens_out: int, user_id: str | None, project_id: str | None, agent: str = "",
               error: str) -> None:
        # The old table has no column for the agent; it keeps what it can and the new stack keeps all.
        self.store.execute(
            "INSERT INTO ai_calls(at, feature, lane, provider, model, ok, ms, tokens_in, tokens_out, "
            "user_id, project_id, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), feature, lane, lane, model, int(ok), ms,
             tokens_in, tokens_out, user_id, project_id, error[:300]))
