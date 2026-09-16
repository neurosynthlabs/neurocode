"""The Git screen, read from git itself: the worktrees, what merging each would do, where branches
collide, and the history that led here.

Nothing on this screen is stored. The database says which runs exist and what they were for; git says
everything else, asked fresh on every load. Three rules keep that safe on a repository that agents
are writing to at the same moment:

* **Never take a lock.** Every call passes `--no-optional-locks`, so a `git status` from this screen
  cannot make an agent's `git add` fail on `index.lock`.
* **Bounded, and off the event loop.** One `asyncio.to_thread` per request runs the whole snapshot,
  every call with a 20 s timeout, at most thirty worktrees, and merge-tree only for pairs of branches
  that touched a file in common — so the cost does not grow as the square of the branches.
* **Nothing from a request reaches git as an option.** A branch is accepted only when it is exactly
  one `for-each-ref` lists, and every revision comes after `--end-of-options`.

`git merge-tree --write-tree` answers "would this merge collide" without touching any working tree,
but it does write the merged trees and blobs into the repository's object store. They are unreachable,
harmless, and removed by the next `git gc` — though for a local project that store is the person's
own repository, which is why merge-tree runs only for branches that have something to merge, and the
conflict hunks only when the Conflicts tab asks for them.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import subprocess
from dataclasses import replace
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.git import AUTHOR, MAX_DIFF, git, repo_of
from ..models import Approval, Plan, Project, Run, Task
from ..repositories.base import NotFound
from ..repositories.runtime import RunRepository
from ..repositories.work import ApprovalRepository, PlanRepository, ProjectRepository, TaskRepository
from ..schemas.git import Head, Preview, RunFacts, Snapshot, WorktreeView, overview_json
from .code import checkout
from .errors import Refused

log = logging.getLogger(__name__)

GIT_TIMEOUT = 20            # seconds per git call; a slow checkout degrades a number, never the page
RUNS_SCANNED = 60           # the newest runs of a project that are matched to worktrees
MAX_WORKTREES = 30
PREVIEW_PAIRS = 20          # branch-against-branch merge-trees in one overview
MAX_DIFF_FILES = 60
HUNK_LINES = 80
MAX_COMMITS = 300
MAX_PAIRS = 10              # collisions the Conflicts tab opens
PATHS_PER_PAIR = 5
MAX_REGIONS = 3
SIDE_LINES = 40
MERGE_TREE = (2, 38)        # `merge-tree --write-tree` arrived in git 2.38

#: The commits NeuroCode makes carry this address (agent/git.py AUTHOR), and nothing else does.
AGENT_EMAIL = next(a.split("=", 1)[1] for a in AUTHOR if a.startswith("user.email="))
SHA = re.compile(r"^[0-9a-f]{7,64}$")
NOT_OURS = "Made outside NeuroCode — merge it with git."
SAMPLE = "This is a sample project, so there is no code on this machine to read."

#: The rules the runtime really enforces at a merge. The same for every collision, because nothing
#: decides a collision case by case — they are listed so nobody expects that something does.
POLICY = [
    "A collision is undone, never half-applied: the merge is aborted and the files are named.",
    "Only a run you accepted, and that finished, can be merged from here.",
    "A checkout with changes that are not committed refuses a merge.",
    "Nothing in NeuroCode edits a conflicted file for you.",
]


# ── git, bounded ─────────────────────────────────────────────────
def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """One read. It never takes an optional lock, and a timeout becomes an empty answer, said in the log."""
    try:
        return git(["-c", "core.quotePath=false", "--no-optional-locks", *args], cwd, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as failed:
        log.warning("git %s in %s did not answer: %s", args[:2], cwd, failed)
        return subprocess.CompletedProcess(args, 124, "", str(failed))


def _out(args: list[str], cwd: Path) -> str:
    done = _git(args, cwd)
    return done.stdout.strip() if done.returncode == 0 else ""


@cache
def git_version() -> tuple[int, int]:
    found = re.search(r"(\d+)\.(\d+)", git(["version"], Path.home(), timeout=GIT_TIMEOUT).stdout)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def _home(path: str) -> str:
    """Absolute paths go out with the home directory written as ~, the way the rest of the app shows them."""
    home = str(Path.home())
    return f"~{path[len(home):]}" if path == home or path.startswith(f"{home}{os.sep}") else path


def _real(path: str | Path) -> str:
    return os.path.realpath(str(path))


def _branches(repo: Path) -> frozenset[str]:
    lines = _out(["for-each-ref", "--format=%(refname)", "refs/heads"], repo).splitlines()
    return frozenset(line[len("refs/heads/"):] for line in lines if line.startswith("refs/heads/"))


def _listed_worktrees(repo: Path) -> list[dict[str, Any]]:
    """`worktree list --porcelain`, parsed. The first entry is the checkout itself."""
    found: list[dict[str, Any]] = []
    for block in _git(["worktree", "list", "--porcelain"], repo).stdout.split("\n\n"):
        entry: dict[str, Any] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                entry["path"] = value
            elif key == "HEAD":
                entry["head"] = value
            elif key == "branch" and value.startswith("refs/heads/"):
                entry["branch"] = value[len("refs/heads/"):]
            elif key == "prunable":
                entry["prunable"] = True
        if entry.get("path"):
            found.append(entry)
    return found


def _numstat(repo: Path, revisions: str) -> tuple[int, int, int, frozenset[str]]:
    files = additions = deletions = 0
    paths: set[str] = set()
    for record in _git(["diff", "--numstat", "--no-renames", "-z", "--end-of-options", revisions],
                       repo).stdout.split("\0"):
        parts = record.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        additions += int(parts[0]) if parts[0].isdigit() else 0
        deletions += int(parts[1]) if parts[1].isdigit() else 0
        paths.add(parts[2])
    return files, additions, deletions, frozenset(paths)


def _merge_tree(repo: Path, ours: str, theirs: str) -> tuple[int, str, list[str]]:
    """Exit code, the merged tree, and the conflicted paths. 0 merges clean, 1 collides, else it failed."""
    done = _git(["merge-tree", "--write-tree", "--name-only", "--no-messages", "--end-of-options",
                 ours, theirs], repo)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    return done.returncode, (lines[0] if lines else ""), lines[1:]


def merge_blocked(run: RunFacts | None, *, merged_in_git: bool, checkout_dirty: bool,
                  branch_exists: bool, head: str) -> str | None:
    """Why Merge would be refused, in the words RunService.merge refuses with, or None when it would not.

    The order is the service's own, so the first reason shown is the one the button would have hit. Two
    are added that the service cannot see from the row: a branch git already has in the checkout (a
    merge would say "Already up to date" and still be recorded as a merge), and a branch that is gone.
    """
    if run is None:
        return NOT_OURS
    if run.status != "done":
        return f"{run.ref} has not finished, so there is nothing settled to merge."
    if run.removed:
        return f"{run.ref}'s branch was removed, so there is nothing to merge."
    if run.merged:
        return f"{run.ref} is already merged into {run.merged.get('into', head)}."
    if run.diff_files <= 0:
        return "Nothing to merge: the branch has no commits."
    if merged_in_git:
        return f"{head} already has every commit on {run.branch}."
    if checkout_dirty:
        return "Your working tree has changes that are not committed. Commit or stash them, then merge."
    if not branch_exists:
        return f"{run.branch} no longer exists, so there is nothing to merge."
    return None


# ── the snapshot: one thread, every question the overview asks ───
def _view(repo: Path, head: Head, heads: frozenset[str], run: RunFacts | None, listed: dict[str, Any] | None,
          collided: set[str]) -> WorktreeView:
    branch = (listed or {}).get("branch") or (run.branch if run else "")
    path = (listed or {}).get("path") or (run.worktree if run else "")
    rev = f"refs/heads/{branch}" if branch in heads else (listed or {}).get("head", "")
    on_disk = bool(listed) and os.path.isdir(path)
    dirty = on_disk and bool(_git(["status", "--porcelain"], Path(path)).stdout.strip())

    ahead = behind = files = additions = deletions = commits = 0
    base, last, last_at, changed = "", "", None, frozenset[str]()
    if rev:
        counts = _out(["rev-list", "--left-right", "--count", "--end-of-options", f"HEAD...{rev}"], repo).split()
        if len(counts) == 2:
            behind, ahead = int(counts[0]), int(counts[1])
        hint = run.base if run and SHA.match(run.base or "") else ""
        base = hint if hint and _git(["cat-file", "-e", f"{hint}^{{commit}}"], repo).returncode == 0 \
            else _out(["merge-base", "--end-of-options", "HEAD", rev], repo)
        if base:
            files, additions, deletions, changed = _numstat(repo, f"{base}..{rev}")
            count = _out(["rev-list", "--count", "--end-of-options", f"{base}..{rev}"], repo)
            commits = int(count) if count.isdigit() else 0
        subject, _, at = _out(["log", "-1", "--format=%s%x1f%cI", "--end-of-options", rev], repo).partition("\x1f")
        last, last_at = subject, at or None

    merged_in_git = bool(rev) and ahead == 0 and commits > 0
    if (run and run.merged) or merged_in_git:
        status = "merged"
    elif branch in collided:
        status = "conflict"
    elif dirty:
        status = "dirty"
    elif ahead > 0:
        status = "ahead"
    else:
        status = "clean"

    blocked = merge_blocked(run, merged_in_git=merged_in_git, checkout_dirty=head.dirty,
                            branch_exists=branch in heads, head=head.branch)
    return WorktreeView(
        id=run.ref if run else f"wt:{hashlib.sha1(_real(path).encode()).hexdigest()[:8]}",
        branch=branch or "(detached)", path=_home(path), agent=(run.agent if run else "") or "—",
        task_ref=(run.task_ref if run else "") or "—", status=status, files_changed=files,
        additions=additions, deletions=deletions, ahead=ahead, behind=behind, last_commit=last,
        last_commit_at=last_at, run_ref=run.ref if run else None, run_status=run.status if run else None,
        made_by="neurocode" if run else "git", gone=run is not None and not on_disk,
        merged=run.merged if run else None, can_merge=blocked is None, merge_blocked=blocked,
        base=base, changed=changed)


def snapshot(root: Path, runs: list[RunFacts]) -> Snapshot:
    """Blocking. Everything the overview shows, read from git in one pass."""
    if not root.is_dir():
        return Snapshot(available=False, reason=f"The code is no longer at {_home(str(root))}.")
    found = repo_of(root)
    if found is None:
        return Snapshot(available=False, reason=f"{_home(str(root))} is not a git repository.")
    repo = found[0]
    sha = _out(["rev-parse", "--verify", "--quiet", "HEAD"], repo)
    if not sha:
        return Snapshot(available=False, repo=_home(str(repo)),
                        reason="The repository has no commit yet, so there is nothing to branch from.")
    head = Head(branch=_out(["rev-parse", "--abbrev-ref", "HEAD"], repo), sha=sha,
                dirty=bool(_git(["status", "--porcelain"], repo).stdout.strip()))
    heads = _branches(repo)
    snap = Snapshot(available=True, repo=_home(str(repo)), head=head, branches=heads,
                    shallow=_out(["rev-parse", "--is-shallow-repository"], repo) == "true")

    # The checkout is not a worktree of its own here, and a run that ran in it is not one either.
    top = _real(repo)
    listed = {_real(e["path"]): e for e in _listed_worktrees(repo) if _real(e["path"]) != top}
    collided = {branch for run in runs for branch, _files in run.conflicts}
    pairs: list[tuple[RunFacts | None, dict[str, Any] | None]] = []
    seen: set[str] = set()
    for run in runs:
        where = _real(run.worktree)
        if run.removed or not run.branch or where == top or where in seen:
            continue
        seen.add(where)
        pairs.append((run, listed.get(where)))
    pairs += [(None, entry) for where, entry in listed.items() if where not in seen]
    snap.worktrees = [_view(repo, head, heads, run, entry, collided) for run, entry in pairs[:MAX_WORKTREES]]

    snap.previews = _previews(repo, head, snap.worktrees)
    colliding = {p.branch for p in snap.previews if p.result == "collides"}
    snap.worktrees = [replace(w, status="conflict") if w.branch in colliding and w.status != "merged" else w
                      for w in snap.worktrees]
    snap.commits_today, snap.agent_commits_today = _today(repo, heads, snap.worktrees)
    return snap


def _previews(repo: Path, head: Head, worktrees: list[WorktreeView]) -> list[Preview]:
    """What merging each branch into the checkout would do today — only for branches with something to bring."""
    open_ = [w for w in worktrees if w.ahead > 0 and w.status != "merged" and w.branch != "(detached)"]
    if git_version() < MERGE_TREE:
        return [Preview(worktree_id=w.id, branch=w.branch, target=head.branch, result="stale",
                        files=w.files_changed, note="git is too old to preview merges (2.38 or later)")
                for w in open_]
    out: list[Preview] = []
    for w in open_:
        code, _tree, paths = _merge_tree(repo, "HEAD", f"refs/heads/{w.branch}")
        if code == 0 and w.behind:
            out.append(Preview(w.id, w.branch, head.branch, "stale", w.files_changed,
                               f"{w.behind} commits behind {head.branch}; merges clean today"))
        elif code == 0:
            out.append(Preview(w.id, w.branch, head.branch, "clean", w.files_changed,
                               f"merges cleanly into {head.branch}"))
        elif code == 1:
            out.append(Preview(w.id, w.branch, head.branch, "collides", len(paths),
                               f"collides with {head.branch} in {len(paths)} files",
                               on_file=paths[0] if paths else None))
        else:
            out.append(Preview(w.id, w.branch, head.branch, "stale", w.files_changed,
                               "git could not preview this merge"))

    # Two branches can each merge cleanly and still collide with each other. Only pairs that changed a
    # file in common can, so only those are asked.
    by_branch = {p.branch: p for p in out}
    asked = 0
    for i, a in enumerate(open_):
        for b in open_[i + 1:]:
            if asked >= PREVIEW_PAIRS or not a.changed & b.changed:
                continue
            asked += 1
            code, _tree, _paths = _merge_tree(repo, f"refs/heads/{a.branch}", f"refs/heads/{b.branch}")
            if code == 1:
                by_branch[a.branch].collides_with = by_branch[a.branch].collides_with or b.branch
                by_branch[b.branch].collides_with = by_branch[b.branch].collides_with or a.branch
    return out


def _today(repo: Path, heads: frozenset[str], worktrees: list[WorktreeView]) -> tuple[int, int]:
    """Commits since local midnight on the checkout and every open branch, and how many NeuroCode made."""
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    revs = ["HEAD", *sorted({f"refs/heads/{w.branch}" for w in worktrees if w.branch in heads})]
    lines = _out(["log", f"--since={midnight.isoformat()}", "--format=%ae", "--end-of-options", *revs],
                 repo).splitlines()
    return len(lines), sum(1 for email in lines if email == AGENT_EMAIL)


# ── one branch's diff ────────────────────────────────────────────
def _patch_path(chunk: str) -> str:
    """Which file one `diff --git` section is about, read from the lines git writes for exactly that."""
    for prefix in ("+++ b/", "rename to ", "--- a/"):
        for line in chunk.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):]
    header = chunk.splitlines()[0] if chunk else ""
    return header.rsplit(" b/", 1)[-1]


def branch_diff(repo: Path, rev_range: str) -> dict[str, Any]:
    """Blocking. The files a range changes, each with its change letter, its counts and its first hunk."""
    letters: dict[str, str] = {}
    tokens = _git(["diff", "--name-status", "-M", "-z", "--end-of-options", rev_range], repo).stdout.split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        if status[:1] in ("R", "C") and i + 2 < len(tokens):
            letters[tokens[i + 2]] = "R" if status[0] == "R" else "A"
            i += 3
        else:
            if i + 1 < len(tokens):
                letters[tokens[i + 1]] = status[:1] if status[:1] in ("A", "D", "M") else "M"
            i += 2

    counts: dict[str, tuple[int, int]] = {}
    tokens = _git(["diff", "--numstat", "-M", "-z", "--end-of-options", rev_range], repo).stdout.split("\0")
    i = 0
    while i < len(tokens):
        parts = tokens[i].split("\t")
        if len(parts) != 3:
            i += 1
            continue
        plus, minus = (int(parts[0]) if parts[0].isdigit() else 0), (int(parts[1]) if parts[1].isdigit() else 0)
        if parts[2] == "" and i + 2 < len(tokens):          # a rename: the old and new paths follow
            counts[tokens[i + 2]] = (plus, minus)
            i += 3
        else:
            counts[parts[2]] = (plus, minus)
            i += 1

    patch = _git(["diff", "-M", "--end-of-options", rev_range], repo).stdout
    clipped = len(patch) > MAX_DIFF
    hunks: dict[str, str] = {}
    for chunk in re.split(r"^diff --git ", patch[:MAX_DIFF], flags=re.M):
        if not chunk.strip():
            continue
        lines = chunk.splitlines()
        start = next((n for n, line in enumerate(lines) if line.startswith(("@@", "Binary files"))), len(lines))
        hunks[_patch_path(chunk)] = "\n".join(lines[start:start + HUNK_LINES])

    paths = list(dict.fromkeys([*letters, *counts]))
    files = [{"path": p, "change": letters.get(p, "M"), "additions": counts.get(p, (0, 0))[0],
              "deletions": counts.get(p, (0, 0))[1], "hunk": hunks.get(p, "")}
             for p in paths[:MAX_DIFF_FILES]]
    return {"files": files, "truncated": clipped or len(paths) > MAX_DIFF_FILES}


# ── history ──────────────────────────────────────────────────────
def history(repo: Path, head: str, heads: frozenset[str], runs: list[RunFacts], limit: int) -> list[dict[str, Any]]:
    """Blocking. The newest commits on the checkout and every open branch, each with the branch it came by."""
    owners = {run.branch: run for run in runs if run.branch}
    revs = ["HEAD", *sorted(f"refs/heads/{b}" for b in owners if b in heads)]
    done = _git(["log", "-n", str(limit), "--date-order", "--source", "--shortstat",
                 "--format=%x1e%H%x1f%s%x1f%an%x1f%ae%x1f%cI%x1f%S", "--end-of-options", *revs], repo)
    out: list[dict[str, Any]] = []
    for record in done.stdout.split("\x1e"):
        if not record.strip():
            continue
        first, _, rest = record.partition("\n")
        fields = first.split("\x1f")
        if len(fields) != 6:
            continue
        sha, subject, name, email, at, source = fields
        branch = head if source == "HEAD" else source.removeprefix("refs/heads/")
        changed = re.search(r"(\d+) files? changed", rest)
        owner = owners.get(branch)
        author = ((owner.agent if owner and owner.agent else "NeuroCode") if email == AGENT_EMAIL else name)
        out.append({"sha": sha, "message": subject, "author": author, "at": at,
                    "files": int(changed.group(1)) if changed else 0, "branch": branch})
    return out


# ── collisions, with their hunks ─────────────────────────────────
def _regions(text: str) -> list[dict[str, Any]]:
    """The conflict markers git wrote into a merged file: where each region is, and both sides of it."""
    regions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    side = ""
    for n, line in enumerate(text.splitlines(), start=1):
        if line.startswith("<<<<<<< "):
            current, side = {"start": n, "ours": [], "theirs": []}, "ours"
        elif current is not None and line.startswith("||||||| "):
            side = "base"
        elif current is not None and line == "=======":
            side = "theirs"
        elif current is not None and line.startswith(">>>>>>> "):
            current["end"] = n
            regions.append(current)
            current = None
            if len(regions) >= MAX_REGIONS:
                break
        elif current is not None and side in ("ours", "theirs") and len(current[side]) < SIDE_LINES:
            current[side].append(line)
    return regions


def collisions(repo: Path, snap: Snapshot, runs: list[RunFacts]) -> list[dict[str, Any]]:
    """Blocking. Every pair of branches known to collide, opened up to the lines that do."""
    owners = {run.branch: run for run in runs if run.branch}
    head = snap.head.branch

    def label(branch: str) -> str:
        if branch == "HEAD":
            return head
        owner = owners.get(branch)
        return (owner.task_ref or owner.ref) if owner else branch

    def agent_of(branch: str) -> str:
        if branch == "HEAD":
            return "you (checkout)"
        owner = owners.get(branch)
        return (owner.agent or "NeuroCode") if owner else "made outside NeuroCode"

    # (ours, theirs, the files a run recorded, what that run said)
    pairs: list[tuple[str, str, tuple[str, ...], str]] = []
    for run in runs:
        if run.removed:
            continue
        notes = dict(run.merge_notes)
        for branch, files in run.conflicts:
            pairs.append((run.branch, branch, files, notes.get(branch) or "The merge was undone, so nothing is half-applied."))
    for p in snap.previews:
        if p.result == "collides":
            pairs.append(("HEAD", p.branch, (), ""))
        if p.collides_with and p.branch < p.collides_with:
            pairs.append((p.branch, p.collides_with, (), ""))
    unique = list({(a, b): (a, b, files, note) for a, b, files, note in pairs}.values())

    out: list[dict[str, Any]] = []
    for ours, theirs, recorded, note in unique[:MAX_PAIRS]:
        refs = [b if b == "HEAD" else f"refs/heads/{b}" for b in (ours, theirs)]
        pair = f"{label(ours)} ✕ {label(theirs)}"
        missing = [b for b in (ours, theirs) if b != "HEAD" and b not in snap.branches]
        code, tree, paths = (1, "", []) if missing else _merge_tree(repo, refs[0], refs[1])
        if missing or code != 1 or git_version() < MERGE_TREE:
            words = ("branch removed" if missing else
                     "They no longer collide: merging the two today would be clean." if code == 0 else note)
            out += [{"id": _id(ours, theirs, f, 0), "file": f, "taskRef": pair, "region": "", "hunks": [],
                     "policy": POLICY, "resolution": words or note, "resolvedBy": "unresolved"}
                    for f in recorded[:PATHS_PER_PAIR]]
            continue
        for path in paths[:PATHS_PER_PAIR]:
            merged = _git(["cat-file", "-p", f"{tree}:{path}"], repo).stdout
            sides = {}
            for branch, rev in zip((ours, theirs), refs, strict=True):
                commit, _, at = _out(["log", "-1", "--format=%h%x1f%cI", "--end-of-options", rev, "--", path],
                                     repo).partition("\x1f")
                sides[branch] = (commit, at or None)
            for n, region in enumerate(_regions(merged)):
                out.append({
                    "id": _id(ours, theirs, path, n), "file": path, "taskRef": pair,
                    "region": f"L{region['start']} – L{region.get('end', region['start'])}",
                    "hunks": [{"side": side, "branch": head if branch == "HEAD" else branch,
                               "agent": agent_of(branch), "commit": sides[branch][0], "at": sides[branch][1],
                               "lines": "\n".join(region[side])}
                              for side, branch in (("ours", ours), ("theirs", theirs))],
                    "policy": POLICY,
                    "resolution": note or "Not merged. Nothing in NeuroCode resolves this for you.",
                    "resolvedBy": "unresolved"})
    return out


def _id(ours: str, theirs: str, path: str, n: int) -> str:
    return hashlib.sha1(f"{ours}|{theirs}|{path}|{n}".encode()).hexdigest()[:12]


# ── the service ──────────────────────────────────────────────────
class GitViewService:
    """Every read the Git screen does. It writes nothing: merging is RunService's, behind its own gate."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.runs = RunRepository(session)

    async def _project(self, project_id: str) -> Project:
        found = await self.projects.get(project_id)
        if found is None:
            raise NotFound(f"project {project_id}")
        return found

    async def _facts(self, project_id: str) -> list[RunFacts]:
        """The project's newest runs as plain records, with their task refs, in a fixed number of queries."""
        runs: list[Run] = (await self.runs.newest(project_id, limit=RUNS_SCANNED)).items
        task_ids = [r.task_id for r in runs if r.task_id]
        plan_ids = [r.plan_id for r in runs if r.plan_id]
        tasks = {t.id: t.ref for t in await TaskRepository(self.session).list(
            Task.id.in_(task_ids), limit=RUNS_SCANNED)} if task_ids else {}
        plans = {p.id: p.ref for p in await PlanRepository(self.session).list(
            Plan.id.in_(plan_ids), limit=RUNS_SCANNED)} if plan_ids else {}
        branch_of = {r.id: r.branch for r in runs}
        facts: list[RunFacts] = []
        for r in runs:
            edits = [s.agent for s in r.steps if s.kind == "edit" and s.agent]
            facts.append(RunFacts(
                id=r.id, ref=r.ref, status=r.status, role=r.role, branch=r.branch, worktree=r.worktree,
                base=r.base, agent=r.agent or (edits[0] if edits else "") or r.requested_by,
                task_ref=tasks.get(r.task_id or "") or plans.get(r.plan_id or "") or r.ref,
                removed=r.removed, merged=r.merged, diff_files=r.diff_files,
                conflicts=tuple((c.branch, tuple(c.files or [])) for c in r.conflicts),
                merge_notes=tuple((branch_of[s.child_run_id], s.detail) for s in r.steps
                                  if s.kind == "merge" and s.child_run_id in branch_of and s.detail)))
        return facts

    async def _root(self, project: Project) -> Path:
        root = checkout(project)
        if root is None:
            raise Refused(SAMPLE)
        return root

    async def overview(self, project_id: str) -> dict[str, Any]:
        project = await self._project(project_id)
        awaiting = await ApprovalRepository(self.session).count(
            Approval.project_id == project_id, Approval.status == "pending", Approval.tool.like("Merge(%"))
        root = checkout(project)
        if root is None:
            return overview_json(Snapshot(available=False, reason=SAMPLE), awaiting_you=awaiting)
        snap = await asyncio.to_thread(snapshot, root, await self._facts(project_id))
        return overview_json(snap, awaiting_you=awaiting)

    async def _available(self, project_id: str) -> tuple[Snapshot, list[RunFacts], Path]:
        project = await self._project(project_id)
        root = await self._root(project)
        facts = await self._facts(project_id)
        snap = await asyncio.to_thread(snapshot, root, facts)
        if not snap.available:
            raise Refused(snap.reason)
        found = await asyncio.to_thread(repo_of, root)
        if found is None:
            raise Refused(f"{project.name} is not a git repository.")
        return snap, facts, found[0]

    async def diff(self, project_id: str, branch: str, against: Literal["base", "head"]) -> dict[str, Any]:
        """What a branch changed since its base — or, against the head, what merging it now would bring."""
        project = await self._project(project_id)
        root = await self._root(project)
        facts = await self._facts(project_id)

        def read() -> dict[str, Any]:
            found = repo_of(root)
            if found is None:
                raise Refused(f"{project.name} is not a git repository.")
            repo = found[0]
            if branch not in _branches(repo):
                raise NotFound(f"branch {branch}")
            rev = f"refs/heads/{branch}"
            if against == "head":
                label = _out(["rev-parse", "--abbrev-ref", "HEAD"], repo)
                return {"branch": branch, "against": label, **branch_diff(repo, f"HEAD...{rev}")}
            run = next((r for r in facts if r.branch == branch and SHA.match(r.base or "")), None)
            base = run.base if run and _git(["cat-file", "-e", f"{run.base}^{{commit}}"], repo).returncode == 0 \
                else _out(["merge-base", "--end-of-options", "HEAD", rev], repo)
            if not base:
                return {"branch": branch, "against": "", "files": [], "truncated": False}
            return {"branch": branch, "against": base[:7], **branch_diff(repo, f"{base}..{rev}")}

        return await asyncio.to_thread(read)

    async def commits(self, project_id: str, limit: int) -> dict[str, Any]:
        project = await self._project(project_id)
        root = await self._root(project)
        facts = await self._facts(project_id)

        def read() -> dict[str, Any]:
            found = repo_of(root)
            if found is None:
                raise Refused(f"{project.name} is not a git repository.")
            repo = found[0]
            if not _out(["rev-parse", "--verify", "--quiet", "HEAD"], repo):
                return {"shallow": False, "commits": []}
            head = _out(["rev-parse", "--abbrev-ref", "HEAD"], repo)
            open_runs = [r for r in facts if not r.removed]
            return {"shallow": _out(["rev-parse", "--is-shallow-repository"], repo) == "true",
                    "commits": history(repo, head, _branches(repo), open_runs, max(1, min(limit, MAX_COMMITS)))}

        return await asyncio.to_thread(read)

    async def conflicts(self, project_id: str) -> list[dict[str, Any]]:
        snap, facts, repo = await self._available(project_id)
        return await asyncio.to_thread(collisions, repo, snap, facts)

