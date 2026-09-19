"""The admin screens on the new stack: people, roles, teams, the workspace and the audit log.

Signed in as the first Owner, against a real Postgres, inside one rolled-back transaction. A second
client signs in as somebody lesser wherever the interesting question is what they are *not* allowed
to do — a permission check that is only ever exercised by an Owner is not a permission check.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.services.identity import MIN_PASSWORD, IdentityService
from app.settings import Settings
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PASSWORD = "a long enough password"


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
        await c.post("/auth/setup", json=OWNER)           # an Owner holds every permission
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


async def test_the_people_screen_gets_every_account_with_its_roles_and_teams(client: AsyncClient):
    people = (await client.get("/admin/users")).json()
    assert len(people) == 1
    owner = people[0]
    assert set(owner) == {"id", "email", "name", "status", "roles", "teams", "lastLoginAt", "createdAt"}
    assert owner["roles"] == ["owner"] and owner["teams"] == []
    assert owner["status"] == "active" and owner["createdAt"] and owner["lastLoginAt"]


async def test_a_person_is_added_re_roled_and_turned_off(client: AsyncClient):
    person = await add(client, "dev@example.com", "Dev", ["engineer"])
    assert person["roles"] == ["engineer"] and person["status"] == "active"

    assert (await client.post("/admin/users", json={"email": "dev@example.com", "name": "Again",
                                                    "password": PASSWORD, "roles": []})).status_code == 409
    unknown = await client.post("/admin/users", json={"email": "x@example.com", "name": "X",
                                                      "password": PASSWORD, "roles": ["wizard"]})
    assert unknown.status_code == 422 and "Unknown role" in unknown.json()["detail"]

    changed = await client.patch(f"/admin/users/{person['id']}", json={"name": "Dev Two",
                                                                       "roles": ["approver", "engineer"]})
    assert changed.status_code == 200
    assert changed.json()["name"] == "Dev Two" and changed.json()["roles"] == ["approver", "engineer"]

    off = await client.patch(f"/admin/users/{person['id']}", json={"status": "disabled"})
    assert off.status_code == 200 and off.json()["status"] == "disabled"

    assert (await client.post(f"/admin/users/{person['id']}/password", json={"password": "short"})
            ).status_code == 422
    reset = await client.post(f"/admin/users/{person['id']}/password", json={"password": "another long one"})
    assert reset.status_code == 200 and reset.json() == {"ok": True}


async def test_only_an_owner_grants_the_owner_role_and_the_last_one_stays(client: AsyncClient, api: FastAPI):
    """The rule that actually bites: an Admin runs the workspace but cannot unseat the last Owner."""
    await add(client, "admin@example.com", "Admin", ["admin"])
    owner = next(p for p in (await client.get("/admin/users")).json() if p["email"] == OWNER["email"])

    async with signed_in(api, "admin@example.com") as other:
        promoting = await other.post("/admin/users", json={"email": "new@example.com", "name": "New",
                                                           "password": PASSWORD, "roles": ["owner"]})
        assert promoting.status_code == 403 and "Owner" in promoting.json()["detail"]

        demoting = await other.patch(f"/admin/users/{owner['id']}", json={"roles": ["viewer"]})
        assert demoting.status_code == 403

        disabling = await other.patch(f"/admin/users/{owner['id']}", json={"status": "disabled"})
        assert disabling.status_code == 409 and "last active Owner" in disabling.json()["detail"]

    assert (await client.get("/admin/users")).json()          # the Owner is still there, still active


async def test_a_built_in_role_cannot_be_edited_or_deleted(client: AsyncClient):
    roles = (await client.get("/admin/roles")).json()
    assert len(roles) == 5 and all(r["builtin"] for r in roles)
    owner = next(r for r in roles if r["id"] == "owner")
    assert set(owner) == {"id", "name", "description", "builtin", "permissions", "members"}
    assert owner["members"] == 1 and "workspace:admin" in owner["permissions"]

    changed = await client.patch("/admin/roles/owner", json={"permissions": []})
    assert changed.status_code == 409 and "Built-in" in changed.json()["detail"]
    assert (await client.delete("/admin/roles/owner")).status_code == 409
    assert (await client.patch("/admin/roles/nobody", json={"name": "N"})).status_code == 404


async def test_a_custom_role_is_made_changed_and_removed(client: AsyncClient):
    made = await client.post("/admin/roles", json={"name": "QA Lead", "description": "ours",
                                                   "permissions": ["tasks:write"]})
    assert made.status_code == 201
    role = made.json()
    assert role["id"] == "qa-lead" and role["builtin"] is False and role["members"] == 0

    bad = await client.post("/admin/roles", json={"name": "Nope", "permissions": ["tasks:fly"]})
    assert bad.status_code == 422 and "tasks:fly" in bad.json()["detail"]

    # Catalogue order, not alphabetical: it is the order the access screen draws its groups in.
    changed = await client.patch("/admin/roles/qa-lead", json={"permissions": ["tasks:write", "plans:decide"]})
    assert changed.status_code == 200 and changed.json()["permissions"] == ["plans:decide", "tasks:write"]

    person = await add(client, "qa@example.com", "QA", ["qa-lead"])
    worn = await client.delete("/admin/roles/qa-lead")
    assert worn.status_code == 409 and "still have this role" in worn.json()["detail"]

    await client.patch(f"/admin/users/{person['id']}", json={"roles": ["viewer"]})
    gone = await client.delete("/admin/roles/qa-lead")
    assert gone.status_code == 200 and gone.json()["id"] == "qa-lead"
    assert all(r["id"] != "qa-lead" for r in (await client.get("/admin/roles")).json())


async def test_a_team_carries_its_members_and_refuses_a_stranger(client: AsyncClient):
    one = await add(client, "one@example.com", "One", ["engineer"])
    two = await add(client, "two@example.com", "Two", ["engineer"])

    made = await client.post("/admin/teams", json={"name": "Platform", "description": "the core",
                                                   "members": [one["id"], one["id"]]})
    assert made.status_code == 201
    team = made.json()
    assert set(team) == {"id", "name", "description", "members", "createdAt"}
    assert team["members"] == [one["id"]] and team["createdAt"]

    # citext: the name is the same name however it was typed.
    assert (await client.post("/admin/teams", json={"name": "platform", "members": []})).status_code == 409
    stranger = await client.post("/admin/teams", json={"name": "Ghosts", "members": ["u_nobody"]})
    assert stranger.status_code == 422 and "u_nobody" in stranger.json()["detail"]

    listed = next(p for p in (await client.get("/admin/users")).json() if p["id"] == one["id"])
    assert listed["teams"] == [team["id"]]

    swapped = await client.patch(f"/admin/teams/{team['id']}", json={"name": "Core",
                                                                     "members": [two["id"], one["id"]]})
    assert swapped.status_code == 200 and swapped.json()["name"] == "Core"
    assert sorted(swapped.json()["members"]) == sorted([one["id"], two["id"]])

    assert (await client.delete(f"/admin/teams/{team['id']}")).json()["id"] == team["id"]
    assert (await client.get("/admin/teams")).json() == []
    assert (await client.delete(f"/admin/teams/{team['id']}")).status_code == 404


async def test_the_catalogue_is_the_one_the_roles_screen_groups_by(client: AsyncClient):
    from app.data import catalogue as shipped

    catalogue = (await client.get("/admin/permissions")).json()
    assert len(catalogue) == len(shipped.PERMISSIONS) == 21
    assert all(set(p) >= {"id", "label", "group", "description"} for p in catalogue)
    assert {p["group"] for p in catalogue} <= {"Work", "Gates", "Knowledge", "Platform", "Admin"}
    assert [p["id"] for p in catalogue[:2]] == ["plans:compile", "plans:decide"]


async def test_anyone_signed_in_reads_the_catalogue_the_screens_name_things_by(api: FastAPI, client: AsyncClient):
    """A Viewer holds no permission, and is still shown role names and told which permission a button
    needs — so the names come from a route any signed-in person may read, not from the admin ones."""
    from app.data import catalogue as shipped

    custom = await client.post("/admin/roles", json={"name": "QA Lead", "permissions": ["tasks:write"]})
    assert custom.status_code == 201
    await add(client, "reader@example.com", "Reader", ["viewer"])

    async with signed_in(api, "reader@example.com") as viewer:
        assert (await viewer.get("/admin/permissions")).status_code == 403
        answer = await viewer.get("/auth/catalogue")
    assert answer.status_code == 200
    body = answer.json()
    assert set(body) == {"permissions", "roles", "agents"}

    assert body["permissions"] == (await client.get("/admin/permissions")).json()
    assert [p["id"] for p in body["permissions"]] == [p.id for p in shipped.PERMISSIONS]
    assert all(set(p) == {"id", "group", "label", "description"} for p in body["permissions"])

    roles = {r["id"]: r for r in body["roles"]}
    assert all(set(r) == {"id", "name", "description", "builtin", "permissions"} for r in body["roles"])
    assert [r["id"] for r in body["roles"][:len(shipped.ROLES)]] == [r.id for r in shipped.ROLES]
    assert roles["approver"]["name"] == "AI Project Manager" and roles["approver"]["builtin"] is True
    assert roles["viewer"]["permissions"] == []
    assert roles[custom.json()["id"]] == {**{k: custom.json()[k] for k in ("id", "name", "description")},
                                          "builtin": False, "permissions": ["tasks:write"]}

    assert sorted(a["id"] for a in body["agents"]) == sorted(a.id for a in shipped.AGENTS)
    assert all(set(a) == {"id", "name", "role", "icon"} for a in body["agents"])   # identity, no stats

    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as nobody:
        assert (await nobody.get("/auth/catalogue")).status_code == 401


async def test_the_workspace_counts_what_is_actually_there(client: AsyncClient):
    workspace = (await client.get("/admin/workspace")).json()
    assert workspace == {"name": "Acme", "createdAt": workspace["createdAt"], "people": 1,
                         "roles": 5, "builtinRoles": 5, "teams": 0,
                         "security": {"sessionDays": 14, "minPassword": MIN_PASSWORD, "loginAttempts": 5,
                                      "lockoutSeconds": 30}}
    assert workspace["createdAt"]

    await add(client, "counted@example.com", "Counted", ["viewer"])
    await client.post("/admin/teams", json={"name": "Squad", "members": []})
    renamed = await client.patch("/admin/workspace", json={"name": "Acme Two"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Acme Two"
    assert renamed.json()["people"] == 2 and renamed.json()["teams"] == 1

    await client.post("/admin/roles", json={"name": "Release manager", "permissions": ["runs:merge"]})
    counted = (await client.get("/admin/workspace")).json()
    assert counted["roles"] == 6 and counted["builtinRoles"] == 5   # a custom role is not a built-in one


async def test_the_workspace_reports_the_sign_in_rules_the_identity_service_enforces(
        client: AsyncClient, api: FastAPI, session: AsyncSession):
    """The screen used to print "14 days", "10 characters" and "5 … 30 s" as text. Where the API runs
    with other settings, those sentences described rules nobody was applying; the answer comes from
    the service that applies them, and changes with it."""
    stricter = Settings(session_days=3, login_attempts=2, lockout_seconds=900)
    api.dependency_overrides[deps.identity_service] = lambda: IdentityService(session, config=stricter)
    security = (await client.get("/admin/workspace")).json()["security"]
    assert security == {"sessionDays": 3, "minPassword": MIN_PASSWORD, "loginAttempts": 2, "lockoutSeconds": 900}

    # And the password floor it reports is the one a new account is refused under.
    short = await client.post("/admin/users", json={"email": "short@example.com", "name": "Short",
                                                    "password": "x" * (MIN_PASSWORD - 1), "roles": ["viewer"]})
    assert short.status_code == 422
    assert str(MIN_PASSWORD) in short.json()["detail"]


async def test_every_change_lands_in_the_audit_log_and_it_pages_backwards(client: AsyncClient):
    person = await add(client, "logged@example.com", "Logged", ["viewer"])
    await client.patch(f"/admin/users/{person['id']}", json={"roles": ["engineer"]})
    await client.post("/admin/roles", json={"name": "Reviewer", "permissions": ["runs:merge"]})
    await client.patch("/admin/workspace", json={"name": "Acme Two"})

    log = (await client.get("/admin/audit")).json()
    actions = [e["action"] for e in log]
    assert actions[:4] == ["workspace.update", "role.create", "user.update", "user.create"]
    assert actions[-1] == "workspace.setup"                   # the very first thing that ever happened

    entry = next(e for e in log if e["action"] == "user.create")
    assert set(entry) == {"seq", "at", "user", "userId", "action", "target", "detail", "ip"}
    assert entry["user"] == "Rajat" and entry["target"] == "logged@example.com"
    assert entry["detail"] == {"roles": ["viewer"]}
    assert next(e for e in log if e["action"] == "user.update")["detail"] == {"roles": ["engineer"]}

    first = (await client.get("/admin/audit", params={"limit": 1})).json()
    assert len(first) == 1 and first[0]["seq"] == log[0]["seq"]
    older = (await client.get("/admin/audit", params={"limit": 2, "before": first[0]["seq"]})).json()
    assert [e["seq"] for e in older] == [e["seq"] for e in log[1:3]]


async def test_a_viewer_is_not_let_into_the_admin_screens(client: AsyncClient, api: FastAPI):
    await add(client, "viewer@example.com", "Viewer", ["viewer"])
    async with signed_in(api, "viewer@example.com") as watcher:
        assert (await watcher.get("/admin/users")).status_code == 403
        assert (await watcher.get("/admin/roles")).status_code == 403
        assert (await watcher.get("/admin/teams")).status_code == 403
        assert (await watcher.get("/admin/permissions")).status_code == 403
        assert (await watcher.get("/admin/audit")).status_code == 403
        assert (await watcher.post("/admin/roles", json={"name": "Mine"})).status_code == 403
        assert (await watcher.patch("/admin/workspace", json={"name": "Mine"})).status_code == 403
        # Everyone signed in may read the workspace — the sidebar shows its name.
        assert (await watcher.get("/admin/workspace")).status_code == 200


async def test_the_people_list_is_the_whole_workspace_not_the_first_page(client: AsyncClient,
                                                                        session: AsyncSession):
    """It stopped at 500 with no total and no cursor, so the People screen said "500 people" beside a
    header saying 601, and every team member past the cut rendered as a raw id with no name."""
    from app.models import User
    from app.repositories.base import MAX_LIMIT

    session.add_all([User(id=f"u_bulk{n:04}", email=f"bulk{n:04}@example.com", name=f"Bulk {n}",
                          password_hash="scrypt$1$1$1$AA==$AA==", roles=[])
                     for n in range(MAX_LIMIT + 20)])
    await session.flush()

    people = (await client.get("/admin/users")).json()
    workspace = (await client.get("/admin/workspace")).json()
    assert len(people) == workspace["people"] > MAX_LIMIT


async def test_a_deliberate_limit_of_zero_means_none_not_a_hundred(client: AsyncClient):
    """`limit or DEFAULT_LIMIT` read a deliberate 0 as an absence, and answered with a hundred rows."""
    assert len((await client.get("/admin/audit", params={"limit": 0})).json()) <= 1


async def test_the_roles_screen_opens_on_owner(client: AsyncClient):
    """Written in one transaction, the built-ins share a timestamp, so the tie fell to the id and
    put Admin first on a screen whose whole meaning is that Owner comes first."""
    roles = (await client.get("/admin/roles")).json()
    assert [r["id"] for r in roles][:2] == ["owner", "admin"]
