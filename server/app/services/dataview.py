"""Data files as the Workbench shows them: CSV, TSV, Parquet, JSON Lines and SQLite.

A data file is not read into the browser. The server answers the questions the grid asks — a page of
rows in some order, the statistics of every column, a chart of one column, a query — and the page shows
the answers. Tabular files go through DuckDB, which reads them where they are and streams; SQLite files
go through Python's own sqlite3, opened read-only.

The file is on the person's machine, so the machine's rules hold: `machine.inside` is the one door from
a request to a path (roots, `realpath`, links followed), and a project path is first turned into a file
by `code.locate`, the resolver the rest of the product uses. Nothing here writes.

The SQL box is the part that needs care, because a query language can do more than select:

* **DuckDB.** The file becomes a view named `data`, and then external access is switched off — with the
  file itself as the only allowed path — and the configuration is locked. A query can read the view and
  nothing else: `read_csv('/etc/passwd')`, another file, a URL, `COPY … TO`, `ATTACH` and `INSTALL` all
  fail inside DuckDB, and no statement can switch the setting back. Only one statement is taken, and it
  must be a SELECT (a `WITH … SELECT` is one).
* **SQLite.** The file is opened with `mode=ro`, and an authorizer allows only reading and functions —
  no writes, no ATTACH, no PRAGMA — so even a statement that got past the first-keyword check is refused
  by SQLite itself.

Every call has a time limit (DuckDB is interrupted, SQLite's progress handler stops it) and every answer
a ceiling on rows, so a query over a large file cannot hold the server.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import duckdb
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.work import ProjectRepository
from . import code as code_service
from . import machine
from .errors import Refused

#: What each extension is read as.
FORMATS = {".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".parquet": "parquet", ".pq": "parquet",
           ".jsonl": "jsonl", ".ndjson": "jsonl", ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite"}
#: How each format is named on screen.
FORMAT_NAMES = {"csv": "CSV", "tsv": "TSV", "parquet": "Parquet", "jsonl": "JSON Lines", "sqlite": "SQLite"}
#: The first bytes of every SQLite database; a `.db` without them is some other kind of file.
SQLITE_MAGIC = b"SQLite format 3\x00"

#: A page of the grid, at most.
MAX_PAGE = 500
#: The rows a query hands back, at most; `truncated` says there were more.
MAX_QUERY_ROWS = 1000
#: A query's text, at most.
MAX_SQL = 20_000
#: One cell's text, at most, in characters — a column of documents is still a grid.
MAX_CELL = 2_000
#: The columns whose statistics are worked out; a wider file says the rest were left out.
MAX_STAT_COLUMNS = 200
#: The tables a SQLite file lists, at most.
MAX_TABLES = 500
#: Above this many rows, distinct counts are DuckDB's estimate (said so), not an exact count.
EXACT_DISTINCT_ROWS = 1_000_000
#: Bars in a histogram, values in a top-values chart, points on a line.
MAX_BINS = 60
TOP_VALUES = 20
MAX_POINTS = 400
#: Seconds a query may take, and the grid's own questions (a page, the statistics, a chart).
QUERY_SECONDS = 15.0
READ_SECONDS = 30.0
#: DuckDB's own ceilings for one connection, so one large file cannot take the machine.
DUCK_THREADS = 2
DUCK_MEMORY = "1GB"
#: How many data questions run at once; the next waits this long for a turn, then hears the server is busy.
MAX_BUSY = 3
WAIT_SECONDS = 20.0

_turns = threading.BoundedSemaphore(MAX_BUSY)

NUMERIC = re.compile(r"^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|UHUGEINT|"
                     r"FLOAT|DOUBLE|REAL|DECIMAL.*|NUMERIC.*|INT.*)$")
TEMPORAL = re.compile(r"^(DATE|TIMESTAMP.*)$")


@dataclass(frozen=True)
class DataFile:
    path: Path
    format: str
    size: int
    modified: float


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    #: What the screen does with it: number, date, bool, text, or other (lists, structs, blobs).
    kind: str

    def json(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "kind": self.kind}


# ── finding the file ─────────────────────────────────────────────
def _duck_kind(type_: str) -> str:
    t = type_.upper()
    if NUMERIC.match(t):
        return "number"
    if TEMPORAL.match(t):
        return "date"
    if t == "BOOLEAN":
        return "bool"
    if t in ("VARCHAR", "UUID", "TIME", "INTERVAL") or t.startswith("ENUM"):
        return "text"
    return "other"


def _sqlite_kind(declared: str, seen: str | None) -> str:
    """SQLite's type affinity rules, applied to the declared type; with none declared, what the first
    stored value is."""
    t = declared.upper()
    if not t:
        return {"integer": "number", "real": "number", "text": "text", "blob": "other"}.get(seen or "", "text")
    if "DATE" in t or "TIME" in t:
        return "date"
    if "BOOL" in t:
        return "bool"
    if "INT" in t or any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "number"
    if "BLOB" in t:
        return "other"
    return "text"


def format_of(path: Path) -> str:
    found = FORMATS.get(path.suffix.lower())
    if found is None:
        names = ", ".join(sorted(FORMATS))
        raise Refused(f"{path.name} is not a data file this view opens ({names}).", status=415)
    return found


def open_file(path: str) -> DataFile:
    """An absolute path (or `~`) as a data file inside the roots. Blocking."""
    real = machine.inside(path)
    try:
        st = os.stat(real)
    except FileNotFoundError as gone:
        raise Refused(f"{path} does not exist.", status=404) from gone
    except PermissionError as denied:
        raise machine.unreadable(real.parent, denied) from denied
    if not stat.S_ISREG(st.st_mode):
        raise Refused(f"{real} is not a file.", status=409)
    kind = format_of(real)
    if kind == "sqlite":
        try:
            with open(real, "rb") as head:
                magic = head.read(len(SQLITE_MAGIC))
        except PermissionError as denied:
            raise machine.unreadable(real.parent, denied) from denied
        if st.st_size and magic != SQLITE_MAGIC:
            raise Refused(f"{real.name} is not a SQLite database.", status=415)
    return DataFile(path=real, format=kind, size=st.st_size, modified=st.st_mtime)


async def resolve(session: AsyncSession, path: str, project_id: str | None) -> str:
    """What a request names, as a path `open_file` takes: an absolute path as it is, a project path
    (label-prefixed for a further source) through the project's own resolver."""
    text = (path or "").strip()
    if text.startswith(("/", "~")) or not project_id:
        return text
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        raise Refused(f"There is no project {project_id}.", status=404)
    found = await code_service.locate(session, project, text)
    if found is None:
        raise Refused(f"{text} is in code that is not on this machine, so it cannot be opened.", status=404)
    return str(found)


# ── turning answers into JSON ────────────────────────────────────
def cell(value: Any) -> Any:
    """One value as JSON the grid can show: exact where JSON can carry it, words where it cannot (an
    integer past 2^53 would be rounded by the browser, NaN is not JSON, bytes are not text)."""
    if value is None or isinstance(value, (bool, str)):
        return value[:MAX_CELL] if isinstance(value, str) else value
    if isinstance(value, int):
        return value if abs(value) <= 2**53 else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return f"<{len(raw)} bytes> {raw[:24].hex()}"
    if isinstance(value, (dt.timedelta, uuid.UUID)):
        return str(value)
    return json.dumps(value, default=str, ensure_ascii=False)[:MAX_CELL]


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value if abs(value) <= 2**53 else float(value)
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _first_word(sql: str) -> str:
    """The statement's first keyword, past comments and brackets."""
    text = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).lstrip(" \t\r\n(")
    word = re.match(r"[A-Za-z]+", text)
    return word.group(0).upper() if word else ""


def _cut(error: Exception) -> str:
    """A database's message: its first paragraph, on one line, without the echo of the offending line or
    the caret drawing — DuckDB's CSV errors put the reason ("not utf-8 encoded") on the third line."""
    head = str(error).strip().split("\n\n")[0]
    lines = [x.strip() for x in head.split("\n") if x.strip() and not x.startswith(("Original Line", "LINE "))
             and not x.strip().startswith("^")]
    return " ".join(lines)[:300]


@contextmanager
def _turn() -> Iterator[None]:
    if not _turns.acquire(timeout=WAIT_SECONDS):
        raise Refused("The server is busy reading other data files. Try again in a moment.", status=429)
    try:
        yield
    finally:
        _turns.release()


# ── DuckDB: CSV, TSV, Parquet, JSON Lines ───────────────────────
def _reader(file: DataFile) -> str:
    where = _literal(str(file.path))
    return {
        "csv": f"read_csv({where}, auto_detect = true)",
        "tsv": f"read_csv({where}, auto_detect = true, delim = '\\t')",
        "parquet": f"read_parquet({where})",
        "jsonl": f"read_json({where}, format = 'newline_delimited')",
    }[file.format]


@contextmanager
def _duck(file: DataFile) -> Iterator[duckdb.DuckDBPyConnection]:
    """A private in-memory DuckDB whose only way out is the file itself, locked that way."""
    with _turn():
        con = duckdb.connect(":memory:", config={"threads": DUCK_THREADS, "memory_limit": DUCK_MEMORY,
                                                 "autoinstall_known_extensions": False,
                                                 "autoload_known_extensions": False})
        try:
            try:
                con.execute(f"CREATE VIEW data AS SELECT * FROM {_reader(file)}")
            except duckdb.Error as failed:
                raise Refused(f"{file.path.name} could not be read as {FORMAT_NAMES[file.format]}: "
                              f"{_cut(failed)}", status=422) from failed
            con.execute(f"SET allowed_paths = [{_literal(str(file.path))}]")
            con.execute("SET enable_external_access = false")
            con.execute("SET lock_configuration = true")
            yield con
        finally:
            con.close()


@contextmanager
def _deadline(stop: Callable[[], None], seconds: float) -> Iterator[threading.Event]:
    """Calls `stop` if the block is still running after `seconds`; the event says it did."""
    fired = threading.Event()

    def ring() -> None:
        fired.set()
        stop()

    timer = threading.Timer(seconds, ring)
    timer.daemon = True
    timer.start()
    try:
        yield fired
    finally:
        timer.cancel()


def _duck_run(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None, *,
              seconds: float = READ_SECONDS, many: int | None = None) -> tuple[list[Any], list[tuple]]:
    """A statement under a time limit: its description and its rows (at most `many` when given)."""
    with _deadline(con.interrupt, seconds) as fired:
        try:
            cur = con.execute(sql, params or [])
            rows = cur.fetchmany(many) if many is not None else cur.fetchall()
            return list(cur.description or []), rows
        except duckdb.Error as failed:
            if fired.is_set():
                raise Refused(f"The query took longer than {seconds:g} seconds and was stopped.",
                              status=408) from failed
            if isinstance(failed, duckdb.PermissionException):
                raise Refused("A query here reads only this file, as the view `data`. Other files, URLs "
                              "and extensions are closed to it.", status=403) from failed
            raise Refused(_cut(failed), status=400) from failed


def _duck_columns(con: duckdb.DuckDBPyConnection) -> list[Column]:
    _, rows = _duck_run(con, "DESCRIBE data")
    return [Column(name=r[0], type=str(r[1]), kind=_duck_kind(str(r[1]))) for r in rows]


# ── SQLite ───────────────────────────────────────────────────────
_READING = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
            getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def _only_reading(action: int, *_: Any) -> int:
    return sqlite3.SQLITE_OK if action in _READING else sqlite3.SQLITE_DENY


@contextmanager
def _lite(file: DataFile) -> Iterator[sqlite3.Connection]:
    with _turn():
        try:
            con = sqlite3.connect(f"file:{quote(str(file.path))}?mode=ro", uri=True, check_same_thread=False)
        except sqlite3.Error as failed:
            raise Refused(f"{file.path.name} could not be opened: {_cut(failed)}", status=422) from failed
        try:
            yield con
        finally:
            con.close()


def _lite_run(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = (), *,
              seconds: float = READ_SECONDS, many: int | None = None) -> tuple[list[Any], list[tuple]]:
    ends = time.monotonic() + seconds
    stopped = threading.Event()

    def check() -> int:
        if time.monotonic() > ends:
            stopped.set()
            return 1
        return 0

    con.set_progress_handler(check, 10_000)
    try:
        cur = con.execute(sql, params)
        rows = cur.fetchmany(many) if many is not None else cur.fetchall()
        return list(cur.description or []), rows
    except sqlite3.ProgrammingError as failed:
        if "one statement" in str(failed):
            raise Refused("Run one statement at a time.", status=400) from failed
        raise Refused(_cut(failed), status=400) from failed
    except sqlite3.Error as failed:
        if stopped.is_set():
            raise Refused(f"The query took longer than {seconds:g} seconds and was stopped.",
                          status=408) from failed
        if "not authorized" in str(failed):
            raise Refused("A query here only reads: no writes, ATTACH or PRAGMA.", status=403) from failed
        if isinstance(failed, sqlite3.DatabaseError) and "not a database" in str(failed):
            raise Refused("This file is not a SQLite database.", status=415) from failed
        raise Refused(_cut(failed), status=400) from failed
    finally:
        con.set_progress_handler(None, 0)


def _lite_tables(con: sqlite3.Connection) -> list[dict[str, Any]]:
    _, rows = _lite_run(con, "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                             "AND name NOT LIKE 'sqlite_%' ORDER BY type, name")
    out = []
    for name, kind in rows[:MAX_TABLES]:
        _, counted = _lite_run(con, f"SELECT count(*) FROM {_ident(name)}")
        out.append({"name": name, "kind": kind, "rows": counted[0][0]})
    return out


def _lite_table(con: sqlite3.Connection, table: str | None) -> str:
    """The table asked for, or the first one; refused when the file has none or not that one."""
    _, rows = _lite_run(con, "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                             "AND name NOT LIKE 'sqlite_%' ORDER BY type, name")
    names = [r[0] for r in rows]
    if not names:
        raise Refused("This database has no tables yet.", status=404)
    if table is None:
        return names[0]
    if table not in names:
        raise Refused(f"This database has no table named {table}.", status=404)
    return table


def _lite_columns(con: sqlite3.Connection, table: str) -> list[Column]:
    _, info = _lite_run(con, f"SELECT name, type FROM pragma_table_info({_literal(table)})")
    out = []
    for name, declared in info:
        seen = None
        if not declared:
            _, first = _lite_run(con, f"SELECT typeof({_ident(name)}) FROM {_ident(table)} "
                                      f"WHERE {_ident(name)} IS NOT NULL LIMIT 1")
            seen = first[0][0] if first else None
        out.append(Column(name=name, type=declared or (seen or "").upper(), kind=_sqlite_kind(declared, seen)))
    return out


# ── the questions the screen asks ────────────────────────────────
def _base(file: DataFile) -> dict[str, Any]:
    return {"path": str(file.path), "name": file.path.name, "format": file.format,
            "formatName": FORMAT_NAMES[file.format], "size": file.size,
            "modified": dt.datetime.fromtimestamp(file.modified, dt.UTC).isoformat()}


def describe(path: str, table: str | None = None) -> dict[str, Any]:
    """The file: its format, size, columns and row count; for SQLite also its tables, and which one
    these columns are."""
    file = open_file(path)
    if file.format == "sqlite":
        with _lite(file) as con:
            tables = _lite_tables(con)
            if not tables:
                return {**_base(file), "tables": [], "table": None, "columns": [], "rows": 0}
            name = _lite_table(con, table)
            rows = next(t["rows"] for t in tables if t["name"] == name)
            return {**_base(file), "tables": tables, "table": name,
                    "columns": [c.json() for c in _lite_columns(con, name)], "rows": rows}
    with _duck(file) as con:
        columns = _duck_columns(con)
        _, counted = _duck_run(con, "SELECT count(*) FROM data")
        return {**_base(file), "tables": None, "table": None, "columns": [c.json() for c in columns],
                "rows": counted[0][0]}


def rows(path: str, *, table: str | None = None, offset: int = 0, limit: int = 100,
         sort: str | None = None, desc: bool = False) -> dict[str, Any]:
    """One page of rows, sorted on the server by one column (nulls last) when asked."""
    file = open_file(path)
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    if file.format == "sqlite":
        with _lite(file) as con:
            name = _lite_table(con, table)
            columns = _lite_columns(con, name)
            order = _order(columns, sort, desc)
            _, counted = _lite_run(con, f"SELECT count(*) FROM {_ident(name)}")
            _, page = _lite_run(con, f"SELECT * FROM {_ident(name)}{order} LIMIT ? OFFSET ?", (limit, offset))
            total = counted[0][0]
    else:
        with _duck(file) as con:
            columns = _duck_columns(con)
            order = _order(columns, sort, desc)
            _, counted = _duck_run(con, "SELECT count(*) FROM data")
            _, page = _duck_run(con, f"SELECT * FROM data{order} LIMIT ? OFFSET ?", [limit, offset])
            total = counted[0][0]
            name = None
    return {"table": name, "columns": [c.json() for c in columns],
            "rows": [[cell(v) for v in r] for r in page], "offset": offset, "limit": limit, "total": total,
            "sort": sort, "desc": desc if sort else False}


def _order(columns: list[Column], sort: str | None, desc: bool) -> str:
    if not sort:
        return ""
    if sort not in {c.name for c in columns}:
        raise Refused(f"There is no column named {sort}.", status=400)
    return f" ORDER BY {_ident(sort)} {'DESC' if desc else 'ASC'} NULLS LAST"


def stats(path: str, *, table: str | None = None) -> dict[str, Any]:
    """Every column's type, nulls, distinct values, min, max, and mean where it is a number — worked out
    in one pass by the database, exactly (above a million rows the distinct count is an estimate, and
    says so)."""
    file = open_file(path)
    started = time.monotonic()
    if file.format == "sqlite":
        with _lite(file) as con:
            name = _lite_table(con, table)
            columns = _lite_columns(con, name)
            shown = columns[:MAX_STAT_COLUMNS]
            _, got = _lite_run(con, f"SELECT {_stat_select(shown, approx=False)} FROM {_ident(name)}")
            approx = False
    else:
        name = None
        with _duck(file) as con:
            columns = _duck_columns(con)
            shown = columns[:MAX_STAT_COLUMNS]
            _, counted = _duck_run(con, "SELECT count(*) FROM data")
            approx = counted[0][0] > EXACT_DISTINCT_ROWS
            _, got = _duck_run(con, f"SELECT {_stat_select(shown, approx=approx)} FROM data")
    values = list(got[0])
    total = values[0]
    out = []
    for i, column in enumerate(shown):
        present, distinct, low, high, mean = values[1 + i * 5: 6 + i * 5]
        out.append({**column.json(), "nulls": total - present, "distinct": distinct, "distinctApprox": approx,
                    "min": cell(low), "max": cell(high),
                    "mean": _number(mean) if column.kind == "number" else None})
    return {"table": name, "rows": total, "columns": out, "capped": len(columns) > len(shown),
            "ms": round((time.monotonic() - started) * 1000)}


def _stat_select(columns: list[Column], *, approx: bool) -> str:
    parts = ["count(*)"]
    for c in columns:
        q = _ident(c.name)
        plain = c.kind != "other"
        parts += [f"count({q})",
                  (f"approx_count_distinct({q})" if approx else f"count(DISTINCT {q})") if plain else "NULL",
                  f"min({q})" if plain else "NULL", f"max({q})" if plain else "NULL",
                  f"avg({q})" if c.kind == "number" else "NULL"]
    return ", ".join(parts)


# ── a chart of one column ────────────────────────────────────────
def plot(path: str, column: str, *, table: str | None = None, bins: int = 20,
         y: str | None = None) -> dict[str, Any]:
    """What to draw for one column: a histogram of a number, the most common values of text (or a
    yes/no), a line over a date — the count per day, month or year, or the mean of `y` when named."""
    file = open_file(path)
    bins = max(2, min(bins, MAX_BINS))
    if file.format == "sqlite":
        with _lite(file) as con:
            name = _lite_table(con, table)
            columns = {c.name: c for c in _lite_columns(con, name)}
            target, measure = _plot_columns(columns, column, y)

            def ask(sql: str, params: list[Any]) -> list[tuple]:
                return _lite_run(con, sql.replace("{t}", _ident(name)), tuple(params))[1]

            return _plot(ask, target, measure, bins, sqlite=True)
    with _duck(file) as con:
        columns = {c.name: c for c in _duck_columns(con)}
        target, measure = _plot_columns(columns, column, y)

        def ask(sql: str, params: list[Any]) -> list[tuple]:
            return _duck_run(con, sql.replace("{t}", "data"), params)[1]

        return _plot(ask, target, measure, bins, sqlite=False)


def _plot_columns(columns: dict[str, Column], column: str, y: str | None) -> tuple[Column, Column | None]:
    if column not in columns:
        raise Refused(f"There is no column named {column}.", status=400)
    target = columns[column]
    if target.kind == "other":
        raise Refused(f"{column} holds {target.type or 'values'} that cannot be charted.", status=400)
    measure = None
    if y:
        if y not in columns or columns[y].kind != "number":
            raise Refused(f"{y} is not a number column to average.", status=400)
        if target.kind != "date":
            raise Refused("A second column is averaged only over a date column.", status=400)
        measure = columns[y]
    return target, measure


def _plot(ask: Callable[[str, list[Any]], list[tuple]], target: Column, measure: Column | None, bins: int,
          *, sqlite: bool) -> dict[str, Any]:
    q = _ident(target.name)
    base = {"column": target.name, "type": target.type}
    if target.kind == "number":
        real = f"typeof({q}) IN ('integer', 'real')" if sqlite else f"isfinite({q}::DOUBLE)"
        (lo, hi, n, total), = ask(f"SELECT min({q}), max({q}), count({q}), count(*) FROM {{t}} WHERE {real} "
                                  f"OR {q} IS NULL", [])
        nulls = total - n
        if n == 0:
            return {**base, "kind": "histogram", "bins": [], "nulls": nulls, "total": total}
        lo, hi = float(lo), float(hi)
        if lo == hi:
            return {**base, "kind": "histogram", "bins": [{"lo": lo, "hi": hi, "count": n}], "nulls": nulls,
                    "total": total}
        width = (hi - lo) / bins
        slot = f"CAST(floor((({q}) * 1.0 - ?) / ?) AS INTEGER)"
        found = dict(ask(f"SELECT min({slot}, ?) AS b, count(*) FROM {{t}} WHERE {real} GROUP BY b"
                         if sqlite else
                         f"SELECT least({slot}, ?) AS b, count(*) FROM {{t}} WHERE {real} GROUP BY b",
                         [lo, width, bins - 1]))
        return {**base, "kind": "histogram", "nulls": nulls, "total": total,
                "bins": [{"lo": lo + i * width, "hi": lo + (i + 1) * width, "count": found.get(i, 0)}
                         for i in range(bins)]}
    if target.kind == "date":
        return _line(ask, target, measure, base, sqlite=sqlite)
    shown = f"CAST({q} AS TEXT)" if sqlite else f"CAST({q} AS VARCHAR)"
    (n, total, distinct), = ask(f"SELECT count({q}), count(*), count(DISTINCT {q}) FROM {{t}}", [])
    top = ask(f"SELECT {shown} AS v, count(*) AS c FROM {{t}} WHERE {q} IS NOT NULL GROUP BY v "
              f"ORDER BY c DESC, v LIMIT ?", [TOP_VALUES])
    values = [{"value": cell(v), "count": c} for v, c in top]
    return {**base, "kind": "top", "values": values, "other": n - sum(v["count"] for v in values),
            "distinct": distinct, "nulls": total - n, "total": total}


def _unit(span_days: float) -> str:
    """The period a line is counted over, by how long its dates span: enough points to see a shape,
    never so many that the line is noise."""
    if span_days <= 2:
        return "hour"
    if span_days <= 180:
        return "day"
    if span_days <= 365 * 12:
        return "month"
    return "year"


_SQLITE_PERIOD = {"hour": 13, "day": 10, "month": 7, "year": 4}


def _line(ask: Callable[[str, list[Any]], list[tuple]], target: Column, measure: Column | None,
          base: dict[str, Any], *, sqlite: bool) -> dict[str, Any]:
    q = _ident(target.name)
    value = f"avg({_ident(measure.name)})" if measure else "count(*)"
    if sqlite:
        # SQLite keeps dates as ISO text; a value that is not one (an epoch number, a typo) is left out.
        iso = f"{q} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]*'"
        (lo, hi, n, total), = ask(f"SELECT min({q}), max({q}), count(CASE WHEN {iso} THEN 1 END), count(*) "
                                  f"FROM {{t}}", [])
        if not n:
            return {**base, "kind": "line", "unit": "day", "y": None, "points": [], "nulls": total, "total": total}
        span = (_parse_day(hi) - _parse_day(lo)).days if lo and hi else 0
        unit = _unit(span)
        width = _SQLITE_PERIOD[unit]
        period = f"replace(substr({q}, 1, {width}), ' ', 'T')"
        got = ask(f"SELECT {period} AS p, {value} FROM {{t}} WHERE {iso} GROUP BY p ORDER BY p LIMIT ?",
                  [MAX_POINTS + 1])
    else:
        stamp = f"CAST({q} AS TIMESTAMP)"
        (lo, hi, n, total), = ask(f"SELECT min({stamp}), max({stamp}), count({q}), count(*) FROM {{t}}", [])
        if not n:
            return {**base, "kind": "line", "unit": "day", "y": None, "points": [], "nulls": total, "total": total}
        unit = _unit((hi - lo).total_seconds() / 86400)
        got = ask(f"SELECT date_trunc('{unit}', {stamp}) AS p, {value} FROM {{t}} WHERE {q} IS NOT NULL "
                  f"GROUP BY p ORDER BY p LIMIT ?", [MAX_POINTS + 1])
    return {**base, "kind": "line", "unit": unit, "y": measure.name if measure else None,
            "points": [{"t": cell(p), "value": _number(v)} for p, v in got[:MAX_POINTS]],
            "capped": len(got) > MAX_POINTS, "nulls": total - n, "total": total}


def _parse_day(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except ValueError:
        return dt.date.min


# ── the SQL box ──────────────────────────────────────────────────
def query(path: str, sql: str) -> dict[str, Any]:
    """One read-only statement over the file, at most `MAX_QUERY_ROWS` rows back, under a time limit."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise Refused("Write a query first.", status=400)
    if len(text) > MAX_SQL:
        raise Refused(f"A query is at most {MAX_SQL:,} characters.", status=400)
    file = open_file(path)
    started = time.monotonic()
    if file.format == "sqlite":
        if _first_word(text) not in ("SELECT", "WITH", "VALUES"):
            raise Refused("Only a SELECT (or WITH … SELECT) runs here; this view never changes the file.",
                          status=400)
        with _lite(file) as con:
            con.set_authorizer(_only_reading)
            described, got = _lite_run(con, text, seconds=QUERY_SECONDS, many=MAX_QUERY_ROWS + 1)
            columns = [{"name": d[0], "type": ""} for d in described]
    else:
        try:
            statements = duckdb.extract_statements(text)
        except duckdb.Error as failed:
            raise Refused(_cut(failed), status=400) from failed
        if len(statements) != 1:
            raise Refused("Run one statement at a time.", status=400)
        if statements[0].type != duckdb.StatementType.SELECT:
            raise Refused("Only a SELECT (or WITH … SELECT) runs here; this view never changes the file.",
                          status=400)
        with _duck(file) as con:
            described, got = _duck_run(con, text, seconds=QUERY_SECONDS, many=MAX_QUERY_ROWS + 1)
            columns = [{"name": d[0], "type": str(d[1])} for d in described]
    return {"columns": columns, "rows": [[cell(v) for v in r] for r in got[:MAX_QUERY_ROWS]],
            "truncated": len(got) > MAX_QUERY_ROWS, "cap": MAX_QUERY_ROWS,
            "ms": round((time.monotonic() - started) * 1000)}
