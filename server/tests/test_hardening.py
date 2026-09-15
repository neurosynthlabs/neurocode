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
from app.data.loader import load_seed, sync_roles, when
from app.models import ActivityEvent, AuditEntry, MemoryConflict, MemoryFact, Plan, Project
from app.services.knowledge import MemoryService

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


# ── the seed's own dates ─────────────────────────────────────────

def test_a_relative_date_in_the_seed_becomes_a_real_one():
    """"2 min ago" used to parse to None, so every seeded project had no last-active date at all."""
    assert when("just now") is not None
    recent, older = when("2 min ago"), when("6 d ago")
    assert recent is not None and older is not None and older < recent
    assert when("2026-09-11T21:51:46") is not None
    assert when("sometime on Tuesday") is None          # still nothing rather than a guess


async def test_every_seeded_fact_and_project_carries_a_date(seeded: AsyncSession):
    undated = (await seeded.execute(
        select(MemoryFact.ref).where(MemoryFact.last_used_at.is_(None)))).scalars().all()
    assert not undated, f"{len(undated)} facts came in with no last-used date"
    assert not (await seeded.execute(
        select(Project.id).where(Project.last_active_at.is_(None)))).scalars().all()


async def test_a_seeded_plan_knows_which_task_it_came_from(seeded: AsyncSession):
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
