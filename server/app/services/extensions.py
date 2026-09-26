"""Skills, commands, hooks and plugins: what Claude Code keeps on this machine, read and never run.

None of it is stored here. A skill is a SKILL.md in a folder, a command is a markdown file, a hook is an
entry in a settings file, a plugin is a line in `installed_plugins.json` — so the truth is on disk, and
it is read from disk every time a screen or a session asks. The database holds only what a person set
(a skill switched off, a plugin kept out of sessions) and what really happened (a session loading a
skill, a command turned into a turn).

Three places are read: the user-level Claude home (`~/.claude`, or wherever `NEUROCODE_CLAUDE_HOME` or
`CLAUDE_CONFIG_DIR` points), the project's own checkout (`.claude/` inside it), and the plugins Claude
Code installed and enabled. Every file found is resolved and must still be inside the root it was found
under, so a symlink cannot walk discovery somewhere else; every count and size is capped.

Hooks are discovered, redacted and shown, and a hook is refused by default: a repository's hook is
somebody else's shell script, and nothing runs it because it is there. A person may allow a specific
one with a tool rule of kind `hook` — matched on `event/command`, the command as it is really written —
and only then does it fire at its event, inside the project's checkout, under the machine's roots, with
a timeout and a cap on what it may say back. It runs behind the OS sandbox a run's test command runs
behind, and without the API's own secrets in its environment. Every firing is in the activity log. A
command's !`shell` lines are still never run: they are replaced with a note when a session expands it.

Plugins are read from Claude Code's own cache, which NeuroCode never writes to, and from a folder of the
workspace's own — `NEUROCODE_PLUGINS_DIR`, or `.plugins` beside the clones. A plugin installed there came
from a person naming an https git URL or a folder inside the machine's roots; it can be listed and
removed again. The registry is a JSON file of plugins somebody wrote down as worth offering, and that is
all it is: there is no index anybody fetches from, and the screen says so in those words.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import math
import os
import re
import secrets as token
import shlex
import shutil
import subprocess
import time
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..agent.env import child_env
from ..agent.git import git
from ..data.base import utcnow
from ..models import Chat, ChatMessage, Project
from ..repositories import ActivityRepository, AuditRepository, NotFound, ProjectRepository
from ..repositories.work import PrefRepository
from ..schemas.extensions import command_json, hook_json, skill_json
from ..settings import settings
from . import machine, sandbox
from .code import checkout
from .errors import Refused
from .identity import Person
from .tool_rules import decide

log = logging.getLogger(__name__)

#: Files read from one root, at most. A skills folder with more than this is not a skills folder.
MAX_PER_ROOT = 500
#: A file bigger than this is skipped, not truncated: it is not a skill or a command anyone wrote by hand.
MAX_FILE_BYTES = 256_000
MAX_SKILL_BODY = 64_000
MAX_COMMAND_BODY = 32_000
MAX_MARKETPLACE = 500
MAX_HOOKS = 500
#: Rows a usage question may return. One per skill or command, so this is far above any real count.
MAX_USAGE_ROWS = 1_000

#: A hook that has not finished in this long is killed, whatever its own `timeout` says — a settings file
#: is not allowed to hold a session open. Its own timeout still applies when it is shorter.
MAX_HOOK_SECONDS = 60
#: What one firing may say back. A hook that prints a log is cut, and the answer says it was.
MAX_HOOK_OUTPUT = 20_000
#: Exit code 2 is Claude Code's own "refuse what was about to happen", and only on the blocking events.
REFUSAL_CODE = 2
#: A plugin folder the workspace owns is copied or cloned at most this big, and with at most this many
#: files. A plugin is skills, commands and agents in markdown — anything larger is not one.
MAX_PLUGIN_FILES = 4_000
MAX_PLUGIN_BYTES = 80 * 1024 * 1024
CLONE_TIMEOUT = 180
#: A plugin name is a folder name, so it is the narrow set a folder name may be here.
PLUGIN_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
#: The marketplace a workspace plugin belongs to: its own. Claude Code never sees these.
WORKSPACE_MARKET = "workspace"
#: Installing a plugin fetches somebody else's code, and removing one deletes a folder, so both need the
#: right that already governs what the runtime may reach for.
MANAGE = "mcp:manage"
#: Reading the hooks list already needs this, because a hook is a command line. Running one needs no more
#: than reading one does — a rule has already said yes, and without a rule the button refuses too.
HOOKS_WRITE = "settings:write"

#: The events where Claude Code lets a hook's exit code 2 refuse what was about to happen.
BLOCKING_EVENTS = frozenset({"PreToolUse", "UserPromptSubmit", "Stop", "SubagentStop"})
#: Pref keys a person sets on the screens. `skills.enabled` is the one the end-to-end test checks.
SKILLS_PREF, COMMANDS_PREF, PLUGINS_PREF = "skills.enabled", "commands.enabled", "plugins.installed"

SCOPE_RANK = {"project": 0, "global": 1, "plugin": 2}
COMMAND_TEXT = re.compile(r"^/([\w:.-]+)(?:\s+(.*))?$", re.S)
SHELL_LINE = re.compile(r"!`([^`\n]+)`")
# `KEY=value`, `--token value` and `Bearer value`: the ways a secret ends up typed into a hook command.
SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:token|key|secret|password|passwd|pwd|auth)[A-Z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)")
SECRET_FLAG = re.compile(r"(?i)(--?(?:token|api-key|key|secret|password|auth)[= ])(\"[^\"]*\"|'[^']*'|\S+)")
BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
#: `Authorization: Basic …`, `x-api-key: …`, `X-Auth-Token: …` — a credential sent as a header, which is how
#: most webhook hooks carry one. The scheme word (Basic, Token) is kept; what follows it is not.
SECRET_HEADER = re.compile(
    r"(?i)((?:authorization|proxy-authorization|x-[\w-]*(?:key|token|secret|auth)|api-?key|private-token)"
    r"\s*:\s*(?:(?:basic|token|bearer|digest)\s+)?)[^\s\"']+")
#: `curl -u admin:hunter22`, `--user admin:hunter22`: the password half of a user:password pair.
USER_PASS = re.compile(r"(\s(?:-u|--user)[=\s]+[\"']?[^\s:\"']+:)[^\s\"']+")
#: `mysql -psecret`: a password glued to its flag, which the flag pattern above cannot see.
GLUED_PASS = re.compile(r"(\b(?:mysql|mysqldump|mariadb)\b[^|;&]*?\s-p)(?=\S)[^\s\"']+")
#: Tokens that are secret wherever they appear, by their own shape — and webhook URLs whose path is the secret.
KNOWN_TOKENS = re.compile(
    r"\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})")
WEBHOOK_PATH = re.compile(
    r"(https?://(?:hooks\.slack\.com/services|discord(?:app)?\.com/api/webhooks"
    r"|[\w.-]*webhook\.office\.com/webhookb2|outlook\.office\.com/webhook)/)[^\s\"']+")


def claude_home() -> Path:
    """Where Claude Code keeps its user-level configuration on this machine.

    Read from the environment rather than app/settings.py so a test or the end-to-end run can point it
    at a fixture; NEUROCODE_CLAUDE_HOME wins, then Claude Code's own CLAUDE_CONFIG_DIR, then ~/.claude.
    """
    chosen = os.environ.get("NEUROCODE_CLAUDE_HOME") or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return Path(os.path.expanduser(chosen))


def redact(command: str) -> str:
    """A hook command as it may be shown to anyone signed in, a Viewer included.

    Hooks are where people paste a webhook, and a webhook's credential turns up in half a dozen shapes:
    `TOKEN=…`, `--password …`, a Bearer or Basic header, `curl -u user:pass`, `mysql -ppass`, a token
    recognisable by its own prefix, or a URL whose path is itself the secret. Each is covered, because
    missing one sends it to every browser that opens the Hooks page.
    """
    out = onboarding.redact(command)
    out = WEBHOOK_PATH.sub(lambda m: f"{m.group(1)}***", out)
    out = SECRET_HEADER.sub(lambda m: f"{m.group(1)}***", out)
    out = USER_PASS.sub(lambda m: f"{m.group(1)}***", out)
    out = GLUED_PASS.sub(lambda m: f"{m.group(1)}***", out)
    out = SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}=***", out)
    out = SECRET_FLAG.sub(lambda m: f"{m.group(1)}***", out)
    out = BEARER.sub(lambda m: f"{m.group(1)}***", out)
    return KNOWN_TOKENS.sub("***", out)


# ── reading files ────────────────────────────────────────────────
def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _split_list(value: str) -> list[str]:
    """`Read, Grep, Bash(git add:*)` → three items. Commas inside brackets belong to the item."""
    items, depth, current = [], 0, ""
    for ch in value:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += ch
    items.append(current)
    return [_unquote(i) for i in items if i.strip()]


def front_matter(text: str) -> tuple[dict[str, Any], str, bool]:
    """The block between the leading `---` lines, the body after it, and whether the block was readable.

    Skills and commands use a small, flat subset of YAML — `key: value`, `[a, b]` lists, `- item` lists
    and folded `>` text — so that subset is what is read. Anything else is kept as its text rather than
    guessed at, and a block that never closes is reported as unreadable instead of failing the list.
    """
    if not text.startswith("---"):
        return {}, text, True
    lines = text.splitlines(keepends=True)
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text, False
    meta: dict[str, Any] = {}
    key: str | None = None
    block: list[str] = []

    def close() -> None:
        if key is None or not block:
            return
        if all(b.lstrip().startswith("- ") for b in block):
            meta[key] = [_unquote(b.lstrip()[2:]) for b in block]
        else:
            meta[key] = " ".join(b.strip() for b in block)

    for line in (raw.rstrip("\r\n") for raw in lines[1:end]):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t-" and ":" in line:
            close()
            name, _, value = line.partition(":")
            key, block = name.strip(), []
            value = value.strip()
            if value in ("", ">", "|", ">-", "|-"):
                meta[key] = ""
            elif value.startswith("[") and value.endswith("]"):
                meta[key] = _split_list(value[1:-1])
            else:
                meta[key] = _unquote(value)
        elif key is not None:
            block.append(line)
    close()
    body = "".join(lines[end + 1:]).lstrip("\r\n")
    return meta, body, True


def _words(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return _split_list(value)
    return []


def _inside(root: Path, path: Path) -> bool:
    """True when `path`, followed through any symlink, is still under `root`."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            log.info("skipped %s: larger than %d bytes", path, MAX_FILE_BYTES)
            return None
        return path.read_text(errors="replace")
    except OSError as e:
        log.info("could not read %s: %s", path, e)
        return None


def _json(path: Path) -> dict[str, Any] | None:
    text = _read(path) if path.is_file() else None
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.info("unreadable JSON in %s: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def _display(path: Path) -> str:
    """A path as a person reads it: under their home folder it starts with ~."""
    text, mine = str(path), os.path.expanduser("~")
    return "~" + text[len(mine):] if text == mine or text.startswith(mine + os.sep) else text


# ── what is found ────────────────────────────────────────────────
@dataclass(frozen=True)
class Root:
    scope: str
    path: str
    exists: bool


@dataclass(frozen=True)
class Plugin:
    """An installed plugin, as Claude Code recorded it."""

    id: str                      # name@marketplace
    name: str
    marketplace: str
    path: Path
    version: str
    updated_at: str
    enabled_in_claude: bool
    used: bool                   # not switched off for NeuroCode sessions


@dataclass(frozen=True)
class SkillFile:
    key: str
    slug: str
    name: str
    description: str
    scope: str
    project: str
    source: str
    path: Path
    tools: tuple[str, ...]
    triggers: tuple[str, ...]
    version: str
    author: str
    tokens: int
    body: str
    truncated: bool


@dataclass(frozen=True)
class CommandFile:
    key: str
    name: str                    # what a person types, with its slash
    stem: str
    plugin: str | None           # the plugin's bare name, when a plugin provides it
    description: str
    scope: str
    source: str
    args: str
    agent: str
    model: str | None
    tools: tuple[str, ...]
    body: str
    truncated: bool


@dataclass(frozen=True)
class HookEntry:
    id: str
    event: str
    matcher: str
    type: str
    command: str
    scope: str
    source: str
    blocking: bool
    description: str
    timeout_s: int | None
    #: The command exactly as the settings file writes it. A rule is matched against this and a firing
    #: runs this, because a rule matched against the redacted text would allow something else. It is
    #: never put in an answer — `hook_json` does not read it, and nothing here returns it.
    raw: str = ""

    @property
    def subject(self) -> str:
        """What a `hook` rule's pattern is matched against: the event, then the command as written.

        This holds the command unredacted, so it never leaves the server: the hooks list is read by
        anyone with `settings:write`, and the whole point of the redaction is that a secret pasted into
        a settings file does not reach a browser. `pattern` is what a screen is given instead.
        """
        return f"{self.event}/{self.raw}"

    @property
    def pattern(self) -> str:
        """A `hook` rule's pattern that would allow exactly this hook, fit to be shown to a person.

        It is built from the redacted command, and it still matches the real one: what redaction leaves
        behind is `***`, and a glob reads that as "anything". So a person can copy this off the screen,
        paste it into a rule, and have it match — without the secret ever being shown to them.
        """
        return f"{self.event}/{self.command}"


@dataclass
class Catalogue:
    skills: list[SkillFile] = field(default_factory=list)
    commands: list[CommandFile] = field(default_factory=list)
    plugins: list[Plugin] = field(default_factory=list)
    roots: list[Root] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def installed_plugins(home: Path, use: dict[str, bool]) -> list[Plugin]:
    """The plugins Claude Code installed, with only the two settings keys that concern them read.

    `installed_plugins.json` is version 2 today; any other version is reported as unreadable rather
    than guessed at. An installPath outside `<home>/plugins` is skipped: only Claude Code's own plugin
    cache is read, never a path a file points somewhere else.
    """
    data = _json(home / "plugins" / "installed_plugins.json")
    if data is None:
        return []
    if data.get("version") != 2 or not isinstance(data.get("plugins"), dict):
        log.info("installed_plugins.json is version %r, which this reader does not know", data.get("version"))
        return []
    settings = _json(home / "settings.json") or {}
    enabled = settings.get("enabledPlugins") if isinstance(settings.get("enabledPlugins"), dict) else {}
    cache = home / "plugins"
    out: list[Plugin] = []
    for pid, entries in sorted(data["plugins"].items()):
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            continue
        entry = entries[0]
        path = Path(str(entry.get("installPath", "")))
        if not path.is_absolute() or not _inside(cache, path) or not path.is_dir():
            continue
        name, _, market = pid.partition("@")
        out.append(Plugin(id=pid, name=name, marketplace=market, path=path.resolve(),
                          version=str(entry.get("version", "")), updated_at=str(entry.get("lastUpdated", "")),
                          enabled_in_claude=enabled.get(pid) is True, used=use.get(pid, True) is not False))
    return out


# ── the workspace's own plugin folder ────────────────────────────
def workspace_plugins_dir() -> Path:
    """Where a plugin this workspace installed lives. Never Claude Code's cache: that folder belongs to
    Claude Code, and writing into it would arm somebody else's hooks inside its configuration.

    Read from the environment first so a test can point it somewhere of its own, exactly as `claude_home`
    is; otherwise it sits beside the clones, where everything else this server writes already lives.
    """
    chosen = os.environ.get("NEUROCODE_PLUGINS_DIR")
    return Path(os.path.expanduser(chosen)) if chosen else Path(settings().repos_dir).parent / ".plugins"


def workspace_plugins(use: dict[str, bool]) -> list[Plugin]:
    """The plugins this workspace installed: one folder each, directly under the plugins folder.

    They are `enabled_in_claude=True` because Claude Code has nothing to say about them — nobody else
    installed them and nobody else can switch them off. Whether their skills and commands reach sessions
    is the same switch every plugin has, `plugins.installed`.
    """
    root = workspace_plugins_dir()
    if not root.is_dir():
        return []
    out: list[Plugin] = []
    for path in sorted(root.iterdir())[:MAX_PER_ROOT]:
        if not path.is_dir() or path.is_symlink() or not _inside(root, path):
            continue
        manifest = _json(path / ".claude-plugin" / "plugin.json") or {}
        pid = f"{path.name}@{WORKSPACE_MARKET}"
        stamp = _json(path / ".claude-plugin" / "neurocode.json") or {}
        out.append(Plugin(id=pid, name=path.name, marketplace=WORKSPACE_MARKET, path=path.resolve(),
                          version=str(manifest.get("version", "")), updated_at=str(stamp.get("installedAt", "")),
                          enabled_in_claude=True, used=use.get(pid, True) is not False))
    return out


def _is_git_url(source: str) -> bool:
    return source.startswith(("http://", "https://", "git@", "ssh://", "git://"))


def _checked_source(source: str) -> tuple[str, str]:
    """What a person may install from, and which of the two it is: ("git", url) or ("path", folder).

    Only https is accepted for a git URL. `git@` and `ssh://` would use this account's own keys, and
    `http://` and `git://` carry the repository over a connection nobody checked — a plugin is code that
    then runs on this machine, so neither is a thing this server does on somebody's word. A local path
    goes through the machine door like every other path a request names.
    """
    text = (source or "").strip()
    if not text:
        raise Refused("Name a git URL to clone, or a folder on this machine to copy.", status=422)
    if _is_git_url(text):
        parts = urllib.parse.urlsplit(text)
        if parts.scheme != "https" or not parts.hostname:
            raise Refused("A plugin is cloned from an https:// URL only. ssh:// and git@ would use this "
                          "machine's own keys, and http:// is not checked on the way — clone it yourself "
                          "and install from the folder instead.", status=422)
        return "git", text
    return "path", str(machine.inside(text))


def _weigh_plugin_folder(root: Path) -> tuple[int, int]:
    """How many files a folder holds and how large they are, stopping the moment it is past the ceiling —
    so a folder nobody should be copying is not first walked in full."""
    files = size = 0
    for here, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            path = Path(here) / name
            if path.is_symlink():
                continue
            files += 1
            try:
                size += path.stat().st_size
            except OSError:
                continue
            if files > MAX_PLUGIN_FILES or size > MAX_PLUGIN_BYTES:
                return files, size
    return files, size


def _plugin_contents(path: Path) -> dict[str, int]:
    """What a folder would bring, so a person is told before it is kept rather than after."""
    return _count_contents(path)


def install_plugin(source: str, name: str) -> dict[str, Any]:
    """Blocking. Put a plugin in the workspace's plugin folder, from an https git URL or a local folder.

    Nothing from the outside is trusted here beyond being copied in: the clone is shallow and its `.git`
    is dropped, so what stays is files rather than a repository that could be pulled from again; symlinks
    are not copied, so a link cannot reach out of the folder later; the size and file count are capped
    before anything is kept. A folder that holds none of the four things a plugin is made of is refused
    with those four things named, because a person who typed the wrong path deserves to be told which.
    """
    machine.enabled()
    if not PLUGIN_NAME.match(name or ""):
        raise Refused("A plugin's name is 1 to 40 characters of lowercase letters, digits, dot, dash or "
                      "underscore — it is the folder it is kept in.", status=422)
    kind, where = _checked_source(source)
    root = workspace_plugins_dir()
    target = root / name
    if target.exists():
        raise Refused(f"A plugin called {name} is already installed here. Remove it first, or install "
                      f"under another name.", status=409)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".installing-{name}-{token.token_hex(4)}"
    try:
        if kind == "git":
            done = git(["clone", "--depth", "1", "--no-tags", "--single-branch", "--", where, str(staging)],
                       root, timeout=CLONE_TIMEOUT)
            if done.returncode != 0:
                raise Refused(f"git could not clone {where}: {done.stderr.strip()[:300] or 'it said nothing'}.")
            shutil.rmtree(staging / ".git", ignore_errors=True)
        else:
            files, size = _weigh_plugin_folder(Path(where))
            if files > MAX_PLUGIN_FILES or size > MAX_PLUGIN_BYTES:
                raise Refused(f"{where} holds {files} files and {size // 1_000_000} MB. A plugin is skills, "
                              f"commands, agents and hooks — this is a working folder, not a plugin.", status=422)
            shutil.copytree(where, staging, symlinks=False, ignore=shutil.ignore_patterns(".git"),
                            ignore_dangling_symlinks=True)
        brought = _plugin_contents(staging)
        if not any(brought.values()) and not (staging / ".claude-plugin" / "plugin.json").is_file():
            raise Refused(f"There is no plugin in {source}: nothing under skills/, commands/, agents/ or "
                          f"hooks/, and no .claude-plugin/plugin.json. Nothing was kept.", status=422)
        (staging / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        # Where it came from, written beside it: the screen shows it, and a person who inherits this
        # machine can tell a plugin somebody chose from a folder that merely appeared.
        (staging / ".claude-plugin" / "neurocode.json").write_text(json.dumps(
            {"source": source if kind == "git" else where, "sourceKind": kind,
             "installedAt": utcnow().isoformat()}, indent=2))
        staging.rename(target)
    except Refused:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (OSError, shutil.Error, subprocess.SubprocessError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise Refused(f"Could not install {name}: {e}") from e
    return {"id": f"{name}@{WORKSPACE_MARKET}", "name": name, "path": _display(target),
            "provides": brought, "source": source, "sourceKind": kind}


def remove_plugin(name: str) -> dict[str, Any]:
    """Blocking. Delete one plugin folder this workspace installed. Only a direct child of that folder,
    resolved, is ever removed — a name is not a path, and nothing else on this machine is reachable."""
    machine.enabled()
    root = workspace_plugins_dir()
    if not PLUGIN_NAME.match(name or ""):
        raise NotFound(f"plugin {name}")
    target = root / name
    if not target.is_dir() or target.is_symlink() or not _inside(root, target):
        raise NotFound(f"plugin {name}")
    shutil.rmtree(target)
    return {"ok": True, "id": f"{name}@{WORKSPACE_MARKET}", "name": name}


def registry() -> dict[str, Any]:
    """The plugins somebody wrote down as worth offering: `registry.json` in the plugins folder.

    This is the whole of the "marketplace" the screen offers, and the screen says so. Nothing is fetched
    from anywhere — a registry is a file a person or a team keeps, and if it is not there the answer is
    the path it would be at, so the screen can say where to put one.
    """
    root = workspace_plugins_dir()
    path = root / "registry.json"
    data = _json(path)
    entries: list[dict[str, Any]] = []
    for item in (data.get("plugins") if data else None) or []:
        if not isinstance(item, dict) or not item.get("name") or not item.get("source"):
            continue
        entries.append({"name": str(item["name"])[:80], "source": str(item["source"])[:500],
                        "description": str(item.get("description", ""))[:500],
                        "publisher": str(item.get("publisher", ""))[:120],
                        "category": str(item.get("category") or "uncategorized")[:60]})
        if len(entries) >= MAX_MARKETPLACE:
            break
    return {"path": _display(path), "exists": path.is_file(),
            "name": str((data or {}).get("name", "")) or "This machine's registry", "plugins": entries}


def _skills_under(root: Path, scope: str, key_prefix: str, project: str, shown: str,
                  plugin: Plugin | None, cat: Catalogue) -> None:
    if not root.is_dir():
        return
    manifest = _json(plugin.path / ".claude-plugin" / "plugin.json") if plugin else None
    plugin_author = ""
    if manifest and isinstance(manifest.get("author"), dict):
        plugin_author = str(manifest["author"].get("name", ""))
    for path in sorted(root.glob("*/SKILL.md"))[:MAX_PER_ROOT]:
        if not _inside(root, path):
            continue
        text = _read(path)
        if text is None:
            continue
        meta, body, readable = front_matter(text)
        slug = path.parent.name
        if not readable:
            cat.unreadable.append(f"{shown}/{slug}/SKILL.md")
        cat.skills.append(SkillFile(
            key=f"{key_prefix}/{slug}", slug=slug, name=str(meta.get("name") or slug),
            description=str(meta.get("description", "")), scope=scope, project=project,
            source=f"{shown}/{slug}/SKILL.md", path=path,
            tools=tuple(_words(meta.get("allowed-tools"))), triggers=tuple(_words(meta.get("triggers"))),
            version=str(meta.get("version") or (plugin.version if plugin else "")),
            author=str(meta.get("author") or plugin_author),
            tokens=math.ceil(len(body) / 4), body=body[:MAX_SKILL_BODY], truncated=len(body) > MAX_SKILL_BODY))


def _commands_under(root: Path, scope: str, key_prefix: str, shown: str, plugin: Plugin | None,
                    cat: Catalogue) -> None:
    if not root.is_dir():
        return
    found = root.glob("*.md") if plugin else root.rglob("*.md")
    for path in sorted(found)[:MAX_PER_ROOT]:
        if not _inside(root, path):
            continue
        text = _read(path)
        if text is None:
            continue
        meta, body, readable = front_matter(text)
        rel = path.relative_to(root).as_posix()
        if not readable:
            cat.unreadable.append(f"{shown}/{rel}")
        stem = path.stem
        cat.commands.append(CommandFile(
            key=f"{key_prefix}/{stem}", name=f"/{plugin.name}:{stem}" if plugin else f"/{stem}", stem=stem,
            plugin=plugin.name if plugin else None, description=str(meta.get("description", "")),
            scope=scope, source=f"{shown}/{rel}", args=str(meta.get("argument-hint", "")),
            agent=str(meta.get("agent") or "Session"), model=str(meta["model"]) if meta.get("model") else None,
            tools=tuple(_words(meta.get("allowed-tools"))), body=body[:MAX_COMMAND_BODY],
            truncated=len(body) > MAX_COMMAND_BODY))


def discover(home: Path, project_root: Path | None, project_id: str | None,
             plugin_use: dict[str, bool]) -> Catalogue:
    """Every skill and command on this machine for one project. Blocking: call it from a thread."""
    cat = Catalogue()
    cat.roots.append(Root("global", _display(home), home.is_dir()))
    _skills_under(home / "skills", "global", "global", "all", _display(home / "skills"), None, cat)
    _commands_under(home / "commands", "global", "global", _display(home / "commands"), None, cat)
    if project_root is not None and project_id:
        claude = project_root / ".claude"
        cat.roots.append(Root("project", str(claude), claude.is_dir()))
        if claude.is_dir() and _inside(project_root, claude):
            _skills_under(claude / "skills", "project", f"project:{project_id}", project_id,
                          ".claude/skills", None, cat)
            _commands_under(claude / "commands", "project", f"project:{project_id}", ".claude/commands", None, cat)
    cat.plugins = [*installed_plugins(home, plugin_use), *workspace_plugins(plugin_use)]
    cat.roots.append(Root("plugin", _display(home / "plugins"), (home / "plugins").is_dir()))
    workspace_root = workspace_plugins_dir()
    cat.roots.append(Root("plugin", _display(workspace_root), workspace_root.is_dir()))
    for plugin in cat.plugins:
        if not (plugin.enabled_in_claude and plugin.used):
            continue
        _skills_under(plugin.path / "skills", "plugin", f"plugin:{plugin.id}", "all",
                      f"plugin {plugin.id} · skills", plugin, cat)
        _commands_under(plugin.path / "commands", "plugin", f"plugin:{plugin.id}",
                        f"plugin {plugin.id} · commands", plugin, cat)
    return cat


def _hooks_from(data: dict[str, Any] | None, scope: str, source: str, description: str,
                out: list[HookEntry]) -> None:
    hooks = data.get("hooks") if data else None
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for g, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            matcher = str(group.get("matcher") or "")
            for h, handler in enumerate(group["hooks"]):
                if not isinstance(handler, dict) or len(out) >= MAX_HOOKS:
                    continue
                kind = str(handler.get("type") or "command")
                command = redact(str(handler.get("command") or handler.get("prompt") or ""))
                # The id is a digest of what is shown (already redacted) and where it sits, so it is stable
                # for a switch or a link and cannot be worked backwards into a secret.
                digest = hashlib.sha256(f"{scope}|{source}|{event}|{g}|{h}|{matcher}|{command}".encode()).hexdigest()
                timeout = handler.get("timeout")
                out.append(HookEntry(
                    id=digest[:16], event=str(event), matcher=matcher, type=kind, command=command,
                    raw=str(handler.get("command") or handler.get("prompt") or ""), scope=scope,
                    source=source, blocking=event in BLOCKING_EVENTS,
                    description=description or (f"Runs on {event} when the tool matches {matcher}." if matcher
                                                 and matcher != "*" else f"Runs on every {event}."),
                    timeout_s=int(timeout) if isinstance(timeout, (int, float)) else None))


def discover_hooks(home: Path, project_root: Path | None) -> tuple[list[HookEntry], list[Root]]:
    """Every hook Claude Code would run for this project. Only the `hooks` key of a settings file is read;
    nothing else in it (env, permissions, helpers) is ever loaded into an answer."""
    out: list[HookEntry] = []
    roots: list[Root] = []
    settings_file = home / "settings.json"
    roots.append(Root("global", _display(settings_file), settings_file.is_file()))
    _hooks_from(_json(settings_file), "global", _display(settings_file), "", out)
    if project_root is not None:
        for name, scope in (("settings.json", "project"), ("settings.local.json", "local")):
            path = project_root / ".claude" / name
            roots.append(Root(scope, f".claude/{name}", path.is_file()))
            if path.is_file() and _inside(project_root, path):
                _hooks_from(_json(path), scope, f".claude/{name}", "", out)
    for plugin in [*installed_plugins(home, {}), *workspace_plugins({})]:
        if not plugin.enabled_in_claude:
            continue
        path = plugin.path / "hooks" / "hooks.json"
        if path.is_file() and _inside(plugin.path, path):
            data = _json(path)
            _hooks_from(data, "plugin", f"plugin {plugin.id}",
                        str(data.get("description", "")) if data else "", out)
    return out, roots


def _count_contents(path: Path) -> dict[str, int]:
    """What a plugin directory contributes, counted from its files."""
    def count(pattern: str) -> int:
        return sum(1 for p in list(path.glob(pattern))[:MAX_PER_ROOT] if _inside(path, p))

    hooks: list[HookEntry] = []
    _hooks_from(_json(path / "hooks" / "hooks.json"), "plugin", "", "", hooks)
    mcp = 0
    for source in (path / ".mcp.json", path / ".claude-plugin" / "plugin.json"):
        data = _json(source)
        if data is None:
            continue
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            mcp = max(mcp, len(servers))
        elif source.name == ".mcp.json":
            mcp = max(mcp, sum(1 for v in data.values() if isinstance(v, dict)))
    return {"agents": count("agents/*.md"), "skills": count("skills/*/SKILL.md"),
            "commands": count("commands/*.md"), "hooks": len(hooks), "mcp": mcp}


def discover_plugins(home: Path, use: dict[str, bool]) -> dict[str, Any]:
    """Installed plugins with what they contribute, the marketplace entries not installed, and the
    install counts Claude Code last fetched. Blocking: call it from a thread."""
    cache = home / "plugins"
    counts_file = _json(cache / "install-counts-cache.json") or {}
    installs = {str(c.get("plugin")): int(c.get("unique_installs") or 0)
                for c in counts_file.get("counts", []) if isinstance(c, dict)}
    catalogue: dict[str, dict[str, Any]] = {}
    for market, entry in sorted((_json(cache / "known_marketplaces.json") or {}).items()):
        if not isinstance(entry, dict):
            continue
        location = Path(str(entry.get("installLocation", "")))
        if not location.is_absolute() or not _inside(cache, location):
            continue
        listing = _json(location / ".claude-plugin" / "marketplace.json") or {}
        owner = listing.get("owner", {}).get("name", "") if isinstance(listing.get("owner"), dict) else ""
        for item in (listing.get("plugins") or [])[:MAX_MARKETPLACE]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            source = item.get("source")
            local = location / source if isinstance(source, str) and not source.startswith(("http", "git")) else None
            catalogue[f"{item['name']}@{market}"] = {
                "item": item, "owner": owner or market, "updated": str(entry.get("lastUpdated", "")),
                "local": local if local is not None and local.is_dir() and _inside(location, local) else None,
            }

    installed = [*installed_plugins(home, use), *workspace_plugins(use)]
    installed_ids = {p.id for p in installed}

    def card(pid: str, name: str, market: str, *, version: str, updated: str, provides: dict[str, int] | None,
             enabled: bool, used: bool | None, path: str | None) -> dict[str, Any]:
        listed = catalogue.get(pid, {})
        item = listed.get("item", {})
        author = item.get("author", {}).get("name", "") if isinstance(item.get("author"), dict) else ""
        return {
            "id": pid, "name": name, "publisher": author or listed.get("owner") or market,
            "version": version or str(item.get("version", "")), "installed": pid in installed_ids,
            "description": str(item.get("description", "")),
            "provides": provides or {"agents": 0, "skills": 0, "commands": 0, "hooks": 0, "mcp": 0},
            "providesKnown": provides is not None, "stars": installs.get(pid, 0),
            "category": str(item.get("category") or "uncategorized"), "updatedAt": updated,
            "enabledInClaude": enabled, "usedInNeuroCode": used, "path": path,
            "installCommand": f"/plugin {'uninstall' if pid in installed_ids else 'install'} {pid}",
            # A workspace plugin is one this server installed and can remove again; Claude Code's own is
            # not, and the screen needs to know which before it offers a Remove button.
            "workspace": market == WORKSPACE_MARKET, "source": "", "sourceKind": "",
        }

    out_installed = []
    for p in installed:
        manifest = _json(p.path / ".claude-plugin" / "plugin.json") or {}
        made = card(p.id, p.name, p.marketplace, version=p.version, updated=p.updated_at,
                    provides=_count_contents(p.path), enabled=p.enabled_in_claude, used=p.used,
                    path=_display(p.path))
        if p.marketplace == WORKSPACE_MARKET:
            stamp = _json(p.path / ".claude-plugin" / "neurocode.json") or {}
            made["source"] = str(stamp.get("source", ""))
            made["sourceKind"] = str(stamp.get("sourceKind", ""))
            made["installCommand"] = ""
        if isinstance(manifest.get("author"), dict) and manifest["author"].get("name"):
            made["publisher"] = str(manifest["author"]["name"])
        if not made["description"]:
            made["description"] = str(manifest.get("description", ""))
        out_installed.append(made)
    market = [card(pid, pid.partition("@")[0], pid.partition("@")[2], version="", updated=listed["updated"],
                   provides=_count_contents(listed["local"]) if listed["local"] else None,
                   enabled=False, used=None, path=None)
              for pid, listed in catalogue.items() if pid not in installed_ids][:MAX_MARKETPLACE]
    return {"installed": out_installed, "marketplace": market,
            "countsFetchedAt": counts_file.get("fetchedAt") if installs else None,
            "registry": registry(), "workspaceRoot": _display(workspace_plugins_dir())}


# ── hooks that may run, and only those ───────────────────────────
@dataclass(frozen=True)
class HookVerdict:
    """What the tool rules say about one hook, in the words the screen and a log line use."""

    action: str                  # allow | ask | deny
    rule_id: int | None
    why: str

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    def json(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "action": self.action, "ruleId": self.rule_id, "why": self.why}


async def hook_verdicts(session: AsyncSession, hooks: Sequence[HookEntry],
                        project_id: str | None) -> dict[str, HookVerdict]:
    """Hook id → what a rule says about it. No rule is a refusal here, not a question: nobody is waiting
    at a PostToolUse to answer one, and a hook that fires while a person is asked would have fired."""
    out: dict[str, HookVerdict] = {}
    for hook in hooks:
        said = await decide(session, "hook", hook.subject, project_id)
        if said.rule_id is None:
            out[hook.id] = HookVerdict("ask", None, "No tool rule allows this hook, so it is shown and not run.")
        else:
            out[hook.id] = HookVerdict(said.action, said.rule_id, said.why)
    return out


def matches(matcher: str, subject: str) -> bool:
    """Whether a hook's matcher covers what just happened. Claude Code writes these as `Write|Edit` — a
    few names separated by pipes — so that is what is read, each part as a glob. An empty matcher, or
    `*`, is every subject; a hook on an event that carries no subject has nothing to narrow and fires."""
    text = matcher.strip()
    if not text or text == "*":
        return True
    if not subject:
        return False
    return any(fnmatch.fnmatchcase(subject, part.strip()) for part in text.split("|") if part.strip())


@dataclass(frozen=True)
class Fired:
    """One hook at one event: whether it ran, what it said, and whether it refused what was happening."""

    hook_id: str
    event: str
    command: str                 # redacted: this goes into answers and into the log
    ran: bool
    exit_code: int | None
    output: str
    blocked: bool
    why: str
    ms: int

    def json(self) -> dict[str, Any]:
        return {"hookId": self.hook_id, "event": self.event, "command": self.command, "ran": self.ran,
                "exitCode": self.exit_code, "output": self.output, "blocked": self.blocked, "why": self.why,
                "ms": self.ms}


def run_hook(hook: HookEntry, cwd: Path, payload: dict[str, Any],
             fence: sandbox.Sandbox | None = None) -> tuple[int, str, int]:
    """Blocking. One hook, in the project's checkout, with its event's payload on stdin as JSON.

    The same limits every command in this product runs under: a timeout the settings file may shorten but
    never lengthen, output cut rather than buffered without end, and the fence a run's test command gets —
    `fence`, or one drawn around `cwd` with the deployment's own policy when the caller named none. Its
    environment is the machine's, not the API's (`child_env`): a repository's hook is somebody else's
    script and is owed no database password. Returns its exit code, what it printed, and how long it took.
    """
    fence = fence if fence is not None else sandbox.around(cwd, sandbox.env_policy())
    seconds = min(hook.timeout_s or MAX_HOOK_SECONDS, MAX_HOOK_SECONDS)
    started = time.monotonic()
    try:
        done = subprocess.run(fence.wrap(["/bin/sh", "-c", hook.raw]), cwd=cwd, input=json.dumps(payload),
                              text=True, capture_output=True, timeout=seconds, check=False,
                              env=child_env({"CI": "1", "NO_COLOR": "1", "CLAUDE_PROJECT_DIR": str(cwd),
                                             "NEUROCODE_HOOK_EVENT": hook.event}))
    except subprocess.TimeoutExpired:
        return 124, f"It did not finish within {seconds} s, so it was stopped.", int((time.monotonic() - started) * 1000)
    except OSError as e:
        return 126, f"It could not be started: {e}", int((time.monotonic() - started) * 1000)
    said = redact((done.stdout or "") + (done.stderr or ""))
    cut = "" if len(said) <= MAX_HOOK_OUTPUT else f"\n[… {len(said) - MAX_HOOK_OUTPUT} more characters were cut]"
    return done.returncode, said[:MAX_HOOK_OUTPUT] + cut, int((time.monotonic() - started) * 1000)


async def fire(session: AsyncSession, event: str, project: Project | None, payload: dict[str, Any], *,
               subject: str = "", actor: str = "a session", actor_kind: str = "agent") -> list[Fired]:
    """Every hook configured on `event` here: the allowed ones run, the rest are reported and do not.

    `subject` is what the event is about — the tool's name, for the tool events — and a hook's matcher is
    weighed against it. Nothing runs without machine access and a checkout on this machine, because a hook
    runs somewhere, and the only somewhere a repository's hook means is the repository.

    Every firing is recorded in the activity feed: a command ran on this machine because somebody wrote a
    rule, and that is exactly the kind of thing a person needs to be able to find afterwards.
    """
    home, root = claude_home(), _root_of(project)
    found, _ = await asyncio.to_thread(discover_hooks, home, root)
    at_event = [h for h in found if h.event == event and matches(h.matcher, subject) and h.type == "command"
                and h.raw.strip()]
    if not at_event:
        return []
    verdicts = await hook_verdicts(session, at_event, project.id if project else None)
    activity = ActivityRepository(session)
    out: list[Fired] = []
    fence: sandbox.Sandbox | None = None
    for hook in at_event:
        verdict = verdicts[hook.id]
        if not verdict.allowed:
            out.append(Fired(hook.id, event, hook.command, False, None, "", False, verdict.why, 0))
            continue
        if not settings().machine_access:
            out.append(Fired(hook.id, event, hook.command, False, None, "", False,
                             "A rule allows this hook, but machine access is off on this server, so nothing "
                             "runs on this machine.", 0))
            continue
        if root is None:
            out.append(Fired(hook.id, event, hook.command, False, None, "", False,
                             "A rule allows this hook, but this project has no checkout on this machine, so "
                             "there is nowhere to run it.", 0))
            continue
        try:
            machine.inside(str(root))
        except Refused as refused:
            out.append(Fired(hook.id, event, hook.command, False, None, "", False, str(refused), 0))
            continue
        if fence is None:
            # Drawn once, around the checkout, with the workspace's answer about the network.
            fence = sandbox.around(root, await sandbox.read_policy(session))
        code, said, ms = await asyncio.to_thread(run_hook, hook, root, payload, fence)
        blocked = hook.blocking and code == REFUSAL_CODE
        fired = Fired(hook.id, event, hook.command, True, code, said, blocked,
                      f"{verdict.why} It exited {code}." + (" It refused what was about to happen."
                                                            if blocked else ""), ms)
        out.append(fired)
        await activity.record(
            actor=actor, actor_kind=actor_kind, action="Hook fired",
            detail=f"{event} · {hook.command[:120]} · exit {code} · {ms} ms"
                   + (" · refused the action" if blocked else "") + f" · {fence.words()}",
            project_id=project.id if project else None,
            level="warn" if blocked or code not in (0, REFUSAL_CODE) else "info")
    return out


def conflicts(commands: Iterable[CommandFile]) -> list[dict[str, str]]:
    """A bare command name claimed by more than one file, and which one a session gets."""
    by_name: dict[str, list[CommandFile]] = {}
    for c in commands:
        by_name.setdefault(c.stem, []).append(c)
    out = []
    for stem, claims in sorted(by_name.items()):
        if len(claims) < 2:
            continue
        ranked = sorted(claims, key=lambda c: (SCOPE_RANK[c.scope], c.key))
        winner, loser = ranked[0], ranked[1]

        def who(c: CommandFile) -> str:
            return c.key.split("/")[0].removeprefix("plugin:") if c.scope == "plugin" else c.scope

        rest = [c.name for c in ranked[1:] if c.plugin]
        reach = f" The plugin one stays reachable as {', '.join(rest)}." if rest else ""
        out.append({"command": f"/{stem}", "a": who(winner), "b": who(loser),
                    "resolution": f"In NeuroCode sessions /{stem} runs the {who(winner)} one: project beats "
                                  f"workspace beats plugin.{reach}"})
    return out


# ── commands in a session ────────────────────────────────────────
def parse_command(text: str) -> tuple[str, str] | None:
    """`/plan invoice tax` → ("plan", "invoice tax"). Anything that is not shaped like one is None."""
    found = COMMAND_TEXT.match(text.strip())
    return (found.group(1), (found.group(2) or "").strip()) if found else None


def resolve_command(commands: Sequence[CommandFile], name: str) -> CommandFile | None:
    """Which file `/name` means: `plugin:cmd` names a plugin's; otherwise project, then workspace, then plugin."""
    plugin, sep, stem = name.partition(":")
    if sep:
        return next((c for c in commands if c.plugin == plugin and c.stem == stem), None)
    matches = sorted((c for c in commands if c.stem == name), key=lambda c: (SCOPE_RANK[c.scope], c.key))
    return matches[0] if matches else None


def expand(command: CommandFile, args: str) -> str:
    """The prompt a command becomes. $ARGUMENTS and $1…$9 are filled in; shell lines are never run."""
    try:
        positional = shlex.split(args)
    except ValueError:
        positional = args.split()
    body = SHELL_LINE.sub(lambda m: f"[not run by NeuroCode: {m.group(1)}]", command.body)
    body = re.sub(r"\$([1-9])",
                  lambda m: positional[int(m.group(1)) - 1] if len(positional) >= int(m.group(1)) else "", body)
    return body.replace("$ARGUMENTS", args)


def resolve_skill(skills: Sequence[SkillFile], name: str) -> SkillFile | None:
    """A skill by its key, or by slug or name — project, then workspace, then plugin."""
    wanted = name.strip()
    exact = next((s for s in skills if s.key == wanted), None)
    if exact is not None:
        return exact
    low = wanted.lower()
    matches = [s for s in skills if low in (s.slug.lower(), s.name.lower())]
    return sorted(matches, key=lambda s: (SCOPE_RANK[s.scope], s.key))[0] if matches else None


def switched_on(prefs: dict[str, Any], key: str) -> bool:
    """Everything found is on until a person switches it off."""
    return prefs.get(key, True) is not False


# ── the database side: preferences and what really happened ─────
class ExtensionUsage:
    """What sessions really did with skills and commands, counted from the turns they wrote."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def pref(self, key: str) -> dict[str, Any]:
        found = await PrefRepository(self.session).get(key)
        return found.value if found is not None and isinstance(found.value, dict) else {}

    async def per_key(self, tool: str, project_id: str | None) -> dict[str, dict[str, Any]]:
        """Per skill or command key: uses in 24 hours, uses ever, the last use, and who started those sessions.

        With no project there is nothing to count: every session belongs to a project, so the answer is
        the real "none" rather than a query that could only come back empty."""
        if project_id is None:
            return {}
        since = utcnow() - timedelta(hours=24)
        stmt = (select(ChatMessage.detail,
                       func.count().filter(ChatMessage.at > since),
                       func.count(),
                       func.max(ChatMessage.at),
                       func.array_agg(distinct(Chat.started_by)))
                .join(Chat, Chat.id == ChatMessage.chat_id)
                .where(ChatMessage.tool == tool, ChatMessage.ok.is_(True), Chat.project_id == project_id)
                .group_by(ChatMessage.detail).limit(MAX_USAGE_ROWS))
        return {key: {"day": day, "ever": ever, "last": last, "by": sorted(b for b in by if b)}
                for key, day, ever, last, by in (await self.session.execute(stmt)).all()}

    async def sessions_24h(self, project_id: str | None) -> int:
        if project_id is None:
            return 0
        since = utcnow() - timedelta(hours=24)
        stmt = (select(func.count(distinct(ChatMessage.chat_id)))
                .join(Chat, Chat.id == ChatMessage.chat_id)
                .where(ChatMessage.role == "you", ChatMessage.at > since, Chat.project_id == project_id))
        return int((await self.session.execute(stmt)).scalar_one())


@dataclass(frozen=True)
class Snapshot:
    """What a session may use, read once per answer: enabled skills and every command with its switch."""

    skills: tuple[SkillFile, ...]
    commands: tuple[CommandFile, ...]
    commands_on: dict[str, Any]


def _root_of(project: Project | None) -> Path | None:
    root = checkout(project) if project is not None else None
    return root if root is not None and root.is_dir() else None


async def snapshot(session: AsyncSession, project: Project | None) -> Snapshot:
    """Discovery once, in a thread, and the three switches read once — so tools never glob per call."""
    usage = ExtensionUsage(session)
    skills_on, commands_on, plugins_use = (await usage.pref(SKILLS_PREF), await usage.pref(COMMANDS_PREF),
                                           await usage.pref(PLUGINS_PREF))
    home = claude_home()

    def read() -> Catalogue:
        return discover(home, _root_of(project), project.id if project else None, plugins_use)

    cat = await asyncio.to_thread(read)
    return Snapshot(skills=tuple(s for s in cat.skills if switched_on(skills_on, s.key)),
                    commands=tuple(cat.commands), commands_on=commands_on)


class ExtensionService:
    """The four extension screens, for one project or for none.

    A workspace starts with no project, and what this machine already has — the skills, commands, hooks
    and plugins under the Claude home — is real before any project exists. So with no project the
    screens read those roots alone, and the counts that belong to a project's sessions are zero.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.usage = ExtensionUsage(session)

    async def _project(self, project_id: str | None) -> Project | None:
        if project_id is None:
            return None
        project = await ProjectRepository(self.session).get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        return project

    async def _catalogue(self, project: Project | None) -> Catalogue:
        use, home = await self.usage.pref(PLUGINS_PREF), claude_home()
        pid = project.id if project else None
        return await asyncio.to_thread(lambda: discover(home, _root_of(project), pid, use))

    async def skills(self, project_id: str | None) -> dict[str, Any]:
        project = await self._project(project_id)
        cat = await self._catalogue(project)
        used = await self.usage.per_key("load_skill", project_id)
        return {"skills": [skill_json(s, used.get(s.key)) for s in cat.skills],
                "sessions24h": await self.usage.sessions_24h(project_id),
                "roots": [asdict(r) for r in cat.roots], "unreadable": cat.unreadable[:50]}

    async def skill(self, project_id: str | None, key: str) -> dict[str, Any]:
        """One skill with its body. The key is looked up in discovery; it is never used as a path."""
        project = await self._project(project_id)
        cat = await self._catalogue(project)
        found = next((s for s in cat.skills if s.key == key), None)
        if found is None:
            raise NotFound(f"skill {key}")
        extra: dict[str, str] = {}
        root = _root_of(project)
        if found.scope == "project" and root is not None and not (found.version and found.author):
            extra = await asyncio.to_thread(_last_commit, root, found.path)
        used = (await self.usage.per_key("load_skill", project_id)).get(found.key)
        out = skill_json(found, used)
        out["version"] = out["version"] or extra.get("sha", "")
        out["author"] = out["author"] or extra.get("author", "")
        return {**out, "body": found.body, "truncated": found.truncated}

    async def commands(self, project_id: str | None) -> dict[str, Any]:
        project = await self._project(project_id)
        cat = await self._catalogue(project)
        used = await self.usage.per_key("command", project_id)
        return {"commands": [command_json(c, used.get(c.key)) for c in cat.commands],
                "shellLines": {c.key: SHELL_LINE.findall(c.body) for c in cat.commands if SHELL_LINE.search(c.body)},
                "conflicts": conflicts(cat.commands), "roots": [asdict(r) for r in cat.roots]}

    async def hooks(self, project_id: str | None) -> dict[str, Any]:
        """Every hook configured here, each one saying plainly whether it would run or is only shown.

        The verdict is added beside the hook rather than inside `hook_json`, because it is not a property
        of the file: it is what this workspace's rules say about that file today, and it changes when
        somebody writes a rule without anything on disk moving.
        """
        project = await self._project(project_id)
        home, root = claude_home(), _root_of(project)
        found, roots = await asyncio.to_thread(discover_hooks, home, root)
        verdicts = await hook_verdicts(self.session, found, project_id)
        rows = [{**hook_json(h), **verdicts[h.id].json(), "pattern": h.pattern} for h in found]
        return {"hooks": rows, "files": [asdict(r) for r in roots],
                "canRun": settings().machine_access and root is not None,
                "checkout": str(root) if root is not None else None,
                "allowed": sum(1 for r in rows if r["allowed"])}

    async def fire_hook(self, project_id: str | None, hook_id: str, who: Person) -> dict[str, Any]:
        """Run one allowed hook, once, because a person pressed the button next to it.

        This is the honest way to find out whether a hook works before it fires on its own: the same
        rule decides, the same limits hold, and the firing is in the activity log like any other. A hook
        no rule allows is refused here exactly as it is refused at its event.
        """
        who.must(HOOKS_WRITE, "run a hook")
        machine.enabled()
        project = await self._project(project_id)
        home, root = claude_home(), _root_of(project)
        found, _ = await asyncio.to_thread(discover_hooks, home, root)
        hook = next((h for h in found if h.id == hook_id), None)
        if hook is None:
            raise NotFound(f"hook {hook_id}")
        if hook.type != "command" or not hook.raw.strip():
            raise Refused(f"This {hook.type} hook has no command to run.", status=422)
        payload = {"hook_event_name": hook.event, "cwd": str(root) if root else "",
                   "source": "NeuroCode · run once from the Hooks screen"}
        fired = await fire(self.session, hook.event, project, payload, subject=hook.matcher.split("|")[0],
                           actor=who.name, actor_kind="human")
        mine = next((f for f in fired if f.hook_id == hook_id), None)
        if mine is None:
            # Its matcher did not cover the subject a trial carries; run it on its own terms instead of
            # answering with somebody else's hook.
            fired = await fire(self.session, hook.event, project, payload, actor=who.name, actor_kind="human")
            mine = next((f for f in fired if f.hook_id == hook_id), None)
        if mine is None:
            raise Refused("This hook did not fire: its matcher covers nothing a trial can stand for.",
                          status=422)
        await AuditRepository(self.session).record(action="hook.run", user_id=who.id,
                                                   target=f"hook {hook_id}",
                                                   detail={"event": hook.event, "exit": mine.exit_code,
                                                           "ran": mine.ran, "projectId": project_id})
        return mine.json()

    # ── the workspace's own plugins ──────────────────────────────
    async def install_plugin(self, source: str, name: str, who: Person) -> dict[str, Any]:
        """Bring a plugin into this workspace's own folder. It is not installed into Claude Code and
        never will be: what is installed here reaches NeuroCode's sessions and nothing else."""
        who.must(MANAGE, "install a plugin")
        made = await asyncio.to_thread(install_plugin, source, name)
        await ActivityRepository(self.session).record(
            actor=who.name, actor_kind="human", action="Plugin installed",
            detail=f"{made['name']} · from {made['sourceKind']} {source[:160]} · "
                   + (", ".join(f"{n} {k}" for k, n in made["provides"].items() if n) or "nothing yet"),
            level="warn")
        await AuditRepository(self.session).record(action="plugin.install", user_id=who.id,
                                                   target=f"plugin {made['name']}",
                                                   detail={"source": source, "kind": made["sourceKind"],
                                                           "provides": made["provides"]})
        return made

    async def remove_plugin(self, name: str, who: Person) -> dict[str, Any]:
        who.must(MANAGE, "remove a plugin")
        gone = await asyncio.to_thread(remove_plugin, name)
        await ActivityRepository(self.session).record(actor=who.name, actor_kind="human",
                                                      action="Plugin removed", detail=name, level="info")
        await AuditRepository(self.session).record(action="plugin.remove", user_id=who.id,
                                                   target=f"plugin {name}", detail={"name": name})
        return gone

    async def plugins(self, project_id: str | None) -> dict[str, Any]:
        project = await self._project(project_id)
        use, home, root = await self.usage.pref(PLUGINS_PREF), claude_home(), _root_of(project)

        def read() -> tuple[dict[str, Any], Catalogue]:
            return discover_plugins(home, use), discover(home, root, project.id if project else None, use)

        out, cat = await asyncio.to_thread(read)
        return {**out, "conflicts": conflicts(cat.commands)}


def _last_commit(root: Path, path: Path) -> dict[str, str]:
    """The short sha and author of the last commit that touched a project skill, when git knows it."""
    try:
        rel = path.resolve().relative_to(root.resolve())
        out = git(["log", "-1", "--format=%h%x09%an", "--", str(rel)], root, timeout=10)
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        log.info("no git history for %s: %s", path, e)
        return {}
    sha, _, author = out.stdout.strip().partition("\t")
    return {"sha": sha, "author": author} if out.returncode == 0 and sha else {}


def refuse_disabled(command: CommandFile) -> Refused:
    return Refused(f"{command.name} is switched off in Commands. Switch it on there to use it here.", status=422)
