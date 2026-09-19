"""Jupyter notebooks in the Workbench: read and save .ipynb files, and run their cells through a kernel on this machine.

Everything here needs `machine:access` and `settings.machine_access`, like every route that reads the
machine's files or runs something on it (`routes_terminal.machine_person`); with the setting off each
route answers 404 before anything else is looked at. A notebook is named by its absolute path, or by a
project path (label-prefixed for a further source) together with `projectId`, and it stays inside the
machine's roots either way.

A kernel's output travels over a WebSocket (`/notebooks/kernels/{id}/ws`), let in by the same rules as
the terminal's: this app's own origin, the person the cookie (or a bearer token naming `machine:access`)
names, and only their own kernel. Commands — run a cell, interrupt, restart, shut down — go over HTTP.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import AuditRepository, NotFound
from ..schemas.machine import saved_json
from ..services import machine
from ..services import notebooks as nb
from ..services.identity import Person
from .deps import session
from .routes_terminal import _pump, _send_json, admit, machine_person

MAX_PATH = 4096


def kernels_of(app: Any) -> nb.Kernels:
    """The app's kernels, made on first use — a test app that never ran its lifespan still has them."""
    state = app.state
    if getattr(state, "kernels", None) is None:
        state.kernels = nb.Kernels()
    return state.kernels


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Closing the API shuts every kernel down, rather than leaving processes nobody can reach."""
    yield
    if getattr(app.state, "kernels", None) is not None:
        await app.state.kernels.close_all()


router = APIRouter(tags=["notebooks"], lifespan=_lifespan)


def kernels(request: Request) -> nb.Kernels:
    return kernels_of(request.app)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def _answer(work: Any) -> Any:
    """macOS's privacy refusal answered in full, as the machine routes answer it."""
    try:
        return await work
    except machine.NeedsOsPermission as blocked:
        return JSONResponse(blocked.as_json(), status_code=blocked.status)


class Where(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH)
    projectId: str | None = Field(default=None, max_length=80)


class SaveIn(Where):
    #: The SHA-1 of the file as the page opened it; the save is refused when the file no longer has it.
    expectSha1: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-fA-F]{40}$")
    #: The notebook as `GET /notebooks/file` gave it (nbformat 4, sources as one string each), edited.
    notebook: dict[str, Any]


class KernelIn(Where):
    #: A kernel from `GET /notebooks/kernelspecs` `available`; left out, the one it would choose.
    kernel: str | None = Field(default=None, max_length=200)


class ExecuteIn(BaseModel):
    cellId: str = Field(min_length=1, max_length=100)
    code: str = Field(max_length=nb.MAX_CODE)


# ── the file ─────────────────────────────────────────────────────
@router.get("/notebooks/file")
async def read_notebook(path: str = Query(min_length=1, max_length=MAX_PATH), projectId: str | None = None,
                        _: Person = Depends(machine_person), open_session: AsyncSession = Depends(session)) -> Any:
    """A notebook for the Workbench: `{path, name, size, modified, sha1, language, kernelName, new, notebook}`.
    `notebook` is nbformat 4 with every cell's source and every output's text as one string, and an id on
    every cell. An empty file opens as a new notebook with one code cell (`new: true`); nothing is written
    until it is saved."""
    async def work() -> dict[str, Any]:
        file = await nb.resolve(open_session, path, projectId)
        return await asyncio.to_thread(nb.read, file)
    return await _answer(work())


@router.put("/notebooks/file")
async def save_notebook(body: SaveIn, request: Request, who: Person = Depends(machine_person),
                        open_session: AsyncSession = Depends(session)) -> Any:
    """Write the notebook back as nbformat 4.5 — only over the version the page opened (409 otherwise,
    nothing written). Answers `{path, size, modified, sha1}`. Audited as a file save."""
    async def work() -> dict[str, Any]:
        file = await nb.resolve(open_session, body.path, body.projectId)
        saved, before = await asyncio.to_thread(nb.write, file, body.notebook, body.expectSha1)
        await machine.MachineService(open_session).saved(who, saved, before, ip=_ip(request))
        return saved_json(saved)
    return await _answer(work())


@router.get("/notebooks/kernelspecs")
async def kernel_options(path: str = Query(min_length=1, max_length=MAX_PATH), projectId: str | None = None,
                         _: Person = Depends(machine_person),
                         open_session: AsyncSession = Depends(session)) -> Any:
    """What could run this notebook, found on this machine: `{language, choice, available, missing}`.
    `choice` is what starting a kernel would use (null when nothing can, and `missing` then says what to
    install); `available` is every kernel that could be picked instead."""
    async def work() -> dict[str, Any]:
        file = await nb.resolve(open_session, path, projectId)
        opened = await asyncio.to_thread(nb.read, file)
        found = await asyncio.to_thread(nb.options, file, opened["language"], opened["kernelName"])
        return nb.options_json(found)
    return await _answer(work())


# ── kernels ──────────────────────────────────────────────────────
@router.post("/notebooks/kernels", status_code=201)
async def start_kernel(body: KernelIn, request: Request, who: Person = Depends(machine_person),
                       open_session: AsyncSession = Depends(session),
                       held: nb.Kernels = Depends(kernels)) -> Any:
    """Your kernel for this notebook: started now (audited), or the one already running for it — a
    notebook has one kernel per person. Answers the kernel, with `new` saying which."""
    async def work() -> dict[str, Any]:
        file = await nb.resolve(open_session, body.path, body.projectId)
        kernel, new = await nb.start_kernel(open_session, held, request.app.state.settings, who, file,
                                            kernel_name=body.kernel, ip=_ip(request))
        return {**kernel.json(), "new": new}
    return await _answer(work())


@router.get("/notebooks/kernels")
async def my_kernels(who: Person = Depends(machine_person),
                     held: nb.Kernels = Depends(kernels)) -> list[dict[str, Any]]:
    """Your kernels, oldest first — a handful at most (`kernels_per_person`), so the list needs no paging."""
    return [k.json() for k in held.mine(who.id)]


@router.get("/notebooks/kernels/{kernel_id}")
async def one_kernel(kernel_id: str, runs: bool = False, who: Person = Depends(machine_person),
                     held: nb.Kernels = Depends(kernels)) -> dict[str, Any]:
    """A kernel; with `runs=true`, the executions it remembers (at most 200) and what each printed."""
    return held.get(kernel_id, who.id).json(runs=runs)


@router.post("/notebooks/kernels/{kernel_id}/execute", status_code=202)
async def execute(kernel_id: str, body: ExecuteIn, who: Person = Depends(machine_person),
                  held: nb.Kernels = Depends(kernels)) -> dict[str, Any]:
    """Queue a cell's code on the kernel. Answers at once with the run (`requestId`); what it prints
    arrives on the socket, and `GET …/runs/{requestId}` reads it afterwards."""
    return held.get(kernel_id, who.id).execute(body.cellId, body.code).json()


@router.get("/notebooks/kernels/{kernel_id}/runs/{request_id}")
async def one_run(kernel_id: str, request_id: str, who: Person = Depends(machine_person),
                  held: nb.Kernels = Depends(kernels)) -> dict[str, Any]:
    """One execution: `{requestId, cellId, status, executionCount, outputs, truncated, startedAt, endedAt}`,
    status queued | running | ok | error | aborted."""
    found = held.get(kernel_id, who.id).runs.get(request_id)
    if found is None:
        raise NotFound(f"run {request_id}")
    return found.json()


@router.post("/notebooks/kernels/{kernel_id}/interrupt")
async def interrupt(kernel_id: str, who: Person = Depends(machine_person),
                    held: nb.Kernels = Depends(kernels)) -> dict[str, Any]:
    """Stop the cell that is running (a KeyboardInterrupt in Python); the cells queued behind it are aborted."""
    kernel = held.get(kernel_id, who.id)
    await kernel.interrupt()
    return kernel.json()


@router.post("/notebooks/kernels/{kernel_id}/restart")
async def restart(kernel_id: str, request: Request, who: Person = Depends(machine_person),
                  open_session: AsyncSession = Depends(session),
                  held: nb.Kernels = Depends(kernels)) -> dict[str, Any]:
    """A fresh process for the same notebook: every variable is gone, and cells still waiting are aborted."""
    kernel = held.get(kernel_id, who.id)
    await kernel.restart()
    await AuditRepository(open_session).record(
        action="notebook.kernel.start", user_id=who.id, target=str(kernel.path),
        detail={"kernel": kernel.id, "name": kernel.choice.name, "source": kernel.choice.source,
                "interpreter": kernel.choice.interpreter, "restart": True}, ip=_ip(request))
    return kernel.json()


@router.delete("/notebooks/kernels/{kernel_id}")
async def shut_down(kernel_id: str, who: Person = Depends(machine_person),
                    held: nb.Kernels = Depends(kernels)) -> dict[str, Any]:
    await held.close(kernel_id, who.id)
    return {"ok": True, "id": kernel_id}


@router.websocket("/notebooks/kernels/{kernel_id}/ws")
async def kernel_socket(websocket: WebSocket, kernel_id: str) -> None:
    """A kernel's events, as JSON text frames. First `hello` ({kernel, runs}: the executions it remembers,
    so a page that reconnects fills in what it missed); then `state` ({kernel}), `queued`, `started`
    ({executionCount}), `stream` ({name, text}), `output` ({output, displayId}), `clear`, `update`
    ({displayId, output}), `done` ({status, executionCount}) — each run event carrying `requestId` and
    `cellId` — and `resync` (this socket fell behind: read the kernel again) or `closed`. The page sends
    nothing; commands go over HTTP."""
    who = await admit(websocket)
    if who is None:
        return
    try:
        kernel = kernels_of(websocket.app).get(kernel_id, who.id)
    except NotFound:
        await websocket.close(code=4404, reason="That kernel is shut down.")
        return
    watcher = kernel.watch()
    first = kernel.json(runs=True)

    async def outgoing() -> None:
        await _send_json(websocket, {"type": "hello", "kernel": {k: v for k, v in first.items() if k != "runs"},
                                     "runs": first["runs"]})
        while True:
            event = await watcher.queue.get()
            await _send_json(websocket, event)
            if event.get("type") == "closed":
                await websocket.close(code=1000, reason="The kernel was shut down.")
                return

    async def incoming() -> None:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    await _pump(websocket, outgoing(), incoming())
    kernel.unwatch(watcher)
