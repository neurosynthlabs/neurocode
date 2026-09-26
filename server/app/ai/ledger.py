"""Where the gateway keeps its settings and its ledger.

(Five questions now, not four. A free tier in 2026 ends on tokens a day rather than calls a day —
Groq's is a thousand calls and two hundred thousand tokens — so the port has to be able to answer
what a lane has spent in tokens as well as in calls, and it is the same one-line query.)

The gateway is a blocking thing by nature: it opens sockets to model providers and waits on them, so
it always runs on a worker thread and can never hold the request's async session. Giving it one
anyway is how you get a deadlock. It is handed this narrow port instead — four questions, answered by
whatever store the deployment runs on.

Four, and no more. The port is deliberately not "a database": everything the gateway needs is a
setting to read, a setting to write, how many calls a lane has made today, and a line to append. A
port that small can be implemented against anything, and *is* — Postgres for the app, and a dict in
the tests. And because it is that small, `Remembered` can wrap any of them and hold the answers for
a couple of seconds, which is what keeps choosing a lane from being forty round trips.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ..data.engine import utc_connect_args


@runtime_checkable
class Ledger(Protocol):
    """The whole of what the AI layer asks of a database."""

    def setting(self, key: str, default: Any = None) -> Any: ...

    def save_setting(self, key: str, value: Any) -> None: ...

    def calls_today(self, lane_id: str) -> int: ...

    def tokens_today(self, lane_id: str) -> int: ...

    def record(self, *, feature: str, lane: str, model: str, ok: bool, ms: int, tokens_in: int,
               tokens_out: int, user_id: str | None, project_id: str | None, agent: str,
               error: str, run_id: str | None = None, tokens_cached: int = 0,
               tokens_reasoning: int = 0) -> None: ...


class PostgresLedger:
    """The real one. A small blocking pool of its own, because its caller is blocking.

    Two connections is the whole pool on purpose: the gateway spends its time waiting on a provider,
    not on this database, and a thread that is waiting on an HTTP response is not holding a
    connection — `record` opens one, writes its line, and gives it straight back.
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        # The same zone as every other connection the app opens (see `data.engine.TIMEZONE`), so this
        # pool's "today" is the day the screens and the rest of the API mean.
        self.engine: Engine = create_engine(
            url, echo=echo, pool_size=2, max_overflow=3, pool_pre_ping=True, pool_recycle=900,
            connect_args=utc_connect_args(url), future=True)

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

        Counted by the database's day, not this machine's: `at` is stored with its zone and the
        session's zone is UTC on every connection, so two processes in different zones agree on when
        the allowance resets — at midnight UTC.
        """
        with self.engine.connect() as conn:
            n = conn.execute(text(
                "SELECT COUNT(*) FROM ai_calls WHERE lane = :lane AND at >= date_trunc('day', now())"),
                {"lane": lane_id}).scalar_one()
        return int(n)

    def tokens_today(self, lane_id: str) -> int:
        """What this lane has spent against a daily *token* allowance — in and out together, because
        that is how a provider counts them. The same day as `calls_today`: the database's, in UTC."""
        with self.engine.connect() as conn:
            n = conn.execute(text(
                "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM ai_calls "
                "WHERE lane = :lane AND at >= date_trunc('day', now())"), {"lane": lane_id}).scalar_one()
        return int(n)

    def record(self, *, feature: str, lane: str, model: str, ok: bool, ms: int, tokens_in: int,
               tokens_out: int, user_id: str | None, project_id: str | None, agent: str = "",
               error: str, run_id: str | None = None, tokens_cached: int = 0,
               tokens_reasoning: int = 0) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO ai_calls(at, feature, lane, model, ok, ms, tokens_in, tokens_out, tokens_cached, "
                "tokens_reasoning, user_id, project_id, agent, run_id, error) VALUES (now(), :feature, :lane, "
                ":model, :ok, :ms, :tin, :tout, :tcached, :treason, :user_id, :project_id, :agent, :run_id, :error)"),
                {"feature": feature, "lane": lane, "model": model, "ok": ok, "ms": ms,
                 "tin": tokens_in, "tout": tokens_out, "tcached": tokens_cached, "treason": tokens_reasoning,
                 "user_id": user_id,
                 "project_id": project_id, "agent": (agent or "")[:60], "run_id": run_id,
                 "error": error[:300]})

    def close(self) -> None:
        self.engine.dispose()


#: How long the gateway may go on believing what it last read. Short enough that an admin editing a
#: lane sees it on the next screen; long enough that one screen's worth of questions is one read.
REMEMBER_SECONDS = 2.0


class Remembered:
    """Any ledger, with the rows it is asked for over and over remembered for a couple of seconds.

    Choosing a lane is nothing but settings. Every lane is settled from `ai.lane.<id>`, then asked
    whether an admin switched it off — the same row again — then asked what it has spent; and the
    router does that for eight lanes, several times, to answer one question. Measured: one `chain()`
    was 36 queries, and Models, which asks for the whole picture, was 350. All of it through
    the gateway's own two-connection pool, so opening that screen while four agents were working made
    the screen and the agents wait on each other.

    Nothing about a *decision* is kept here — only the rows a decision reads, and only for a moment.
    A write goes straight through and drops what it replaced, so an admin saving a lane sees the lane
    they saved rather than the one from two seconds ago; and a call recorded drops that lane's count,
    because a budget that is one call behind is not a budget.
    """

    def __init__(self, store: Ledger) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._settings: dict[str, tuple[float, Any]] = {}
        self._spent: dict[str, tuple[float, int]] = {}
        self._tokens: dict[str, tuple[float, int]] = {}

    def __getattr__(self, name: str) -> Any:
        """Everything else — `close`, a test's own `calls` — belongs to the ledger underneath."""
        return getattr(self._store, name)

    def _fresh(self, kept: dict[str, tuple[float, Any]], key: str) -> tuple[bool, Any]:
        with self._lock:
            found = kept.get(key)
        if found is None or time.monotonic() - found[0] >= REMEMBER_SECONDS:
            return False, None
        return True, found[1]

    def _keep(self, kept: dict[str, tuple[float, Any]], key: str, value: Any) -> None:
        with self._lock:
            kept[key] = (time.monotonic(), value)

    def setting(self, key: str, default: Any = None) -> Any:
        # The default is applied here rather than remembered, so two callers asking for the same key
        # with different defaults each get their own.
        known, value = self._fresh(self._settings, key)
        if not known:
            value = self._store.setting(key, None)
            self._keep(self._settings, key, value)
        return default if value is None else value

    def save_setting(self, key: str, value: Any) -> None:
        self._store.save_setting(key, value)
        with self._lock:
            self._settings.pop(key, None)

    def calls_today(self, lane_id: str) -> int:
        known, value = self._fresh(self._spent, lane_id)
        if not known:
            value = self._store.calls_today(lane_id)
            self._keep(self._spent, lane_id, value)
        return int(value)

    def tokens_today(self, lane_id: str) -> int:
        known, value = self._fresh(self._tokens, lane_id)
        if not known:
            value = self._store.tokens_today(lane_id)
            self._keep(self._tokens, lane_id, value)
        return int(value)

    def record(self, **line: Any) -> None:
        self._store.record(**line)
        with self._lock:
            lane = str(line.get("lane") or "")
            self._spent.pop(lane, None)
            self._tokens.pop(lane, None)

    def forget(self) -> None:
        """Everything, now — for a test, and for anything that changed the database behind us."""
        with self._lock:
            self._settings.clear()
            self._spent.clear()
            self._tokens.clear()


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

    def tokens_today(self, lane_id: str) -> int:
        today = datetime.now(timezone.utc).date()
        return sum(int(c.get("tokens_in") or 0) + int(c.get("tokens_out") or 0)
                   for c in self.calls if c["lane"] == lane_id and c["at"].date() == today)

    def record(self, **line: Any) -> None:
        with self._lock:
            self.calls.append({**line, "at": datetime.now(timezone.utc)})
