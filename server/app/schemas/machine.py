"""The Workbench's shapes: a folder's entries, an opened file, and what git says about a repository.

Built in a worker thread from what the filesystem answered, as plain frozen records, and copied into
camelCase at the edge. Paths go out absolute and real (symlinks resolved), because that is the path the
next request will be checked against; the screen shortens them for display.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EntryKind = Literal["dir", "file", "link"]


def stamp(mtime: float | None) -> str | None:
    """A file's modification time in the one format the API hands out: ISO 8601 with its timezone."""
    if mtime is None:
        return None
    return datetime.fromtimestamp(mtime, tz=UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Root:
    path: str
    label: str


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    path: str
    kind: EntryKind
    size: int | None
    modified: float | None
    #: A folder that holds a repository of its own (it has a `.git`).
    git: bool = False
    #: What a link points at, when it points somewhere inside the roots: dir | file. None otherwise.
    link_to: Literal["dir", "file"] | None = None


@dataclass(frozen=True, slots=True)
class Listing:
    path: str
    parent: str | None
    entries: tuple[Entry, ...]
    capped: bool
    total: int
    hidden: int


@dataclass(frozen=True, slots=True)
class Opened:
    path: str
    size: int
    modified: float
    binary: bool
    text: str | None
    language: str | None
    sha1: str
    #: Why the text is not shown, when it is not: "binary" or "not UTF-8".
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Saved:
    path: str
    size: int
    modified: float
    sha1: str


@dataclass(frozen=True, slots=True)
class Change:
    path: str
    #: One letter: M modified, A added, D deleted, R renamed, C copied, T type changed, U conflicted, ? untracked.
    status: str
    staged: bool
    #: Where a rename or copy came from, relative to the repository.
    was: str | None = None


@dataclass(frozen=True, slots=True)
class GitState:
    root: str
    branch: str | None
    head: str | None
    upstream: str | None
    ahead: int
    behind: int
    changed: tuple[Change, ...] = field(default_factory=tuple)
    capped: bool = False


@dataclass(frozen=True, slots=True)
class FileList:
    root: str
    files: tuple[str, ...]
    capped: bool
    #: "git" when the list is git's own (tracked and untracked, .gitignore respected), "walk" otherwise.
    source: Literal["git", "walk"]


def root_json(root: Root) -> dict[str, Any]:
    return {"path": root.path, "label": root.label}


def entry_json(entry: Entry) -> dict[str, Any]:
    return {"name": entry.name, "path": entry.path, "kind": entry.kind, "size": entry.size,
            "modified": stamp(entry.modified), "git": entry.git, "linkTo": entry.link_to}


def listing_json(listing: Listing) -> dict[str, Any]:
    return {"path": listing.path, "parent": listing.parent,
            "entries": [entry_json(e) for e in listing.entries],
            "capped": listing.capped, "total": listing.total, "hidden": listing.hidden}


def opened_json(opened: Opened) -> dict[str, Any]:
    return {"path": opened.path, "size": opened.size, "modified": stamp(opened.modified),
            "binary": opened.binary, "text": opened.text, "language": opened.language, "sha1": opened.sha1,
            "reason": opened.reason}


def saved_json(saved: Saved) -> dict[str, Any]:
    return {"path": saved.path, "size": saved.size, "modified": stamp(saved.modified), "sha1": saved.sha1}


def git_json(state: GitState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {"root": state.root, "branch": state.branch, "head": state.head, "upstream": state.upstream,
            "ahead": state.ahead, "behind": state.behind, "capped": state.capped,
            "changed": [{"path": c.path, "status": c.status, "staged": c.staged,
                         **({"was": c.was} if c.was else {})} for c in state.changed]}


def files_json(found: FileList) -> dict[str, Any]:
    return {"root": found.root, "files": list(found.files), "capped": found.capped, "source": found.source}
