"""Rights by module: where each right is filed, the fences that were missing, and per-project grants.

Signed in against a real Postgres, inside one rolled-back transaction, with a second and third client
signed in as somebody lesser — because a permission check only an Owner ever exercises is not a check.

Two of these tests read no database at all. They are the ones worth keeping longest: one walks every
route in the app and fails any that carries no fence, and one holds the catalogue and the sidebar
against each other, so the map a person reads and the rights an admin grants cannot drift apart.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import ROUTERS, create_api
from app.data import catalogue as shipped
from app.data.loader import UPGRADE_MARKS, sync_roles, upgrade_custom_roles
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PASSWORD = "a long enough password"

REPO = Path(__file__).resolve().parents[2]


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


async def add(client: AsyncClient, email: str, name: str, roles: list[str]) -> dict:
    made = await client.post("/admin/users", json={"email": email, "name": name,
                                                   "password": PASSWORD, "roles": roles})
    assert made.status_code == 201, made.text
    return made.json()


async def role_with(client: AsyncClient, name: str, permissions: list[str]) -> str:
    made = await client.post("/admin/roles", json={"name": name, "permissions": permissions})
    assert made.status_code == 201, made.text
    return made.json()["id"]


# ── the two mechanical checks ────────────────────────────────────
#: Public on purpose. Signing in cannot need a session, and a webhook is authenticated by its own
#: token in the URL. The three WebSockets authenticate in the body, through `admit`.
OPEN_ON_PURPOSE = {
    ("POST", "/auth/setup"), ("POST", "/auth/login"), ("POST", "/auth/logout"), ("GET", "/auth/status"),
    ("POST", "/schedules/{schedule_id}/webhook"),
    ("WS", "/machine/terminals/{terminal_id}/ws"), ("WS", "/debug/{session_id}/ws"),
    ("WS", "/notebooks/kernels/{kernel_id}/ws"),
}

#: What counts as a fence: a dependency that decides whether this request may happen at all.
FENCES = {"current_person", "machine_person", "operator", "signed_in_person",
          "require.<locals>.dependency", "require_any.<locals>.dependency"}


def _dependency_names(dependant) -> set[str]:  # noqa: ANN001 — FastAPI's own internal type
    found = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            found.add(getattr(sub.call, "__qualname__", ""))
        found |= _dependency_names(sub)
    return found


def test_every_route_carries_a_fence():
    """The API is the fence and the UI is only a sign — so the thing worth automating is not the sign,
    it is the absence of a fence. A new route that forgets one fails here instead of shipping."""
    unfenced = []
    for router in ROUTERS:
        # A dependency on the router itself fences every route under it (`/extensions` does this).
        outer = {getattr(d.dependency, "__qualname__", "") for d in (router.dependencies or [])
                 if d.dependency is not None}
        for route in router.routes:
            if not isinstance(route, APIRoute | APIWebSocketRoute):
                continue
            methods = sorted(getattr(route, "methods", None) or ["WS"])
            if not (_dependency_names(route.dependant) | outer) & FENCES:
                unfenced += [(method, route.path) for method in methods]
    assert sorted(set(unfenced) - OPEN_ON_PURPOSE) == []
    # And the allow-list is not allowed to rot: every path on it is still a real, still-open route.
    assert set(unfenced) == OPEN_ON_PURPOSE


def test_every_right_is_filed_where_the_sidebar_files_it():
    """The catalogue and `src/lib/nav.ts` are two halves of one pair. A right filed under a module the
    sidebar does not have is a right nobody can find; a screen asking for a permission the catalogue
    does not have is a door that can never be opened."""
    modules = dict(shipped.MODULES)
    for p in shipped.PERMISSIONS:
        assert p.module in modules, p.id
        assert p.sub is None or p.sub in modules[p.module], p.id
        assert p.verb in shipped.VERBS, p.id
        assert p.label and p.description.endswith("."), p.id

    nav = (REPO / "src/lib/nav.ts").read_text()
    sections = set(re.findall(r"'([A-Za-z]+)'", nav.split("export type NavSection =")[1].split(";")[0]))
    assert set(modules) == sections

    subs = set(re.findall(r"sub: '([^']+)'", nav))
    for module, declared in modules.items():
        assert set(declared) <= subs, module

    known = {p.id for p in shipped.PERMISSIONS}
    asked = set(re.findall(r"'([a-z]+:[a-z]+)'", "".join(re.findall(r"perm: \[([^\]]*)\]", nav))))
    assert asked <= known, sorted(asked - known)


def test_the_catalogue_is_json_the_screens_can_read():
    """The file is part of the program, so its shape is checked where a person can see the whole of it."""
    raw = json.loads((REPO / "server/app/data/catalogue.json").read_text())
    assert len(raw["permissions"]) == 26
    assert all(set(p) >= {"id", "group", "module", "verb", "label", "description"} for p in raw["permissions"])
    ids = [p["id"] for p in raw["permissions"]]
    assert len(ids) == len(set(ids))
    for new in ("ops:read", "sessions:read", "people:read"):
        assert new in ids


# ── the one-off carry-over for custom roles ──────────────────────
async def test_a_custom_role_keeps_the_reads_it_already_had(session: AsyncSession):
    """A right added in a release fences a door that was open. `sync_roles` rewrites the built-in roles
    from the catalogue, so they pick the right up for free; a custom role nothing rewrites has to be
    handed it, once, or the release quietly takes away access the workspace already had."""
    await load_workspace(session)
    await session.execute(m.Pref.__table__.delete().where(m.Pref.id == UPGRADE_MARKS))
    for role_id, held in (("release-manager", []), ("team-lead", ["teams:manage"])):
        session.add(m.Role(id=role_id, name=role_id, description="", builtin=False, rank=99))
        await session.flush()
        for permission in held:
            session.add(m.RolePermission(role_id=role_id, permission=permission))
    await session.flush()

    assert sorted(await upgrade_custom_roles(session)) == ["w3.ops:read", "w3.people:read", "w3.sessions:read"]

    async def held(role_id: str) -> set[str]:
        rows = await session.execute(
            m.RolePermission.__table__.select().where(m.RolePermission.role_id == role_id))
        return {r.permission for r in rows}

    # Both could read the DevOps screen and everyone's sessions yesterday, so both still can.
    assert {"ops:read", "sessions:read"} <= await held("release-manager")
    assert {"ops:read", "sessions:read", "people:read"} <= await held("team-lead")
    # Only the one that could already manage people sees the directory; the other does not gain it.
    assert "people:read" not in await held("release-manager")


async def test_the_carry_over_happens_once_and_never_undoes_an_admin(session: AsyncSession):
    """The point of doing this in `sync_roles` rather than a migration is that it runs on every start —
    so it has to remember. An admin who deliberately takes a right off a custom role must be able to."""
    await load_workspace(session)
    await session.execute(m.Pref.__table__.delete().where(m.Pref.id == UPGRADE_MARKS))
    session.add(m.Role(id="release-manager", name="Release manager", description="", builtin=False, rank=99))
    await session.flush()

    assert await upgrade_custom_roles(session)
    assert await upgrade_custom_roles(session) == []          # nothing left to do

    await session.execute(m.RolePermission.__table__.delete().where(
        (m.RolePermission.role_id == "release-manager") & (m.RolePermission.permission == "ops:read")))
    await session.flush()
    await sync_roles(session)                                  # a restart

    rows = await session.execute(
        m.RolePermission.__table__.select().where(m.RolePermission.role_id == "release-manager"))
    assert "ops:read" not in {r.permission for r in rows}


# ── the fences that were missing ─────────────────────────────────
async def test_the_devops_screen_is_not_handed_to_anyone_signed_in(api: FastAPI, client: AsyncClient):
    """A Viewer could enumerate the containers and read the logs of the machine the API runs on."""
    nobody = await role_with(client, "Note taker", ["tasks:write"])
    await add(client, "viewer@example.com", "Viewer", ["viewer"])
    await add(client, "note@example.com", "Note", [nobody])

    async with signed_in(api, "note@example.com") as taker:
        refused = await taker.get("/ops/overview")
        assert refused.status_code == 403
        assert "ops:read" in refused.json()["detail"]
        assert (await taker.get("/ops/logs")).status_code == 403
        assert (await taker.get("/ops/containers")).status_code == 403

    async with signed_in(api, "viewer@example.com") as viewer:
        assert (await viewer.get("/ops/overview")).status_code == 200
        assert (await viewer.get("/ops/deliveries")).status_code == 200
        # What is running on the machine is the machine's business, not the workspace's.
        assert (await viewer.get("/ops/containers")).status_code == 403
        assert (await viewer.get("/ops/secrets")).status_code == 403

    assert (await client.get("/ops/containers")).status_code == 200     # the Owner holds machine:access


async def test_your_sessions_are_yours_and_someone_elses_needs_the_right(api: FastAPI, client: AsyncClient,
                                                                         session: AsyncSession):
    """A transcript is the one place a person's own code and whatever they pasted end up in plain text.

    Dev wears a custom role rather than Engineer, because every built-in role ships with
    `sessions:read` — so that the day this lands nobody loses a list they were already reading. A
    workspace that wants the fence to bite makes a role without it, which is the only way: the
    built-in five are written again from the catalogue on every start."""
    reader = await role_with(client, "Support", ["sessions:read"])
    mine_only = await role_with(client, "Contractor", ["sessions:chat"])
    await add(client, "dev@example.com", "Dev", [mine_only])
    await add(client, "support@example.com", "Support", [reader])
    for ref, who in (("CHAT-900", "Dev"), ("CHAT-901", "Rajat")):
        session.add(m.Chat(id=f"c-{ref}", ref=ref, project_id="erp", title=ref, started_by=who))
    await session.flush()

    async with signed_in(api, "dev@example.com") as dev:
        assert [c["ref"] for c in (await dev.get("/sessions")).json()] == ["CHAT-900"]
        assert (await dev.get("/sessions/CHAT-900")).status_code == 200
        refused = await dev.get("/sessions/CHAT-901")
        assert refused.status_code == 403 and "sessions:read" in refused.json()["detail"]
        assert (await dev.get("/sessions/CHAT-901/export")).status_code == 403
        assert (await dev.get("/sessions/CHAT-901/files/1")).status_code == 403
        assert (await dev.get("/sessions/CHAT-404")).status_code == 404

    async with signed_in(api, "support@example.com") as support:
        assert sorted(c["ref"] for c in (await support.get("/sessions")).json()) == ["CHAT-900", "CHAT-901"]
        assert (await support.get("/sessions/CHAT-900")).status_code == 200


async def test_the_hooks_list_is_the_one_extension_screen_behind_a_right(api: FastAPI, client: AsyncClient):
    """Everything else under /extensions is a name and a description. A hook is a command line."""
    nobody = await role_with(client, "Reader", ["tasks:write"])
    await add(client, "reader@example.com", "Reader", [nobody])

    async with signed_in(api, "reader@example.com") as reader:
        assert (await reader.get("/extensions/skills")).status_code == 200
        assert (await reader.get("/extensions/commands")).status_code == 200
        assert (await reader.get("/extensions/plugins")).status_code == 200
        refused = await reader.get("/extensions/hooks")
        assert refused.status_code == 403 and "settings:write" in refused.json()["detail"]

    assert (await client.get("/extensions/hooks")).status_code == 200


async def test_the_directory_can_be_read_without_being_changed(api: FastAPI, client: AsyncClient):
    """The one grant a workspace actually asks for that the first 23 rights could not express: see who
    is here, without being handed the power to reset their passwords."""
    lead = await role_with(client, "Team lead", ["people:read"])
    nobody = await role_with(client, "Note taker 2", ["tasks:write"])
    await add(client, "lead@example.com", "Lead", [lead])
    await add(client, "note2@example.com", "Note", [nobody])

    async with signed_in(api, "lead@example.com") as team_lead:
        assert (await team_lead.get("/admin/users")).status_code == 200
        assert (await team_lead.get("/admin/teams")).status_code == 200
        # Seeing who is here is not seeing which rights a role carries, nor changing anybody.
        assert (await team_lead.get("/admin/permissions")).status_code == 403
        assert (await team_lead.post("/admin/users", json={"email": "x@example.com", "name": "X",
                                                           "password": PASSWORD, "roles": []})).status_code == 403

    async with signed_in(api, "note2@example.com") as taker:
        assert (await taker.get("/admin/users")).status_code == 403


# ── per-project rights ───────────────────────────────────────────
async def restrict(session: AsyncSession, pid: str, grants: list[tuple[str, str]]) -> None:
    """Close a project and list who may work in it: `(user_id, role_id)` rows, as the Access tab will."""
    project = await session.get(m.Project, pid)
    assert project is not None
    project.restricted = True
    for user_id, role_id in grants:
        session.add(m.ProjectRole(project_id=pid, user_id=user_id, role_id=role_id))
    await session.flush()


async def test_a_restricted_project_stops_existing_for_anyone_not_listed(api: FastAPI, client: AsyncClient,
                                                                         session: AsyncSession):
    """404, not 403: whether there is a project here at all is itself something they were not told."""
    dev = await add(client, "dev@example.com", "Dev", ["engineer"])
    other = await add(client, "other@example.com", "Other", ["engineer"])
    await restrict(session, "hims", [(dev["id"], "engineer")])

    async with signed_in(api, "other@example.com") as outsider:
        assert [p["id"] for p in (await outsider.get("/projects")).json()] == ["erp"]
        assert (await outsider.get("/projects/hims")).status_code == 404
        assert (await outsider.get("/projects/hims/sources")).status_code == 404
        assert (await outsider.get("/projects/hims/references")).status_code == 404
        assert (await outsider.get("/projects/hims/instructions")).status_code == 404
        assert (await outsider.get("/projects/erp")).status_code == 200

    async with signed_in(api, "dev@example.com") as listed:
        assert sorted(p["id"] for p in (await listed.get("/projects")).json()) == ["erp", "hims"]
        assert (await listed.get("/projects/hims")).status_code == 200

    # An Owner is never locked out of a room in their own workspace.
    assert sorted(p["id"] for p in (await client.get("/projects")).json()) == ["erp", "hims"]
    assert other["id"]


async def test_a_project_grant_narrows_and_never_widens(api: FastAPI, client: AsyncClient,
                                                        session: AsyncSession):
    """Nobody ever gains a right from a project they did not already hold across the workspace, so
    reviewing somebody's access stays one screen."""
    from app.services.identity import IdentityService

    dev = await add(client, "dev@example.com", "Dev", ["engineer"])
    await restrict(session, "hims", [(dev["id"], "viewer")])

    person = await IdentityService(session).need(dev["id"])
    # Open project: exactly the workspace set, which is every project until someone restricts one.
    assert person.in_project("erp", False) == person.permissions
    # Restricted: the workspace set cut to what the grant carries. Viewer does not include runs:run.
    assert "runs:run" in person.permissions
    assert "runs:run" not in person.in_project("hims", True)
    assert "sessions:read" in person.in_project("hims", True)
    # And a grant cannot hand out what the person never had: an Engineer holds no workspace:admin,
    # so wearing a role that carries it inside one project does not produce it.
    assert "workspace:admin" not in person.in_project("hims", True)

    owner = await IdentityService(session).need(
        next(p["id"] for p in (await client.get("/admin/users")).json() if p["email"] == OWNER["email"]))
    assert owner.may_see("hims", True)
    assert {"workspace:admin", "roles:manage"} <= owner.in_project("hims", True)


async def test_what_a_person_holds_in_a_restricted_project_reaches_the_screens(api: FastAPI,
                                                                               client: AsyncClient,
                                                                               session: AsyncSession):
    """`canIn(projectId, …)` in the web app is fed from here; an open project is simply absent, which
    is what "use the workspace set" means."""
    dev = await add(client, "dev@example.com", "Dev", ["engineer"])
    await restrict(session, "hims", [(dev["id"], "viewer")])

    async with signed_in(api, "dev@example.com") as listed:
        me = (await listed.get("/auth/me")).json()["user"]
    assert set(me["projectRights"]) == {"hims"}
    assert "runs:run" not in me["projectRights"]["hims"]
    assert "ops:read" in me["projectRights"]["hims"]
