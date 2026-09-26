"""The fence around every command the runtime runs.

Two halves. The first asks the sandbox module what it would do on each kind of machine, with the
machine injected, so the Linux answers are checked on a Mac and the "there is nothing here" answer is
checked on a machine that has something. The second runs real commands through the real Seatbelt on
this Mac and watches them be refused — a sandbox nobody has watched refuse anything is a claim, not a
sandbox — and then does the same through an actual agent run, whose test command tries to write into
the home directory and cannot.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.data.engine import Database
from app.models import Plan, PlanStep, Project, Run
from app.repositories import ProjectRepository, RunLogRepository, RunRepository
from app.services import sandbox
from app.services.runs import RunService, execute
from app.settings import Settings

from tests.test_runs_checks import FakeGateway, edit, run_git

darwin_only = pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists(sandbox.SANDBOX_EXEC),
                                 reason="the real Seatbelt is only on macOS")


# ── what each kind of machine offers ─────────────────────────────
def test_the_sandbox_is_whatever_this_machine_has_and_says_so_when_it_has_none():
    assert sandbox.detect(platform="darwin", exists=lambda p: p == sandbox.SANDBOX_EXEC) == (
        sandbox.SEATBELT, "")
    fine = lambda _kind: True                                                      # noqa: E731
    assert sandbox.detect(platform="linux", which=lambda t: "/usr/bin/bwrap" if t == "bwrap" else None,
                          works=fine) == (sandbox.BUBBLEWRAP, "")

    kind, why = sandbox.detect(platform="linux", which=lambda t: "/usr/bin/unshare" if t == "unshare" else None,
                               works=fine)
    assert kind == sandbox.UNSHARE and "bubblewrap" in why

    kind, why = sandbox.detect(platform="linux", which=lambda t: None)
    assert kind == sandbox.NONE and "apt install bubblewrap" in why

    # No pretending: a machine with nothing gets nothing, and the sentence says what that means.
    kind, why = sandbox.detect(platform="win32", which=lambda t: None, exists=lambda p: False)
    assert kind == sandbox.NONE and "same rights as the account running NeuroCode" in why


def test_a_fence_the_kernel_refuses_is_not_trusted_because_it_is_installed():
    """The live server's container has unshare and no namespaces: every test, check and hook there failed with
    "unshare failed: Operation not permitted". A refused fence is no fence, and says why."""
    both = lambda t: f"/usr/bin/{t}"                                              # noqa: E731
    kind, why = sandbox.detect(platform="linux", which=both, works=lambda _kind: False)
    assert kind == sandbox.NONE and "refuses the Linux namespaces" in why and "container" in why
    kind, why = sandbox.detect(platform="linux", which=both, works=lambda k: k == sandbox.UNSHARE)
    assert kind == sandbox.UNSHARE and "bubblewrap is refused here" in why


def test_a_probe_that_cannot_even_start_is_a_refusal(monkeypatch: pytest.MonkeyPatch):
    def missing(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("bwrap")

    sandbox.allowed.cache_clear()
    monkeypatch.setattr(sandbox.subprocess, "run", missing)
    try:
        assert sandbox.allowed(sandbox.BUBBLEWRAP) is False
    finally:
        sandbox.allowed.cache_clear()                   # the next test asks the real kernel again


def test_a_machine_with_no_sandbox_says_so_in_the_words_the_run_screen_prints(monkeypatch):
    monkeypatch.setattr(sandbox, "detect", lambda **_: (sandbox.NONE, "Nothing is installed here."))
    fence = sandbox.around("/tmp", sandbox.Policy())
    assert fence.wrap(["make", "test"]) == ["make", "test"]          # nothing is added, nothing is claimed
    assert fence.words() == "Sandbox: none. Nothing is installed here."
    assert fence.json()["confinesWrites"] is False


def test_only_the_network_can_be_confined_without_bubblewrap_and_allowing_it_leaves_nothing(monkeypatch):
    monkeypatch.setattr(sandbox, "detect", lambda **_: (sandbox.UNSHARE, "bubblewrap is not installed"))
    off = sandbox.around("/tmp", sandbox.Policy(network=False))
    assert off.wrap(["pytest"])[:3] == ["unshare", "--net", "--"]
    assert "writes are not confined" in off.words() and "network off" in off.words()

    # With the network allowed there is nothing left for unshare to take away, so it says none.
    on = sandbox.around("/tmp", sandbox.Policy(network=True))
    assert on.kind == sandbox.NONE and "Install bubblewrap" in on.why


def test_bubblewrap_binds_the_worktree_and_the_temp_dirs_over_a_read_only_machine(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "detect", lambda **_: (sandbox.BUBBLEWRAP, ""))
    argv = sandbox.around(tmp_path, sandbox.Policy()).wrap(["go", "test", "./..."])
    assert argv[0] == "bwrap" and argv[-3:] == ["go", "test", "./..."]
    assert "--ro-bind" in argv and "--die-with-parent" in argv and "--unshare-net" in argv
    # The worktree is bound writable over the read-only root, after it.
    assert argv.index("--bind") > argv.index("--ro-bind")
    assert str(tmp_path.resolve()) in argv
    assert "--unshare-net" not in sandbox.around(tmp_path, sandbox.Policy(network=True)).wrap(["x"])


# ── the real thing, on this machine ──────────────────────────────
@pytest.fixture
def outside(tmp_path_factory) -> Iterator[Path]:
    """A folder the sandbox must refuse — which means outside the temp directory, because the temp
    directory is one of the two places a command is *allowed* to write. pytest's own tmp_path is in
    there, so a test that escaped into it would prove nothing. This one is removed either way, so a
    sandbox that failed leaves the machine as it found it."""
    folder = Path.home() / ".neurocode-sandbox-test"
    folder.mkdir(exist_ok=True)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@darwin_only
def test_seatbelt_really_refuses_a_write_outside_the_worktree(tmp_path, outside):
    work = tmp_path / "work"
    work.mkdir()
    fence = sandbox.around(work, sandbox.Policy())

    inside = subprocess.run(fence.wrap(["/bin/sh", "-c", "echo hello > mine.txt"]), cwd=work,
                            capture_output=True, text=True)
    assert inside.returncode == 0 and (work / "mine.txt").read_text() == "hello\n"

    escape = subprocess.run(fence.wrap(["/bin/sh", "-c", f"echo gone > {outside}/theirs.txt"]), cwd=work,
                            capture_output=True, text=True)
    assert escape.returncode != 0 and not (outside / "theirs.txt").exists()
    assert "not permitted" in escape.stderr.lower()

    # And it can still read what a build reads — a sandbox that broke every test would be no use.
    read = subprocess.run(fence.wrap(["/bin/sh", "-c", "cat /etc/hosts > read.txt"]), cwd=work,
                          capture_output=True, text=True)
    assert read.returncode == 0 and (work / "read.txt").read_text()


@darwin_only
def test_the_network_is_off_until_somebody_turns_it_on(tmp_path):
    """Against a socket on this machine, so the test needs no internet and cannot be flaky."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    reach = ("import socket,sys\n"
             f"try: socket.create_connection(('127.0.0.1', {port}), timeout=3); print('reached')\n"
             "except OSError as e: print('refused', e); sys.exit(7)\n")
    try:
        off = sandbox.around(tmp_path, sandbox.Policy(network=False))
        blocked = subprocess.run(off.wrap([sys.executable, "-c", reach]), capture_output=True, text=True)
        assert blocked.returncode == 7 and "refused" in blocked.stdout

        on = sandbox.around(tmp_path, sandbox.Policy(network=True))
        assert "network allowed" in on.words()
        allowed = subprocess.run(on.wrap([sys.executable, "-c", reach]), capture_output=True, text=True)
        assert allowed.returncode == 0 and "reached" in allowed.stdout
    finally:
        listener.close()


def test_a_path_with_a_quote_in_it_does_not_widen_the_profile(tmp_path):
    odd = tmp_path / 'we"ird'
    odd.mkdir()
    profile = sandbox.Sandbox(sandbox.SEATBELT, (str(odd),), False)._seatbelt()
    assert f'(subpath "{odd}"'.replace('"', '\\"', 1) not in profile      # not spliced in raw
    assert '\\"' in profile and profile.count("(subpath") == 1


# ── the policy: what the deployment allows, and what the workspace chose ──
async def test_the_workspace_chooses_the_network_and_the_server_keeps_the_last_word(session: AsyncSession):
    strict = Settings(sandbox=True, sandbox_network=False)
    assert await sandbox.read_policy(session, strict) == sandbox.Policy(enabled=True, network=False)

    await sandbox.write_policy(session, enabled=True, network=True)
    assert (await sandbox.read_policy(session, strict)).network is True

    # A server started without a sandbox cannot be given one from a screen: the fence protects the
    # machine the API runs on, and that is the deployment's call.
    opened = Settings(sandbox=False)
    assert (await sandbox.read_policy(session, opened)).enabled is False

    await sandbox.write_policy(session, enabled=False, network=False)
    assert (await sandbox.read_policy(session, strict)).enabled is False


# ── an actual run, whose test command tries to get out ───────────
PROJECT, PLAN = "sandbox-project", "PLAN-7777"


@pytest_asyncio.fixture
async def escaping(tmp_path: Path, schema: str, outside: Path) -> AsyncIterator[tuple[Database, Path]]:
    """A repository whose `make test` writes a file next to the checkout — which is exactly the thing
    a worktree does not stop and a sandbox does."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("def total(x):\n    return x\n")
    target = outside / "loot.txt"
    (root / "Makefile").write_text(f"test:\n\t@echo 'running'; echo taken > {target}\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)

    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=PROJECT, name="Escaping", source_kind="local", source_repo=str(root)))
        await s.flush()
        s.add(m.Setting(key=f"runtime.tests.{PROJECT}", value="allowed"))
        s.add(Plan(id="p-sbx", ref=PLAN, project_id=PROJECT, status="draft",
                   raw_requirement="Round the invoice total", affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(PlanStep(id="p-sbx-1", plan_id="p-sbx", n=1, label="Fix rounding in pkg/core.py",
                       agent="Backend Engineer"))
    yield db, target
    async with db.session() as s:
        for run in (await s.execute(select(Run).where(Run.project_id == PROJECT))).scalars().unique():
            shutil.rmtree(Path(run.worktree), ignore_errors=True)
        await s.execute(delete(Run).where(Run.project_id == PROJECT))
        await s.execute(delete(Plan).where(Plan.project_id == PROJECT))
        await s.execute(delete(m.Setting).where(m.Setting.key.startswith(f"runtime.tests.{PROJECT}")))
        await s.execute(delete(m.Setting).where(m.Setting.key == sandbox.SETTING_KEY))
        await s.execute(delete(Project).where(Project.id == PROJECT))
    await db.close()


@darwin_only
async def test_a_run_s_test_command_is_fenced_and_the_run_says_which_fence(escaping):
    db, target = escaping
    gateway = FakeGateway(edit())
    async with db.session() as s:
        project = await ProjectRepository(s).get(PROJECT)
        plan = (await s.execute(select(Plan).where(Plan.ref == PLAN))).scalar_one()
        ref = (await RunService(s, gateway).plan_runs(plan, None, project, "Rajat"))[-1].ref
    await execute(db, gateway, ref)

    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        lines = [entry.line for entry in await RunLogRepository(s).after(run.id, limit=2000)]

    # The command ran — and the file it tried to write outside the worktree is not there.
    assert run.tests_status in ("passed", "failed")
    assert not target.exists(), "the test command wrote outside its worktree"
    # And the run says, in words, which fence it ran behind.
    said = [line for line in lines if line.startswith("Sandbox:")]
    assert said and "macOS Seatbelt" in said[0] and "network off" in said[0]
