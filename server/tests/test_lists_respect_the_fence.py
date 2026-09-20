"""Every list that crosses projects stops at the same fence the project itself does.

Wave 3 closed the project: a restricted one is gone from `GET /projects`, answers 404 by id, and its
events never reach a tab that may not see it. What stayed open was everything that lists *work*
rather than projects — the board, the inbox, plans, runs, memory, routines, sessions, reviews — where
a row carries a title, a reference, a branch name and a project id. Reading those lists told anybody
signed in exactly what a restriction exists to withhold.

Three things are asserted for every list here, because any one of them alone would be a fence with a
gap in it:

- somebody **listed** in the closed project sees both projects' rows, and the Owner, whom a
  restriction never narrows, sees everything;
- somebody **not listed** sees the open project's rows and the workspace's own, and none of the
  closed project's;
- a **total** or a count beside a list counts the rows that list can show, and the cut is made in the
  query — a page of one is one row, so `offset` walks a list that does not change under it.

Against a real Postgres, inside the suite's rolled-back transaction, with two ordinary Engineers
signed in beside the Owner: a fence only an Owner ever meets is not a fence.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.data.base import utcnow
from tests.fixtures.workspace import WORKSPACE, load_workspace, rows

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PASSWORD = "a long enough password"

#: The closed one and the open one. `hims` carries the newest task in the fixture, which is what
#: makes the paging assertion below able to tell "cut in the query" from "cut out of the answer".
CLOSED, OPEN = "hims", "erp"


@pytest_asyncio.fixture
async def api(session: AsyncSession) -> FastAPI:
    await load_workspace(session)
    built = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    built.dependency_overrides[deps.session] = use_the_test_session
    return built


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)           # an Owner holds every right
        yield c


@asynccontextmanager
async def signed_in(api: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    """The same app, a cookie jar of its own — so two people are really two people."""
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        assert (await c.post("/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200
        yield c


async def add(client: AsyncClient, email: str, name: str, roles: list[str]) -> dict[str, Any]:
    made = await client.post("/admin/users", json={"email": email, "name": name,
                                                   "password": PASSWORD, "roles": roles})
    assert made.status_code == 201, made.text
    return made.json()


async def restrict(session: AsyncSession, pid: str, grants: list[tuple[str, str]]) -> None:
    """Close a project and list who may work in it: `(user_id, role_id)` rows, as the Access tab will."""
    project = await session.get(m.Project, pid)
    assert project is not None
    project.restricted = True
    for user_id, role_id in grants:
        session.add(m.ProjectRole(project_id=pid, user_id=user_id, role_id=role_id))
    await session.flush()


async def seed(session: AsyncSession) -> None:
    """One row of every listable kind in each project, and one that belongs to no project at all.

    The workspace's own rows are the half of the rule that is easy to get wrong: `project_id NOT IN
    (…)` drops a null, so a fence written that way would hide the decisions, the gates, the facts
    and the rules that belong to everybody.
    """
    now = utcnow()
    for pid in (OPEN, CLOSED):
        project = await session.get(m.Project, pid)
        assert project is not None
        project.source_kind = "local"                     # the Testing screen lists onboarded projects
        session.add_all([
            m.Run(id=f"run-{pid}", ref=f"RUN-{pid}", project_id=pid, status="done", role="solo",
                  branch=f"neurocode/{pid}-1", worktree=f"/tmp/{pid}", repo=f"/tmp/{pid}",
                  requirement=f"the {pid} run", finished_at=now),
            m.Chat(id=f"chat-{pid}", ref=f"CHAT-{pid}", project_id=pid, title=f"the {pid} session",
                   started_by="Rajat", last_at=now),
            m.Schedule(id=f"sched-{pid}", name=f"nightly {pid}", project_id=pid,
                       requirement="sweep the logs", cadence="0 2 * * *"),
            m.CodeReview(id=f"rev-{pid}", ref=f"REV-{pid}", project_id=pid, target="branch",
                         source="main", base="main", head="feature", status="done", finished_at=now),
            m.Decision(id=f"decision.{pid}", project_id=pid, subject=f"how {pid} rounds money",
                       verdict="banker's"),
            m.Brainstorm(id=f"idea-{pid}", ref=f"IDEA-{pid}", project_id=pid, idea=f"an idea for {pid}"),
            m.ResearchReport(id=f"res-{pid}", ref=f"RES-{pid}", project_id=pid,
                             question=f"where does {pid} keep its money?", status="done",
                             finished_at=now),
            m.ToolRule(tool="edit", pattern=f"{pid}/**", action="ask", project_id=pid),
            m.WorkflowDefinition(id=f"wf-{pid}", name=f"{pid} release", project_id=pid,
                                 requirement_template="ship {input}"),
            m.TasteSignal(project_id=pid, kind="rework_note", payload={"text": f"{pid} note"}),
        ])
        fact = rows("memory", projectId=pid)[0]
        session.add(m.MemoryHit(fact_id=fact["id"], feature="chat", ref=f"CHAT-{pid}"))
    # The workspace's own: no project, so nobody is ever shut out of them.
    session.add_all([
        m.Decision(id="decision.workspace", subject="which database", verdict="Postgres"),
        m.ToolRule(tool="edit", pattern="**/*.md", action="allow"),
        m.WorkflowDefinition(id="wf-workspace", name="workspace release",
                             requirement_template="ship {input}"),
    ])
    await session.flush()


@pytest_asyncio.fixture
async def people(api: FastAPI, client: AsyncClient, session: AsyncSession) -> dict[str, str]:
    """A is listed in the closed project, B is not, and both are ordinary Engineers."""
    a = await add(client, "a@example.com", "Listed", ["engineer"])
    await add(client, "b@example.com", "Outsider", ["engineer"])
    await seed(session)
    await restrict(session, CLOSED, [(a["id"], "engineer")])
    return {"a": "a@example.com", "b": "b@example.com"}


def projects_of(items: list[dict[str, Any]], key: str = "projectId") -> set[str | None]:
    return {item.get(key) for item in items}


# ── the work: tasks, plans, the gates ────────────────────────────

async def test_the_board_leaves_out_a_project_you_cannot_see(api: FastAPI, client: AsyncClient,
                                                             people: dict[str, str]):
    """`GET /tasks` was the plainest leak of the lot: every task in the workspace, title and all."""
    closed = len(rows("tasks", projectId=CLOSED))
    every = len(WORKSPACE["tasks"])
    assert closed and every > closed                     # the fixture has some of both

    async with signed_in(api, people["b"]) as outsider:
        mine = (await outsider.get("/tasks", params={"limit": 500})).json()
    assert len(mine) == every - closed
    assert projects_of(mine) == {OPEN}

    async with signed_in(api, people["a"]) as listed:
        theirs = (await listed.get("/tasks", params={"limit": 500})).json()
    assert len(theirs) == every and projects_of(theirs) == {OPEN, CLOSED}
    assert len((await client.get("/tasks", params={"limit": 500})).json()) == every


async def test_the_board_is_cut_in_the_query_and_not_out_of_the_answer(api: FastAPI, client: AsyncClient,
                                                                       session: AsyncSession,
                                                                       people: dict[str, str]):
    """A page of one must be one task, not "one minus the one you cannot see".

    The newest task is deliberately the closed project's, which is the case that tells the two
    apart: filtered out of the answer, the first page comes back empty and a screen scrolling the
    board stops at the top while the list underneath it still has rows.
    """
    # Two tasks with times of their own, because the fixture's are written in one transaction and
    # share `now()` — a tie leaves the order to Postgres and would prove nothing either way.
    now = utcnow()
    session.add_all([
        m.Task(id="t-newest", ref="TASK-999", title="the newest of all", project_id=CLOSED,
               status="backlog", created_at=now + timedelta(days=2)),
        m.Task(id="t-next", ref="TASK-998", title="the newest one you may read", project_id=OPEN,
               status="backlog", created_at=now + timedelta(days=1)),
    ])
    await session.flush()
    assert (await client.get("/tasks", params={"limit": 1})).json()[0]["ref"] == "TASK-999"

    async with signed_in(api, people["b"]) as outsider:
        first = (await outsider.get("/tasks", params={"limit": 1})).json()
        second = (await outsider.get("/tasks", params={"limit": 1, "offset": 1})).json()
    # One asked for, one returned — not "one minus the one you cannot see", which is zero here.
    assert len(first) == 1 and first[0]["ref"] == "TASK-998"
    assert len(second) == 1 and second[0]["ref"] != "TASK-998"
    assert second[0]["projectId"] == OPEN


async def test_plans_and_gates_stop_at_the_same_fence(api: FastAPI, client: AsyncClient,
                                                      people: dict[str, str]):
    """A plan is a requirement written out in full, and a gate names the tool and the file it wants."""
    plans_closed = len(rows("plans", projectId=CLOSED))
    gates_closed = len(rows("approvals", projectId=CLOSED))
    assert plans_closed and gates_closed

    async with signed_in(api, people["b"]) as outsider:
        plans = (await outsider.get("/plans", params={"limit": 500})).json()
        gates = (await outsider.get("/approvals", params={"limit": 500})).json()
        pending = (await outsider.get("/approvals", params={"status": "pending", "limit": 500})).json()
    assert len(plans) == len(WORKSPACE["plans"]) - plans_closed
    assert projects_of(plans) == {OPEN}
    assert len(gates) == len(WORKSPACE["approvals"]) - gates_closed
    assert projects_of(gates) <= {OPEN, None}            # a workspace gate belongs to everybody
    assert projects_of(pending) <= {OPEN, None}

    async with signed_in(api, people["a"]) as listed:
        assert len((await listed.get("/plans", params={"limit": 500})).json()) == len(WORKSPACE["plans"])
        assert len((await listed.get("/approvals", params={"limit": 500})).json()) == \
            len(WORKSPACE["approvals"])
    assert len((await client.get("/plans", params={"limit": 500})).json()) == len(WORKSPACE["plans"])


async def test_the_decisions_list_keeps_the_workspaces_own(api: FastAPI, client: AsyncClient,
                                                           people: dict[str, str]):
    """The null case, said out loud: a decision filed against no project is everybody's."""
    mine = {f"decision.{OPEN}", "decision.workspace"}
    both = mine | {f"decision.{CLOSED}"}

    async with signed_in(api, people["b"]) as outsider:
        made = {d["id"] for d in (await outsider.get("/decisions")).json()}
    assert made & both == mine                           # the workspace's own is nobody's to lose

    async with signed_in(api, people["a"]) as listed:
        assert {d["id"] for d in (await listed.get("/decisions")).json()} & both == both
    assert {d["id"] for d in (await client.get("/decisions")).json()} & both == both


# ── the runtime: runs and sessions ───────────────────────────────

async def test_runs_and_sessions_leave_out_a_project_you_cannot_see(api: FastAPI, client: AsyncClient,
                                                                    people: dict[str, str]):
    """A run names its project, its branch and the requirement it was given; a session names its title."""
    async with signed_in(api, people["b"]) as outsider:
        runs = (await outsider.get("/runs")).json()
        sessions = (await outsider.get("/sessions")).json()
    assert {r["ref"] for r in runs} == {f"RUN-{OPEN}"}
    assert {s["ref"] for s in sessions} == {f"CHAT-{OPEN}"}

    async with signed_in(api, people["a"]) as listed:
        assert {r["ref"] for r in (await listed.get("/runs")).json()} == {f"RUN-{OPEN}", f"RUN-{CLOSED}"}
        assert {s["ref"] for s in (await listed.get("/sessions")).json()} == \
            {f"CHAT-{OPEN}", f"CHAT-{CLOSED}"}
    assert {r["ref"] for r in (await client.get("/runs")).json()} == {f"RUN-{OPEN}", f"RUN-{CLOSED}"}


async def test_a_session_of_a_closed_project_is_not_there_even_for_a_reader(
        api: FastAPI, client: AsyncClient, session: AsyncSession, people: dict[str, str]):
    """`sessions:read` opens anybody's session — but not one in a project that does not exist for you,
    and the refusal is 404 rather than the right's name, which would say the session is there."""
    await add(client, "reader@example.com", "Reader", ["approver"])
    async with signed_in(api, "reader@example.com") as reader:
        assert (await reader.get(f"/sessions/CHAT-{OPEN}")).status_code == 200
        assert (await reader.get(f"/sessions/CHAT-{CLOSED}")).status_code == 404


# ── memory ───────────────────────────────────────────────────────

async def test_memory_and_its_figures_count_the_same_facts(api: FastAPI, client: AsyncClient,
                                                           people: dict[str, str]):
    """A header saying 412 above a list that can only ever show 380 is a number nobody can
    reconcile — and the difference is the size of a project they were never told about."""
    closed = len(rows("memory", projectId=CLOSED))
    every = len(WORKSPACE["memory"])
    assert closed and every > closed

    async with signed_in(api, people["b"]) as outsider:
        facts = (await outsider.get("/memory")).json()
        figures = (await outsider.get("/memory/stats")).json()
        hits = (await outsider.get("/memory/hits")).json()
    assert len(facts) == every - closed
    assert projects_of(facts) <= {OPEN, None}            # the workspace's own facts are everybody's
    assert figures["held"] == every - closed
    assert {h["ref"] for h in hits} == {rows("memory", projectId=OPEN)[0]["ref"]}

    async with signed_in(api, people["a"]) as listed:
        assert len((await listed.get("/memory")).json()) == every
        assert (await listed.get("/memory/stats")).json()["held"] == every
        assert len((await listed.get("/memory/hits")).json()) == 2
    assert (await client.get("/memory/stats")).json()["held"] == every


async def test_a_fact_of_a_closed_project_cannot_be_pinned_by_reference(api: FastAPI, client: AsyncClient,
                                                                        people: dict[str, str]):
    """Guessing at `MEM-…` must not be a way round the list: the answer is 404, not "you may not"."""
    theirs = rows("memory", projectId=CLOSED)[0]["ref"]
    ours = rows("memory", projectId=OPEN)[0]["ref"]
    async with signed_in(api, people["b"]) as outsider:
        assert (await outsider.post(f"/memory/{theirs}/pin", json={"pinned": True})).status_code == 404
        assert (await outsider.post(f"/memory/{ours}/pin", json={"pinned": True})).status_code == 200

    async with signed_in(api, people["a"]) as listed:
        assert (await listed.post(f"/memory/{theirs}/pin", json={"pinned": True})).status_code == 200


# ── routines, the inbox, and the totals beside them ──────────────

async def test_the_routines_list_and_its_total_agree(api: FastAPI, client: AsyncClient,
                                                     people: dict[str, str]):
    """`/schedules` is paged, so it is the one list that says a total out loud — and a total counted
    over rows the screen cannot show is a lie the screen repeats."""
    async with signed_in(api, people["b"]) as outsider:
        page = (await outsider.get("/schedules")).json()
    assert [r["projectId"] for r in page["items"]] == [OPEN]
    assert page["total"] == len(page["items"]) == 1

    async with signed_in(api, people["a"]) as listed:
        theirs = (await listed.get("/schedules")).json()
    assert theirs["total"] == 2 and len(theirs["items"]) == 2
    assert (await client.get("/schedules")).json()["total"] == 2


async def test_the_inbox_and_its_counts_leave_out_a_project_you_cannot_see(
        api: FastAPI, client: AsyncClient, people: dict[str, str]):
    """The inbox is read from the work itself, so it leaked everything at once: the gates waiting,
    the runs that finished, the plans compiled, the reviews and the research that ended."""
    async with signed_in(api, people["b"]) as outsider:
        mine = (await outsider.get("/inbox")).json()
    assert projects_of(mine["needsYou"]) <= {OPEN, None}
    assert projects_of(mine["doneSince"]) <= {OPEN, None}
    assert not [i for i in mine["doneSince"] if i["ref"].endswith(CLOSED)]
    waiting = rows("approvals", status="pending")
    assert mine["counts"]["needsYou"] == len([a for a in waiting if a.get("projectId") != CLOSED])

    async with signed_in(api, people["a"]) as listed:
        theirs = (await listed.get("/inbox")).json()
    assert theirs["counts"]["needsYou"] == len(waiting)
    assert {i["ref"] for i in theirs["doneSince"]} >= {f"RUN-{CLOSED}", f"REV-{CLOSED}", f"RES-{CLOSED}"}
    assert (await client.get("/inbox")).json()["counts"]["needsYou"] == len(waiting)


# ── reviews ──────────────────────────────────────────────────────

async def test_a_closed_projects_reviews_are_not_listed_or_readable(api: FastAPI, client: AsyncClient,
                                                                    people: dict[str, str]):
    """The reviews list names its project in its path, so the fence is the project's own 404 — and a
    review reached by its reference is weighed against the project the review itself names."""
    async with signed_in(api, people["b"]) as outsider:
        assert (await outsider.get(f"/projects/{OPEN}/reviews")).status_code == 200
        assert (await outsider.get(f"/projects/{CLOSED}/reviews")).status_code == 404
        assert (await outsider.get(f"/reviews/REV-{OPEN}")).status_code == 200
        assert (await outsider.get(f"/reviews/REV-{CLOSED}")).status_code == 404

    async with signed_in(api, people["a"]) as listed:
        assert (await listed.get(f"/projects/{CLOSED}/reviews")).json()["total"] == 1
        assert (await listed.get(f"/reviews/REV-{CLOSED}")).status_code == 200
    assert (await client.get(f"/reviews/REV-{CLOSED}")).status_code == 200


# ── the rest of the cross-project lists ──────────────────────────

async def test_ideas_research_rules_workflows_and_signals_stop_at_the_fence(
        api: FastAPI, client: AsyncClient, people: dict[str, str]):
    """The long tail, each of which names a project and quotes somebody's words about it."""
    async with signed_in(api, people["b"]) as outsider:
        ideas = (await outsider.get("/ai/brainstorms")).json()
        reports = (await outsider.get("/research")).json()
        tool_rules = (await outsider.get("/permissions/tool-rules")).json()
        library = (await outsider.get("/workflows")).json()
        signals = (await outsider.get("/taste/signals")).json()
        testing = (await outsider.get("/testing")).json()

    assert {i["ref"] for i in ideas} == {f"IDEA-{OPEN}"}
    assert {r["ref"] for r in reports} == {f"RES-{OPEN}"}
    assert projects_of(tool_rules) == {OPEN, None}       # the workspace's own rule stays
    assert {w["projectId"] for w in library} == {OPEN, None}
    assert {s["projectId"] for s in signals} == {OPEN}
    assert projects_of(testing["suites"]) == {OPEN}

    async with signed_in(api, people["a"]) as listed:
        assert {i["ref"] for i in (await listed.get("/ai/brainstorms")).json()} == \
            {f"IDEA-{OPEN}", f"IDEA-{CLOSED}"}
        assert {r["ref"] for r in (await listed.get("/research")).json()} == \
            {f"RES-{OPEN}", f"RES-{CLOSED}"}
        assert projects_of((await listed.get("/permissions/tool-rules")).json()) == {OPEN, CLOSED, None}
        assert {w["projectId"] for w in (await listed.get("/workflows")).json()} == {OPEN, CLOSED, None}
        assert {s["projectId"] for s in (await listed.get("/taste/signals")).json()} == {OPEN, CLOSED}
        assert projects_of((await listed.get("/testing")).json()["suites"]) == {OPEN, CLOSED}

    assert projects_of((await client.get("/testing")).json()["suites"]) == {OPEN, CLOSED}
    assert projects_of((await client.get("/permissions/tool-rules")).json()) == {OPEN, CLOSED, None}


# ── one row, asked for by its own reference ──────────────────────

async def test_an_item_of_a_closed_project_answers_404_and_never_403(api: FastAPI, client: AsyncClient,
                                                                     people: dict[str, str]):
    """"Does this exist" is itself the information. A 403 would answer it while refusing to.

    Every one of these is reachable without ever seeing the list it came from — a reference in a
    chat message, a URL somebody pasted, a guess at the next number.
    """
    task = rows("tasks", projectId=CLOSED)[0]["ref"]
    plan = rows("plans", projectId=CLOSED)[0]["ref"]
    async with signed_in(api, people["b"]) as outsider:
        for path in (f"/tasks/{task}", f"/plans/{plan}", f"/runs/RUN-{CLOSED}",
                     f"/runs/RUN-{CLOSED}/diff", f"/reviews/REV-{CLOSED}", f"/research/RES-{CLOSED}",
                     f"/schedules/sched-{CLOSED}", f"/schedules/sched-{CLOSED}/fires",
                     f"/workflows/wf-{CLOSED}", f"/agents/custom?project={CLOSED}"):
            answered = await outsider.get(path)
            assert answered.status_code == 404, f"{path} answered {answered.status_code}"

    # The open project's own are there, so the 404s above are the fence and not a broken route.
    open_task = rows("tasks", projectId=OPEN)[0]["ref"]
    open_plan = rows("plans", projectId=OPEN)[0]["ref"]
    async with signed_in(api, people["b"]) as outsider:
        for path in (f"/tasks/{open_task}", f"/plans/{open_plan}", f"/runs/RUN-{OPEN}",
                     f"/schedules/sched-{OPEN}", f"/workflows/wf-{OPEN}",
                     f"/agents/custom?project={OPEN}"):
            assert (await outsider.get(path)).status_code == 200, path

    # And for somebody the project is open to, and for the Owner, nothing is hidden at all.
    async with signed_in(api, people["a"]) as listed:
        for path in (f"/tasks/{task}", f"/plans/{plan}", f"/runs/RUN-{CLOSED}",
                     f"/schedules/sched-{CLOSED}", f"/workflows/wf-{CLOSED}"):
            assert (await listed.get(path)).status_code == 200, path
    assert (await client.get(f"/tasks/{task}")).status_code == 200


async def test_a_write_reached_by_a_bare_reference_is_fenced_like_the_read(
        api: FastAPI, client: AsyncClient, people: dict[str, str]):
    """Hiding a row from a list and leaving the write that acts on it open would be a fence with a
    door beside it: approving a gate resumes a run inside the project, and moving a task edits it.

    Both are tried by people who hold the right across the whole workspace — B is an Engineer, so
    `tasks:write` is theirs everywhere, and the Approver holds `approvals:decide` everywhere — which
    is exactly the case per-project rights exist for. Someone who lacks the right outright is
    refused by the right, before any of this, and hears nothing about the row either way.
    """
    closed_task = rows("tasks", projectId=CLOSED)[0]["ref"]
    open_task = rows("tasks", projectId=OPEN)[0]["ref"]
    closed_gate = rows("approvals", projectId=CLOSED, status="pending")[0]["ref"]
    open_gate = rows("approvals", projectId=OPEN, status="pending")[0]["ref"]
    await add(client, "approver@example.com", "Approver", ["approver"])

    async with signed_in(api, people["b"]) as outsider:
        assert (await outsider.patch(f"/tasks/{closed_task}", json={"status": "done"})).status_code == 404
        assert (await outsider.patch(f"/tasks/{open_task}", json={"status": "review"})).status_code == 200

    async with signed_in(api, "approver@example.com") as approver:
        assert (await approver.post(f"/approvals/{closed_gate}/approve")).status_code == 404
        assert (await approver.post(f"/approvals/{open_gate}/approve")).status_code == 200

    async with signed_in(api, people["a"]) as listed:
        assert (await listed.patch(f"/tasks/{closed_task}", json={"status": "review"})).status_code == 200


async def test_nothing_is_narrowed_while_no_project_is_restricted(api: FastAPI, client: AsyncClient,
                                                                  session: AsyncSession):
    """The cost and the behaviour in an ordinary workspace: `unseen_by` answers nothing, every list
    runs the statement it always ran, and an Engineer reads exactly what the Owner reads."""
    await add(client, "b@example.com", "Outsider", ["engineer"])
    await seed(session)

    async with signed_in(api, "b@example.com") as anybody:
        assert len((await anybody.get("/tasks", params={"limit": 500})).json()) == len(WORKSPACE["tasks"])
        assert len((await anybody.get("/memory")).json()) == len(WORKSPACE["memory"])
        assert (await anybody.get("/schedules")).json()["total"] == 2
        assert {r["ref"] for r in (await anybody.get("/runs")).json()} == {f"RUN-{OPEN}", f"RUN-{CLOSED}"}
        assert (await anybody.get(f"/reviews/REV-{CLOSED}")).status_code == 200
