"""The Git screen over HTTP, against a real repository with real worktrees.

Every number the screen shows is git's answer, so the only honest test is a repository whose history
is known because the test made it: a checkout on `main`, two agent branches that each merge cleanly
into it but collide with each other, a worktree somebody made by hand, and a run whose worktree was
deleted underneath it. The runs are rows written here rather than dispatched, because what is under
test is the reading — and the shared `.worktrees` folder the runtime uses must not be touched by a
test that can run beside others.
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.agent.git import AUTHOR
from app.api import deps
from app.api.app import create_api
from app.data.loader import load_seed, sync_roles

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
PID = "gitlab"
ONE, TWO, HAND = "neurocode/task-1", "neurocode/task-2", "hand-made"


def run_git(args: list[str], cwd: Path, *, agent: bool = False) -> str:
    who = AUTHOR if agent else ["-c", "user.name=Rajat", "-c", "user.email=rajat@example.com",
                                "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *who, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest_asyncio.fixture
async def api(session: AsyncSession):
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
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


@pytest_asyncio.fixture
async def repo(tmp_path: Path, session: AsyncSession, client: AsyncClient) -> Path:
    """The checkout, two agent worktrees that touch the same line, one made by hand, and one gone."""
    root = tmp_path / "shop"
    root.mkdir()
    (root / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
    (root / "README.md").write_text("# Shop\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    base = run_git(["rev-parse", "HEAD"], root).strip()

    for branch, ref, line in ((ONE, "RUN-1", "b = 'one'"), (TWO, "RUN-2", "b = 'two'")):
        tree = tmp_path / "worktrees" / ref
        run_git(["worktree", "add", "-q", "-b", branch, str(tree), base], root)
        (tree / "app.py").write_text(f"a = 1\n{line}\nc = 3\n")
        run_git(["commit", "-qam", f"{ref}: change b"], tree, agent=True)
    run_git(["worktree", "add", "-q", "-b", HAND, str(tmp_path / "worktrees" / "hand"), base], root)

    session.add(m.Project(id=PID, name="Git Lab", source_kind="local", source_repo=str(root)))
    await session.flush()
    for ref, branch, status, agent, files in (("RUN-1", ONE, "done", "Backend Engineer", 1),
                                              ("RUN-2", TWO, "running", "Frontend Engineer", 1),
                                              ("RUN-3", "neurocode/task-3", "done", "QA Engineer", 2)):
        session.add(m.Run(id=f"r-{ref}", ref=ref, project_id=PID, status=status, role="solo", agent=agent,
                          branch=branch, worktree=str(tmp_path / "worktrees" / ref), repo=str(root), base=base,
                          requirement=f"{ref} work", requested_by="Rajat", diff_files=files, steps=[],
                          conflicts=[]))
    session.add(m.Approval(id="ap-git", ref="APPR-900", title="Accept RUN-1", tool=f"Merge({ONE})",
                           risk="MEDIUM", status="pending", project_id=PID, run_ref="RUN-1", step=1))
    await session.flush()
    return root


async def test_the_overview_is_read_from_git_not_stored(client: AsyncClient, repo: Path):
    body = (await client.get(f"/projects/{PID}/git")).json()
    assert body["available"] is True and body["shallow"] is False
    assert body["head"]["branch"] == "main" and body["head"]["dirty"] is False
    assert len(body["head"]["sha"]) == 40

    by_id = {w["id"]: w for w in body["worktrees"]}
    one = by_id["RUN-1"]
    assert one["branch"] == ONE and one["madeBy"] == "neurocode" and one["agent"] == "Backend Engineer"
    assert (one["status"], one["ahead"], one["behind"]) == ("ahead", 1, 0)
    assert (one["filesChanged"], one["additions"], one["deletions"]) == (1, 1, 1)
    assert one["lastCommit"] == "RUN-1: change b" and one["canMerge"] is True and one["mergeBlocked"] is None

    assert by_id["RUN-2"]["canMerge"] is False
    assert by_id["RUN-2"]["mergeBlocked"] == "RUN-2 has not finished, so there is nothing settled to merge."
    gone = by_id["RUN-3"]
    assert gone["gone"] is True and gone["canMerge"] is False and "no longer exists" in gone["mergeBlocked"]

    hand = next(w for w in body["worktrees"] if w["madeBy"] == "git")
    assert hand["branch"] == HAND and hand["agent"] == "—" and hand["runRef"] is None
    assert hand["id"].startswith("wt:") and hand["status"] == "clean"
    assert hand["mergeBlocked"] == "Made outside NeuroCode — merge it with git."

    previews = {p["branch"]: p for p in body["mergePreview"]}
    assert set(previews) == {ONE, TWO}                    # only branches with something to bring
    assert previews[ONE]["result"] == "clean" and previews[ONE]["target"] == "main"
    # Each merges cleanly on its own, and they still collide with each other — merge-tree says so.
    assert previews[ONE]["collidesWith"] == TWO and previews[TWO]["collidesWith"] == ONE

    assert body["stats"] == {"worktrees": 4, "dirty": 0, "clean": 2, "collisions": 0, "commitsToday": 3,
                             "agentCommitsToday": 2, "awaitingYou": 1}


async def test_merge_is_offered_exactly_when_the_merge_route_would_accept_it(client: AsyncClient, repo: Path,
                                                                           session: AsyncSession):
    (repo / "notes.txt").write_text("not committed\n")
    body = (await client.get(f"/projects/{PID}/git")).json()
    assert body["head"]["dirty"] is True
    one = next(w for w in body["worktrees"] if w["id"] == "RUN-1")
    assert one["canMerge"] is False
    refused = await client.post("/runs/RUN-1/merge")
    assert refused.status_code == 409 and refused.json()["detail"] == one["mergeBlocked"]

    # A run that changed nothing: the merge would be "Already up to date", recorded as a merge of nothing.
    (repo / "notes.txt").unlink()
    run = await session.get(m.Run, "r-RUN-1")
    run.diff_files = 0
    await session.flush()
    one = next(w for w in (await client.get(f"/projects/{PID}/git")).json()["worktrees"] if w["id"] == "RUN-1")
    assert one["canMerge"] is False and one["mergeBlocked"] == "Nothing to merge: the branch has no commits."


async def test_a_branch_that_collides_with_the_checkout_is_named_with_its_file(client: AsyncClient, repo: Path):
    (repo / "app.py").write_text("a = 1\nb = 'main'\nc = 3\n")
    run_git(["commit", "-qam", "main moves b"], repo)
    body = (await client.get(f"/projects/{PID}/git")).json()
    preview = next(p for p in body["mergePreview"] if p["branch"] == ONE)
    assert preview["result"] == "collides" and preview["onFile"] == "app.py" and preview["files"] == 1
    assert next(w for w in body["worktrees"] if w["id"] == "RUN-1")["status"] == "conflict"
    assert body["stats"]["collisions"] == 2


async def test_a_diff_is_git_s_and_a_branch_must_be_one_git_lists(client: AsyncClient, repo: Path):
    body = (await client.get(f"/projects/{PID}/git/diff", params={"branch": ONE})).json()
    assert body["branch"] == ONE and len(body["against"]) == 7 and body["truncated"] is False
    [changed] = body["files"]
    assert (changed["path"], changed["change"], changed["additions"], changed["deletions"]) == ("app.py", "M", 1, 1)
    assert "+b = 'one'" in changed["hunk"] and changed["hunk"].startswith("@@")

    against_head = (await client.get(f"/projects/{PID}/git/diff", params={"branch": ONE, "against": "head"})).json()
    assert against_head["against"] == "main" and [f["path"] for f in against_head["files"]] == ["app.py"]

    for hostile in ("--output=/tmp/owned", "main..HEAD", "nope"):
        assert (await client.get(f"/projects/{PID}/git/diff", params={"branch": hostile})).status_code == 404
    assert (await client.get(f"/projects/{PID}/git/diff", params={"branch": ONE, "against": "x"})).status_code == 422


async def test_conflicts_open_the_collision_down_to_both_sides(client: AsyncClient, repo: Path):
    found = (await client.get(f"/projects/{PID}/git/conflicts")).json()
    [conflict] = found
    assert conflict["file"] == "app.py" and conflict["taskRef"] == "RUN-1 ✕ RUN-2"
    assert conflict["region"] == "L2 – L6" and conflict["resolvedBy"] == "unresolved"
    ours, theirs = conflict["hunks"]
    assert (ours["side"], ours["branch"], ours["agent"], ours["lines"]) == ("ours", ONE, "Backend Engineer", "b = 'one'")
    assert (theirs["branch"], theirs["agent"], theirs["lines"]) == (TWO, "Frontend Engineer", "b = 'two'")
    assert len(ours["commit"]) >= 7 and ours["at"]
    assert conflict["policy"] and all(isinstance(rule, str) for rule in conflict["policy"])


async def test_commits_say_which_run_made_them(client: AsyncClient, repo: Path):
    body = (await client.get(f"/projects/{PID}/git/commits")).json()
    assert body["shallow"] is False
    by_message = {c["message"]: c for c in body["commits"]}
    assert by_message["RUN-1: change b"]["author"] == "Backend Engineer"
    assert by_message["RUN-1: change b"]["branch"] == ONE and by_message["RUN-1: change b"]["files"] == 1
    assert by_message["first"]["author"] == "Rajat" and by_message["first"]["branch"] == "main"
    assert len((await client.get(f"/projects/{PID}/git/commits", params={"limit": 1})).json()["commits"]) == 1


async def test_a_sample_project_answers_honestly_and_reading_needs_a_session(api, client: AsyncClient):
    body = (await client.get("/projects/erp/git")).json()
    assert body["available"] is False and "sample project" in body["reason"] and body["worktrees"] == []
    refused = await client.get("/projects/erp/git/commits")
    assert refused.status_code == 409 and "sample project" in refused.json()["detail"]
    assert (await client.get("/projects/nope/git")).status_code == 404

    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/projects/erp/git")).status_code == 200
        assert (await viewer.post("/runs/RUN-1/merge")).status_code == 403
    async with _client(api) as stranger:
        assert (await stranger.get("/projects/erp/git")).status_code == 401


async def test_a_recorded_collision_whose_branch_is_gone_keeps_its_files(client: AsyncClient, repo: Path,
                                                                         session: AsyncSession, tmp_path: Path):
    session.add(m.Run(id="r-RUN-4", ref="RUN-4", project_id=PID, status="failed", role="integration",
                      branch="neurocode/merge", worktree=str(tmp_path / "worktrees" / "RUN-4"), repo=str(repo),
                      steps=[], conflicts=[m.RunConflict(branch="neurocode/task-9", agent="QA Engineer",
                                                         files=["app.py", "README.md"])]))
    await session.flush()
    recorded = [c for c in (await client.get(f"/projects/{PID}/git/conflicts")).json() if c["taskRef"].startswith("RUN-4")]
    assert [c["file"] for c in recorded] == ["app.py", "README.md"]
    assert all(c["hunks"] == [] and c["resolution"] == "branch removed" for c in recorded)
    overview = (await client.get(f"/projects/{PID}/git")).json()
    assert next(w for w in overview["worktrees"] if w["id"] == "RUN-4")["gone"] is True
