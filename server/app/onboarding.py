"""Onboarding, the part that is real today: get the code onto this machine and measure it.

It clones a remote (or reads a local path), walks the tree and records what can be proven without a
parser: files, lines, languages, SQL objects and top-level modules. The code index (codeindex.py)
then adds symbols and the dependency graph. Business rules and test mapping are not connected yet,
and the project record says so instead of pretending.
"""
from __future__ import annotations

import base64
import fnmatch
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .settings import settings

MAX_FILES = 60_000
MAX_BYTES = 2_000_000
SKIP_DIRS = {"node_modules", "bin", "obj", "dist", "build", "out", "target", "vendor", "packages", "coverage",
             "venv", "__pycache__", "site-packages"}
CONTAINERS = {"src", "lib", "app", "apps", "packages", "services", "modules", "source", "projects"}
LANGS = {
    ".cs": "C#", ".vb": "VB.NET", ".sql": "T-SQL", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".py": "Python", ".java": "Java", ".kt": "Kotlin", ".go": "Go",
    ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".dart": "Dart", ".cpp": "C++", ".cc": "C++",
    ".c": "C", ".h": "C", ".cshtml": "Razor", ".razor": "Razor", ".aspx": "ASP.NET WebForms", ".html": "HTML",
    ".css": "CSS", ".scss": "CSS", ".ps1": "PowerShell", ".sh": "Shell", ".yml": "YAML", ".yaml": "YAML",
    # The languages the code index reads with tree-sitter, so the scan counts what the index parses.
    ".cjs": "JavaScript", ".mts": "TypeScript", ".cts": "TypeScript", ".kts": "Kotlin", ".scala": "Scala",
    ".sc": "Scala", ".hpp": "C++", ".hh": "C++", ".hxx": "C++", ".cxx": "C++", ".lua": "Lua", ".r": "R", ".jl": "Julia",
    ".ex": "Elixir", ".exs": "Elixir", ".hs": "Haskell", ".ml": "OCaml", ".mli": "OCaml", ".zig": "Zig",
    ".bash": "Shell", ".vue": "Vue", ".svelte": "Svelte", ".ipynb": "Jupyter Notebook",
}
GIT_URL = re.compile(r"^(?:(?:https?|ssh|git)://[^\s/@]+(?:@[^\s/]+)?/\S+|[\w.-]+@[\w.-]+:[\w./~-]+)$")


def repos_dir() -> Path:
    """Where git projects are cloned: `settings().repos_dir`, so the folder DevOps shows is the one used,
    and tests and the e2e stack can clone somewhere of their own."""
    return settings().repos_dir


def __getattr__(name: str) -> Any:
    # `onboarding.REPOS_DIR` is read by the onboarding job and its tests. Answered from the settings on
    # every read, so it can never become a second, stale copy of the path.
    if name == "REPOS_DIR":
        return repos_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


BRANCH = re.compile(r"^(?!-)(?!.*\.\.)[\w./-]{1,100}$")
USERINFO = re.compile(r"(://)[^/@\s]+@")
TABLE = re.compile(rb"\bcreate\s+table\b", re.I)
PROC = re.compile(rb"\bcreate\s+(?:or\s+alter\s+)?proc(?:edure)?\b", re.I)


def source_root(project: dict[str, Any]) -> Path | None:
    """Where an onboarded project's code lives on this machine. A project with no source has none.

    A source with no kind, or a local one with no path, is no source either: `Path("")` is the current
    directory, so a project that was never onboarded used to answer with the API's own folder — and its
    test command with the API's own tests.
    """
    src = project.get("source")
    if not src or not src.get("kind"):
        return None
    if src["kind"] == "git":
        return repos_dir() / project["id"]
    return Path(os.path.expanduser(src["repo"])) if (src.get("repo") or "").strip() else None


def source_path(project_id: str, source_id: int, kind: str, repo: str) -> Path | None:
    """Where one of a project's further sources lives on this machine.

    A local one is the folder it names. A cloned one goes beside the project's own clone, under a name
    made from the source's id rather than its label: a label can be renamed, and a rename must never
    leave the clone behind under the old name. `+` cannot appear in a project id, so the folder can
    never be mistaken for another project's clone.
    """
    if kind == "git":
        return repos_dir() / f"{project_id}+{source_id}"
    return Path(os.path.expanduser(repo)) if (repo or "").strip() else None


def redact(text: str) -> str:
    """Strip credentials from every URL in the text: https://user:token@host → https://***@host."""
    return USERINFO.sub(r"\1***@", text)


def problem(source: str, repo: str, branch: str) -> str | None:
    """What is wrong with the request, or None. Checked before anything touches git or the disk."""
    repo = repo.strip()
    if source == "local":
        path = Path(os.path.expanduser(repo))
        if not path.is_absolute():
            return "Use an absolute path, starting with / or ~/"
        return None if path.is_dir() else f"{repo} is not a folder on this machine"
    if not GIT_URL.match(repo):
        return "Not a clone URL, e.g. git@github.com:org/repo.git"
    return None if BRANCH.match(branch.strip()) else "The branch name is empty or invalid"


def slug(repo: str) -> str:
    tail = re.split(r"[/:\\]", repo.strip().rstrip("/"))[-1]
    tail = re.sub(r"\.git$", "", tail, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")[:40] or "project"


def title(project_slug: str) -> str:
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in project_slug.split("-") if w)


#: The user name each forge takes beside a token over HTTPS.
TOKEN_USER = {"github.com": "x-access-token", "gitlab.com": "oauth2"}


def host_of(repo: str) -> str:
    """`https://github.com/org/x.git` and `git@github.com:org/x.git` → `github.com`; "" when there is none."""
    found = re.match(r"^(?:[a-z+]+://)?(?:[^@/\s]+@)?([\w.-]+)[:/]", repo.strip(), re.I)
    return found.group(1).lower() if found else ""


def clone(repo: str, branch: str, dest: Path, token: str | None = None) -> None:
    """A shallow clone of one branch. `token` is the person's own for that forge, for a private repository:
    it rides in an HTTP header set through git's environment, so it is never in the URL, the clone's
    .git/config, the process list or a log line."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # never prompt (a prompt would hang the job), never run the ext:: or file:: transports, and `--`
    # so a "URL" that starts with a dash can never be read as an option
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15"}
    host = host_of(repo)
    if token and repo.strip().lower().startswith("https://") and host in TOKEN_USER:
        basic = base64.b64encode(f"{TOKEN_USER[host]}:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=f"http.https://{host}/.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    result = subprocess.run(
        ["git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never", "clone", "--depth", "1",
         "--single-branch", "--branch", branch, "--", repo, str(dest)],
        capture_output=True, text=True, timeout=240, env=env,
    )
    if result.returncode != 0:
        lines = [ln for ln in result.stderr.strip().splitlines() if ln.strip()]
        raise RuntimeError(redact(lines[-1] if lines else "git clone failed")[:300])


def _excluded(rel: str, name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p.removeprefix("**/")) for p in patterns)


def walk(root: Path, excluded: list[str]) -> Iterator[tuple[str, str, bytes]]:
    """Every source file under root that is not excluded, as (path relative to root with forward
    slashes, language, contents). Dependency and build folders are never entered."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                       and not _excluded(os.path.normpath(os.path.join(rel_dir, d)), d, excluded)]
        for name in filenames:
            lang = LANGS.get(os.path.splitext(name)[1].lower())
            rel = os.path.normpath(os.path.join(rel_dir, name))
            if not lang or _excluded(rel, name, excluded):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            yield rel.replace(os.sep, "/"), lang, data
            count += 1
            if count >= MAX_FILES:
                return


def module_of(rel: str) -> str:
    """The top-level module a file belongs to: src/billing/x.cs → src/billing, api/x.py → api."""
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0].lower() in CONTAINERS:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if len(parts) >= 2 else "(root)"


def combine(parts: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Several scans as one project: the counts added up, the languages weighed by their lines across
    all of them, and a further source's modules named under its label, so `api/billing` and the web
    app's `billing` stay two modules. `parts` are (prefix, scan) with the first source's prefix empty."""
    if len(parts) == 1 and not parts[0][0]:
        return parts[0][1]
    by_lang: dict[str, int] = {}
    files = lines = tables = procs = modules = 0
    truncated = False
    for _prefix, found in parts:
        files += found["files"]
        lines += found["lines"]
        tables += found["dbTables"]
        procs += found["storedProcs"]
        modules += found["modules"]
        truncated = truncated or found["truncated"]
        for lang, n in (found.get("byLang") or {}).items():
            by_lang[lang] = by_lang.get(lang, 0) + n
    ranked = sorted(by_lang.items(), key=lambda kv: -kv[1])
    languages = [{"name": lang, "pct": round(100 * n / lines)} for lang, n in ranked if lines and n / lines >= 0.02][:6]
    return {"files": files, "lines": lines, "languages": languages, "dbTables": tables, "storedProcs": procs,
            "modules": modules, "truncated": truncated, "byLang": by_lang}


def scan(root: Path, excluded: list[str]) -> dict[str, Any]:
    by_lang: dict[str, int] = {}
    modules: set[str] = set()
    files = lines = tables = procs = 0
    for rel, lang, data in walk(root, excluded):
        n = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        files += 1
        lines += n
        by_lang[lang] = by_lang.get(lang, 0) + n
        if rel.lower().endswith(".sql"):
            tables += len(TABLE.findall(data))
            procs += len(PROC.findall(data))
        if "/" in rel:
            modules.add(module_of(rel))
    ranked = sorted(by_lang.items(), key=lambda kv: -kv[1])
    languages = [{"name": lang, "pct": round(100 * n / lines)} for lang, n in ranked if lines and n / lines >= 0.02][:6]
    return {"files": files, "lines": lines, "languages": languages, "dbTables": tables, "storedProcs": procs,
            "modules": len(modules), "truncated": files >= MAX_FILES, "byLang": by_lang}


def fmt_lines(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k" if n < 10_000 else f"{round(n / 1000)}k"
    return f"{n / 1_000_000:.1f}M"


def measured(found: dict[str, Any], coverage: dict[str, int] | None = None,
             index: dict[str, Any] | None = None) -> dict[str, Any]:
    """The project fields a scan (and, when given, the code index) can honestly fill in.

    Coverage is only what the index measured. Rows for what a scan cannot fail at — every file it read
    was read — and for what nothing reads yet, business rules, used to sit beside them at a fixed 100
    and a fixed 0, drawn exactly like the numbers that were measured.
    """
    names = [lang["name"] for lang in found["languages"]]
    legacy = any(n in names for n in ("VB.NET", "ASP.NET WebForms")) or (
        ("C#" in names or "T-SQL" in names) and found["lines"] > 50_000)
    capped = " (stopped at the file cap)" if found["truncated"] else ""
    indexed = (f"{index['symbols']:,} symbols and {index['edges']:,} dependencies indexed; business rules and test "
               "mapping are not connected yet.") if index else "The code index has not run yet."
    return {
        "stack": names[:4], "languages": found["languages"], "files": found["files"], "lines": fmt_lines(found["lines"]),
        "modules": found["modules"], "dbTables": found["dbTables"], "storedProcs": found["storedProcs"],
        "kind": "legacy" if legacy else "greenfield", "status": "active",
        "coverage": [{"label": label, "pct": pct} for label, pct in (coverage or {}).items()],
        "description": f"{found['files']:,} files and {fmt_lines(found['lines'])} lines measured{capped}. {indexed}",
    }
