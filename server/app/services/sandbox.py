"""The fence around every command the runtime runs.

A worktree already keeps an agent's *edits* to itself — that is git, and it is real. It does nothing
about the one thing the runtime genuinely executes: the project's own test command, its own checks,
and the commands a custom tool names. Those are the project's commands, not a model's, but they still
run as the account the API runs as, and `npm test` on an unfamiliar repository is somebody else's
code with your home directory in reach. So the command is handed to the operating system's own
sandbox, and what the sandbox allows is written down here rather than assumed:

* it may **write** inside the run's worktree and the temp directory, and nowhere else;
* it may **read** what a build reads — interpreters, toolchains, the repository — because a test that
  cannot read Python cannot run;
* it may **not reach the network**, unless somebody turned that on knowingly, because a test suite
  that installs packages is a real thing and so is a test suite that posts your environment somewhere.

Three facts about this file are load-bearing:

**It never pretends.** A machine with no sandbox gets no sandbox, and `Sandbox.words()` says so in
the run's log and on the settings screen, in the same sentence a person would use. A silent fallback
to "no fence" would be the one thing this product never does.

**It says exactly what it confines.** macOS Seatbelt and bubblewrap confine writes and the network;
`unshare` on a Linux without bubblewrap confines only the network, and says only that. `confines`
carries the two answers separately so no screen has to guess.

**It wraps, it does not spawn.** `wrap()` returns an argv, and whoever was already starting the
process starts this one instead — so the sandbox cannot change how output is read, how a process is
killed, or how long it is given.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Setting
from ..settings import Settings, settings as get_settings

#: The workspace's answer to "may a sandboxed command reach the network?", beside the runtime's other
#: remembered answers (`runtime.tests.<project>`, `runtime.checks.<project>.<name>`).
SETTING_KEY = "runtime.sandbox"

SEATBELT = "seatbelt"
BUBBLEWRAP = "bubblewrap"
UNSHARE = "unshare"
NONE = "none"

NAMES = {SEATBELT: "macOS Seatbelt", BUBBLEWRAP: "bubblewrap", UNSHARE: "Linux namespaces (unshare)",
         NONE: "no sandbox"}

#: Where Seatbelt's own tooling lives. It ships with macOS; it is not installed, so it is looked for
#: at its path rather than on PATH.
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: The character devices a program expects to be able to write to. Without these a shell cannot even
#: redirect to /dev/null, which every test runner in the world does.
DEVICES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/tty", "/dev/stdout",
           "/dev/stderr", "/dev/fd", "/dev/ptmx")


@dataclass(frozen=True, slots=True)
class Policy:
    """What the workspace wants: a sandbox at all, and whether it may reach the network."""

    enabled: bool = True
    network: bool = False


@dataclass(frozen=True, slots=True)
class Sandbox:
    """One command's fence: what the machine offers, what this command may write, and the network."""

    kind: str
    #: Real paths the command may write to. Empty when nothing confines writes.
    writable: tuple[str, ...]
    network: bool
    #: Why there is no fence, when there is none — in words a person can act on.
    why: str = ""

    @property
    def name(self) -> str:
        return NAMES.get(self.kind, self.kind)

    @property
    def confines_writes(self) -> bool:
        return self.kind in (SEATBELT, BUBBLEWRAP)

    @property
    def confines_network(self) -> bool:
        return not self.network and self.kind in (SEATBELT, BUBBLEWRAP, UNSHARE)

    def wrap(self, argv: list[str] | tuple[str, ...]) -> list[str]:
        """The argv to start instead. With no sandbox it is the command itself, unchanged."""
        command = list(argv)
        if not command:
            return command
        if self.kind == SEATBELT:
            return [SANDBOX_EXEC, "-p", self._seatbelt(), *command]
        if self.kind == BUBBLEWRAP:
            return [*self._bwrap(), *command]
        if self.kind == UNSHARE:
            # Only the network, and only when it is off: with the network allowed there is nothing
            # for unshare to take away, and `detect` has already said so.
            return ["unshare", "--net", "--", *command]
        return command

    def words(self) -> str:
        """One line, for the run's log, a gate's payload and the settings screen."""
        if self.kind == NONE:
            return f"Sandbox: none. {self.why}".strip()
        parts = [f"Sandbox: {self.name}"]
        parts.append("writes limited to the worktree and the temp directory" if self.confines_writes
                     else "writes are not confined by this sandbox")
        parts.append("network off" if self.confines_network else "network allowed")
        return " · ".join(parts)

    def json(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "network": self.network,
                "confinesWrites": self.confines_writes, "confinesNetwork": self.confines_network,
                "writable": list(self.writable), "why": self.why, "words": self.words()}

    # ── the profiles themselves ──────────────────────────────────
    def _seatbelt(self) -> str:
        """A Seatbelt profile that starts from `allow default` and takes two things away.

        Starting from `deny default` and naming every right a build needs is the stricter shape and
        the wrong one here: a toolchain nobody anticipated — a Gradle daemon, a Go build cache, a
        language server a test spawns — fails with an error that names nothing, and the person is
        left debugging the sandbox instead of their code. What this profile takes away is exactly
        what the run screen claims it takes away: writing outside the worktree, and the network.
        """
        # No writable paths at all is not a profile a command ever gets (`around` always names the
        # folder and the temp directories); it is what `preview` holds for a screen. Written out
        # anyway, because an empty `(allow file-write*)` is a profile sandbox-exec refuses to parse.
        allowed = ("(allow file-write*\n"
                   + "\n".join(f'  (subpath {_sbpl(path)})' for path in self.writable) + ")\n"
                   if self.writable else "")
        devices = " ".join(f"(literal {_sbpl(d)})" for d in DEVICES)
        network = "(allow network*)" if self.network else (
            ";; No IP at all. Unix sockets are left alone — a build talks to its own daemons over them.\n"
            "(deny network-outbound (remote ip))\n(deny network-inbound (remote ip))")
        return ("(version 1)\n"
                ";; NeuroCode: the project's own command, inside its run's worktree.\n"
                "(allow default)\n"
                "(deny file-write*)\n"
                f"{allowed}"
                f"(allow file-write-data {devices})\n"
                f"{network}\n")

    def _bwrap(self) -> list[str]:
        """bubblewrap: the whole filesystem read-only, the writable paths bound back over it.

        `--die-with-parent` matters as much as the binds do — the runtime kills a command by killing
        its process group, and a bwrap that outlived the kill would leave the test running.
        """
        argv = ["bwrap", "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/",
                "--dev", "/dev", "--proc", "/proc"]
        for path in self.writable:
            argv += ["--bind", path, path]
        if not self.network:
            argv.append("--unshare-net")
        return [*argv, "--"]


def _sbpl(path: str) -> str:
    """A path as a Seatbelt string literal. Quotes and backslashes are escaped rather than refused:
    a folder called `my "work"` is a folder, and a profile that dropped it would silently widen."""
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def detect(*, platform: str | None = None, which=shutil.which,
           exists=os.path.exists) -> tuple[str, str]:
    """What this machine has, and — when it has nothing — why, in words a person can act on.

    Injectable rather than reading the world directly, so the Linux answers can be tested on a Mac
    and the Mac answer on a Linux box. Nothing here runs a command to find out: `sandbox-exec` is
    part of macOS and `bwrap`/`unshare` are on PATH or they are not.
    """
    system = platform or sys.platform
    if system == "darwin":
        if exists(SANDBOX_EXEC):
            return SEATBELT, ""
        return NONE, (f"This Mac has no {SANDBOX_EXEC}, which every macOS ships with. Commands run "
                      "with the same rights as the account running NeuroCode.")
    if system.startswith("linux"):
        if which("bwrap"):
            return BUBBLEWRAP, ""
        if which("unshare"):
            return UNSHARE, ("bubblewrap is not installed, so only the network is confined. "
                             "Install bubblewrap (`apt install bubblewrap`) to confine writes too.")
        return NONE, ("Neither bubblewrap nor unshare is on this machine. Install bubblewrap "
                      "(`apt install bubblewrap`) to confine what a command may write and reach.")
    return NONE, (f"NeuroCode has no sandbox for {system}. Commands run with the same rights as the "
                  "account running NeuroCode.")


def temp_dirs() -> tuple[str, ...]:
    """The temp directories a build actually uses, as the kernel sees them.

    Real paths, because on a Mac `/tmp` is a symlink to `/private/tmp` and `$TMPDIR` lives under
    `/private/var/folders` — a profile naming the symlink allows nothing at all.
    """
    found: list[str] = []
    for path in (tempfile.gettempdir(), "/tmp", "/var/tmp"):
        real = os.path.realpath(path)
        if os.path.isdir(real) and real not in found:
            found.append(real)
    return tuple(found)


def around(folder: Path | str, policy: Policy, *, extra: tuple[str, ...] = ()) -> Sandbox:
    """The fence for one command: this folder and the temp directories, and nothing else.

    `extra` is for the further sources a run checks out beside its own worktree — a command that must
    write in two worktrees gets both, named, rather than a widened profile nobody can read.
    """
    if not policy.enabled:
        return Sandbox(NONE, (), policy.network,
                       why="Sandboxing is switched off for this server (NEUROCODE_SANDBOX=false).")
    kind, why = detect()
    if kind == UNSHARE and policy.network:
        return Sandbox(NONE, (), True,
                       why="Only the network can be confined on this machine, and the network is allowed, "
                           "so nothing is confined. Install bubblewrap to confine writes too.")
    writable = [os.path.realpath(str(folder)), *(os.path.realpath(p) for p in extra), *temp_dirs()]
    seen: list[str] = []
    for path in writable:
        if path not in seen:
            seen.append(path)
    return Sandbox(kind, tuple(seen), policy.network, why=why)


def preview(policy: Policy) -> Sandbox:
    """The same answer a command would get, with no command and no folder: what a settings screen
    shows. `writable` is empty because there is no worktree yet — every run gets its own, and naming
    a folder here that no run will write in would be describing something that does not happen."""
    return replace(around("/", policy), writable=())


def env_policy(config: Settings | None = None) -> Policy:
    """What the deployment asks for, before the workspace has said anything."""
    cfg = config or get_settings()
    return Policy(enabled=cfg.sandbox, network=cfg.sandbox_network)


async def read_policy(open_session: AsyncSession, config: Settings | None = None) -> Policy:
    """The deployment's answer, with the workspace's own on top of it.

    The env is a floor and not a default: a server started with `NEUROCODE_SANDBOX=false` stays
    unsandboxed whatever the workspace row says, because turning the fence *on* from a screen when
    the machine it protects was deliberately opened would be a promise this cannot keep. The network
    is the other way round — the workspace is who knows whether today's test suite installs packages.
    """
    cfg = config or get_settings()
    row = await open_session.get(Setting, SETTING_KEY)
    held = row.value if row and isinstance(row.value, dict) else {}
    return Policy(enabled=cfg.sandbox and bool(held.get("enabled", True)),
                  network=bool(held.get("network", cfg.sandbox_network)))


async def write_policy(open_session: AsyncSession, *, enabled: bool, network: bool) -> Policy:
    """Store the workspace's answer. Whoever calls this writes the audit line — this only stores."""
    row = await open_session.get(Setting, SETTING_KEY)
    value = {"enabled": bool(enabled), "network": bool(network)}
    if row is None:
        open_session.add(Setting(key=SETTING_KEY, value=value))
    else:
        row.value = value
    await open_session.flush()
    return await read_policy(open_session)
