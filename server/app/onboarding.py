"""Onboarding, the part that is real today: get the code onto this machine and measure it.

It clones a remote (or reads a local path), walks the tree and records what can be proven without a
parser: files, lines, languages, SQL objects and top-level modules. The code index (codeindex.py)
then adds symbols and the dependency graph. Business rules and test mapping are not connected yet,
and the project record says so instead of pretending.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPOS_DIR = Path(__file__).resolve().parent.parent / ".repos"
TOTAL_STEPS = 15      # the pipeline the wizard shows (src/mock/modules.ts → onboardingSteps)
MEASURED_STEPS = 2    # clone & detect the stack, map the repository tree
INDEXED_STEPS = 4     # and, once the code index has run: syntax & symbols, the dependency graph
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
}
GIT_URL = re.compile(r"^(?:(?:https?|ssh|git)://[^\s/@]+(?:@[^\s/]+)?/\S+|[\w.-]+@[\w.-]+:[\w./~-]+)$")
BRANCH = re.compile(r"^(?!-)(?!.*\.\.)[\w./-]{1,100}$")
USERINFO = re.compile(r"(://)[^/@\s]+@")
TABLE = re.compile(rb"\bcreate\s+table\b", re.I)
PROC = re.compile(rb"\bcreate\s+(?:or\s+alter\s+)?proc(?:edure)?\b", re.I)


def source_root(project: dict[str, Any]) -> Path | None:
    """Where an onboarded project's code lives on this machine. Sample projects have none."""
    src = project.get("source")
    if not src:
        return None
    return REPOS_DIR / project["id"] if src["kind"] == "git" else Path(os.path.expanduser(src["repo"]))


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


def step_count(connect_db: bool, mine_git: bool, ingest_docs: bool) -> int:
    return TOTAL_STEPS - (0 if connect_db else 4) - (0 if mine_git else 1) - (0 if ingest_docs else 1)


def clone(repo: str, branch: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # never prompt (a prompt would hang the job), never run the ext:: or file:: transports, and `--`
    # so a "URL" that starts with a dash can never be read as an option
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15"}
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
            "modules": len(modules), "truncated": files >= MAX_FILES}


def fmt_lines(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k" if n < 10_000 else f"{round(n / 1000)}k"
    return f"{n / 1_000_000:.1f}M"


def measured(found: dict[str, Any], steps_total: int, coverage: dict[str, int] | None = None,
             index: dict[str, Any] | None = None) -> dict[str, Any]:
    """The project fields a scan (and, when given, the code index) can honestly fill in."""
    names = [lang["name"] for lang in found["languages"]]
    legacy = any(n in names for n in ("VB.NET", "ASP.NET WebForms")) or (
        ("C#" in names or "T-SQL" in names) and found["lines"] > 50_000)
    capped = " (stopped at the file cap)" if found["truncated"] else ""
    cov = coverage or {}
    indexed = (f"{index['symbols']:,} symbols and {index['edges']:,} dependencies indexed; business rules and test "
               "mapping are not connected yet.") if index else "The code index has not run yet."
    return {
        "stack": names[:4], "languages": found["languages"], "files": found["files"], "lines": fmt_lines(found["lines"]),
        "modules": found["modules"], "dbTables": found["dbTables"], "storedProcs": found["storedProcs"],
        "kind": "legacy" if legacy else "greenfield", "status": "active", "lastActive": "just now",
        "understoodPct": round(100 * (INDEXED_STEPS if index else MEASURED_STEPS) / steps_total),
        "coverage": [{"label": "Files & languages", "pct": 100}, {"label": "Repository tree", "pct": 100},
                     {"label": "Syntax & symbols", "pct": cov.get("Syntax & symbols", 0)},
                     {"label": "Dependency graph", "pct": cov.get("Dependency graph", 0)},
                     {"label": "Database links", "pct": cov.get("Database links", 0)},
                     {"label": "Business rules", "pct": 0}],
        "description": f"{found['files']:,} files and {fmt_lines(found['lines'])} lines measured{capped}. {indexed}",
    }
