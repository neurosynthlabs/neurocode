"""The Git screen's shapes: what a snapshot of the repository holds, and how it goes over the wire.

The snapshot is taken in a thread, away from the database, so what it knows about a run arrives as a
plain frozen record rather than an ORM object — a thread must never be the thing that touches a lazy
relationship. Everything else here is a straight copy into camelCase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RunFacts:
    """What the snapshot needs to know about one run, copied out of the session before the thread."""

    id: str
    ref: str
    status: str
    role: str
    branch: str
    worktree: str
    base: str
    agent: str
    task_ref: str
    removed: bool
    merged: dict[str, Any] | None
    diff_files: int
    #: (branch, files) for every collision the integration step recorded.
    conflicts: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: child branch → what the merge step said when it brought that branch in.
    merge_notes: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class Head:
    branch: str
    sha: str
    dirty: bool


@dataclass(slots=True)
class WorktreeView:
    id: str
    branch: str
    path: str
    agent: str
    task_ref: str
    status: str
    files_changed: int
    additions: int
    deletions: int
    ahead: int
    behind: int
    last_commit: str
    last_commit_at: str | None
    run_ref: str | None
    run_status: str | None
    made_by: str
    gone: bool
    merged: dict[str, Any] | None
    can_merge: bool
    merge_blocked: str | None
    base: str
    #: The files the branch changed since its base, kept to decide which pairs are worth a merge-tree.
    changed: frozenset[str] = frozenset()


@dataclass(slots=True)
class Preview:
    worktree_id: str
    branch: str
    target: str
    result: str
    files: int
    note: str
    collides_with: str | None = None
    on_file: str | None = None


@dataclass(slots=True)
class Snapshot:
    available: bool
    reason: str = ""
    repo: str = ""
    head: Head = field(default_factory=lambda: Head("", "", False))
    shallow: bool = False
    worktrees: list[WorktreeView] = field(default_factory=list)
    previews: list[Preview] = field(default_factory=list)
    commits_today: int = 0
    agent_commits_today: int = 0
    #: Every local branch, exactly as git lists it. A branch a request names must be one of these.
    branches: frozenset[str] = frozenset()


def worktree_json(w: WorktreeView) -> dict[str, Any]:
    return {"id": w.id, "branch": w.branch, "path": w.path, "agent": w.agent, "taskRef": w.task_ref,
            "status": w.status, "filesChanged": w.files_changed, "additions": w.additions,
            "deletions": w.deletions, "ahead": w.ahead, "behind": w.behind, "lastCommit": w.last_commit,
            "lastCommitAt": w.last_commit_at, "runRef": w.run_ref, "runStatus": w.run_status,
            "madeBy": w.made_by, "gone": w.gone, "merged": w.merged, "canMerge": w.can_merge,
            "mergeBlocked": w.merge_blocked, "base": w.base}


def preview_json(p: Preview) -> dict[str, Any]:
    return {"worktreeId": p.worktree_id, "branch": p.branch, "target": p.target, "result": p.result,
            "files": p.files, "note": p.note,
            **({"collidesWith": p.collides_with} if p.collides_with else {}),
            **({"onFile": p.on_file} if p.on_file else {})}


def overview_json(snap: Snapshot, *, awaiting_you: int) -> dict[str, Any]:
    """The whole screen's first answer. Counts are taken from the lists they describe, never stored."""
    return {
        "available": snap.available, **({"reason": snap.reason} if snap.reason else {}),
        "repo": snap.repo,
        "head": {"branch": snap.head.branch, "sha": snap.head.sha, "dirty": snap.head.dirty},
        "shallow": snap.shallow,
        "worktrees": [worktree_json(w) for w in snap.worktrees],
        "mergePreview": [preview_json(p) for p in snap.previews],
        "stats": {"worktrees": len(snap.worktrees),
                  "dirty": sum(1 for w in snap.worktrees if w.status == "dirty"),
                  "clean": sum(1 for p in snap.previews if p.result == "clean"),
                  "collisions": sum(1 for p in snap.previews if p.result == "collides"),
                  "commitsToday": snap.commits_today, "agentCommitsToday": snap.agent_commits_today,
                  "awaitingYou": awaiting_you},
    }
