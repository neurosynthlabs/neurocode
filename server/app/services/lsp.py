"""Hover, go to definition and a file's outline, from the language servers already on this machine.

A language server is the program an editor asks "what is this name?" — pyright (or basedpyright) for
Python, typescript-language-server for TypeScript and JavaScript, gopls for Go, rust-analyzer for Rust.
NeuroCode installs none of them: it looks for them — the project's own `node_modules/.bin` and virtual
environment first, then the PATH and the places Go and Rust put their tools — and when one is missing
the Workbench says which, and the line that installs it.

One server runs per (language, project root), started the first time the editor asks about a file there
and stopped after it has been idle a while (`IDLE_SECONDS`), when too many are running (`MAX_SERVERS`, the
least recently used goes), and when the API stops. The root is the nearest folder above the file that
the language marks as a project — a pyproject.toml, a tsconfig.json or package.json, a go.mod, a
Cargo.toml — never above the machine's roots.

The client speaks the Language Server Protocol over the server's stdin and stdout: `Content-Length`
framed JSON-RPC, requests matched to answers by id. It opens the file the editor shows with the text the
editor holds (unsaved typing included), so an answer is about what the person sees; the first question
about a file waits (briefly) for the server's first report on it, so it is not answered half-loaded.
Positions are 1-based lines and 1-based columns counted in UTF-16 code units — the editor's own, and the protocol's
default encoding. A server's requests back to the client are answered plainly (no configuration, no
progress UI); its notifications are read, and only a file's first diagnostics report is acted on.

Nothing here is driven by a model, and nothing a server answers is executed: it is text for a tooltip and
positions to open.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import shutil
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from . import machine
from .errors import Refused

#: Language servers running at once, across everyone; the least recently used is stopped for a new one.
MAX_SERVERS = 4
#: A server nobody asked for this long is stopped.
IDLE_SECONDS = 600.0
#: How often idle servers are looked for.
REAP_SECONDS = 60.0
#: How long a server may take to start (rust-analyzer reads a whole workspace), and to answer.
START_SECONDS = 45.0
ANSWER_SECONDS = 20.0
#: Files a server holds open at once; the oldest is closed when another is opened.
OPEN_FILES = 40
#: The most text a request may carry, the editor's own ceiling for a file.
MAX_TEXT = machine.MAX_TEXT
#: A hover's text beyond this is cut; symbols and definitions beyond these are left out.
HOVER_CHARS = 20_000
MAX_SYMBOLS = 2000
MAX_LOCATIONS = 50
#: What a server printed on stderr, kept to explain why it failed.
STDERR_LINES = 40
#: How long the first question about a file just opened waits for the server to have read it. A server
#: asked straight away answers from what it has loaded so far — tsserver says a name is `any` until its
#: project is read — so the first report it sends on the file is waited for, never longer than this.
SETTLE_SECONDS = 8.0

#: The protocol's SymbolKind numbers, as a person reads them.
SYMBOL_KINDS = {1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class", 6: "method", 7: "property",
                8: "field", 9: "constructor", 10: "enum", 11: "interface", 12: "function", 13: "variable",
                14: "constant", 15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object", 20: "key",
                21: "null", 22: "enum member", 23: "struct", 24: "event", 25: "operator", 26: "type parameter"}


@dataclass(frozen=True, slots=True)
class Spec:
    """A language and the servers that serve it, the first found wins. A candidate's first word is a
    program name looked for as described above, or an absolute path."""

    id: str
    language: str
    extensions: dict[str, str]            # extension → the protocol's languageId
    candidates: tuple[tuple[str, ...], ...]
    markers: tuple[str, ...]
    install: str
    #: Where the project installs this server itself: "node" (node_modules/.bin), "python" (its venv).
    local: tuple[str, ...] = ()


SPECS: list[Spec] = [
    Spec("python", "Python", {".py": "python", ".pyi": "python"},
         (("basedpyright-langserver", "--stdio"), ("pyright-langserver", "--stdio")),
         ("pyrightconfig.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"),
         "pip install pyright (or npm install -g pyright)", ("python", "node")),
    Spec("typescript", "TypeScript and JavaScript",
         {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "typescriptreact",
          ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascriptreact"},
         (("typescript-language-server", "--stdio"),),
         ("tsconfig.json", "jsconfig.json", "package.json"),
         "npm install -g typescript-language-server typescript", ("node",)),
    Spec("go", "Go", {".go": "go"}, (("gopls",),), ("go.work", "go.mod"),
         "go install golang.org/x/tools/gopls@latest"),
    Spec("rust", "Rust", {".rs": "rust"}, (("rust-analyzer",),), ("Cargo.toml",),
         "rustup component add rust-analyzer"),
]


def spec_for(path: Path) -> Spec | None:
    suffix = path.suffix.lower()
    return next((s for s in SPECS if suffix in s.extensions), None)


def _executable(file: Path) -> bool:
    return file.is_file() and os.access(file, os.X_OK)


def _bound(real: Path) -> Path | None:
    """The machine root a path is inside — the ceiling for every walk upwards."""
    inside = [r for r in machine._real_roots() if real == r or real.is_relative_to(r)]   # noqa: SLF001
    return max(inside, key=lambda r: len(r.parts)) if inside else None


def _upwards(start: Path, bound: Path | None) -> list[Path]:
    out = [start]
    here = start
    while here.parent != here and (bound is None or here != bound):
        here = here.parent
        if bound is not None and not (here == bound or here.is_relative_to(bound)):
            break
        out.append(here)
    return out


def root_for(file: Path, spec: Spec) -> Path:
    """The folder a server for this file is started in: the nearest above it that the language marks as
    a project, else the repository holding it, else the file's own folder — never above the roots."""
    folder = file.parent
    bound = _bound(folder)
    chain = _upwards(folder, bound)
    for here in chain:
        if any((here / m).exists() for m in spec.markers):
            return here
    for here in chain:
        if (here / ".git").exists():
            return here
    return folder


def find(spec: Spec, root: Path) -> list[str] | None:
    """The command that starts this language's server for this root, or None when none is here."""
    bound = _bound(root)
    for candidate in spec.candidates:
        name, args = candidate[0], list(candidate[1:])
        if os.path.isabs(name):
            if _executable(Path(name)):
                return [name, *args]
            continue
        for here in _upwards(root, bound) if spec.local else []:
            places = []
            if "node" in spec.local:
                places.append(here / "node_modules" / ".bin" / name)
            if "python" in spec.local:
                places += [here / env / "bin" / name for env in (".venv", "venv")]
            hit = next((p for p in places if _executable(p)), None)
            if hit is not None:
                return [str(hit), *args]
        found = shutil.which(name)
        if found is None:
            extra = [Path.home() / "go" / "bin" / name, Path.home() / ".cargo" / "bin" / name]
            found = next((str(p) for p in extra if _executable(p)), None)
        if found is not None:
            return [found, *args]
    return None


def uri_of(path: Path | str) -> str:
    return "file://" + quote(str(path))


def path_of(uri: str) -> str | None:
    parsed = urlparse(uri)
    return unquote(parsed.path) if parsed.scheme == "file" else None


def _markdown(contents: Any) -> str:
    """A hover's contents, which the protocol allows in three shapes, as one markdown text."""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        return "\n\n".join(t for t in (_markdown(c) for c in contents) if t)
    if isinstance(contents, dict):
        if "kind" in contents:
            return str(contents.get("value") or "")
        if "language" in contents:
            return f"```{contents['language']}\n{contents.get('value', '')}\n```"
    return ""


# ── one running server ────────────────────────────────────────────
class LanguageServer:
    def __init__(self, spec: Spec, root: Path, argv: list[str]) -> None:
        self.spec, self.root, self.argv = spec, root, argv
        self.key = (spec.id, str(root))
        self.id = f"{spec.id}:{abs(hash(self.key)) % 10 ** 10}"
        self.state = "starting"            # starting · ready · failed · stopped
        self.note = ""
        self.name = Path(argv[0]).name
        self.used = time.monotonic()
        self.started_at = time.time()
        self._proc: asyncio.subprocess.Process | None = None
        self._ids = itertools.count(1)
        self._waiting: dict[int, asyncio.Future[Any]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._said: deque[str] = deque(maxlen=STDERR_LINES)
        self._open: OrderedDict[str, tuple[int, str]] = OrderedDict()     # uri → (version, text)
        self._analysed: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self.capabilities: dict[str, Any] = {}

    # ── lifecycle ────────────────────────────────────────────────
    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.argv, cwd=self.root, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True)
        except OSError as failed:
            self._fail(f"{self.name} could not be started: {failed.strerror or failed}")
            raise Refused(self.note, status=502) from failed
        self._reader = asyncio.get_running_loop().create_task(self._read())
        self._stderr = asyncio.get_running_loop().create_task(self._drain())
        try:
            answer = await self.request("initialize", {
                "processId": os.getpid(), "clientInfo": {"name": "NeuroCode"},
                "rootUri": uri_of(self.root), "rootPath": str(self.root),
                "workspaceFolders": [{"uri": uri_of(self.root), "name": self.root.name}],
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                    "textDocument": {
                        "synchronization": {"dynamicRegistration": False, "didSave": False},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "definition": {"linkSupport": True},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "publishDiagnostics": {"relatedInformation": False},
                    },
                    "workspace": {"configuration": True, "workspaceFolders": True},
                    "window": {"workDoneProgress": True},
                },
            }, timeout=START_SECONDS, starting=True)
        except Refused as refused:
            self._fail(self._why(str(refused)))
            await self.stop()
            raise Refused(self.note, status=502) from refused
        self.capabilities = (answer or {}).get("capabilities") or {}
        await self.notify("initialized", {})
        self.state = "ready"
        self._ready.set()

    def _why(self, first: str) -> str:
        said = [ln for ln in self._said if ln.strip()][-3:]
        return f"{self.name} did not start: {first}" + (f" — it said: {' · '.join(said)}" if said else "")

    def _fail(self, note: str) -> None:
        self.state, self.note = "failed", note[:500]

    async def stop(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if self.state == "ready":
                with contextlib.suppress(Refused, TimeoutError, OSError):
                    await self.request("shutdown", None, timeout=3)
                with contextlib.suppress(Exception):
                    await self.notify("exit", None)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, 9)
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=3)
        for task in (self._reader, self._stderr):
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
        if self.state != "failed":
            self.state = "stopped"
        self._proc = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and self.state in ("starting", "ready")

    # ── the wire ─────────────────────────────────────────────────
    async def _send(self, message: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None or self._proc.returncode is not None:
            raise Refused(f"{self.name} is not running.", status=409)
        body = json.dumps({"jsonrpc": "2.0", **message}).encode()
        try:
            self._proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            await self._proc.stdin.drain()
        except (ConnectionError, BrokenPipeError) as gone:
            raise Refused(f"{self.name} has stopped.", status=409) from gone

    async def notify(self, method: str, params: Any) -> None:
        await self._send({"method": method, **({} if params is None else {"params": params})})

    async def request(self, method: str, params: Any, *, timeout: float = ANSWER_SECONDS,
                      starting: bool = False) -> Any:
        if not starting and not self._ready.is_set():
            raise Refused(f"{self.name} is not ready.", status=409)
        n = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._waiting[n] = future
        try:
            await self._send({"id": n, "method": method, **({} if params is None else {"params": params})})
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as slow:
            with contextlib.suppress(Refused):
                await self.notify("$/cancelRequest", {"id": n})
            raise Refused(f"{self.name} did not answer {method} within {int(timeout)} seconds.", status=504) from slow
        finally:
            self._waiting.pop(n, None)

    async def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        try:
            while True:
                length = 0
                while True:
                    line = await stream.readline()
                    if not line:
                        return
                    text = line.decode("ascii", "replace").strip()
                    if not text:
                        break
                    name, _, value = text.partition(":")
                    if name.lower() == "content-length":
                        length = int(value.strip())
                if length <= 0:
                    continue
                message = json.loads(await stream.readexactly(length))
                if isinstance(message, dict):
                    await self._dispatch(message)
        except (asyncio.IncompleteReadError, ValueError, ConnectionError):
            return
        finally:
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception(Refused(f"{self.name} has stopped.", status=409))
            if self.state in ("starting", "ready"):
                self._fail(self._why("it exited") if self.state == "starting" else f"{self.name} exited.")

    async def _drain(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while line := await self._proc.stderr.readline():
            self._said.append(line.decode("utf-8", "replace").rstrip()[:300])

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message) and "method" not in message:
            future = self._waiting.get(message["id"]) if isinstance(message["id"], int) else None
            if future is None or future.done():
                return
            if message.get("error"):
                error = message["error"] or {}
                future.set_exception(Refused(f"{self.name}: {error.get('message') or 'refused the request'}",
                                             status=502))
            else:
                future.set_result(message.get("result"))
            return
        if "id" in message and "method" in message:
            # A request from the server. Answered plainly: this client keeps no settings and shows no progress.
            method = message["method"]
            if method == "workspace/configuration":
                items = (message.get("params") or {}).get("items") or []
                reply: dict[str, Any] = {"result": [None] * len(items)}
            elif method == "workspace/workspaceFolders":
                reply = {"result": [{"uri": uri_of(self.root), "name": self.root.name}]}
            elif method in ("window/workDoneProgress/create", "client/registerCapability",
                            "client/unregisterCapability", "window/showMessageRequest"):
                reply = {"result": None}
            else:
                reply = {"error": {"code": -32601, "message": f"{method} is not supported by NeuroCode"}}
            with contextlib.suppress(Refused):
                await self._send({"id": message["id"], **reply})
        elif message.get("method") == "textDocument/publishDiagnostics":
            # The first report on a file just opened says the server has read it, and its project.
            uri = (message.get("params") or {}).get("uri")
            waiting = self._analysed.get(uri) if isinstance(uri, str) else None
            if waiting is not None:
                waiting.set()
        # Other notifications (logs, progress) are read and let go.

    # ── what the editor asks ─────────────────────────────────────
    async def sync(self, file: Path, text: str | None, language_id: str) -> str:
        """Make the server hold `file` with `text` (the editor's, or the file's on disk). Returns its uri."""
        uri = uri_of(file)
        if text is None:
            try:
                data = file.read_bytes()
            except OSError as failed:
                raise Refused(f"{file.name} could not be read: {failed.strerror or failed}.", status=409) from failed
            if len(data) > MAX_TEXT:
                raise Refused(f"{file.name} is larger than the editor opens.", status=413)
            text = data.decode("utf-8", "replace")
        async with self._lock:
            held = self._open.get(uri)
            if held is None:
                self._analysed[uri] = asyncio.Event()
                await self.notify("textDocument/didOpen", {"textDocument": {
                    "uri": uri, "languageId": language_id, "version": 1, "text": text}})
                self._open[uri] = (1, text)
                while len(self._open) > OPEN_FILES:
                    old, _ = self._open.popitem(last=False)
                    self._analysed.pop(old, None)
                    await self.notify("textDocument/didClose", {"textDocument": {"uri": old}})
            elif held[1] != text:
                version = held[0] + 1
                await self.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": version},
                                                             "contentChanges": [{"text": text}]})
                self._open[uri] = (version, text)
                self._open.move_to_end(uri)
            else:
                self._open.move_to_end(uri)
            analysed = self._analysed.get(uri)
        if analysed is not None and not analysed.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(analysed.wait(), timeout=SETTLE_SECONDS)
            # Waited once: a server that never reports on a file is not waited for again.
            analysed.set()
        return uri

    def json(self) -> dict[str, Any]:
        return {"id": self.id, "language": self.spec.language, "languageId": self.spec.id, "server": self.name,
                "root": str(self.root), "state": self.state, "note": self.note,
                "idleSeconds": int(time.monotonic() - self.used), "openFiles": len(self._open)}


# ── every running server ──────────────────────────────────────────
class LanguageServers:
    """The servers this API runs. Held on `app.state`; stopped when idle, when crowded, and with the API."""

    def __init__(self) -> None:
        self._all: dict[tuple[str, str], LanguageServer] = {}
        self._starting: dict[tuple[str, str], asyncio.Task[LanguageServer]] = {}
        self._reaper: asyncio.Task[None] | None = None

    def running(self) -> list[LanguageServer]:
        return sorted(self._all.values(), key=lambda s: s.used, reverse=True)

    def get(self, server_id: str) -> LanguageServer:
        from ..repositories import NotFound

        found = next((s for s in self._all.values() if s.id == server_id), None)
        if found is None:
            raise NotFound(f"language server {server_id}")
        return found

    def held(self, spec: Spec, root: Path) -> LanguageServer | None:
        return self._all.get((spec.id, str(root)))

    async def server(self, spec: Spec, root: Path) -> LanguageServer:
        """The server for this root, started when it is not running — once, however many ask at once."""
        key = (spec.id, str(root))
        found = self._all.get(key)
        if found is not None and found.alive:
            if found.state == "ready":
                found.used = time.monotonic()
                return found
        elif found is not None:
            self._all.pop(key, None)
        pending = self._starting.get(key)
        if pending is None:
            argv = find(spec, root)
            if argv is None:
                raise Refused(missing_words(spec), status=409)
            pending = asyncio.get_running_loop().create_task(self._start(spec, root, argv))
            self._starting[key] = pending
        try:
            server = await asyncio.shield(pending)
        finally:
            if pending.done():
                self._starting.pop(key, None)
        server.used = time.monotonic()
        return server

    async def _start(self, spec: Spec, root: Path, argv: list[str]) -> LanguageServer:
        while len([s for s in self._all.values() if s.alive]) >= MAX_SERVERS:
            oldest = min(self._all.values(), key=lambda s: s.used)
            self._all.pop(oldest.key, None)
            await oldest.stop()
        server = LanguageServer(spec, root, argv)
        self._all[server.key] = server
        try:
            await server.start()
        except Refused:
            # The failed one is kept to say why, until someone asks again.
            raise
        self._watch()
        return server

    def _watch(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap())

    async def _reap(self) -> None:
        while self._all:
            await asyncio.sleep(REAP_SECONDS)
            await self.stop_idle()

    async def stop_idle(self, *, older_than: float = IDLE_SECONDS) -> int:
        now, stopped = time.monotonic(), 0
        for server in list(self._all.values()):
            if now - server.used >= older_than:
                self._all.pop(server.key, None)
                await server.stop()
                stopped += 1
        return stopped

    async def remove(self, server_id: str) -> None:
        server = self.get(server_id)
        self._all.pop(server.key, None)
        await server.stop()

    async def close_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for task in list(self._starting.values()):
            task.cancel()
        for server in list(self._all.values()):
            with contextlib.suppress(Exception):
                await server.stop()
        self._all.clear()
        self._starting.clear()


def missing_words(spec: Spec) -> str:
    names = " or ".join(c[0] for c in spec.candidates)
    return f"No language server for {spec.language} on this machine — install {names} ({spec.install})."


# ── the four questions, in the editor's coordinates ───────────────
@dataclass(frozen=True, slots=True)
class Opened:
    file: Path
    spec: Spec
    root: Path


def resolve(path: str) -> Opened:
    """A file the editor shows, inside the roots, with the language that serves it. Blocking."""
    file = machine.inside(path)
    if not file.is_file():
        raise Refused(f"{path} is not a file.", status=404)
    spec = spec_for(file)
    if spec is None:
        raise Refused(f"Hover and go to definition cover {', '.join(s.language for s in SPECS)} — not "
                      f"{file.suffix or file.name} files.", status=422)
    return Opened(file=file, spec=spec, root=root_for(file, spec))


def status(held: LanguageServers, path: str) -> dict[str, Any]:
    """What the status bar says about the file shown. Starts nothing. Blocking (it looks on disk)."""
    file = machine.inside(path)
    spec = spec_for(file)
    if spec is None:
        return {"language": None, "state": "none", "server": None, "root": None,
                "message": f"No language server covers {file.suffix or file.name} files."}
    root = root_for(file, spec)
    running = held.held(spec, root)
    if running is not None and running.state in ("starting", "ready", "failed"):
        words = {"starting": f"{running.name} starting…", "ready": f"{running.name} ready",
                 "failed": running.note or f"{running.name} failed"}[running.state]
        return {"language": spec.language, "state": running.state, "server": running.name, "root": str(root),
                "message": words}
    argv = find(spec, root)
    if argv is None:
        return {"language": spec.language, "state": "missing", "server": None, "root": str(root),
                "message": missing_words(spec)}
    name = Path(argv[0]).name
    return {"language": spec.language, "state": "available", "server": name, "root": str(root),
            "message": f"{name} starts when you hover or go to a definition"}


def _position(line: int, col: int) -> dict[str, int]:
    return {"line": max(0, line - 1), "character": max(0, col - 1)}


def _place(uri: str, rng: dict[str, Any]) -> dict[str, Any]:
    start, end = (rng or {}).get("start") or {}, (rng or {}).get("end") or {}
    found = path_of(uri)
    openable = False
    if found is not None:
        with contextlib.suppress(Refused, OSError):
            found = str(machine.inside(found))
            openable = True
    return {"path": found or uri, "openable": openable,
            "line": int(start.get("line", 0)) + 1, "col": int(start.get("character", 0)) + 1,
            "endLine": int(end.get("line", start.get("line", 0))) + 1,
            "endCol": int(end.get("character", start.get("character", 0))) + 1}


async def _ready(held: LanguageServers, path: str, text: str | None) -> tuple[LanguageServer, str]:
    opened = await asyncio.to_thread(resolve, path)
    if text is not None and len(text.encode("utf-8")) > MAX_TEXT:
        raise Refused("The text is larger than the editor opens.", status=413)
    server = await held.server(opened.spec, opened.root)
    uri = await server.sync(opened.file, text, opened.spec.extensions[opened.file.suffix.lower()])
    return server, uri


async def hover(held: LanguageServers, path: str, line: int, col: int, text: str | None) -> dict[str, Any]:
    server, uri = await _ready(held, path, text)
    answer = await server.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                         "position": _position(line, col)})
    words = _markdown((answer or {}).get("contents")).strip() if isinstance(answer, dict) else ""
    if len(words) > HOVER_CHARS:
        words = words[:HOVER_CHARS - 1] + "…"
    rng = (answer or {}).get("range") if isinstance(answer, dict) else None
    return {"server": server.name, "markdown": words or None, "range": _place(uri, rng) if rng else None}


async def definition(held: LanguageServers, path: str, line: int, col: int, text: str | None) -> dict[str, Any]:
    server, uri = await _ready(held, path, text)
    answer = await server.request("textDocument/definition", {"textDocument": {"uri": uri},
                                                              "position": _position(line, col)})
    raw = answer if isinstance(answer, list) else [answer] if isinstance(answer, dict) else []
    places = []
    for item in raw[:MAX_LOCATIONS]:
        if not isinstance(item, dict):
            continue
        if "targetUri" in item:              # a LocationLink: the name itself is the selection range
            places.append(_place(item["targetUri"], item.get("targetSelectionRange") or item.get("targetRange")))
        elif "uri" in item:
            places.append(_place(item["uri"], item.get("range")))
    return {"server": server.name, "locations": places}


def _flatten(items: list[Any], depth: int, out: list[dict[str, Any]]) -> None:
    for item in items:
        if len(out) >= MAX_SYMBOLS or not isinstance(item, dict):
            return
        if "location" in item:               # SymbolInformation: flat, with a container name
            rng = (item.get("location") or {}).get("range") or {}
            container = item.get("containerName")
            d = 1 if container else 0
        else:
            rng = item.get("selectionRange") or item.get("range") or {}
            d = depth
        start = rng.get("start") or {}
        out.append({"name": str(item.get("name") or ""), "detail": item.get("detail") or None,
                    "kind": SYMBOL_KINDS.get(int(item.get("kind") or 0), "symbol"), "depth": d,
                    "line": int(start.get("line", 0)) + 1, "col": int(start.get("character", 0)) + 1})
        children = item.get("children")
        if isinstance(children, list) and children:
            _flatten(children, depth + 1, out)


async def symbols(held: LanguageServers, path: str, text: str | None) -> dict[str, Any]:
    server, uri = await _ready(held, path, text)
    answer = await server.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    out: list[dict[str, Any]] = []
    _flatten(answer if isinstance(answer, list) else [], 0, out)
    return {"server": server.name, "symbols": out, "capped": len(out) >= MAX_SYMBOLS}
