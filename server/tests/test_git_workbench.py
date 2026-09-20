"""The Workbench's own git actions, against a real checkout a person has edited.

The Workbench saves straight into the project's working tree, and a working tree with changes that
are not committed refuses every merge. These are the three routes that let a person deal with that
without leaving for a terminal: read what one file changed, commit the files they picked, or put
files back the way the last commit had them.

Everything here is git's real answer in a repository this test made, and the merge it unblocks is
the real `/runs/{ref}/merge` refusal, so the test fails if the two ever stop meaning the same thing.
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.services import machine
from app.settings import settings as real_settings
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
PID = "bench"


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest_asyncio.fixture
async def api(session: AsyncSession):
    await load_workspace(session)
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


@pytest_asyncio.fixture
async def client(api) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest_asyncio.fixture
async def repo(tmp_path: Path, session: AsyncSession, client: AsyncClient,
               monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project's checkout with one committed file, one edited since, and one git has never seen."""
    root = tmp_path / "shop"
    root.mkdir()
    (root / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
    (root / "README.md").write_text("# Shop\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["config", "user.name", "Rajat"], root)
    run_git(["config", "user.email", "rajat@example.com"], root)
    run_git(["config", "commit.gpgsign", "false"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)

    (root / "app.py").write_text("a = 1\nb = 22\nc = 3\n")          # edited in the Workbench
    (root / "notes.md").write_text("thinking out loud\n")           # made in the Workbench, never committed

    configured = real_settings().model_copy(update={"machine_roots": str(tmp_path), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    session.add(m.Project(id=PID, name="Shop", source_kind="local", source_repo=str(root)))
    await session.flush()
    return root


async def audit_actions(session: AsyncSession) -> list[str]:
    return list((await session.execute(select(m.AuditEntry.action))).scalars())


async def test_a_file_s_diff_is_git_s_including_one_git_has_never_seen(client: AsyncClient, repo: Path):
    changed = (await client.get(f"/projects/{PID}/git/file-diff", params={"path": "app.py"})).json()
    assert (changed["change"], changed["tracked"]) == ("M", True)
    assert (changed["additions"], changed["deletions"]) == (1, 1)
    assert "+b = 22" in changed["patch"] and "-b = 2" in changed["patch"]
    assert changed["truncated"] is False

    new = (await client.get(f"/projects/{PID}/git/file-diff", params={"path": "notes.md"})).json()
    assert (new["change"], new["tracked"], new["additions"], new["deletions"]) == ("?", False, 1, 0)
    assert "+thinking out loud" in new["patch"]

    untouched = (await client.get(f"/projects/{PID}/git/file-diff", params={"path": "README.md"})).json()
    assert untouched["patch"] == "" and untouched["change"] == ""

    for outside in ("../secrets.env", "/etc/passwd", ".git/config"):
        refused = await client.get(f"/projects/{PID}/git/file-diff", params={"path": outside})
        assert refused.status_code in (403, 409), outside

    # A link inside the repository that leads out of the machine's roots is the same refusal: the
    # second fence resolves where a path really goes, not where it is written.
    (repo / "elsewhere").symlink_to("/etc")
    escaped = await client.get(f"/projects/{PID}/git/file-diff", params={"path": "elsewhere/hosts"})
    assert escaped.status_code == 403 and "outside the folders this server opens" in escaped.json()["detail"]


async def test_committing_one_file_unblocks_the_merge_and_leaves_the_other_change_alone(
        client: AsyncClient, repo: Path, session: AsyncSession):
    # The product's own words for why a merge cannot happen, read from the Git screen.
    dirty = (await client.get(f"/projects/{PID}/git")).json()
    assert dirty["head"]["dirty"] is True

    done = await client.post(f"/projects/{PID}/git/commit",
                             json={"paths": ["app.py"], "message": "fix the total"})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["files"] == 1 and body["branch"] == "main" and body["message"] == "fix the total"
    assert body["by"] == "Rajat <rajat@example.com>"
    assert run_git(["log", "-1", "--format=%s%n%an"], repo).split("\n")[:2] == ["fix the total", "Rajat"]
    # Only the file named: the one nobody committed is still there, and still untracked.
    assert (repo / "notes.md").read_text() == "thinking out loud\n"
    assert run_git(["status", "--porcelain"], repo).strip() == "?? notes.md"
    assert "git.commit" in await audit_actions(session)

    # An untracked file leaves the tree dirty, so the merge is still refused — in words that say so.
    assert (await client.get(f"/projects/{PID}/git")).json()["head"]["dirty"] is True
    (repo / "notes.md").unlink()
    after = (await client.get(f"/projects/{PID}/git")).json()
    assert after["head"]["dirty"] is False


async def test_a_commit_with_nothing_in_it_is_refused_in_words(client: AsyncClient, repo: Path):
    refused = await client.post(f"/projects/{PID}/git/commit",
                                json={"paths": ["README.md"], "message": "nothing"})
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Those files hold nothing that is not already committed."
    blank = await client.post(f"/projects/{PID}/git/commit", json={"paths": ["app.py"], "message": "   "})
    assert blank.status_code == 409
    assert blank.json()["detail"] == "A commit needs a message saying what changed."
    assert (await client.post(f"/projects/{PID}/git/commit",
                              json={"paths": [], "message": "nothing named"})).status_code == 422


async def test_git_with_no_name_to_commit_with_says_how_to_set_one(client: AsyncClient, repo: Path,
                                                                   monkeypatch: pytest.MonkeyPatch):
    run_git(["config", "--unset", "user.name"], repo)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(repo / "nothing-here"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(repo / "nothing-here"))
    refused = await client.post(f"/projects/{PID}/git/commit",
                                json={"paths": ["app.py"], "message": "fix the total"})
    assert refused.status_code == 409 and "git config --global user.name" in refused.json()["detail"]
    assert run_git(["log", "-1", "--format=%s"], repo).strip() == "first"


async def test_discard_puts_a_file_back_and_never_deletes_one_git_has_no_copy_of(
        client: AsyncClient, repo: Path, session: AsyncSession):
    done = await client.post(f"/projects/{PID}/git/discard", json={"paths": ["app.py"]})
    assert done.status_code == 200 and done.json()["discarded"] == ["app.py"]
    assert (repo / "app.py").read_text() == "a = 1\nb = 2\nc = 3\n"
    assert "git.discard" in await audit_actions(session)

    refused = await client.post(f"/projects/{PID}/git/discard", json={"paths": ["notes.md"]})
    assert refused.status_code == 409
    assert "not in the last commit" in refused.json()["detail"]
    assert (repo / "notes.md").exists()                    # the only copy of it is still there


async def test_these_are_the_machine_s_own_door(api, client: AsyncClient, repo: Path,
                                                monkeypatch: pytest.MonkeyPatch):
    await client.post("/admin/users", json=VIEWER)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        denied = await viewer.post(f"/projects/{PID}/git/commit", json={"paths": ["app.py"], "message": "mine"})
        assert denied.status_code == 403 and "machine:access" in denied.json()["detail"]
        read = await viewer.get(f"/projects/{PID}/git/file-diff", params={"path": "app.py"})
        assert read.status_code == 403 and "machine:access" in read.json()["detail"]

    off = real_settings().model_copy(update={"machine_roots": str(repo.parent), "machine_access": False})
    monkeypatch.setattr(machine, "settings", lambda: off)
    for call in (client.get(f"/projects/{PID}/git/file-diff", params={"path": "app.py"}),
                 client.post(f"/projects/{PID}/git/commit", json={"paths": ["app.py"], "message": "x"}),
                 client.post(f"/projects/{PID}/git/discard", json={"paths": ["app.py"]})):
        assert (await call).status_code == 404
    # And to somebody who is not signed in at all: the setting is weighed before the session, so a
    # server with machine access off never admits that these routes are there.
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as nobody:
        assert (await nobody.get(f"/projects/{PID}/git/file-diff", params={"path": "app.py"})).status_code == 404
    # Reading the screen itself is untouched: it needs only a session, as it always has.
    assert (await client.get(f"/projects/{PID}/git")).status_code == 200
