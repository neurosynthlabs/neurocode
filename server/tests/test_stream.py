"""The transport: when a request's writes become true, what the live stream sends, and what it holds.

Most tests here run inside the suite's rolled-back transaction. The ones about *when* something
commits cannot — a commit is the thing under test — so they run against a workspace that is really
committed, and clean it away afterwards.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.ledger import PostgresLedger
from app.api import deps
from app.api.app import create_api
from app.data.engine import Database
from app.data.loader import sync_roles
from app.events import Bus
from app.models import CodeIndexRun, Plan, Project, Run, Task
from app.settings import Settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "stream-owner@example.com",
         "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


# ── a workspace that really commits ──────────────────────────────

@pytest_asyncio.fixture
async def live(settings: Settings, tmp_path: Path) -> AsyncIterator[FastAPI]:
    """The API over a real, committing database and its own bus, with nobody signed up yet."""
    db = Database(url=settings.test_database_url)
    async with db.session() as s:
        await sync_roles(s)
    app = create_api(db=db)
    app.state.settings = settings.model_copy(update={"database_url": settings.test_database_url,
                                                     "backups_dir": tmp_path / "backups"})
    try:
        yield app
    finally:
        async with db.session() as s:
            await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM activity"))
            await s.execute(text("DELETE FROM sessions"))
            await s.execute(text("DELETE FROM user_roles"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM workspace"))
        app.state.ledger.close()
        await db.close()


async def test_a_write_is_committed_before_its_answer_is_sent(live: FastAPI, settings: Settings):
    """The transaction used to commit after the response had gone, so a screen that wrote and read
    straight back — empty the workspace, then reload it — could read the state from before its own
    write. At the moment the answer starts, another connection must already see the row."""
    outside = create_async_engine(settings.test_database_url, poolclass=NullPool)
    seen: list[int] = []

    async def visible() -> int:
        async with outside.connect() as conn:
            return int((await conn.execute(text("SELECT count(*) FROM users WHERE email = :e"),
                                           {"e": OWNER["email"]})).scalar_one())

    async def watched(scope: dict[str, Any], receive: Any, send: Any) -> None:
        async def spy(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and scope.get("path") == "/auth/setup":
                seen.append(await visible())
            await send(message)
        await live(scope, receive, spy)

    try:
        async with AsyncClient(transport=ASGITransport(app=watched), base_url="http://api",
                               headers=HEADERS) as c:
            assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
    finally:
        await outside.dispose()
    assert seen == [1], "the answer was on its way before the account it describes was stored"


async def test_the_stream_holds_no_connection_while_it_is_open(live: FastAPI):
    """Authenticating the stream took a pooled connection and kept it, idle in a transaction, for as
    long as the tab stayed open — so a dozen tabs emptied the pool and REINDEX waited on each."""
    async with AsyncClient(transport=ASGITransport(app=live), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        token = c.cookies.get(deps.COOKIE)
    assert token

    db: Database = live.state.db
    held: list[int] = []
    first_event = asyncio.Event()

    async def receive() -> dict[str, Any]:
        await first_event.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        if message["type"] == "http.response.body" and message.get("body") and not held:
            held.append(db.engine.pool.checkedout())
            first_event.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": "/activity/stream", "raw_path": b"/activity/stream",
             "root_path": "", "query_string": b"", "client": ("127.0.0.1", 50000), "server": ("api", 80),
             "headers": [(b"host", b"api"), (b"cookie", f"{deps.COOKIE}={token}".encode())]}
    streaming = asyncio.create_task(live(scope, receive, send))
    try:
        await asyncio.wait_for(first_event.wait(), timeout=10)
        await asyncio.wait_for(asyncio.shield(streaming), timeout=5)
    except TimeoutError:
        pass
    finally:
        streaming.cancel()
        await asyncio.gather(streaming, return_exceptions=True)
    assert held == [0], f"the open stream is holding {held} pooled connection(s)"


async def test_emptying_the_workspace_tells_every_open_tab_once_it_has_committed(live: FastAPI):
    """Emptying is bulk deletes, so the unit of work saw no documents go and announced nothing: every
    other open tab kept showing the deleted projects as live."""
    heard: list[tuple[str, Any]] = []
    live.state.bus.listen(lambda kind, data: heard.append((kind, data)))
    async with AsyncClient(transport=ASGITransport(app=live), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        assert (await c.post("/admin/reset")).status_code == 400
        assert not [k for k, _ in heard if k == "reset"]          # refused: nothing to tell anyone
        assert (await c.post("/admin/reset", headers={"X-Confirm": "reset"})).status_code == 200
    resets = [data for kind, data in heard if kind == "reset"]
    assert len(resets) == 1 and set(resets[0]) == {"at"}
    assert datetime.fromisoformat(resets[0]["at"]).tzinfo is not None


# ── what a change carries ────────────────────────────────────────

@pytest_asyncio.fixture
async def client(seeded: AsyncSession) -> AsyncIterator[AsyncClient]:
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield seeded

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


def _puts(session: AsyncSession, collection: str) -> list[dict[str, Any]]:
    return [c["doc"] for kind, c in session.info.get("changes", [])
            if kind == "change" and c["op"] == "put" and c["collection"] == collection]


async def test_a_project_announced_is_the_project_listed(client: AsyncClient, seeded: AsyncSession,
                                                         tmp_path: Path):
    """Announced bare, a project lost its task counts and its code index on every change — the card
    read 'Tasks 0' and code search switched itself off right after indexing."""
    seeded.add(CodeIndexRun(project_id="erp", root=str(tmp_path), ms=10, files=3, symbols=4, edges=1,
                            finished_at=datetime(2026, 9, 1, 10, 30).astimezone()))
    await seeded.flush()
    listed = {p["id"]: p for p in (await client.get("/projects")).json()}["erp"]
    assert listed["work"]["tasks"] > 0 and listed.get("codeIndex")

    seeded.info["bus"] = Bus()
    project = await seeded.get(Project, "erp")
    project.understood_pct = 91
    await seeded.flush()
    assert _puts(seeded, "projects")[-1] == {**listed, "understoodPct": 91}


async def test_a_run_announced_keeps_its_project_its_agents_and_its_work(client: AsyncClient,
                                                                        seeded: AsyncSession):
    """Announced bare, every step of a run blanked its project name, emptied 'Agents in parallel' and
    dropped its task and plan on screen."""
    task = (await seeded.execute(text("SELECT id FROM tasks WHERE project_id = 'erp' LIMIT 1"))).scalar_one()
    plan = (await seeded.execute(text("SELECT id FROM plans LIMIT 1"))).scalar_one()
    common = {"project_id": "erp", "branch": "nc/x", "worktree": "/tmp/x", "repo": "/tmp/repo"}
    seeded.add(Run(id="run-lead", ref="RUN-9001", task_id=task, plan_id=plan, role="integration", **common))
    await seeded.flush()
    seeded.add(Run(id="run-child", ref="RUN-9002", parent_id="run-lead", role="agent", **common))
    await seeded.flush()
    listed = (await client.get("/runs/RUN-9001")).json()
    listed.pop("logs")

    seeded.info["bus"] = Bus()
    lead = await seeded.get(Run, "run-lead")
    lead.note = "a step moved"
    await seeded.flush()
    doc = _puts(seeded, "runs")[-1]
    task_ref = (await seeded.get(Task, task)).ref
    plan_ref = (await seeded.get(Plan, plan)).ref
    assert doc["projectName"] == listed["projectName"] == "Legacy ERP"
    assert doc["children"] == listed["children"] == ["RUN-9002"]
    assert (doc["taskRef"], doc["planRef"]) == (listed["taskRef"], listed["planRef"]) == (task_ref, plan_ref)
    assert doc == {**listed, "note": "a step moved"}


async def test_a_decision_says_who_decided_by_name(client: AsyncClient, seeded: AsyncSession):
    """The screen writes the person's name, and the server's copy replaced it with a user id."""
    seeded.info["bus"] = Bus()
    made = await client.post("/decisions/release.stream", json={"value": "ship", "action": "Release"})
    assert made.status_code == 201
    announced = _puts(seeded, "decisions")[-1]
    assert announced["decidedBy"] == OWNER["name"]


# ── what a day is ────────────────────────────────────────────────

@pytest.mark.parametrize("which", ["api", "ledger"])
async def test_every_connection_counts_days_in_utc(settings: Settings, which: str):
    """The screens count days in UTC; the database cut them in its own zone (Asia/Kolkata here), so
    for five and a half hours a night today's spending was filed under a day the chart did not draw."""
    evening_utc = "SELECT to_char(date_trunc('day', timestamptz '2026-09-18 20:00:00+00'), 'YYYY-MM-DD')"
    if which == "api":
        db = Database(url=settings.test_database_url)
        try:
            async with db.read() as s:
                assert (await s.execute(text("SHOW timezone"))).scalar_one() == "UTC"
                assert (await s.execute(text(evening_utc))).scalar_one() == "2026-09-18"
        finally:
            await db.close()
    else:
        ledger = PostgresLedger(settings.test_database_url.replace("+asyncpg", "+psycopg"))
        try:
            with ledger.engine.connect() as conn:
                assert conn.execute(text("SHOW timezone")).scalar_one() == "UTC"
                assert conn.execute(text(evening_utc)).scalar_one() == "2026-09-18"
        finally:
            ledger.close()
