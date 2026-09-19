"""A project's own instructions: the AGENTS.md and CLAUDE.md its people wrote for the agents that work in it.

The files are the truth, so nothing is stored. Every caller — the compiler, a session's system prompt,
the Instructions tab — reads the checkout when it asks, the way Claude Code and OpenCode do, and gets
the same answer: which files were read, what each one said, and what was refused and why.

Only the checkout root is read, never a folder above it: a checkout under someone's home directory must
not pick up the instructions they wrote for a different project. From the root come AGENTS.md,
CLAUDE.md, .claude/CLAUDE.md and CLAUDE.local.md, and every markdown file under .claude/rules and
.neurocode/rules. A file may pull another in with `@path`, up to four imports deep; an import that
leaves the checkout — through `..`, an absolute path, a home directory or a symlink — is refused and
listed, never read. HTML comments are notes for the people editing the file, so they are stripped.

A rule file may say which files it is about, with a `paths:` list of globs in its front matter. Such a
rule applies only when one of the targets matches, so a rule for the SQL migrations is not handed to a
plan about the CSS. Every file is listed either way, and says whether it applied.

The text is capped. What did not fit is cut at a line, and the file that was cut says so, so a reader
of the Instructions tab knows the model did not see the end of it.

Blocking: it reads files. Callers run it with `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..models import Project
from .code import checkout
from .extensions import _words, front_matter

#: What the model is handed of all the files together. Instructions are read on every compile and every
#: session turn, so a repository's whole handbook would be paid for again and again.
MAX_BYTES = 24_000
#: A file bigger than this is not read: nobody writes a megabyte of instructions by hand.
MAX_FILE_BYTES = 1_000_000
#: How many imports one file may reach through: A imports B imports C imports D imports E, and no further.
MAX_HOPS = 4
#: Rule files read from one rules folder, at most.
MAX_RULES = 200

#: The files at the checkout root, in the order they are handed over. AGENTS.md first: it is the file
#: every tool reads, and the Claude files add to it.
PROJECT_FILES = ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
RULE_DIRS = (".claude/rules", ".neurocode/rules")

COMMENT = re.compile(r"<!--.*?-->", re.S)
FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
SPAN = re.compile(r"`[^`\n]*`")
#: `@docs/setup.md` or `@./notes.md` at the start of a line or after a space — so an e-mail address or a
#: package like `@types/node` inside backticks is never taken for an import.
IMPORT = re.compile(r"(?:^|(?<=[\s(]))@(~?[\w./\-]+)", re.M)

Scope = Literal["project", "rules"]


@dataclass
class Resolved:
    """What the model is handed, the files it came from, and the imports that were refused.

    `files` are `{path, sha1, bytes, scope, matched, paths, applied, importedBy, cut}`: `matched` is the
    rule glob a target matched, or None for a file that always applies; `paths` is the rule's globs, empty
    when it has none; `cut` is true when the cap cut the file short or left it out.
    """

    text: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return len(self.text.encode())

    @property
    def capped(self) -> bool:
        return any(f["cut"] for f in self.files)

    def brief(self) -> list[dict[str, Any]]:
        """The files that were really handed over, as a session lists them: path and size."""
        return [{"path": f["path"], "bytes": f["bytes"]} for f in self.files if f["applied"]]

    def grounding(self) -> list[dict[str, str]]:
        """The files that applied, as a plan's `grounding` records them — the ref is the content's sha1,
        so a reader can tell whether the file changed since."""
        return [{"kind": "instructions", "ref": f["sha1"][:12], "path": f["path"]}
                for f in self.files if f["applied"]]


# ── globs ────────────────────────────────────────────────────────
def _glob_regex(glob: str) -> re.Pattern[str]:
    """A rule's glob as a pattern: `**/` is any folders or none, `*` stays inside one folder, `{a,b}`
    is either. `src/**/*.{ts,tsx}` matches src/a.ts and src/x/y/b.tsx, not lib/a.ts."""
    out, i = "", 0
    glob = glob.strip().lstrip("/")
    while i < len(glob):
        ch = glob[i]
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif ch == "*":
            out, i = out + "[^/]*", i + 1
        elif ch == "?":
            out, i = out + "[^/]", i + 1
        elif ch == "{" and (end := glob.find("}", i)) > i:
            out += "(?:" + "|".join(re.escape(part) for part in glob[i + 1:end].split(",")) + ")"
            i = end + 1
        else:
            out, i = out + re.escape(ch), i + 1
    return re.compile(out + r"\Z")


def _plain(path: str) -> str:
    """`./src/a.ts` and `/src/a.ts` are both src/a.ts. Not `lstrip("./")`, which would eat the dot of
    `.github/workflows/ci.yml` too."""
    path = path.strip().replace("\\", "/")
    while path.startswith(("./", "/")):
        path = path[2:] if path.startswith("./") else path[1:]
    return path


def matching(globs: Sequence[str], targets: Sequence[str]) -> str | None:
    """The first glob one of the targets matches, or None."""
    clean = [_plain(t) for t in targets if t and t.strip()]
    for glob in globs:
        pattern = _glob_regex(glob)
        if any(pattern.match(t) for t in clean):
            return glob
    return None


# ── reading ──────────────────────────────────────────────────────
def _inside(root: Path, path: Path) -> bool:
    """True when `path`, followed through any symlink, is still under the checkout."""
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _imports(text: str) -> list[str]:
    """The `@path` imports in a file, outside code blocks and code spans. A word with no dot or slash
    (`@alice`) is a mention, not a path."""
    prose = SPAN.sub("", FENCE.sub("", text))
    found = [m.group(1).rstrip(".,;:)") for m in IMPORT.finditer(prose)]
    return list(dict.fromkeys(f for f in found if "/" in f or "." in f.lstrip(".")))


class _Reader:
    def __init__(self, root: Path, targets: Sequence[str]) -> None:
        self.root = root.resolve()
        self.targets = list(targets)
        self.seen: set[Path] = set()
        self.out = Resolved()
        self.sections: list[tuple[dict[str, Any], str]] = []

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def refuse(self, path: str, why: str, by: str) -> None:
        self.out.refused.append({"path": path, "why": why, "from": by})

    def read(self, path: Path, scope: Scope, *, by: str | None = None, hops: int = 0) -> None:
        """One file, and then what it imports, depth first — so an import sits right after its importer."""
        real = path.resolve()
        if real in self.seen:
            return
        self.seen.add(real)
        try:
            if real.stat().st_size > MAX_FILE_BYTES:
                self.refuse(self.rel(path), f"larger than {MAX_FILE_BYTES // 1_000_000} MB", by or "")
                return
            raw = real.read_bytes()
        except OSError as e:
            self.refuse(self.rel(path), f"could not be read: {e.strerror or type(e).__name__}", by or "")
            return
        text = raw.decode(errors="replace")
        meta, body, _readable = front_matter(text)
        globs = _words(meta.get("paths")) if scope == "rules" else []
        matched = matching(globs, self.targets) if globs else None
        applied = not globs or matched is not None
        entry = {"path": self.rel(path), "sha1": hashlib.sha1(raw).hexdigest(), "bytes": len(raw),
                 "scope": scope, "matched": matched, "paths": globs, "applied": applied,
                 "importedBy": by, "cut": False}
        self.out.files.append(entry)
        if not applied:
            return                  # a rule about other files: listed, and its imports never read
        body = COMMENT.sub("", body).strip()
        self.sections.append((entry, body))
        for wanted in _imports(body):
            self.follow(wanted, path, scope, hops + 1, entry["path"])

    def follow(self, wanted: str, importer: Path, scope: Scope, hops: int, by: str) -> None:
        if wanted.startswith("~") or wanted.startswith("/"):
            self.refuse(wanted, "outside the checkout", by)
            return
        target = importer.parent / wanted
        if not _inside(self.root, target):
            self.refuse(wanted, "outside the checkout", by)
            return
        if hops > MAX_HOPS:
            self.refuse(wanted, f"more than {MAX_HOPS} imports deep", by)
            return
        if not target.is_file():
            self.refuse(wanted, "a folder, not a file" if target.is_dir() else "no such file", by)
            return
        self.read(target, scope, by=by, hops=hops)

    def text(self) -> str:
        """Each applied file under its own path, in order, until the cap — the file that crosses it is cut
        at a line, and every file after it is left out and says so."""
        parts: list[str] = []
        room = MAX_BYTES
        for entry, body in self.sections:
            if not body:
                continue
            section = f"=== {entry['path']} ===\n{body}\n"
            size = len(section.encode())
            if size <= room:
                parts.append(section)
                room -= size
                continue
            entry["cut"] = True
            if room > 200:
                kept = section.encode()[:room - 80].decode(errors="ignore")
                kept = kept[:kept.rfind("\n")] if "\n" in kept else kept
                left = size - len(kept.encode())
                parts.append(f"{kept}\n[… {left:,} more bytes of {entry['path']} did not fit]\n")
            room = 0
        return "\n".join(parts).strip()


def resolve(root: Path, targets: Sequence[str] = ()) -> Resolved:
    """The instructions at a checkout's root, for work on `targets` (paths relative to the root)."""
    if not root.is_dir():
        return Resolved()
    reader = _Reader(root, targets)
    for name in PROJECT_FILES:
        path = root / name
        if path.is_file():
            if _inside(reader.root, path):
                reader.read(path, "project")
            else:
                reader.refuse(name, "a link to somewhere outside the checkout", "")
    for folder in RULE_DIRS:
        base = root / folder
        if not base.is_dir() or not _inside(reader.root, base):
            continue
        for path in sorted(base.rglob("*.md"))[:MAX_RULES]:
            if not path.is_file():
                continue
            if _inside(reader.root, path):
                reader.read(path, "rules")
            else:
                reader.refuse(path.relative_to(root).as_posix(), "a link to somewhere outside the checkout", "")
    reader.out.text = reader.text()
    return reader.out


async def for_project(project: Project | None, targets: Sequence[str] = ()) -> Resolved:
    """The instructions of a project's checkout on this machine; nothing for a project without one."""
    root = checkout(project) if project is not None else None
    if root is None:
        return Resolved()
    return await asyncio.to_thread(resolve, root, targets)


def as_json(found: Resolved) -> dict[str, Any]:
    """What the Instructions tab reads."""
    return {"files": found.files, "refused": found.refused, "bytes": found.bytes, "capped": found.capped,
            "cap": MAX_BYTES}
