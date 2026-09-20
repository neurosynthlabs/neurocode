"""The other half of per-project rights: the places a restricted project could still be seen.

Builder R closed the front door — a restricted project is not in `GET /projects` and answers 404 when
asked for by id. These are the windows: the live stream, which pushes every event to every open tab;
the activity log and its figures; the access token, which resolved a person without their grants and
so hid the project from the very people it was for; and the routes that reach a project's work
rather than the project itself.

Everything here runs against a real Postgres, inside the suite's rolled-back transaction, with a
second and third client signed in as somebody lesser — a fence only an Owner ever meets is not a
fence.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.api.stream import project_of
from app.repositories import NotFound
from app.services.errors import Denied
from app.services.identity import IdentityService
from app.services.tokens import TokenService
from tests.fixtures.workspace import WORKSPACE, load_workspace, rows

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PASSWORD = "a long enough password"


@pytest_asyncio.fixture
async def api(session) -> FastAPI:
    await load_workspace(session)
    built = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[Any]:
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


async def add(client: AsyncClient, email: str, name: str, roles: list[str]) -> dict:
    made = await client.post("/admin/users", json={"email": email, "name": name,
                                                   "password": PASSWORD, "roles": roles})
    assert made.status_code == 201, made.text
    return made.json()


async def restrict(session, pid: str, grants: list[tuple[str, str]]) -> None:
    """Close a project and list who may work in it: `(user_id, role_id)` rows, as the Access tab will."""
    project = await session.get(m.Project, pid)
    assert project is not None
    project.restricted = True
    for user_id, role_id in grants:
        session.add(m.ProjectRole(project_id=pid, user_id=user_id, role_id=role_id))
    await session.flush()


# ── the stream ───────────────────────────────────────────────────

def test_project_of_finds_the_project_on_every_event_that_names_one():
    """The filter can only drop what it can recognise, so what it recognises is written down.

    A project's own document is the awkward one: the project is not *in* it, it *is* it, so the id to
    read is `id` and not `projectId`.
    """
    assert project_of("activity", {"id": "1", "projectId": "hims"}) == "hims"
    assert project_of("routine", {"scheduleId": "s1", "projectId": "hims"}) == "hims"
    assert project_of("review", {"ref": "REV-1", "projectId": "hims"}) == "hims"
    assert project_of("change", {"op": "put", "collection": "runs", "doc": {"projectId": "hims"}}) == "hims"
    assert project_of("change", {"op": "put", "collection": "projects", "doc": {"id": "hims"}}) == "hims"
    assert project_of("change", {"op": "drop", "collection": "projects", "id": "hims"}) == "hims"

    # The workspace's own story belongs to everybody, and is never dropped.
    assert project_of("activity", {"id": "2", "actor": "Rajat", "projectId": None}) is None
    assert project_of("change", {"op": "put", "collection": "prefs", "doc": {"key": "theme"}}) is None
    assert project_of("resync", {"why": "missed", "events": 3}) is None
    assert project_of("reset", {"at": "2026-09-20T00:00:00+00:00"}) is None
    assert project_of("run", {"runRef": "RUN-1", "line": "…"}) is None


async def heard(api: FastAPI, token: str, publish: Callable[[], None], *, want: int,
                timeout: float = 10.0) -> list[dict[str, Any]]:
    """Open the live stream as this person, publish, and collect what it really sends.

    Driven as an ASGI app rather than through the test client, because the client gathers a whole
    answer before it hands it over and this answer never ends.
    """
    chunks: list[str] = []
    open_now, enough = asyncio.Event(), asyncio.Event()

    async def receive() -> dict[str, Any]:
        await enough.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200, message
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"].decode())
            open_now.set()
            if len([c for c in chunks if c.startswith("event:")]) >= want:
                enough.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": "/activity/stream", "raw_path": b"/activity/stream",
             "root_path": "", "query_string": b"", "client": ("127.0.0.1", 50000), "server": ("api", 80),
             "headers": [(b"host", b"api"), (b"cookie", f"{deps.COOKIE}={token}".encode())]}
    streaming = asyncio.create_task(api(scope, receive, send))
    try:
        await asyncio.wait_for(open_now.wait(), timeout)
        publish()
        await asyncio.wait_for(enough.wait(), timeout)
    finally:
        enough.set()
        streaming.cancel()
        await asyncio.gather(streaming, return_exceptions=True)
    return [json.loads(c.split("data: ", 1)[1]) for c in chunks if c.startswith("event:")]


async def test_a_restricted_projects_events_do_not_scroll_past_the_people_it_is_closed_to(
        api: FastAPI, client: AsyncClient, session):
    """The place the whole feature stood or fell: the project is gone from the list and 404s by id,
    while its runs, plans and activity lines keep arriving in every open tab.

    The hidden events are published *first*, so the visible one arriving proves they were dropped
    rather than merely late: one queue, one reader, in order.
    """
    dev = await add(client, "dev@example.com", "Dev", ["engineer"])
    await add(client, "other@example.com", "Other", ["engineer"])
    await restrict(session, "hims", [(dev["id"], "engineer")])
    feed = api.state.bus

    def publish() -> None:
        feed.publish("activity", {"id": "9001", "actor": "Orchestrator", "action": "Run started",
                                  "detail": "the secret project", "projectId": "hims"})
        feed.publish("change", {"op": "put", "collection": "projects", "doc": {"id": "hims", "name": "HIMS v3"}})
        feed.publish("review", {"ref": "REV-9", "projectId": "hims", "status": "done"})
        feed.publish("activity", {"id": "9002", "actor": "Rajat", "action": "Signed in", "projectId": None})
        feed.publish("activity", {"id": "9003", "actor": "Orchestrator", "action": "Run started",
                                  "detail": "the open project", "projectId": "erp"})

    async with signed_in(api, "other@example.com") as outsider:
        token = outsider.cookies.get(deps.COOKIE)
        assert token
        seen = await heard(api, token, publish, want=2)
    assert [e.get("id") for e in seen] == ["9002", "9003"]
    assert not [e for e in seen if json.dumps(e).find("hims") >= 0]

    # And for somebody the project is open to, nothing is dropped at all.
    async with signed_in(api, "dev@example.com") as listed:
        token = listed.cookies.get(deps.COOKIE)
        assert token
        seen = await heard(api, token, publish, want=5)
    assert [e.get("id") or e.get("ref") or e["doc"]["id"] for e in seen] == \
        ["9001", "hims", "REV-9", "9002", "9003"]


# ── the activity log ─────────────────────────────────────────────

async def test_the_activity_log_and_its_figures_leave_out_a_project_you_cannot_see(
        api: FastAPI, client: AsyncClient, session):
    """A feed is only as closed as its log. And the figures beside it are counted over the same rows:
    a total that includes lines a person will never be shown is a number they cannot reconcile with
    the feed in front of them — and it tells them how busy a project they were never told about is.
    """
    dev = await add(client, "dev@example.com", "Dev", ["engineer"])
    await add(client, "other@example.com", "Other", ["engineer"])
    await restrict(session, "erp", [(dev["id"], "engineer")])
    closed = len(rows("activity", projectId="erp"))
    every = len(WORKSPACE["activity"])
    assert closed and every > closed                     # the fixture has some of both

    async with signed_in(api, "other@example.com") as outsider:
        feed = (await outsider.get("/activity")).json()
        figures = (await outsider.get("/activity/summary")).json()
    assert len(feed) == every - closed
    assert not [e for e in feed if e["projectId"] == "erp"]
    assert figures["total"] == every - closed
    assert figures["through"] == max(int(e["id"]) for e in feed)

    # The people it is open to, and the Owner a restriction never locks out, still read all of it.
    async with signed_in(api, "dev@example.com") as listed:
        assert len((await listed.get("/activity")).json()) == every
        assert (await listed.get("/activity/summary")).json()["total"] == every
    assert (await client.get("/activity/summary")).json()["total"] == every


async def test_the_log_is_cut_in_the_query_and_not_out_of_the_answer(api: FastAPI, client: AsyncClient,
                                                                     session):
    """A page of 200 must be 200 lines, not "200 minus the ones you cannot see" — otherwise `offset`
    walks a list that changes under it and a screen scrolling the log skips rows at every page."""
    await add(client, "other@example.com", "Other", ["engineer"])
    await restrict(session, "erp", [])
    # The newest lines in the fixture belong to the closed project, which is the case that tells the
    # two apart: cut out of the answer, a page of one comes back empty and the screen stops scrolling.
    newest = (await client.get("/activity", params={"limit": 1})).json()
    assert newest[0]["projectId"] == "erp"

    async with signed_in(api, "other@example.com") as outsider:
        first = (await outsider.get("/activity", params={"limit": 1})).json()
        second = (await outsider.get("/activity", params={"limit": 1, "offset": 1})).json()
    assert len(first) == 1 and first[0]["projectId"] != "erp"
    assert not second or second[0]["id"] != first[0]["id"]


# ── the token ────────────────────────────────────────────────────

async def test_an_access_token_still_sees_the_projects_its_owner_is_listed_on(
        api: FastAPI, client: AsyncClient, session):
    """A token acts as the person who made it, and never as more — but it was acting as less than the
    person in the one way that cannot be granted back: it dropped their project grants, so `nc` and
    every script answered 404 for a restricted project to the people it was made for."""
    dev = await add(client, "dev@example.com", "Dev", ["engineer"])
    await add(client, "other@example.com", "Other", ["engineer"])
    await restrict(session, "hims", [(dev["id"], "engineer")])

    tokens = TokenService(session)
    listed = await IdentityService(session).need(dev["id"])
    _, secret = await tokens.create(listed, name="nc on my laptop", scopes=[], expires_days=None)
    through_the_token = await tokens.person(secret)
    assert set(through_the_token.project_rights) == {"hims"}
    assert through_the_token.may_see("hims", True)

    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api",
                           headers={**HEADERS, "Authorization": f"Bearer {secret}"}) as scripted:
        assert (await scripted.get("/projects/hims")).status_code == 200
        assert sorted(p["id"] for p in (await scripted.get("/projects")).json()) == ["erp", "hims"]

    # And narrowed inside a project it is still that token: what a script is refused for says which
    # token was refused, which is the only way to find the one to revoke.
    inside = await deps.scoped("runs:run")(asking("hims"), through_the_token, session)
    assert inside.token_id == through_the_token.token_id
    assert set(inside.project_rights) == {"hims"}

    # A token of somebody the project is closed to is closed to it too: the grant is the person's.
    outsider = await IdentityService(session).need(
        next(p["id"] for p in (await client.get("/admin/users")).json() if p["email"] == "other@example.com"))
    _, theirs = await tokens.create(outsider, name="theirs", scopes=[], expires_days=None)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api",
                           headers={**HEADERS, "Authorization": f"Bearer {theirs}"}) as scripted:
        assert (await scripted.get("/projects/hims")).status_code == 404


# ── the document ─────────────────────────────────────────────────

async def test_a_project_document_says_whether_it_is_restricted(api: FastAPI, client: AsyncClient,
                                                                session):
    """The screens cannot show a switch whose state the server never sends. It is not what hides a
    project — only the people who may already see it are ever handed the document."""
    assert [p["restricted"] for p in (await client.get("/projects")).json()] == [False, False]
    await restrict(session, "hims", [])
    by_id = {p["id"]: p for p in (await client.get("/projects")).json()}
    assert by_id["hims"]["restricted"] is True and by_id["erp"]["restricted"] is False
    assert (await client.get("/projects/hims")).json()["restricted"] is True


# ── the work inside a project ────────────────────────────────────

def asking(pid: str) -> Request:
    """A request that names this project in its path, and nothing else — what `scoped` reads."""
    return Request({"type": "http", "method": "GET", "path": f"/projects/{pid}/code", "headers": [],
                    "query_string": b"", "path_params": {"pid": pid}})


async def test_a_permission_asked_inside_a_project_is_the_one_the_project_grants(client: AsyncClient,
                                                                                 session):
    """The narrow-only rule, enforced where the work is reached rather than only where the project is.

    An Approver holds `projects:onboard` across the workspace. Listed in a restricted project as an
    Engineer, they do not hold it *there* — and the person handed on to the route is narrowed too, so
    a check the route makes for itself cannot answer with more than the project allows.
    """
    lead = await add(client, "lead@example.com", "Lead", ["approver"])
    await add(client, "nobody@example.com", "Nobody", ["engineer"])
    await restrict(session, "hims", [(lead["id"], "engineer")])
    identity = IdentityService(session)
    inside, outside = await identity.need(lead["id"]), await identity.need(
        next(p["id"] for p in (await client.get("/admin/users")).json() if p["email"] == "nobody@example.com"))

    # Open project: exactly `require`, and the same person, unchanged.
    allowed = await deps.scoped("projects:onboard")(asking("erp"), inside, session)
    assert allowed.permissions == inside.permissions

    # Restricted, and they are listed — but an Engineer does not onboard.
    with pytest.raises(Denied):
        await deps.scoped("projects:onboard")(asking("hims"), inside, session)
    narrowed = await deps.scoped("runs:run")(asking("hims"), inside, session)
    assert "projects:onboard" not in narrowed.permissions and "runs:run" in narrowed.permissions

    # Restricted, and they are not listed: 404 before the permission is even weighed.
    with pytest.raises(NotFound):
        await deps.scoped("runs:run")(asking("hims"), outside, session)
    with pytest.raises(NotFound):
        await deps.scoped()(asking("hims"), outside, session)

    # A project id that is not a project at all is left to the route to answer in its own words.
    unknown = await deps.scoped("runs:run")(asking("no-such-project"), outside, session)
    assert unknown is outside


async def test_a_restricted_projects_code_is_not_there_for_anyone_it_is_closed_to(
        api: FastAPI, client: AsyncClient, session):
    """`/projects/{pid}/code` is where a project's work is actually read: its file tree, its search,
    its symbols. Hiding the project and leaving these open would have been decoration."""
    lead = await add(client, "lead@example.com", "Lead", ["approver"])
    await add(client, "other@example.com", "Other", ["engineer"])
    await restrict(session, "hims", [(lead["id"], "engineer")])

    async with signed_in(api, "other@example.com") as outsider:
        assert (await outsider.get("/projects/hims/code")).status_code == 404
        assert (await outsider.get("/projects/hims/code/files")).status_code == 404
        assert (await outsider.get("/projects/hims/mentions")).status_code == 404
        assert (await outsider.post("/projects/hims/code/reindex")).status_code == 404
        assert (await outsider.get("/projects/erp/code")).status_code == 200

    async with signed_in(api, "lead@example.com") as listed:
        assert (await listed.get("/projects/hims/code")).status_code == 200
        # Listed, so the project is there — with the rights the project gives, which are fewer.
        refused = await listed.post("/projects/hims/code/reindex")
        assert refused.status_code == 403
        assert "projects:onboard" in refused.text
