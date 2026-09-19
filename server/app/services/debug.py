"""Debugging a project's program from the Workbench: breakpoints, stepping, the call stack, variables.

Python is debugged through debugpy, speaking the Debug Adapter Protocol: `python -m debugpy.adapter`
runs from the API's own environment, and it launches the program with the *project's* interpreter (its
`.venv` when it has one), so the program sees its own packages while the adapter needs none of them.
The order the protocol requires is kept exactly — initialize, launch, wait for `initialized`, set the
breakpoints, configurationDone — because a breakpoint set after configurationDone can miss the very
lines a short script runs first.

Node is debugged through its own inspector: `node --inspect-brk` on a loopback port chosen by the
system, and the Chrome DevTools Protocol over a WebSocket. It stops before the first line, the
breakpoints are set, and it is released; the first stop (the one `--inspect-brk` makes) is resumed
without troubling anyone. What the two protocols answer is turned into one shape here, so the Debug
panel draws a Python frame and a Node frame the same way.

A debug session lives in this process and belongs to the person who started it; another person's
session is as absent as one that never existed. Nothing here is driven by a model.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import re
import shlex
import shutil
import signal
import sys
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .errors import Refused

#: One person's debug sessions at once.
MAX_PER_PERSON = 4
#: How long a debugger is given to answer one request, and to start.
ANSWER_SECONDS = 15.0
START_SECONDS = 40.0
#: What a session remembers of its program's output, in pieces and in characters.
OUTPUT_PIECES = 2000
OUTPUT_CHARS = 512 * 1024
#: Frames fetched when the program stops; the panel pages further with stackTrace.
FRAMES = 60
#: Children listed for one variable at once.
MAX_CHILDREN = 500
#: Output categories the panel shows. debugpy also sends telemetry, which is its own business.
SHOWN = frozenset({"console", "stdout", "stderr", "important"})
QUEUE_EVENTS = 2000
#: Lines the Node inspector prints about itself, which are not the program's output.
INSPECTOR_CHATTER = re.compile(r"^(Debugger listening on |For help, see: |Debugger attached\.|"
                               r"Waiting for the debugger to disconnect\.\.\.)")
LISTENING = re.compile(r"Debugger listening on (ws://\S+)")


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(at: datetime | None) -> str | None:
    return at.isoformat(timespec="seconds") if at else None


class Watcher:
    """One open socket on a session: the events it has not sent yet."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(QUEUE_EVENTS)

    def offer(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # A socket this far behind is sent the whole state again rather than the backlog.
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "resync"})


class DebugSession:
    """What both debuggers share: who owns it, what it runs, its state, its output and its watchers.

    A language fills in the protocol: `_launch`, `_command` and `_close`."""

    language = ""

    def __init__(self, *, owner: str, project_id: str, name: str, program: str | None, module: str | None,
                 args: list[str], cwd: Path, env: dict[str, str], interpreter: str,
                 breakpoints: dict[str, list[int]], run_config_id: int | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.owner = owner
        self.project_id = project_id
        self.name = name
        self.program = program
        self.module = module
        self.args = args
        self.cwd = cwd
        self.env = env
        self.interpreter = interpreter
        self.run_config_id = run_config_id
        #: What the person asked for, by absolute path, and what the debugger made of each line.
        self.breakpoints: dict[str, list[int]] = {p: sorted(set(lines)) for p, lines in breakpoints.items()}
        self.verified: dict[str, list[dict[str, Any]]] = {}
        self.status = "starting"
        self.note = ""
        self.stopped: dict[str, Any] | None = None
        self.frames: list[dict[str, Any]] = []
        self.output: deque[dict[str, str]] = deque(maxlen=OUTPUT_PIECES)
        self._output_chars = 0
        self.exit_code: int | None = None
        self.started_at = _now()
        self.ended_at: datetime | None = None
        self.watchers: set[Watcher] = set()
        self.restarts = 0

    # ── the parts a language provides ─────────────────────────────
    async def _launch(self) -> None:
        raise NotImplementedError

    async def _command(self, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def _close(self) -> None:
        raise NotImplementedError

    # ── life ──────────────────────────────────────────────────────
    async def start(self) -> None:
        try:
            await asyncio.wait_for(self._launch(), timeout=START_SECONDS)
        except TimeoutError as slow:
            await self._close()
            self._end("failed", "The debugger did not start in time.")
            raise Refused("The debugger did not start in time.", status=504) from slow
        except Refused as refused:
            await self._close()
            self._end("failed", str(refused))
            raise
        if self.status == "starting":
            self._set_status("running")

    async def restart(self) -> None:
        await self.terminate()
        self.status, self.note, self.stopped, self.frames = "starting", "", None, []
        self.exit_code, self.ended_at = None, None
        self.restarts += 1
        self.say("console", "-- restarted --\n")
        self._broadcast_state()
        await self.start()

    async def terminate(self) -> None:
        if self.status in ("ended", "failed"):
            await self._close()
            return
        with contextlib.suppress(Refused, TimeoutError):
            await asyncio.wait_for(self._command("terminate", {}), timeout=5)
        await self._close()
        self._end("ended", self.note)

    async def command(self, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One request from the panel. Stepping and reading need a live session; reading frames and
        variables needs a stopped one — the debugger's own answer would say the same less plainly."""
        if command == "terminate":
            await self.terminate()
            return {"ok": True}
        if command == "restart":
            await self.restart()
            return {"ok": True}
        if command == "setBreakpoints":
            return await self.set_breakpoints(str(arguments.get("path", "")), arguments.get("lines") or [])
        if self.status not in ("running", "paused"):
            raise Refused("The program is no longer running.", status=409)
        if command in ("stackTrace", "scopes", "variables") and self.status != "paused":
            raise Refused("The program is running. Pause it, or wait for a breakpoint, to read its state.",
                          status=409)
        return await self._command(command, arguments)

    async def set_breakpoints(self, path: str, lines: list[Any]) -> dict[str, Any]:
        if not path or not os.path.isabs(path):
            raise Refused("A breakpoint names a file by its absolute path.", status=422)
        wanted = sorted({int(n) for n in lines if isinstance(n, int) and 0 < n < 10_000_000})
        if wanted:
            self.breakpoints[path] = wanted
        else:
            self.breakpoints.pop(path, None)
        if self.status in ("running", "paused"):
            result = await self._command("setBreakpoints", {"path": path, "lines": wanted})
            self.verified[path] = result["breakpoints"]
        else:
            self.verified[path] = [{"line": n, "verified": False, "message": None} for n in wanted]
        self._broadcast_state()
        return {"path": path, "breakpoints": self.verified.get(path, [])}

    # ── state and events ──────────────────────────────────────────
    def say(self, category: str, text: str) -> None:
        if not text or category not in SHOWN:
            return
        piece = {"category": category, "text": text[:OUTPUT_CHARS]}
        if len(self.output) == self.output.maxlen and self.output:
            self._output_chars -= len(self.output[0]["text"])
        self.output.append(piece)
        self._output_chars += len(piece["text"])
        while self._output_chars > OUTPUT_CHARS and len(self.output) > 1:
            self._output_chars -= len(self.output.popleft()["text"])
        self._broadcast({"type": "output", **piece})

    def _set_status(self, status: str) -> None:
        self.status = status
        self._broadcast_state()

    def _paused(self, stopped: dict[str, Any], frames: list[dict[str, Any]]) -> None:
        self.stopped, self.frames = stopped, frames
        self._set_status("paused")

    def _resumed(self) -> None:
        if self.status == "paused":
            self.stopped, self.frames = None, []
            self._set_status("running")

    def _end(self, status: str, note: str = "") -> None:
        if self.status in ("ended", "failed") and self.ended_at is not None:
            return
        self.status, self.note = status, note
        self.stopped, self.frames = None, []
        self.ended_at = _now()
        self._broadcast_state()

    def _broadcast(self, event: dict[str, Any]) -> None:
        for watcher in list(self.watchers):
            watcher.offer(event)

    def _broadcast_state(self) -> None:
        # Output travels as its own events; a change of state does not resend what was already said.
        self._broadcast({"type": "state", "session": self.json(output=0)})

    def watch(self) -> Watcher:
        watcher = Watcher()
        self.watchers.add(watcher)
        return watcher

    def unwatch(self, watcher: Watcher) -> None:
        self.watchers.discard(watcher)

    def json(self, *, output: int = 400) -> dict[str, Any]:
        shown = list(self.output)[-output:] if output else []
        return {"id": self.id, "projectId": self.project_id, "name": self.name, "language": self.language,
                "program": self.program, "module": self.module, "args": self.args, "cwd": str(self.cwd),
                "interpreter": self.interpreter, "runConfigId": self.run_config_id, "status": self.status,
                "note": self.note, "stopped": self.stopped, "frames": self.frames, "exitCode": self.exit_code,
                "breakpoints": {p: self.verified.get(p) or [{"line": n, "verified": False, "message": None}
                                                           for n in lines]
                                for p, lines in self.breakpoints.items()},
                "output": shown, "startedAt": _stamp(self.started_at), "endedAt": _stamp(self.ended_at),
                "restarts": self.restarts}


# ── Python: the Debug Adapter Protocol, through debugpy ──────────

def _frame(raw: dict[str, Any]) -> dict[str, Any]:
    source = raw.get("source") or {}
    return {"id": raw.get("id"), "name": raw.get("name", ""), "path": source.get("path"),
            "line": raw.get("line"), "column": raw.get("column"),
            "hint": raw.get("presentationHint")}


def _variable(raw: dict[str, Any]) -> dict[str, Any]:
    return {"name": raw.get("name", ""), "value": raw.get("value", ""), "type": raw.get("type"),
            "ref": raw.get("variablesReference", 0), "named": raw.get("namedVariables"),
            "indexed": raw.get("indexedVariables")}


class PythonSession(DebugSession):
    """debugpy's adapter on stdin/stdout, spoken to in DAP frames."""

    language = "python"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._proc: asyncio.subprocess.Process | None = None
        self._seq = itertools.count(1)
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._initialized = asyncio.Event()
        self._adapter_said: deque[str] = deque(maxlen=20)
        self._tasks: set[asyncio.Task[Any]] = set()

    async def _launch(self) -> None:
        self._initialized = asyncio.Event()
        self._seq = itertools.count(1)
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "debugpy.adapter", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={k: v for k, v in os.environ.items() if not k.startswith("NEUROCODE_")})
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._drain_stderr())
        await self._request("initialize", {
            "clientID": "neurocode", "clientName": "NeuroCode", "adapterID": "debugpy", "pathFormat": "path",
            "linesStartAt1": True, "columnsStartAt1": True, "supportsVariableType": True,
            "supportsVariablePaging": True, "supportsRunInTerminalRequest": False,
            "supportsStartDebuggingRequest": False})
        target: dict[str, Any] = {"module": self.module} if self.module else {"program": self.program}
        launch = asyncio.create_task(self._request("launch", {
            "request": "launch", "type": "debugpy", "name": self.name, **target, "args": self.args,
            "cwd": str(self.cwd), "env": self.env, "python": [self.interpreter], "console": "internalConsole",
            "redirectOutput": True, "justMyCode": True, "stopOnEntry": False, "subProcess": False,
            "showReturnValue": True}, timeout=START_SECONDS))
        initialized = asyncio.create_task(self._initialized.wait())
        done, _ = await asyncio.wait({launch, initialized}, return_when=asyncio.FIRST_COMPLETED)
        if launch in done and not initialized.done():
            initialized.cancel()
            launch.result()           # raises the adapter's refusal, if that is why it answered first
            raise Refused("The debugger answered the launch without getting ready.", status=502)
        for path, lines in self.breakpoints.items():
            answer = await self._request("setBreakpoints", {"source": {"path": path},
                                                            "breakpoints": [{"line": n} for n in lines]})
            self.verified[path] = self._verified(answer, lines)
        await self._request("setExceptionBreakpoints", {"filters": ["uncaught"]})
        await self._request("configurationDone")
        await launch

    @staticmethod
    def _verified(answer: dict[str, Any], lines: list[int]) -> list[dict[str, Any]]:
        got = answer.get("breakpoints") or []
        return [{"line": (got[i].get("line") if i < len(got) else None) or n,
                 "verified": bool(got[i].get("verified")) if i < len(got) else False,
                 "message": got[i].get("message") if i < len(got) else None} for i, n in enumerate(lines)]

    async def _command(self, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if command == "setBreakpoints":
            lines = list(arguments["lines"])
            answer = await self._request("setBreakpoints", {"source": {"path": arguments["path"]},
                                                            "breakpoints": [{"line": n} for n in lines]})
            return {"breakpoints": self._verified(answer, lines)}
        if command == "terminate":
            await self._request("terminate", {}, timeout=5)
            return {"ok": True}
        if command in ("continue", "next", "stepIn", "stepOut", "pause"):
            thread = await self._thread(arguments)
            await self._request(command, {"threadId": thread})
            if command != "pause":
                self._resumed()
            return {"ok": True}
        if command == "threads":
            answer = await self._request("threads")
            return {"threads": [{"id": t.get("id"), "name": t.get("name", "")} for t in answer.get("threads", [])]}
        if command == "stackTrace":
            thread = await self._thread(arguments)
            answer = await self._request("stackTrace", {
                "threadId": thread, "startFrame": max(0, int(arguments.get("startFrame", 0))),
                "levels": max(1, min(int(arguments.get("levels", FRAMES)), 500))})
            return {"frames": [_frame(f) for f in answer.get("stackFrames", [])],
                    "total": answer.get("totalFrames")}
        if command == "scopes":
            answer = await self._request("scopes", {"frameId": int(arguments["frameId"])})
            return {"scopes": [{"name": s.get("name", ""), "ref": s.get("variablesReference", 0),
                                "expensive": bool(s.get("expensive"))} for s in answer.get("scopes", [])]}
        if command == "variables":
            answer = await self._request("variables", {
                "variablesReference": int(arguments["ref"]), "start": max(0, int(arguments.get("start", 0))),
                "count": max(1, min(int(arguments.get("count", MAX_CHILDREN)), MAX_CHILDREN))})
            return {"variables": [_variable(v) for v in answer.get("variables", [])]}
        if command == "evaluate":
            request: dict[str, Any] = {"expression": str(arguments["expression"]),
                                       "context": arguments.get("context", "repl")}
            if arguments.get("frameId") is not None:
                request["frameId"] = int(arguments["frameId"])
            try:
                answer = await self._request("evaluate", request)
            except Refused as failed:
                # A watch that does not evaluate is an answer, not an error: say why, where it sits.
                return {"result": str(failed), "type": "error", "ref": 0}
            return {"result": answer.get("result", ""), "type": answer.get("type"),
                    "ref": answer.get("variablesReference", 0)}
        raise Refused(f"The debugger has no command called {command}.", status=404)

    async def _thread(self, arguments: dict[str, Any]) -> int:
        if arguments.get("threadId") is not None:
            return int(arguments["threadId"])
        if self.stopped and self.stopped.get("threadId") is not None:
            return int(self.stopped["threadId"])
        answer = await self._request("threads")
        threads = answer.get("threads") or []
        if not threads:
            raise Refused("The program has no threads to act on yet.", status=409)
        return int(threads[0]["id"])

    # ── the wire ──────────────────────────────────────────────────
    async def _request(self, command: str, arguments: dict[str, Any] | None = None, *,
                       timeout: float = ANSWER_SECONDS) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.returncode is not None:
            raise Refused("The debugger is not running.", status=409)
        seq = next(self._seq)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiting[seq] = future
        message = {"seq": seq, "type": "request", "command": command, "arguments": arguments or {}}
        body = json.dumps(message).encode()
        try:
            self._proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            await self._proc.stdin.drain()
            answer = await asyncio.wait_for(future, timeout=timeout)
        except (ConnectionError, BrokenPipeError) as gone:
            raise Refused("The debugger has stopped.", status=409) from gone
        except TimeoutError as slow:
            raise Refused(f"The debugger did not answer {command} in time.", status=504) from slow
        finally:
            self._waiting.pop(seq, None)
        if not answer.get("success"):
            error = ((answer.get("body") or {}).get("error") or {}).get("format")
            raise Refused(answer.get("message") or error or f"The debugger refused {command}.", status=409)
        return answer.get("body") or {}

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
                self._dispatch(message)
        except (asyncio.IncompleteReadError, ValueError, ConnectionError):
            return
        finally:
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception(Refused("The debugger has stopped.", status=409))
            if self.status in ("starting", "running", "paused"):
                self._end("ended", self.note)

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while line := await self._proc.stderr.readline():
            self._adapter_said.append(line.decode("utf-8", "replace").rstrip())

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "response":
            future = self._waiting.get(int(message.get("request_seq", -1)))
            if future is not None and not future.done():
                future.set_result(message)
        elif kind == "event":
            self._event(str(message.get("event")), message.get("body") or {})
        elif kind == "request":
            # A reverse request (runInTerminal, startDebugging) this client said it does not support.
            self._spawn(self._refuse_reverse(message))

    async def _refuse_reverse(self, message: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        body = json.dumps({"seq": next(self._seq), "type": "response", "request_seq": message.get("seq"),
                           "command": message.get("command"), "success": False,
                           "message": "Not supported by NeuroCode"}).encode()
        with contextlib.suppress(Exception):
            self._proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            await self._proc.stdin.drain()

    def _event(self, event: str, body: dict[str, Any]) -> None:
        if event == "initialized":
            self._initialized.set()
        elif event == "output":
            self.say(str(body.get("category") or "console"), str(body.get("output") or ""))
        elif event == "stopped":
            self._spawn(self._on_stopped(body))
        elif event == "continued":
            self._resumed()
        elif event == "exited":
            self.exit_code = body.get("exitCode")
        elif event == "terminated":
            self._end("ended", self.note)
            self._spawn(self._close())
        elif event == "breakpoint":
            changed = body.get("breakpoint") or {}
            path = ((changed.get("source") or {}).get("path")) or ""
            for known in self.verified.get(path, []):
                if known["line"] == changed.get("line"):
                    known["verified"] = bool(changed.get("verified"))
            self._broadcast_state()

    async def _on_stopped(self, body: dict[str, Any]) -> None:
        stopped = {"reason": body.get("reason", ""), "threadId": body.get("threadId"),
                   "description": body.get("description") or body.get("text") or ""}
        frames: list[dict[str, Any]] = []
        if body.get("threadId") is not None:
            with contextlib.suppress(Refused):
                answer = await self._request("stackTrace", {"threadId": body["threadId"], "startFrame": 0,
                                                            "levels": FRAMES})
                frames = [_frame(f) for f in answer.get("stackFrames", [])]
        self._paused(stopped, frames)

    def _spawn(self, work: Any) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            with contextlib.suppress(Refused, TimeoutError):
                await asyncio.wait_for(self._request("disconnect", {"terminateDebuggee": True}, timeout=3),
                                       timeout=4)
        if proc.returncode is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3)
        for task in (self._reader, self._stderr):
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
        self._proc = None


# ── Node: its inspector, over the Chrome DevTools Protocol ───────

def _file_url(path: str) -> str:
    return "file://" + quote(path)


def _path_of(url: str) -> str | None:
    if url.startswith("file://"):
        return unquote(urlparse(url).path)
    return url if url.startswith("/") else None


def _remote_value(obj: dict[str, Any]) -> tuple[str, str | None]:
    """How a value reads in the panel, and its type — the way a JavaScript console writes it."""
    kind = obj.get("type")
    if kind == "undefined":
        return "undefined", "undefined"
    if obj.get("subtype") == "null":
        return "null", "null"
    if kind == "string":
        return json.dumps(obj.get("value", ""), ensure_ascii=False), "string"
    if "unserializableValue" in obj:
        return str(obj["unserializableValue"]), kind
    if "value" in obj and kind in ("number", "boolean"):
        return json.dumps(obj["value"]), kind
    return str(obj.get("description") or obj.get("className") or kind), obj.get("className") or kind


class NodeSession(DebugSession):
    """node --inspect-brk on a loopback port, spoken to over the DevTools protocol."""

    language = "node"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._proc: asyncio.subprocess.Process | None = None
        self._socket: Any = None
        self._ids = itertools.count(1)
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._frames_raw: list[dict[str, Any]] = []
        #: Object handles the panel asks for by number, valid while the program stays paused.
        self._handles: dict[int, str] = {}
        self._next_handle = itertools.count(1)
        self._bp_ids: dict[str, list[str]] = {}
        self._first_pause = True
        #: Script ids to their URLs. A call frame's own `url` is deprecated and arrives empty in newer
        #: node, so where a frame is comes from the script it is in.
        self._scripts: dict[str, str] = {}

    async def _launch(self) -> None:
        from websockets.asyncio.client import connect

        self._first_pause = True
        target = [self.program] if self.program else []
        self._proc = await asyncio.create_subprocess_exec(
            self.interpreter, "--inspect-brk=127.0.0.1:0", *target, *self.args, cwd=str(self.cwd), env=self.env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        address = await self._listening()
        self._spawn(self._pipe(self._proc.stdout, "stdout"))
        self._spawn(self._pipe(self._proc.stderr, "stderr"))
        self._spawn(self._exited())
        self._socket = await connect(address, max_size=None, ping_interval=None, open_timeout=10)
        self._spawn(self._read())
        await self._send("Runtime.enable")
        await self._send("Debugger.enable")
        for path, lines in list(self.breakpoints.items()):
            self.verified[path] = await self._set(path, lines)
        await self._send("Debugger.setPauseOnExceptions", {"state": "uncaught"})
        await self._send("Runtime.runIfWaitingForDebugger")

    async def _listening(self) -> str:
        """Read the inspector's address from what node prints first. Anything else it printed before
        that is the program's own and is kept as output."""
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                code = await self._proc.wait()
                raise Refused(f"node exited ({code}) before its debugger was ready.", status=409)
            text = line.decode("utf-8", "replace")
            found = LISTENING.search(text)
            if found:
                return found.group(1)
            if not INSPECTOR_CHATTER.match(text):
                self.say("stderr", text)

    async def _pipe(self, stream: asyncio.StreamReader | None, category: str) -> None:
        if stream is None:
            return
        while chunk := await stream.read(65536):
            text = chunk.decode("utf-8", "replace")
            if category == "stderr":
                text = "".join(line for line in text.splitlines(keepends=True) if not INSPECTOR_CHATTER.match(line))
            self.say(category, text)

    async def _exited(self) -> None:
        assert self._proc is not None
        self.exit_code = await self._proc.wait()
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.close()
        self._end("ended", self.note)

    async def _send(self, method: str, params: dict[str, Any] | None = None, *,
                    timeout: float = ANSWER_SECONDS) -> dict[str, Any]:
        if self._socket is None:
            raise Refused("The debugger is not running.", status=409)
        message_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiting[message_id] = future
        try:
            await self._socket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
            answer = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as slow:
            raise Refused(f"node did not answer {method} in time.", status=504) from slow
        except Exception as gone:          # noqa: BLE001 — a closed socket, whatever the library calls it
            if isinstance(gone, Refused):
                raise
            raise Refused("The debugger has stopped.", status=409) from gone
        finally:
            self._waiting.pop(message_id, None)
        if "error" in answer:
            raise Refused(str((answer["error"] or {}).get("message") or f"node refused {method}."), status=409)
        return answer.get("result") or {}

    async def _read(self) -> None:
        try:
            async for raw in self._socket:
                message = json.loads(raw)
                if "id" in message:
                    future = self._waiting.get(int(message["id"]))
                    if future is not None and not future.done():
                        future.set_result(message)
                else:
                    self._event(str(message.get("method")), message.get("params") or {})
        except Exception:                   # noqa: BLE001 — the socket closing is how a session ends
            pass
        finally:
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception(Refused("The debugger has stopped.", status=409))

    def _event(self, method: str, params: dict[str, Any]) -> None:
        if method == "Debugger.scriptParsed":
            self._scripts[str(params.get("scriptId"))] = str(params.get("url") or "")
        elif method == "Debugger.paused":
            if self._first_pause and not params.get("hitBreakpoints"):
                # The stop --inspect-brk makes before the first line. The breakpoints are set; go on.
                self._first_pause = False
                self._spawn(self._send("Debugger.resume"))
                return
            self._first_pause = False
            self._frames_raw = list(params.get("callFrames") or [])
            self._handles.clear()
            reason = str(params.get("reason", ""))
            reason = "breakpoint" if params.get("hitBreakpoints") else {"other": "step", "exception": "exception",
                                                                        "promiseRejection": "exception"}.get(
                reason, reason)
            data = params.get("data") or {}
            self._paused({"reason": reason, "threadId": 1,
                          "description": str(data.get("description") or "") if reason == "exception" else ""},
                         [self._frame(i, f) for i, f in enumerate(self._frames_raw[:FRAMES])])
        elif method == "Debugger.resumed":
            self._frames_raw = []
            self._handles.clear()
            self._resumed()
        elif method == "Runtime.executionContextDestroyed":
            # The program has finished; node waits for the debugger to let go before it exits.
            if self._socket is not None:
                self._spawn(self._socket.close())

    def _frame(self, index: int, raw: dict[str, Any]) -> dict[str, Any]:
        location = raw.get("location") or {}
        url = raw.get("url") or self._scripts.get(str(location.get("scriptId")), "")
        return {"id": index, "name": raw.get("functionName") or "(anonymous)", "path": _path_of(url),
                "line": int(location.get("lineNumber", 0)) + 1, "column": int(location.get("columnNumber", 0)) + 1,
                "hint": None}

    def _handle(self, object_id: str) -> int:
        number = next(self._next_handle)
        self._handles[number] = object_id
        return number

    async def _set(self, path: str, lines: list[int]) -> list[dict[str, Any]]:
        for old in self._bp_ids.pop(path, []):
            with contextlib.suppress(Refused):
                await self._send("Debugger.removeBreakpoint", {"breakpointId": old})
        results: list[dict[str, Any]] = []
        ids: list[str] = []
        # CommonJS scripts are named by path in older node and by file URL in newer; match either.
        pattern = "^(file://)?" + re.escape(quote(path)) + "$" if quote(path) != path else \
            "^(file://)?" + re.escape(path) + "$"
        for line in lines:
            try:
                answer = await self._send("Debugger.setBreakpointByUrl", {"lineNumber": line - 1, "urlRegex": pattern})
            except Refused as refused:
                results.append({"line": line, "verified": False, "message": str(refused)})
                continue
            ids.append(answer.get("breakpointId", ""))
            located = answer.get("locations") or []
            # A script not loaded yet has no locations; node binds the breakpoint when it loads.
            results.append({"line": (located[0]["lineNumber"] + 1) if located else line, "verified": True,
                            "message": None})
        self._bp_ids[path] = ids
        return results

    async def _command(self, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if command == "setBreakpoints":
            return {"breakpoints": await self._set(str(arguments["path"]), list(arguments["lines"]))}
        if command == "terminate":
            if self._proc is not None and self._proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.terminate()
            return {"ok": True}
        steps = {"continue": "Debugger.resume", "next": "Debugger.stepOver", "stepIn": "Debugger.stepInto",
                 "stepOut": "Debugger.stepOut", "pause": "Debugger.pause"}
        if command in steps:
            await self._send(steps[command])
            return {"ok": True}
        if command == "threads":
            return {"threads": [{"id": 1, "name": "main"}]}
        if command == "stackTrace":
            start = max(0, int(arguments.get("startFrame", 0)))
            levels = max(1, min(int(arguments.get("levels", FRAMES)), 500))
            return {"frames": [self._frame(i, f) for i, f in enumerate(self._frames_raw)][start:start + levels],
                    "total": len(self._frames_raw)}
        if command == "scopes":
            index = int(arguments["frameId"])
            if not 0 <= index < len(self._frames_raw):
                raise Refused("That frame is gone; the program has moved on.", status=409)
            scopes = []
            for scope in self._frames_raw[index].get("scopeChain") or []:
                object_id = (scope.get("object") or {}).get("objectId")
                if object_id:
                    kind = str(scope.get("type", ""))
                    scopes.append({"name": scope.get("name") or kind.capitalize(), "ref": self._handle(object_id),
                                   "expensive": kind == "global"})
            return {"scopes": scopes}
        if command == "variables":
            object_id = self._handles.get(int(arguments["ref"]))
            if object_id is None:
                raise Refused("That value is gone; the program has moved on.", status=409)
            answer = await self._send("Runtime.getProperties", {"objectId": object_id, "ownProperties": True,
                                                                "generatePreview": False})
            variables = []
            for prop in (answer.get("result") or [])[:MAX_CHILDREN]:
                if prop.get("name") == "__proto__" or "value" not in prop:
                    continue
                value = prop["value"] or {}
                text, kind = _remote_value(value)
                child = value.get("objectId") if value.get("type") == "object" and value.get("subtype") != "null" \
                    else None
                variables.append({"name": prop.get("name", ""), "value": text, "type": kind,
                                  "ref": self._handle(child) if child else 0, "named": None, "indexed": None})
            return {"variables": variables}
        if command == "evaluate":
            expression = str(arguments["expression"])
            frame = arguments.get("frameId")
            if frame is not None and self._frames_raw and 0 <= int(frame) < len(self._frames_raw):
                answer = await self._send("Debugger.evaluateOnCallFrame", {
                    "callFrameId": self._frames_raw[int(frame)]["callFrameId"], "expression": expression,
                    "silent": True})
            else:
                answer = await self._send("Runtime.evaluate", {"expression": expression, "silent": True})
            if answer.get("exceptionDetails"):
                details = answer["exceptionDetails"]
                message = ((details.get("exception") or {}).get("description") or details.get("text") or "Error")
                return {"result": str(message).splitlines()[0], "type": "error", "ref": 0}
            value = answer.get("result") or {}
            text, kind = _remote_value(value)
            child = value.get("objectId") if value.get("type") == "object" else None
            return {"result": text, "type": kind, "ref": self._handle(child) if child else 0}
        raise Refused(f"The debugger has no command called {command}.", status=404)

    def _spawn(self, work: Any) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _close(self) -> None:
        proc = self._proc
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.close()
        if proc is not None and proc.returncode is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, sig)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=2)
                if proc.returncode is not None:
                    break


# ── every debug session in this process ──────────────────────────

LANGUAGES = {"python": PythonSession, "node": NodeSession}


def node_binary() -> str | None:
    return shutil.which("node")


class Debuggers:
    """The debug sessions this API holds, per person. Held on `app.state` and closed with the app."""

    def __init__(self) -> None:
        self._all: dict[str, DebugSession] = {}

    def mine(self, owner: str, project_id: str | None = None) -> list[DebugSession]:
        return sorted((s for s in self._all.values() if s.owner == owner
                       and (project_id is None or s.project_id == project_id)), key=lambda s: s.started_at)

    def get(self, session_id: str, owner: str) -> DebugSession:
        from ..repositories import NotFound

        found = self._all.get(session_id)
        if found is None or found.owner != owner:
            raise NotFound(f"debug session {session_id}")
        return found

    async def start(self, *, language: str, owner: str, **kwargs: Any) -> DebugSession:
        live = [s for s in self.mine(owner) if s.status in ("starting", "running", "paused")]
        if len(live) >= MAX_PER_PERSON:
            raise Refused(f"You have {MAX_PER_PERSON} debug sessions running. Stop one first.", status=429)
        # Finished sessions are kept to be read, but not without end.
        for old in [s for s in self.mine(owner) if s.status in ("ended", "failed")][:-MAX_PER_PERSON]:
            self._all.pop(old.id, None)
        kind = LANGUAGES.get(language)
        if kind is None:
            raise Refused("Python and Node programs can be debugged here. Run a shell command from Run.",
                          status=422)
        session = kind(owner=owner, **kwargs)
        self._all[session.id] = session
        try:
            await session.start()
        except Refused:
            self._all.pop(session.id, None)
            raise
        return session

    async def remove(self, session_id: str, owner: str) -> None:
        session = self.get(session_id, owner)
        await session.terminate()
        self._all.pop(session.id, None)

    async def close_all(self) -> None:
        for session in list(self._all.values()):
            with contextlib.suppress(Exception):
                await session.terminate()
        self._all.clear()


def split_target(command: str, language: str) -> tuple[str | None, str | None, list[str]]:
    """A debug configuration's command, read as a program or, for Python, `-m module`, plus any words
    after it, which go in front of the configuration's own arguments."""
    words = shlex.split(command)
    if not words:
        raise Refused("Name the program to debug.", status=422)
    if language == "python" and words[0] == "-m":
        if len(words) < 2:
            raise Refused("Name the module after -m.", status=422)
        return None, words[1], words[2:]
    return words[0], None, words[1:]


async def launch(open_session: Any, debuggers: Debuggers, who: Any, project_id: str, *,
                 run_config_id: int | None, program: str | None, module: str | None, args: list[str] | None,
                 cwd: str | None, language: str | None, breakpoints: dict[str, list[int]],
                 ip: str = "") -> DebugSession:
    """Debug a project's program: a saved debug configuration, or a program (or Python module) named
    directly — a file of the checkout, never a path outside it.

    The breakpoints the Workbench holds are handed in at the start, so a breakpoint on a script's first
    lines is in place before those lines run."""
    from ..repositories import AuditRepository
    from .terminal import (MACHINE, RunConfigService, base_env, folder_in_project, project_roots, python_for,
                           source_holding)

    who.must(MACHINE, "debug a program")
    service = RunConfigService(open_session)
    project = await service.project(project_id)
    sources = await project_roots(open_session, project)
    env: dict[str, str] = {}
    name = ""
    extra: list[str] = []
    if run_config_id is not None:
        config, _ = await service.one(run_config_id)
        if config.project_id != project.id:
            raise Refused("That configuration belongs to another project.", status=409)
        if config.kind != "debug":
            raise Refused(f"{config.name} is a run configuration. Start it from Run, or save a debug one.",
                          status=409)
        language = config.language
        program, module, extra = split_target(config.command, config.language)
        extra += [str(a) for a in (config.args or [])]
        cwd, env, name = config.cwd, {str(k): str(v) for k, v in (config.env or {}).items()}, config.name
    else:
        extra = [str(a) for a in (args or [])]
        if not program and not module:
            raise Refused("Choose a debug configuration, or name the program to debug.", status=422)
    # The folder is a project path (under a further source's label for that source's code); the program
    # may be any file of any of the project's checkouts, and the interpreter is the one of the checkout
    # that holds it.
    source, folder = folder_in_project(sources, cwd or "")
    checkout = Path(os.path.realpath(source.root))
    if module:
        language = "python"
        if not re.fullmatch(r"[A-Za-z_][\w.]*", module):
            raise Refused(f"{module} is not a module name.", status=422)
        target_program: str | None = None
    else:
        assert program is not None
        raw = program if os.path.isabs(program) else os.path.join(folder, program)
        real = Path(os.path.realpath(raw))
        holder = source_holding(sources, real)
        if holder is None:
            raise Refused(f"{program} is outside this project's checkout.", status=403)
        checkout = Path(os.path.realpath(holder.root))
        if not real.is_file():
            raise Refused(f"{program} is not a file in this project's checkout.", status=404)
        target_program = str(real)
        if language not in LANGUAGES:
            language = "python" if real.suffix == ".py" else "node" if real.suffix in (".js", ".mjs", ".cjs") \
                else None
        if language is None:
            raise Refused("Python (.py) and JavaScript (.js, .mjs, .cjs) programs can be debugged here. "
                          "Run anything else from Run.", status=422)
    if language == "node":
        interpreter = node_binary()
        if interpreter is None:
            raise Refused("node is not installed on this machine, or not on the API's PATH.", status=409)
    else:
        interpreter = python_for(checkout, folder)
    marks = {p: [int(n) for n in lines if isinstance(n, int) and n > 0]
             for p, lines in list(breakpoints.items())[:500] if os.path.isabs(p)}
    session = await debuggers.start(
        language=language or "python", owner=who.id, project_id=project.id,
        name=name or (module or os.path.relpath(target_program or "", checkout)), program=target_program,
        module=module, args=extra, cwd=folder, env=base_env(env), interpreter=interpreter,
        breakpoints={p: lines for p, lines in marks.items() if lines}, run_config_id=run_config_id)
    await AuditRepository(open_session).record(
        action="debug.start", user_id=who.id, target=session.name,
        detail={"project": project.id, "language": session.language, "program": target_program,
                "module": module, "args": extra, "cwd": str(folder), "interpreter": interpreter,
                "config": run_config_id, "env": sorted(env), "session": session.id}, ip=ip)
    return session
