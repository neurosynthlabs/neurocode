"""Git and the worktree, as plain blocking functions.

Lifted out of the old runtime unchanged in behaviour. Nothing here opens a database or awaits
anything, so each rule can be read on its own line and tested by calling it — and the orchestrator
above can run these in a thread without dragging a session in with them.

Every git call is made with fixed arguments and a timeout, and with `GIT_TERMINAL_PROMPT=0` so a
repository that wants a password fails instead of hanging forever.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILES, MAX_FILE_BYTES, MAX_DIFF = 20, 256_000, 200_000
TEST_TIMEOUT, TEST_LINES = 600, 400

AUTHOR = ["-c", "user.name=NeuroCode", "-c", "user.email=neurocode@localhost", "-c", "commit.gpgsign=false"]

# How a project runs its own tests. The first match whose tool is installed wins.
TEST_RECIPES: list[tuple[str, list[str], str | None]] = [
    ("Makefile", ["make", "test"], "test:"),
    ("uv.lock", ["uv", "run", "pytest", "-q"], None),
    ("pytest.ini", ["python", "-m", "pytest", "-q"], None),
    ("pyproject.toml", ["python", "-m", "pytest", "-q"], "pytest"),
    ("package.json", ["npm", "test", "--silent"], '"test"'),
    ("go.mod", ["go", "test", "./..."], None),
]


class Refused(RuntimeError):
    """Why something cannot be done: no code here, nothing to branch from, a path that escapes."""


def git(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "core.fsmonitor=false", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def repo_of(root: Path) -> tuple[Path, str] | None:
    """The repository that holds a project's root, and where the root sits inside it."""
    try:
        out = git(["rev-parse", "--show-toplevel"], root)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    top, here = Path(os.path.realpath(out.stdout.strip())), Path(os.path.realpath(root))
    try:
        return top, ("" if here == top else str(here.relative_to(top)))
    except ValueError:
        return None


def detect_tests(root: Path) -> dict[str, Any] | None:
    """The project's own test command, found in the project itself — never one a model chose."""
    for name, argv, needle in TEST_RECIPES:
        f = root / name
        if not f.is_file() or shutil.which(argv[0]) is None:
            continue
        if needle:
            try:
                if needle not in f.read_text(errors="replace"):
                    continue
            except OSError:
                continue
        return {"argv": argv, "command": " ".join(argv)}
    if (next(root.glob("*.sln"), None) or next(root.glob("*.csproj"), None)) and shutil.which("dotnet"):
        return {"argv": ["dotnet", "test"], "command": "dotnet test"}
    return None


def free_branch(repo: Path, wanted: str) -> str:
    """`wanted`, or `wanted-2`, `wanted-3`… — a run never takes a branch that already exists."""
    name, n = wanted, 1
    while git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], repo).returncode == 0:
        n += 1
        name = f"{wanted}-{n}"
    return name


def open_worktree(repo: Path, branch: str, base: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        git(["worktree", "prune"], repo)
    out = git(["worktree", "add", "-b", branch, str(path), base], repo, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"git worktree: {out.stderr.strip()[:200]}")


def safe_path(rel: str) -> Path:
    """A path the runtime is willing to write: inside the worktree, never .git, never upwards.
    A path that tries to leave is refused, never quietly rewritten."""
    p = PurePosixPath(str(rel).strip().replace("\\", "/"))
    parts = [part for part in p.parts if part != "."]
    if not parts or p.is_absolute() or any(part in ("..", ".git") for part in parts):
        raise Refused(f"refused to write outside the worktree: {rel}")
    return Path(*parts)


def apply_files(work: Path, files: Iterable[tuple[str, str]]) -> list[str]:
    """Write what a model proposed. Bounded in number and size, and never outside the worktree."""
    written: list[str] = []
    root = work.resolve()
    for path, content in list(files)[:MAX_FILES]:
        rel = safe_path(path)
        target = (work / rel).resolve()
        if not str(target).startswith(f"{root}{os.sep}") and target != root:
            raise Refused(f"refused to write outside the worktree: {path}")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise Refused(f"refused to write {path}: it is larger than {MAX_FILE_BYTES // 1000} kB")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(str(rel))
    return written


def stats(tree: Path, base: str) -> dict[str, int]:
    """What the branch actually changed, measured with git rather than claimed by anyone."""
    files = insertions = deletions = 0
    for line in git(["diff", "--numstat", f"{base}..HEAD"], tree).stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        insertions += int(parts[0]) if parts[0].isdigit() else 0
        deletions += int(parts[1]) if parts[1].isdigit() else 0
    count = git(["rev-list", "--count", f"{base}..HEAD"], tree).stdout.strip()
    return {"files": files, "insertions": insertions, "deletions": deletions, "commits": int(count or 0)}


def commit(work: Path, message: str) -> bool:
    """Commit whatever the step wrote. False when there was nothing to commit."""
    git(["add", "-A"], work)
    if not git(["status", "--porcelain"], work).stdout.strip():
        return False
    out = git([*AUTHOR, "commit", "-m", message], work)
    if out.returncode != 0:
        raise RuntimeError(f"git commit: {out.stderr.strip()[:200]}")
    return True


def merge_branch(work: Path, branch: str, base: str, into: str) -> tuple[bool, list[str]]:
    """Bring one branch into this worktree. On a collision the merge is undone, never half-applied."""
    if git(["rev-list", "--count", f"{base}..{branch}"], work).stdout.strip() in ("", "0"):
        return False, []
    out = git([*AUTHOR, "merge", "--no-ff", "-m", f"Merge {branch} into {into}", branch], work, timeout=300)
    if out.returncode == 0:
        return True, []
    files = [ln for ln in git(["diff", "--name-only", "--diff-filter=U"], work).stdout.splitlines() if ln.strip()]
    git(["merge", "--abort"], work)
    return False, files[:20]


def run_tests(argv: list[str], cwd: Path, on_line: Callable[[int, str], None],
              stopped: threading.Event) -> tuple[int, list[str]]:
    """The project's own command, with a timeout and a kill switch. Returns its code and its last lines."""
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, "CI": "1", "NO_COLOR": "1"})
    killer = threading.Timer(TEST_TIMEOUT, proc.kill)
    killer.start()
    tail: list[str] = []
    try:
        for i, line in enumerate(proc.stdout or []):
            if stopped.is_set():
                proc.kill()
                break
            text = line.rstrip()[:400]
            tail = [*tail[-4:], text]
            on_line(i, text)
        code = proc.wait(timeout=60)
    finally:
        killer.cancel()
    return code, tail


def diff(tree: Path, base: str) -> str:
    return git(["diff", f"{base}..HEAD"], tree, timeout=60).stdout


def cleanup(repo: Path, tree: Path, branch: str) -> None:
    """Remove the worktree and the branch. The commits stay until git prunes them."""
    if tree.exists():
        git(["worktree", "remove", "--force", str(tree)], repo, timeout=120)
    if tree.exists():
        shutil.rmtree(tree, ignore_errors=True)
    git(["worktree", "prune"], repo)
    git(["branch", "-D", branch], repo)


def merge_into_checkout(repo: Path, branch: str, message: str) -> dict[str, Any]:
    """Merge into whatever the repository has checked out. Refuses a dirty tree, undoes itself on a
    collision, and always hands back the command that undoes it."""
    if git(["status", "--porcelain"], repo).stdout.strip():
        raise Refused("Your working tree has changes that are not committed. Commit or stash them, then merge.")
    into = git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    before = git(["rev-parse", "HEAD"], repo).stdout.strip()
    out = git([*AUTHOR, "merge", "--no-ff", "-m", message, branch], repo, timeout=300)
    if out.returncode != 0:
        files = [ln for ln in git(["diff", "--name-only", "--diff-filter=U"], repo).stdout.splitlines() if ln.strip()]
        git(["merge", "--abort"], repo)
        return {"merged": False, "into": into, "conflicts": files[:20], "commit": None, "undo": None}
    sha = git(["rev-parse", "HEAD"], repo).stdout.strip()
    return {"merged": True, "into": into, "conflicts": [], "commit": sha[:7],
            "undo": f"git reset --hard {before[:7]}"}


#: Patterns the offline reviewer looks for when no model can read the diff.
SECRETS = re.compile(r"(sk-[A-Za-z0-9]{10,}|password\s*=\s*['\"][^'\"]{3,}|api[_-]?key\s*=\s*['\"][^'\"]{6,})", re.I)
LEFTOVERS = re.compile(r"\b(console\.log|debugger|print\()")
TODO = re.compile(r"\b(TODO|FIXME|XXX)\b")
