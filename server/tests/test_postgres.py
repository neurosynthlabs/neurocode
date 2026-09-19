"""The new stack, proved where it matters: rules in services, questions in SQL.

Each test runs inside a transaction that is rolled back, against the same schema Alembic builds for
production. Nothing here mocks a database — a rule that only holds against a fake one is not a rule.
"""
from __future__ import annotations

import pytest

from app.models import Role, RolePermission, User
from app.repositories import ProjectRepository, RoleRepository, TaskRepository, UserRepository
from app.services.errors import Refused
from app.services.work import Actor, TaskService
from tests.fixtures.workspace import WORKSPACE, rows

ME = Actor(name="Rajat", permissions=frozenset({"tasks:write"}))


async def test_the_board_counts_are_asked_of_the_database(seeded):
    """What used to be a loop over parsed documents is one GROUP BY, and both ways agree."""
    per_project = await ProjectRepository(seeded).task_counts()
    erp = await TaskRepository(seeded).counts_by_status("erp")
    assert per_project["erp"] == erp
    assert sum(erp.values()) == len(rows("tasks", projectId="erp"))       # the fixture's, counted in SQL


async def test_a_page_says_how_many_there_are(seeded):
    """No endpoint returns everything: a page knows its own total, so a screen can say "5 of 13"."""
    total = len(WORKSPACE["tasks"])
    page = await TaskRepository(seeded).board(limit=5)
    assert len(page.items) == 5 and page.total == total
    assert page.more is True and page.next_offset == 5
    assert (await TaskRepository(seeded).board(limit=5, offset=total - 4)).more is False


async def test_a_task_cannot_skip_the_board(seeded):
    """A move that makes no sense is refused in words a person can act on."""
    tasks = TaskRepository(seeded)
    waiting = next(t for t in await tasks.list(order_by=None, limit=200) if t.status == "backlog")
    with pytest.raises(Refused) as refused:
        await TaskService(seeded).move(waiting.ref, "done", ME)
    assert "backlog" in str(refused.value) and "not to done" in str(refused.value)

    moved = await TaskService(seeded).move(waiting.ref, "in_progress", ME)
    assert moved.status == "in_progress"


async def test_finishing_needs_the_checklist_finished(seeded):
    """The rule that used to live in a route handler, where it was written twice and drifted."""
    tasks = TaskRepository(seeded)
    task = next(t for t in await tasks.list(limit=200) if t.checklist and any(not i.done for i in t.checklist))
    task.status = "review"
    await seeded.flush()

    with pytest.raises(Refused) as refused:
        await TaskService(seeded).move(task.ref, "done", ME)
    assert "checklist items are still open" in str(refused.value)

    for item in task.checklist:
        await TaskService(seeded).tick(task.ref, item.id, True, ME)
    assert (await TaskService(seeded).move(task.ref, "done", ME)).status == "done"
    assert (await tasks.by_ref(task.ref)).progress == 100


async def test_permissions_are_one_join_over_every_role_worn(seeded):
    """Three roles, one statement, no unions in Python — and a role knows who wears it."""
    roles = RoleRepository(seeded)
    assert "sessions:chat" in await roles.permissions("engineer")

    seeded.add(User(id="u1", email="Rajat@Example.com", name="Rajat", password_hash="x"))
    await seeded.flush()
    await UserRepository(seeded).set_roles("u1", ["engineer", "approver"])

    users = UserRepository(seeded)
    permissions = await users.permissions("u1")
    assert permissions >= (await roles.permissions("engineer")) | (await roles.permissions("approver"))
    assert "runs:merge" in permissions                    # from approver, not from engineer
    assert await roles.worn_by("engineer") == 1
    assert await users.count_active_with("runs:merge") == 1

    # citext: the address is the same address however it was typed.
    assert (await users.by_email("rajat@example.com")).id == "u1"


async def test_a_custom_role_is_left_alone_by_the_built_in_sync(seeded):
    """Re-syncing built-in roles on every start must not touch a role someone made here."""
    seeded.add(Role(id="qa-lead", name="QA Lead", description="ours", builtin=False))
    await seeded.flush()
    seeded.add(RolePermission(role_id="qa-lead", permission="tasks:write"))
    await seeded.flush()

    from app.data.loader import sync_roles
    await sync_roles(seeded)

    assert await RoleRepository(seeded).permissions("qa-lead") == {"tasks:write"}
    assert (await RoleRepository(seeded).get("qa-lead")).builtin is False
