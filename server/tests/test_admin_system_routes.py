"""Administering the workspace itself: the database under it, and the model lanes above it.

Against a real Postgres, inside one rolled-back transaction, signed in as the first Owner — and, where
it matters, as someone who is not. The gateway is given a ledger that remembers and a secrets file of
this test's own, so a key set here is written for real and taken away with the temporary directory.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import lanes
from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.data.engine import Database
from app.data.loader import sync_roles
from app.repositories.identity import AuditRepository
from app.secrets import Secrets
from app.services.identity import IdentityService
from app.services.maintenance import COLLECTIONS, find_pg_dump
from app.settings import Settings
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "vik@example.com", "password": "another good password"}
HEADERS = {"X-NC-Client": "test"}
#: Every route in this family, with the method that reaches it — used to prove one rule holds for all.
GUARDED = (("GET", "/admin/database"), ("POST", "/admin/database/backup"), ("POST", "/admin/database/check"),
           ("POST", "/admin/database/optimize"), ("POST", "/admin/reset"), ("GET", "/admin/ai"),
           ("PUT", "/admin/ai"), ("POST", "/admin/ai/test"))


@pytest.fixture
def lane_gateway(tmp_path: Path) -> Gateway:
    return Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))


@pytest_asyncio.fixture
async def api(session: AsyncSession, settings: Settings, tmp_path: Path, lane_gateway: Gateway,
              monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    """The API over this test's own database, with its backups and its keys in a temporary directory.

    Every lane's environment variable is taken away first: a machine that happens to export
    GROQ_API_KEY would otherwise make a lane answer differently here than on anybody else's.
    """
    monkeypatch.delenv("NEUROCODE_COMPILER", raising=False)
    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    await load_workspace(session)

    db = Database(url=settings.test_database_url)
    app = create_api(db=db)
    app.state.settings = settings.model_copy(update={"database_url": settings.test_database_url,
                                                     "backups_dir": tmp_path / "backups"})

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    app.dependency_overrides[deps.gateway] = lambda: lane_gateway
    yield app
    await db.close()


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)           # an Owner holds every permission
        yield c


@pytest_asyncio.fixture
async def live_admin(settings: Settings, tmp_path: Path, lane_gateway: Gateway,
                     monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """An Owner in a workspace that is really committed.

    Almost everything here runs inside the suite's rolled-back transaction, which is what keeps tests
    from seeing each other. REINDEX cannot: it waits for every open transaction in the database, so a
    test holding one open would be the thing it waits for. This one commits, and cleans up after.
    """
    monkeypatch.delenv("NEUROCODE_COMPILER", raising=False)
    db = Database(url=settings.test_database_url)
    async with db.session() as s:
        await sync_roles(s)
    app = create_api(db=db)
    app.state.settings = settings.model_copy(update={"database_url": settings.test_database_url,
                                                     "backups_dir": tmp_path / "backups"})
    app.dependency_overrides[deps.gateway] = lambda: lane_gateway
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api",
                               headers=HEADERS) as c:
            await c.post("/auth/setup", json={**OWNER, "email": "optimize-owner@example.com"})
            yield c
    finally:
        async with db.session() as s:
            await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM sessions"))
            await s.execute(text("DELETE FROM user_roles"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM workspace"))
        await db.close()


@pytest_asyncio.fixture
async def viewer(api: FastAPI, client: AsyncClient, session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Someone who may use the workspace and administer none of it."""
    await IdentityService(session).create(VIEWER["email"], "Vik", VIEWER["password"], ["viewer"])
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/login", json=VIEWER)
        yield c


# ── the database ─────────────────────────────────────────────────
async def test_the_database_report_counts_rows_rather_than_estimating_them(client: AsyncClient):
    report = (await client.get("/admin/database")).json()
    assert set(report) >= {"path", "engine", "version", "pageSize", "pages", "freePages", "walLevel",
                           "sizeBytes", "walBytes", "tables", "indexes", "indexHealth", "migrations",
                           "backups", "backupDir"}
    # It used to answer `sqlite: "16.12"`, which the Health panel rendered as "SQLite 16.12".
    assert report["engine"] == "PostgreSQL" and "sqlite" not in report
    assert report["walLevel"] in ("minimal", "replica", "logical")
    assert report["sizeBytes"] > 0 and report["pageSize"] > 0 and report["pages"] > 0
    assert report["indexes"] == report["indexHealth"]["count"] > 0
    # Counted in full, named only in part: the screen says how many, and a name list is capped.
    health = report["indexHealth"]
    assert health["unusedCount"] >= len(health["unused"]) and health["invalidCount"] >= len(health["invalid"])
    assert health["invalidCount"] == 0 and isinstance(health["unusedCount"], int)
    assert report["backups"] == []                        # a temporary directory, nothing in it yet

    # The fixture is written but not committed, so the planner still believes the workspace is empty.
    # Anything reading n_live_tup would report zero tasks; this reports what is really there.
    tasks = next(t for t in report["tables"] if t["name"] == "tasks")
    assert tasks["rows"] == len((await client.get("/tasks")).json()) > 0


async def test_the_check_says_what_it_actually_checked(client: AsyncClient):
    result = (await client.post("/admin/database/check")).json()
    assert {c["name"] for c in result["checked"]} == {"connection", "schema", "foreignKeys", "bloat"}
    sound = {c["name"]: c["ok"] for c in result["checked"]}
    assert sound["connection"] and sound["schema"] and sound["foreignKeys"]
    assert result["foreignKeyProblems"] == 0
    assert "foreign keys walked" in next(c for c in result["checked"] if c["name"] == "foreignKeys")["detail"]
    # Whether anything wants vacuuming depends on what this database has been put through, so it is
    # reported rather than asserted. What must hold is what the screen reads: `ok` is "nothing was
    # found", and `integrity` says "ok" exactly then and lists the findings otherwise.
    assert result["ok"] is all(sound.values())
    assert (result["integrity"] == ["ok"]) is result["ok"]


async def test_a_backup_with_no_pg_dump_is_refused_in_words(client: AsyncClient,
                                                            monkeypatch: pytest.MonkeyPatch):
    """The failure that actually happens: the API runs where the Postgres client tools do not — not on
    PATH, and not in any of the places they are usually installed either."""
    from app.services import maintenance

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(maintenance, "PG_BIN_GLOBS", ())
    refused = await client.post("/admin/database/backup")
    assert refused.status_code == 409 and "pg_dump" in refused.json()["detail"]


@pytest.mark.skipif(find_pg_dump(16) is None, reason="no pg_dump for PostgreSQL 16 on this machine")
async def test_a_backup_is_a_file_the_report_then_lists(client: AsyncClient, tmp_path: Path):
    made = await client.post("/admin/database/backup")
    assert made.status_code == 201
    body = made.json()
    assert set(body) == {"name", "bytes", "at"} and body["bytes"] > 0
    assert (tmp_path / "backups" / body["name"]).is_file()
    assert (await client.get("/admin/database")).json()["backups"] == [body]


async def test_the_migration_history_stops_where_the_database_stands(client: AsyncClient,
                                                                     session: AsyncSession):
    """`version` is an index into the list, so comparing it to the list's length proved nothing.
    What matters is that the history ends at the revision this database is actually on."""
    from app.services.maintenance import MaintenanceService

    report = (await client.get("/admin/database")).json()
    at = await MaintenanceService(session).revision()
    assert at and report["migrations"][-1]["revision"] == at
    assert [m["version"] for m in report["migrations"]] == list(range(1, len(report["migrations"]) + 1))


async def test_a_database_that_was_never_migrated_is_reported_not_a_500(session: AsyncSession):
    """The state this screen exists to diagnose must not be the state that breaks it."""
    from sqlalchemy import text

    from app.services.maintenance import MaintenanceService

    chores = MaintenanceService(session)
    savepoint = await session.begin_nested()
    await session.execute(text("DROP TABLE alembic_version"))
    assert await chores.revision() == ""
    assert await chores.migrations() == []
    await savepoint.rollback()


async def test_optimizing_runs_outside_the_request_and_says_what_it_did(live_admin):
    """VACUUM and REINDEX cannot live in a transaction — and REINDEX waits for every *other* open
    transaction too, the request's included, which is why this runs against a committed workspace
    rather than inside the suite's rolled-back one."""
    client = live_admin
    done = (await client.post("/admin/database/optimize")).json()
    assert set(done) >= {"beforeBytes", "afterBytes", "ms"}
    assert done["beforeBytes"] > 0 and done["afterBytes"] > 0 and done["ms"] >= 0
    for step in done["did"]:
        assert step["ok"] is True, f"{step['step']} did not run: {step['detail']}"


# ── emptying the workspace ───────────────────────────────────────
async def test_a_reset_needs_the_header_and_empties_the_work_but_keeps_the_people(client: AsyncClient,
                                                                                session: AsyncSession):
    from sqlalchemy import func, select

    from app import models as m
    from app.data import catalogue

    unconfirmed = await client.post("/admin/reset")
    assert unconfirmed.status_code == 400 and "X-Confirm" in unconfirmed.json()["detail"]
    assert "empties the workspace" in unconfirmed.json()["detail"]

    # Beside the fixture's projects, the rows that belong to no project — each of them has to go too.
    session.add_all([
        m.MemoryFact(id="m-ws", ref="MEM-9001", category="decisions", title="Workspace fact", body="Kept?"),
        m.Chunk(project_id=None, kind="memory", ref="MEM-9001", path="decisions", title="t", body="b"),
        m.Brainstorm(id="b-ws", ref="IDEA-9001", idea="An idea"),
        m.Decision(id="release.9", subject="Release", verdict="ship"),
        m.Pref(id="skills.rag", value=True),
        m.WorkflowDefinition(id="wf-ws", name="Workspace flow", requirement_template="Do {input}"),
        m.EvalSuite(id="es-ws", name="Workspace suite", target_kind="prompt"),
        m.AiCall(feature="ask", lane="groq", model="m"),
    ])
    await session.flush()
    # A call made for one of the fixture's projects: the project goes, what the call cost must not.
    project_id = (await session.execute(select(m.Project.id).limit(1))).scalar_one()
    session.add(m.AiCall(feature="agent", lane="groq", model="m", tokens_in=7, project_id=project_id))
    await session.flush()
    ledger_before = (await session.execute(select(func.count()).select_from(m.AiCall))).scalar_one()
    people = (await session.execute(select(func.count()).select_from(m.User))).scalar_one()
    audit_before = (await session.execute(select(func.count()).select_from(m.AuditEntry))).scalar_one()

    done = await client.post("/admin/reset", headers={"X-Confirm": "reset"})
    assert done.status_code == 200
    body = done.json()
    assert body["ok"] is True and body["emptied"] is True
    assert set(body["counts"]) == {name for name, _ in COLLECTIONS}
    assert {k: v for k, v in body["counts"].items() if k != "agents"} == dict.fromkeys(
        (name for name, _ in COLLECTIONS if name != "agents"), 0)
    assert body["counts"]["agents"] == len(catalogue.AGENTS)          # the roster is the product's
    assert body["compiler"]["provider"]

    async def left(model) -> int:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()

    for model in (m.Project, m.Task, m.Plan, m.Approval, m.MemoryFact, m.MemoryConflict, m.Chunk,
                  m.ActivityEvent, m.McpServer, m.McpTool, m.Brainstorm, m.Decision,
                  m.Pref, m.WorkflowDefinition, m.EvalSuite):
        assert await left(model) == 0, model.__tablename__
    assert await left(m.User) == people and people > 0                # accounts are kept
    assert await left(m.Role) == len(catalogue.ROLES)                 # and the roles
    assert await left(m.Agent) == len(catalogue.AGENTS)               # and the roster
    assert (await client.get("/auth/me")).status_code == 200          # nobody was signed out

    recorded = (await AuditRepository(session).recent(action="data.reset")).items
    assert len(recorded) == 1 and "backup" in recorded[0].detail and recorded[0].target == "workspace emptied"
    assert await left(m.AuditEntry) == audit_before + 1               # the log keeps everything before it
    # The ledger is history: every line is still there, the project's line only without its project.
    assert await left(m.AiCall) == ledger_before
    orphaned = (await session.execute(select(m.AiCall.project_id).where(m.AiCall.tokens_in == 7))).scalar_one()
    assert orphaned is None


async def test_a_reset_forgets_every_projects_answer_so_a_new_project_of_the_same_name_is_asked(
        client: AsyncClient, session: AsyncSession):
    """A project's id is its repository's last path segment, and its standing answer to running tests
    is a setting with no foreign key to it. Emptying kept the answer, so github.com/bob/erp onboarded
    afterwards as 'erp' inherited github.com/alice/erp's "allowed" and ran its tests unasked."""
    from sqlalchemy import select

    from app import models as m
    from app.repositories.runtime import ResultsRepository

    session.add_all([
        m.Setting(key="runtime.tests.erp", value="allowed"),
        # Left behind by an emptying from before the rule: its project is already gone.
        m.Setting(key="runtime.tests.gone", value="allowed"),
        m.Setting(key="lanes.kept", value={"enabled": True}),          # the workspace's own: kept
    ])
    await session.flush()
    assert (await ResultsRepository(session).answers(["erp"])) == {"erp": "allowed"}

    assert (await client.post("/admin/reset", headers={"X-Confirm": "reset"})).status_code == 200

    left = set((await session.execute(select(m.Setting.key))).scalars())
    assert not {k for k in left if k.startswith("runtime.tests.")}
    assert "lanes.kept" in left
    session.add(m.Project(id="erp", name="Somebody else's ERP"))
    await session.flush()
    assert (await ResultsRepository(session).answers(["erp"])) == {}


# ── the model lanes ──────────────────────────────────────────────
async def test_the_lane_report_is_the_shape_the_providers_screen_reads(client: AsyncClient):
    report = (await client.get("/admin/ai")).json()
    assert set(report) >= {"preference", "preferenceLocked", "active", "deepseek", "ollama", "lanes"}
    assert report["preference"] == "auto" and report["preferenceLocked"] is False
    assert [lane["id"] for lane in report["lanes"]] == list(lanes.IDS)
    assert set(report["lanes"][0]) >= {"label", "model", "baseUrl", "enabled", "hasKey", "keyMask",
                                       "keySource", "rejected", "ready", "blocked", "allowed", "spent"}
    assert report["deepseek"]["hasKey"] is False and report["deepseek"]["keyMask"] is None


async def test_a_key_is_taken_and_never_handed_back(client: AsyncClient, lane_gateway: Gateway,
                                                    session: AsyncSession):
    secret = "sk-groq-do-not-echo-9999"
    answer = await client.put("/admin/ai", json={"lane": "groq", "key": secret, "enabled": False})
    assert answer.status_code == 200 and secret not in answer.text

    groq = next(lane for lane in answer.json()["lanes"] if lane["id"] == "groq")
    assert groq["hasKey"] is True and groq["keyMask"] == "••••9999"
    assert groq["enabled"] is False and groq["blocked"] == "switched off"
    assert lane_gateway.secrets.get("groq_api_key") == secret   # the file, not the database

    recorded = (await AuditRepository(session).recent(action="ai.update")).items
    assert recorded[0].detail["groq.key"] == "set" and secret not in json.dumps(recorded[0].detail)


async def test_what_an_administrator_changes_about_a_lane_is_what_the_next_read_says(client: AsyncClient):
    saved = await client.put("/admin/ai", json={"preference": "free", "lane": "gemini",
                                                "model": "gemini-9-ultra", "rpm": 3})
    assert saved.status_code == 200
    again = (await client.get("/admin/ai")).json()
    assert again["preference"] == "free"
    gemini = next(lane for lane in again["lanes"] if lane["id"] == "gemini")
    assert gemini["model"] == "gemini-9-ultra" and gemini["rpm"] == 3


async def test_a_change_the_gateway_cannot_make_is_refused_with_its_reason(client: AsyncClient):
    assert (await client.put("/admin/ai", json={"lane": "nope", "enabled": True})).status_code == 404
    bad = await client.put("/admin/ai", json={"preference": "turbo"})
    assert bad.status_code == 400 and "routing must be one of" in bad.json()["detail"]
    keyless = await client.put("/admin/ai", json={"lane": "ollama", "key": "irrelevant"})
    assert keyless.status_code == 400 and "needs no key" in keyless.json()["detail"]


async def test_a_probe_reports_what_came_back(client: AsyncClient):
    offline = (await client.post("/admin/ai/test", json={"provider": "rules"})).json()
    assert offline == {"ok": True, "ms": 0, "detail": "The offline rules need no model."}

    unconfigured = (await client.post("/admin/ai/test", json={"provider": "groq"})).json()
    assert unconfigured["ok"] is False and unconfigured["detail"] == "No API key is set."

    unknown = (await client.post("/admin/ai/test", json={"provider": "nope"})).json()
    assert unknown["ok"] is False and "no lane called nope" in unknown["detail"]


# ── who may ──────────────────────────────────────────────────────
@pytest.mark.parametrize(("method", "path"), GUARDED)
async def test_none_of_this_is_open_to_someone_without_workspace_admin(viewer: AsyncClient, method: str,
                                                                       path: str):
    refused = await viewer.request(method, path, json={} if method in ("POST", "PUT") else None)
    assert refused.status_code == 403 and "workspace:admin" in refused.json()["detail"]


@pytest.mark.parametrize(("method", "path"), GUARDED)
async def test_none_of_this_is_open_to_a_stranger(api: FastAPI, client: AsyncClient, method: str, path: str):
    transport = ASGITransport(app=api)
    async with AsyncClient(transport=transport, base_url="http://api", headers=HEADERS) as nobody:
        refused = await nobody.request(method, path, json={} if method in ("POST", "PUT") else None)
    assert refused.status_code == 401


def test_pg_dump_is_found_off_path_where_homebrew_puts_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Homebrew's postgresql@16 is keg-only, so pg_dump was never on PATH on the commonest Mac install
    and a backup could not be taken on exactly the machine this runs on."""
    from app.services import maintenance

    keg = tmp_path / "opt" / "postgresql@16" / "bin"
    keg.mkdir(parents=True)
    fake = keg / "pg_dump"
    fake.write_text("#!/bin/sh\necho 'pg_dump (PostgreSQL) 16.12'\n")
    fake.chmod(0o755)
    old = tmp_path / "old" / "bin"
    old.mkdir(parents=True)
    stale = old / "pg_dump"
    stale.write_text("#!/bin/sh\necho 'pg_dump (PostgreSQL) 14.9'\n")
    stale.chmod(0o755)

    monkeypatch.setenv("PATH", str(old))                        # an older client is on PATH
    monkeypatch.setattr(maintenance, "PG_BIN_GLOBS", (str(tmp_path / "opt" / "postgresql@*" / "bin"),))
    assert find_pg_dump(16) == str(fake)                          # too old to dump 16, so the keg wins
    assert find_pg_dump(17) is None                               # and nothing new enough is invented
