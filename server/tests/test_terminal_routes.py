"""Terminals, run configurations and the debugger, over HTTP.

Everything runs for real: a shell on a pseudo-terminal, a command in a project's checkout, and debugpy
launching a script and stopping at a breakpoint. The checkout is a temporary folder, the machine's roots
are narrowed to it, and the shell is /bin/sh, so nobody's own rc files take part.

The sockets are tested in test_terminal_socket.py, against a workspace that really commits: a socket
looks its person up in a session of its own, which a rolled-back test transaction cannot hand it.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.api.routes_terminal import terminals_of
from app.services import terminal as terminal_service
from app.services.terminal import Terminal, detect
from app.settings import Settings
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ADMIN = {"email": "admin@example.com", "name": "Asha", "password": "another long passphrase", "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}


@pytest.fixture
def machine(tmp_path: Path) -> Path:
    """The only folder the machine routes may open in these tests."""
    root = tmp_path / "machine"
    root.mkdir()
    return Path(os.path.realpath(root))


@pytest.fixture
def checkout(machine: Path) -> Path:
    """A small project: a script to debug, a Makefile, a package.json, and a notebook."""
    root = machine / "shop"
    root.mkdir()
    (root / "calc.py").write_text(
        "def total(items):\n"
        "    running = 0\n"
        "    for item in items:\n"
        "        running += item\n"
        "    return running\n"
        "\n"
        "print('result', total([1, 2, 3]))\n")
    (root / "Makefile").write_text(".PHONY: test\ntest:\n\tpython -m pytest\nserve: build\n\techo serve\nbuild:\n"
                                   "\techo build\n%.o: %.c\n\tcc $<\n")
    (root / "package.json").write_text(json.dumps({"name": "shop", "scripts": {"dev": "vite", "build": "vite build"}}))
    (root / "manage.py").write_text("print('django')\n")
    (root / "analysis.ipynb").write_text("{}")
    web = root / "api"
    web.mkdir()
    (web / "go.mod").write_text("module shop/api\n")
    return root


@pytest_asyncio.fixture
async def api(session: AsyncSession, settings: Settings, machine: Path, checkout: Path,
              monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    monkeypatch.setenv("SHELL", "/bin/sh")
    await load_workspace(session)
    session.add(m.Project(id="shop", name="Shop", source_kind="local", source_repo=str(checkout)))
    session.add(m.Project(id="bare", name="No Code Here"))
    await session.flush()
    made = create_api(db=None)
    made.state.settings = settings.model_copy(update={"machine_roots": str(machine), "machine_access": True})

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    try:
        yield made
    finally:
        await terminals_of(made).close_all()
        held = getattr(made.state, "debuggers", None)
        if held is not None:
            await held.close_all()
        made.state.ledger.close()
        await made.state.db.close()


def _client(made: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=made), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def _until(check: Any, seconds: float = 20.0) -> Any:
    """Poll until `check()` answers something truthy, or fail saying so."""
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        found = await check()
        if found:
            return found
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("waited too long")
        await asyncio.sleep(0.05)


async def _audit(session: AsyncSession, action: str) -> list[m.AuditEntry]:
    return list((await session.execute(select(m.AuditEntry).where(m.AuditEntry.action == action))).scalars())


# ── the gate ─────────────────────────────────────────────────────

async def test_machine_routes_need_the_permission(api: FastAPI, client: AsyncClient):
    """Even an Admin is refused: a shell on the server is an Owner's to hand out."""
    assert (await client.post("/admin/users", json=ADMIN)).status_code in (200, 201)
    async with _client(api) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        for method, path in (("get", "/machine/terminals"), ("post", "/machine/terminals"),
                             ("get", "/projects/shop/run-configs"), ("get", "/projects/shop/run-configs/detect"),
                             ("post", "/projects/shop/debug")):
            answer = await admin.request(method.upper(), path, json={} if method == "post" else None)
            assert answer.status_code == 403, path
            assert "machine:access" in answer.json()["detail"]
    async with _client(api) as stranger:
        assert (await stranger.get("/machine/terminals")).status_code == 401


async def test_machine_access_off_answers_404(api: FastAPI, client: AsyncClient):
    api.state.settings = api.state.settings.model_copy(update={"machine_access": False})
    for path in ("/machine/terminals", "/projects/shop/run-configs", "/projects/shop/debug"):
        answer = await client.get(path)
        assert answer.status_code == 404
        assert answer.json()["detail"] == "Machine access is off on this server"


# ── terminals ────────────────────────────────────────────────────

async def test_a_terminal_opens_inside_the_roots_only(client: AsyncClient, machine: Path, tmp_path: Path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (machine / "escape").symlink_to(outside)
    for cwd in (str(outside), str(machine / "escape"), str(machine / ".." / "elsewhere"), "/"):
        answer = await client.post("/machine/terminals", json={"cwd": cwd})
        assert answer.status_code == 403, cwd
        assert "outside the folders" in answer.json()["detail"]
    assert (await client.post("/machine/terminals", json={"cwd": "relative/path"})).status_code == 422
    assert (await client.post("/machine/terminals", json={"cwd": str(machine / "missing")})).status_code == 404

    opened = await client.post("/machine/terminals", json={"cwd": str(machine), "cols": 90, "rows": 20})
    assert opened.status_code == 201
    body = opened.json()
    assert body["cwd"] == str(machine) and body["kind"] == "shell" and body["status"] == "running"
    assert body["shell"] == "/bin/sh" and (body["cols"], body["rows"]) == (90, 20)
    assert [t["id"] for t in (await client.get("/machine/terminals")).json()] == [body["id"]]


async def test_a_terminal_defaults_to_the_projects_checkout(client: AsyncClient, checkout: Path,
                                                            session: AsyncSession):
    body = (await client.post("/machine/terminals", json={"projectId": "shop"})).json()
    assert body["cwd"] == str(checkout) and body["projectId"] == "shop"
    audited = await _audit(session, "terminal.open")
    assert audited and audited[-1].target == str(checkout) and audited[-1].detail["shell"] == "/bin/sh"
    refused = await client.post("/machine/terminals", json={"projectId": "bare"})
    assert refused.status_code == 409 and "no code on this machine" in refused.json()["detail"]


async def test_a_pty_echoes_what_it_is_sent_and_takes_a_new_size(api: FastAPI, client: AsyncClient,
                                                                  machine: Path):
    """The round trip at the pty itself: bytes in, the shell's answer out, and `stty size` agreeing with
    the size it was given. The socket carries exactly these bytes (test_terminal_socket.py)."""
    body = (await client.post("/machine/terminals", json={"cwd": str(machine), "cols": 80, "rows": 24})).json()
    owner = (await client.get("/auth/me")).json()["user"]["id"]
    terminal = terminals_of(api).get(body["id"], owner)
    terminal.write("echo round-trip-$((40+2))\n".encode())
    await _until(lambda: _has(terminal, b"round-trip-42"))
    terminal.resize(132, 41)
    terminal.write(b"stty size\n")
    await _until(lambda: _has(terminal, b"41 132"))
    # A resize over HTTP says the same, for a caller with no socket open.
    assert (await client.post(f"/machine/terminals/{body['id']}/resize", json={"cols": 100, "rows": 30})).json()[
        "cols"] == 100


async def _has(terminal: Terminal, text: bytes) -> bool:
    return text in bytes(terminal.buffer)


async def test_a_person_holds_at_most_eight_terminals(client: AsyncClient, machine: Path):
    ids = [(await client.post("/machine/terminals", json={"cwd": str(machine)})).json()["id"]
           for _ in range(terminal_service.MAX_PER_PERSON)]
    ninth = await client.post("/machine/terminals", json={"cwd": str(machine)})
    assert ninth.status_code == 429 and "Close one first" in ninth.json()["detail"]
    assert (await client.delete(f"/machine/terminals/{ids[0]}")).json() == {"ok": True, "id": ids[0]}
    assert (await client.get(f"/machine/terminals/{ids[0]}")).status_code == 404
    assert (await client.post("/machine/terminals", json={"cwd": str(machine)})).status_code == 201


async def test_another_persons_terminal_is_not_there(api: FastAPI, client: AsyncClient, machine: Path,
                                                     session: AsyncSession):
    mine = (await client.post("/machine/terminals", json={"cwd": str(machine)})).json()["id"]
    second = {**ADMIN, "email": "second-owner@example.com", "roles": ["owner"]}
    await client.post("/admin/users", json=second)
    async with _client(api) as other:
        await other.post("/auth/login", json={"email": second["email"], "password": second["password"]})
        assert (await other.get(f"/machine/terminals/{mine}")).status_code == 404
        assert (await other.delete(f"/machine/terminals/{mine}")).status_code == 404
        assert (await other.get("/machine/terminals")).json() == []


# ── run configurations ───────────────────────────────────────────

async def test_run_configurations_are_saved_changed_and_removed(client: AsyncClient, session: AsyncSession):
    made = await client.post("/projects/shop/run-configs", json={
        "name": "Serve", "kind": "run", "language": "shell", "command": "make serve", "args": ["--quiet"],
        "cwd": "", "env": {"API_KEY": "sk-secret-value", "PORT": "8080"}})
    assert made.status_code == 201
    config = made.json()
    assert config["env"] == ["API_KEY", "PORT"]                      # the names, never the values
    assert "sk-secret-value" not in json.dumps(config)
    assert config["createdBy"] == "Rajat" and config["projectId"] == "shop"

    same = await client.post("/projects/shop/run-configs", json={"name": "serve", "command": "x"})
    assert same.status_code == 409
    assert (await client.post("/projects/shop/run-configs", json={"name": "Up", "command": "x",
                                                                  "cwd": "../elsewhere"})).status_code == 422
    assert (await client.post("/projects/shop/run-configs", json={"name": "Up", "command": "x",
                                                                  "env": {"1BAD": "x"}})).status_code == 422

    changed = await client.patch(f"/projects/shop/run-configs/{config['id']}",
                                 json={"command": "make build", "env": {"API_KEY": None, "DEBUG": "1"}})
    assert changed.status_code == 200
    assert changed.json()["command"] == "make build" and changed.json()["env"] == ["DEBUG", "PORT"]
    stored = await session.get(m.RunConfig, config["id"])
    assert stored is not None and stored.env == {"PORT": "8080", "DEBUG": "1"}

    listed = (await client.get("/projects/shop/run-configs")).json()
    assert [c["name"] for c in listed] == ["Serve"]
    assert (await client.get("/projects/shop/run-configs", params={"kind": "debug"})).json() == []
    assert (await client.patch(f"/projects/bare/run-configs/{config['id']}", json={"name": "x"})).status_code == 404

    assert (await client.delete(f"/projects/shop/run-configs/{config['id']}")).json()["ok"] is True
    assert (await client.get("/projects/shop/run-configs")).json() == []
    actions = [a.action for a in (await session.execute(select(m.AuditEntry))).scalars()]
    assert {"run_config.create", "run_config.update", "run_config.delete"} <= set(actions)
    assert all("sk-secret-value" not in json.dumps(a.detail) for a in
               (await session.execute(select(m.AuditEntry))).scalars())


async def test_detect_suggests_and_never_runs(client: AsyncClient, checkout: Path):
    found = (await client.get("/projects/shop/run-configs/detect")).json()
    assert found["checkout"] == str(checkout)
    by_command = {(s["command"], s["cwd"]): s for s in found["suggestions"]}
    assert ("npm run dev", "") in by_command and by_command[("npm run dev", "")]["why"] == "package.json script `dev`"
    assert {("make test", ""), ("make serve", ""), ("make build", "")} <= set(by_command)
    assert not any(c.startswith("make .") or "%" in c for c, _ in by_command)
    assert any(c.endswith("manage.py runserver") for c, _ in by_command)
    assert ("go run .", "api") in by_command
    assert any(c.startswith("jupyter nbconvert --to notebook --execute analysis.ipynb") for c, _ in by_command)
    debug = [s for s in found["suggestions"] if s["kind"] == "debug"]
    assert {"manage.py"} <= {s["command"] for s in debug}
    assert not (checkout / "analysis.executed.ipynb").exists()             # nothing was run
    assert all(s["saved"] is False for s in found["suggestions"])

    await client.post("/projects/shop/run-configs", json={"name": "dev", "language": "node", "command": "npm run dev"})
    again = (await client.get("/projects/shop/run-configs/detect")).json()
    assert [s["saved"] for s in again["suggestions"] if s["command"] == "npm run dev"] == [True]


def test_detect_reads_a_monorepo_and_its_lockfiles(tmp_path: Path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(json.dumps({"scripts": {"start": "node ."}}))
    (tmp_path / "web" / "pnpm-lock.yaml").write_text("")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n[project.scripts]\nserve = "x:main"\n')
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.json").write_text(json.dumps({"scripts": {"hidden": "x"}}))
    commands = {(s.command, s.cwd) for s in detect(tmp_path)}
    assert ("pnpm run start", "web") in commands
    assert ("uv run serve", "") in commands
    assert ("cargo run", "") in commands
    assert not any("hidden" in c for c, _ in commands)


async def test_a_run_streams_its_command_with_its_environment(api: FastAPI, client: AsyncClient,
                                                              session: AsyncSession):
    """Started, it is a terminal running the command in the configured folder with the configured
    environment — any script, not only English — and it can be stopped and restarted."""
    config = (await client.post("/projects/shop/run-configs", json={
        "name": "Greet", "command": 'printf "%s in %s\\n" "$GREETING" "$(basename "$PWD")"', "cwd": "api",
        "env": {"GREETING": "नमस्ते 你好"}})).json()
    started = await client.post(f"/run-configs/{config['id']}/start", json={"cols": 100, "rows": 30})
    assert started.status_code == 201
    body = started.json()
    assert body["kind"] == "run" and body["runConfigId"] == config["id"] and body["title"] == "Greet"
    owner = (await client.get("/auth/me")).json()["user"]["id"]
    terminal = terminals_of(api).get(body["id"], owner)

    async def finished() -> dict[str, Any] | None:
        now = (await client.get(f"/machine/terminals/{body['id']}")).json()
        return now if now["status"] == "exited" else None

    done = await _until(finished)
    assert done["exitCode"] == 0
    assert "नमस्ते 你好 in api" in bytes(terminal.buffer).decode("utf-8")
    audited = await _audit(session, "run.start")
    assert audited[-1].target == "Greet" and audited[-1].detail["env"] == ["GREETING"]
    assert "नमस्ते" not in json.dumps(audited[-1].detail)

    restarted = (await client.post(f"/machine/terminals/{body['id']}/restart")).json()
    assert restarted["runs"] == 2
    await _until(finished)
    assert bytes(terminal.buffer).decode("utf-8").count("नमस्ते 你好 in api") == 2


async def test_a_running_configuration_is_stopped_not_doubled(client: AsyncClient):
    config = (await client.post("/projects/shop/run-configs", json={"name": "Wait", "command": "sleep 30"})).json()
    first = (await client.post(f"/run-configs/{config['id']}/start")).json()
    twice = await client.post(f"/run-configs/{config['id']}/start")
    assert twice.status_code == 409 and "already running" in twice.json()["detail"]
    stopped = (await client.post(f"/machine/terminals/{first['id']}/stop")).json()
    assert stopped["status"] == "exited" and stopped["exitCode"] != 0
    assert (await client.post(f"/run-configs/{config['id']}/start")).status_code == 201


async def test_a_run_needs_a_checkout(client: AsyncClient):
    config = (await client.post("/projects/bare/run-configs", json={"name": "x", "command": "true"})).json()
    answer = await client.post(f"/run-configs/{config['id']}/start")
    assert answer.status_code == 409 and "no code on this machine" in answer.json()["detail"]


# ── the debugger ─────────────────────────────────────────────────

@pytest.fixture
def venv(checkout: Path) -> Path:
    """The project's own interpreter, as a .venv beside its code — the one the debugger must use."""
    bin_dir = checkout / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(sys.executable)
    return bin_dir / "python"


async def test_python_debugging_stops_reads_and_finishes(client: AsyncClient, checkout: Path, venv: Path,
                                                         session: AsyncSession):
    """A real debugpy session: the breakpoint is hit, the frame and its variables read, an expression
    evaluated, and the program continued to its end with its output kept."""
    script = str(checkout / "calc.py")
    started = await client.post("/projects/shop/debug", json={"program": "calc.py", "breakpoints": {script: [5]}})
    assert started.status_code == 201, started.text
    debug = started.json()
    assert debug["language"] == "python" and debug["interpreter"] == str(venv)
    assert debug["program"] == script

    async def paused() -> dict[str, Any] | None:
        now = (await client.get(f"/debug/{debug['id']}")).json()
        return now if now["status"] == "paused" else None

    stopped = await _until(paused, 40)
    assert stopped["stopped"]["reason"] == "breakpoint"
    top = stopped["frames"][0]
    assert (top["path"], top["line"], top["name"]) == (script, 5, "total")
    assert stopped["breakpoints"][script][0]["verified"] is True

    scopes = (await client.post(f"/debug/{debug['id']}/scopes", json={"frameId": top["id"]})).json()["scopes"]
    local = next(s for s in scopes if s["name"].lower().startswith("local"))
    variables = (await client.post(f"/debug/{debug['id']}/variables", json={"ref": local["ref"]})).json()["variables"]
    values = {v["name"]: v["value"] for v in variables}
    assert values["running"] == "6" and values["items"] == "[1, 2, 3]"
    items = next(v for v in variables if v["name"] == "items")
    assert items["ref"] > 0
    children = (await client.post(f"/debug/{debug['id']}/variables", json={"ref": items["ref"]})).json()["variables"]
    assert [c["value"] for c in children if c["name"] in ("0", "1", "2")] == ["1", "2", "3"]

    watched = (await client.post(f"/debug/{debug['id']}/evaluate",
                                 json={"expression": "running * 7", "frameId": top["id"], "context": "watch"})).json()
    assert watched["result"] == "42"
    broken = (await client.post(f"/debug/{debug['id']}/evaluate",
                                json={"expression": "nothing_here", "frameId": top["id"]})).json()
    assert broken["type"] == "error" and "nothing_here" in broken["result"]

    assert (await client.post(f"/debug/{debug['id']}/continue", json={})).json() == {"ok": True}

    async def ended() -> dict[str, Any] | None:
        now = (await client.get(f"/debug/{debug['id']}")).json()
        return now if now["status"] == "ended" else None

    finished = await _until(ended, 30)
    assert "result 6" in "".join(piece["text"] for piece in finished["output"])
    assert finished["exitCode"] == 0
    audited = await _audit(session, "debug.start")
    assert audited[-1].detail["interpreter"] == str(venv) and audited[-1].detail["program"] == script
    assert (await client.post(f"/debug/{debug['id']}/next", json={})).status_code == 409
    assert (await client.delete(f"/debug/{debug['id']}")).json()["ok"] is True
    assert (await client.get(f"/debug/{debug['id']}")).status_code == 404


async def test_a_breakpoint_set_while_paused_is_hit_next(client: AsyncClient, checkout: Path, venv: Path):
    """The editor contract: toggling a breakpoint while paused sends setBreakpoints, and it holds."""
    script = str(checkout / "calc.py")
    debug = (await client.post("/projects/shop/debug", json={"program": "calc.py",
                                                             "breakpoints": {script: [2]}})).json()

    async def paused_at(line: int) -> bool:
        now = (await client.get(f"/debug/{debug['id']}")).json()
        return now["status"] == "paused" and now["frames"][0]["line"] == line

    await _until(lambda: paused_at(2), 40)
    answer = (await client.post(f"/debug/{debug['id']}/setBreakpoints", json={"path": script, "lines": [4]})).json()
    assert answer["breakpoints"][0]["line"] == 4 and answer["breakpoints"][0]["verified"] is True
    await client.post(f"/debug/{debug['id']}/continue", json={})
    await _until(lambda: paused_at(4), 20)
    await client.post(f"/debug/{debug['id']}/terminate", json={})
    assert (await client.get(f"/debug/{debug['id']}")).json()["status"] == "ended"


async def test_debugging_refuses_what_is_not_the_projects(client: AsyncClient, checkout: Path, tmp_path: Path):
    outside = tmp_path / "other.py"
    outside.write_text("print(1)\n")
    answer = await client.post("/projects/shop/debug", json={"program": str(outside)})
    assert answer.status_code == 403 and "outside this project's checkout" in answer.json()["detail"]
    assert (await client.post("/projects/shop/debug", json={"program": "../other.py"})).status_code == 403
    assert (await client.post("/projects/shop/debug", json={"program": "missing.py"})).status_code == 404
    assert (await client.post("/projects/shop/debug", json={"program": "Makefile"})).status_code == 422
    assert (await client.post("/projects/shop/debug", json={})).status_code == 422
    run = (await client.post("/projects/shop/run-configs", json={"name": "Serve", "command": "make serve"})).json()
    refused = await client.post("/projects/shop/debug", json={"runConfigId": run["id"]})
    assert refused.status_code == 409 and "is a run configuration" in refused.json()["detail"]
    assert (await client.post("/debug/nothing/continue", json={})).status_code == 404
    assert (await client.post("/debug/nothing/explode", json={})).status_code == 404


async def test_a_debug_configuration_runs_under_the_debugger(client: AsyncClient, checkout: Path, venv: Path):
    """A saved debug configuration: its program and arguments, its environment, the project's python."""
    (checkout / "greet.py").write_text("import os, sys\nprint(os.environ['WHO'], *sys.argv[1:])\n")
    config = (await client.post("/projects/shop/run-configs", json={
        "name": "Greet", "kind": "debug", "language": "python", "command": "greet.py", "args": ["and", "friends"],
        "env": {"WHO": "Zoë"}})).json()
    debug = (await client.post("/projects/shop/debug", json={"runConfigId": config["id"]})).json()
    assert debug["name"] == "Greet" and debug["runConfigId"] == config["id"]

    async def ended() -> dict[str, Any] | None:
        now = (await client.get(f"/debug/{debug['id']}")).json()
        return now if now["status"] == "ended" else None

    finished = await _until(ended, 40)
    assert "Zoë and friends" in "".join(piece["text"] for piece in finished["output"])
    listed = (await client.get("/projects/shop/debug")).json()
    assert [d["id"] for d in listed] == [debug["id"]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed on this machine")
async def test_node_debugging_stops_at_a_breakpoint(client: AsyncClient, checkout: Path):
    """Node through its own inspector: the breakpoint is hit, a local read, and the program finishes."""
    script = checkout / "sum.js"
    script.write_text("function total(items) {\n  let running = 0;\n  for (const item of items) running += item;\n"
                      "  return running;\n}\nconsole.log('result', total([4, 5, 6]));\n")
    debug = (await client.post("/projects/shop/debug", json={"program": "sum.js",
                                                             "breakpoints": {str(script): [4]}})).json()
    assert debug["language"] == "node"

    async def paused() -> dict[str, Any] | None:
        now = (await client.get(f"/debug/{debug['id']}")).json()
        return now if now["status"] == "paused" else None

    stopped = await _until(paused, 30)
    top = stopped["frames"][0]
    assert (top["path"], top["line"], top["name"]) == (str(script), 4, "total")
    scopes = (await client.post(f"/debug/{debug['id']}/scopes", json={"frameId": top["id"]})).json()["scopes"]
    local = scopes[0]
    values = {v["name"]: v["value"] for v in
              (await client.post(f"/debug/{debug['id']}/variables", json={"ref": local["ref"]})).json()["variables"]}
    assert values["running"] == "15"
    evaluated = (await client.post(f"/debug/{debug['id']}/evaluate",
                                   json={"expression": "running + 1", "frameId": top["id"]})).json()
    assert evaluated["result"] == "16"
    await client.post(f"/debug/{debug['id']}/continue", json={})

    async def ended() -> dict[str, Any] | None:
        now = (await client.get(f"/debug/{debug['id']}")).json()
        return now if now["status"] == "ended" else None

    finished = await _until(ended, 20)
    assert "result 15" in "".join(piece["text"] for piece in finished["output"])


# ── a project with several sources ───────────────────────────────

async def test_runs_detection_and_debugging_span_every_source(api: FastAPI, client: AsyncClient,
                                                              session: AsyncSession, machine: Path, venv: Path):
    """A further source's folders are named under its label, as every project path is: a configuration
    in `tools` runs in that checkout, detection suggests its scripts there, and its programs debug — while
    a source still being onboarded is refused rather than read."""
    tools = machine / "tools"
    tools.mkdir()
    (tools / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
    (tools / "hello.py").write_text("name = 'tools'\nprint('hello from', name)\n")
    session.add(m.ProjectSource(project_id="shop", label="tools", kind="local", repo=str(tools), status="active"))
    session.add(m.ProjectSource(project_id="shop", label="later", kind="local", repo=str(machine / "later"),
                                status="onboarding"))
    await session.flush()

    detected = (await client.get("/projects/shop/run-configs/detect")).json()
    assert [s["label"] for s in detected["sources"]] == ["shop", "tools"]
    lint = next(s for s in detected["suggestions"] if s["command"] == "npm run lint")
    assert (lint["name"], lint["cwd"]) == ("tools: lint", "tools")
    assert any(s["command"] == "npm run dev" and s["cwd"] == "" for s in detected["suggestions"])

    config = (await client.post("/projects/shop/run-configs", json={
        "name": "Where", "command": 'basename "$PWD"', "cwd": "tools"})).json()
    started = (await client.post(f"/run-configs/{config['id']}/start")).json()
    owner = (await client.get("/auth/me")).json()["user"]["id"]
    terminal = terminals_of(api).get(started["id"], owner)
    assert started["cwd"] == str(tools)

    async def finished() -> dict[str, Any] | None:
        now = (await client.get(f"/machine/terminals/{started['id']}")).json()
        return now if now["status"] == "exited" else None

    await _until(finished)
    assert "tools" in bytes(terminal.buffer).decode("utf-8")

    waiting = (await client.post("/projects/shop/run-configs", json={
        "name": "Not yet", "command": "true", "cwd": "later"})).json()
    refused = await client.post(f"/run-configs/{waiting['id']}/start")
    assert refused.status_code == 409 and "later" in refused.json()["detail"]

    script = str(tools / "hello.py")
    debugged = await client.post("/projects/shop/debug", json={"program": "hello.py", "cwd": "tools",
                                                              "breakpoints": {script: [2]}})
    assert debugged.status_code == 201, debugged.text
    session_id = debugged.json()["id"]

    async def paused() -> dict[str, Any] | None:
        now = (await client.get(f"/debug/{session_id}")).json()
        return now if now["status"] == "paused" else None

    stopped = await _until(paused, 40)
    assert (stopped["frames"][0]["path"], stopped["frames"][0]["line"]) == (script, 2)
    assert (await client.post(f"/debug/{session_id}/terminate", json={})).json() == {"ok": True}

    (machine / "elsewhere.py").write_text("print('not the project')\n")
    outside = await client.post("/projects/shop/debug", json={"program": str(machine / "elsewhere.py")})
    assert outside.status_code == 403 and "outside" in outside.json()["detail"]
