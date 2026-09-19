"""Pushing an accepted run's branch, against a real bare repository standing in for the remote.

Nothing reaches the network: the remote is a bare repository in the test's own folder. For the compare
link the remote's fetch URL names GitHub while its push URL is that folder — git pushes where the push
URL says, and the link is built from the URL a person would recognise.
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent
from app import models as m
from app.agent.git import _push_refusal
from app.api import deps, routes_runs
from app.api.app import create_api
from app.services import runs as runtime
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
PID, BRANCH = "push-lab", "neurocode/task-8001"


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


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
async def lab(session: AsyncSession, client: AsyncClient, tmp_path: Path) -> dict[str, Any]:
    """A checkout, a bare remote, and an accepted run whose branch was reviewed exactly as it stands."""
    root, remote = tmp_path / "shop", tmp_path / "remote.git"
    root.mkdir()
    (root / "core.py").write_text("def total(x):\n    return x\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    run_git(["init", "-q", "--bare", str(remote)], tmp_path)
    base = run_git(["rev-parse", "HEAD"], root).strip()
    tree = tmp_path / "worktrees" / "RUN-8001"
    run_git(["worktree", "add", "-q", "-b", BRANCH, str(tree), base], root)
    (tree / "core.py").write_text("def total(x):\n    return round(x, 2)\n")
    run_git(["commit", "-qam", "round"], tree)
    patch, head = agent.branch_diff(root, base, BRANCH)

    session.add(m.Project(id=PID, name="Push Lab", source_kind="local", source_repo=str(root)))
    await session.flush()
    session.add(m.Run(
        id="r-RUN-8001", ref="RUN-8001", project_id=PID, status="done", role="solo", branch=BRANCH,
        worktree=str(tree), repo=str(root), base=base, requirement="Round the total", requested_by="Rajat",
        diff_files=1, diff_commits=1,
        review={"findings": [], "verdict": "Fine.", "by": "mistral-small",
                "receipt": {"sha256": agent.fingerprint(patch), "head": head, "base": base, "by": "mistral-small"}},
        steps=[m.RunStep(n=1, kind="edit", label="Round", status="done"),
               m.RunStep(n=2, kind="review", label="Review the diff", status="done"),
               m.RunStep(n=3, kind="handoff", label="Your approval", status="done")],
        conflicts=[]))
    await session.flush()
    return {"root": root, "remote": remote, "tree": tree, "head": head}


def remote_head(remote: Path, branch: str = BRANCH) -> str:
    out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=remote,
                         capture_output=True, text=True)
    return out.stdout.strip()


# ── the blocking half ────────────────────────────────────────────
def test_a_compare_link_is_built_only_for_the_forges_that_open_pull_requests():
    github = agent.compare_url("git@github.com:acme/shop.git", "main", BRANCH)
    assert github == "https://github.com/acme/shop/compare/main...neurocode/task-8001?expand=1"
    # Credentials written into a URL never reach the link.
    assert agent.compare_url("https://x-token:ghp_secret@github.com/acme/shop.git", None, BRANCH) == \
        "https://github.com/acme/shop/compare/neurocode/task-8001?expand=1"
    assert agent.compare_url("https://gitlab.com/group/sub/shop.git", "main", BRANCH) == (
        "https://gitlab.com/group/sub/shop/-/merge_requests/new?merge_request%5Bsource_branch%5D="
        "neurocode%2Ftask-8001&merge_request%5Btarget_branch%5D=main")
    assert agent.compare_url("git@bitbucket.org:team/shop.git", "main", BRANCH) == \
        "https://bitbucket.org/team/shop/pull-requests/new?source=neurocode%2Ftask-8001&dest=main"
    for other in ("/srv/git/shop.git", "file:///srv/shop.git", "https://git.example.com/acme/shop.git",
                  "https://github.com/acme"):
        assert agent.compare_url(other, "main", BRANCH) is None


def test_a_failed_push_is_said_in_words_a_person_can_act_on():
    assert "refused your credentials" in _push_refusal(
        "remote: Permission to acme/shop.git denied to rajat.\nfatal: unable to access: 403", "origin", BRANCH)
    assert "never force-pushes" in _push_refusal(
        " ! [rejected]  neurocode/task-8001 -> neurocode/task-8001 (fetch first)", "origin", BRANCH)
    assert "could not be reached" in _push_refusal("fatal: Could not resolve host: github.com", "origin", BRANCH)


def test_a_push_needs_a_remote_and_a_branch_that_exists(tmp_path: Path):
    root = tmp_path / "alone"
    root.mkdir()
    (root / "a.txt").write_text("a\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    with pytest.raises(agent.Refused, match="no remote to push to"):
        agent.push(root, "main")
    run_git(["remote", "add", "upstream", str(tmp_path / "nowhere.git")], root)
    run_git(["remote", "add", "fork", str(tmp_path / "nowhere.git")], root)
    with pytest.raises(agent.Refused, match="none is called origin"):
        agent.push(root, "main")
    with pytest.raises(agent.Refused, match="no remote called origin"):
        agent.push(root, "main", "origin")
    with pytest.raises(agent.Refused, match="no longer exists"):
        agent.push(root, "neurocode/gone", "fork")
    with pytest.raises(agent.Refused, match="not a branch"):
        agent.push(root, "--mirror", "fork")


# ── over HTTP ────────────────────────────────────────────────────
async def test_pushing_an_accepted_run_sends_its_branch_and_keeps_the_compare_link(
        client: AsyncClient, lab: dict[str, Any], session: AsyncSession):
    run_git(["remote", "add", "origin", "git@github.com:acme/shop.git"], lab["root"])
    run_git(["config", "remote.origin.pushurl", str(lab["remote"])], lab["root"])

    sent = await client.post("/runs/RUN-8001/push", json={})
    assert sent.status_code == 200, sent.text
    pushed = sent.json()["pushed"]
    assert remote_head(lab["remote"]) == lab["head"] == pushed["sha"]
    assert pushed["remote"] == "origin" and pushed["branch"] == BRANCH and pushed["by"] == "Rajat" and pushed["at"]
    assert pushed["compareUrl"] == "https://github.com/acme/shop/compare/main...neurocode/task-8001?expand=1"
    assert remote_head(lab["remote"], "main") == ""          # the run's own branch, and nothing else

    audit = (await session.execute(select(m.AuditEntry).where(m.AuditEntry.action == "run.push"))).scalars().all()
    assert [a.target for a in audit] == [f"{BRANCH} → origin"]
    line = next(e for e in (await client.get("/activity")).json() if e["action"] == "Branch pushed")
    assert "RUN-8001" in line["detail"] and lab["head"][:7] in line["detail"]

    # Pushing again with nothing new is harmless, and a remote on disk gets no link.
    run_git(["remote", "add", "backup", str(lab["remote"])], lab["root"])
    again = (await client.post("/runs/RUN-8001/push", json={"remote": "backup"})).json()["pushed"]
    assert again["remote"] == "backup" and again["compareUrl"] is None


async def test_a_push_is_refused_until_the_run_is_accepted_and_while_its_branch_is_not_what_was_reviewed(
        client: AsyncClient, lab: dict[str, Any], session: AsyncSession):
    run_git(["remote", "add", "origin", str(lab["remote"])], lab["root"])
    run = await session.get(m.Run, "r-RUN-8001")
    gate = next(x for x in run.steps if x.kind == "handoff")
    gate.status, run.status = "waiting", "waiting"
    await session.flush()
    refused = await client.post("/runs/RUN-8001/push")
    assert refused.status_code == 409 and "has not been accepted" in refused.json()["detail"]
    gate.status, run.status = "done", "done"
    await session.flush()

    (lab["tree"] / "core.py").write_text("def total(x):\n    return x  # after the review\n")
    run_git(["commit", "-qam", "after the review"], lab["tree"])
    refused = await client.post("/runs/RUN-8001/push")
    assert refused.status_code == 409 and "changed since it was reviewed" in refused.json()["detail"]
    merge = await client.post("/runs/RUN-8001/merge")
    assert merge.status_code == 409 and merge.json()["detail"] == refused.json()["detail"].replace("push", "merge")
    assert remote_head(lab["remote"]) == ""
    assert (await client.post("/runs/RUN-0/push")).status_code == 404


async def test_a_remote_branch_that_moved_is_never_overwritten(client: AsyncClient, lab: dict[str, Any],
                                                             tmp_path: Path):
    run_git(["remote", "add", "origin", str(lab["remote"])], lab["root"])
    other = tmp_path / "someone-else"
    run_git(["clone", "-q", str(lab["remote"]), str(other)], tmp_path)
    (other / "theirs.txt").write_text("theirs\n")
    run_git(["checkout", "-q", "-b", BRANCH], other)
    run_git(["add", "-A"], other)
    run_git(["commit", "-qm", "theirs"], other)
    run_git(["push", "-q", "origin", BRANCH], other)
    theirs = remote_head(lab["remote"])

    refused = await client.post("/runs/RUN-8001/push")
    assert refused.status_code == 409 and "never force-pushes" in refused.json()["detail"]
    assert remote_head(lab["remote"]) == theirs
    logs = (await client.get("/runs/RUN-8001")).json()["logs"]
    assert any(line["line"].startswith("push refused") for line in logs)


async def test_pushing_needs_runs_merge(api: FastAPI, client: AsyncClient, lab: dict[str, Any]):
    run_git(["remote", "add", "origin", str(lab["remote"])], lab["root"])
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.post("/runs/RUN-8001/push")).status_code == 403
    async with _client(api) as stranger:
        assert (await stranger.post("/runs/RUN-8001/push")).status_code == 401
    assert remote_head(lab["remote"]) == ""


# ── reading the review again ─────────────────────────────────────
async def test_asking_for_the_review_again_marks_it_and_hands_the_reading_to_the_background(
        api: FastAPI, client: AsyncClient, lab: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
    started: list[tuple[Any, ...]] = []

    async def record(open_session: AsyncSession, jobs: Any, job: Any, *args: Any) -> None:
        started.append((job, *args))

    monkeypatch.setattr(routes_runs, "hand_off", record)
    asked = await client.post("/runs/RUN-8001/review")
    assert asked.status_code == 200, asked.text
    step = next(s for s in asked.json()["steps"] if s["kind"] == "review")
    assert step["status"] == "running" and "asked by Rajat" in step["detail"]
    assert "reviewing" not in asked.json()["review"]
    [(job, _db, _gw, ref, n, by)] = started
    assert job is runtime.reread and (ref, n, by) == ("RUN-8001", 2, "Rajat")

    again = await client.post("/runs/RUN-8001/review")
    assert again.status_code == 409 and "already asked for it to be read again" in again.json()["detail"]
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.post("/runs/RUN-8001/review")).status_code == 403
    assert (await client.post("/runs/RUN-0/review")).status_code == 404
    assert len(started) == 1
