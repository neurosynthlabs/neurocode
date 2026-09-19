"""Projects from anywhere on the machine: an archive lying on the Desktop, one the browser uploads, an
empty folder started from nothing — and what macOS says when it guards a folder.

Every archive here is built by the test, byte for byte, so each guard is shown refusing exactly the entry
it exists for: a name that climbs out, an absolute name, a link, a bomb, too many entries, too many
bytes, and a folder that already exists. The routes run over HTTP inside a rolled-back transaction with
the background job recorded; the job itself — unpack, then the ordinary onboarding — runs for real once,
against the test database.
"""
from __future__ import annotations

import errno
import io
import json
import os
import stat
import subprocess
import tarfile
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Provider, Result
from app.api import deps, routes_machine
from app.api.app import create_api
from app.data.engine import Database
from app.services import archive as archives
from app.services import machine
from app.services.errors import Refused
from app.services.onboarding import Spec
from app.settings import settings as real_settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ADMIN = {"email": "admin@example.com", "name": "Admin", "password": "another long passphrase", "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}

SHOP = {"shop-main/README.md": "# Shop\nThe storefront.\n",
        "shop-main/app/main.py": "def charge(amount):\n    return amount\n",
        "shop-main/app/util.py": "def half(x):\n    return x / 2\n"}


def make_zip(path: Path, files: dict[str, str | bytes], *, links: dict[str, str] | None = None,
             executable: tuple[str, ...] = ()) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o100755 if name in executable else 0o100644) << 16)
            zf.writestr(info, body)
        for name, target in (links or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return path


def make_tgz(path: Path, files: dict[str, str], *, symlink: tuple[str, str] | None = None) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink[0])
            info.type = tarfile.SYMTYPE
            info.linkname = symlink[1]
            tf.addfile(info)
    return path


# ── the guards, one by one ───────────────────────────────────────
def test_a_clean_archive_is_surveyed_and_its_top_folder_dropped(tmp_path: Path):
    found = archives.survey(make_zip(tmp_path / "shop-main.zip", SHOP), "zip")
    assert found.files == 3 and found.top == "shop-main"
    assert sorted(found.placed(e) for e in found.entries) == ["README.md", "app/main.py", "app/util.py"]
    assert found.total == sum(len(v) for v in SHOP.values())

    target = tmp_path / "shop"
    target.mkdir()
    done = archives.extract(tmp_path / "shop-main.zip", target, found)
    assert (done.files, done.top) == (3, "shop-main")
    assert (target / "app" / "main.py").read_text() == SHOP["shop-main/app/main.py"]
    assert not (target / "shop-main").exists()                      # the single top folder was dropped

    # Two folders at the top: nothing is dropped. Finder's __MACOSX shadow is left out, not counted.
    two = archives.survey(make_zip(tmp_path / "two.zip", {"a/x.py": "x", "b/y.py": "y", "__MACOSX/a/._x.py": "fork"}),
                          "zip")
    assert two.top is None and sorted(e.name for e in two.entries) == ["a/x.py", "b/y.py"]


def test_an_executable_keeps_its_bit_and_a_tarball_unpacks_the_same(tmp_path: Path):
    zipped = make_zip(tmp_path / "tool.zip", {"tool/run.sh": "#!/bin/sh\necho hi\n", "tool/a.txt": "a"},
                      executable=("tool/run.sh",))
    found = archives.survey(zipped, "zip")
    (tmp_path / "out").mkdir()
    archives.extract(zipped, tmp_path / "out", found)
    assert os.stat(tmp_path / "out" / "run.sh").st_mode & 0o111
    assert not os.stat(tmp_path / "out" / "a.txt").st_mode & 0o111

    tgz = make_tgz(tmp_path / "shop.tgz", SHOP)
    got = archives.survey(tgz, "tar")
    (tmp_path / "t").mkdir()
    archives.extract(tgz, tmp_path / "t", got)
    assert (tmp_path / "t" / "app" / "util.py").read_text() == SHOP["shop-main/app/util.py"]


@pytest.mark.parametrize("name", ["../evil.py", "shop/../../evil.py", "a/../../b.py", "..\\evil.py"])
def test_zip_slip_is_refused_before_anything_is_written(tmp_path: Path, name: str):
    bad = make_zip(tmp_path / "slip.zip", {"ok.py": "fine", name: "owned"})
    with pytest.raises(Refused) as refused:
        archives.survey(bad, "zip")
    assert refused.value.status == 422 and "'..'" in str(refused.value)
    assert not (tmp_path / "evil.py").exists() and not (tmp_path.parent / "evil.py").exists()


@pytest.mark.parametrize("name", ["/etc/cron.d/evil", "C:/Windows/evil.dll", "\\\\server\\share\\x"])
def test_an_absolute_name_is_refused(tmp_path: Path, name: str):
    with pytest.raises(Refused) as refused:
        archives.survey(make_zip(tmp_path / "abs.zip", {"ok.py": "fine", name: "x"}), "zip")
    assert "absolute path" in str(refused.value)


def test_a_link_in_either_kind_of_archive_is_refused(tmp_path: Path):
    zipped = make_zip(tmp_path / "link.zip", {"a.py": "a"}, links={"escape": "/etc"})
    with pytest.raises(Refused) as refused:
        archives.survey(zipped, "zip")
    assert "is a link" in str(refused.value)
    tgz = make_tgz(tmp_path / "link.tgz", {"a.py": "a"}, symlink=("home", "../../.."))
    with pytest.raises(Refused, match="is a link"):
        archives.survey(tgz, "tar")


def test_a_bomb_is_refused_by_its_ratio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bomb = make_zip(tmp_path / "bomb.zip", {"zeros.bin": b"\0" * (4 * 1024 * 1024)})
    assert bomb.stat().st_size < 64 * 1024                    # four megabytes of zeros packs to almost nothing
    archives.survey(bomb, "zip")                               # under the floor: harmless, and let through
    monkeypatch.setattr(archives, "RATIO_FLOOR", 1024 * 1024)
    with pytest.raises(Refused) as refused:
        archives.survey(bomb, "zip")
    assert "archive bomb" in str(refused.value) and refused.value.status == 422


def test_the_entry_count_and_the_unpacked_size_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    many = make_zip(tmp_path / "many.zip", {f"f{n}.txt": "x" for n in range(12)})
    monkeypatch.setattr(archives, "MAX_ENTRIES", 10)
    with pytest.raises(Refused) as refused:
        archives.survey(many, "zip")
    assert refused.value.status == 413 and "more than 10 entries" in str(refused.value)

    monkeypatch.setattr(archives, "MAX_ENTRIES", 50_000)
    monkeypatch.setattr(archives, "MAX_TOTAL", 1000)
    big = make_zip(tmp_path / "big.zip", {"a.txt": "a" * 600, "b.txt": "b" * 600})
    with pytest.raises(Refused) as refused:
        archives.survey(big, "zip")
    assert refused.value.status == 413 and "unpacks to more than" in str(refused.value)


def test_a_twice_named_entry_and_an_empty_archive_are_refused(tmp_path: Path):
    with pytest.raises(Refused, match="twice"):
        archives.survey(make_zip(tmp_path / "dup.zip", {"a.py": "1", "./a.py": "2"}), "zip")
    with pytest.raises(Refused, match="holds no files"):
        archives.survey(make_zip(tmp_path / "none.zip", {"__MACOSX/._x": "fork"}), "zip")
    (tmp_path / "junk.zip").write_bytes(b"not a zip at all")
    with pytest.raises(Refused, match="not a readable zip"):
        archives.survey(tmp_path / "junk.zip", "zip")


# ── the routes ───────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The only root: a pretend home with a Desktop holding a zip, and a code folder to extract into."""
    root = tmp_path / "home"
    (root / "Desktop").mkdir(parents=True)
    (root / "code").mkdir()
    make_zip(root / "Desktop" / "shop-main.zip", SHOP)
    (root / "Desktop" / "notes.txt").write_text("not an archive\n")
    configured = real_settings().model_copy(update={"machine_roots": str(root), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    return root


@pytest_asyncio.fixture
async def api(catalogued: AsyncSession):
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield catalogued

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: Any) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    """The jobs the routes hand off, recorded instead of run."""
    seen: list[tuple[str, tuple[Any, ...]]] = []

    async def unpacked(_db, _gw, pid, archive, target, surveyed, spec, uploaded):
        seen.append(("import", (pid, Path(archive), Path(target), surveyed.files, spec.repo, uploaded)))

    async def onboarded(_db, _gw, pid, spec):
        seen.append(("onboard", (pid, spec.repo)))

    monkeypatch.setattr(archives, "import_archive", unpacked)
    monkeypatch.setattr(routes_machine, "onboard", onboarded)
    return seen


async def test_the_picker_offers_the_quick_places_under_the_roots(client: AsyncClient, home: Path,
                                                                  monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(home))
    # Only the Desktop exists in this home: Downloads and Documents are left out, never invented.
    assert (await client.get("/machine/places")).json() == [{"path": str((home / "Desktop").resolve()),
                                                            "label": "Desktop"}]
    (home / "Downloads").mkdir()
    assert [p["label"] for p in (await client.get("/machine/places")).json()] == ["Desktop", "Downloads"]


async def test_an_archive_is_surveyed_before_anything_is_written(client: AsyncClient, home: Path):
    shown = await client.get("/machine/archive", params={"path": str(home / "Desktop" / "shop-main.zip")})
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert (body["kind"], body["files"], body["top"], body["suggestedName"]) == ("zip", 3, "shop-main", "shop-main")
    assert "app/main.py" in body["sample"] and body["total"] == sum(len(v) for v in SHOP.values())
    wrong = await client.get("/machine/archive", params={"path": str(home / "Desktop" / "notes.txt")})
    assert wrong.status_code == 422 and "not an archive" in wrong.json()["detail"]
    outside = await client.get("/machine/archive", params={"path": "/etc/hosts"})
    assert outside.status_code == 403
    assert list((home / "code").iterdir()) == []


async def test_importing_from_the_desktop_makes_a_project(client: AsyncClient, session: AsyncSession, home: Path,
                                                          jobs: list[tuple[str, tuple[Any, ...]]]):
    made = await client.post("/machine/import", json={
        "archive": str(home / "Desktop" / "shop-main.zip"), "into": str(home / "code"), "name": "shop",
        "excluded": ["dist"], "rules": [{"id": "deps", "label": "New dependencies need approval"}]})
    assert made.status_code == 201, made.text
    body = made.json()
    target = Path(body["target"])
    assert target == (home / "code" / "shop").resolve() and target.is_dir() and list(target.iterdir()) == []
    assert body["project"]["id"] == "shop" and body["project"]["status"] == "onboarding"
    assert body["files"] == 3 and body["top"] == "shop-main"
    assert jobs == [("import", ("shop", (home / "Desktop" / "shop-main.zip").resolve(), target, 3, str(target), False))]

    project = await session.get(m.Project, "shop")
    assert project.source_kind == "local" and project.source_repo == str(target) and project.excluded == ["dist"]
    audit = (await session.execute(select(m.AuditEntry).where(m.AuditEntry.action == "machine.import"))).scalars().one()
    assert audit.target == str(target) and audit.detail["entries"] == 3 and audit.detail["uploaded"] is False

    # The folder now exists: a second import there is refused, never merged.
    again = await client.post("/machine/import", json={"archive": str(home / "Desktop" / "shop-main.zip"),
                                                       "into": str(home / "code"), "name": "shop"})
    assert again.status_code == 409 and "already exists" in again.json()["detail"]
    assert len(jobs) == 1


async def test_an_import_is_refused_whole_and_leaves_nothing(client: AsyncClient, session: AsyncSession, home: Path,
                                                            jobs: list[tuple[str, tuple[Any, ...]]]):
    make_zip(home / "Desktop" / "slip.zip", {"ok.py": "1", "../../escape.py": "owned"})
    refused = await client.post("/machine/import", json={"archive": str(home / "Desktop" / "slip.zip"),
                                                         "into": str(home / "code"), "name": "slip"})
    assert refused.status_code == 422 and "'..'" in refused.json()["detail"]
    assert not (home / "code" / "slip").exists() and jobs == []
    assert await session.get(m.Project, "slip") is None
    for bad in ({"name": "../up"}, {"name": ".hidden"}, {"into": "/etc"}):
        body = {"archive": str(home / "Desktop" / "shop-main.zip"), "into": str(home / "code"), "name": "x", **bad}
        assert (await client.post("/machine/import", json=body)).status_code in (403, 422), bad


async def test_an_uploaded_archive_is_streamed_checked_and_imported(client: AsyncClient, home: Path, tmp_path: Path,
                                                                    jobs: list[tuple[str, tuple[Any, ...]]],
                                                                    monkeypatch: pytest.MonkeyPatch):
    data = make_tgz(tmp_path / "upload.tgz", SHOP).read_bytes()
    made = await client.post("/machine/import/upload", params={"into": str(home / "code"), "name": "shop-up"},
                             files={"archive": ("shop-main.tar.gz", data, "application/gzip")},
                             data={"options": json.dumps({"excluded": ["node_modules"]})})
    assert made.status_code == 201, made.text
    (kind, (pid, temp, target, files, repo, uploaded)), = jobs
    assert kind == "import" and pid == "shop-up" and files == 3 and uploaded is True
    assert temp.read_bytes() == data                     # the whole body, and only it, reached the file
    assert target == (home / "code" / "shop-up").resolve()
    temp.unlink()

    # Past the ceiling the stream stops and nothing is kept.
    held = tmp_path / "uploads"
    held.mkdir()
    monkeypatch.setattr(routes_machine, "UPLOAD_DIR", str(held))
    monkeypatch.setattr(routes_machine, "MAX_UPLOAD", 1024)
    big = await client.post("/machine/import/upload", params={"into": str(home / "code"), "name": "big"},
                            files={"archive": ("big.zip", b"x" * 5000, "application/zip")})
    assert big.status_code == 413
    assert list(held.iterdir()) == []
    wrong = await client.post("/machine/import/upload", params={"into": str(home / "code"), "name": "w"},
                              files={"archive": ("notes.rar", b"x", "application/octet-stream")})
    assert wrong.status_code == 422 and "not an archive" in wrong.json()["detail"]
    assert list(held.iterdir()) == []                    # a refused upload is deleted too
    assert not (home / "code" / "big").exists() and not (home / "code" / "w").exists()


async def test_an_empty_project_is_a_repository_with_a_readme(client: AsyncClient, session: AsyncSession, home: Path,
                                                               jobs: list[tuple[str, tuple[Any, ...]]]):
    made = await client.post("/machine/empty-project", json={"into": str(home / "code"), "name": "fresh"})
    assert made.status_code == 201, made.text
    target = Path(made.json()["target"])
    assert (target / "README.md").read_text() == "# fresh\n"
    log = subprocess.run(["git", "log", "--format=%s", "-1"], cwd=target, capture_output=True, text=True, check=True)
    assert log.stdout.strip() == "Start fresh"
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=target, capture_output=True, text=True, check=True)
    assert branch.stdout.strip() == "main"
    assert jobs == [("onboard", ("fresh", str(target)))]
    assert (await session.get(m.Project, "fresh")).source_repo == str(target)
    assert (await client.post("/machine/empty-project", json={"into": str(home / "code"), "name": "fresh"})
            ).status_code == 409


async def test_importing_needs_machine_access_and_onboarding(api: Any, client: AsyncClient, home: Path,
                                                             jobs: list[tuple[str, tuple[Any, ...]]]):
    assert (await client.post("/admin/users", json=ADMIN)).status_code in (200, 201)
    async with _client(api) as other:
        assert (await other.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
                ).status_code == 200
        body = {"archive": str(home / "Desktop" / "shop-main.zip"), "into": str(home / "code"), "name": "shop"}
        assert (await other.post("/machine/import", json=body)).status_code == 403
        assert (await other.post("/machine/empty-project", json={"into": str(home / "code"), "name": "x"})
                ).status_code == 403
    assert jobs == [] and list((home / "code").iterdir()) == []


# ── macOS privacy ────────────────────────────────────────────────
async def test_a_folder_macos_guards_says_which_switch_to_turn_on(client: AsyncClient, home: Path,
                                                                  monkeypatch: pytest.MonkeyPatch):
    blocked = str((home / "Desktop").resolve())
    real_scandir = os.scandir

    def guarded(path: Any) -> Any:
        if str(path) == blocked:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(path))
        return real_scandir(path)

    monkeypatch.setattr(machine.os, "scandir", guarded)
    monkeypatch.setattr(machine, "PLATFORM", "darwin")
    monkeypatch.setattr(machine, "host_app", lambda: "iTerm")
    answer = await client.get("/machine/list", params={"path": blocked})
    assert answer.status_code == 403
    body = answer.json()
    assert body["code"] == "needs_os_permission" and body["folder"] == blocked and body["app"] == "iTerm"
    assert body["detail"] == ("macOS has not given iTerm access to Desktop. Open System Settings → Privacy & "
                              "Security → Files and Folders (or Full Disk Access), turn it on for iTerm, then try again.")

    # When the app cannot be told, it says so rather than guess.
    monkeypatch.setattr(machine, "host_app", lambda: None)
    body = (await client.get("/machine/list", params={"path": blocked})).json()
    assert "the app that started NeuroCode" in body["detail"] and body["app"] is None

    # Not a Mac, or an ordinary permission outside a guarded place: the old words, no code.
    monkeypatch.setattr(machine, "PLATFORM", "linux")
    plain = (await client.get("/machine/list", params={"path": blocked})).json()
    assert "code" not in plain and "may not read" in plain["detail"]


def test_the_app_is_the_outermost_bundle_on_the_parent_chain():
    assert machine._bundle("/Applications/iTerm.app/Contents/MacOS/iTerm2") == "iTerm"
    assert machine._bundle("/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper.app/Contents/MacOS/x") \
        == "Visual Studio Code"
    assert machine._bundle("/opt/homebrew/Frameworks/Python.framework/Resources/Python.app/Contents/MacOS/Python") is None
    assert machine._bundle("/bin/zsh") is None


# ── the job, for real ────────────────────────────────────────────
class FakeGateway:
    """Retrieval asks for an embedding lane; there is none. No provider is ever called."""

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: Any, parse: Any, **kw: Any) -> Result[Any]:
        return Result(parse("{}"), Provider("groq", "llama-3.3-70b-versatile"), 1)


async def test_the_import_job_unpacks_then_onboards(schema: str, tmp_path: Path):
    pid = "import-live"
    archive = make_zip(tmp_path / "shop-main.zip", SHOP)
    target = tmp_path / "shop"
    target.mkdir()
    db = Database(url=schema)
    try:
        async with db.session() as s:
            await s.execute(delete(m.Project).where(m.Project.id == pid))
            s.add(m.Project(id=pid, name="Import Live", source_kind="local", source_repo=str(target),
                            status="onboarding"))
        found = archives.survey(archive, "zip")
        await archives.import_archive(db, FakeGateway(), pid, archive, target, found,
                                      Spec(source="local", repo=str(target)))
        assert (target / "app" / "main.py").is_file()
        async with db.read() as s:
            project = await s.get(m.Project, pid)
            files = set((await s.execute(select(m.CodeFile.path).where(m.CodeFile.project_id == pid))).scalars())
            said = [e.action for e in (await s.execute(select(m.ActivityEvent).where(
                m.ActivityEvent.project_id == pid).order_by(m.ActivityEvent.seq))).scalars()]
        assert project.status == "active" and {"app/main.py", "app/util.py"} <= files
        assert said[:2] == ["Extracting", "Extracting"] and "Extracted" in said and "Code indexed" in said

        # A header that lies about its size stops the job, removes the folder it made and pauses the project.
        liar = tmp_path / "liar"
        liar.mkdir()
        lying = archives.survey(archive, "zip")
        lying.entries[0] = archives.Entry(name=lying.entries[0].name, source=lying.entries[0].source, dir=False, size=1)
        await archives.import_archive(db, FakeGateway(), pid, archive, liar, lying,
                                      Spec(source="local", repo=str(liar)))
        assert not liar.exists()
        async with db.read() as s:
            project = await s.get(m.Project, pid)
        assert project.status == "paused" and "more than its header says" in project.description
    finally:
        async with db.session() as s:
            await s.execute(delete(m.ActivityEvent).where(m.ActivityEvent.project_id == pid))
            await s.execute(delete(m.Project).where(m.Project.id == pid))
        await db.close()
