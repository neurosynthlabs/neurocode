"""The machine's fences, held on every path that starts a process or reaches a project.

Each rule here was once broken, and each test names the rule it holds:

* A command a custom tool names, and a repository hook a rule allows, start behind the same OS sandbox
  as a run's test command — and they, and the project's checkers, start with the machine's environment
  and not the API's, so none of them can read the database password.
* Run configurations, the debugger and a terminal opened by project honour a restricted project the way
  `scoped` does on every other project route: 404 with no grant, and the grant's own rights with one.
* One configuration started twice at once opens one terminal, and two restarts at once leave no process
  running that nobody holds a handle to.
* A terminal's socket admits no origin that CORS itself would refuse.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
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
from app.api.routes_terminal import origin_allowed, terminals_of
from app.services import custom_tools, extensions, machine, sandbox
from app.services import terminal as t
from app.services.custom_tools import CustomToolService
from app.services.diagnostics import Check, Checker, Target
from app.services.errors import Refused
from app.services.extensions import HookEntry, fire
from app.services.identity import Person
from app.settings import LOCAL_ORIGINS, Settings, settings as real_settings
from tests.fixtures.workspace import load_workspace

SECRET = "postgresql+asyncpg://neurocode:s3cr3t-adv@127.0.0.1:5432/neurocode"
PRINT_ENV = "import os; print('present' if 'NEUROCODE_DATABASE_URL' in os.environ else 'absent')"
OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PASSWORD = "a long enough password"
PID = "erp"

needs_a_sandbox = pytest.mark.skipif(sandbox.detect()[0] == sandbox.NONE, reason="needs a sandbox on this machine")


def _hook(raw: str, timeout_s: int = 10) -> HookEntry:
    return HookEntry(id="h", event="PostToolUse", matcher="", type="command", command=raw, scope="project",
                     source=".claude/settings.json", blocking=False, description="", timeout_s=timeout_s, raw=raw)


class _Captured:
    """`subprocess.run` stood in for: what would have been started, and nothing started."""

    returncode, stdout, stderr = 0, "", ""

    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def __call__(self, argv: list[str], **_: Any) -> _Captured:
        self.seen.append(list(argv))
        return self


@pytest.fixture
def outside() -> Iterator[Path]:
    """A folder no fence may write in: outside the temp directory, which every fence allows (pytest's
    own tmp_path is in there, so escaping into it would prove nothing). Removed either way."""
    folder = Path.home() / ".neurocode-terminal-hardening-test"
    folder.mkdir(exist_ok=True)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _confining(folder: Path) -> sandbox.Sandbox:
    fence = sandbox.around(folder, sandbox.Policy())
    if not fence.confines_writes:
        pytest.skip("this machine's sandbox does not confine writes")
    return fence


# ── the API's secrets stay behind ────────────────────────────────

def test_a_custom_tool_command_does_not_inherit_the_api_database_password(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NEUROCODE_DATABASE_URL", SECRET)
    called = custom_tools._run_command({"argv": [sys.executable, "-c", PRINT_ENV], "timeoutS": 10}, {},  # noqa: SLF001
                                       tmp_path)
    assert called.ok, called.text
    assert called.text.strip() == "absent", called.text


def test_a_governed_hook_does_not_inherit_the_api_database_password(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NEUROCODE_DATABASE_URL", SECRET)
    code, said, _ = extensions.run_hook(_hook(f"{sys.executable} -c \"{PRINT_ENV}\""), tmp_path, {})
    assert code == 0, said
    # run_hook redacts what the hook prints, so the value is not the test; whether the child had it is.
    assert said.strip() == "absent", said


async def test_a_project_checker_does_not_inherit_the_api_database_password(monkeypatch, tmp_path: Path):
    """A checker is the project's own code (an ESLint plugin, a Gradle script). It exits 1 if it can see
    the password, which the check reads as `failed`; `passed` means it could not."""
    monkeypatch.setenv("NEUROCODE_DATABASE_URL", SECRET)
    probe = "import os, sys; sys.exit(1 if 'NEUROCODE_DATABASE_URL' in os.environ else 0)"
    check = Check(owner="u1", target=Target(kind="folder", folder=tmp_path, name="probe"), missing=[],
                  checkers=[Checker("probe", "Probe", (sys.executable, "-c", probe), "text", "a test")])
    await check.run()
    assert check.tools[0].status == "passed", (check.tools[0].status, check.tools[0].note, check.tools[0].said)


def test_a_terminal_shell_does_not_inherit_the_api_database_password(monkeypatch):
    monkeypatch.setenv("NEUROCODE_DATABASE_URL", SECRET)
    assert "NEUROCODE_DATABASE_URL" not in t.base_env()
    assert os.environ["NEUROCODE_DATABASE_URL"] == SECRET


# ── the sandbox is around every command a person allowed ─────────

@needs_a_sandbox
def test_a_custom_tool_command_is_started_inside_the_sandbox(monkeypatch, tmp_path: Path):
    """sandbox.py: "the commands a custom tool names" are handed to the OS sandbox. Captured, not run."""
    started = _Captured()
    monkeypatch.setattr(custom_tools.subprocess, "run", started)
    custom_tools._run_command({"argv": ["/bin/echo", "hi"], "timeoutS": 5}, {}, tmp_path)  # noqa: SLF001
    assert started.seen == [sandbox.around(tmp_path, sandbox.env_policy()).wrap(["/bin/echo", "hi"])]
    assert started.seen[0][0] != "/bin/echo"


@needs_a_sandbox
def test_a_repository_hook_is_started_inside_the_sandbox(monkeypatch, tmp_path: Path):
    """A repository's hook is "somebody else's shell script" (extensions.py); it gets the same fence."""
    started = _Captured()
    monkeypatch.setattr(extensions.subprocess, "run", started)
    extensions.run_hook(_hook("true", 5), tmp_path, {})
    assert started.seen == [sandbox.around(tmp_path, sandbox.env_policy()).wrap(["/bin/sh", "-c", "true"])]
    assert started.seen[0][0] != "/bin/sh"


@needs_a_sandbox
def test_a_custom_tool_command_cannot_write_outside_its_checkout(tmp_path: Path, outside: Path):
    fence = _confining(tmp_path)
    mine = custom_tools._run_command({"argv": ["/usr/bin/touch", "mine.txt"]}, {}, tmp_path, fence)  # noqa: SLF001
    assert mine.ok and (tmp_path / "mine.txt").exists(), mine.text
    escape = custom_tools._run_command({"argv": ["/usr/bin/touch", str(outside / "theirs.txt")]}, {},  # noqa: SLF001
                                       tmp_path, fence)
    assert not escape.ok and not (outside / "theirs.txt").exists(), escape.text


@needs_a_sandbox
def test_a_repository_hook_cannot_write_outside_the_checkout(tmp_path: Path, outside: Path):
    fence = _confining(tmp_path)
    code, said, _ = extensions.run_hook(_hook(f"echo gone > {outside}/theirs.txt"), tmp_path, {}, fence)
    assert code != 0 and not (outside / "theirs.txt").exists(), said
    code, said, _ = extensions.run_hook(_hook("echo kept > mine.txt"), tmp_path, {}, fence)
    assert code == 0 and (tmp_path / "mine.txt").read_text() == "kept\n", said


@pytest_asyncio.fixture
async def checkout(session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The ERP's code in a temporary folder inside the machine's roots, with machine access on and a Claude
    home of its own, so only the hooks a test writes exist."""
    await load_workspace(session)
    repo = tmp_path / "erp"
    repo.mkdir()
    project = await session.get(m.Project, PID)
    assert project is not None
    project.source_kind, project.source_repo = "local", str(repo)
    await session.flush()
    monkeypatch.setenv("NEUROCODE_CLAUDE_HOME", str(tmp_path / "claude"))
    configured = real_settings().model_copy(update={"machine_roots": str(tmp_path), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    monkeypatch.setattr(extensions, "settings", lambda: configured)
    return repo


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every argv started, and each still started for real."""
    seen: list[list[str]] = []
    real = subprocess.run

    def run(argv: list[str], **kwargs: Any) -> Any:
        seen.append(list(argv))
        return real(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return seen


async def _logged(session: AsyncSession, action: str) -> list[str]:
    return list((await session.execute(
        select(m.ActivityEvent.detail).where(m.ActivityEvent.action == action))).scalars())


@needs_a_sandbox
async def test_a_called_custom_tool_runs_behind_the_workspace_fence_and_its_log_line_names_it(
        session: AsyncSession, checkout: Path, monkeypatch: pytest.MonkeyPatch):
    seen = _spy(monkeypatch)
    tool = m.CustomTool(id="ct-probe", project_id=PID, name="probe", description="", kind="command",
                        spec=custom_tools.checked_spec("command", {"argv": ["/bin/echo", "hi"]}), enabled=True)
    called = await CustomToolService(session).call(tool, {}, await session.get(m.Project, PID))
    assert called.ok and called.text.strip() == "hi", called.text

    fence = sandbox.around(checkout, await sandbox.read_policy(session))
    assert fence.wrap(["/bin/echo", "hi"]) in seen
    logged = await _logged(session, "Custom tool called")
    assert len(logged) == 1 and logged[0].endswith(fence.words()), logged


@needs_a_sandbox
async def test_a_fired_hook_runs_behind_the_workspace_fence_and_its_log_line_names_it(
        session: AsyncSession, checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "claude"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"hooks": {"PostToolUse": [
        {"hooks": [{"type": "command", "command": "echo fired > fired.log"}]}]}}))
    session.add(m.ToolRule(project_id=PID, tool="hook", pattern="*", action="allow", note=""))
    await session.flush()
    seen = _spy(monkeypatch)

    fired = await fire(session, "PostToolUse", await session.get(m.Project, PID), {}, subject="build")
    assert len(fired) == 1 and fired[0].ran and fired[0].exit_code == 0, fired
    assert (checkout / "fired.log").read_text() == "fired\n"              # it still writes in the checkout

    fence = sandbox.around(checkout, await sandbox.read_policy(session))
    assert fence.wrap(["/bin/sh", "-c", "echo fired > fired.log"]) in seen
    logged = await _logged(session, "Hook fired")
    assert len(logged) == 1 and logged[0].endswith(fence.words()), logged


# ── a restricted project is restricted here too ─────────────────

@pytest_asyncio.fixture
async def api(session: AsyncSession, settings: Settings, tmp_path: Path) -> AsyncIterator[FastAPI]:
    await load_workspace(session)
    built = create_api(db=None)
    built.state.settings = settings.model_copy(update={"machine_roots": str(tmp_path), "machine_access": True})

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    built.dependency_overrides[deps.session] = use_the_test_session
    try:
        yield built
    finally:
        await terminals_of(built).close_all()


@pytest_asyncio.fixture
async def owner(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


@asynccontextmanager
async def signed_in(api: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        assert (await c.post("/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200
        yield c


async def _operator(owner: AsyncClient, session: AsyncSession, grant: str | None = None) -> str:
    """A role holding machine:access and nothing that is never narrowed; a person wearing it; and `erp`
    restricted, with its Owner listed in it — a grant is the whole of anyone's rights in a restricted
    project, an Owner's too, beyond the few `NEVER_NARROWED` keeps — and, when `grant` names a role
    ("operator" for their own), that person listed as well. Returns the person's id."""
    made = await owner.post("/admin/roles", json={"name": "Operator", "description": "runs things",
                                                  "permissions": ["machine:access", "sessions:read"]})
    assert made.status_code == 201, made.text
    role = made.json()["id"]
    user = await owner.post("/admin/users", json={"email": "op@example.com", "name": "Op",
                                                  "password": PASSWORD, "roles": [role]})
    assert user.status_code == 201, user.text
    project = await session.get(m.Project, PID)
    assert project is not None
    project.restricted = True
    owner_id = (await session.execute(select(m.User.id).where(m.User.email == OWNER["email"]))).scalar_one()
    session.add(m.ProjectRole(project_id=PID, user_id=owner_id, role_id="owner"))
    if grant is not None:
        session.add(m.ProjectRole(project_id=PID, user_id=user.json()["id"],
                                  role_id=role if grant == "operator" else grant))
    await session.flush()
    return str(user.json()["id"])


async def test_run_configs_of_a_restricted_project_are_not_listed_to_someone_without_a_grant(api, owner, session):
    await _operator(owner, session)
    assert (await owner.get(f"/projects/{PID}/run-configs")).status_code == 200     # its people still see them
    async with signed_in(api, "op@example.com") as op:
        assert (await op.get(f"/projects/{PID}")).status_code == 404               # the front door holds
        listed = await op.get(f"/projects/{PID}/run-configs")
        assert listed.status_code == 404, f"{listed.status_code}: {listed.text}"
        assert (await op.get(f"/projects/{PID}/run-configs/detect")).status_code == 404


async def test_a_run_config_cannot_be_written_into_a_restricted_project_without_a_grant(api, owner, session):
    await _operator(owner, session)
    kept = await owner.post(f"/projects/{PID}/run-configs", json={"name": "serve", "command": "true"})
    assert kept.status_code == 201, kept.text
    async with signed_in(api, "op@example.com") as op:
        made = await op.post(f"/projects/{PID}/run-configs", json={"name": "x", "command": "true"})
        assert made.status_code == 404, f"{made.status_code}: {made.text}"
        changed = await op.patch(f"/projects/{PID}/run-configs/{kept.json()['id']}", json={"command": "false"})
        assert changed.status_code == 404, f"{changed.status_code}: {changed.text}"
        removed = await op.delete(f"/projects/{PID}/run-configs/{kept.json()['id']}")
        assert removed.status_code == 404, f"{removed.status_code}: {removed.text}"


async def test_a_restricted_projects_configuration_cannot_be_started_by_its_id_without_a_grant(api, owner,
                                                                                               session):
    op_id = await _operator(owner, session)
    kept = await owner.post(f"/projects/{PID}/run-configs", json={"name": "serve", "command": "sleep 5"})
    assert kept.status_code == 201, kept.text
    async with signed_in(api, "op@example.com") as op:
        started = await op.post(f"/run-configs/{kept.json()['id']}/start")
        assert started.status_code == 404, f"{started.status_code}: {started.text}"
        assert "erp" not in started.text                                   # not even which project it is
    assert terminals_of(api).mine(op_id) == []                               # and nothing was started


async def test_a_terminal_cannot_be_opened_in_a_restricted_project_without_a_grant(api, owner, session):
    await _operator(owner, session)
    async with signed_in(api, "op@example.com") as op:
        opened = await op.post("/machine/terminals", json={"projectId": PID})
        assert opened.status_code == 404, f"{opened.status_code}: {opened.text}"
        debugged = await op.get(f"/projects/{PID}/debug")
        assert debugged.status_code == 404, f"{debugged.status_code}: {debugged.text}"
        launched = await op.post(f"/projects/{PID}/debug", json={"program": "x.py"})
        assert launched.status_code == 404, f"{launched.status_code}: {launched.text}"


async def test_a_grant_that_leaves_out_machine_access_is_honoured_inside_the_project(api, owner, session):
    await _operator(owner, session, grant="engineer")
    async with signed_in(api, "op@example.com") as op:
        listed = await op.get(f"/projects/{PID}/run-configs")
        assert listed.status_code == 403 and "machine:access" in listed.json()["detail"], listed.text
        opened = await op.post("/machine/terminals", json={"projectId": PID})
        assert opened.status_code == 403 and "machine:access" in opened.json()["detail"], opened.text


async def test_a_grant_that_carries_machine_access_lets_its_holder_in(api, owner, session):
    await _operator(owner, session, grant="operator")
    async with signed_in(api, "op@example.com") as op:
        assert (await op.get(f"/projects/{PID}/run-configs")).status_code == 200
        made = await op.post(f"/projects/{PID}/run-configs", json={"name": "x", "command": "true"})
        assert made.status_code == 201, made.text


# ── one start, one restart, one process ──────────────────────────

async def test_two_starts_of_one_configuration_open_one_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The database lookups are stood in for so the two requests interleave at every await, exactly as
    two requests on two connections do."""
    monkeypatch.setenv("SHELL", "/bin/sh")
    root = Path(os.path.realpath(tmp_path))
    config = SimpleNamespace(id=7, project_id="p", name="serve", kind="run", language="shell",
                             command="sleep 5", args=[], cwd="", env={})
    project = SimpleNamespace(id="p", name="P")
    source = SimpleNamespace(ready=True, root=root, label="p", primary=True)
    who = Person(id="u1", email="op@example.com", name="Op", status="active", roles=("owner",),
                 permissions=frozenset({t.MACHINE}))

    async def one(self, _id):
        await asyncio.sleep(0)
        return config, "Op"

    async def proj(self, _pid):
        await asyncio.sleep(0)
        return project

    async def roots(_session, _project):
        await asyncio.sleep(0.01)
        return [source]

    async def record(**_):
        return None

    monkeypatch.setattr(t.RunConfigService, "one", one)
    monkeypatch.setattr(t.RunConfigService, "project", proj)
    monkeypatch.setattr(t, "project_roots", roots)
    monkeypatch.setattr(t, "folder_in_project", lambda sources, rel: (source, root))

    held = t.Terminals()
    service = t.RunConfigService(session=None)  # type: ignore[arg-type]
    service.audit = SimpleNamespace(record=record)  # type: ignore[assignment]
    try:
        results = await asyncio.gather(service.start(7, who, held, cols=80, rows=24),
                                       service.start(7, who, held, cols=80, rows=24), return_exceptions=True)
        opened = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if isinstance(r, Refused)]
        assert len(held.mine("u1")) == 1, f"{len(held.mine('u1'))} terminals run the same configuration"
        assert len(opened) == 1 and len(refused) == 1, results
        assert "already running" in str(refused[0])
    finally:
        await held.close_all()


async def test_two_restarts_at_once_leave_no_process_behind(tmp_path: Path):
    """A program that shrugs off the hang-up and the terminate, as a dev server with its own handlers may,
    makes both stops wait out the whole grace in step — the interleaving in which two unguarded restarts
    both spawn and the first new process is dropped still running. The grace is cut so the test is quick."""
    held = t.Terminals()
    terminal = held.open(owner="u1", kind="run", title="stubborn", cwd=tmp_path, env=t.base_env(), cols=80,
                         rows=24, argv=["/bin/sh", "-c", "trap '' HUP TERM; echo ready; sleep 30"])
    started: list[subprocess.Popen[bytes]] = [terminal.proc] if terminal.proc else []
    spawn, stop = terminal.spawn, terminal.stop

    def spawn_and_keep() -> None:
        spawn()
        assert terminal.proc is not None
        started.append(terminal.proc)

    terminal.spawn = spawn_and_keep  # type: ignore[method-assign]
    terminal.stop = lambda grace=0.2: stop(grace)  # type: ignore[method-assign]
    try:
        for _ in range(100):                          # until the trap is set: it printed after setting it
            if b"ready" in terminal.buffer:
                break
            await asyncio.sleep(0.02)
        await asyncio.gather(terminal.restart(), terminal.restart())
        assert len(started) == 3 and terminal.proc is started[-1] and terminal.proc.poll() is None
        left = [p.pid for p in started[:-1] if p.poll() is None]
        assert not left, f"restarted, and still running with nobody holding them: {left}"
    finally:
        await held.close_all()
        for proc in started:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()


# ── the socket's origin ──────────────────────────────────────────

class _Socket:
    def __init__(self, origin: str, host: str) -> None:
        self.headers = {"origin": origin, "host": host}


def test_the_socket_admits_no_origin_that_cors_itself_would_refuse():
    """Starlette's CORS matches the operator's pattern whole; the socket matches it the same way."""
    import re

    config = Settings(cors_origin_regex=r"https://app\.example\.com")
    other = "https://app.example.com.other.test"
    assert re.compile(config.cors_origin_regex).fullmatch(other) is None      # CORS refuses it
    assert not origin_allowed(_Socket(other, "api.example.com"), config, by_cookie=True)  # type: ignore[arg-type]
    assert origin_allowed(_Socket("https://app.example.com", "api.example.com"), config,  # type: ignore[arg-type]
                          by_cookie=True)


def test_the_apps_own_page_is_still_let_in():
    config = Settings(cors_origin_regex=LOCAL_ORIGINS)
    assert origin_allowed(_Socket("http://localhost:5180", "127.0.0.1:8000"), config, by_cookie=True)  # type: ignore[arg-type]
    assert origin_allowed(_Socket("https://nc.example.com", "nc.example.com"), config, by_cookie=True)  # type: ignore[arg-type]
    assert not origin_allowed(_Socket("http://localhost:5180.evil.test", "127.0.0.1:8000"), config,  # type: ignore[arg-type]
                              by_cookie=True)
