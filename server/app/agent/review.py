"""Any diff a person asks to have read — a branch against its base, the working tree, a range of commits.

Blocking git, like the rest of this package, and it only ever reads: no checkout moves, nothing is
staged, and the working tree is diffed as it stands. The one thing it may do beyond reading is fetch a
branch someone pushed, when a person asked for exactly that, with their own git credentials.

Every name a person types is checked before git sees it: a ref is one word of the characters refs are
made of, never an option (`-…`) and never a range (`a..b`); then git itself must know it as a commit.
The patch is labelled the way the project names its paths, so a finding's `file` is a path a person
can open.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .git import git, label_patch, repo_of

#: What a ref a person names may be made of. Refs hold more than this in theory; none worth reviewing does.
REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/@{}~^+-]{0,199}$")
#: New files in a working tree that are read into its patch, at most, and how big one may be.
MAX_UNTRACKED, MAX_UNTRACKED_BYTES = 40, 200_000
#: Where a repository keeps its brief for reviewers, in the order they are looked for.
BRIEFS = ("REVIEW.md", ".neurocode/REVIEW.md")
#: The most of a brief a reviewer is handed. A brief longer than this is a document, not a brief.
MAX_BRIEF = 16_000
#: The branches listed for a person to pick from, newest first.
MAX_BRANCHES = 200
FETCH_TIMEOUT = 120
TRAILER = re.compile(r"\bNeuroCode (RUN-\d+)\b")


class Unreviewable(RuntimeError):
    """Why this diff cannot be read, in words for the screen."""


@dataclass
class Diff:
    patch: str
    base: str                                # the commit the diff starts from
    head: str                                # the commit it ends at; empty for a working tree
    commits: int
    untracked: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, int]:
        return {"files": len(self.files), "insertions": sum(f["insertions"] for f in self.files),
                "deletions": sum(f["deletions"] for f in self.files), "commits": self.commits,
                "untracked": self.untracked}


def _say(out: subprocess.CompletedProcess[str]) -> str:
    return (out.stderr or out.stdout).strip().splitlines()[-1][:200] if (out.stderr or out.stdout).strip() else ""


def checked_ref(repo: Path, ref: str, what: str) -> str:
    """The commit a person's ref names. Refused when it is not shaped like a ref or git does not know it."""
    name = ref.strip()
    if not REF.match(name) or ".." in name or name.endswith((".lock", "/")):
        raise Unreviewable(f"{name or 'An empty name'} is not a branch, tag or commit NeuroCode will read as the "
                           f"{what}.")
    out = git(["rev-parse", "--verify", "--quiet", f"{name}^{{commit}}"], repo)
    if out.returncode != 0 or not out.stdout.strip():
        raise Unreviewable(f"This repository has no branch, tag or commit called {name} (the {what}).")
    return out.stdout.strip()


def current_branch(repo: Path) -> str | None:
    out = git(["symbolic-ref", "--short", "-q", "HEAD"], repo)
    return out.stdout.strip() or None if out.returncode == 0 else None


def remote_names(repo: Path) -> list[str]:
    return [line.strip() for line in git(["remote"], repo).stdout.splitlines() if line.strip()]


def branches(repo: Path) -> list[dict[str, Any]]:
    """Local branches and the remote ones this checkout knows of, the most recently committed first."""
    out = git(["for-each-ref", f"--count={MAX_BRANCHES}", "--sort=-committerdate",
               "--format=%(refname)%09%(refname:short)%09%(objectname:short)%09%(committerdate:iso-strict)%09"
               "%(subject)", "refs/heads", "refs/remotes"], repo)
    current = current_branch(repo)
    found: list[dict[str, Any]] = []
    for line in out.stdout.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5 or parts[0].endswith("/HEAD"):
            continue
        full, short, sha, at, subject = parts
        remote = full.startswith("refs/remotes/")
        found.append({"name": short, "remote": remote, "sha": sha, "at": at or None, "subject": subject[:200],
                      "current": not remote and short == current})
    return found


def dirty_files(root: Path) -> int:
    """How many paths under this checkout differ from its last commit, new files included."""
    out = git(["status", "--porcelain", "--untracked-files=all", "--", "."], root)
    return len([line for line in out.stdout.splitlines() if line.strip()])


def fetch(repo: Path, head: str) -> str | None:
    """Fetch a branch someone pushed, when `head` names one of this checkout's remotes (`origin/feature`).
    Returns what was fetched, or None when `head` is not a remote's branch. Raises with git's reason."""
    remote, sep, branch = head.strip().partition("/")
    if not sep or remote not in remote_names(repo) or not REF.match(branch) or ".." in branch:
        return None
    try:
        out = subprocess.run(["git", "-c", "core.fsmonitor=false", "fetch", "--no-tags", "--quiet", remote,
                              f"refs/heads/{branch}:refs/remotes/{remote}/{branch}"], cwd=repo,
                             capture_output=True, text=True, timeout=FETCH_TIMEOUT,
                             env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired as slow:
        raise Unreviewable(f"Fetching {head} took longer than {FETCH_TIMEOUT} seconds, so it was stopped.") from slow
    if out.returncode != 0:
        raise Unreviewable(f"{remote} would not give {branch}: {_say(out) or 'git fetch failed'}.")
    return f"{remote}/{branch}"


def _files(patch: str) -> list[dict[str, Any]]:
    """Each file in a patch with what it adds and removes, read from the patch itself — so the numbers are
    of exactly the bytes the reviewer is handed."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            path = line.rsplit(" b/", 1)[-1] if " b/" in line else line[len("diff --git "):]
            current = {"path": path.strip('"'), "insertions": 0, "deletions": 0}
            files.append(current)
            in_hunk = False
        elif current is None:
            continue
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            current["insertions"] += 1
        elif in_hunk and line.startswith("-"):
            current["deletions"] += 1
    return files


def _untracked(repo: Path, root: Path, spec: list[str]) -> tuple[str, int]:
    """New files git is not tracking yet, as patches that add them. Only text files, capped in number and
    size; nothing is staged to get them, so the working tree and the index are left exactly as they were."""
    out = git(["ls-files", "--others", "--exclude-standard", "-z", "--", *spec], repo)
    names = [n for n in out.stdout.split("\0") if n][:MAX_UNTRACKED]
    patches: list[str] = []
    for name in names:
        f = repo / name
        try:
            if (f.is_symlink() or not f.is_file() or f.stat().st_size > MAX_UNTRACKED_BYTES
                    or not os.path.realpath(f).startswith(os.path.realpath(root) + os.sep)):
                continue
        except OSError:
            continue
        made = git(["diff", "--no-color", "--no-ext-diff", "--no-index", "--", "/dev/null", name], repo, timeout=30)
        if "Binary files" in made.stdout[:400]:
            continue
        patches.append(made.stdout)
    return "".join(patches), len(patches)


def build(root: Path, target: str, base: str, head: str, label: str = "") -> Diff:
    """The diff to review, from the checkout at `root`, its paths as the project names them (`label` is a
    further source's; empty for the first). `branch`: what `head` changed since it left `base` — the diff a
    pull request shows. `commit-range`: `base` to `head`, exactly. `working-tree`: everything not yet
    committed, new files included."""
    found = repo_of(root)
    if found is None:
        raise Unreviewable("This checkout is not a git repository, so there is no diff to read.")
    repo, prefix = found
    spec = [prefix] if prefix else []
    args = ["diff", "--no-color", "--no-ext-diff"]
    if target == "working-tree":
        start = git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], repo).stdout.strip()
        if not start:
            raise Unreviewable("This repository has no commit yet, so there is nothing to compare the working "
                               "tree with.")
        patch = git([*args, start, "--", *spec], repo, timeout=60).stdout
        extra, count = _untracked(repo, root, spec)
        whole = label_patch(patch + extra, label, prefix)
        return Diff(whole, start, "", 0, count, _files(whole))
    end = checked_ref(repo, head, "head")
    start = checked_ref(repo, base, "base")
    if target == "branch":
        joined = git(["merge-base", start, end], repo)
        if joined.returncode != 0 or not joined.stdout.strip():
            raise Unreviewable(f"{head} and {base} share no history, so there is no branch diff between them.")
        start = joined.stdout.strip()
    patch = label_patch(git([*args, start, end, "--", *spec], repo, timeout=60).stdout, label, prefix)
    count = git(["rev-list", "--count", f"{start}..{end}"], repo).stdout.strip()
    return Diff(patch, start, end, int(count or 0), 0, _files(patch))


def brief(root: Path) -> tuple[str, str] | None:
    """The repository's brief for reviewers — REVIEW.md, then .neurocode/REVIEW.md, in the checkout and then
    at the top of its repository — and where it was found. Read from the checkout as it stands, never from
    the diff under review, so a change cannot rewrite the rules it is read by. Capped; None when there is none."""
    places = [root]
    found = repo_of(root)
    if found is not None and found[0] != root:
        places.append(found[0])
    for place in places:
        home = os.path.realpath(place)
        for name in BRIEFS:
            f = place / name
            try:
                if not f.is_file() or not os.path.realpath(f).startswith(home + os.sep):
                    continue
                text = f.read_text(errors="replace")
            except OSError:
                continue
            if text.strip():
                shown = name if place == root else f"{os.path.relpath(f, root)}"
                return text[:MAX_BRIEF], shown
    return None


def run_refs(repo: Path, base: str, head: str) -> list[str]:
    """The NeuroCode runs whose commits are in this range, by the trailer each of their commits carries."""
    out = git(["log", "--format=%B", f"{base}..{head}", "-n", "200"], repo)
    return list(dict.fromkeys(TRAILER.findall(out.stdout)))
