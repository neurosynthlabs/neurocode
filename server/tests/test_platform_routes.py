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
from app.data.loader import load_seed, sync_roles

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def test_a_project_card_is_computed_not_stored(client: AsyncClient):
    projects = (await client.get("/projects")).json()
    assert len(projects) == 5
    erp = next(p for p in projects if p["id"] == "erp")

    assert erp["work"]["tasks"] == 10                      # counted from the tasks table
    board = (await client.get("/tasks", params={"project": "erp"})).json()
    assert erp["work"]["running"] == sum(1 for t in board if t["status"] == "in_progress")
    assert erp["work"]["review"] == sum(1 for t in board if t["status"] == "review")
    assert erp["work"]["blocked"] == sum(1 for t in board if t["status"] == "blocked")


async def test_a_project_keeps_the_shape_the_screens_read(client: AsyncClient):
    erp = (await client.get("/projects/erp")).json()
    assert set(erp) >= {"id", "name", "codename", "stack", "kind", "status", "lines", "modules",
                        "dbTables", "storedProcs", "repo", "coverage", "work", "description"}
    assert isinstance(erp["lines"], str) and isinstance(erp["stack"], list)
    assert (await client.get("/projects/nope")).status_code == 404


async def test_the_mcp_registry_lists_and_registers(client: AsyncClient):
    servers = (await client.get("/mcp/servers")).json()
    assert len(servers) == 16
    assert all(isinstance(s["tools"], list) and isinstance(s["errorRate"], float) for s in servers)

    made = await client.post("/mcp/servers", json={
        "name": "ledger-tools", "transport": "stdio", "command": "npx ledger-mcp",
        "scope": "project", "defaultEffect": "ask", "config": json.dumps({"cmd": "npx"})})
    assert made.status_code == 201
    assert made.json()["untrusted"] is True                # registered here means trusted by nobody yet
    assert made.json()["status"] == "disconnected"
    assert len((await client.get("/mcp/servers")).json()) == 17


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
