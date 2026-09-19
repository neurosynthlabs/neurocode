"""Personal access tokens over HTTP: made once and shown once, a bearer that acts as its person within its
scopes, `machine:access` only when named, 401 once revoked or expired, managed only from a session.

Signed in as the first Owner, inside the suite's rolled-back transaction. Requests made with a token use
a client of their own that holds no cookie — exactly what a script or the `nc` client sends.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.data.base import utcnow
from app.services import machine
from app.services.identity import IdentityService
from app.services.tokens import PREFIX, TokenService
from app.settings import settings as real_settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def api(catalogued: AsyncSession) -> FastAPI:
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield catalogued

    made.dependency_overrides[deps.session] = use_the_test_session
    return made


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


def bearer(api: FastAPI, token: str) -> AsyncClient:
    """A caller with a token and nothing else: no cookie, and no X-NC-Client header."""
    return AsyncClient(transport=ASGITransport(app=api), base_url="http://api",
                       headers={"Authorization": f"Bearer {token}"})


async def make(client: AsyncClient, **body) -> dict:
    made = await client.post("/tokens", json={"name": "laptop", **body})
    assert made.status_code == 201, made.text
    return made.json()


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "work").mkdir()
    configured = real_settings().model_copy(update={"machine_roots": str(tmp_path / "work"), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    return tmp_path / "work"


# ── made once, shown once ────────────────────────────────────────
async def test_a_token_is_shown_once_and_only_its_prefix_after(client: AsyncClient, catalogued: AsyncSession):
    made = await make(client, name="  ci on the laptop  ", expiresInDays=30)
    assert made["token"].startswith(PREFIX) and len(made["token"]) > 40
    assert made["name"] == "ci on the laptop" and made["prefix"] == made["token"][:len(PREFIX) + 4]
    assert made["scopes"] == [] and made["allScopes"] is True and made["state"] == "active"
    assert made["lastUsedAt"] is None and made["revokedAt"] is None
    assert made["expiresAt"] is not None

    listed = (await client.get("/tokens")).json()
    assert listed["total"] == 1 and listed["nextOffset"] is None
    assert listed["items"][0]["id"] == made["id"] and "token" not in listed["items"][0]
    assert made["token"] not in str(listed)

    stored = (await catalogued.execute(select(m.ApiToken).where(m.ApiToken.id == made["id"]))).scalar_one()
    assert stored.token_hash != made["token"] and made["token"] not in stored.token_hash

    audit = (await catalogued.execute(select(m.AuditEntry).where(m.AuditEntry.action == "token.create"))).scalars().all()
    assert len(audit) == 1 and audit[0].detail["token"] == made["id"] and made["token"] not in str(audit[0].detail)


async def test_a_token_is_refused_what_its_person_does_not_hold_or_nobody_knows(client: AsyncClient,
                                                                              api: FastAPI):
    assert (await client.post("/admin/users", json={"email": "viewer@example.com", "name": "Vee",
                                                    "password": "long enough pass", "roles": ["viewer"]})
            ).status_code in (200, 201)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as viewer:
        assert (await viewer.post("/auth/login", json={"email": "viewer@example.com",
                                                       "password": "long enough pass"})).status_code == 200
        held = set((await viewer.get("/auth/me")).json()["user"]["permissions"])
        assert "runs:merge" not in held
        refused = await viewer.post("/tokens", json={"name": "merge", "scopes": ["runs:merge"]})
        assert refused.status_code == 403 and "runs:merge" in refused.json()["detail"]

    unknown = await client.post("/tokens", json={"name": "x", "scopes": ["runs:everything"]})
    assert unknown.status_code == 422 and "runs:everything" in unknown.json()["detail"]
    blank = await client.post("/tokens", json={"name": "   "})
    assert blank.status_code == 422
    too_long = await client.post("/tokens", json={"name": "x", "expiresInDays": 400})
    assert too_long.status_code == 422
    assert (await client.get("/tokens")).json()["total"] == 0


# ── the bearer ───────────────────────────────────────────────────
async def test_a_token_acts_as_its_person_without_machine_access_unless_named(client: AsyncClient, api: FastAPI,
                                                                            roots: Path):
    everything = await make(client)
    owner = set((await client.get("/auth/me")).json()["user"]["permissions"])
    assert "machine:access" in owner

    async with bearer(api, everything["token"]) as script:
        me = (await script.get("/auth/me")).json()["user"]
        assert me["email"] == OWNER["email"]
        assert set(me["permissions"]) == owner - {"machine:access"}
        # A change with a token needs no X-NC-Client: no cookie rides on it, so there is nothing to forge.
        added = await script.post("/memory/facts", json={"facts": [{"title": "Tax", "body": "Rounded once."}]})
        assert added.status_code == 201
        shell = await script.get("/machine/roots")
        assert shell.status_code == 403 and "machine:access" in shell.json()["detail"]

    named = await make(client, name="shell", scopes=["machine:access", "sessions:chat"])
    assert named["scopes"] == ["sessions:chat", "machine:access"]          # catalogue order
    async with bearer(api, named["token"]) as script:
        assert (await script.get("/machine/roots")).status_code == 200
        assert set((await script.get("/auth/me")).json()["user"]["permissions"]) == {"machine:access",
                                                                                     "sessions:chat"}


async def test_a_scoped_token_can_do_only_what_it_names(client: AsyncClient, api: FastAPI):
    made = await make(client, scopes=["memory:write"])
    async with bearer(api, made["token"]) as script:
        assert (await script.get("/projects")).status_code == 200          # reading needs a person, no more
        assert (await script.post("/memory/facts", json={"facts": [{"title": "A", "body": "B"}]})).status_code == 201
        refused = await script.post("/plans/compile", json={"requirement": "add a thing", "projectId": "nowhere"})
        assert refused.status_code == 403 and "plans:compile" in refused.json()["detail"]


async def test_a_token_follows_its_person_when_their_roles_change(client: AsyncClient, api: FastAPI,
                                                                  catalogued: AsyncSession):
    assert (await client.post("/admin/users", json={"email": "dev@example.com", "name": "Dev",
                                                    "password": "long enough pass", "roles": ["admin"]})
            ).status_code in (200, 201)
    dev = (await catalogued.execute(select(m.User).where(m.User.email == "dev@example.com"))).scalar_one()
    person = await IdentityService(catalogued).need(dev.id)
    _, secret = await TokenService(catalogued).create(person, name="dev", scopes=["runs:merge"], expires_days=None)
    async with bearer(api, secret) as script:
        assert "runs:merge" in (await script.get("/auth/me")).json()["user"]["permissions"]
        assert (await client.patch(f"/admin/users/{dev.id}", json={"roles": ["viewer"]})).status_code == 200
        assert (await script.get("/auth/me")).json()["user"]["permissions"] == []
        assert (await client.patch(f"/admin/users/{dev.id}", json={"status": "disabled"})).status_code == 200
        gone = await script.get("/auth/me")
        assert gone.status_code == 401


async def test_revoked_expired_and_unknown_tokens_are_refused_alike(client: AsyncClient, api: FastAPI,
                                                                   catalogued: AsyncSession):
    made = await make(client)
    async with bearer(api, made["token"]) as script:
        assert (await script.get("/projects")).status_code == 200
        revoked = await client.post(f"/tokens/{made['id']}/revoke")
        assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
        after = await script.get("/projects")
        assert after.status_code == 401 and "not valid" in after.json()["detail"]
        # Even a public route says so, rather than treating a dead token as nobody.
        assert (await script.get("/auth/status")).status_code == 401
    again = await client.post(f"/tokens/{made['id']}/revoke")
    assert again.status_code == 200 and again.json()["revokedAt"] == revoked.json()["revokedAt"]
    revokes = (await catalogued.execute(select(m.AuditEntry).where(m.AuditEntry.action == "token.revoke"))
               ).scalars().all()
    assert len(revokes) == 1

    lapsing = await make(client, name="short", expiresInDays=1)
    row = (await catalogued.execute(select(m.ApiToken).where(m.ApiToken.id == lapsing["id"]))).scalar_one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    await catalogued.flush()
    async with bearer(api, lapsing["token"]) as script:
        assert (await script.get("/projects")).status_code == 401
    listed = {t["id"]: t["state"] for t in (await client.get("/tokens")).json()["items"]}
    assert listed == {made["id"]: "revoked", lapsing["id"]: "expired"}

    async with bearer(api, PREFIX + "made-up") as script:
        assert (await script.get("/projects")).status_code == 401


async def test_last_used_is_written_at_most_once_a_minute(client: AsyncClient, api: FastAPI,
                                                         catalogued: AsyncSession):
    made = await make(client)
    row = (await catalogued.execute(select(m.ApiToken).where(m.ApiToken.id == made["id"]))).scalar_one()
    async with bearer(api, made["token"]) as script:
        await script.get("/projects")
        first = row.last_used_at
        assert first is not None
        await script.get("/projects")
        assert row.last_used_at == first
        row.last_used_at = first - timedelta(minutes=2)
        await catalogued.flush()
        await script.get("/projects")
        assert row.last_used_at > first - timedelta(minutes=2)
    assert (await client.get("/tokens")).json()["items"][0]["lastUsedAt"] is not None


# ── managed from a session only, and only one's own ──────────────
async def test_a_token_cannot_list_make_or_revoke_tokens(client: AsyncClient, api: FastAPI):
    made = await make(client, scopes=["machine:access"])
    async with bearer(api, made["token"]) as script:
        for answer in (await script.get("/tokens"),
                       await script.post("/tokens", json={"name": "more", "scopes": ["machine:access"]}),
                       await script.post(f"/tokens/{made['id']}/revoke")):
            assert answer.status_code == 403 and "signed-in session" in answer.json()["detail"]
    assert (await client.get("/tokens")).json()["total"] == 1


async def test_someone_elses_token_is_not_there(client: AsyncClient, catalogued: AsyncSession):
    other = await IdentityService(catalogued).create("other@example.com", "Other", "long enough pass", ["admin"])
    row, _ = await TokenService(catalogued).create(other, name="theirs", scopes=[], expires_days=None)
    assert (await client.post(f"/tokens/{row.id}/revoke")).status_code == 404
    assert row.revoked_at is None
    assert (await client.get("/tokens")).json()["total"] == 0


async def test_the_list_is_paged_with_a_ceiling(client: AsyncClient, api: FastAPI):
    for n in range(3):
        await make(client, name=f"t{n}")
    first = (await client.get("/tokens", params={"limit": 2})).json()
    assert [t["name"] for t in first["items"]] == ["t2", "t1"] and first["nextOffset"] == 2
    rest = (await client.get("/tokens", params={"limit": 2, "offset": 2})).json()
    assert [t["name"] for t in rest["items"]] == ["t0"] and rest["nextOffset"] is None
    assert (await client.get("/tokens", params={"limit": 1000})).status_code == 422
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api") as stranger:
        assert (await stranger.get("/tokens")).status_code == 401
