"""The standing rules the runtime applies, over HTTP.

A rule is listed only when the runtime would consult it: a project's kept answer to running its own
tests. So the test writes exactly what the runtime writes — the setting, and the test-step approval a
person decided — and checks that what comes back is that, credited to the person who really decided it
and not to a gate that was closed some other way.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.data.base import utcnow
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def api(session: AsyncSession) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    return made


def _client(made: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=made), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest_asyncio.fixture
async def answered(session: AsyncSession, client: AsyncClient, tmp_path: Path) -> None:
    """Two answered projects — one whose code has a `make test`, one with no code here — and one that
    was never asked. Rajat allowed the first one's tests; a stopped check run closed a later gate there
    with nobody's name on it, and a signature on a diff is not a test gate at all."""
    owner_id = (await client.get("/auth/me")).json()["user"]["id"]
    root = tmp_path / "ledger"
    root.mkdir()
    (root / "Makefile").write_text("test:\n\tpython -m pytest -q\n")
    session.add_all([m.Project(id="gate-a", name="Alpha Ledger", source_kind="local", source_repo=str(root)),
                     m.Project(id="gate-b", name="Beta Shop"),
                     m.Project(id="gate-c", name="Gamma Untouched")])
    await session.flush()
    session.add_all([m.Setting(key="runtime.tests.gate-a", value="allowed"),
                     m.Setting(key="runtime.tests.gate-b", value="refused")])
    session.add(m.Run(id="r-gate", ref="RUN-8001", project_id="gate-a", status="done", role="solo",
                      branch="neurocode/x", worktree=str(tmp_path / "wt"), repo=str(root),
                      steps=[m.RunStep(n=1, kind="test", label="Run the project's tests"),
                             m.RunStep(n=2, kind="handoff", label="Your approval")], conflicts=[]))
    await session.flush()
    now = utcnow()
    # Each one carries `run_id`, as every gate the runtime writes does: who gave a standing answer is
    # read by joining the gate to its run on the link, not on the ref it prints.
    session.add_all([
        m.Approval(id="ap-g1", ref="APPR-8001", title="Run `make test`", tool="Bash(make test)", status="approved",
                   project_id="gate-a", run_id="r-gate", run_ref="RUN-8001", step=1,
                   decided_at=now - timedelta(hours=2), decided_by=owner_id),
        m.Approval(id="ap-g2", ref="APPR-8002", title="Run `make test`", tool="Bash(make test)", status="denied",
                   project_id="gate-a", run_id="r-gate", run_ref="RUN-8001", step=1,
                   decided_at=now - timedelta(hours=1)),
        m.Approval(id="ap-g3", ref="APPR-8003", title="Accept RUN-8001", tool="Merge(neurocode/x)",
                   status="approved", project_id="gate-a", run_id="r-gate", run_ref="RUN-8001", step=2,
                   decided_at=now, decided_by=owner_id),
    ])
    await session.flush()


async def test_the_rules_are_the_answers_the_runtime_reads_credited_to_who_gave_them(
        client: AsyncClient, answered: None, session: AsyncSession):
    body = (await client.get("/permissions/rules")).json()
    mine = [r for r in body if r["projectId"].startswith("gate-")]
    assert [r["projectId"] for r in mine] == ["gate-a", "gate-b"]            # ordered by name; gate-c never answered
    alpha, beta = mine
    assert set(alpha) == {"projectId", "projectName", "rule", "command", "answer", "decidedAt", "decidedBy"}
    assert (alpha["projectName"], alpha["rule"], alpha["answer"]) == ("Alpha Ledger", "tests", "allowed")
    assert alpha["command"] == "make test" and alpha["decidedBy"] == "Rajat" and alpha["decidedAt"]
    # Who and when are the same decision: Rajat's approval two hours ago, not the moment the setting
    # row was last written (which does not move when a second person writes the same answer again).
    decided = (await session.get(m.Approval, "ap-g1")).decided_at
    assert datetime.fromisoformat(alpha["decidedAt"]) == decided.replace(microsecond=0)
    setting_at = (await session.get(m.Setting, "runtime.tests.gate-a")).updated_at
    assert abs(setting_at - decided) > timedelta(hours=1)
    # No person decided beta's answer, so its time is the setting's own.
    assert beta["decidedAt"]
    # No code on this machine, so no command; nobody's decision is recorded, so nobody is named.
    assert (beta["answer"], beta["command"], beta["decidedBy"]) == ("refused", None, None)

    setting = await session.get(m.Setting, "runtime.tests.gate-a")
    setting.value = "refused"
    await session.flush()
    alpha = next(r for r in (await client.get("/permissions/rules")).json() if r["projectId"] == "gate-a")
    # The only denial there was closed by a stopped check run, which carries nobody.
    assert (alpha["answer"], alpha["decidedBy"]) == ("refused", None)


async def test_only_rules_the_runtime_applies_are_listed_and_the_list_is_paged(
        client: AsyncClient, answered: None, session: AsyncSession):
    body = (await client.get("/permissions/rules")).json()
    assert all(r["rule"] == "tests" for r in body)
    assert not any("rm -rf" in (r["command"] or "") for r in body)
    assert len((await client.get("/permissions/rules", params={"limit": 1})).json()) == 1


async def test_anyone_signed_in_may_read_the_rules(api: FastAPI, client: AsyncClient, answered: None):
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/permissions/rules")).status_code == 200
    async with _client(api) as stranger:
        assert (await stranger.get("/permissions/rules")).status_code == 401
