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

Hooks are discovered, redacted and shown. NeuroCode never executes one, and nothing here runs anything:
a command's !`shell` lines are shown with a warning and replaced with a note when a session expands it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shlex
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..agent.git import git
from ..data.base import utcnow
from ..models import Chat, ChatMessage, Project
from ..repositories import NotFound, ProjectRepository
from ..repositories.work import PrefRepository
from ..schemas.extensions import command_json, hook_json, skill_json
from .code import checkout
from .errors import Refused

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
    cat.plugins = installed_plugins(home, plugin_use)
    cat.roots.append(Root("plugin", _display(home / "plugins"), (home / "plugins").is_dir()))
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
                    id=digest[:16], event=str(event), matcher=matcher, type=kind, command=command, scope=scope,
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
    for plugin in installed_plugins(home, {}):
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

    installed = installed_plugins(home, use)
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
        }

    out_installed = []
    for p in installed:
        manifest = _json(p.path / ".claude-plugin" / "plugin.json") or {}
        made = card(p.id, p.name, p.marketplace, version=p.version, updated=p.updated_at,
                    provides=_count_contents(p.path), enabled=p.enabled_in_claude, used=p.used,
                    path=_display(p.path))
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
            "countsFetchedAt": counts_file.get("fetchedAt") if installs else None}


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

    async def per_key(self, tool: str, project_id: str) -> dict[str, dict[str, Any]]:
        """Per skill or command key: uses in 24 hours, uses ever, the last use, and who started those sessions."""
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

    async def sessions_24h(self, project_id: str) -> int:
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
    """The four extension screens for one project."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.usage = ExtensionUsage(session)

    async def _project(self, project_id: str) -> Project:
        project = await ProjectRepository(self.session).get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        return project

    async def _catalogue(self, project: Project) -> Catalogue:
        use, home = await self.usage.pref(PLUGINS_PREF), claude_home()
        return await asyncio.to_thread(lambda: discover(home, _root_of(project), project.id, use))

    async def skills(self, project_id: str) -> dict[str, Any]:
        project = await self._project(project_id)
        cat = await self._catalogue(project)
        used = await self.usage.per_key("load_skill", project.id)
        return {"skills": [skill_json(s, used.get(s.key)) for s in cat.skills],
                "sessions24h": await self.usage.sessions_24h(project.id),
                "roots": [asdict(r) for r in cat.roots], "unreadable": cat.unreadable[:50]}

    async def skill(self, project_id: str, key: str) -> dict[str, Any]:
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
        used = (await self.usage.per_key("load_skill", project.id)).get(found.key)
        out = skill_json(found, used)
        out["version"] = out["version"] or extra.get("sha", "")
        out["author"] = out["author"] or extra.get("author", "")
        return {**out, "body": found.body, "truncated": found.truncated}

    async def commands(self, project_id: str) -> dict[str, Any]:
        project = await self._project(project_id)
        cat = await self._catalogue(project)
        used = await self.usage.per_key("command", project.id)
        return {"commands": [command_json(c, used.get(c.key)) for c in cat.commands],
                "shellLines": {c.key: SHELL_LINE.findall(c.body) for c in cat.commands if SHELL_LINE.search(c.body)},
                "conflicts": conflicts(cat.commands), "roots": [asdict(r) for r in cat.roots]}

    async def hooks(self, project_id: str) -> dict[str, Any]:
        project = await self._project(project_id)
        home, root = claude_home(), _root_of(project)
        found, roots = await asyncio.to_thread(discover_hooks, home, root)
        return {"hooks": [hook_json(h) for h in found], "files": [asdict(r) for r in roots]}

    async def plugins(self, project_id: str) -> dict[str, Any]:
        project = await self._project(project_id)
        use, home, root = await self.usage.pref(PLUGINS_PREF), claude_home(), _root_of(project)

        def read() -> tuple[dict[str, Any], Catalogue]:
            return discover_plugins(home, use), discover(home, root, project.id, use)

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
