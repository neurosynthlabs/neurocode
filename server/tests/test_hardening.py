"""The things an audit found were claimed but not true.

Each test here stands for one defect that was live: a docstring that promised something the database
did not enforce, a column typed as one thing and filled with another, a guard applied to writes and
forgotten on reads. They are grouped in one file because that is what they have in common — every one
of them passed review, and every one of them was wrong.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.git import Refused as PathRefused
from app.agent.git import safe_path
from app.api import deps
from app.api.app import create_api
from app.data.loader import sync_roles, when
from app.models import ActivityEvent, AuditEntry, MemoryConflict, MemoryFact, Plan, Project
from app.services.knowledge import MemoryService
from app.settings import Settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(seeded: AsyncSession) -> AsyncIterator[AsyncClient]:
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield seeded

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


# ── an imported workspace's dates ───────────────────────────────

def test_a_relative_date_in_an_old_document_becomes_a_real_one():
    """"2 min ago" used to parse to None, so every imported project had no last-active date at all."""
    assert when("just now") is not None
    recent, older = when("2 min ago"), when("6 d ago")
    assert recent is not None and older is not None and older < recent
    assert when("2026-09-11T21:51:46") is not None
    assert when("sometime on Tuesday") is None          # still nothing rather than a guess


async def test_every_loaded_fact_and_project_carries_a_date(seeded: AsyncSession):
    undated = (await seeded.execute(
        select(MemoryFact.ref).where(MemoryFact.last_used_at.is_(None)))).scalars().all()
    assert not undated, f"{len(undated)} facts came in with no last-used date"
    assert not (await seeded.execute(
        select(Project.id).where(Project.last_active_at.is_(None)))).scalars().all()


async def test_a_loaded_plan_knows_which_task_it_came_from(seeded: AsyncSession):
    """The column existed and was never filled, so every plan looked unattached to its work."""
    plans = (await seeded.execute(select(Plan.ref, Plan.task_id))).all()
    assert plans and all(task_id for _, task_id in plans)


# ── memory conflicts ─────────────────────────────────────────────

async def test_resolving_a_conflict_archives_the_fact_that_lost(seeded: AsyncSession):
    """The sides are fact ids. Read as documents, the loser was never found — so nothing happened."""
    conflict = (await seeded.execute(select(MemoryConflict).limit(1))).scalar_one()
    loser_id = conflict.b
    await MemoryService(seeded).resolve(conflict.id, "a", by="Rajat")
    await seeded.flush()

    loser = await seeded.get(MemoryFact, loser_id)
    assert loser is not None and loser.archived is True
    assert (await seeded.get(MemoryConflict, conflict.id)).status == "resolved"
    said = (await seeded.execute(select(ActivityEvent.detail)
                                 .where(ActivityEvent.action == "Conflict resolved"))).scalars().all()
    assert said and loser.ref in said[0]


async def test_a_conflict_cannot_name_a_fact_that_is_not_there(seeded: AsyncSession):
    """What the foreign key is for: this used to be accepted and silently mean nothing."""
    savepoint = await seeded.begin_nested()
    seeded.add(MemoryConflict(id="cf-bad", topic="Invented", a="m324", b="no-such-fact"))
    with pytest.raises(DBAPIError):
        await seeded.flush()
    await savepoint.rollback()


async def test_an_unknown_memory_category_is_refused_not_a_crash(client: AsyncClient):
    """The column is a Postgres enum, so an unchecked query string reached it and raised a 500."""
    assert (await client.get("/memory", params={"category": "not-a-category"})).status_code == 422
    assert (await client.get("/memory", params={"category": "bugs"})).status_code == 200


# ── the audit log ────────────────────────────────────────────────

async def test_the_audit_log_refuses_to_be_rewritten(seeded: AsyncSession):
    """It said "append-only, enforced by a trigger". There was no trigger."""
    seeded.add(AuditEntry(action="test.entry", user_id=None, target="x"))
    await seeded.flush()
    seq = (await seeded.execute(select(AuditEntry.seq).order_by(AuditEntry.seq.desc()))).scalars().first()

    for statement in (f"UPDATE audit_log SET action = 'tampered' WHERE seq = {seq}",
                      f"DELETE FROM audit_log WHERE seq = {seq}"):
        # The savepoint is rolled back by hand: a refused statement leaves the transaction aborted,
        # and `async with begin_nested()` would try to release a savepoint Postgres will not accept.
        savepoint = await seeded.begin_nested()
        with pytest.raises(DBAPIError):
            await seeded.execute(text(statement))
        await savepoint.rollback()


async def test_losing_an_account_does_not_lose_what_they_did(seeded: AsyncSession):
    """The one change the trigger must allow: a deleted user's entries stay, without their id."""
    seeded.add(AuditEntry(action="test.entry", user_id=None, target="x"))
    await seeded.flush()
    seq = (await seeded.execute(select(AuditEntry.seq).order_by(AuditEntry.seq.desc()))).scalars().first()
    await seeded.execute(text(f"UPDATE audit_log SET user_id = NULL WHERE seq = {seq}"))
    assert (await seeded.get(AuditEntry, seq)) is not None


# ── paths a model chose ──────────────────────────────────────────

@pytest.mark.parametrize("escape", ["../../../../etc/passwd", "/etc/passwd", ".git/config",
                                    "..", "pkg/../../outside.py", "\\..\\..\\windows"])
def test_a_path_that_leaves_the_worktree_is_refused(escape: str):
    """Writes were checked and reads were not — and a read is what gets sent to a provider."""
    with pytest.raises(PathRefused):
        safe_path(escape)


def test_an_ordinary_path_still_comes_through():
    assert safe_path("pkg/core.py") == Path("pkg/core.py")
    assert safe_path("./pkg/core.py") == Path("pkg/core.py")


# ── the feed ─────────────────────────────────────────────────────

async def test_ticking_a_checklist_item_reaches_the_feed(client: AsyncClient, seeded: AsyncSession):
    """Its sibling recorded a move; this recorded nothing, so the work vanished from the log."""
    task = next(t for t in (await client.get("/tasks")).json() if t["checklist"])
    item = next(i for i in task["checklist"] if not i["done"])
    assert (await client.post(f"/tasks/{task['ref']}/checklist/{item['id']}",
                              json={"done": True})).status_code == 200

    said = (await seeded.execute(select(ActivityEvent.detail)
                                 .where(ActivityEvent.action == "Checklist updated"))).scalars().all()
    assert said and task["ref"] in said[0] and item["label"] in said[0]


# ── what start-up is for ─────────────────────────────────────────

async def test_the_built_in_roles_are_reconciled_on_every_start(seeded: AsyncSession):
    """sync_roles had no caller outside the tests, so a new permission never reached a running one."""
    await seeded.execute(text("DELETE FROM role_permissions WHERE role_id = 'owner'"))
    assert await sync_roles(seeded) > 0
    back = (await seeded.execute(text(
        "SELECT count(*) FROM role_permissions WHERE role_id = 'owner'"))).scalar_one()
    assert back > 0


async def test_a_brand_new_workspace_holds_the_catalogue_and_nothing_else(session: AsyncSession):
    """It used to open on a sample: five projects, their tasks and gates, forty-seven facts, sixteen MCP
    servers — shown on every screen as though somebody had done that work. What start-up writes now
    is the product's own catalogue, and a new workspace holds nothing else until someone makes it.

    A new database is what the migrations leave, so every table is emptied first — inside this test's
    transaction, which rolls it all back — and then start-up runs exactly as the API runs it."""
    from app.api.app import start_up_chores
    from app.data import catalogue

    tables = (await session.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
        "ORDER BY tablename"))).scalars().all()
    await session.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))

    await start_up_chores(session)
    await session.flush()

    held = {}
    for table in tables:
        n = (await session.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar_one()
        if n:
            held[table] = n
    # `prefs` holds one row the product wrote about itself, not about this workspace: which of the
    # one-off carry-overs for custom roles this database has been through. A brand-new workspace has
    # no custom roles, so it goes through all of them at once and has nothing to show for it.
    assert held == {"roles": len(catalogue.ROLES), "agents": len(catalogue.AGENTS),
                    "role_permissions": sum(len(r.permissions) for r in catalogue.ROLES),
                    "prefs": 1}
    names = (await session.execute(text("SELECT id, name FROM agents"))).all()
    assert dict(names) == {a.id: a.name for a in catalogue.AGENTS}


async def test_an_agent_a_person_switched_off_stays_off_across_a_restart(catalogued: AsyncSession):
    """The roster is re-written on every start — but only what the catalogue declares. The one status a
    person sets is theirs, and a restart that switched an agent back on would undo them."""
    from app.data.loader import sync_agents
    from app.models import Agent

    agent = await catalogued.get(Agent, "vision")
    agent.status, agent.name = "disabled", "Renamed by hand"
    await catalogued.flush()
    await sync_agents(catalogued)
    await catalogued.refresh(agent)
    assert agent.status == "disabled" and agent.name == "Vision Agent"


async def test_health_says_what_the_screens_read(client: AsyncClient):
    """It answered {ok, database}; the Settings screen read .db and .counts and threw."""
    body = (await client.get("/health")).json()
    assert body["ok"] is True
    assert isinstance(body["db"], str) and body["db"]
    assert isinstance(body["counts"], dict) and body["counts"]
    assert body["compiler"]["provider"]


# ── the fourth kind of event ─────────────────────────────────────

async def test_a_document_that_changes_reaches_the_open_tabs(schema: str):
    """The stream documented `change` and nothing ever published it, so a second tab never moved.

    This one needs a real commit — that is the whole point of when it fires — so it runs against its
    own engine and cleans up after itself rather than inside the rolled-back session.
    """
    from app.data.engine import Database
    from app.events import Bus
    from app.models import Task

    seen: list[tuple[str, dict]] = []
    feed = Bus()
    feed.listen(lambda kind, data: seen.append((kind, data)))
    db = Database(url=schema, bus=feed)
    try:
        async with db.session() as s:
            s.add(Project(id="chg", name="Change Test"))
            await s.flush()
            s.add(Task(id="chg-1", ref="TASK-CHG", title="Say so", project_id="chg",
                       checklist=[], assignees=[]))

        put = [d for k, d in seen if k == "change" and d["op"] == "put"]
        assert any(d["collection"] == "tasks" and d["doc"]["ref"] == "TASK-CHG" for d in put)
        assert any(d["collection"] == "projects" and d["doc"]["id"] == "chg" for d in put)

        seen.clear()
        async with db.session() as s:
            await s.delete(await s.get(Task, "chg-1"))
        assert ("change", {"op": "drop", "collection": "tasks", "id": "chg-1"}) in seen
    finally:
        async with db.session() as s:
            await s.execute(text("DELETE FROM tasks WHERE project_id = 'chg'"))
            await s.execute(text("DELETE FROM projects WHERE id = 'chg'"))
        await db.close()


async def test_nothing_is_announced_for_a_change_that_was_rolled_back(schema: str):
    from app.data.engine import Database
    from app.events import Bus

    seen: list[tuple[str, dict]] = []
    feed = Bus()
    feed.listen(lambda kind, data: seen.append((kind, data)))
    db = Database(url=schema, bus=feed)
    try:
        with pytest.raises(RuntimeError):
            async with db.session() as s:
                s.add(Project(id="rollback-me", name="Never"))
                await s.flush()
                raise RuntimeError("something went wrong halfway")
        assert not [d for k, d in seen if k == "change"]
    finally:
        await db.close()


# ── two people clicking at once ──────────────────────────────────

async def test_two_requests_at_once_get_two_references(schema: str):
    """Read-the-maximum-then-insert is a race: both saw the same number and the second one 500'd."""
    import asyncio

    from app.data.engine import Database
    from app.models import Task
    from app.repositories.work import TaskRepository

    db = Database(url=schema)

    async def claim(title: str) -> str:
        async with db.session() as s:
            ref = await TaskRepository(s).next_ref(prefix="TASK-RACE-")
            s.add(Task(id=f"race-{title}", ref=ref, title=title, project_id="erp",
                       checklist=[], assignees=[]))
            return ref

    try:
        async with db.session() as s:
            s.add(Project(id="erp", name="Legacy ERP"))
        refs = await asyncio.gather(*(claim(str(n)) for n in range(5)))
        assert len(set(refs)) == 5, f"two requests claimed the same reference: {refs}"
    finally:
        async with db.session() as s:
            await s.execute(text("DELETE FROM tasks WHERE ref LIKE 'TASK-RACE-%'"))
            await s.execute(text("DELETE FROM projects WHERE id = 'erp'"))
        await db.close()


# ── pasted notes ─────────────────────────────────────────────────

async def test_facts_pasted_from_notes_are_saved_and_come_back(client: AsyncClient):
    """The route returned each new fact with its tags, and a new fact's tag collection had never been
    loaded — so reading it lazy-loaded inside async code and every "Add from text" failed with a 500."""
    fact = {"category": "business_rules", "title": "Credit notes",
            "body": "Credit notes must always reference the original invoice number.",
            "reason": "", "confidence": "HIGH"}
    added = await client.post("/memory/facts", json={"projectId": "erp", "facts": [fact]})
    assert added.status_code == 201, added.text[:300]
    body = added.json()
    assert body[0]["tags"] == ["business-rules"] and body[0]["ref"].startswith("MEM-")

    found = (await client.get("/memory", params={"q": "credit notes original"})).json()
    assert any(f["body"].startswith("Credit notes must always") for f in found)


# ── a gate that nothing reopened ─────────────────────────────────

async def test_deciding_a_runs_gate_resumes_the_run(client: AsyncClient, seeded: AsyncSession, monkeypatch):
    """The route's docstring said the runtime would resume the run, and nothing did: every agent run
    stopped at its first gate — a project's first test run, or your signature — and stayed there."""
    import app.api.routes_work as routes_work
    from app.models import Approval

    resumed: list[tuple] = []

    async def record(db, gw, run_ref, step, approved):
        resumed.append((run_ref, step, approved))

    monkeypatch.setattr(routes_work, "resume_run", record)
    seeded.add(Approval(id="ap-gate", ref="APPR-9001", title="Run the tests in shop", run_ref="RUN-77",
                        step=3, status="pending"))
    await seeded.flush()

    assert (await client.post("/approvals/APPR-9001/approve")).status_code == 200
    assert resumed == [("RUN-77", 3, True)]


async def test_a_gate_with_no_run_behind_it_resumes_nothing(client: AsyncClient, monkeypatch):
    import app.api.routes_work as routes_work

    resumed: list[tuple] = []

    async def record(*args):
        resumed.append(args)

    monkeypatch.setattr(routes_work, "resume_run", record)
    pending = (await client.get("/approvals", params={"status": "pending"})).json()
    assert (await client.post(f"/approvals/{pending[0]['ref']}/deny")).status_code == 200
    assert resumed == []


async def test_every_list_of_gates_comes_back_in_the_same_order(client: AsyncClient):
    """Loaded in one transaction, the gates share a timestamp. With no second key the full list and the
    pending list came back in different orders, so the first Approve button was a different gate."""
    pending = [a["ref"] for a in (await client.get("/approvals", params={"status": "pending"})).json()]
    everything = [a["ref"] for a in (await client.get("/approvals")).json() if a["status"] == "pending"]
    assert pending and pending == everything
    assert pending == sorted(pending, key=lambda ref: int(ref.split("-")[-1]), reverse=True)


# ── a repository with a history ──────────────────────────────────

async def test_a_git_repository_indexes_with_its_history(seeded: AsyncSession, tmp_path: Path):
    """git's dates arrived as ISO text and the column is a real timestamp, so asyncpg refused the whole
    insert: every project that was a git repository failed to index, and a plain folder did not."""
    import subprocess

    from app.models import CodeFile
    from app.services.indexing import build_index

    repo = tmp_path / "shop"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "core.py").write_text("def total(x):\n    return x\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                  "commit", "-qm", "start"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    await build_index(seeded, "erp", repo, [])
    changed = (await seeded.execute(select(CodeFile.changed_at).where(CodeFile.project_id == "erp",
                                                                     CodeFile.path == "pkg/core.py"))).scalar_one()
    assert changed is not None and changed.tzinfo is not None


async def test_how_much_is_understood_is_the_share_of_the_scanned_files_the_index_holds(seeded: AsyncSession,
                                                                                     tmp_path: Path):
    """It was a step count — 2 of 15, then 4 of 15 — dressed as a percentage of understanding. Now it is
    measured: of the source files the onboarding scan found, how many the code index holds. A project
    whose files were never counted has no share to speak of."""
    from app.services.indexing import build_index

    repo = tmp_path / "half"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "core.py").write_text("def total(x):\n    return x\n")
    project = Project(id="half", name="Half", files_count=4)
    seeded.add_all([project, Project(id="uncounted", name="Uncounted")])
    await seeded.flush()
    assert project.understood_pct is None                                # not indexed: nothing to say

    await build_index(seeded, "half", repo, [])
    assert project.understood_pct == 25                                  # one file of the four scanned
    await build_index(seeded, "uncounted", repo, [])
    assert (await seeded.get(Project, "uncounted")).understood_pct is None


# ── a question is not a search-box query ─────────────────────────

async def test_a_natural_question_finds_the_code_that_answers_it(seeded: AsyncSession):
    """Every word of a question was required, so "how does the app hand work to a background job?" matched
    nothing — for research, session grounding, Ask memory and evals alike — while the same words typed as
    a search found the function at once."""
    from app.models import Chunk
    from app.repositories.retrieval import ChunkRepository

    seeded.add(Project(id="words", name="Words"))
    await seeded.flush()
    seeded.add_all([
        Chunk(project_id="words", kind="code", ref="deps.py#hand_off", path="app/api/deps.py", title="hand_off",
              body="Commit what this request wrote, then queue the background job that will read it."),
        Chunk(project_id="words", kind="code", ref="css.py#color", path="app/css.py", title="color",
              body="Pick a colour for the sidebar."),
    ])
    await seeded.flush()
    chunks = ChunkRepository(seeded)

    question = "How does the app hand work to a background job?"
    asked = await chunks.search("words", question)
    assert [p["ref"] for p in asked][:1] == ["deps.py#hand_off"]
    assert await chunks.search("words", question, mode="all") == []     # what used to happen, every time
    assert await chunks.lexical_count("words", "background job", mode="all") == 1


async def test_nothing_a_person_types_becomes_query_syntax(seeded: AsyncSession):
    """Asked of Postgres itself, because the failure that matters is its syntax error, not a string."""
    from sqlalchemy import cast as sql_cast
    from sqlalchemy import select as sql_select
    from sqlalchemy.types import Text

    from app.repositories.words import tsquery

    parsed = (await seeded.execute(sql_select(sql_cast(tsquery("tax & rounding | !drop :* ') --", "any"), Text)))
              ).scalar_one()
    assert parsed == "'tax' | 'round' | 'drop'"


# ── what a person is told when something has gone wrong ──────────

#: A database on a server that is there, with a name that is not. `ping` fails; nothing hangs.
NOWHERE = "postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode_does_not_exist"


async def test_health_answers_that_the_database_is_down_instead_of_failing_with_it(
        settings: Settings, tmp_path: Path):
    """It computed `ok = await db.ping()` and then asked the gateway for its status anyway — and the
    gateway reads its lanes from the settings table, through the same database. So the one route
    whose job is to say "Postgres is not running" was the one route that could not say it: a
    plain-text 500, which the web app can only report as "HTTP 500"."""
    from app.data.engine import Database

    config = settings.model_copy(update={"database_url": NOWHERE, "secrets_path": tmp_path / "s.json"})
    api = create_api(db=Database(url=NOWHERE, config=config), config=config)
    try:
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api") as c:
            answered = await c.get("/health")
    finally:
        api.state.ledger.close()
        await api.state.db.close()

    assert answered.status_code == 200
    body = answered.json()
    assert body["ok"] is False and body["counts"] == {}
    assert "neurocode_does_not_exist" in body["db"]
    assert body["compiler"]["provider"] == "unknown"
    assert "not answering" in body["compiler"]["note"]


async def test_the_router_probe_cannot_take_health_down_with_it():
    """Even with the database up, anything the gateway raises is the gateway's news, not liveness's."""
    from types import SimpleNamespace

    from app.api.app import _compiler

    class Unreachable:
        def status(self) -> dict[str, str]:
            raise RuntimeError("could not connect")

    app = SimpleNamespace(state=SimpleNamespace(gateway=Unreachable()))
    assert (await _compiler(app, ok=True))["provider"] == "unknown"


@pytest_asyncio.fixture
async def breakable(seeded: AsyncSession) -> AsyncIterator[AsyncClient]:
    """The real API with three routes that fail in the three ways nobody writes a handler for."""
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield seeded

    api.dependency_overrides[deps.session] = use_the_test_session

    @api.get("/broken/unexpected")
    async def _unexpected() -> None:
        raise RuntimeError("a dict changed size during iteration")

    @api.get("/broken/pool")
    async def _pool() -> None:
        raise PoolTimeout("QueuePool limit of size 5 overflow 10 reached, connection timed out")

    @api.get("/broken/database")
    async def _database() -> None:
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))

    # The response is sent and *then* the exception is re-raised, so the server's log keeps it. That
    # is the real behaviour under uvicorn; here it would reach the test instead of the assertions.
    transport = ASGITransport(app=api, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://api", headers=HEADERS) as c:
        yield c


async def test_an_unexpected_failure_reaches_a_person_as_words_and_not_internal_server_error(
        breakable: AsyncClient):
    """Starlette's own answer is the plain-text body `Internal Server Error`; the web app reads
    `detail` out of the body, fails, and shows a toast saying "HTTP 500" and nothing else."""
    answered = await breakable.get("/broken/unexpected")
    assert answered.status_code == 500
    assert answered.headers["content-type"].startswith("application/json")
    detail = answered.json()["detail"]
    assert "/broken/unexpected" in detail and "RuntimeError" in detail


async def test_a_full_connection_pool_ends_in_words_rather_than_a_bare_500(breakable: AsyncClient):
    answered = await breakable.get("/broken/pool")
    assert answered.status_code == 503
    assert "busy" in answered.json()["detail"] and "try again" in answered.json()["detail"]


async def test_postgres_going_away_mid_request_says_which_thing_to_check(breakable: AsyncClient):
    answered = await breakable.get("/broken/database")
    assert answered.status_code == 503
    assert "Postgres is running" in answered.json()["detail"]


# ── work nobody is doing ─────────────────────────────────────────

async def test_a_session_left_thinking_by_a_restart_is_put_right_and_says_why(seeded: AsyncSession):
    """`chat.think` clears the status in its own `finally`, which a killed process never runs — and
    answering is a background job that takes minutes, so this is the ordinary case, not the rare one.
    The Command Center's "what is working" and the Sessions list both read that status, and nothing
    in the UI could clear it: the session pulsed at the person forever."""
    from app.api.app import reconcile_interrupted
    from app.models import Chat, ChatMessage

    project = (await seeded.execute(select(Project.id))).scalars().first()
    seeded.add(Chat(id="c-int", ref="SESS-INT", project_id=project, title="Half an answer",
                    status="thinking"))
    await seeded.flush()

    counted = await reconcile_interrupted(seeded)
    assert counted["sessions"] >= 1

    chat = await seeded.get(Chat, "c-int")
    await seeded.refresh(chat)
    assert chat.status == "idle"
    said = (await seeded.execute(
        select(ChatMessage).where(ChatMessage.chat_id == "c-int"))).scalars().all()
    assert len(said) == 1 and said[0].role == "note"
    assert "restarted" in said[0].body and "ask again" in said[0].body


# ── a decision is final, and the database is what makes it so ────

async def test_two_approvals_of_one_gate_cannot_both_go_through(schema: str):
    """A read, an `if`, then a write is not a guard under READ COMMITTED: a double click on Approve,
    or a browser retrying the POST, had both requests read `pending`, both pass the check, and both
    hand off a resume of the same run — the gated step run twice.

    A real commit is the whole point, so this runs against its own database and clears up after."""
    import asyncio

    from app.data.engine import Database
    from app.models import Approval
    from app.services.errors import Refused
    from app.services.gates import ApprovalService

    db = Database(url=schema)
    try:
        async with db.session() as s:
            s.add(Project(id="gate", name="Gate Test"))
            await s.flush()
            s.add(Approval(id="a-gate", ref="APPR-GATE", title="Run the project's tests",
                           project_id="gate", status="pending"))

        async def approve_again() -> str:
            async with db.session() as s:
                return (await ApprovalService(s).decide("APPR-GATE", "approve", by_id=None,
                                                        by_name="Rajat")).status

        async with db.session() as first:
            await ApprovalService(first).decide("APPR-GATE", "approve", by_id=None, by_name="Rajat")
            second = asyncio.create_task(approve_again())
            await asyncio.sleep(0.3)
            assert not second.done(), "the second Approve read the gate as pending and went ahead"
        # The first has committed now, so the second wakes, reads what it wrote, and refuses.
        with pytest.raises(Refused) as refused:
            await second
        assert "already approved" in str(refused.value)

        async with db.session() as s:
            decided = (await s.execute(text(
                "SELECT status FROM approvals WHERE ref = 'APPR-GATE'"))).scalar_one()
            assert decided == "approved"
    finally:
        async with db.session() as s:
            await s.execute(text("DELETE FROM activity WHERE project_id = 'gate'"))
            await s.execute(text("DELETE FROM approvals WHERE project_id = 'gate'"))
            await s.execute(text("DELETE FROM projects WHERE id = 'gate'"))
        await db.close()
