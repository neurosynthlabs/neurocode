"""The Workbench's data view, over HTTP, against data files the test wrote.

Every figure asserted here is known because the test made the file: a CSV of cities, the same rows as TSV,
Parquet and JSON Lines, and a SQLite database with two tables. The roots setting is pointed at a temporary
folder, and a secret sits beside it, outside, so the SQL guard is tested against a file that really exists.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import duckdb
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.models import Project
from app.services import dataview, machine
from app.settings import settings as real_settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ADMIN = {"email": "admin@example.com", "name": "Admin", "password": "another long passphrase", "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}

#: city, population, founded, coastal — one null population, one null city.
CITIES = [
    ("Mumbai", 12442373, "1507-01-01", True),
    ("Delhi", 11034555, "1639-06-15", False),
    ("Chennai", 4646732, "1639-08-22", True),
    ("Pune", None, "1700-03-01", False),
    ("Kochi", 677381, "1800-05-10", True),
    (None, 1000, "1900-01-01", False),
]


def _csv(rows: list[tuple], sep: str = ",") -> str:
    out = [sep.join(["city", "population", "founded", "coastal"])]
    for city, pop, founded, coastal in rows:
        out.append(sep.join(["" if city is None else city, "" if pop is None else str(pop), founded,
                             "true" if coastal else "false"]))
    return "\n".join(out) + "\n"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    (root / "cities.csv").write_text(_csv(CITIES))
    (root / "cities.tsv").write_text(_csv(CITIES, "\t"))
    (root / "cities.jsonl").write_text("".join(json.dumps(
        {"city": c, "population": p, "founded": f, "coastal": k}) + "\n" for c, p, f, k in CITIES))
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM read_csv('{root / 'cities.csv'}')) TO '{root / 'cities.parquet'}' "
                "(FORMAT parquet)")
    con.close()

    db = sqlite3.connect(root / "shop.sqlite")
    db.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, total REAL, placed DATE)")
    db.execute("CREATE TABLE notes (body TEXT)")
    db.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)",
                   [(i, ["ana", "bo", "cy"][i % 3], float(i * 10), f"2026-0{1 + i % 3}-1{i % 9}")
                    for i in range(1, 31)])
    db.commit()
    db.close()
    (root / "fake.db").write_text("not a database at all")
    (root / "readme.txt").write_text("hello\n")

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.csv").write_text("secret\nhunter2\n")

    configured = real_settings().model_copy(update={"machine_roots": str(root), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    return root


@pytest_asyncio.fixture
async def api(catalogued: AsyncSession):
    session = catalogued
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


# ── every format opens with its columns and row count ────────────
@pytest.mark.parametrize("name, fmt", [("cities.csv", "csv"), ("cities.tsv", "tsv"),
                                       ("cities.parquet", "parquet"), ("cities.jsonl", "jsonl")])
async def test_each_tabular_format_opens_with_columns_rows_and_size(client: AsyncClient, tree: Path, name: str,
                                                                   fmt: str):
    body = (await client.get("/data/open", params={"path": str(tree / name)})).json()
    assert body["format"] == fmt and body["name"] == name and body["path"] == str((tree / name).resolve())
    assert body["size"] == (tree / name).stat().st_size and body["rows"] == len(CITIES)
    assert body["tables"] is None and body["table"] is None
    kinds = {c["name"]: c["kind"] for c in body["columns"]}
    assert kinds == {"city": "text", "population": "number", "founded": "date", "coastal": "bool"}


async def test_a_sqlite_file_lists_its_tables_and_opens_the_one_asked(client: AsyncClient, tree: Path):
    path = str(tree / "shop.sqlite")
    body = (await client.get("/data/open", params={"path": path})).json()
    assert body["format"] == "sqlite" and body["formatName"] == "SQLite"
    assert body["tables"] == [{"name": "notes", "kind": "table", "rows": 0},
                              {"name": "orders", "kind": "table", "rows": 30}]
    assert body["table"] == "notes" and body["rows"] == 0            # the first, in name order

    orders = (await client.get("/data/open", params={"path": path, "table": "orders"})).json()
    assert orders["table"] == "orders" and orders["rows"] == 30
    assert [(c["name"], c["type"], c["kind"]) for c in orders["columns"]] == [
        ("id", "INTEGER", "number"), ("customer", "TEXT", "text"), ("total", "REAL", "number"),
        ("placed", "DATE", "date")]
    missing = await client.get("/data/open", params={"path": path, "table": "nope"})
    assert missing.status_code == 404 and missing.json()["detail"] == "This database has no table named nope."


async def test_what_is_not_a_data_file_is_refused_in_words(client: AsyncClient, tree: Path):
    fake = await client.get("/data/open", params={"path": str(tree / "fake.db")})
    assert fake.status_code == 415 and fake.json()["detail"] == "fake.db is not a SQLite database."
    text = await client.get("/data/open", params={"path": str(tree / "readme.txt")})
    assert text.status_code == 415 and "is not a data file this view opens" in text.json()["detail"]
    assert (await client.get("/data/open", params={"path": str(tree / "gone.csv")})).status_code == 404
    outside = await client.get("/data/open", params={"path": str(tree.parent / "elsewhere" / "secret.csv")})
    assert outside.status_code == 403 and "outside the folders this server opens" in outside.json()["detail"]


# ── paging and sorting on the server ─────────────────────────────
async def test_rows_are_paged_in_file_order(client: AsyncClient, tree: Path):
    body = (await client.get("/data/rows", params={"path": str(tree / "cities.csv"), "limit": 2,
                                                   "offset": 2})).json()
    assert body["total"] == 6 and body["offset"] == 2 and body["limit"] == 2
    assert [c["name"] for c in body["columns"]] == ["city", "population", "founded", "coastal"]
    assert body["rows"] == [["Chennai", 4646732, "1639-08-22", True], ["Pune", None, "1700-03-01", False]]
    past = (await client.get("/data/rows", params={"path": str(tree / "cities.csv"), "offset": 100})).json()
    assert past["rows"] == [] and past["total"] == 6
    too_many = await client.get("/data/rows", params={"path": str(tree / "cities.csv"), "limit": 501})
    assert too_many.status_code == 422


async def test_rows_sort_by_a_column_with_nulls_last_both_ways(client: AsyncClient, tree: Path):
    for name in ("cities.csv", "cities.parquet", "cities.jsonl"):
        up = (await client.get("/data/rows", params={"path": str(tree / name), "sort": "population"})).json()
        assert [r[1] for r in up["rows"]] == [1000, 677381, 4646732, 11034555, 12442373, None], name
        down = (await client.get("/data/rows", params={"path": str(tree / name), "sort": "population",
                                                       "desc": True, "limit": 2})).json()
        assert [r[0] for r in down["rows"]] == ["Mumbai", "Delhi"] and down["desc"] is True
    bad = await client.get("/data/rows", params={"path": str(tree / "cities.csv"), "sort": "x\" ; DROP"})
    assert bad.status_code == 400 and bad.json()["detail"] == 'There is no column named x" ; DROP.'


async def test_sqlite_rows_page_and_sort_within_a_table(client: AsyncClient, tree: Path):
    body = (await client.get("/data/rows", params={"path": str(tree / "shop.sqlite"), "table": "orders",
                                                   "sort": "total", "desc": True, "limit": 3})).json()
    assert body["table"] == "orders" and body["total"] == 30
    assert [r[0] for r in body["rows"]] == [30, 29, 28] and body["rows"][0][2] == 300.0


# ── statistics ───────────────────────────────────────────────────
async def test_stats_are_exact_per_column(client: AsyncClient, tree: Path):
    body = (await client.get("/data/stats", params={"path": str(tree / "cities.csv")})).json()
    assert body["rows"] == 6 and body["capped"] is False and body["ms"] >= 0
    by = {c["name"]: c for c in body["columns"]}
    pop = by["population"]
    assert pop["nulls"] == 1 and pop["distinct"] == 5 and pop["distinctApprox"] is False
    assert pop["min"] == 1000 and pop["max"] == 12442373
    assert pop["mean"] == pytest.approx((12442373 + 11034555 + 4646732 + 677381 + 1000) / 5)
    city = by["city"]
    assert city["nulls"] == 1 and city["distinct"] == 5 and city["min"] == "Chennai" and city["max"] == "Pune"
    assert city["mean"] is None
    assert by["founded"]["min"] == "1507-01-01" and by["coastal"]["distinct"] == 2


async def test_sqlite_stats_for_a_table(client: AsyncClient, tree: Path):
    body = (await client.get("/data/stats", params={"path": str(tree / "shop.sqlite"), "table": "orders"})).json()
    by = {c["name"]: c for c in body["columns"]}
    assert body["rows"] == 30 and body["table"] == "orders"
    assert by["total"]["min"] == 10.0 and by["total"]["max"] == 300.0 and by["total"]["mean"] == 155.0
    assert by["customer"]["distinct"] == 3 and by["customer"]["mean"] is None and by["id"]["nulls"] == 0


# ── charts ───────────────────────────────────────────────────────
async def test_a_number_column_plots_as_a_histogram(client: AsyncClient, tree: Path):
    body = (await client.get("/data/plot", params={"path": str(tree / "shop.sqlite"), "table": "orders",
                                                   "column": "total", "bins": 3})).json()
    assert body["kind"] == "histogram" and body["nulls"] == 0 and body["total"] == 30
    assert [b["count"] for b in body["bins"]] == [10, 10, 10]
    assert body["bins"][0]["lo"] == 10.0 and body["bins"][-1]["hi"] == pytest.approx(300.0)

    csv = (await client.get("/data/plot", params={"path": str(tree / "cities.csv"), "column": "population",
                                                  "bins": 2})).json()
    assert [b["count"] for b in csv["bins"]] == [3, 2] and csv["nulls"] == 1


async def test_a_text_column_plots_its_top_values(client: AsyncClient, tree: Path):
    body = (await client.get("/data/plot", params={"path": str(tree / "shop.sqlite"), "table": "orders",
                                                   "column": "customer"})).json()
    assert body["kind"] == "top" and body["distinct"] == 3 and body["other"] == 0
    assert body["values"] == [{"value": "ana", "count": 10}, {"value": "bo", "count": 10},
                              {"value": "cy", "count": 10}]
    coastal = (await client.get("/data/plot", params={"path": str(tree / "cities.parquet"),
                                                      "column": "coastal"})).json()
    assert coastal["kind"] == "top" and {v["value"]: v["count"] for v in coastal["values"]} == {"true": 3, "false": 3}


async def test_a_date_column_plots_as_a_line(client: AsyncClient, tree: Path):
    body = (await client.get("/data/plot", params={"path": str(tree / "shop.sqlite"), "table": "orders",
                                                   "column": "placed"})).json()
    assert body["kind"] == "line" and body["unit"] == "day" and body["y"] is None
    assert sum(p["value"] for p in body["points"]) == 30
    months = (await client.get("/data/plot", params={"path": str(tree / "cities.csv"),
                                                     "column": "founded"})).json()
    assert months["kind"] == "line" and months["unit"] == "year" and len(months["points"]) == 5
    assert months["points"][0]["t"].startswith("1507-01-01")
    mean = (await client.get("/data/plot", params={"path": str(tree / "cities.csv"), "column": "founded",
                                                   "y": "population"})).json()
    assert mean["y"] == "population" and mean["points"][0]["value"] == 12442373
    wrong = await client.get("/data/plot", params={"path": str(tree / "cities.csv"), "column": "city",
                                                   "y": "population"})
    assert wrong.status_code == 400


# ── the SQL box ──────────────────────────────────────────────────
async def test_a_select_runs_over_the_file_as_data(client: AsyncClient, tree: Path):
    for name in ("cities.csv", "cities.tsv", "cities.parquet", "cities.jsonl"):
        body = (await client.post("/data/query", json={
            "path": str(tree / name),
            "sql": "WITH c AS (SELECT * FROM data WHERE coastal) SELECT city, population FROM c ORDER BY city;"})).json()
        assert body["columns"][0]["name"] == "city" and body["truncated"] is False, name
        assert body["rows"] == [["Chennai", 4646732], ["Kochi", 677381], ["Mumbai", 12442373]], name


async def test_a_query_is_capped_and_says_so(client: AsyncClient, tree: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dataview, "MAX_QUERY_ROWS", 4)
    body = (await client.post("/data/query", json={"path": str(tree / "cities.csv"),
                                                   "sql": "SELECT * FROM data"})).json()
    assert len(body["rows"]) == 4 and body["truncated"] is True and body["cap"] == 4
    lite = (await client.post("/data/query", json={"path": str(tree / "shop.sqlite"),
                                                   "sql": "SELECT id FROM orders"})).json()
    assert len(lite["rows"]) == 4 and lite["truncated"] is True


@pytest.mark.parametrize("sql, status, words", [
    ("SELECT * FROM read_csv('{secret}')", 403, "reads only this file"),
    ("SELECT * FROM read_csv('/etc/passwd')", 403, "reads only this file"),
    ("SELECT * FROM '{secret}'", 403, "reads only this file"),
    ("SELECT * FROM read_text('https://example.com/x')", 403, "reads only this file"),
    ("COPY data TO '{out}'", 400, "Only a SELECT"),
    ("CREATE TABLE t AS SELECT 1", 400, "Only a SELECT"),
    ("SET enable_external_access = true", 400, "Only a SELECT"),
    ("ATTACH '{out}.db'", 400, "Only a SELECT"),
    ("SELECT 1; SELECT 2", 400, "one statement at a time"),
    ("SELECT 1; COPY data TO '{out}'", 400, "one statement at a time"),
])
async def test_the_duckdb_guard_refuses_everything_but_one_select_over_the_file(
        client: AsyncClient, tree: Path, sql: str, status: int, words: str):
    secret = tree.parent / "elsewhere" / "secret.csv"
    out = tree / "leaked.csv"
    answer = await client.post("/data/query", json={"path": str(tree / "cities.csv"),
                                                    "sql": sql.format(secret=secret, out=out)})
    assert answer.status_code == status and words in answer.json()["detail"], answer.json()
    assert "hunter2" not in answer.text
    assert not out.exists() and not Path(f"{out}.db").exists()


@pytest.mark.parametrize("sql, status, words", [
    ("DELETE FROM orders", 400, "Only a SELECT"),
    ("SELECT 1; DELETE FROM orders", 400, "one statement at a time"),
    ("WITH x AS (SELECT 1) DELETE FROM orders", 403, "only reads"),
    ("ATTACH '{out}' AS other", 400, "Only a SELECT"),
    ("SELECT * FROM pragma_table_info('orders')", 403, "only reads"),
])
async def test_the_sqlite_guard_allows_only_reading(client: AsyncClient, tree: Path, sql: str, status: int,
                                                    words: str):
    out = tree / "other.sqlite"
    answer = await client.post("/data/query", json={"path": str(tree / "shop.sqlite"), "sql": sql.format(out=out)})
    assert answer.status_code == status and words in answer.json()["detail"], answer.json()
    assert not out.exists()
    count = sqlite3.connect(tree / "shop.sqlite").execute("SELECT count(*) FROM orders").fetchone()[0]
    assert count == 30

    ok = (await client.post("/data/query", json={
        "path": str(tree / "shop.sqlite"),
        "sql": "SELECT customer, count(*) AS n FROM orders GROUP BY customer ORDER BY customer"})).json()
    assert ok["columns"] == [{"name": "customer", "type": ""}, {"name": "n", "type": ""}]
    assert ok["rows"] == [["ana", 10], ["bo", 10], ["cy", 10]]


async def test_a_query_past_its_time_limit_is_stopped(client: AsyncClient, tree: Path,
                                                      monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dataview, "QUERY_SECONDS", 0.2)
    slow = "SELECT count(*) FROM range(1000000000) a, range(1000) b WHERE a.range + b.range = -1"
    answer = await client.post("/data/query", json={"path": str(tree / "cities.csv"), "sql": slow})
    assert answer.status_code == 408 and "took longer than 0.2 seconds" in answer.json()["detail"]
    lite = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n")
    answer = await client.post("/data/query", json={"path": str(tree / "shop.sqlite"), "sql": lite})
    assert answer.status_code == 408


async def test_a_syntax_error_answers_with_the_database_words(client: AsyncClient, tree: Path):
    answer = await client.post("/data/query", json={"path": str(tree / "cities.csv"), "sql": "SELEC 1"})
    assert answer.status_code == 400 and "syntax error" in answer.json()["detail"].lower()


# ── project paths, who may, and a server without machine access ──
async def test_a_project_path_opens_inside_that_project(client: AsyncClient, tree: Path, session: AsyncSession):
    session.add(Project(id="data-lab", name="Data Lab", source_kind="local", source_repo=str(tree)))
    await session.flush()
    body = (await client.get("/data/open", params={"path": "cities.csv", "projectId": "data-lab"})).json()
    assert body["path"] == str((tree / "cities.csv").resolve()) and body["rows"] == 6
    escape = await client.get("/data/open", params={"path": "../elsewhere/secret.csv", "projectId": "data-lab"})
    assert escape.status_code in (400, 403)
    missing = await client.get("/data/open", params={"path": "cities.csv", "projectId": "nope"})
    assert missing.status_code == 404


async def test_only_a_holder_of_machine_access_reads_data(api, client: AsyncClient, tree: Path,
                                                          monkeypatch: pytest.MonkeyPatch):
    assert (await client.post("/admin/users", json=ADMIN)).status_code in (200, 201)
    async with _client(api) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        for answer in (await admin.get("/data/open", params={"path": str(tree / "cities.csv")}),
                       await admin.post("/data/query", json={"path": str(tree / "cities.csv"),
                                                             "sql": "SELECT 1"})):
            assert answer.status_code == 403 and "machine:access" in answer.json()["detail"]
    async with _client(api) as stranger:
        assert (await stranger.get("/data/rows", params={"path": str(tree / "cities.csv")})).status_code == 401

    off = real_settings().model_copy(update={"machine_roots": str(tree), "machine_access": False})
    monkeypatch.setattr(machine, "settings", lambda: off)
    answer = await client.get("/data/stats", params={"path": str(tree / "cities.csv")})
    assert answer.status_code == 404 and answer.json()["detail"] == "Machine access is off on this server."


async def test_a_file_that_is_not_utf8_says_why(client: AsyncClient, tree: Path):
    (tree / "latin.csv").write_bytes("name,n\ncafé,1\n".encode("latin-1"))
    answer = await client.get("/data/open", params={"path": str(tree / "latin.csv")})
    assert answer.status_code == 422
    assert answer.json()["detail"].startswith("latin.csv could not be read as CSV:")
    assert "not utf-8 encoded" in answer.json()["detail"]
