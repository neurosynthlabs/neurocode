"""Terminals and run configurations on the machine the API runs on.

A terminal is a real pseudo-terminal: `pty.openpty()`, the person's own shell on the slave side, and the
master side read by the event loop and fanned out to every browser tab watching it. The shell is given
the slave as its controlling terminal, so Ctrl+C, job control and full-screen programs (vim, htop, a
REPL) behave as they do in any terminal app — without that, the line discipline has no foreground
process group to send SIGINT to, and Ctrl+C silently did nothing.

What a terminal printed recently is kept in a ring buffer (`BUFFER_BYTES`), so a tab that reconnects —
a reload, a laptop that slept — repaints the screen it had instead of a blank one. Terminals live in
this process: they are a person's tools on their own machine, not records, and a restart of the API
ends them exactly as closing a terminal app would.

A run configuration is how a person runs a project: a command in a folder of its checkout, with extra
environment. Starting one opens a terminal running that command, so its output streams through the
same socket as any shell. "Detect" reads the checkout and *suggests* configurations — package.json
scripts, Makefile targets, a manage.py — which a person saves; nothing detected ever runs on its own.

Nothing here is driven by a model. Every terminal opened and every run started is in the audit log.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import pty
import re
import shlex
import shutil
import signal
import struct
import subprocess
import termios
import time
import tomllib
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Project, RunConfig, User
from ..repositories import ActivityRepository, AuditRepository, NotFound, ProjectRepository
from ..repositories.base import Repository, bounded
from ..schemas.terminal import run_config_json
from ..settings import Settings
from . import code
from .errors import Refused
from .identity import Person

#: The permission every machine route needs. Owners hold it; nobody else does unless an Owner grants it.
MACHINE = "machine:access"

#: One person's terminals at once, shells and runs together. A browser tab per terminal is already a lot.
MAX_PER_PERSON = 8
#: What a terminal remembers of its own output, so a reconnecting tab repaints the screen it had.
BUFFER_BYTES = 256 * 1024
#: Chunks queued for one watching socket. A tab that falls this far behind is repainted from the buffer
#: rather than fed a backlog it will never catch up on.
QUEUE_CHUNKS = 1024
#: Input waiting for the shell to read it. A paste larger than this is refused rather than held.
PENDING_INPUT = 1024 * 1024
#: A running terminal nobody has watched, and that has printed nothing, for this long is closed.
IDLE_SECONDS = 4 * 60 * 60
#: A finished terminal nobody is watching is forgotten after this long; until then it can be reopened
#: to read what it printed and how it ended.
EXITED_KEEP_SECONDS = 15 * 60
REAP_EVERY_SECONDS = 60
#: How long a stopped process is given to leave before it is killed outright.
STOP_GRACE_SECONDS = 2.0
MIN_COLS, MAX_COLS = 20, 500
MIN_ROWS, MAX_ROWS = 5, 300

#: Environment the API itself runs with that a person's shell has no business inheriting: its database
#: URL carries a password, and a provider key may be here too.
PRIVATE_ENV = re.compile(r"^(NEUROCODE_|DEEPSEEK_API_KEY$|UV_|VIRTUAL_ENV$|PYTHONPATH$|PYTHONHOME$)")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ENV = 64
MAX_ENV_VALUE = 8000
MAX_ARGS = 64
MAX_ARG = 2000
MAX_COMMAND = 4000


# ── where a terminal may start ───────────────────────────────────

def machine_roots(config: Settings) -> list[Path]:
    """The folders this server may open, real and absolute. `~` is the home of the account the API runs
    as. A root that does not exist is left out rather than trusted later, when something may appear
    there."""
    roots: list[Path] = []
    for part in config.machine_roots.split(":"):
        text = part.strip()
        if not text:
            continue
        path = Path(os.path.realpath(os.path.expanduser(text)))
        if path.is_dir() and path not in roots:
            roots.append(path)
    return roots


def inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def folder_in_roots(raw: str, config: Settings) -> Path:
    """A folder a person named, resolved through every symlink, and refused unless it is inside a root.

    Resolving first is the point: `~/code/../../etc` and a link inside the root that points out of it
    both arrive at their real place before the check, so neither can pass it."""
    roots = machine_roots(config)
    if not roots:
        raise Refused("No folder on this machine is open to the Workbench. Set NEUROCODE_MACHINE_ROOTS.",
                      status=409)
    text = raw.strip()
    if not text:
        return roots[0]
    expanded = os.path.expanduser(text)
    if not os.path.isabs(expanded):
        raise Refused("Give the folder as an absolute path.", status=422)
    real = Path(os.path.realpath(expanded))
    if not any(inside(real, root) for root in roots):
        raise Refused(f"{text} is outside the folders this server may open.", status=403)
    if not real.is_dir():
        raise Refused(f"{text} is not a folder on this machine.", status=404)
    return real


def folder_in_checkout(checkout: Path, relative: str) -> Path:
    """A folder of a project's checkout, given relative to its root; refused if it resolves outside."""
    root = Path(os.path.realpath(checkout))
    text = relative.strip().strip("/") if relative else ""
    if os.path.isabs(relative or ""):
        raise Refused("A run's folder is given relative to the project's checkout.", status=422)
    real = Path(os.path.realpath(root / text)) if text else root
    if not inside(real, root):
        raise Refused(f"{relative} is outside this project's checkout.", status=403)
    if not real.is_dir():
        raise Refused(f"{relative or 'The checkout'} is not a folder in this project's checkout.", status=404)
    return real


async def project_roots(session: AsyncSession, project: Project) -> list[code.Source]:
    """Every checkout of a project, as `code.roots` gives them, refused when none is on this machine."""
    sources = await code.roots(session, project)
    if not any(s.ready and s.root.is_dir() for s in sources):
        raise Refused(f"{project.name} has no code on this machine. Onboard it from a local folder or a "
                      "repository first.", status=409)
    return sources


def folder_in_project(sources: list[code.Source], relative: str) -> tuple[code.Source, Path]:
    """A folder of a project named the way the project names its paths: plain for the first source,
    under its label for a further one (`api/src` is `src` in the `api` checkout). Refused when it
    resolves outside that checkout, or when that checkout is not ready to be read."""
    text = (relative or "").strip()
    if os.path.isabs(text):
        raise Refused("A run's folder is given relative to the project's checkout.", status=422)
    hit = code.split(sources, text.strip("/"))
    if hit is None:
        raise Refused(f"{relative} is in a source that is not on this machine yet.", status=409)
    source, rel = hit
    if not source.ready or not source.root.is_dir():
        raise Refused(f"The {source.label} source is not on this machine yet.", status=409)
    return source, folder_in_checkout(source.root, rel)


def source_holding(sources: list[code.Source], path: Path) -> code.Source | None:
    """The checkout a real path is inside, if it is inside any of them."""
    for source in sources:
        if source.ready and inside(path, Path(os.path.realpath(source.root))):
            return source
    return None


def pick_shell() -> str:
    """The person's own shell, then bash, then sh — the first that is really there."""
    for candidate in (os.environ.get("SHELL", ""), "/bin/bash", "/bin/sh"):
        if candidate and os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("sh")
    if found:
        return found
    raise Refused("No shell was found on this machine.", status=500)


def interactive(shell: str) -> bool:
    """Shells that read their rc file with `-i`, which is where people keep PATH (nvm, pyenv, conda)."""
    return os.path.basename(shell) in ("zsh", "bash", "fish", "ksh")


def base_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The API's environment minus its own secrets, set up for a colour, UTF-8 terminal.

    UTF-8 is the default only when the machine names no locale of its own: a person whose shell is set
    to another one keeps it, and anything typed in any script reaches the program intact either way."""
    env = {k: v for k, v in os.environ.items() if not PRIVATE_ENV.match(k)}
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["TERM_PROGRAM"] = "NeuroCode"
    if not env.get("LANG") and not env.get("LC_ALL"):
        env["LANG"] = "en_US.UTF-8"
    env.update(extra or {})
    return env


def _controlling_terminal() -> None:  # pragma: no cover — runs in the child between fork and exec
    """Make the pty the child's controlling terminal. `start_new_session` has already called setsid(),
    so the child leads a session with no terminal; this gives it one."""
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(at: datetime | None) -> str | None:
    return at.isoformat(timespec="seconds") if at else None


# ── one terminal ─────────────────────────────────────────────────

@dataclass(eq=False)
class Listener:
    """One watching socket: what it has not sent yet, and whether it fell too far behind to catch up."""

    queue: asyncio.Queue[tuple[str, Any]] = field(default_factory=lambda: asyncio.Queue(QUEUE_CHUNKS))

    def offer(self, kind: str, payload: Any) -> None:
        try:
            self.queue.put_nowait((kind, payload))
        except asyncio.QueueFull:
            # Behind by a thousand chunks: throw the backlog away and repaint from the buffer, which
            # already holds everything the backlog did.
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(("repaint", None))


class Terminal:
    """A shell (or a run's command) on a pseudo-terminal, and everyone watching it."""

    def __init__(self, *, owner: str, kind: str, title: str, argv: list[str], cwd: Path, env: dict[str, str],
                 cols: int, rows: int, project_id: str | None = None, run_config_id: int | None = None,
                 command: str = "") -> None:
        self.id = uuid.uuid4().hex[:12]
        self.owner = owner
        self.kind = kind
        self.title = title
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.command = command
        self.cols, self.rows = cols, rows
        self.project_id = project_id
        self.run_config_id = run_config_id
        self.buffer = bytearray()
        self.listeners: set[Listener] = set()
        self.proc: subprocess.Popen[bytes] | None = None
        self.fd: int | None = None
        self.status = "starting"
        self.exit_code: int | None = None
        self.started_at = _now()
        self.ended_at: datetime | None = None
        self.last_output = time.monotonic()
        self.detached_since: float | None = time.monotonic()
        self.runs = 0
        self._pending = bytearray()
        self._writing = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ended = asyncio.Event()
        self._restarting = asyncio.Lock()

    # ── life ──────────────────────────────────────────────────────
    def spawn(self) -> None:
        """Start the program on a fresh pty. Called again by a restart, in the same terminal."""
        self._loop = asyncio.get_running_loop()
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
            self.proc = subprocess.Popen(  # noqa: S603 — the argv is the person's shell or their own run command
                self.argv, stdin=slave, stdout=slave, stderr=slave, cwd=str(self.cwd), env=self.env,
                start_new_session=True, preexec_fn=_controlling_terminal, close_fds=True)
        except OSError as failed:
            os.close(master)
            raise Refused(f"Could not start {self.argv[0]}: {failed.strerror or failed}", status=500) from failed
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        self.fd = master
        self.status = "running"
        self.exit_code = None
        self.ended_at = None
        self.runs += 1
        self._ended = asyncio.Event()
        self.last_output = time.monotonic()
        self._loop.add_reader(master, self._readable)
        self._broadcast_state()

    def _readable(self) -> None:
        if self.fd is None:
            return
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""          # EIO: the last process holding the slave side has gone
        if not data:
            self._closed_pty()
            return
        self.record(data)

    def record(self, data: bytes) -> None:
        """Keep it in the ring buffer and send it to everyone watching."""
        self.last_output = time.monotonic()
        self.buffer += data
        if len(self.buffer) > BUFFER_BYTES:
            cut = len(self.buffer) - BUFFER_BYTES
            # Cut at a line break when there is one near, so the repaint does not begin halfway through
            # an escape sequence; and never inside a UTF-8 character, whatever the script.
            newline = self.buffer.find(b"\n", cut, cut + 4096)
            cut = newline + 1 if newline != -1 else cut
            while cut < len(self.buffer) and (self.buffer[cut] & 0xC0) == 0x80:
                cut += 1
            del self.buffer[:cut]
        for listener in list(self.listeners):
            listener.offer("out", bytes(data))

    def _closed_pty(self) -> None:
        if self._loop is not None and self.fd is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self.fd)
            with contextlib.suppress(Exception):
                self._loop.remove_writer(self.fd)
            with contextlib.suppress(OSError):
                os.close(self.fd)
        self.fd = None
        self._pending.clear()
        if self._loop is not None:
            self._loop.create_task(self._reap_process())

    async def _reap_process(self) -> None:
        proc = self.proc
        code_: int | None = None
        if proc is not None:
            code_ = proc.poll()
            if code_ is None:
                code_ = await asyncio.to_thread(proc.wait)
        self.exit_code = code_
        self.status = "exited"
        self.ended_at = _now()
        self._ended.set()
        for listener in list(self.listeners):
            listener.offer("event", {"type": "exit", "code": code_})
        self._broadcast_state()

    async def stop(self, grace: float = STOP_GRACE_SECONDS) -> None:
        """End the program the way closing a terminal window does: hang up, then terminate, then kill."""
        proc = self.proc
        if proc is None or self.status != "running":
            return
        for sig in (signal.SIGHUP, signal.SIGTERM):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, sig)
        deadline = time.monotonic() + grace
        while proc.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        # A child that ignored the hang-up may still hold the pty open; closing our side ends the reading.
        if self.fd is not None:
            self._closed_pty()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ended.wait(), timeout=grace + 3)

    async def restart(self) -> None:
        """Stop the program and start it again, one restart at a time. Two that arrived together (a
        double click, two tabs) would otherwise both stop the old process and both spawn, and the first
        new one would be left running with nobody holding its handle to stop it."""
        async with self._restarting:
            await self.stop()
            self.record(b"\r\n\x1b[2m-- restarted --\x1b[0m\r\n")
            self.spawn()

    # ── input and size ────────────────────────────────────────────
    def write(self, data: bytes) -> None:
        if self.fd is None or self.status != "running" or not data:
            return
        if len(self._pending) + len(data) > PENDING_INPUT:
            raise Refused("That is more input than the terminal can take at once.", status=413)
        self._pending += data
        self._flush()

    def _flush(self) -> None:
        if self.fd is None:
            return
        while self._pending:
            try:
                n = os.write(self.fd, self._pending)
            except BlockingIOError:
                if not self._writing and self._loop is not None:
                    self._loop.add_writer(self.fd, self._flush)
                    self._writing = True
                return
            except OSError:
                self._pending.clear()
                return
            del self._pending[:n]
        if self._writing and self._loop is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_writer(self.fd)
            self._writing = False

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = _clamp(cols, MIN_COLS, MAX_COLS), _clamp(rows, MIN_ROWS, MAX_ROWS)
        if self.fd is not None:
            with contextlib.suppress(OSError):
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

    # ── watching ──────────────────────────────────────────────────
    def attach(self) -> Listener:
        listener = Listener()
        self.listeners.add(listener)
        self.detached_since = None
        return listener

    def detach(self, listener: Listener) -> None:
        self.listeners.discard(listener)
        if not self.listeners:
            self.detached_since = time.monotonic()

    def _broadcast_state(self) -> None:
        for listener in list(self.listeners):
            listener.offer("event", {"type": "state", "terminal": self.json()})

    def json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "title": self.title, "cwd": str(self.cwd),
                "command": self.command, "shell": self.argv[0] if self.argv else "",
                "pid": self.proc.pid if self.proc else None, "cols": self.cols, "rows": self.rows,
                "status": self.status, "exitCode": self.exit_code, "startedAt": _stamp(self.started_at),
                "endedAt": _stamp(self.ended_at), "attached": len(self.listeners), "bytes": len(self.buffer),
                "projectId": self.project_id, "runConfigId": self.run_config_id, "runs": self.runs}


# ── every terminal in this process ───────────────────────────────

class Terminals:
    """The terminals this API holds, per person. Held on `app.state` and closed with the app."""

    def __init__(self) -> None:
        self._all: dict[str, Terminal] = {}
        self._reaper: asyncio.Task[None] | None = None

    def mine(self, owner: str) -> list[Terminal]:
        return sorted((t for t in self._all.values() if t.owner == owner), key=lambda t: t.started_at)

    def get(self, terminal_id: str, owner: str) -> Terminal:
        """Someone else's terminal is exactly as absent as one that never existed."""
        found = self._all.get(terminal_id)
        if found is None or found.owner != owner:
            raise NotFound(f"terminal {terminal_id}")
        return found

    def running_for(self, owner: str, run_config_id: int) -> Terminal | None:
        return next((t for t in self.mine(owner) if t.run_config_id == run_config_id and t.status == "running"),
                    None)

    def open(self, *, owner: str, kind: str, title: str, argv: list[str], cwd: Path, env: dict[str, str],
             cols: int, rows: int, project_id: str | None = None, run_config_id: int | None = None,
             command: str = "") -> Terminal:
        if len(self.mine(owner)) >= MAX_PER_PERSON:
            raise Refused(f"You have {MAX_PER_PERSON} terminals open. Close one first.", status=429)
        terminal = Terminal(owner=owner, kind=kind, title=title, argv=argv, cwd=cwd, env=env,
                            cols=_clamp(cols, MIN_COLS, MAX_COLS), rows=_clamp(rows, MIN_ROWS, MAX_ROWS),
                            project_id=project_id, run_config_id=run_config_id, command=command)
        terminal.spawn()
        self._all[terminal.id] = terminal
        self._watch()
        return terminal

    async def close(self, terminal_id: str, owner: str) -> None:
        terminal = self.get(terminal_id, owner)
        await terminal.stop()
        self._forget(terminal)

    def _forget(self, terminal: Terminal) -> None:
        self._all.pop(terminal.id, None)
        for listener in list(terminal.listeners):
            listener.offer("event", {"type": "closed"})

    async def close_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for terminal in list(self._all.values()):
            with contextlib.suppress(Exception):
                await terminal.stop(grace=0.5)
            self._forget(terminal)

    async def reap(self) -> list[str]:
        """Close what nobody is using: a finished terminal nobody reopened, and a running one that has
        been neither watched nor heard from for hours. Returns the ids it closed."""
        now = time.monotonic()
        gone: list[str] = []
        for terminal in list(self._all.values()):
            if terminal.listeners:
                continue
            quiet = now - max(terminal.last_output, terminal.detached_since or now)
            if terminal.status == "exited" and terminal.ended_at is not None \
                    and (_now() - terminal.ended_at).total_seconds() > EXITED_KEEP_SECONDS:
                gone.append(terminal.id)
            elif terminal.status == "running" and quiet > IDLE_SECONDS:
                await terminal.stop()
                gone.append(terminal.id)
        for terminal_id in gone:
            found = self._all.get(terminal_id)
            if found is not None:
                self._forget(found)
        return gone

    def _watch(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_forever())

    async def _reap_forever(self) -> None:
        while self._all:
            await asyncio.sleep(REAP_EVERY_SECONDS)
            with contextlib.suppress(Exception):
                await self.reap()


def shell_argv(shell: str, line: str | None = None) -> list[str]:
    """The shell alone, or the shell running one command line — interactively for the shells that keep
    PATH in their rc file, so a run finds the same `node` and `python` the person's terminal does."""
    flags = ["-i"] if interactive(shell) else []
    return [shell, *flags] if line is None else [shell, *flags, "-c", line]


# ── run configurations ───────────────────────────────────────────

class RunConfigRepository(Repository[RunConfig]):
    model = RunConfig

    async def listed(self, project_id: str, *, kind: str | None, limit: int | None,
                     offset: int) -> list[tuple[RunConfig, str | None]]:
        where = [RunConfig.project_id == project_id]
        if kind:
            where.append(RunConfig.kind == kind)
        stmt = (select(RunConfig, User.name).outerjoin(User, User.id == RunConfig.created_by).where(*where)
                .order_by(RunConfig.kind, func.lower(RunConfig.name), RunConfig.id)
                .limit(bounded(limit)).offset(max(0, offset)))
        return [(config, author) for config, author in await self.session.execute(stmt)]

    async def named(self, config_id: int) -> tuple[RunConfig, str | None] | None:
        stmt = (select(RunConfig, User.name).outerjoin(User, User.id == RunConfig.created_by)
                .where(RunConfig.id == config_id))
        found = (await self.session.execute(stmt)).first()
        return (found[0], found[1]) if found else None

    async def same_name(self, project_id: str, name: str, kind: str) -> RunConfig | None:
        return await self.one(RunConfig.project_id == project_id, RunConfig.kind == kind,
                              func.lower(RunConfig.name) == name.lower())

    async def count_for(self, project_id: str) -> int:
        return int((await self.session.execute(
            select(func.count()).select_from(RunConfig).where(RunConfig.project_id == project_id))).scalar_one())


KINDS = ("run", "debug")
LANGUAGES = ("python", "node", "shell")
#: The most configurations one project keeps. A Run menu longer than this is not a menu.
MAX_CONFIGS = 200


@dataclass(frozen=True, slots=True)
class Checked:
    name: str
    kind: str
    language: str
    command: str
    args: list[str]
    cwd: str
    env: dict[str, str]


def _check_fields(*, name: str, kind: str, language: str, command: str, args: Iterable[str], cwd: str,
                  env: Mapping[str, str]) -> Checked:
    name = name.strip()
    if not name or len(name) > 120:
        raise Refused("Name the configuration in 1 to 120 characters.", status=422)
    if kind not in KINDS:
        raise Refused("A configuration runs or debugs.", status=422)
    if language not in LANGUAGES:
        raise Refused(f"The language is one of: {', '.join(LANGUAGES)}.", status=422)
    command = command.strip()
    if not command or len(command) > MAX_COMMAND or "\n" in command or "\x00" in command:
        raise Refused(f"The command is one line of 1 to {MAX_COMMAND} characters.", status=422)
    arguments = [str(a) for a in args]
    if len(arguments) > MAX_ARGS or any(len(a) > MAX_ARG or "\x00" in a for a in arguments):
        raise Refused(f"At most {MAX_ARGS} arguments of up to {MAX_ARG} characters each.", status=422)
    cwd = (cwd or "").strip()
    if os.path.isabs(cwd) or ".." in Path(cwd).parts:
        raise Refused("The folder is relative to the project's checkout and stays inside it.", status=422)
    if len(env) > MAX_ENV:
        raise Refused(f"At most {MAX_ENV} environment variables.", status=422)
    for key, value in env.items():
        if not ENV_NAME.match(key):
            raise Refused(f"{key} is not a valid environment variable name.", status=422)
        if not isinstance(value, str) or len(value) > MAX_ENV_VALUE or "\x00" in value:
            raise Refused(f"The value of {key} must be text of at most {MAX_ENV_VALUE} characters.", status=422)
    return Checked(name, kind, language, command, arguments, cwd.strip("/"), dict(env))


def python_for(checkout: Path, folder: Path | None = None) -> str:
    """The project's own interpreter when it has one — a `.venv` or `venv` beside the code — else the
    machine's python3. Never the API's own environment: the project's packages are not installed there."""
    for base in (folder, checkout):
        if base is None:
            continue
        for venv in (".venv", "venv", "env"):
            candidate = base / venv / "bin" / "python"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return shutil.which("python3") or shutil.which("python") or "python3"


def command_line(config: RunConfig, checkout: Path, folder: Path) -> str:
    """What the shell is asked to run for this configuration.

    A `run` is its command and arguments as written. A `debug` configuration started without the
    debugger runs its program the way the debugger would have launched it: with the project's python,
    or node, and the same arguments."""
    args = [str(a) for a in (config.args or [])]
    if config.kind == "debug" and config.language == "python":
        return shlex.join([python_for(checkout, folder), *shlex.split(config.command), *args])
    if config.kind == "debug" and config.language == "node":
        return shlex.join(["node", *shlex.split(config.command), *args])
    return config.command + (" " + shlex.join(args) if args else "")


class RunConfigService:
    """A project's run and debug configurations, and starting one."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.configs = RunConfigRepository(session)
        self.activity = ActivityRepository(session)
        self.audit = AuditRepository(session)

    async def project(self, project_id: str) -> Project:
        found = await ProjectRepository(self.session).get(project_id)
        if found is None:
            raise NotFound(f"project {project_id}")
        return found

    @staticmethod
    def checkout(project: Project) -> Path:
        root = code.checkout(project)
        if root is None or not root.is_dir():
            raise Refused(f"{project.name} has no code on this machine. Onboard it from a local folder or a "
                          "repository first.", status=409)
        return Path(os.path.realpath(root))

    async def listed(self, project_id: str, *, kind: str | None, limit: int | None,
                     offset: int) -> list[dict[str, Any]]:
        await self.project(project_id)
        if kind and kind not in KINDS:
            raise Refused("A configuration runs or debugs.", status=422)
        rows = await self.configs.listed(project_id, kind=kind, limit=limit, offset=offset)
        return [run_config_json(config, author) for config, author in rows]

    async def one(self, config_id: int) -> tuple[RunConfig, str | None]:
        found = await self.configs.named(config_id)
        if found is None:
            raise NotFound(f"run configuration {config_id}")
        return found

    async def create(self, project_id: str, *, name: str, kind: str, language: str, command: str,
                     args: list[str], cwd: str, env: dict[str, str], who: Person, ip: str = "") -> dict[str, Any]:
        who.must(MACHINE, "save a run configuration")
        project = await self.project(project_id)
        checked = _check_fields(name=name, kind=kind, language=language, command=command, args=args, cwd=cwd,
                                env=env)
        if await self.configs.count_for(project.id) >= MAX_CONFIGS:
            raise Refused(f"{project.name} already has {MAX_CONFIGS} configurations. Remove one first.")
        if await self.configs.same_name(project.id, checked.name, checked.kind) is not None:
            raise Refused(f"There is already a {checked.kind} configuration called {checked.name}.")
        config = await self.configs.add(RunConfig(
            project_id=project.id, name=checked.name, kind=checked.kind, language=checked.language,
            command=checked.command, args=checked.args, cwd=checked.cwd, env=checked.env, created_by=who.id))
        await self._record(who, "Run configuration added", "run_config.create", config, ip,
                           {"command": config.command, "cwd": config.cwd, "env": sorted(config.env)})
        return run_config_json(config, who.name)

    async def update(self, config_id: int, *, name: str | None, language: str | None, command: str | None,
                     args: list[str] | None, cwd: str | None, env: dict[str, str | None] | None, who: Person,
                     ip: str = "") -> dict[str, Any]:
        """Change what a configuration runs. `env` is a patch: a value sets a variable, null removes it,
        and a variable not named keeps its value — the screen never holds the values it does not show."""
        who.must(MACHINE, "change a run configuration")
        config, author = await self.one(config_id)
        merged_env = dict(config.env or {})
        for key, value in (env or {}).items():
            if value is None:
                merged_env.pop(key, None)
            else:
                merged_env[key] = value
        checked = _check_fields(
            name=config.name if name is None else name, kind=config.kind,
            language=config.language if language is None else language,
            command=config.command if command is None else command,
            args=list(config.args or []) if args is None else args,
            cwd=config.cwd if cwd is None else cwd, env=merged_env)
        if checked.name.lower() != config.name.lower():
            clash = await self.configs.same_name(config.project_id, checked.name, config.kind)
            if clash is not None and clash.id != config.id:
                raise Refused(f"There is already a {config.kind} configuration called {checked.name}.")
        before = {"name": config.name, "language": config.language, "command": config.command,
                  "args": list(config.args or []), "cwd": config.cwd, "env": sorted(config.env or {})}
        config.name, config.language, config.command = checked.name, checked.language, checked.command
        config.args, config.cwd, config.env = checked.args, checked.cwd, checked.env
        after = {"name": config.name, "language": config.language, "command": config.command,
                 "args": list(config.args), "cwd": config.cwd, "env": sorted(config.env)}
        changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
        # A value changed under the same name shows nowhere in the names, so say that it did.
        if env and any(v is not None for v in env.values()) and "env" not in changed:
            changed["env"] = {"values": sorted(k for k, v in env.items() if v is not None)}
        if changed:
            await self.session.flush()
            await self._record(who, "Run configuration changed", "run_config.update", config, ip, changed)
        return run_config_json(config, author)

    async def delete(self, config_id: int, who: Person, ip: str = "") -> dict[str, Any]:
        who.must(MACHINE, "remove a run configuration")
        config, _ = await self.one(config_id)
        await self._record(who, "Run configuration removed", "run_config.delete", config, ip,
                           {"command": config.command, "cwd": config.cwd})
        await self.configs.remove(config)
        return {"ok": True, "id": config_id}

    async def start(self, config_id: int, who: Person, terminals: Terminals, *, cols: int, rows: int,
                    ip: str = "") -> dict[str, Any]:
        """Open a terminal running this configuration in its folder, with its environment."""
        who.must(MACHINE, "run a configuration")
        config, _ = await self.one(config_id)
        project = await self.project(config.project_id)
        source, folder = folder_in_project(await project_roots(self.session, project), config.cwd)
        line = command_line(config, Path(os.path.realpath(source.root)), folder)
        shell = pick_shell()
        # Asked here, with no await between the question and the open: two starts that arrive together
        # (a double click, two tabs) interleave at every await, and would both find nothing running.
        if terminals.running_for(who.id, config.id) is not None:
            raise Refused(f"{config.name} is already running. Restart it, or stop it first.")
        terminal = terminals.open(owner=who.id, kind="run", title=config.name, argv=shell_argv(shell, line),
                                  cwd=folder, env=base_env({str(k): str(v) for k, v in (config.env or {}).items()}),
                                  cols=cols, rows=rows, project_id=project.id, run_config_id=config.id,
                                  command=line)
        await self.audit.record(action="run.start", user_id=who.id, target=config.name,
                                detail={"project": project.id, "config": config.id, "command": line,
                                        "cwd": str(folder), "env": sorted(config.env or {}),
                                        "terminal": terminal.id}, ip=ip)
        return terminal.json()

    async def _record(self, who: Person, what: str, audited: str, config: RunConfig, ip: str,
                      detail: dict[str, Any]) -> None:
        """Both logs. A configuration decides what runs on this machine when someone presses Run, which
        is why its changes are audited as well as shown to the team."""
        await self.activity.record(actor=who.name, actor_kind="human", action=what,
                                   detail=f"{config.name} · {config.kind} · {config.command[:160]}",
                                   project_id=config.project_id)
        await self.audit.record(action=audited, user_id=who.id, target=f"run configuration #{config.id}",
                                detail={"project": config.project_id, "name": config.name, **detail}, ip=ip)


async def open_shell(session: AsyncSession, terminals: Terminals, config: Settings, who: Person, *,
                     cwd: str | None, project_id: str | None, cols: int, rows: int, ip: str = "") -> dict[str, Any]:
    """A shell in a folder inside the roots — or, given only a project, in that project's checkout."""
    who.must(MACHINE, "open a terminal")
    project: Project | None = None
    if project_id:
        project = await RunConfigService(session).project(project_id)
    if cwd:
        folder = folder_in_roots(cwd, config)
    elif project is not None:
        folder = RunConfigService.checkout(project)
    else:
        folder = folder_in_roots("", config)
    shell = pick_shell()
    terminal = terminals.open(owner=who.id, kind="shell", title=folder.name or str(folder),
                              argv=shell_argv(shell), cwd=folder, env=base_env(), cols=cols, rows=rows,
                              project_id=project.id if project else None)
    await AuditRepository(session).record(action="terminal.open", user_id=who.id, target=str(folder),
                                          detail={"shell": shell, "terminal": terminal.id,
                                                  "project": project.id if project else None}, ip=ip)
    return terminal.json()


# ── detecting configurations ─────────────────────────────────────

#: Folders never worth looking inside for a project's entry points.
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "target",
                       ".next", ".nuxt", ".cache", ".idea", ".vscode", ".tox", ".mypy_cache", ".pytest_cache",
                       "vendor", "bin", "obj", ".worktrees", ".repos"})
#: The most suggestions one detection returns, and the most notebooks it names.
MAX_SUGGESTIONS = 80
MAX_NOTEBOOKS = 10
MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-/]*)\s*:(?!=)")


@dataclass(frozen=True, slots=True)
class Suggestion:
    name: str
    kind: str
    language: str
    command: str
    args: tuple[str, ...]
    cwd: str
    #: Where it was read from, in words: "package.json script `dev`".
    why: str

    def json(self, saved: bool) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "language": self.language, "command": self.command,
                "args": list(self.args), "cwd": self.cwd, "why": self.why, "saved": saved}


def _read_text(path: Path, limit: int = 512 * 1024) -> str | None:
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _node_runner(folder: Path) -> str:
    if (folder / "pnpm-lock.yaml").exists():
        return "pnpm run"
    if (folder / "yarn.lock").exists():
        return "yarn"
    if (folder / "bun.lockb").exists() or (folder / "bun.lock").exists():
        return "bun run"
    return "npm run"


def _in_folder(folder: Path, rel: str) -> str:
    return f"{rel}: " if rel else ""


def _detect_in(folder: Path, rel: str, checkout: Path) -> list[Suggestion]:
    """What one folder says about how it is run. Reads files; runs nothing."""
    found: list[Suggestion] = []
    label = _in_folder(folder, rel)
    python = python_for(checkout, folder)
    py = shlex.quote(os.path.relpath(python, folder)) if python.startswith(str(folder)) else shlex.quote(python)

    package = folder / "package.json"
    if package.is_file():
        text = _read_text(package)
        try:
            scripts = (json.loads(text) if text else {}).get("scripts") or {}
        except (ValueError, AttributeError):
            scripts = {}
        runner = _node_runner(folder)
        if isinstance(scripts, dict):
            for script in list(scripts)[:30]:
                found.append(Suggestion(f"{label}{script}", "run", "node", f"{runner} {shlex.quote(script)}", (),
                                        rel, f"package.json script `{script}`"))
        pkg = json.loads(text) if text and text.strip().startswith("{") else {}
        main = pkg.get("main") if isinstance(pkg, dict) else None
        if isinstance(main, str) and main.endswith((".js", ".mjs", ".cjs")) and (folder / main).is_file():
            found.append(Suggestion(f"{label}debug {main}", "debug", "node", main, (), rel,
                                    "package.json `main`"))

    makefile = next((folder / n for n in ("Makefile", "makefile", "GNUmakefile") if (folder / n).is_file()), None)
    if makefile is not None:
        targets: list[str] = []
        for line in (_read_text(makefile) or "").splitlines():
            match = MAKE_TARGET.match(line)
            if match and not match.group(1).startswith(".") and "%" not in match.group(1) \
                    and match.group(1) not in targets:
                targets.append(match.group(1))
        for target in targets[:20]:
            found.append(Suggestion(f"{label}make {target}", "run", "shell", f"make {shlex.quote(target)}", (), rel,
                                    f"{makefile.name} target `{target}`"))

    pyproject = folder / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(_read_text(pyproject) or "")
        except tomllib.TOMLDecodeError:
            data = {}
        uses_uv = (folder / "uv.lock").exists()
        scripts = dict((data.get("project") or {}).get("scripts") or {})
        for script in list(scripts)[:20]:
            command = f"uv run {shlex.quote(script)}" if uses_uv else shlex.quote(script)
            found.append(Suggestion(f"{label}{script}", "run", "python", command, (), rel,
                                    f"pyproject.toml script `{script}`"))
        poetry = dict(((data.get("tool") or {}).get("poetry") or {}).get("scripts") or {})
        for script in list(poetry)[:20]:
            found.append(Suggestion(f"{label}{script}", "run", "python", f"poetry run {shlex.quote(script)}", (),
                                    rel, f"Poetry script `{script}`"))
        text = _read_text(pyproject) or ""
        if "pytest" in text:
            found.append(Suggestion(f"{label}pytest", "run", "python", f"{py} -m pytest", (), rel,
                                    "pytest is named in pyproject.toml"))

    if (folder / "manage.py").is_file():
        found.append(Suggestion(f"{label}Django server", "run", "python", f"{py} manage.py runserver", (), rel,
                                "manage.py"))
        found.append(Suggestion(f"{label}debug Django server", "debug", "python", "manage.py",
                                ("runserver", "--noreload"), rel, "manage.py"))
    for entry in ("main.py", "app.py", "run.py", "server.py"):
        if (folder / entry).is_file():
            found.append(Suggestion(f"{label}{entry}", "run", "python", f"{py} {entry}", (), rel, f"`{entry}`"))
            found.append(Suggestion(f"{label}debug {entry}", "debug", "python", entry, (), rel, f"`{entry}`"))
    for entry in ("index.js", "server.js", "main.js", "app.js"):
        if (folder / entry).is_file() and not package.is_file():
            found.append(Suggestion(f"{label}{entry}", "run", "node", f"node {entry}", (), rel, f"`{entry}`"))

    if (folder / "go.mod").is_file():
        found.append(Suggestion(f"{label}go run", "run", "shell", "go run .", (), rel, "go.mod"))
        found.append(Suggestion(f"{label}go test", "run", "shell", "go test ./...", (), rel, "go.mod"))
    if (folder / "Cargo.toml").is_file():
        found.append(Suggestion(f"{label}cargo run", "run", "shell", "cargo run", (), rel, "Cargo.toml"))
        found.append(Suggestion(f"{label}cargo test", "run", "shell", "cargo test", (), rel, "Cargo.toml"))
    for project_file in sorted(folder.glob("*.csproj"))[:3]:
        found.append(Suggestion(f"{label}dotnet run {project_file.stem}", "run", "shell",
                                f"dotnet run --project {shlex.quote(project_file.name)}", (), rel, project_file.name))
    if (folder / "pom.xml").is_file():
        found.append(Suggestion(f"{label}mvn test", "run", "shell", "mvn test", (), rel, "pom.xml"))
    if (folder / "gradlew").is_file():
        found.append(Suggestion(f"{label}gradle build", "run", "shell", "./gradlew build", (), rel, "gradlew"))
    for compose in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        if (folder / compose).is_file():
            found.append(Suggestion(f"{label}docker compose up", "run", "shell", "docker compose up", (), rel,
                                    compose))
            break
    return found


def _notebooks(checkout: Path) -> list[Suggestion]:
    found: list[Suggestion] = []
    for dirpath, dirnames, filenames in os.walk(checkout):
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS and not d.startswith(".")]
        depth = len(Path(dirpath).relative_to(checkout).parts)
        if depth >= 3:
            dirnames[:] = []
        for name in sorted(filenames):
            if not name.endswith(".ipynb") or name.endswith(".executed.ipynb"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), checkout)
            folder = os.path.dirname(rel)
            out = name[:-len(".ipynb")] + ".executed.ipynb"
            found.append(Suggestion(f"execute {rel}", "run", "python",
                                    f"jupyter nbconvert --to notebook --execute {shlex.quote(name)} "
                                    f"--output {shlex.quote(out)}", (), folder,
                                    f"notebook `{rel}` (its outputs go to {out}; the notebook is left as it is)"))
            if len(found) >= MAX_NOTEBOOKS:
                return found
    return found


def detect(checkout: Path) -> list[Suggestion]:
    """Suggestions for a checkout: its root, each folder directly under it that has a project file of its
    own (a monorepo's `web/`, `api/`), and its notebooks. Blocking — callers use `asyncio.to_thread`."""
    root = Path(os.path.realpath(checkout))
    found = _detect_in(root, "", root)
    markers = ("package.json", "pyproject.toml", "manage.py", "go.mod", "Cargo.toml", "Makefile", "pom.xml",
               "main.py", "app.py")
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS
                          and not p.name.startswith("."))
    except OSError:
        children = []
    for child in children[:60]:
        real = Path(os.path.realpath(child))
        if not inside(real, root) or not any((child / m).exists() for m in markers):
            continue
        found.extend(_detect_in(child, child.name, root))
        if len(found) >= MAX_SUGGESTIONS:
            break
    found.extend(_notebooks(root))
    unique: dict[tuple[str, str, str], Suggestion] = {}
    for suggestion in found:
        unique.setdefault((suggestion.kind, suggestion.command, suggestion.cwd), suggestion)
    return list(unique.values())[:MAX_SUGGESTIONS]


def _under(source: code.Source, found: list[Suggestion]) -> list[Suggestion]:
    """A further source's suggestions named and placed under its label, the way the project names its
    paths — so `dev` in the `web` checkout is saved as `web: dev` running in `web`."""
    if source.primary:
        return found
    return [Suggestion(f"{source.label}: {s.name}", s.kind, s.language, s.command, s.args,
                       "/".join(p for p in (source.label, s.cwd) if p), s.why) for s in found]


async def suggestions(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """What each of the project's checkouts suggests, each marked `saved` when the project already has
    that configuration."""
    service = RunConfigService(session)
    project = await service.project(project_id)
    sources = [s for s in await project_roots(session, project) if s.ready and s.root.is_dir()]
    found: list[Suggestion] = []
    for source in sources:
        found.extend(_under(source, await asyncio.to_thread(detect, source.root)))
    capped = len(found) >= MAX_SUGGESTIONS
    found = found[:MAX_SUGGESTIONS]
    have = {(c.kind, c.command, c.cwd) for c, _ in await service.configs.listed(project.id, kind=None,
                                                                                 limit=MAX_CONFIGS, offset=0)}
    return {"checkout": str(Path(os.path.realpath(sources[0].root))),
            "sources": [{"label": s.label, "root": str(Path(os.path.realpath(s.root))), "primary": s.primary}
                        for s in sources],
            "suggestions": [s.json((s.kind, s.command, s.cwd) in have) for s in found],
            "capped": capped}
