"""The rules that keep a run safe to leave alone, held against the ways they were found broken.

Four groups. Who may act on a run or a plan: every write reached by a bare `RUN-…` or `PLAN-…` is fenced
the way its read is, and weighed against what the person holds in that project. What a model's files and
a project's own commands can do: a path is refused however the git directory is spelled, a refused file
leaves nothing half-written, git's hooks never run for the runtime, and a test command takes everything it
started with it when it ends and never sees the API's secrets. What a merge may be taken back as: only the
merge NeuroCode made, of the run's branch, on the branch it went into. And the runtime's own bookkeeping: a
signature is never skipped on a stale count, two presses start a run once, and a restart leaves nothing
queued for ever.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent
from app import models as m
from app.agent.git import AUTHOR
from app.api import deps
from app.api.app import create_api, reconcile_interrupted
from app.data.engine import Database
from app.repositories import ApprovalRepository, ProjectRepository, RunRepository
from app.services import runs as runtime
from app.services.errors import Refused
from app.services.runs import RunService, execute
from tests.fixtures.workspace import load_workspace

agent_git = importlib.import_module("app.agent.git")

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PASSWORD = "a long enough password"
BRANCH = "neurocode/task-1"


def git(args: list[str], cwd: Path, *, as_agent: bool = False) -> str:
    who = AUTHOR if as_agent else ["-c", "user.name=Rajat", "-c", "user.email=rajat@example.com",
                                   "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *who, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def head(repo: Path, rev: str = "HEAD") -> str:
    return git(["rev-parse", rev], repo).strip()


def checkout(root: Path, files: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True)
    for name, text in (files or {"app.py": "a = 1\n"}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git(["init", "-q", "-b", "main"], root)
    git(["add", "-A"], root)
    git(["commit", "-qm", "first"], root)
    return root


def agent_branch(root: Path, tree: Path, base: str, change: str | None) -> None:
    git(["worktree", "add", "-q", "-b", BRANCH, str(tree), base], root)
    if change is not None:
        (tree / "app.py").write_text(change)
        git(["commit", "-qam", "RUN-1: change a"], tree, as_agent=True)


def teammate_merge(root: Path) -> str:
    """The person merges a teammate's branch the ordinary way: HEAD is now a merge commit that is theirs."""
    git(["checkout", "-q", "-b", "teammate"], root)
    (root / "team.py").write_text("t = 1\n")
    git(["add", "-A"], root)
    git(["commit", "-qm", "teammate work"], root)
    git(["checkout", "-q", "main"], root)
    git(["merge", "-q", "--no-ff", "-m", "Merge teammate", "teammate"], root)
    return head(root)


# ── the API, as a signed-in person ───────────────────────────────
@pytest_asyncio.fixture
async def api(session: AsyncSession):
    await load_workspace(session)
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def _person(client: AsyncClient, email: str, roles: list[str]) -> str:
    made = await client.post("/admin/users", json={"email": email, "name": email.split("@")[0],
                                                   "password": PASSWORD, "roles": roles})
    assert made.status_code == 201, made.text
    return made.json()["id"]


@asynccontextmanager
async def _login(api, email: str) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        assert (await c.post("/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200
        yield c


async def add_run(session: AsyncSession, pid: str, root: Path, tree: Path, base: str, *, files: int,
                  restricted: bool = False, sources: list[dict] | None = None,
                  steps: list[m.RunStep] | None = None) -> m.Run:
    """A finished run, written as a row, whose receipt is exactly what `_review` keeps: the fingerprint of
    the patch it was handed, empty or not."""
    session.add(m.Project(id=pid, name=pid.title(), source_kind="local", source_repo=str(root),
                          restricted=restricted))
    await session.flush()
    run = m.Run(id="r-RUN-1", ref="RUN-1", project_id=pid, status="done", role="solo", agent="Backend Engineer",
                branch=BRANCH, worktree=str(tree), repo=str(root), base=base, requirement="change a",
                requested_by="Rajat", diff_files=files, steps=steps or [], conflicts=[],
                review={"sources": sources} if sources else {})
    session.add(run)
    await session.flush()
    patch, _ = runtime._patch(runtime._parts(run))
    run.review = {**(run.review or {}), "receipt": {"sha256": agent.fingerprint(patch)}}
    await session.flush()
    return run


# ── who may act on a run or a plan ───────────────────────────────
@pytest_asyncio.fixture
async def fenced(tmp_path: Path, session: AsyncSession, client: AsyncClient) -> Path:
    """A checkout on main and one accepted, reviewed run on it, in a project that is restricted."""
    root = checkout(tmp_path / "shop")
    base = head(root)
    tree = tmp_path / "worktrees" / "RUN-1"
    agent_branch(root, tree, base, change="a = 2\n")
    await add_run(session, "fenced", root, tree, base, files=1, restricted=True)
    return root


async def _grant(session: AsyncSession, pid: str, user_id: str, role_id: str) -> None:
    session.add(m.ProjectRole(project_id=pid, user_id=user_id, role_id=role_id))
    await session.flush()


#: Every write on a run, and the body it needs to get past validation to the fence.
RUN_WRITES: list[tuple[str, dict[str, Any] | None]] = [
    ("/runs/RUN-1/cancel", None), ("/runs/RUN-1/merge", None), ("/runs/RUN-1/unmerge", None),
    ("/runs/RUN-1/push", None), ("/runs/RUN-1/pr", None), ("/runs/RUN-1/review", None),
    ("/runs/RUN-1/discard", None), ("/runs/RUN-1/rework", {"notes": "again"}), ("/runs/RUN-1/resume", None),
    ("/runs/RUN-1/steps/1/revert", {"redo": True}),
]


async def test_someone_who_cannot_see_a_project_can_do_nothing_to_its_runs(api, client, fenced, session):
    await _person(client, "outsider@example.com", ["approver"])     # runs:run and runs:merge, no grant here
    before = head(fenced)
    async with _login(api, "outsider@example.com") as outsider:
        assert (await outsider.get("/runs/RUN-1")).status_code == 404       # the read is fenced...
        for path, body in RUN_WRITES:                                         # ...and so is every write
            answer = await outsider.post(path, json=body) if body else await outsider.post(path)
            assert answer.status_code == 404, (path, answer.text)
    assert git(["branch", "--list", BRANCH], fenced).strip(), "the run's branch was deleted by an outsider"
    assert head(fenced) == before, "an outsider merged into the checkout"
    run = await session.get(m.Run, "r-RUN-1")
    assert run.status == "done" and not run.removed and not run.merged


async def test_a_viewer_grant_in_a_project_narrows_what_its_runs_allow(api, client, fenced, session):
    uid = await _person(client, "narrow@example.com", ["approver"])
    await _grant(session, "fenced", uid, "viewer")                    # in this project: a viewer
    before = head(fenced)
    async with _login(api, "narrow@example.com") as narrowed:
        me = (await narrowed.get("/auth/me")).json()["user"]
        assert "runs:merge" not in me["projectRights"]["fenced"]
        assert (await narrowed.get("/runs/RUN-1")).status_code == 200       # a viewer there reads it
        merged = await narrowed.post("/runs/RUN-1/merge")
        discarded = await narrowed.post("/runs/RUN-1/discard")
    assert merged.status_code == 403 and "runs:merge" in merged.text, merged.text
    assert discarded.status_code == 403 and "runs:run" in discarded.text, discarded.text
    assert head(fenced) == before


async def _hide_hims(session: AsyncSession) -> None:
    hims = await session.get(m.Project, "hims")
    hims.restricted = True
    await session.flush()


#: Every route a plan is reached by, read or write, with the body it needs to reach the fence.
PLAN_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/plans/PLAN-519/dispatch", None), ("POST", "/plans/PLAN-519/recompile", None),
    ("PATCH", "/plans/PLAN-519", {"acceptanceCriteria": ["done"]}),
    ("PATCH", "/plans/PLAN-519/steps/p519-s1", {"label": "x"}),
    ("POST", "/plans/PLAN-519/steps", {"label": "x", "agent": "Backend Engineer"}),
    ("DELETE", "/plans/PLAN-519/steps/p519-s1", None), ("PATCH", "/plans/PLAN-519/steps", {"order": ["p519-s1"]}),
    ("GET", "/plans/PLAN-519/comments", None), ("POST", "/plans/PLAN-519/comments", {"body": "why?"}),
    ("POST", "/plans/PLAN-519/comments/1/resolve", None), ("POST", "/plans/PLAN-519/revise", None),
]


async def test_someone_who_cannot_see_a_plan_can_neither_read_nor_shape_nor_dispatch_it(api, client, session):
    await _hide_hims(session)
    await _person(client, "appr2@example.com", ["approver"])        # plans:decide and :compile, no grant in hims
    async with _login(api, "appr2@example.com") as outsider:
        assert (await outsider.get("/plans/PLAN-519")).status_code == 404
        for method, path, body in PLAN_ROUTES:
            answer = await outsider.request(method, path, json=body)
            assert answer.status_code == 404, (method, path, answer.text)
        compiled = await outsider.post("/plans/compile", json={"requirement": "Add a field", "projectId": "hims"})
        assert compiled.status_code == 404, compiled.text
    plan = await session.get(m.Plan, "p519")
    assert plan.status != "dispatched"


async def test_a_viewer_grant_in_a_project_does_not_dispatch_its_plans(api, client, session):
    await _hide_hims(session)
    uid = await _person(client, "narrow2@example.com", ["approver"])
    await _grant(session, "hims", uid, "viewer")
    async with _login(api, "narrow2@example.com") as narrowed:
        assert (await narrowed.get("/plans/PLAN-519/comments")).status_code == 200
        dispatched = await narrowed.post("/plans/PLAN-519/dispatch")
    assert dispatched.status_code == 403 and "plans:decide" in dispatched.text, dispatched.text
    assert (await session.get(m.Plan, "p519")).status != "dispatched"


# ── what a model's files can do to a worktree ────────────────────
def test_a_git_directory_is_refused_however_it_is_spelled():
    for spelled in (".GIT", ".Git", "sub/.gIt/config", ".GIT/hooks/pre-commit", ".git./config", ".git /x",
                    "git~1/config", "GIT~1", ".g\u200cit/config", ".git::$INDEX_ALLOCATION/config", "a/../b"):
        with pytest.raises(agent.Refused):
            agent.safe_path(spelled)
    # Names that only begin like it are ordinary files a project keeps.
    for fine in (".gitignore", ".gitattributes", ".github/workflows/ci.yml", "docs/.gitkeep", "git/notes.md"):
        assert str(agent.safe_path(fine)) == fine


def test_a_refused_file_leaves_nothing_written(tmp_path: Path):
    with pytest.raises(agent.Refused, match="larger than"):
        agent.apply_files(tmp_path, [("half.py", "h = 1\n"), ("big.py", "x" * 300_000)])
    assert not (tmp_path / "half.py").exists()
    assert agent.apply_files(tmp_path, [("half.py", "h = 1\n")]) == ["half.py"]


def test_the_runtime_never_runs_a_repositorys_git_hooks(tmp_path: Path):
    """A repository whose config points core.hooksPath at a tracked folder, as husky does: the hook files
    are the repository's, a model may rewrite them, and a worktree shares the config. None of the runtime's
    own git calls — opening a worktree, a step's commit, the integration merge, the merge into the checkout,
    opening a worktree again — may run one. The person's own git still does."""
    names = ("pre-commit", "commit-msg", "post-commit", "post-checkout", "pre-merge-commit", "post-merge")
    root = checkout(tmp_path / "shop", {"app.py": "a = 1\n",
                                        **{f".githooks/{n}": "#!/bin/sh\nexit 0\n" for n in names}})
    for name in names:
        (root / ".githooks" / name).chmod(0o755)
    git(["add", "-A"], root)
    git(["commit", "-qm", "hooks are executable"], root)
    git(["config", "core.hooksPath", ".githooks"], root)
    base, marks = head(root), tmp_path / "ran"
    tree, other = tmp_path / "worktrees" / "RUN-1", tmp_path / "worktrees" / "RUN-2"

    agent.open_worktree(root, BRANCH, base, tree)
    # What a model can write through apply_files: the tracked hooks, saying something else.
    evil = f"#!/bin/sh\necho \"$0\" >> {marks}\n"
    agent.apply_files(tree, [*((f".githooks/{n}", evil) for n in names), ("app.py", "a = 2\n")])
    assert agent.commit(tree, "Step one\n\nNeuroCode RUN-1")
    agent.open_worktree(root, "neurocode/integration", base, other)
    assert agent.merge_branch(other, BRANCH, base, "neurocode/integration") == (True, [])
    shutil.rmtree(tree)
    agent_git.reopen_worktree(root, BRANCH, tree)
    assert agent.merge_into_checkout(root, BRANCH, "Merge RUN-1\n\nNeuroCode " + BRANCH)["merged"] is True
    assert not marks.exists(), f"the runtime ran the model's hooks: {marks.read_text()}"

    git(["commit", "--allow-empty", "-qm", "the person's own commit"], root)
    assert marks.exists(), "the hooks were never live, so the test above proved nothing"


# ── a project's own test command ─────────────────────────────────
def test_a_timed_out_test_command_takes_its_children_with_it(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(agent_git, "TEST_TIMEOUT", 1)
    pidfile = tmp_path / "child.pid"
    argv = ["/bin/sh", "-c", f"sleep 6 & echo $! > {pidfile}; echo started; wait"]
    started = time.monotonic()
    try:
        agent_git.run_tests(argv, tmp_path, lambda i, s: None, threading.Event())
        took = time.monotonic() - started
        child = int(pidfile.read_text().strip())
        try:
            os.kill(child, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert took < 4, f"run_tests returned {took:.1f}s after a 1s timeout"
        assert not alive, "the command's child outlived the timeout"
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def test_a_finished_test_command_does_not_wait_on_what_it_left_running(tmp_path: Path):
    """A test that forgot to stop its server: the command exits 0, the server keeps the pipe open. The run
    used to read until the server exited — for ever, for a server."""
    pidfile = tmp_path / "server.pid"
    started = time.monotonic()
    try:
        code, tail = agent_git.run_tests(["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}; echo ok"], tmp_path,
                                         lambda i, s: None, threading.Event())
        assert code == 0 and tail == ["ok"] and time.monotonic() - started < 6
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text().strip()), 0)
    finally:
        try:
            os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
        except (ProcessLookupError, ValueError, OSError):
            pass


def test_a_stop_ends_a_silent_test_command_at_once(tmp_path: Path):
    """The stop used to be read only when a line arrived, so a command printing nothing ran on."""
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.monotonic()
    agent_git.run_tests(["/bin/sh", "-c", "sleep 30"], tmp_path, lambda i, s: None, stop)
    assert time.monotonic() - started < 5


def test_a_test_command_keeps_its_lines_and_its_exit_code(tmp_path: Path):
    said: list[tuple[int, str]] = []
    code, tail = agent_git.run_tests(["/bin/sh", "-c", "printf 'one\\r\\ntwo\\nthree'; exit 3"], tmp_path,
                                     lambda i, s: said.append((i, s)), threading.Event())
    assert code == 3 and said == [(0, "one"), (1, "two"), (2, "three")] and tail == ["one", "two", "three"]


def test_a_test_command_never_sees_the_apis_secrets(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NEUROCODE_DATABASE_URL", "postgresql://neurocode:hunter2@db/neurocode")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-for-tests")
    said: list[str] = []
    code, _ = agent_git.run_tests(
        ["/bin/sh", "-c", 'echo "${NEUROCODE_DATABASE_URL:-absent} ${DEEPSEEK_API_KEY:-absent} $CI $NO_COLOR"'],
        tmp_path, lambda i, s: said.append(s), threading.Event())
    assert code == 0 and said == ["absent absent 1 1"]


# ── what a merge may be taken back as ────────────────────────────
async def test_a_branch_the_checkout_already_holds_is_not_recorded_as_a_merge(client, session, tmp_path):
    """A run whose agent changed nothing: `git merge` would answer "Already up to date" and make no commit.
    Recording that as a merge named the person's own HEAD as ours, and Undo merge reset it away."""
    root = checkout(tmp_path / "shop")
    base = head(root)
    tree = tmp_path / "worktrees" / "RUN-1"
    agent_branch(root, tree, base, change=None)            # the agent's branch has no commit
    theirs = teammate_merge(root)                          # the person's own merge, made afterwards
    await add_run(session, "undo-lab", root, tree, base, files=0)

    merged = await client.post("/runs/RUN-1/merge")
    assert merged.status_code == 409 and merged.json()["detail"] == f"main already has every commit on {BRANCH}."
    assert (await session.get(m.Run, "r-RUN-1")).merged is None
    assert (await client.post("/runs/RUN-1/unmerge")).status_code == 409
    assert head(root) == theirs and (root / "team.py").exists()


def test_an_undo_refuses_a_merge_that_is_not_the_runs(tmp_path: Path):
    root = checkout(tmp_path / "shop")
    base = head(root)
    tree = tmp_path / "worktrees" / "RUN-1"
    agent_branch(root, tree, base, change="a = 2\n")
    theirs = teammate_merge(root)
    refusal = agent.unmerge_refusal(root, theirs[:7], into="main", tip=head(root, BRANCH))
    assert refusal and "not the merge NeuroCode made" in refusal
    with pytest.raises(agent.Refused):
        agent.take_back_merge(root, theirs[:7], into="main", tip=head(root, BRANCH))
    assert head(root) == theirs


async def test_a_source_the_run_did_not_change_does_not_block_or_corrupt_the_undo(client, session, tmp_path):
    """Two sources, both given a branch, only the first changed. The second already holds its branch, so
    nothing is merged there and nothing is taken back there; the first's merge comes out cleanly."""
    api_root = checkout(tmp_path / "api")
    web_root = checkout(tmp_path / "web")
    api_base, web_base = head(api_root), head(web_root)
    api_tree, web_tree = tmp_path / "worktrees" / "RUN-1", tmp_path / "worktrees" / "RUN-1+web"
    agent_branch(api_root, api_tree, api_base, change="a = 2\n")
    agent_branch(web_root, web_tree, web_base, change=None)
    sources = [{"label": "", "repo": str(api_root), "prefix": "", "base": api_base, "branch": BRANCH,
                "worktree": str(api_tree)},
               {"label": "web", "repo": str(web_root), "prefix": "", "base": web_base, "branch": BRANCH,
                "worktree": str(web_tree)}]
    await add_run(session, "undo-lab", api_root, api_tree, api_base, files=1, sources=sources)

    merged = await client.post("/runs/RUN-1/merge")
    assert merged.status_code == 200 and merged.json()["merged"] is True
    assert (api_root / "app.py").read_text() == "a = 2\n" and head(web_root) == web_base
    web = next(x for x in merged.json()["sources"] if x["label"] == "web")
    assert web["commit"] is None and web["nothing"] is True
    undone = await client.post("/runs/RUN-1/unmerge")
    assert undone.status_code == 200, undone.json()
    assert head(api_root) == api_base and head(web_root) == web_base


async def test_undo_does_not_reset_a_different_branch_that_happens_to_stand_on_the_merge(client, session, tmp_path):
    """The person merged into main, then started a branch from there. Undo must not rewind that branch,
    report main back and clear the merge while main still carries it."""
    root = checkout(tmp_path / "shop")
    base = head(root)
    tree = tmp_path / "worktrees" / "RUN-1"
    agent_branch(root, tree, base, change="a = 2\n")
    await add_run(session, "undo-lab", root, tree, base, files=1)

    merged = (await client.post("/runs/RUN-1/merge")).json()
    assert merged["merged"] is True and merged["into"] == "main"
    merge_commit = head(root)
    git(["checkout", "-q", "-b", "hotfix"], root)          # a new branch, standing on the merge

    undone = await client.post("/runs/RUN-1/unmerge")
    assert undone.status_code == 409 and "not on main" in undone.json()["detail"], undone.json()
    assert head(root, "main") == merge_commit and head(root, "hotfix") == merge_commit
    assert (await session.get(m.Run, "r-RUN-1")).merged is not None
    git(["checkout", "-q", "main"], root)                   # back where the merge went, it comes out
    assert (await client.post("/runs/RUN-1/unmerge")).status_code == 200
    assert head(root, "main") == base


async def test_a_run_ending_at_a_signature_is_merged_only_once_it_is_given(client, session, tmp_path):
    root = checkout(tmp_path / "shop")
    base = head(root)
    tree = tmp_path / "worktrees" / "RUN-1"
    agent_branch(root, tree, base, change="a = 2\n")
    gate = m.RunStep(n=1, kind="handoff", label="Your approval", agent="You", status="todo")
    await add_run(session, "undo-lab", root, tree, base, files=1, steps=[gate])

    unsigned = await client.post("/runs/RUN-1/merge")
    assert unsigned.status_code == 409 and "has not been accepted" in unsigned.json()["detail"]
    gate.status = "skipped"
    await session.flush()
    skipped = await client.post("/runs/RUN-1/merge")
    assert skipped.status_code == 409 and skipped.json()["detail"] == "Nothing to merge: the branch has no commits."
    assert head(root) == base
    gate.status = "done"
    await session.flush()
    assert (await client.post("/runs/RUN-1/merge")).json()["merged"] is True


# ── the runtime's own bookkeeping ────────────────────────────────
PID, PLAN = "hardened-runs", "PLAN-9771"
ORIGINAL = "def total(x):\n    return x\n"
ROUNDED = "def total(x):\n    return round(x, 2)\n"
REVIEWED = json.dumps({"findings": [], "verdict": "Reads fine."})


class ProcessDied(BaseException):
    """The process being killed, from inside a step: `execute` catches Exception, and this is not one."""


class FakeGateway:
    def __init__(self, *script: Any) -> None:
        self.script = list(script)

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def chain(self, role: str | None = None, limit: int = 20) -> list[Any]:
        return []

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Any:
        from app.ai.gateway import Provider, Result
        raw = self.script.pop(0) if self.script else '{"summary": "nothing", "files": []}'
        if isinstance(raw, BaseException):
            raise raw
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 20)


async def _forget(db: Database, pid: str) -> None:
    async with db.session() as s:
        for run in (await s.execute(select(m.Run).where(m.Run.project_id == pid))).scalars().unique():
            if Path(run.repo).is_dir():
                runtime._cleanup(run)
        await s.execute(delete(m.Approval).where(m.Approval.project_id == pid))
        await s.execute(delete(m.Run).where(m.Run.project_id == pid))
        await s.execute(delete(m.Plan).where(m.Plan.project_id == pid))
        await s.execute(delete(m.Setting).where(m.Setting.key.like(f"runtime.%.{pid}%")))
        await s.execute(delete(m.Project).where(m.Project.id == pid))


@pytest_asyncio.fixture
async def live(schema: str, tmp_path: Path) -> AsyncIterator[Database]:
    """A project with a one-step plan and tests allowed, in a database the runtime's own sessions commit to."""
    root = checkout(tmp_path / "shop", {"pkg/core.py": ORIGINAL,
                                        "Makefile": f"test:\n\t{sys.executable} -c \"print('1 passed')\"\n"})
    db = Database(url=schema)
    await _forget(db, PID)
    async with db.session() as s:
        s.add(m.Project(id=PID, name="Hardened", source_kind="local", source_repo=str(root)))
        await s.flush()
        s.add(m.Plan(id="p-hardened", ref=PLAN, project_id=PID, status="draft",
                     raw_requirement="Round the invoice total", affected_files=["pkg/core.py"]))
        await s.flush()
        s.add(m.PlanStep(id="p-hardened-1", plan_id="p-hardened", n=1, label="Fix rounding in pkg/core.py",
                         agent="Backend Engineer"))
        s.add(m.Setting(key=f"runtime.tests.{PID}", value="allowed"))
    yield db
    await _forget(db, PID)
    await db.close()


async def _new_run(db: Database, gateway: FakeGateway, plan: str = PLAN, pid: str = PID) -> str:
    async with db.session() as s:
        project = await ProjectRepository(s).get(pid)
        found = (await s.execute(select(m.Plan).where(m.Plan.ref == plan))).scalar_one()
        return (await RunService(s, gateway).plan_runs(found, None, project, "Rajat"))[-1].ref


async def test_an_adopted_only_step_still_stops_at_the_persons_signature(live: Database):
    """The step committed with the run's trailer and the process died before that was written down — the
    case carrying on adopts. The branch now holds the model's code while the run's count still said 0
    files, and a signature skipped on that count let the code finish unsigned and merge."""
    gateway = FakeGateway(ProcessDied("killed"))
    ref = await _new_run(live, gateway)
    with pytest.raises(ProcessDied):
        await execute(live, gateway, ref)
    async with live.session() as s:
        await reconcile_interrupted(s)                      # what start-up does

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
    tree = Path(run.worktree)
    (tree / "pkg" / "core.py").write_text(ROUNDED)
    git(["commit", "-qam", f"Fix rounding\n\nRounded.\n\nNeuroCode {ref}"], tree)

    async with live.session() as s:
        made = await RunService(s, gateway).carry_on(ref, "Rajat")
        resume_from = made.review["resumes"][-1]["from"]
        assert made.diff_files == 1 and made.diff_commits == 1       # measured, not the stale count
    gateway.script = [REVIEWED]
    await execute(live, gateway, ref, resume_from)

    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
        gate = await ApprovalRepository(s).waiting_on_person(ref)
    assert gate is not None and run.status == "waiting", f"run {run.status}, diff_files={run.diff_files}"
    with pytest.raises(Refused, match="has not finished"):
        async with live.session() as s:
            await RunService(s, gateway).merge(ref, "Rajat")


async def test_two_reverts_with_redo_do_not_both_start_the_run(live: Database):
    gateway = FakeGateway(json.dumps({"summary": "Rounded.", "files": [{"path": "pkg/core.py", "content": ROUNDED}]}),
                          REVIEWED)
    ref = await _new_run(live, gateway)
    await execute(live, gateway, ref)
    async with live.read() as s:
        assert (await RunRepository(s).by_ref(ref)).status == "waiting"     # at the signature

    async def press(who: str) -> str:
        try:
            async with live.session() as s:
                await RunService(s, gateway).revert(ref, 1, who, redo=True)
            return "started"
        except Refused as refused:
            return f"refused: {refused}"

    answers = await asyncio.gather(press("Rajat"), press("Asha"))
    assert answers.count("started") == 1, answers
    # And once the first has committed, a late second press reads what it wrote.
    assert (await press("Asha")).startswith(f"refused: {ref} is still working")


async def test_a_run_queued_when_the_server_stopped_is_not_left_queued_for_ever(live: Database):
    """A run is queued the moment it is handed to a job; one handed off just before a restart is owned by
    nothing, and a queued run refuses resume, revert and rework alike."""
    ref = await _new_run(live, FakeGateway())
    async with live.session() as s:                      # the restart, before the job began
        assert (await reconcile_interrupted(s))["runs"] >= 1
    async with live.read() as s:
        run = await RunRepository(s).by_ref(ref)
    assert run.status == "failed" and run.note == "interrupted: the server restarted"


async def test_a_refused_step_leaves_nothing_for_the_next_step_to_commit(schema: str, tmp_path: Path):
    """Step 1 proposes two files and the second is over the size limit, so the step is refused. Its first
    file used to be written already, and step 2's `git add -A` committed it under step 2's name."""
    pid, plan_ref = "hardened-paths", "PLAN-9772"
    root = checkout(tmp_path / "shop")
    db = Database(url=schema)
    await _forget(db, pid)
    try:
        async with db.session() as s:
            s.add(m.Project(id=pid, name="Paths", source_kind="local", source_repo=str(root)))
            await s.flush()
            s.add(m.Plan(id="p-hardened-paths", ref=plan_ref, project_id=pid, status="draft",
                         raw_requirement="two steps", affected_files=["app.py"]))
            await s.flush()
            s.add(m.PlanStep(id="p-hardened-paths-1", plan_id="p-hardened-paths", n=1, label="One",
                             agent="Backend Engineer"))
            s.add(m.PlanStep(id="p-hardened-paths-2", plan_id="p-hardened-paths", n=2, label="Two",
                             agent="Backend Engineer"))
        gateway = FakeGateway(
            json.dumps({"summary": "one", "files": [{"path": "half.py", "content": "h = 1\n"},
                                                    {"path": "big.py", "content": "x" * 300_000}]}),
            json.dumps({"summary": "two", "files": [{"path": "two.py", "content": "t = 2\n"}]}),
            json.dumps({"findings": [], "verdict": "fine"}))
        ref = await _new_run(db, gateway, plan_ref, pid)
        await execute(db, gateway, ref)
        async with db.read() as s:
            run = await RunRepository(s).by_ref(ref)
        one = next(x for x in run.steps if x.n == 1)
        assert one.status == "failed" and "larger than" in one.detail
        on_branch = git(["ls-tree", "-r", "--name-only", run.branch], root).split()
        assert "two.py" in on_branch and "half.py" not in on_branch
    finally:
        await _forget(db, pid)
        await db.close()
