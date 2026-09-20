"""Notebooks in the Workbench, over HTTP and the kernel socket, against real files and a real kernel.

The files are written by the test into a temporary root, so every answer is checked against bytes the
test knows. The kernel is a real IPython kernel: the notebook's folder has a `.venv/bin/python` that is
the test's own interpreter (which has ipykernel, a dev dependency), so the kernel is chosen exactly as a
project's would be — its own environment first. Nothing reaches the internet or a model.

File routes run on the rolled-back test session. Kernel and socket tests run on a workspace that really
commits (the socket looks its person up in a session of its own), cleaned away afterwards, as in
test_terminal_socket.py.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.api.routes_notebooks import kernels_of
from app.data.engine import Database
from app.data.loader import sync_roles
from app.services import machine
from app.services import notebooks as nb
from app.settings import Settings
from tests.test_terminal_socket import ORIGIN, Socket, _cookie

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "notebook-owner@example.com",
         "password": "correct horse battery"}
ADMIN = {"email": "notebook-admin@example.com", "name": "Asha", "password": "another long passphrase",
         "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}
#: A 1×1 PNG, the smallest real picture a kernel can display.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9aw"
                       "AAAABJRU5ErkJggg==")

STORED = {
    "nbformat": 4, "nbformat_minor": 4,
    "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                 "custom": {"kept": True}},
    "cells": [
        {"cell_type": "markdown", "metadata": {}, "source": ["# Title\n", "Some *text*."]},
        {"cell_type": "code", "execution_count": 3, "metadata": {"tags": ["x"]}, "source": ["x = 1\n", "x + 1"],
         "outputs": [
             {"output_type": "stream", "name": "stdout", "text": ["a\n", "b\n"]},
             {"output_type": "execute_result", "execution_count": 3, "metadata": {},
              "data": {"text/plain": ["2"], "image/png": "iVBORw0K\nGgo=\n"}},
             {"output_type": "error", "ename": "ValueError", "evalue": "bad",
              "traceback": ["\x1b[31mValueError\x1b[0m: bad"]},
             {"output_type": "something-else"},
         ]},
        {"cell_type": "raw", "metadata": {}, "source": "raw text"},
    ],
}


def _write(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _project_venv(folder: Path) -> Path:
    """A project environment whose python is the test's own interpreter, which has ipykernel."""
    python = folder / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    return python


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


# ── the file: read and save, on the rolled-back session ───────────
@pytest.fixture
def root(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "elsewhere").mkdir()
    configured = settings.model_copy(update={"machine_roots": str(work), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    return Path(os.path.realpath(work))


@pytest_asyncio.fixture
async def api(catalogued: AsyncSession, root: Path, settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_api(db=None)
    app.state.settings = settings.model_copy(update={"machine_roots": str(root), "machine_access": True})

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield catalogued

    app.dependency_overrides[deps.session] = use_the_test_session
    yield app
    await kernels_of(app).close_all()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


async def test_a_notebook_opens_with_its_cells_and_outputs(client: AsyncClient, root: Path):
    file = _write(root / "analysis.ipynb", STORED)
    got = await client.get("/notebooks/file", params={"path": str(file)})
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["path"] == str(file) and body["name"] == "analysis.ipynb" and body["new"] is False
    assert body["sha1"] == _sha1(file) and body["size"] == file.stat().st_size
    assert body["language"] == "python" and body["kernelName"] == "python3"
    cells = body["notebook"]["cells"]
    assert [c["cell_type"] for c in cells] == ["markdown", "code", "raw"]
    assert cells[0]["source"] == "# Title\nSome *text*."
    assert all(nb.CELL_ID.match(c["id"]) for c in cells) and len({c["id"] for c in cells}) == 3
    code = cells[1]
    assert code["source"] == "x = 1\nx + 1" and code["execution_count"] == 3 and code["metadata"] == {"tags": ["x"]}
    # Texts joined into one string; the picture's line breaks taken out; the unknown output left out.
    assert code["outputs"] == [
        {"output_type": "stream", "name": "stdout", "text": "a\nb\n"},
        {"output_type": "execute_result", "execution_count": 3, "metadata": {},
         "data": {"text/plain": "2", "image/png": "iVBORw0KGgo="}},
        {"output_type": "error", "ename": "ValueError", "evalue": "bad",
         "traceback": ["\x1b[31mValueError\x1b[0m: bad"]},
    ]
    assert body["notebook"]["metadata"]["custom"] == {"kept": True}


async def test_a_save_writes_nbformat_as_jupyter_does_and_refuses_a_stale_one(client: AsyncClient, root: Path,
                                                                            catalogued: AsyncSession):
    file = _write(root / "analysis.ipynb", STORED)
    opened = (await client.get("/notebooks/file", params={"path": str(file)})).json()
    document = opened["notebook"]
    document["cells"][1]["source"] = "y = 2\nprint(y)\n"
    document["cells"][1]["outputs"] = [{"output_type": "stream", "name": "stdout", "text": "2\n",
                                        "extra": "page's own"}]
    document["cells"].append({"id": "fresh-cell", "cell_type": "markdown", "source": "## New", "metadata": {}})
    saved = await client.put("/notebooks/file", json={"path": str(file), "expectSha1": opened["sha1"],
                                                       "notebook": document})
    assert saved.status_code == 200, saved.text
    assert saved.json()["sha1"] == _sha1(file)

    written = file.read_text(encoding="utf-8")
    on_disk = json.loads(written)
    # Jupyter's own layout: one-space indent, keys sorted, a newline at the end, lines as lists; upgraded to 4.5.
    assert written == json.dumps(on_disk, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    assert on_disk["nbformat"] == 4 and on_disk["nbformat_minor"] == 5
    assert on_disk["metadata"]["custom"] == {"kept": True}
    code = on_disk["cells"][1]
    assert code["source"] == ["y = 2\n", "print(y)\n"]
    assert code["outputs"] == [{"output_type": "stream", "name": "stdout", "text": ["2\n"]}]
    assert on_disk["cells"][3] == {"id": "fresh-cell", "cell_type": "markdown", "metadata": {}, "source": ["## New"]}
    assert [c["id"] for c in on_disk["cells"]] == [c["id"] for c in document["cells"]]

    # Read back, it is what was saved.
    again = (await client.get("/notebooks/file", params={"path": str(file)})).json()
    assert again["notebook"]["cells"][1]["source"] == "y = 2\nprint(y)\n"
    assert again["notebook"]["cells"][1]["outputs"] == [{"output_type": "stream", "name": "stdout", "text": "2\n"}]

    # The version the page opened is gone: refused, and nothing written.
    before = file.read_bytes()
    stale = await client.put("/notebooks/file", json={"path": str(file), "expectSha1": opened["sha1"],
                                                       "notebook": document})
    assert stale.status_code == 409 and "changed on disk" in stale.json()["detail"]
    assert file.read_bytes() == before

    saves = select(m.AuditEntry).where(m.AuditEntry.action == "machine.file.save")
    audit = (await catalogued.execute(saves)).scalars().all()
    assert [a.target for a in audit] == [str(file)]


async def test_an_empty_file_opens_as_a_new_notebook_and_saves(client: AsyncClient, root: Path):
    file = root / "blank.ipynb"
    file.write_bytes(b"")
    body = (await client.get("/notebooks/file", params={"path": str(file)})).json()
    assert body["new"] is True and body["sha1"] == hashlib.sha1(b"").hexdigest()
    assert [c["cell_type"] for c in body["notebook"]["cells"]] == ["code"]
    assert file.read_bytes() == b""                      # opening wrote nothing
    body["notebook"]["cells"][0]["source"] = "print('hi')"
    saved = await client.put("/notebooks/file", json={"path": str(file), "expectSha1": body["sha1"],
                                                       "notebook": body["notebook"]})
    assert saved.status_code == 200, saved.text
    assert json.loads(file.read_text())["cells"][0]["source"] == ["print('hi')"]


async def test_what_is_not_a_notebook_or_not_inside_is_refused(client: AsyncClient, root: Path, api: FastAPI):
    outside = _write(root.parent / "elsewhere" / "secret.ipynb", STORED)
    got = await client.get("/notebooks/file", params={"path": str(outside)})
    assert got.status_code == 403 and "outside the folders" in got.json()["detail"]
    os.symlink(outside, root / "link.ipynb")
    assert (await client.get("/notebooks/file", params={"path": str(root / "link.ipynb")})).status_code == 403

    (root / "notes.txt").write_text("hello")
    got = await client.get("/notebooks/file", params={"path": str(root / "notes.txt")})
    assert got.status_code == 422 and "not a notebook" in got.json()["detail"]
    (root / "broken.ipynb").write_text("{not json")
    got = await client.get("/notebooks/file", params={"path": str(root / "broken.ipynb")})
    assert got.status_code == 422 and "not valid notebook JSON" in got.json()["detail"]
    _write(root / "old.ipynb", {"nbformat": 3, "worksheets": [], "cells": []})
    got = await client.get("/notebooks/file", params={"path": str(root / "old.ipynb")})
    assert got.status_code == 422 and "version 3" in got.json()["detail"]
    got = await client.get("/notebooks/file", params={"path": str(root / "missing.ipynb")})
    assert got.status_code == 404

    # A notebook with a cell that is not a cell is not saved.
    file = _write(root / "ok.ipynb", STORED)
    bad = await client.put("/notebooks/file", json={"path": str(file), "expectSha1": _sha1(file),
                                                     "notebook": {"cells": [{"cell_type": "picture", "source": ""}]}})
    assert bad.status_code == 422

    # Only a person with machine:access, and only while the server allows it.
    await client.post("/admin/users", json=ADMIN)
    async with _client(api) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        got = await admin.get("/notebooks/file", params={"path": str(file)})
        assert got.status_code == 403 and "machine:access" in got.json()["detail"]
        assert (await admin.post("/notebooks/kernels", json={"path": str(file)})).status_code == 403
    api.state.settings = api.state.settings.model_copy(update={"machine_access": False})
    got = await client.get("/notebooks/file", params={"path": str(file)})
    assert got.status_code == 404 and got.json()["detail"] == "Machine access is off on this server"


async def test_a_project_path_is_found_in_its_checkout(client: AsyncClient, root: Path, catalogued: AsyncSession):
    checkout = root / "shop"
    _write(checkout / "notebooks" / "eda.ipynb", STORED)
    catalogued.add(m.Project(id="shop", name="Shop", source_kind="local", source_repo=str(checkout)))
    await catalogued.flush()
    got = await client.get("/notebooks/file", params={"path": "notebooks/eda.ipynb", "projectId": "shop"})
    assert got.status_code == 200, got.text
    assert got.json()["path"] == str(checkout / "notebooks" / "eda.ipynb")
    escape = await client.get("/notebooks/file", params={"path": "../elsewhere/x.ipynb", "projectId": "shop"})
    assert escape.status_code == 403
    assert (await client.get("/notebooks/file", params={"path": "a.ipynb", "projectId": "nope"})).status_code == 404


async def test_the_kernel_it_would_choose_and_what_to_install_when_none(client: AsyncClient, root: Path,
                                                                        monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(nb, "installed_specs", dict)
    project = root / "ml"
    python = _project_venv(project)
    file = _write(project / "notebooks" / "train.ipynb", STORED)
    got = await client.get("/notebooks/kernelspecs", params={"path": str(file)})
    assert got.status_code == 200, got.text
    choice = got.json()["choice"]
    assert choice["source"] == "project" and choice["interpreter"] == str(python)
    # No version: opening a notebook does not run the interpreter to ask it for one.
    assert choice["displayName"] == "Python (.venv)"
    assert got.json()["missing"] is None

    # An R notebook on a machine with no R kernel: nothing is started, and it says what to install.
    r_notebook = _write(root / "stats.ipynb", {**STORED, "metadata": {"kernelspec": {"name": "ir", "language": "R"}}})
    got = (await client.get("/notebooks/kernelspecs", params={"path": str(r_notebook)})).json()
    assert got["choice"] is None and "IRkernel" in got["missing"]
    started = await client.post("/notebooks/kernels", json={"path": str(r_notebook)})
    assert started.status_code == 409 and "IRkernel" in started.json()["detail"]
    named = await client.post("/notebooks/kernels", json={"path": str(file), "kernel": "julia-1.10"})
    assert named.status_code == 404 and "julia-1.10" in named.json()["detail"]


async def test_opening_a_notebook_runs_no_interpreter_and_starting_a_kernel_asks_it(
        client: AsyncClient, root: Path, monkeypatch: pytest.MonkeyPatch):
    """A `.venv/bin/python` is a file of the project — anything that can write one file in a project
    folder writes it — so opening the notebook beside it must not run it. Only starting a kernel does."""
    monkeypatch.setattr(nb, "installed_specs", dict)
    project = root / "cloned"
    marker = project / "it-ran"
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\necho ran > "{marker}"\nexit 1\n')
    python.chmod(0o755)
    file = _write(project / "notes.ipynb", STORED)

    got = await client.get("/notebooks/kernelspecs", params={"path": str(file)})
    assert got.status_code == 200, got.text
    choice = got.json()["choice"]
    assert choice["source"] == "project" and choice["interpreter"] == str(python)
    assert choice["displayName"] == "Python (.venv)"          # found, not asked anything
    assert not marker.exists()

    # Starting a kernel is the person asking for that interpreter to run, so there it is asked — and
    # what it answers is that it has no ipykernel, in the words that say what to install.
    started = await client.post("/notebooks/kernels", json={"path": str(file), "kernel": "project"})
    assert started.status_code == 409 and "ipykernel" in started.json()["detail"]
    assert marker.exists()


async def test_a_save_over_a_file_too_big_to_open_is_refused_before_it_is_read(client: AsyncClient, root: Path):
    """A notebook too big to open is too big to save over, and is refused on its size on disk — not
    pulled into memory to have its SHA-1 taken first."""
    file = root / "huge.ipynb"
    with open(file, "wb") as out:
        out.truncate(nb.MAX_BYTES + 1)
    refused = await client.put("/notebooks/file", json={"path": str(file), "expectSha1": "0" * 40,
                                                        "notebook": {"cells": []}})
    assert refused.status_code == 413 and "40.0 MB" in refused.json()["detail"]
    assert file.stat().st_size == nb.MAX_BYTES + 1            # nothing written


# ── kernels and the socket, on a committing workspace ─────────────
@pytest_asyncio.fixture
async def live(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    monkeypatch.setattr(nb, "installed_specs", dict)
    db = Database(url=settings.test_database_url)
    async with db.session() as s:
        await sync_roles(s)
    app = create_api(db=db)
    work = tmp_path / "machine"
    work.mkdir()
    configured = settings.model_copy(update={"database_url": settings.test_database_url,
                                             "machine_roots": os.path.realpath(work), "machine_access": True})
    app.state.settings = configured
    monkeypatch.setattr(machine, "settings", lambda: configured)
    try:
        yield app
    finally:
        await kernels_of(app).close_all()
        async with db.session() as s:
            await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM activity"))
            await s.execute(text("DELETE FROM api_tokens"))
            await s.execute(text("DELETE FROM sessions"))
            await s.execute(text("DELETE FROM user_roles"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM workspace"))
        app.state.ledger.close()
        await db.close()


@pytest_asyncio.fixture
async def owner(live: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(live) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


def _notebook(live: FastAPI, name: str = "work.ipynb") -> Path:
    project = Path(live.state.settings.machine_roots) / "proj"
    _project_venv(project)
    return _write(project / name, STORED)


async def _kernel(owner: AsyncClient, file: Path) -> dict[str, Any]:
    started = await owner.post("/notebooks/kernels", json={"path": str(file)})
    assert started.status_code == 201, started.text
    return started.json()


async def _finished(owner: AsyncClient, kernel: str, request_id: str, seconds: float = 30) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        got = await owner.get(f"/notebooks/kernels/{kernel}/runs/{request_id}")
        assert got.status_code == 200, got.text
        run = got.json()
        if run["status"] in ("ok", "error", "aborted"):
            return run
        assert asyncio.get_running_loop().time() < deadline, run
        await asyncio.sleep(0.05)


async def _run(owner: AsyncClient, kernel: str, code: str, cell: str = "c1", seconds: float = 30) -> dict[str, Any]:
    queued = await owner.post(f"/notebooks/kernels/{kernel}/execute", json={"cellId": cell, "code": code})
    assert queued.status_code == 202, queued.text
    return await _finished(owner, kernel, queued.json()["requestId"], seconds)


async def test_a_real_kernel_runs_cells(live: FastAPI, owner: AsyncClient):
    file = _notebook(live)
    kernel = await _kernel(owner, file)
    assert kernel["new"] is True and kernel["status"] == "idle" and kernel["source"] == "project"
    assert kernel["interpreter"].endswith("proj/.venv/bin/python")
    # Starting it asked the interpreter what it is, which is where its version comes from.
    assert kernel["displayName"] == f"Python {'.'.join(map(str, sys.version_info[:3]))} (.venv)"
    # One kernel per notebook: asking again answers the same one.
    again = await _kernel(owner, file)
    assert again["id"] == kernel["id"] and again["new"] is False

    one = await _run(owner, kernel["id"], "1+1")
    assert one["status"] == "ok" and one["executionCount"] == 1
    assert one["outputs"] == [{"output_type": "execute_result", "data": {"text/plain": "2"}, "metadata": {},
                               "execution_count": 1}]

    printed = await _run(owner, kernel["id"], "import os\nprint('hello', 6 * 7)\nprint(os.getcwd())")
    assert printed["status"] == "ok"
    assert printed["outputs"] == [{"output_type": "stream", "name": "stdout", "text": f"hello 42\n{file.parent}\n"}]

    failed = await _run(owner, kernel["id"], "1 / 0")
    assert failed["status"] == "error"
    error = failed["outputs"][-1]
    assert error["output_type"] == "error" and error["ename"] == "ZeroDivisionError"
    assert any("ZeroDivisionError" in line for line in error["traceback"])

    picture = await _run(owner, kernel["id"],
                         f"from IPython.display import Image, display\ndisplay(Image(data={PNG!r}))")
    assert picture["status"] == "ok"
    shown = picture["outputs"][0]
    assert shown["output_type"] == "display_data"
    assert base64.b64decode(shown["data"]["image/png"]) == PNG

    # The kernel's environment is the project's, never the API's secrets.
    env = await _run(owner, kernel["id"],
                     "import os\nprint(sorted(k for k in os.environ if k.startswith('NEUROCODE_')), "
                     "os.environ.get('VIRTUAL_ENV', '').endswith('proj/.venv'))")
    assert env["outputs"][0]["text"] == "[] True\n"

    listed = (await owner.get("/notebooks/kernels")).json()
    assert [k["id"] for k in listed] == [kernel["id"]]
    detail = (await owner.get(f"/notebooks/kernels/{kernel['id']}", params={"runs": True})).json()
    assert len(detail["runs"]) == 5 and detail["executionCount"] == 5


async def test_interrupt_restart_and_shut_down(live: FastAPI, owner: AsyncClient):
    kernel = (await _kernel(owner, _notebook(live)))["id"]
    assert (await _run(owner, kernel, "x = 41"))["status"] == "ok"
    slow = (await owner.post(f"/notebooks/kernels/{kernel}/execute",
                             json={"cellId": "slow", "code": "import time\ntime.sleep(60)"})).json()
    behind = (await owner.post(f"/notebooks/kernels/{kernel}/execute", json={"cellId": "next", "code": "x"})).json()
    for _ in range(100):
        if (await owner.get(f"/notebooks/kernels/{kernel}/runs/{slow['requestId']}")).json()["status"] == "running":
            break
        await asyncio.sleep(0.05)
    assert (await owner.post(f"/notebooks/kernels/{kernel}/interrupt")).status_code == 200
    for _ in range(200):
        stopped = (await owner.get(f"/notebooks/kernels/{kernel}/runs/{slow['requestId']}")).json()
        waited = (await owner.get(f"/notebooks/kernels/{kernel}/runs/{behind['requestId']}")).json()
        if stopped["status"] != "running" and waited["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.05)
    assert stopped["status"] == "error" and stopped["outputs"][-1]["ename"] == "KeyboardInterrupt"
    assert waited["status"] == "aborted"               # a failed cell stops the ones queued behind it
    assert (await _run(owner, kernel, "x + 1"))["outputs"][0]["data"]["text/plain"] == "42"

    restarted = await owner.post(f"/notebooks/kernels/{kernel}/restart")
    assert restarted.status_code == 200 and restarted.json()["restarts"] == 1 and restarted.json()["status"] == "idle"
    gone = await _run(owner, kernel, "x")
    assert gone["status"] == "error" and gone["outputs"][-1]["ename"] == "NameError" and gone["executionCount"] == 1

    assert (await owner.delete(f"/notebooks/kernels/{kernel}")).status_code == 200
    assert (await owner.get(f"/notebooks/kernels/{kernel}")).status_code == 404
    late = await owner.post(f"/notebooks/kernels/{kernel}/execute", json={"cellId": "c", "code": "1"})
    assert late.status_code == 404


async def test_a_kernel_that_died_is_closed_when_the_notebook_starts_another(live: FastAPI, owner: AsyncClient):
    """Starting a kernel for a notebook whose last one died drops the dead one — and closes it, rather
    than leaving its folder, its log file and its channels held for the life of the process."""
    file = _notebook(live, "crash.ipynb")
    first = await _kernel(owner, file)
    held = kernels_of(live)
    died = held._all[first["id"]]
    folder = died._folder
    suicide = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)"
    killed = await owner.post(f"/notebooks/kernels/{first['id']}/execute",
                              json={"cellId": "boom", "code": suicide})
    assert killed.status_code == 202
    for _ in range(200):
        await held.reap_once()
        if died.status == "dead":
            break
        await asyncio.sleep(0.05)
    assert died.status == "dead" and os.path.isdir(folder)

    again = await _kernel(owner, file)
    assert again["id"] != first["id"] and again["new"] is True
    assert died.status == "closed" and died._log_file.closed
    assert not os.path.isdir(folder)      # with it, the connection file's session key


async def test_a_cell_still_running_is_not_forgotten_when_older_runs_are_trimmed(
        live: FastAPI, owner: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """A kernel remembers its last KEPT_RUNS executions, but what it is still working on is how its
    messages are found again: trimming that would drop the cell's output and leave it waiting forever."""
    monkeypatch.setattr(nb, "KEPT_RUNS", 2)
    kernel = (await _kernel(owner, _notebook(live, "many.ipynb")))["id"]
    slow = (await owner.post(f"/notebooks/kernels/{kernel}/execute",
                             json={"cellId": "slow", "code": "import time\nprint('a')\ntime.sleep(2)"})).json()
    for _ in range(200):
        if (await owner.get(f"/notebooks/kernels/{kernel}/runs/{slow['requestId']}")).json()["status"] == "running":
            break
        await asyncio.sleep(0.05)

    behind = []
    for i in range(3):
        queued = await owner.post(f"/notebooks/kernels/{kernel}/execute",
                                  json={"cellId": f"c{i}", "code": f"print({i})"})
        assert queued.status_code == 202, queued.text
        behind.append(queued.json()["requestId"])
    still = await owner.get(f"/notebooks/kernels/{kernel}/runs/{slow['requestId']}")
    assert still.status_code == 200, "the cell the kernel is working on was forgotten"

    ran = await _finished(owner, kernel, slow["requestId"])
    assert ran["status"] == "ok" and ran["outputs"][0]["text"] == "a\n"
    assert (await _finished(owner, kernel, behind[-1]))["outputs"][0]["text"] == "2\n"


async def test_the_limits_and_the_idle_shut_down(live: FastAPI, owner: AsyncClient):
    live.state.settings = live.state.settings.model_copy(update={"kernels_per_person": 1, "kernel_idle_minutes": 5})
    first = await _kernel(owner, _notebook(live, "one.ipynb"))
    second = await owner.post("/notebooks/kernels", json={"path": str(_notebook(live, "two.ipynb"))})
    assert second.status_code == 429 and "1 notebook kernels running" in second.json()["detail"]

    held = kernels_of(live)
    kernel = held._all[first["id"]]
    kernel.last_activity -= timedelta(minutes=6)
    await held.reap_once()
    assert kernel.status == "closed" and "Shut down after 5 minutes" in kernel.note
    assert (await owner.get(f"/notebooks/kernels/{first['id']}")).status_code == 404
    assert (await owner.post("/notebooks/kernels", json={"path": str(_notebook(live, "two.ipynb"))})).status_code == 201

    live.state.settings = live.state.settings.model_copy(update={"kernels_max": 0})
    off = await owner.post("/notebooks/kernels", json={"path": str(_notebook(live, "three.ipynb"))})
    assert off.status_code == 409 and "switched off" in off.json()["detail"]


async def test_the_socket_streams_a_cell_and_is_let_in_only_for_its_owner(live: FastAPI, owner: AsyncClient):
    kernel = (await _kernel(owner, _notebook(live)))["id"]
    path = f"/notebooks/kernels/{kernel}/ws"
    async with Socket(live, path, {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        hello = json.loads((await ws.receive())["text"])
        assert hello["type"] == "hello" and hello["kernel"]["id"] == kernel and hello["runs"] == []
        queued = (await owner.post(f"/notebooks/kernels/{kernel}/execute",
                                   json={"cellId": "cell-a", "code": "for i in range(3): print(i)\n'done'"})).json()
        seen: list[dict[str, Any]] = []
        while not seen or seen[-1]["type"] != "done":
            event = json.loads((await ws.receive(30))["text"])
            if event.get("requestId") == queued["requestId"]:
                seen.append(event)
        kinds = [e["type"] for e in seen]
        assert kinds[0] == "queued" and "started" in kinds and kinds[-1] == "done"
        assert all(e["cellId"] == "cell-a" for e in seen)
        assert "".join(e["text"] for e in seen if e["type"] == "stream") == "0\n1\n2\n"
        result = next(e for e in seen if e["type"] == "output")
        assert result["output"]["data"]["text/plain"] == "'done'"
        assert seen[-1]["status"] == "ok" and seen[-1]["executionCount"] == 1

    # A page that reconnects is handed what it missed.
    async with Socket(live, path, {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        hello = json.loads((await ws.receive())["text"])
        assert [r["requestId"] for r in hello["runs"]] == [queued["requestId"]]
        assert hello["runs"][0]["outputs"][0]["text"] == "0\n1\n2\n"

    async with Socket(live, path, {"origin": ORIGIN}) as ws:
        assert (await ws.receive())["code"] == 4401
    async with Socket(live, path, {"cookie": _cookie(owner), "origin": "https://evil.example"}) as ws:
        assert (await ws.receive())["code"] == 4403
    async with Socket(live, "/notebooks/kernels/nope/ws", {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        assert (await ws.receive())["code"] == 4404

    # Another person with machine:access is not let into someone else's kernel.
    await owner.post("/admin/users", json=ADMIN)
    async with _client(live) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        async with Socket(live, path, {"cookie": _cookie(admin), "origin": ORIGIN}) as ws:
            assert (await ws.receive())["code"] == 4403       # no machine:access at all

    # A personal token opens it only when it names machine:access.
    plain = (await owner.post("/tokens", json={"name": "script", "scopes": [], "expiresInDays": 30})).json()
    shell = (await owner.post("/tokens", json={"name": "nc", "scopes": ["machine:access"], "expiresInDays": 30})).json()
    async with Socket(live, path, {"authorization": f"Bearer {plain['token']}", "origin": ORIGIN}) as ws:
        assert (await ws.receive())["code"] == 4403
    async with Socket(live, path, {"authorization": f"Bearer {shell['token']}", "origin": ORIGIN}) as ws:
        assert json.loads((await ws.receive())["text"])["type"] == "hello"

    # Shutting the kernel down closes its sockets.
    async with Socket(live, path, {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        await ws.receive()
        assert (await owner.delete(f"/notebooks/kernels/{kernel}")).status_code == 200
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.close":
                break
            if json.loads(message["text"])["type"] == "closed":
                continue
