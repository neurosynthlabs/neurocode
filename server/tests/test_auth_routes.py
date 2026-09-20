"""The auth routes on the new stack, over real HTTP, inside one rolled-back transaction.

The app is given this test's session, so everything it writes is undone at the end and no test can
see another's accounts. What is checked here is the *contract* — paths, JSON, cookie, status codes —
because the frontend must not be able to tell that the backend underneath it changed.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.data.loader import sync_roles
from app.settings import settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await sync_roles(session)                    # the built-in roles, as every real start-up syncs them
    await session.flush()
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        yield c


async def test_a_fresh_workspace_asks_to_be_set_up(client: AsyncClient):
    status = (await client.get("/auth/status")).json()
    assert status == {"needsSetup": True, "setupNeedsToken": False, "user": None, "workspace": None,
                      "machineAccess": True}


async def test_a_server_with_a_setup_token_lets_only_its_holder_create_the_first_owner(client: AsyncClient,
                                                                                    monkeypatch):
    # A server on the internet: whoever finds the address first must not be able to claim the workspace.
    monkeypatch.setattr(settings(), "setup_token", "s3t-up-token")
    assert (await client.get("/auth/status")).json()["setupNeedsToken"] is True
    for token in (None, "", "not-the-token"):
        body = OWNER if token is None else {**OWNER, "setupToken": token}
        refused = await client.post("/auth/setup", json=body)
        assert refused.status_code == 403 and "NEUROCODE_SETUP_TOKEN" in refused.json()["detail"]
    assert (await client.get("/auth/status")).json()["needsSetup"] is True       # nothing was made
    made = await client.post("/auth/setup", json={**OWNER, "setupToken": " s3t-up-token "})
    assert made.status_code == 201
    assert (await client.get("/auth/status")).json()["setupNeedsToken"] is False


async def test_a_server_that_opens_no_machine_says_so_before_a_screen_offers_a_folder(client: AsyncClient,
                                                                                      monkeypatch):
    """A hosted server answers every machine route with 404. The web app is told once, on the session, so the
    Workbench, the folder pickers and Blueprint scaffolding say why instead of offering what cannot happen."""
    monkeypatch.setattr(settings(), "machine_access", False)
    assert (await client.get("/auth/status")).json()["machineAccess"] is False
    made = await client.post("/auth/setup", json=OWNER)
    assert made.status_code == 201 and made.json()["machineAccess"] is False
    me = (await client.get("/auth/me")).json()
    # The Owner still holds the permission — it is the server that opens nothing.
    assert me["machineAccess"] is False and "machine:access" in me["user"]["permissions"]
    assert (await client.get("/machine/places")).status_code == 404


async def test_the_session_cookie_is_https_only_when_the_server_is_served_over_https(client: AsyncClient,
                                                                                    monkeypatch):
    monkeypatch.setattr(settings(), "cookie_secure", True)
    made = await client.post("/auth/setup", json=OWNER)
    cookie = made.headers["set-cookie"]
    assert "nc_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie


async def test_setup_creates_the_first_owner_and_signs_them_in(client: AsyncClient):
    made = await client.post("/auth/setup", json=OWNER)
    assert made.status_code == 201
    body = made.json()
    assert body["workspace"] == {"name": "Acme"} and body["user"]["email"] == OWNER["email"]
    assert "owner" in body["user"]["roles"] and "workspace:admin" in body["user"]["permissions"]
    assert "nc_session" in made.cookies

    assert (await client.get("/auth/me")).json()["user"]["name"] == "Rajat"
    assert (await client.get("/auth/status")).json()["needsSetup"] is False
    again = await client.post("/auth/setup", json={**OWNER, "email": "second@example.com"})
    assert again.status_code == 409 and "already set up" in again.json()["detail"]


async def test_signing_in_and_out(client: AsyncClient):
    await client.post("/auth/setup", json=OWNER)
    await client.post("/auth/logout")
    assert (await client.get("/auth/me")).status_code == 401

    wrong = await client.post("/auth/login", json={"email": OWNER["email"], "password": "nope"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Wrong email or password."

    ok = await client.post("/auth/login", json={"email": "OWNER@EXAMPLE.COM", "password": OWNER["password"]})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == OWNER["email"]
    assert (await client.get("/auth/me")).status_code == 200


async def test_changing_your_password_needs_the_old_one(client: AsyncClient):
    await client.post("/auth/setup", json=OWNER)

    refused = await client.post("/auth/password", json={"current": "wrong one", "new": "another long password"})
    assert refused.status_code == 403

    changed = await client.post("/auth/password", json={"current": OWNER["password"],
                                                        "new": "another long password"})
    assert changed.status_code == 200 and changed.json() == {"ok": True}
    assert (await client.get("/auth/me")).status_code == 200          # this session is kept
    assert (await client.post("/auth/login", json={"email": OWNER["email"],
                                                   "password": "another long password"})).status_code == 200


async def test_a_cookie_alone_cannot_change_anything(client: AsyncClient):
    """The CSRF guard: another site can make the browser send the cookie, not the header."""
    await client.post("/auth/setup", json=OWNER)
    naked = await client.post("/auth/logout", headers={"X-NC-Client": ""})
    assert naked.status_code == 403 and "X-NC-Client" in naked.json()["detail"]
