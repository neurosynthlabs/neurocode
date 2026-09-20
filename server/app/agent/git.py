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
import urllib.error
import urllib.parse
import urllib.request
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


def branch_exists(repo: Path, branch: str) -> bool:
    return git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo).returncode == 0


def reopen_worktree(repo: Path, branch: str, path: Path) -> None:
    """Put a run's worktree back where it was, on the branch it already has.

    `open_worktree` cannot be asked to do this. It removes the path first and passes `-b`, which would
    throw away whatever the interrupted run had written and then fail because the branch already
    exists. Carrying on from a step means keeping both, so this one adds a worktree for an existing
    branch and refuses to touch a path that is still there — a directory that exists is either the
    worktree itself, in which case there is nothing to reopen, or somebody else's, in which case
    deleting it is not ours to do.
    """
    if path.exists():
        raise Refused(f"{path} is already there, so it was not opened again.")
    if not branch_exists(repo, branch):
        raise Refused(f"{branch} no longer exists, so there is no worktree to open on it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # A worktree whose directory was deleted is still registered, and git refuses to add it twice.
    git(["worktree", "prune"], repo)
    out = git(["worktree", "add", str(path), branch], repo, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"git worktree: {out.stderr.strip()[:200]}")


def commits_since(tree: Path, sha: str, until: str = "HEAD") -> list[tuple[str, str]]:
    """The commits `until` holds beyond `sha`, oldest first, as (sha, whole message).

    The message is the whole thing, not the subject, because what a resume asks of it is whether the
    run's own trailer is in it. Empty also when `until` is not a descendant of `sha` at all, which is
    what makes "the worktree moved somewhere else" a case the caller can tell apart and refuse.
    """
    out = git(["log", "--format=%H%x1f%B%x1e", f"{sha}..{until}"], tree, timeout=60)
    if out.returncode != 0:
        return []
    found: list[tuple[str, str]] = []
    for record in out.stdout.split("\x1e"):
        if "\x1f" not in record:
            continue
        commit, _, message = record.strip().partition("\x1f")
        found.append((commit.strip(), message.strip()))
    return list(reversed(found))


def keep_partial(tree: Path, ref: str, message: str) -> str:
    """Commit whatever is loose in a worktree onto a ref of our own, and hand back the commit.

    A step killed between writing its files and committing them leaves work nobody has accounted for.
    Throwing it away would be the runtime losing something a person might want; committing it onto the
    run's own branch would change the diff a person signs and the patch the review was fingerprinted
    against. So it goes somewhere that is neither: a ref under `refs/neurocode/partial/`, which no
    branch and no merge ever reads, and which `git show` will print for anyone who wants to look.

    Returns '' when there was nothing loose to keep.
    """
    if not re.fullmatch(r"refs/neurocode/partial/[A-Za-z0-9._/-]+", ref):
        raise Refused(f"{ref!r} is not a ref this runtime would write.")
    git(["add", "-A"], tree)
    tree_sha = git(["write-tree"], tree).stdout.strip()
    if not tree_sha:
        raise RuntimeError("git write-tree said nothing")
    parent = head(tree)
    if git(["diff", "--quiet", "--cached", parent], tree).returncode == 0:
        git(["reset", "--quiet", parent], tree)          # leave the index as we found it
        return ""
    made = git([*AUTHOR, "commit-tree", tree_sha, "-p", parent, "-m", message], tree)
    if made.returncode != 0:
        raise RuntimeError(f"git commit-tree: {made.stderr.strip()[:200]}")
    sha = made.stdout.strip()
    kept = git(["update-ref", ref, sha], tree)
    if kept.returncode != 0:
        raise RuntimeError(f"git update-ref: {kept.stderr.strip()[:200]}")
    git(["reset", "--quiet", parent], tree)
    return sha


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


def head(tree: Path) -> str:
    """The commit a worktree stands on, or '' when git cannot say."""
    out = git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], tree)
    return out.stdout.strip() if out.returncode == 0 else ""


def reset_worktree(tree: Path, branch: str, sha: str) -> None:
    """Take a run's worktree back to one of its own commits: `git reset --hard`, then `git clean -fd`.

    Only ever a worktree the runtime made, standing on the run's own branch — never a checkout. A tree
    whose git directory is the repository's own (the main checkout), or that stands on another branch,
    is refused, because a reset there would throw away a person's work. Files git ignores are left
    alone: they are build output, not the agent's."""
    if not re.fullmatch(r"[0-9a-f]{7,64}", sha):
        raise Refused(f"{sha!r} is not a commit this runtime would reset to.")
    own = git(["rev-parse", "--path-format=absolute", "--git-dir"], tree).stdout.strip()
    common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], tree).stdout.strip()
    if not own or own == common:
        raise Refused(f"{tree} is not a worktree NeuroCode made, so it is not reset.")
    on = git(["rev-parse", "--abbrev-ref", "HEAD"], tree).stdout.strip()
    if on != branch:
        raise Refused(f"{tree} stands on {on or 'no branch'}, not on the run's {branch}, so it is not reset.")
    if git(["merge-base", "--is-ancestor", sha, "HEAD"], tree).returncode != 0:
        raise Refused(f"{sha[:7]} is not a commit of {branch}, so the worktree was not moved there.")
    out = git(["reset", "--hard", "--quiet", sha], tree, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"git reset: {out.stderr.strip()[:200]}")
    git(["clean", "-fdq"], tree, timeout=120)


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


def remote_url(repo: Path, remote: str) -> str:
    """The URL a remote points at, as the person configured it. Empty when there is no such remote."""
    return git(["remote", "get-url", "--", remote], repo).stdout.strip()


def default_branch(repo: Path, remote: str) -> str:
    """The branch a pull request would go into: what the remote's own HEAD points at, and failing that
    whatever this checkout has out. Empty when neither answers, in which case the forge is left to
    choose its own default rather than being told a branch nobody measured."""
    base = git(["symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"], repo).stdout.strip()
    base = base.removeprefix(f"{remote}/") if base else ""
    if base:
        return base
    checked_out = git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    return "" if checked_out in ("", "HEAD") else checked_out


def forge_of(remote_url: str) -> tuple[str, str] | None:
    """The forge a remote belongs to and the project's path on it — ("github.com", "acme/shop") — or
    None for anything else, a path on disk included. Credentials written into a remote URL are dropped
    here, so nothing downstream can carry them into a link or an API call."""
    found = _REMOTE.match(remote_url.strip())
    if not found:
        return None
    host = (found.group("h1") or found.group("h2") or "").lower()
    path = found.group("path").strip("/")
    if host not in FORGES or path.count("/") < 1:
        return None
    return host, path


def compare_url(remote_url: str, base: str | None, branch: str) -> str | None:
    """The page on GitHub, GitLab or Bitbucket where a person opens a pull request for `branch` under
    their own account — or None for any other remote, a path on disk included."""
    where = forge_of(remote_url)
    if where is None:
        return None
    host, raw = where
    safe = "/-_.~"
    path = quote(raw, safe=safe)
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
    moved is refused by git, not overwritten. Returns {remote, branch, sha, baseBranch, compareUrl}."""
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
    base = default_branch(repo, remote)
    # The base branch is kept, not only folded into the link: opening the pull request later has to ask
    # the forge for the same branch the compare link named, without measuring the repository again.
    return {"remote": remote, "branch": branch, "sha": sha, "baseBranch": base,
            "compareUrl": compare_url(url, base or None, branch)}


# ── opening the pull request from here ───────────────────────────
FORGE_TIMEOUT = 120
#: How long a question about credentials may take before it is taken as "no".
FORGE_ASK_TIMEOUT = 20
MAX_PR_TITLE, MAX_PR_BODY = 200, 60_000

#: What can speak for each forge, in the order this product trusts it. The person's own command line
#: tool comes first: they already signed it in, so nothing new is kept here and nothing new can leak.
#: A token in the keys file is the fallback. Bitbucket is in FORGES because it has a compare page; it
#: has neither of these here, so that link stays the whole of what NeuroCode can do for it.
FORGE_TOOL = {"github.com": "gh", "gitlab.com": "glab"}
#: What each forge calls the thing, in the words its own screens use.
FORGE_NOUN = {"github.com": "pull request", "gitlab.com": "merge request", "bitbucket.org": "pull request"}


def forge_noun(host: str) -> str:
    return FORGE_NOUN.get(host, "pull request")


def _forge_env() -> dict[str, str]:
    """No prompt, no pager, no colour and no browser. Everything here is a question asked by a worker
    with nobody sitting in front of it: a tool that waits for an answer has to fail instead."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat",
            "GLAB_PAGER": "cat", "PAGER": "cat", "NO_COLOR": "1", "CLICOLOR": "0", "BROWSER": "true"}


def _forge_run(argv: list[str], cwd: Path, *, stdin: str = "",
               timeout: int = FORGE_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Always with a stdin of its own, even an empty one: a tool that decides to ask a question gets
    end-of-file and fails, rather than inheriting a terminal nobody is sitting at and waiting for ever."""
    return subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, text=True,
                          timeout=timeout, env=_forge_env())


def tool_ready(tool: str, host: str) -> bool:
    """Is the person's own forge CLI installed here and signed in to this host? `auth status` asks
    nothing and exits non-zero when it is not, so this is a question and never a prompt."""
    if not shutil.which(tool):
        return False
    try:
        out = subprocess.run([tool, "auth", "status", "--hostname", host], capture_output=True,
                             text=True, timeout=FORGE_ASK_TIMEOUT, env=_forge_env())
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def forge_reach(host: str, token: str | None = None) -> str | None:
    """How this machine can reach a forge right now: the person's own `gh` or `glab` when it is signed
    in, else "token" when they stored one, else None — which is not a failure, only a fact the screen
    and the refusal both say out loud."""
    tool = FORGE_TOOL.get(host)
    if tool and tool_ready(tool, host):
        return tool
    return "token" if token else None


def no_way_to_open(host: str, link: str | None = None) -> str:
    """Why nothing here can open it, in words a person can act on — and the link, which is what they
    had before this existed and still have now."""
    noun = forge_noun(host)
    keep = f" The compare link still opens one in your browser: {link}" if link else ""
    tool = FORGE_TOOL.get(host)
    if tool is None:
        return (f"NeuroCode cannot open a {noun} on {host} from here: it drives `gh` for GitHub and "
                f"`glab` for GitLab, and knows no API for {host}.{keep}")
    return (f"Nothing here can open a {noun} on {host}: `{tool}` is either not installed on this machine "
            f"or not signed in to {host}, and no {host} token is stored. Install it and run "
            f"`{tool} auth login`, or keep a token here with `nc forges --set-token {host}`.{keep}")


def _first_line(text: str, cap: int = 300) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")[:cap]


def _tool_refusal(tool: str, host: str, out: subprocess.CompletedProcess[str], doing: str) -> str:
    said = f"{out.stderr or ''}\n{out.stdout or ''}"
    low, first = said.lower(), _first_line(out.stderr or out.stdout or "")
    if "auth" in low and any(k in low for k in ("login", "token", "credential", "unauthorized", "401")):
        return (f"{host} refused `{tool}`'s credentials, so {doing} did not happen. Sign in again with "
                f"`{tool} auth login`. It said: {first}")
    if "404" in low or "not found" in low or "does not exist" in low:
        return (f"{host} says that project is not there, or your account cannot see it, so {doing} did "
                f"not happen. It said: {first}")
    if "403" in low or "permission" in low or "forbidden" in low:
        return (f"{host} did not let your account do that, so {doing} did not happen. It said: {first}")
    return f"`{tool}` could not manage {doing} on {host}: {first or 'it said nothing at all'}"


def _forge_http(method: str, url: str, headers: dict[str, str],
                payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    """The one place a token talks to a forge. Every call goes through here, so a test replaces this and
    nothing else — no test of this product has ever reached a real forge, and none can."""
    body = json.dumps(payload).encode() if payload is not None else None
    ask = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": "NeuroCode", "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if body else {}),
                                          **headers})
    try:
        with urllib.request.urlopen(ask, timeout=FORGE_TIMEOUT) as answer:      # noqa: S310 — https, built here
            return answer.status, json.loads(answer.read().decode() or "null")
    except urllib.error.HTTPError as bad:
        raw = bad.read().decode(errors="replace")
        try:
            return bad.code, json.loads(raw or "null")
        except ValueError:
            return bad.code, raw
    except (urllib.error.URLError, OSError, TimeoutError) as unreachable:
        raise Refused(f"{urllib.parse.urlsplit(url).netloc} could not be reached: {unreachable}") from unreachable


def _api_refusal(host: str, status: int, body: Any, doing: str) -> str:
    said = body.get("message") or body.get("error") or "" if isinstance(body, dict) else str(body or "")
    said = _first_line(str(said)) or f"HTTP {status}"
    if status in (401, 403):
        return (f"{host} refused the stored token, so {doing} did not happen. Store a token with rights "
                f"over that project in Settings → Forges. It said: {said}")
    if status == 404:
        return (f"{host} says that project is not there, or the stored token cannot see it, so {doing} "
                f"did not happen. It said: {said}")
    return f"{host} would not manage {doing} (HTTP {status}): {said}"


def _state(raw: str, merged: bool = False) -> str:
    """One word for the state, whichever forge said it: GitHub says OPEN/CLOSED/MERGED, GitLab says
    opened/merged/closed/locked. Anything else stays "unknown" rather than being guessed at."""
    said = (raw or "").strip().lower()
    if merged or said == "merged":
        return "merged"
    if said in ("open", "opened", "locked"):
        return "open"
    if said == "closed":
        return "closed"
    return "unknown"


def _gh_json(repo: Path, path: str, ref: str) -> dict[str, Any]:
    out = _forge_run(["gh", "pr", "view", ref, "--repo", path, "--json",
                      "number,url,state,isDraft,title"], repo)
    if out.returncode != 0:
        raise Refused(_tool_refusal("gh", "github.com", out, "reading the pull request back"))
    try:
        got = json.loads(out.stdout or "{}")
    except ValueError as unreadable:
        raise Refused(f"`gh` answered with something that is not JSON: {_first_line(out.stdout)}") from unreadable
    return {"number": int(got.get("number") or 0), "url": str(got.get("url") or ""),
            "state": _state(str(got.get("state") or "")), "draft": bool(got.get("isDraft")),
            "title": str(got.get("title") or "")}


def _glab_json(repo: Path, path: str, ref: str) -> dict[str, Any]:
    out = _forge_run(["glab", "mr", "view", ref, "--repo", path, "-F", "json"], repo)
    if out.returncode != 0:
        raise Refused(_tool_refusal("glab", "gitlab.com", out, "reading the merge request back"))
    try:
        got = json.loads(out.stdout or "{}")
    except ValueError as unreadable:
        raise Refused(f"`glab` answered with something that is not JSON: {_first_line(out.stdout)}") from unreadable
    return {"number": int(got.get("iid") or got.get("number") or 0),
            "url": str(got.get("web_url") or got.get("url") or ""),
            "state": _state(str(got.get("state") or "")), "draft": bool(got.get("draft") or got.get("work_in_progress")),
            "title": str(got.get("title") or "")}


def _gh_api(path: str, token: str, method: str = "GET", tail: str = "",
            payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    return _forge_http(method, f"https://api.github.com/repos/{path}/pulls{tail}",
                       {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}, payload)


def _glab_api(path: str, token: str, method: str = "GET", tail: str = "",
              payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    project = quote(path, safe="")
    return _forge_http(method, f"https://gitlab.com/api/v4/projects/{project}/merge_requests{tail}",
                       {"PRIVATE-TOKEN": token}, payload)


def _gh_doc(got: dict[str, Any]) -> dict[str, Any]:
    return {"number": int(got.get("number") or 0), "url": str(got.get("html_url") or ""),
            "state": _state(str(got.get("state") or ""), merged=bool(got.get("merged") or got.get("merged_at"))),
            "draft": bool(got.get("draft")), "title": str(got.get("title") or "")}


def _glab_doc(got: dict[str, Any]) -> dict[str, Any]:
    return {"number": int(got.get("iid") or 0), "url": str(got.get("web_url") or ""),
            "state": _state(str(got.get("state") or "")),
            "draft": bool(got.get("draft") or got.get("work_in_progress")),
            "title": str(got.get("title") or "")}


def open_pull_request(repo: Path, *, host: str, path: str, branch: str, base: str, title: str, body: str,
                      draft: bool, how: str, token: str | None = None) -> dict[str, Any]:
    """Open the pull or merge request for a branch that is already on the forge, and answer with what
    came back: its number, its URL, its state and whether it is a draft.

    Opening one twice is not an error anywhere here. Every forge refuses a second request for the same
    branch, and each refusal is read for exactly that and turned into a read of the one that exists —
    so a person who presses the button again gets the request they already have, not a failure.
    """
    title, body = title[:MAX_PR_TITLE], body[:MAX_PR_BODY]
    if how == "gh":
        argv = ["gh", "pr", "create", "--repo", path, "--head", branch, "--title", title, "--body-file", "-"]
        if base:
            argv += ["--base", base]
        if draft:
            argv.append("--draft")
        out = _forge_run(argv, repo, stdin=body)
        if out.returncode != 0 and "already exists" not in (out.stderr + out.stdout).lower():
            raise Refused(_tool_refusal("gh", host, out, "opening the pull request"))
        return {**_gh_json(repo, path, branch), "via": "gh"}
    if how == "glab":
        argv = ["glab", "mr", "create", "--repo", path, "--source-branch", branch, "--title", title,
                "--description", body, "--yes"]
        if base:
            argv += ["--target-branch", base]
        if draft:
            argv.append("--draft")
        out = _forge_run(argv, repo)
        if out.returncode != 0 and "already exists" not in (out.stderr + out.stdout).lower():
            raise Refused(_tool_refusal("glab", host, out, "opening the merge request"))
        return {**_glab_json(repo, path, branch), "via": "glab"}
    if how == "token" and token and host == "github.com":
        owner = path.split("/", 1)[0]
        # `base` is left out rather than sent empty when the remote named no default: the forge then
        # picks its own, which is a measured fact of the forge, not a guess made here.
        status, got = _gh_api(path, token, "POST", payload={"title": title, "head": branch, "body": body,
                                                            "draft": draft, **({"base": base} if base else {})})
        if status == 422 and "already exist" in json.dumps(got).lower():
            status, got = _gh_api(path, token, tail=f"?state=open&head={quote(owner, safe='')}:{quote(branch, safe='')}")
            got = got[0] if isinstance(got, list) and got else None
            if not got:
                raise Refused(f"{host} says a pull request for {branch} already exists, but would not show it.")
            return {**_gh_doc(got), "via": "token"}
        if status not in (200, 201):
            raise Refused(_api_refusal(host, status, got, "opening the pull request"))
        return {**_gh_doc(got if isinstance(got, dict) else {}), "via": "token"}
    if how == "token" and token and host == "gitlab.com":
        # GitLab has no `draft` field to set: a draft is a title that starts with "Draft:", which is
        # what its own web form writes too.
        shown = f"Draft: {title}"[:MAX_PR_TITLE] if draft else title
        status, got = _glab_api(path, token, "POST", payload={"source_branch": branch, "title": shown,
                                                              "description": body,
                                                              **({"target_branch": base} if base else {})})
        if status == 409 or (status == 400 and "already exists" in json.dumps(got).lower()):
            status, got = _glab_api(path, token, tail=f"?state=opened&source_branch={quote(branch, safe='')}")
            got = got[0] if isinstance(got, list) and got else None
            if not got:
                raise Refused(f"{host} says a merge request for {branch} already exists, but would not show it.")
            return {**_glab_doc(got), "via": "token"}
        if status not in (200, 201):
            raise Refused(_api_refusal(host, status, got, "opening the merge request"))
        return {**_glab_doc(got if isinstance(got, dict) else {}), "via": "token"}
    raise Refused(no_way_to_open(host))


def pull_request_state(repo: Path, *, host: str, path: str, number: int, how: str,
                       token: str | None = None) -> dict[str, Any]:
    """Read a request that is already open back from the forge, so the run screen can say "open",
    "merged" or "closed" without anybody going to look."""
    if how == "gh":
        return {**_gh_json(repo, path, str(number)), "via": "gh"}
    if how == "glab":
        return {**_glab_json(repo, path, str(number)), "via": "glab"}
    if how == "token" and token and host == "github.com":
        status, got = _gh_api(path, token, tail=f"/{number}")
        if status != 200 or not isinstance(got, dict):
            raise Refused(_api_refusal(host, status, got, "reading the pull request back"))
        return {**_gh_doc(got), "via": "token"}
    if how == "token" and token and host == "gitlab.com":
        status, got = _glab_api(path, token, tail=f"/{number}")
        if status != 200 or not isinstance(got, dict):
            raise Refused(_api_refusal(host, status, got, "reading the merge request back"))
        return {**_glab_doc(got), "via": "token"}
    raise Refused(no_way_to_open(host))


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


# ── the checkout a person works in: the Workbench's own changes ──
#: How many paths one commit or discard may name. A person picks these in a panel, file by file.
MAX_CHANGE_PATHS = 100
#: A commit message is one message, not a pasted file.
MAX_MESSAGE = 500
NO_IDENTITY = ("Git on this machine has no name and address to commit with. Set them once — "
               "`git config --global user.name \"Your Name\"` and "
               "`git config --global user.email \"you@example.com\"` — then commit again.")


def _counted(numstat: str) -> tuple[int, int]:
    """The first numstat record's two numbers. A binary file's are `-`, which counts as nothing added."""
    parts = numstat.split("\n", 1)[0].split("\t")
    if len(parts) < 2:
        return 0, 0
    return (int(parts[0]) if parts[0].isdigit() else 0), (int(parts[1]) if parts[1].isdigit() else 0)


def _in_head(repo: Path, path: str) -> bool:
    return git(["cat-file", "-e", f"HEAD:{path}"], repo).returncode == 0


def file_diff(repo: Path, rel: str) -> dict[str, Any]:
    """What one file in the checkout has that its last commit does not — git's own answer, not a guess.

    A file git has never seen is diffed against nothing at all (`--no-index` from /dev/null), which is
    how git itself shows a new file, so the panel can show it exactly like any other change instead of
    saying it has none.
    """
    path = str(safe_path(rel))
    tracked = git(["ls-files", "--error-unmatch", "--", path], repo).returncode == 0
    if not tracked and not (repo / path).exists():
        raise Refused(f"{rel} is not in this repository and not on disk, so there is nothing to compare.")
    if tracked:
        change = (git(["diff", "--name-status", "HEAD", "--", path], repo).stdout.split("\t", 1)[0] or "")[:1]
        numstat = git(["diff", "--numstat", "HEAD", "--", path], repo).stdout
        patch = git(["diff", "--no-color", "--no-ext-diff", "HEAD", "--", path], repo, timeout=60).stdout
    else:
        change = "?"
        numstat = git(["diff", "--numstat", "--no-index", "--", os.devnull, path], repo).stdout
        patch = git(["diff", "--no-color", "--no-ext-diff", "--no-index", "--", os.devnull, path],
                    repo, timeout=60).stdout
    additions, deletions = _counted(numstat)
    return {"path": rel, "change": change, "tracked": tracked, "additions": additions, "deletions": deletions,
            "patch": patch[:MAX_DIFF], "truncated": len(patch) > MAX_DIFF}


def identity(repo: Path) -> tuple[str, str]:
    """Who a commit made from here is by: the git identity this machine is configured with.

    Never NeuroCode's own (`AUTHOR`) — that address is how the Git screen tells an agent's commits
    from a person's, and signing a person's own edit with it would make that reading a lie.
    """
    name = git(["config", "--get", "user.name"], repo).stdout.strip()
    email = git(["config", "--get", "user.email"], repo).stdout.strip()
    if not name or not email:
        raise Refused(NO_IDENTITY)
    return name, email


def commit_paths(repo: Path, paths: list[str], message: str) -> dict[str, Any]:
    """Commit exactly these paths in the checkout, and nothing else that happens to be changed.

    Signing is switched off for this one call: a commit that wants a passphrase would hold the request
    open until it timed out, and a person who signs their commits can make this one in a terminal.
    """
    wanted = [str(safe_path(p)) for p in paths[:MAX_CHANGE_PATHS]]
    if not wanted:
        raise Refused("Name at least one file to commit.")
    text = message.strip()[:MAX_MESSAGE]
    if not text:
        raise Refused("A commit needs a message saying what changed.")
    name, email = identity(repo)
    added = git(["add", "-A", "--", *wanted], repo, timeout=120)
    if added.returncode != 0:
        raise Refused(f"Git could not stage those files: {added.stderr.strip()[:200]}")
    staged = [line for line in git(["diff", "--cached", "--name-only", "--", *wanted],
                                   repo).stdout.splitlines() if line.strip()]
    if not staged:
        raise Refused("Those files hold nothing that is not already committed.")
    out = git(["-c", "commit.gpgsign=false", "commit", "-m", text, "--", *wanted], repo, timeout=120)
    if out.returncode != 0:
        raise Refused(f"Git did not commit: {(out.stderr or out.stdout).strip()[:200]}")
    sha = git(["rev-parse", "HEAD"], repo).stdout.strip()
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    return {"commit": sha[:7], "sha": sha, "branch": branch, "files": len(staged), "message": text,
            "by": f"{name} <{email}>"}


def discard_paths(repo: Path, paths: list[str]) -> dict[str, Any]:
    """Take these paths back to the commit the checkout stands on.

    A file that is not in that commit is refused by name rather than deleted: there is no earlier copy
    of it to go back to, and throwing away the only copy is not undoing anything.
    """
    wanted = [str(safe_path(p)) for p in paths[:MAX_CHANGE_PATHS]]
    if not wanted:
        raise Refused("Name at least one file to discard.")
    new = [p for p in wanted if not _in_head(repo, p)]
    if new:
        named = ", ".join(new[:5]) + (f" and {len(new) - 5} more" if len(new) > 5 else "")
        raise Refused(f"{named} is not in the last commit, so there is no earlier version to go back to. "
                      f"Delete it yourself if that is what you meant.")
    out = git(["checkout", "HEAD", "--", *wanted], repo, timeout=120)
    if out.returncode != 0:
        raise Refused(f"Git did not take those files back: {(out.stderr or out.stdout).strip()[:200]}")
    return {"discarded": wanted}


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
