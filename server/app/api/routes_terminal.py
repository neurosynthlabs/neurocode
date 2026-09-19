"""Terminals, runs and debug sessions on the machine the API runs on.

Everything here needs `machine:access` (an Owner's permission) and `settings.machine_access`. With the
setting off every route answers 404 "Machine access is off on this server", before anything else is
looked at, so a hosted server does not even advertise that it could.

Two WebSockets carry what streams: a terminal's bytes both ways (`/machine/terminals/{id}/ws`) and a
debug session's events (`/debug/{id}/ws`). A browser sends its cookies with a WebSocket to any site, and
no preflight stands in the way, so a socket is let in only when its Origin is this app's own — the rule
CORS applies to every other request — and only for the person the session cookie names. A refused socket
is accepted and then closed with a code and a reason (4401 no session, 4403 not allowed, 4404 not
there), because a socket refused during the handshake reaches the page as a bare 1006 with no words.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import AuditRepository, NotFound
from ..services import debug as debugging
from ..services import terminal as terminals_service
from ..services.errors import Refused
from ..services.identity import IdentityService, Person
from ..services.terminal import MACHINE, RunConfigService, Terminals, open_shell, suggestions
from ..services.tokens import PREFIX as TOKEN_PREFIX, TokenService
from ..settings import Settings
from .deps import COOKIE, current_person, session, token_from

#: What every machine route answers when the server was set up without machine access.
OFF = "Machine access is off on this server"
#: The most configurations one page lists, however the caller spells the number.
MAX_LIST = 200
#: Debugger commands the panel may send, and nothing else.
COMMANDS = ("continue", "next", "stepIn", "stepOut", "pause", "terminate", "restart", "threads", "stackTrace",
            "scopes", "variables", "evaluate", "setBreakpoints")


def _state(app: Any) -> Any:
    return app.state


def terminals_of(app: Any) -> Terminals:
    """The app's terminals, made on first use — a test app that never ran its lifespan still has them."""
    state = _state(app)
    if getattr(state, "terminals", None) is None:
        state.terminals = Terminals()
    return state.terminals


def debuggers_of(app: Any) -> debugging.Debuggers:
    state = _state(app)
    if getattr(state, "debuggers", None) is None:
        state.debuggers = debugging.Debuggers()
    return state.debuggers


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Closing the API closes every terminal and debugger it opened, rather than leaving shells and
    debuggees behind with nobody able to reach them."""
    yield
    state = _state(app)
    if getattr(state, "terminals", None) is not None:
        await state.terminals.close_all()
    if getattr(state, "debuggers", None) is not None:
        await state.debuggers.close_all()


router = APIRouter(tags=["terminal"], lifespan=_lifespan)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def machine_person(request: Request, who: Person = Depends(current_person)) -> Person:
    """Machine access is on here, and this person holds `machine:access`."""
    if not _settings(request).machine_access:
        raise Refused(OFF, status=404)
    who.must(MACHINE, "use this machine")
    return who


def terminals(request: Request) -> Terminals:
    return terminals_of(request.app)


def debuggers(request: Request) -> debugging.Debuggers:
    return debuggers_of(request.app)


# ── sockets: who may open one ────────────────────────────────────

def origin_allowed(websocket: WebSocket, config: Settings, *, by_cookie: bool) -> bool:
    """This app's own page, and nothing else a browser could be showing.

    Same-origin is the Origin naming the host the socket was opened on; beyond that, the origins CORS
    already trusts with the cookie. A socket with no Origin at all is not a browser's, so it cannot be
    riding on someone's cookie without their knowing — but it may use the cookie only if it says where
    it comes from, and a script with a token sends the token instead."""
    origin = websocket.headers.get("origin")
    if not origin:
        return not by_cookie
    if re.match(config.cors_origin_regex, origin):
        return True
    host = websocket.headers.get("host", "")
    return bool(host) and urlparse(origin).netloc == host


async def socket_person(websocket: WebSocket) -> Person | None:
    """The person a socket's session cookie (or bearer token) names, looked up as every request's is.

    The lookup takes a connection for a moment and gives it back: a terminal can stay open for hours, and
    a socket that held its request's transaction that long would hold a pooled connection with it."""
    token = token_from(websocket)  # type: ignore[arg-type]  # a WebSocket carries headers and cookies alike
    if not token:
        return None
    if token.startswith(TOKEN_PREFIX):
        # A personal access token opens a terminal only when it names `machine:access` itself; the
        # permission check in `admit` sees the token's cut. `session()`, not `read()`: its use is recorded.
        try:
            async with websocket.app.state.db.session() as open_session:
                return await TokenService(open_session).person(token)
        except Refused:
            return None
    async with websocket.app.state.db.read() as open_session:
        return await IdentityService(open_session).whoami(token)


async def admit(websocket: WebSocket) -> Person | None:
    """Accept the socket, or accept it only to close it with a code and a reason. Returns who it is."""
    config: Settings = websocket.app.state.settings
    by_cookie = bool(websocket.cookies.get(COOKIE)) and not websocket.headers.get("authorization")
    await websocket.accept()
    refusal: tuple[int, str] | None = None
    who: Person | None = None
    if not origin_allowed(websocket, config, by_cookie=by_cookie):
        refusal = (4403, "This socket was opened from another site.")
    elif not config.machine_access:
        refusal = (4404, OFF)
    else:
        who = await socket_person(websocket)
        if who is None:
            refusal = (4401, "Sign in to continue.")
        elif MACHINE not in who.permissions:
            refusal = (4403, f"This needs the {MACHINE} permission.")
    if refusal is not None:
        await websocket.close(code=refusal[0], reason=refusal[1])
        return None
    return who


async def _send_json(websocket: WebSocket, data: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(data, ensure_ascii=False))


# ── terminals ────────────────────────────────────────────────────

class TerminalIn(BaseModel):
    #: An absolute folder inside the roots. Left out, the project's checkout, else the first root.
    cwd: str | None = Field(default=None, max_length=4096)
    projectId: str | None = Field(default=None, max_length=80)
    cols: int = Field(default=100, ge=terminals_service.MIN_COLS, le=terminals_service.MAX_COLS)
    rows: int = Field(default=30, ge=terminals_service.MIN_ROWS, le=terminals_service.MAX_ROWS)


class SizeIn(BaseModel):
    cols: int = Field(default=100, ge=terminals_service.MIN_COLS, le=terminals_service.MAX_COLS)
    rows: int = Field(default=30, ge=terminals_service.MIN_ROWS, le=terminals_service.MAX_ROWS)


@router.post("/machine/terminals", status_code=201)
async def open_terminal(body: TerminalIn, request: Request, who: Person = Depends(machine_person),
                        open_session: AsyncSession = Depends(session),
                        held: Terminals = Depends(terminals)) -> dict[str, Any]:
    """A shell on this machine, in a folder inside the roots. Audited: who, where, which shell."""
    return await open_shell(open_session, held, _settings(request), who, cwd=body.cwd, project_id=body.projectId,
                            cols=body.cols, rows=body.rows, ip=_ip(request))


@router.get("/machine/terminals")
async def list_terminals(who: Person = Depends(machine_person),
                         held: Terminals = Depends(terminals)) -> list[dict[str, Any]]:
    """Your terminals, oldest first — at most eight, so the list needs no paging."""
    return [t.json() for t in held.mine(who.id)]


@router.get("/machine/terminals/{terminal_id}")
async def one_terminal(terminal_id: str, who: Person = Depends(machine_person),
                       held: Terminals = Depends(terminals)) -> dict[str, Any]:
    return held.get(terminal_id, who.id).json()


@router.post("/machine/terminals/{terminal_id}/stop")
async def stop_terminal(terminal_id: str, who: Person = Depends(machine_person),
                        held: Terminals = Depends(terminals)) -> dict[str, Any]:
    """End what it is running and keep what it printed, so a finished run can still be read."""
    terminal = held.get(terminal_id, who.id)
    await terminal.stop()
    return terminal.json()


@router.post("/machine/terminals/{terminal_id}/restart")
async def restart_terminal(terminal_id: str, request: Request, who: Person = Depends(machine_person),
                           open_session: AsyncSession = Depends(session),
                           held: Terminals = Depends(terminals)) -> dict[str, Any]:
    """The same command in the same folder again, in the same terminal. Audited like a start."""
    terminal = held.get(terminal_id, who.id)
    await terminal.restart()
    await AuditRepository(open_session).record(
        action="run.start" if terminal.kind == "run" else "terminal.open", user_id=who.id,
        target=terminal.title, detail={"terminal": terminal.id, "cwd": str(terminal.cwd), "restart": True,
                                       "command": terminal.command, "project": terminal.project_id},
        ip=_ip(request))
    return terminal.json()


@router.post("/machine/terminals/{terminal_id}/resize")
async def resize_terminal(terminal_id: str, body: SizeIn, who: Person = Depends(machine_person),
                          held: Terminals = Depends(terminals)) -> dict[str, Any]:
    """The socket's resize message, for a caller without a socket open."""
    terminal = held.get(terminal_id, who.id)
    terminal.resize(body.cols, body.rows)
    return terminal.json()


@router.delete("/machine/terminals/{terminal_id}")
async def close_terminal(terminal_id: str, who: Person = Depends(machine_person),
                         held: Terminals = Depends(terminals)) -> dict[str, Any]:
    await held.close(terminal_id, who.id)
    return {"ok": True, "id": terminal_id}


@router.websocket("/machine/terminals/{terminal_id}/ws")
async def terminal_socket(websocket: WebSocket, terminal_id: str) -> None:
    """The terminal's bytes. Server to page: binary frames are output; text frames are JSON — `hello`
    (the terminal, sent first and followed by what it printed recently), `state`, `exit`, `repaint`
    (followed by the whole buffer), `closed` and `error`. Page to server: binary frames are keystrokes;
    text frames are JSON — `{"type": "input", "data": "…"}` or `{"type": "resize", "cols", "rows"}`."""
    who = await admit(websocket)
    if who is None:
        return
    try:
        terminal = terminals_of(websocket.app).get(terminal_id, who.id)
    except NotFound:
        await websocket.close(code=4404, reason="That terminal is closed.")
        return
    listener = terminal.attach()
    # Taken with no await since attaching, so nothing printed in between is lost or sent twice.
    snapshot = bytes(terminal.buffer)

    async def outgoing() -> None:
        await _send_json(websocket, {"type": "hello", "terminal": terminal.json()})
        if snapshot:
            await websocket.send_bytes(snapshot)
        if terminal.status == "exited":
            await _send_json(websocket, {"type": "exit", "code": terminal.exit_code})
        while True:
            kind, payload = await listener.queue.get()
            if kind == "out":
                await websocket.send_bytes(payload)
            elif kind == "repaint":
                await _send_json(websocket, {"type": "repaint"})
                await websocket.send_bytes(bytes(terminal.buffer))
            else:
                await _send_json(websocket, payload)
                if payload.get("type") == "closed":
                    await websocket.close(code=1000, reason="The terminal was closed.")
                    return

    async def incoming() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            try:
                if message.get("bytes"):
                    terminal.write(message["bytes"])
                elif message.get("text"):
                    control = json.loads(message["text"])
                    if control.get("type") == "input":
                        terminal.write(str(control.get("data", "")).encode("utf-8"))
                    elif control.get("type") == "resize":
                        terminal.resize(int(control.get("cols", terminal.cols)), int(control.get("rows", terminal.rows)))
            except Refused as refused:
                await _send_json(websocket, {"type": "error", "message": str(refused)})
            except (ValueError, TypeError, AttributeError):
                await _send_json(websocket, {"type": "error", "message": "That message was not understood."})

    await _pump(websocket, outgoing(), incoming())
    terminal.detach(listener)


async def _pump(websocket: WebSocket, *sides: Any) -> None:
    """Run both directions until either ends — the page went away, or the terminal did."""
    tasks = [asyncio.create_task(side) for side in sides]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
        with contextlib.suppress(RuntimeError):
            await websocket.close()


# ── run configurations ───────────────────────────────────────────

Kind = Literal["run", "debug"]
Language = Literal["python", "node", "shell"]


class RunConfigIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Kind = "run"
    language: Language = "shell"
    command: str = Field(min_length=1, max_length=terminals_service.MAX_COMMAND)
    args: list[str] = Field(default_factory=list, max_length=terminals_service.MAX_ARGS)
    cwd: str = Field(default="", max_length=1000)
    env: dict[str, str] = Field(default_factory=dict)


class RunConfigPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: Language | None = None
    command: str | None = Field(default=None, min_length=1, max_length=terminals_service.MAX_COMMAND)
    args: list[str] | None = Field(default=None, max_length=terminals_service.MAX_ARGS)
    cwd: str | None = Field(default=None, max_length=1000)
    #: A patch: a value sets the variable, null removes it, and one not named keeps its value.
    env: dict[str, str | None] | None = None


@router.get("/projects/{pid}/run-configs")
async def run_configs(pid: str, kind: Kind | None = None, limit: int = Query(default=100, ge=1),
                      offset: int = Query(default=0, ge=0), _: Person = Depends(machine_person),
                      open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """A project's run and debug configurations. Environment values never leave: only their names."""
    return await RunConfigService(open_session).listed(pid, kind=kind, limit=min(limit, MAX_LIST), offset=offset)


@router.post("/projects/{pid}/run-configs", status_code=201)
async def add_run_config(pid: str, body: RunConfigIn, request: Request, who: Person = Depends(machine_person),
                         open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await RunConfigService(open_session).create(
        pid, name=body.name, kind=body.kind, language=body.language, command=body.command, args=body.args,
        cwd=body.cwd, env=body.env, who=who, ip=_ip(request))


@router.get("/projects/{pid}/run-configs/detect")
async def detect_run_configs(pid: str, _: Person = Depends(machine_person),
                             open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """What the checkout suggests running — read from its files, never run. A person saves the ones they
    want; each says where it was read from, and whether the project already has it."""
    return await suggestions(open_session, pid)


async def _owned(open_session: AsyncSession, pid: str, config_id: int) -> None:
    config, _ = await RunConfigService(open_session).one(config_id)
    if config.project_id != pid:
        raise NotFound(f"run configuration {config_id}")


@router.patch("/projects/{pid}/run-configs/{config_id}")
async def change_run_config(pid: str, config_id: int, body: RunConfigPatch, request: Request,
                            who: Person = Depends(machine_person),
                            open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    await _owned(open_session, pid, config_id)
    return await RunConfigService(open_session).update(
        config_id, name=body.name, language=body.language, command=body.command, args=body.args, cwd=body.cwd,
        env=body.env, who=who, ip=_ip(request))


@router.delete("/projects/{pid}/run-configs/{config_id}")
async def remove_run_config(pid: str, config_id: int, request: Request, who: Person = Depends(machine_person),
                            open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    await _owned(open_session, pid, config_id)
    return await RunConfigService(open_session).delete(config_id, who, ip=_ip(request))


@router.post("/run-configs/{config_id}/start", status_code=201)
async def start_run_config(config_id: int, request: Request, body: SizeIn | None = None,
                           who: Person = Depends(machine_person), open_session: AsyncSession = Depends(session),
                           held: Terminals = Depends(terminals)) -> dict[str, Any]:
    """A terminal running the configuration's command in its folder with its environment. Its output
    streams through the terminal socket like any shell's; stop and restart are the terminal's."""
    size = body or SizeIn()
    return await RunConfigService(open_session).start(config_id, who, held, cols=size.cols, rows=size.rows,
                                                      ip=_ip(request))


# ── debugging ────────────────────────────────────────────────────

class DebugIn(BaseModel):
    runConfigId: int | None = None
    #: A file of the checkout — relative to `cwd`, or absolute inside the checkout.
    program: str | None = Field(default=None, max_length=4096)
    #: A Python module to run as `-m`, instead of a program.
    module: str | None = Field(default=None, max_length=200)
    args: list[str] = Field(default_factory=list, max_length=terminals_service.MAX_ARGS)
    cwd: str | None = Field(default=None, max_length=1000)
    language: Literal["python", "node"] | None = None
    #: The Workbench's breakpoints, by absolute path, set before the program's first line runs.
    breakpoints: dict[str, list[int]] = Field(default_factory=dict)


@router.post("/projects/{pid}/debug", status_code=201)
async def start_debug(pid: str, body: DebugIn, request: Request, who: Person = Depends(machine_person),
                      open_session: AsyncSession = Depends(session),
                      held: debugging.Debuggers = Depends(debuggers)) -> dict[str, Any]:
    """Launch a program under the debugger and answer once it is running (or already stopped at a
    breakpoint). Audited: who, what, where, with which interpreter."""
    started = await debugging.launch(open_session, held, who, pid, run_config_id=body.runConfigId,
                                     program=body.program, module=body.module, args=body.args, cwd=body.cwd,
                                     language=body.language, breakpoints=body.breakpoints, ip=_ip(request))
    return started.json()


@router.get("/projects/{pid}/debug")
async def project_debug_sessions(pid: str, who: Person = Depends(machine_person),
                                 held: debugging.Debuggers = Depends(debuggers)) -> list[dict[str, Any]]:
    """Your debug sessions in this project, oldest first — a handful at most, so the list needs no paging."""
    return [s.json(output=0) for s in held.mine(who.id, pid)]


@router.get("/debug/{session_id}")
async def debug_session(session_id: str, who: Person = Depends(machine_person),
                        held: debugging.Debuggers = Depends(debuggers)) -> dict[str, Any]:
    return held.get(session_id, who.id).json(output=debugging.OUTPUT_PIECES)


@router.post("/debug/{session_id}/{command}")
async def debug_command(session_id: str, command: str, arguments: dict[str, Any] = Body(default_factory=dict),
                        who: Person = Depends(machine_person),
                        held: debugging.Debuggers = Depends(debuggers)) -> dict[str, Any]:
    """One debugger command: continue, next, stepIn, stepOut, pause, terminate, restart, threads,
    stackTrace, scopes, variables, evaluate or setBreakpoints, with its arguments as the body."""
    if command not in COMMANDS:
        raise Refused(f"The debugger has no command called {command}.", status=404)
    found = held.get(session_id, who.id)
    try:
        return await found.command(command, arguments)
    except (KeyError, ValueError, TypeError) as wrong:
        raise Refused(f"{command} was sent without what it needs ({wrong}).", status=422) from wrong


@router.delete("/debug/{session_id}")
async def end_debug(session_id: str, who: Person = Depends(machine_person),
                    held: debugging.Debuggers = Depends(debuggers)) -> dict[str, Any]:
    await held.remove(session_id, who.id)
    return {"ok": True, "id": session_id}


@router.websocket("/debug/{session_id}/ws")
async def debug_socket(websocket: WebSocket, session_id: str) -> None:
    """A debug session's events, as JSON text frames: `state` (the whole session, sent first and on every
    change of status, stop or breakpoint), `output` ({category, text}) and `resync` (fetch the session
    again: this socket fell behind). The page sends nothing; commands go over HTTP."""
    who = await admit(websocket)
    if who is None:
        return
    try:
        found = debuggers_of(websocket.app).get(session_id, who.id)
    except NotFound:
        await websocket.close(code=4404, reason="That debug session is over.")
        return
    watcher = found.watch()
    first = found.json(output=debugging.OUTPUT_PIECES)

    async def outgoing() -> None:
        await _send_json(websocket, {"type": "state", "session": first})
        while True:
            await _send_json(websocket, await watcher.queue.get())

    async def incoming() -> None:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    await _pump(websocket, outgoing(), incoming())
    found.unwatch(watcher)

