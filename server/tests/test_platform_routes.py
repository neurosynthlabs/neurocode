"""Projects and the MCP registry on the new stack.

The project card is the interesting one: every number on it is computed from what the database holds,
so it cannot disagree with the board the way a stored count used to.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from tests.fixtures.workspace import WORKSPACE, load_workspace, rows

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def test_a_project_card_is_computed_not_stored(client: AsyncClient):
    projects = (await client.get("/projects")).json()
    assert len(projects) == len(WORKSPACE["projects"])
    erp = next(p for p in projects if p["id"] == "erp")

    assert erp["work"]["tasks"] == len(rows("tasks", projectId="erp"))   # counted from the tasks table
    board = (await client.get("/tasks", params={"project": "erp"})).json()
    assert erp["work"]["running"] == sum(1 for t in board if t["status"] == "in_progress")
    assert erp["work"]["review"] == sum(1 for t in board if t["status"] == "review")
    assert erp["work"]["blocked"] == sum(1 for t in board if t["status"] == "blocked")


async def test_a_project_keeps_the_shape_the_screens_read(client: AsyncClient):
    erp = (await client.get("/projects/erp")).json()
    assert set(erp) >= {"id", "name", "codename", "stack", "kind", "status", "lines", "modules",
                        "dbTables", "storedProcs", "repo", "coverage", "work", "description", "understoodPct"}
    assert "memoryPct" not in erp                          # nothing measures it, so nothing says it
    assert erp["understoodPct"] is None                    # never indexed: no share to speak of
    assert isinstance(erp["lines"], str) and isinstance(erp["stack"], list)
    assert (await client.get("/projects/nope")).status_code == 404


async def test_the_mcp_registry_lists_and_registers(client: AsyncClient):
    servers = (await client.get("/mcp/servers")).json()
    assert len(servers) == len(WORKSPACE["mcp"])
    assert all(isinstance(s["tools"], list) for s in servers)
    # Nothing has checked them, so nothing about them is a measurement yet.
    assert all(s["checkedAt"] is None and s["latencyMs"] is None and "errorRate" not in s
               and "calls24h" not in s for s in servers)

    made = await client.post("/mcp/servers", json={
        "name": "ledger-tools", "transport": "stdio", "command": "npx ledger-mcp",
        "scope": "project", "defaultEffect": "ask", "config": json.dumps({"cmd": "npx"})})
    assert made.status_code == 201
    assert made.json()["untrusted"] is True                # registered here means trusted by nobody yet
    assert made.json()["status"] == "disconnected"
    assert made.json()["checkedAt"] is None and made.json()["resources"] is None and made.json()["tools"] == []
    assert len((await client.get("/mcp/servers")).json()) == len(WORKSPACE["mcp"]) + 1


async def test_the_same_server_cannot_be_registered_twice(client: AsyncClient):
    """A name is the key here, so the second one is refused in words rather than by a crash."""
    spec = {"name": "ledger-tools", "transport": "stdio", "command": "npx ledger-mcp",
            "scope": "project", "defaultEffect": "ask", "config": "{}"}
    assert (await client.post("/mcp/servers", json=spec)).status_code == 201

    again = await client.post("/mcp/servers", json=spec)
    assert again.status_code == 409 and "already registered" in again.json()["detail"]


async def test_a_config_that_is_not_json_is_refused(client: AsyncClient):
    refused = await client.post("/mcp/servers", json={
        "name": "broken", "transport": "http", "command": "x", "scope": "global",
        "defaultEffect": "deny", "config": "{not json"})
    assert refused.status_code == 422


async def test_onboarding_a_folder_through_the_route_writes_the_project_and_hands_off_the_work(
        client: AsyncClient, tmp_path, monkeypatch):
    # The route, not the service: a name the route uses but never imported only fails here, at request time.
    from app.api import routes_platform

    handed: list[tuple] = []

    async def hand_off(open_session, jobs, job, *args):
        handed.append((job.__name__, args[-1].repo))

    monkeypatch.setattr(routes_platform, "hand_off", hand_off)
    folder = tmp_path / "ledger"
    folder.mkdir()
    made = await client.post("/projects", json={"source": "local", "repo": str(folder)})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["status"] == "onboarding"
    assert [(x["label"], x["kind"], x["id"]) for x in body["sources"]] == [("ledger", "local", None)]   # the primary
    assert handed == [("onboard", str(folder))]
