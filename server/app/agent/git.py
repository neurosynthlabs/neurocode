"""Git and the worktree, as plain blocking functions.

Lifted out of the old runtime unchanged in behaviour. Nothing here opens a database or awaits
anything, so each rule can be read on its own line and tested by calling it — and the orchestrator
above can run these in a thread without dragging a session in with them.

Every git call is made with fixed arguments and a timeout, and with `GIT_TERMINAL_PROMPT=0` so a
repository that wants a password fails instead of hanging forever.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

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


#: The checks a project may have besides its tests, in the order a run takes them.
CHECK_NAMES: tuple[str, ...] = ("lint", "typecheck")
#: package.json scripts that mean "check the types", the first one present wins.
TYPECHECK_SCRIPTS = ("typecheck", "type-check", "tsc")


def _read(f: Path) -> str:
    try:
        return f.read_text(errors="replace") if f.is_file() and not f.is_symlink() else ""
    except OSError:
        return ""


def _make_targets(root: Path) -> set[str]:
    return {line.split(":", 1)[0].strip() for line in _read(root / "Makefile").splitlines()
            if re.match(r"^[A-Za-z][\w-]*\s*:(?!=)", line)}


def _python_tool(root: Path, tool: list[str]) -> list[str] | None:
    """A Python tool the way the project runs it: through uv when it is a uv project, else from PATH."""
    if (root / "uv.lock").is_file() and shutil.which("uv"):
        return ["uv", "run", *tool]
    return tool if shutil.which(tool[0]) else None


def detect_checks(root: Path) -> list[dict[str, Any]]:
    """The project's own lint and typecheck commands, found in the project itself — never chosen by a
    model, and only ones whose tool is installed here. One command per check, in `CHECK_NAMES` order.

    A Makefile target of that name wins, as it does for tests: it is what the project says it runs.
    Then package.json scripts, ruff and mypy when the project configures them, `go vet` for a Go
    module and `dotnet format --verify-no-changes` for a .NET solution.
    """
    found: dict[str, list[str]] = {}
    targets = _make_targets(root) if shutil.which("make") else set()
    for name in CHECK_NAMES:
        if name in targets:
            found[name] = ["make", name]

    scripts: dict[str, Any] = {}
    if shutil.which("npm"):
        try:
            loaded = json.loads(_read(root / "package.json") or "{}")
        except ValueError:
            loaded = {}
        found_scripts = loaded.get("scripts") if isinstance(loaded, dict) else None
        scripts = found_scripts if isinstance(found_scripts, dict) else {}
    if "lint" in scripts:
        found.setdefault("lint", ["npm", "run", "--silent", "lint"])
    typed = next((s for s in TYPECHECK_SCRIPTS if s in scripts), None)
    if typed:
        found.setdefault("typecheck", ["npm", "run", "--silent", typed])

    pyproject = _read(root / "pyproject.toml")
    if "[tool.ruff" in pyproject or (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file():
        ruff = _python_tool(root, ["ruff", "check", "."])
        if ruff:
            found.setdefault("lint", ruff)
    if "[tool.mypy" in pyproject or (root / "mypy.ini").is_file() or "[mypy" in _read(root / "setup.cfg"):
        mypy = _python_tool(root, ["mypy", "."])
        if mypy:
            found.setdefault("typecheck", mypy)

    if (root / "go.mod").is_file() and shutil.which("go"):
        found.setdefault("lint", ["go", "vet", "./..."])
    if (next(root.glob("*.sln"), None) or next(root.glob("*.csproj"), None)) and shutil.which("dotnet"):
        found.setdefault("lint", ["dotnet", "format", "--verify-no-changes"])
    return [{"name": name, "argv": found[name], "command": " ".join(found[name])}
            for name in CHECK_NAMES if name in found]


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
    return git(["diff", "--no-color", "--no-ext-diff", f"{base}..HEAD"], tree, timeout=60).stdout


def branch_diff(repo: Path, base: str, branch: str) -> tuple[str, str]:
    """The branch's whole patch against its base, and the commit it ends at — read from the branch
    itself rather than a worktree, so what is fingerprinted is exactly what a merge or a push would take.
    ('', '') when the branch is gone."""
    head = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], repo)
    if head.returncode != 0 or not head.stdout.strip():
        return "", ""
    sha = head.stdout.strip()
    patch = git(["diff", "--no-color", "--no-ext-diff", f"{base}..{sha}"], repo, timeout=60).stdout
    return patch, sha


def fingerprint(patch: str) -> str:
    """What a review receipt holds: the sha-256 of the patch the reviewer was given."""
    return "sha256:" + hashlib.sha256(patch.encode()).hexdigest()


# ── pushing a run's branch to the project's own remote ───────────
PUSH_TIMEOUT = 120
#: Hosts whose compare page opens a pull request, and how to build it. Anything else gets no link.
FORGES = ("github.com", "gitlab.com", "bitbucket.org")
_REMOTE = re.compile(r"^(?:(?:https?|ssh|git)://(?:[^@/]+@)?(?P<h1>[^/:]+)(?::\d+)?/|(?:[^@/]+@)?(?P<h2>[^/:]+):)"
                     r"(?P<path>[^?#]+?)(?:\.git)?/?$")


def remotes(repo: Path) -> list[str]:
    return [line.strip() for line in git(["remote"], repo).stdout.splitlines() if line.strip()]


def compare_url(remote_url: str, base: str | None, branch: str) -> str | None:
    """The page on GitHub, GitLab or Bitbucket where a person opens a pull request for `branch` under
    their own account — or None for any other remote, a path on disk included. Credentials written
    into the URL are never carried into the link."""
    found = _REMOTE.match(remote_url.strip())
    if not found:
        return None
    host = (found.group("h1") or found.group("h2") or "").lower()
    safe = "/-_.~"
    path = quote(found.group("path").strip("/"), safe=safe)
    if host not in FORGES or path.count("/") < 1:
        return None
    head = quote(branch, safe=safe)
    if host == "github.com":
        span = f"{quote(base, safe=safe)}...{head}" if base else head
        return f"https://github.com/{path}/compare/{span}?expand=1"
    if host == "gitlab.com":
        into = f"&merge_request%5Btarget_branch%5D={quote(base, safe='')}" if base else ""
        source = f"merge_request%5Bsource_branch%5D={quote(branch, safe='')}"
        return f"https://gitlab.com/{path}/-/merge_requests/new?{source}{into}"
    into = f"&dest={quote(base, safe='')}" if base else ""
    return f"https://bitbucket.org/{path}/pull-requests/new?source={quote(branch, safe='')}{into}"


def _push_env(repo: Path) -> dict[str, str]:
    """The person's own git credentials, asked for nothing: no terminal prompt, and ssh in batch mode
    unless they configured ssh for git themselves — a push that wants a password fails in words
    instead of hanging a worker forever."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    if "GIT_SSH_COMMAND" not in env and not git(["config", "--get", "core.sshCommand"], repo).stdout.strip():
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def _push_refusal(stderr: str, remote: str, branch: str) -> str:
    said = stderr.lower()
    first = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")[:200]
    if any(k in said for k in ("authentication failed", "permission denied", "could not read username",
                               "could not read password", "403", "access denied", "invalid username")):
        return (f"{remote} refused your credentials, so nothing was pushed. Sign in to it with git on this "
                f"machine (a credential helper or an ssh key), then push again. Git said: {first}")
    if "rejected" in said or "non-fast-forward" in said or "fetch first" in said:
        return (f"{remote} already has a different {branch}, so nothing was pushed. NeuroCode never "
                f"force-pushes: rename or delete that branch on {remote} yourself. Git said: {first}")
    if any(k in said for k in ("could not resolve host", "unable to access", "connection refused",
                               "timed out", "network is unreachable", "does not appear to be a git repository")):
        return f"{remote} could not be reached, so nothing was pushed. Git said: {first}"
    return f"Git could not push {branch} to {remote}: {first or 'no reason given'}"


def push(repo: Path, branch: str, remote: str | None = None) -> dict[str, Any]:
    """Push one local branch to the project's remote under the same name, with the person's own
    credentials. Never forced: the refspec carries no `+` and no force flag, so a remote branch that
    moved is refused by git, not overwritten. Returns {remote, branch, sha, compareUrl}."""
    if not branch or branch.startswith("-"):
        raise Refused(f"{branch!r} is not a branch NeuroCode will push.")
    known = remotes(repo)
    if not known:
        raise Refused("This repository has no remote to push to. Add one with `git remote add origin <url>`, "
                      "then push again.")
    if remote is None:
        remote = "origin" if "origin" in known else known[0] if len(known) == 1 else None
        if remote is None:
            raise Refused(f"This repository has {len(known)} remotes and none is called origin: "
                          f"say which — {', '.join(known[:6])}.")
    elif remote not in known:
        raise Refused(f"This repository has no remote called {remote}. It has: {', '.join(known[:6])}.")
    sha = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], repo).stdout.strip()
    if not sha:
        raise Refused(f"{branch} no longer exists on this machine, so there is nothing to push.")
    url = git(["remote", "get-url", "--", remote], repo).stdout.strip()
    try:
        out = subprocess.run(["git", "-c", "core.fsmonitor=false", "push", "--porcelain", "--", remote,
                              f"refs/heads/{branch}:refs/heads/{branch}"], cwd=repo, capture_output=True,
                             text=True, timeout=PUSH_TIMEOUT, env=_push_env(repo))
    except subprocess.TimeoutExpired as late:
        raise Refused(f"{remote} did not answer within {PUSH_TIMEOUT} s, so the push was stopped.") from late
    if out.returncode != 0:
        raise Refused(_push_refusal(out.stderr or out.stdout, remote, branch))
    base = git(["symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"], repo).stdout.strip()
    base = base.removeprefix(f"{remote}/") if base else ""
    if not base:
        checked_out = git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
        base = "" if checked_out in ("", "HEAD") else checked_out
    return {"remote": remote, "branch": branch, "sha": sha, "compareUrl": compare_url(url, base or None, branch)}


def cleanup(repo: Path, tree: Path, branch: str) -> None:
    """Remove the worktree and the branch. The commits stay until git prunes them."""
    if tree.exists():
        git(["worktree", "remove", "--force", str(tree)], repo, timeout=120)
    if tree.exists():
        shutil.rmtree(tree, ignore_errors=True)
    git(["worktree", "prune"], repo)
    git(["branch", "-D", branch], repo)


DIRTY = "Your working tree has changes that are not committed. Commit or stash them, then merge."


def dirty(repo: Path) -> bool:
    return bool(git(["status", "--porcelain"], repo).stdout.strip())


def merge_into_checkout(repo: Path, branch: str, message: str) -> dict[str, Any]:
    """Merge into whatever the repository has checked out. Refuses a dirty tree, undoes itself on a
    collision, and always hands back the command that undoes it."""
    if dirty(repo):
        raise Refused(DIRTY)
    into = git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    before = git(["rev-parse", "HEAD"], repo).stdout.strip()
    out = git([*AUTHOR, "merge", "--no-ff", "-m", message, branch], repo, timeout=300)
    if out.returncode != 0:
        files = [ln for ln in git(["diff", "--name-only", "--diff-filter=U"], repo).stdout.splitlines() if ln.strip()]
        git(["merge", "--abort"], repo)
        return {"merged": False, "into": into, "conflicts": files[:20], "commit": None, "undo": None}
    sha = git(["rev-parse", "HEAD"], repo).stdout.strip()
    return {"merged": True, "into": into, "conflicts": [], "commit": sha[:7],
            "undo": f"git reset --hard {before[:7]}", "before": before}


def undo_merge(repo: Path, before: str, commit: str) -> bool:
    """Take back a merge this runtime made a moment ago — only while the checkout still stands exactly
    on it, so nothing a person did since is ever thrown away. The tree was clean before the merge (it
    is refused otherwise), so going back to `before` loses nothing but the merge. False when the
    checkout moved on; the person then has the undo command, as always."""
    head = git(["rev-parse", "HEAD"], repo).stdout.strip()
    if not head.startswith(commit) or dirty(repo):
        return False
    return git(["reset", "--hard", before], repo).returncode == 0


def label_patch(patch: str, label: str, prefix: str = "") -> str:
    """One checkout's patch with its paths as the project names them: under the source's label, and
    relative to the checkout rather than its repository (`prefix`). Only the header lines of each file
    are rewritten — a removed line that happens to begin `-- a/` is content, and stays as it was."""
    if not label and not prefix:
        return patch
    lead = f"{label}/" if label else ""
    inner = f"{prefix}/" if prefix else ""

    def moved(path: str) -> str:
        quoted = path.startswith('"')
        bare = path[1:] if quoted else path
        side, _, rest = bare.partition("/")
        rest = rest[len(inner):] if inner and rest.startswith(inner) else rest
        out = f"{side}/{lead}{rest}"
        return f'"{out}' if quoted else out

    lines: list[str] = []
    header = False
    for line in patch.splitlines(keepends=True):
        body = line.rstrip("\n")
        end = line[len(body):]
        if body.startswith("diff --git "):
            header = True
            left, sep, right = body[len("diff --git "):].partition(" b/")
            line = f"diff --git {moved(left)}{sep and ' '}{moved('b/' + right) if sep else ''}{end}"
        elif header and body.startswith("@@"):
            header = False
        elif header and (body.startswith("--- a/") or body.startswith("+++ b/")
                         or body.startswith('--- "a/') or body.startswith('+++ "b/')):
            line = f"{body[:4]}{moved(body[4:])}{end}"
        elif header:
            for word in ("rename from ", "rename to ", "copy from ", "copy to "):
                if body.startswith(word):
                    path = body[len(word):]
                    rest = path[len(inner):] if inner and path.startswith(inner) else path
                    line = f"{word}{lead}{rest}{end}"
                    break
        lines.append(line)
    return "".join(lines)


#: Patterns the offline reviewer looks for when no model can read the diff.
SECRETS = re.compile(r"(sk-[A-Za-z0-9]{10,}|password\s*=\s*['\"][^'\"]{3,}|api[_-]?key\s*=\s*['\"][^'\"]{6,})", re.I)
LEFTOVERS = re.compile(r"\b(console\.log|debugger|print\()")
TODO = re.compile(r"\b(TODO|FIXME|XXX)\b")
