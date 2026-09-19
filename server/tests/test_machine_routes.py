"""The Workbench's view of the machine, over HTTP, against real folders made by the test.

Every answer here is the filesystem's or git's, so the only honest test is a tree whose contents are
known because the test wrote them: a root with folders, hidden files, a repository, a binary file, a
symlink that stays inside and one that escapes. The roots setting is pointed at that tree, so nothing
outside a temporary folder is ever listed, read or written.
"""
from __future__ import annotations

import hashlib
import os
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

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ADMIN = {"email": "admin@example.com", "name": "Admin", "password": "another long passphrase", "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}


def run_git(args: list[str], cwd: Path) -> str:
    who = ["-c", "user.name=Rajat", "-c", "user.email=rajat@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *who, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The only root: `work/`. `elsewhere/` sits beside it, outside, holding a secret."""
    root = tmp_path / "work"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hello')\n")
    (root / "notes.txt").write_text("नमस्ते — hello\r\nsecond line\r\n", encoding="utf-8")
    (root / "Zeta").mkdir()
    (root / ".hidden").write_text("dot file\n")
    (root / "image.bin").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0rest")
    (root / "shop" / ".git").mkdir(parents=True)          # a folder that looks like a repository
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("not yours\n")
    os.symlink(outside, root / "escape")
    os.symlink(outside / "secret.txt", root / "secret-link.txt")
    os.symlink(root / "src" / "app.py", root / "app-link.py")

    configured = real_settings().model_copy(update={"machine_roots": str(root), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    return root


@pytest_asyncio.fixture
async def api(catalogued: AsyncSession):
    """The built-in roles are re-read from the catalogue first, as a server start does, so the Owner
    holds `machine:access` whatever an older run left in the test database."""
    session = catalogued
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
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


# ── roots and the boundary ────────────────────────────────────────
async def test_the_roots_are_the_configured_folders_that_exist(client: AsyncClient, tree: Path,
                                                               monkeypatch: pytest.MonkeyPatch):
    body = (await client.get("/machine/roots")).json()
    assert body == [{"path": str(tree.resolve()), "label": "work"}]

    both = real_settings().model_copy(update={"machine_roots": f"{tree}:{tree}/missing:{tree}/src"})
    monkeypatch.setattr(machine, "settings", lambda: both)
    labels = [r["label"] for r in (await client.get("/machine/roots")).json()]
    assert labels == ["work", "src"]                      # the missing one is left out, none twice


async def test_nothing_outside_the_roots_is_listed_or_read(client: AsyncClient, tree: Path):
    outside = tree.parent / "elsewhere"
    for path in (str(outside), str(tree / ".." / "elsewhere"), f"{tree}/src/../../elsewhere/secret.txt",
                 str(tree / "escape"), str(tree / "escape" / "secret.txt")):
        listed = await client.get("/machine/list", params={"path": path})
        read = await client.get("/machine/file", params={"path": path})
        assert listed.status_code == 403, path
        assert read.status_code == 403, path
        assert "outside the folders this server opens" in read.json()["detail"]
    # A link that escapes cannot be read through its own name either.
    assert (await client.get("/machine/file", params={"path": str(tree / "secret-link.txt")})).status_code == 403
    assert (await client.get("/machine/list", params={"path": "work/src"})).status_code == 400
    assert (await client.get("/machine/file", params={"path": "/etc/hosts"})).status_code == 403


# ── listing ───────────────────────────────────────────────────────
async def test_a_folder_lists_folders_first_and_hides_dot_files_unless_asked(client: AsyncClient, tree: Path):
    body = (await client.get("/machine/list", params={"path": str(tree)})).json()
    assert body["path"] == str(tree.resolve()) and body["parent"] is None and body["capped"] is False
    names = [e["name"] for e in body["entries"]]
    assert names[:4] == ["escape", "shop", "src", "Zeta"]   # folders (and a link to one) first, case folded
    assert ".hidden" not in names and body["hidden"] == 1
    by = {e["name"]: e for e in body["entries"]}
    assert by["shop"]["git"] is True and by["src"]["git"] is False and by["src"]["kind"] == "dir"
    assert by["notes.txt"]["kind"] == "file" and by["notes.txt"]["size"] == (tree / "notes.txt").stat().st_size
    assert by["notes.txt"]["modified"].endswith("+00:00")
    assert by["escape"]["kind"] == "link" and by["escape"]["linkTo"] is None       # points outside
    assert by["secret-link.txt"]["linkTo"] is None and by["secret-link.txt"]["size"] is None
    assert by["app-link.py"]["linkTo"] == "file" and by["app-link.py"]["size"] == 15

    shown = (await client.get("/machine/list", params={"path": str(tree), "hidden": True})).json()
    assert ".hidden" in [e["name"] for e in shown["entries"]] and shown["hidden"] == 0

    inner = (await client.get("/machine/list", params={"path": f"{tree}/src"})).json()
    assert inner["parent"] == str(tree.resolve())
    assert [e["name"] for e in inner["entries"]] == ["app.py"]


async def test_a_listing_says_when_it_was_capped(client: AsyncClient, tree: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(machine, "MAX_ENTRIES", 3)
    body = (await client.get("/machine/list", params={"path": str(tree)})).json()
    assert body["capped"] is True and len(body["entries"]) == 3 and body["total"] > 3
    assert (await client.get("/machine/list", params={"path": f"{tree}/nope"})).status_code == 404
    assert (await client.get("/machine/list", params={"path": f"{tree}/notes.txt"})).status_code == 409


# ── reading ───────────────────────────────────────────────────────
async def test_a_text_file_opens_with_its_sha1_and_language(client: AsyncClient, tree: Path):
    body = (await client.get("/machine/file", params={"path": f"{tree}/src/app.py"})).json()
    assert body["text"] == "print('hello')\n" and body["binary"] is False and body["reason"] is None
    assert body["language"] == "Python" and body["sha1"] == sha1(tree / "src" / "app.py")
    assert body["size"] == 15 and body["path"] == str((tree / "src" / "app.py").resolve())

    notes = (await client.get("/machine/file", params={"path": f"{tree}/notes.txt"})).json()
    assert notes["text"] == "नमस्ते — hello\r\nsecond line\r\n"                # every script, line endings kept

    linked = (await client.get("/machine/file", params={"path": f"{tree}/app-link.py"})).json()
    assert linked["path"] == str((tree / "src" / "app.py").resolve())       # a link inside opens its target


async def test_a_binary_file_comes_without_text_and_a_large_one_is_refused(client: AsyncClient, tree: Path):
    body = (await client.get("/machine/file", params={"path": f"{tree}/image.bin"})).json()
    assert body["binary"] is True and body["text"] is None and body["reason"] == "binary"
    assert body["sha1"] == sha1(tree / "image.bin")

    (tree / "latin.txt").write_bytes("café".encode("latin-1"))
    latin = (await client.get("/machine/file", params={"path": f"{tree}/latin.txt"})).json()
    assert latin["binary"] is False and latin["text"] is None and latin["reason"] == "not UTF-8"

    big = tree / "big.log"
    big.write_bytes(b"x" * (machine.MAX_TEXT + 1))
    refused = await client.get("/machine/file", params={"path": str(big)})
    assert refused.status_code == 413
    assert refused.json()["detail"] == "big.log is 2.0 MB; the editor opens files up to 2.0 MB."
    assert (await client.get("/machine/file", params={"path": f"{tree}/src"})).status_code == 409
    assert (await client.get("/machine/file", params={"path": f"{tree}/gone.py"})).status_code == 404


# ── saving ────────────────────────────────────────────────────────
async def test_a_save_lands_only_when_the_file_is_still_the_one_opened(client: AsyncClient, tree: Path,
                                                                       session: AsyncSession):
    target = tree / "src" / "app.py"
    opened = (await client.get("/machine/file", params={"path": str(target)})).json()

    saved = await client.put("/machine/file", json={"path": str(target), "text": "print('saved')\n",
                                                    "expectSha1": opened["sha1"]})
    assert saved.status_code == 200
    assert target.read_text() == "print('saved')\n"
    assert saved.json()["sha1"] == sha1(target) and saved.json()["size"] == 15

    audit = (await session.execute(select(m.AuditEntry).where(m.AuditEntry.action == "machine.file.save"))).scalars().all()
    assert len(audit) == 1 and audit[0].target == str(target.resolve())
    assert audit[0].detail == {"bytes": 15, "sha1": sha1(target), "was": opened["sha1"]}

    # Someone else writes the file; a save from the old tab is refused and changes nothing.
    target.write_text("print('theirs')\n")
    stale = await client.put("/machine/file", json={"path": str(target), "text": "print('mine')\n",
                                                    "expectSha1": saved.json()["sha1"]})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "app.py changed on disk since you opened it."
    assert target.read_text() == "print('theirs')\n"

    outside = tree.parent / "elsewhere" / "secret.txt"
    escaped = await client.put("/machine/file", json={"path": str(tree / "secret-link.txt"), "text": "x",
                                                      "expectSha1": sha1(outside)})
    assert escaped.status_code == 403 and outside.read_text() == "not yours\n"
    bad = await client.put("/machine/file", json={"path": str(target), "text": "x", "expectSha1": "nope"})
    assert bad.status_code == 422


async def test_crlf_and_every_script_survive_a_save(client: AsyncClient, tree: Path):
    target = tree / "notes.txt"
    opened = (await client.get("/machine/file", params={"path": str(target)})).json()
    text = opened["text"] + "مرحبا\r\n"
    assert (await client.put("/machine/file", json={"path": str(target), "text": text,
                                                    "expectSha1": opened["sha1"]})).status_code == 200
    assert target.read_bytes() == text.encode("utf-8")


# ── new folders and files ─────────────────────────────────────────
async def test_new_folders_and_files_appear_only_where_named(client: AsyncClient, tree: Path, session: AsyncSession):
    made = await client.post("/machine/mkdir", json={"path": f"{tree}/src/lib"})
    assert made.status_code == 201 and made.json()["kind"] == "dir" and (tree / "src" / "lib").is_dir()
    assert (await client.post("/machine/mkdir", json={"path": f"{tree}/src/lib"})).status_code == 409

    new = await client.post("/machine/new-file", json={"path": f"{tree}/src/lib/util.py"})
    assert new.status_code == 201 and new.json()["kind"] == "file" and new.json()["size"] == 0
    assert (tree / "src" / "lib" / "util.py").read_text() == ""
    assert (await client.post("/machine/new-file", json={"path": f"{tree}/src/lib/util.py"})).status_code == 409

    assert (await client.post("/machine/mkdir", json={"path": f"{tree}/src/.."})).status_code == 400
    assert (await client.post("/machine/new-file", json={"path": f"{tree}/escape/planted.txt"})).status_code == 403
    assert (await client.post("/machine/mkdir", json={"path": f"{tree}/../planted"})).status_code == 403
    assert (await client.post("/machine/new-file", json={"path": f"{tree}/missing/x.py"})).status_code == 404
    assert not (tree.parent / "elsewhere" / "planted.txt").exists() and not (tree.parent / "planted").exists()

    actions = (await session.execute(select(m.AuditEntry.action, m.AuditEntry.target)
                                     .where(m.AuditEntry.action.like("machine.%")))).all()
    assert sorted(actions) == [("machine.file.create", str((tree / "src" / "lib" / "util.py").resolve())),
                               ("machine.folder.create", str((tree / "src" / "lib").resolve()))]


# ── git ───────────────────────────────────────────────────────────
async def test_git_status_is_read_from_the_repository_holding_the_path(client: AsyncClient, tree: Path):
    remote = tree / "remote.git"
    run_git(["init", "-q", "--bare", "-b", "main", str(remote)], tree)
    repo = tree / "shop2"
    repo.mkdir()
    (repo / "app.py").write_text("a = 1\n")
    (repo / "old.py").write_text("gone soon\n")
    (repo / ".gitignore").write_text("build/\n")
    run_git(["init", "-q", "-b", "main"], repo)
    run_git(["add", "-A"], repo)
    run_git(["commit", "-qm", "first"], repo)
    run_git(["remote", "add", "origin", str(remote)], repo)
    run_git(["push", "-q", "-u", "origin", "main"], repo)
    (repo / "app.py").write_text("a = 2\n")
    run_git(["commit", "-qam", "second"], repo)                     # one ahead of origin/main
    (repo / "app.py").write_text("a = 3\n")                         # modified, not staged
    (repo / "staged.py").write_text("new\n")
    run_git(["add", "staged.py"], repo)                              # added, staged
    run_git(["mv", "old.py", "renamed.py"], repo)                    # renamed, staged
    (repo / "loose.py").write_text("untracked\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("ignored\n")

    body = (await client.get("/machine/git", params={"path": f"{repo}/app.py"})).json()
    assert body["root"] == str(repo.resolve()) and body["branch"] == "main"
    assert body["upstream"] == "origin/main" and (body["ahead"], body["behind"]) == (1, 0)
    assert len(body["head"]) == 40 and body["capped"] is False
    by = {c["path"]: c for c in body["changed"]}
    assert by["app.py"] == {"path": "app.py", "status": "M", "staged": False}
    assert by["staged.py"] == {"path": "staged.py", "status": "A", "staged": True}
    assert by["renamed.py"] == {"path": "renamed.py", "status": "R", "staged": True, "was": "old.py"}
    assert by["loose.py"]["status"] == "?"
    assert "build/out.js" not in by and "build/" not in by

    assert (await client.get("/machine/git", params={"path": f"{tree}/src"})).json() is None

    found = (await client.get("/machine/files", params={"path": str(repo)})).json()
    assert found["source"] == "git" and found["root"] == str(repo.resolve())
    assert set(found["files"]) == {".gitignore", "app.py", "staged.py", "renamed.py", "loose.py"}

    walked = (await client.get("/machine/files", params={"path": f"{tree}/src"})).json()
    assert walked == {"root": str((tree / "src").resolve()), "files": ["app.py"], "capped": False, "source": "walk"}


# ── who may, and whether the server allows it at all ──────────────
async def test_only_a_holder_of_machine_access_gets_in(api, client: AsyncClient, tree: Path):
    assert (await client.post("/admin/users", json=ADMIN)).status_code in (200, 201)
    async with _client(api) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        for call in (admin.get("/machine/roots"), admin.get("/machine/list", params={"path": str(tree)}),
                     admin.get("/machine/file", params={"path": f"{tree}/src/app.py"}),
                     admin.post("/machine/mkdir", json={"path": f"{tree}/admin-was-here"})):
            answer = await call
            assert answer.status_code == 403 and "machine:access" in answer.json()["detail"]
    assert not (tree / "admin-was-here").exists()
    async with _client(api) as stranger:
        assert (await stranger.get("/machine/roots")).status_code == 401


async def test_a_server_with_machine_access_off_does_not_have_these_routes(api, client: AsyncClient, tree: Path,
                                                                            monkeypatch: pytest.MonkeyPatch):
    off = real_settings().model_copy(update={"machine_roots": str(tree), "machine_access": False})
    monkeypatch.setattr(machine, "settings", lambda: off)
    for answer in (await client.get("/machine/roots"),
                   await client.get("/machine/list", params={"path": str(tree)}),
                   await client.put("/machine/file", json={"path": f"{tree}/src/app.py", "text": "x",
                                                           "expectSha1": sha1(tree / "src" / "app.py")})):
        assert answer.status_code == 404 and answer.json()["detail"] == "Machine access is off on this server."
    assert (tree / "src" / "app.py").read_text() == "print('hello')\n"
    async with _client(api) as stranger:
        assert (await stranger.get("/machine/roots")).status_code == 404
