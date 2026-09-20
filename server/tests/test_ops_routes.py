"""DevOps over HTTP: this machine's services and checks, deliveries, the pipeline, logs, containers, keys.

Runs, approvals, logs and ledger lines are written into the rolled-back transaction, so the answers can
be known in advance. The web dev server probe is replaced — a test must not depend on whether a Vite
server happens to be running — and the workflow's state is asked of a real git repository in tmp_path,
with a real bare remote, because "committed" and "pushed" are git's answers and nothing else's.
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.services import ops
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ENGINEER = {"email": "dev@example.com", "name": "Dev", "password": "another long passphrase",
            "roles": ["engineer"]}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def api(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    await load_workspace(session)
    monkeypatch.setattr(ops, "probe_http", lambda url: (False, 3, "ConnectionRefusedError"))
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


def _client(api: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


def _run(ref: str, **kw: object) -> m.Run:
    return m.Run(id=f"ops-{ref}", ref=ref, project_id="erp", branch=f"neurocode/{ref.lower()}",
                 worktree=f"/nowhere/{ref}", repo="/nowhere", base="abcdef1234567", requested_by="Rajat",
                 **kw)


async def test_the_overview_is_this_machine_and_the_real_gate(client: AsyncClient, session: AsyncSession):
    session.add_all([
        _run("RUN-701", status="waiting", diff_files=3, diff_insertions=40, diff_deletions=2,
             tests_status="failed", tests_summary="2 failed",
             review={"findings": [{"severity": "HIGH", "title": "Secret in config"}], "verdict": "changes"}),
        _run("RUN-702", status="done", diff_files=1, diff_insertions=5, finished_at=datetime.now(UTC)),
        _run("RUN-703", status="done", merged={"into": "main", "commit": "1234567", "at": "2026-09-16T10:00:00+00:00",
                                               "by": "Rajat", "undo": "git reset --hard 7654321"}),
    ])
    await session.flush()
    session.add(m.Approval(id="ops-ap-1", ref="APR-701", title="Accept RUN-701", tool="Merge(neurocode/run-701)",
                           risk="HIGH", payload="branch neurocode/run-701 from abcdef1\n3 files · +40 −2",
                           run_ref="RUN-701", project_id="erp"))
    await session.flush()

    body = (await client.get("/ops/overview")).json()
    assert [s["id"] for s in body["services"]] == ["api", "postgres", "web", "ollama"]
    api_service, postgres, web = body["services"][:3]
    assert api_service["status"] == "ok" and api_service["facts"][0]["k"] == "PID"
    assert postgres["version"] and postgres["startedAt"] and postgres["cpuPct"] is None
    assert web["status"] == "down"

    gate = {g["runRef"]: g for g in body["gate"]}
    assert set(gate) == {"RUN-701", "RUN-702"}                     # the merged run is no longer waiting
    handoff = gate["RUN-701"]
    assert handoff["kind"] == "handoff" and handoff["approvalRef"] == "APR-701" and handoff["risk"] == "HIGH"
    assert handoff["ships"] == ["branch neurocode/run-701 from abcdef1", "3 files · +40 −2"]
    assert any("Secret in config" in d for d in handoff["dangers"]) and any("Tests failed" in d for d in handoff["dangers"])
    assert gate["RUN-702"]["kind"] == "ready" and gate["RUN-702"]["risk"] is None

    checks = {c["id"]: c for c in body["checks"]}
    assert {"db", "schema", "pg_dump", "backup", "disk", "secrets", "lanes", "keys", "embeddings", "web",
            "ollama", "worktrees", "silent", "failures"} <= set(checks)
    assert checks["db"]["status"] == "ok" and checks["schema"]["status"] == "ok"
    # Every check is of this machine, so none carries an environment label that could only ever say so.
    assert all("env" not in c for c in body["checks"])
    assert {"Python", "PostgreSQL", "Routing", "AI calls today"} <= {r["k"] for r in body["runtime"]}


async def test_deliveries_are_runs_and_what_became_of_them(client: AsyncClient, session: AsyncSession):
    now = datetime.now(UTC)
    session.add_all([
        _run("RUN-801", status="done", finished_at=now,
             merged={"into": "main", "commit": "1234567", "at": now.isoformat(), "by": "Rajat",
                     "undo": "git reset --hard 7654321"}),
        _run("RUN-802", status="done", removed=True, finished_at=now),
        _run("RUN-803", status="failed", finished_at=now, note="tests would not start"),
        _run("RUN-804", status="done", finished_at=now, tests_summary="12 passed"),
        _run("RUN-805", status="done", role="agent"),                  # an agent's branch is not a delivery
    ])
    await session.flush()

    body = (await client.get("/ops/deliveries")).json()
    by_ref = {d["id"]: d for d in body["items"]}
    assert "RUN-805" not in by_ref
    assert by_ref["RUN-801"]["status"] == "success" and by_ref["RUN-801"]["commit"] == "1234567"
    assert by_ref["RUN-801"]["note"] == "git reset --hard 7654321"
    assert by_ref["RUN-802"]["status"] == "discarded"
    assert by_ref["RUN-803"]["status"] == "failed" and by_ref["RUN-803"]["note"] == "tests would not start"
    assert by_ref["RUN-804"]["status"] == "ready" and by_ref["RUN-804"]["project"] == "Legacy ERP"
    stats = body["stats"]
    assert stats["mergedToday"] >= 1 and stats["merged"] >= 1 and stats["failed"] >= 1 and stats["discarded"] >= 1
    assert len((await client.get("/ops/deliveries", params={"limit": 2})).json()["items"]) == 2


async def test_the_pipeline_is_a_runs_own_steps(client: AsyncClient, session: AsyncSession):
    run = _run("RUN-901", status="running")
    session.add(run)
    await session.flush()
    session.add_all([m.RunStep(run_id=run.id, n=1, kind="edit", label="Write the tax fix", status="done", ms=4200),
                     m.RunStep(run_id=run.id, n=2, kind="test", label="make test", status="running")])
    await session.flush()

    body = (await client.get("/ops/pipeline", params={"run": "RUN-901"})).json()
    assert body["run"]["ref"] == "RUN-901" and body["run"]["runner"] == "local worktree · rules"
    assert [(s["name"], s["state"], s["durationS"]) for s in body["stages"]] == [
        ("Write the tax fix", "pass", 4), ("make test", "running", 0)]
    assert body["workflow"]["path"] == ".github/workflows/verify.yml" and body["workflow"]["note"]
    assert (await client.get("/ops/pipeline", params={"run": "RUN-000"})).status_code == 404


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def _workflow(repo: Path) -> Path:
    flow = repo / ".github" / "workflows" / "verify.yml"
    flow.parent.mkdir(parents=True)
    flow.write_text("jobs:\n  verify:\n    steps:\n      - name: Install\n      - name: 'Server tests'\n")
    return flow


def test_the_workflow_note_is_gits_answer_not_a_sentence(tmp_path: Path):
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", str(remote), str(repo))
    _git(repo, "checkout", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "start")
    _git(repo, "push", "-u", "origin", "main")
    assert ops.workflow_state(repo)["present"] is False

    _workflow(repo)
    untracked = ops.workflow_state(repo)
    assert untracked["steps"] == ["Install", "Server tests"] and untracked["tracked"] is False
    assert "not committed" in untracked["note"]

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "ci")
    ahead = ops.workflow_state(repo)
    assert ahead["tracked"] is True and ahead["upstream"] == "origin/main" and ahead["pushed"] is False
    assert "does not have it yet" in ahead["note"]

    _git(repo, "push")
    pushed = ops.workflow_state(repo)
    assert pushed["pushed"] is True and "pushed to origin/main" in pushed["note"]


def test_with_no_upstream_the_note_says_nothing_about_pushing(tmp_path: Path):
    _git(tmp_path, "init", "-b", "main")
    _workflow(tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "ci")
    state = ops.workflow_state(tmp_path)
    assert state["tracked"] is True and state["upstream"] is None and state["pushed"] is None
    assert "push" not in state["note"]


async def test_logs_translate_the_level_before_asking_the_database(client: AsyncClient, session: AsyncSession):
    run = _run("RUN-951", status="running")
    session.add(run)
    await session.flush()
    session.add_all([m.RunLog(run_id=run.id, level="tool", line="$ make test"),
                     m.RunLog(run_id=run.id, level="err", line="2 failed"),
                     m.AiCall(feature="research", lane="groq", model="llama", ok=False, error="HTTP 429")])
    await session.flush()

    debug = (await client.get("/ops/logs", params={"level": "debug"})).json()["lines"]
    assert debug and all(line["level"] == "debug" for line in debug)
    assert "$ make test" in [line["text"] for line in debug]

    errors = (await client.get("/ops/logs", params={"level": "err"})).json()["lines"]
    texts = [line["text"] for line in errors]
    assert "2 failed" in texts and "research · llama · HTTP 429" in texts
    assert all(line["level"] == "err" for line in errors)

    assert (await client.get("/ops/logs", params={"level": "verbose"})).status_code == 422
    first = (await client.get("/ops/logs", params={"limit": 2})).json()
    assert len(first["lines"]) == 2 and first["next"].endswith(f"|{first['lines'][-1]['id']}")
    assert (await client.get("/ops/logs", params={"before": "yesterday"})).status_code == 422


async def test_paging_back_through_the_logs_loses_no_line(client: AsyncClient, session: AsyncSession):
    """The cursor was the last line's time cut to the second, compared with `<`: every line written in
    that same second that did not fit on the page was on no page at all. A run writes many a second."""
    from datetime import UTC, datetime

    run = _run("RUN-960", status="running")
    session.add(run)
    await session.flush()
    same_second = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
    written = {f"line {n}" for n in range(7)}
    session.add_all([m.RunLog(run_id=run.id, level="tool", line=text, at=same_second.replace(microsecond=n * 1000))
                     for n, text in enumerate(sorted(written))])
    await session.flush()

    seen: list[str] = []
    before = None
    for _ in range(10):
        params = {"level": "debug", "limit": 3, **({"before": before} if before else {})}
        page = (await client.get("/ops/logs", params=params)).json()
        seen += [line["text"] for line in page["lines"] if line["text"] in written]
        before = page["next"]
        if not before:
            break
    assert sorted(seen) == sorted(written) and len(seen) == len(written)     # every line, once


async def test_containers_say_why_there_are_none(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ops, "_docker_seen", None)
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    body = (await client.get("/ops/containers")).json()
    assert body == {"available": False, "containers": [],
                    "reason": "Docker is not installed. NeuroCode itself runs no containers."}


async def test_secrets_are_names_and_need_workspace_admin(api: FastAPI, client: AsyncClient,
                                                          session: AsyncSession):
    session.add(m.AuditEntry(action="ai.update", target="AI providers", detail={"groq.key": "set"}))
    await session.flush()
    rows = (await client.get("/ops/secrets")).json()
    by_id = {r["id"]: r for r in rows}
    assert "database" in by_id and by_id["database"]["name"].startswith("NEUROCODE_DATABASE_URL")
    assert by_id["groq"]["lastSetAt"] is not None and by_id["groq"]["replaceableBy"] == ["Rajat"]
    for row in rows:
        assert set(row) == {"id", "name", "env", "store", "set", "rejected", "lastSetAt", "replaceableBy",
                            "usedBy", "note"}                         # no value, and no mask of one

    await client.post("/admin/users", json=ENGINEER)
    async with _client(api) as engineer:
        await engineer.post("/auth/login", json={"email": ENGINEER["email"], "password": ENGINEER["password"]})
        assert (await engineer.get("/ops/overview")).status_code == 200
        assert (await engineer.get("/ops/secrets")).status_code == 403
    async with _client(api) as stranger:
        assert (await stranger.get("/ops/overview")).status_code == 401


async def test_a_saved_key_is_said_to_live_in_the_keys_file_this_gateway_writes(
        api: FastAPI, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The store used to read "server/secrets.json" whatever the gateway was really writing to, and
    NEUROCODE_SECRETS_PATH moves that file."""
    from app.ai import lanes
    from app.ai.gateway import Gateway
    from app.ai.ledger import MemoryLedger
    from app.secrets import Secrets

    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    elsewhere = tmp_path / "keys" / "held-here.json"
    elsewhere.parent.mkdir()
    gw = Gateway(MemoryLedger(), Secrets(elsewhere))
    gw.secrets.set(lanes.BY_ID["groq"].secret, "gsk_not_a_real_key")
    api.dependency_overrides[deps.gateway] = lambda: gw

    by_id = {r["id"]: r for r in (await client.get("/ops/secrets")).json()}
    assert by_id["groq"]["set"] is True and by_id["groq"]["store"] == str(elsewhere)
    assert by_id["cerebras"]["set"] is False and by_id["cerebras"]["store"] == "not set"


def test_ps_elapsed_time_is_read_in_seconds():
    assert ops._etime_seconds("05:03") == 303
    assert ops._etime_seconds("02:00:01") == 7201
    assert ops._etime_seconds("1-00:00:00") == 86400
    assert ops._etime_seconds("soon") is None


async def test_devops_shows_the_folders_the_runtime_really_uses(client: AsyncClient,
                                                                monkeypatch: pytest.MonkeyPatch):
    """The screen read `repos_dir` and `worktrees_dir` while runs and onboarding kept their own paths, so
    it named a folder nothing used and counted test leftovers as real worktrees."""
    from app import onboarding
    from app.services import runs
    from app.settings import SERVER_DIR, Settings, settings

    used = settings()
    # The tests point both at a temporary folder (conftest), so nothing here lands in the server's own.
    assert SERVER_DIR not in used.worktrees_dir.parents and SERVER_DIR not in used.repos_dir.parents
    assert runs.worktrees_dir() == used.worktrees_dir
    assert onboarding.source_root({"id": "p", "source": {"kind": "git", "repo": "x"}}) == used.repos_dir / "p"
    assert onboarding.REPOS_DIR == used.repos_dir

    runtime = {r["k"]: r["v"] for r in (await client.get("/ops/overview")).json()["runtime"]}
    assert runtime["Repositories"] == str(used.repos_dir)
    assert str(runtime["Worktrees"]).startswith(f"{used.worktrees_dir} · ")

    # Unset, they are the folders clones and worktrees have always gone to: nobody's data moves.
    monkeypatch.delenv("NEUROCODE_WORKTREES_DIR")
    monkeypatch.delenv("NEUROCODE_REPOS_DIR")
    defaults = Settings(_env_file=None)
    assert defaults.repos_dir == SERVER_DIR / ".repos" and defaults.worktrees_dir == SERVER_DIR / ".worktrees"


async def test_each_source_of_the_timeline_is_cut_before_the_three_are_merged(client: AsyncClient,
                                                                              session: AsyncSession):
    """The newest hundred lines used to be found by reading every run log, every activity row and every
    failed model call ever written and sorting the lot. Each source carries its own LIMIT now, and the
    answer is the same one: the newest of the three, in order."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    run = _run("RUN-970", status="running")
    session.add(run)
    await session.flush()
    # Tomorrow, so these are the newest lines the timeline holds whatever else the fixture wrote.
    at = datetime.now(UTC) + timedelta(days=1)
    session.add_all([m.RunLog(run_id=run.id, level="err", line=f"run line {n}",
                              at=at + timedelta(minutes=n)) for n in range(4)])
    session.add_all([m.AiCall(feature="chat", lane="groq", model="llama", ok=False, error=f"HTTP 5{n}",
                              at=at + timedelta(minutes=30 + n)) for n in range(4)])
    await session.flush()

    asked: list[str] = []

    def watch(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        asked.append(statement)

    event.listen(Engine, "before_cursor_execute", watch)
    try:
        body = (await client.get("/ops/logs", params={"level": "err", "limit": 3})).json()
    finally:
        event.remove(Engine, "before_cursor_execute", watch)

    timeline = next(s for s in asked if "UNION ALL" in s)
    # One LIMIT for every branch, and one more for the merge: nothing is read in full and sorted after.
    assert timeline.count("LIMIT $") == timeline.count("UNION ALL") + 2
    assert [line["text"] for line in body["lines"]] == ["chat · llama · HTTP 53", "chat · llama · HTTP 52",
                                                        "chat · llama · HTTP 51"]


async def test_the_timeline_orders_by_the_number_in_a_line_id_not_by_its_text(client: AsyncClient,
                                                                              session: AsyncSession):
    """'run:999999' sorts after 'run:1000000' as text and before it as a number, and the id is what
    breaks a tie when two lines share a moment. Sorted as text, the older line came first and the
    cursor stepped past the newer one."""
    run = _run("RUN-971", status="running")
    session.add(run)
    await session.flush()
    together = datetime.now(UTC) + timedelta(days=2)          # newer than anything else in the timeline
    session.add_all([m.RunLog(id=999_999, run_id=run.id, level="err", line="older", at=together),
                     m.RunLog(id=1_000_000, run_id=run.id, level="err", line="newer", at=together)])
    await session.flush()

    seen: list[str] = []
    before = None
    for _ in range(4):
        params = {"level": "err", "limit": 1, **({"before": before} if before else {})}
        page = (await client.get("/ops/logs", params=params)).json()
        seen += [line["text"] for line in page["lines"] if line["text"] in ("older", "newer")]
        before = page["next"]
        if not before:
            break
    assert seen == ["newer", "older"]         # newest first, and neither one skipped


async def test_a_cursor_that_names_no_line_is_refused_in_words(client: AsyncClient):
    """The number in the cursor reaches the database as a number, so anything else is a 422 with a
    sentence, not a driver error on a screen somebody opened because something was already wrong."""
    moment = datetime(2026, 9, 17, tzinfo=UTC).isoformat()
    for cursor in (f"{moment}|nonsense", f"{moment}|run:", f"{moment}|run:12345678901234567890", moment):
        answer = await client.get("/ops/logs", params={"before": cursor})
        assert answer.status_code == 422, cursor
        assert "cursor" in answer.json()["detail"]
