"""The machine the API runs on, as the Workbench sees it: its folders, its files, and what git says.

NeuroCode's code lives on the person's own machine, so the Workbench opens it where it is — the way an
editor does — rather than through a copy. That is a lot of reach, so three rules hold everywhere here:

* **Only inside the roots.** `settings.machine_roots` names the folders the browser may go into; every
  path a request names is resolved with `realpath` first (so `..`, `~` and symlinks are all followed to
  where they really lead) and refused unless the result is inside one of them. A symlink that points
  out of the roots is listed as a link and cannot be opened.
* **Only a person, only an Owner, only when the server says so.** The routes check `machine:access` and
  `settings.machine_access`; nothing a model says ever reaches these functions — agents keep writing
  only in their worktrees.
* **Nothing overwritten blind.** A save names the SHA-1 of the text the editor opened; if the file on
  disk no longer has it, the save is refused and the person decides. Every save, new file and new
  folder is in the audit log with who and where.

The filesystem calls are blocking, so the routes run them in a worker thread; a service instance is
only needed for the part that writes the audit log.
"""
from __future__ import annotations

import hashlib
import logging
import os
import stat
import subprocess
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.git import git
from ..repositories import AuditRepository
from ..schemas.machine import Change, Entry, FileList, GitState, Listing, Opened, Root, Saved
from ..settings import settings
from .errors import Refused
from .identity import Person

log = logging.getLogger(__name__)

#: What one folder listing holds. A folder with more (a cache, a dump of images) says it was capped.
MAX_ENTRIES = 2000
#: The editor opens text up to this size; beyond it a browser tab is the wrong tool.
MAX_TEXT = 2 * 1024 * 1024
#: Quick-open's list of a folder's files, and the changes git status reports, stop here.
MAX_FILES = 20000
MAX_CHANGES = 2000
#: How much of a file is read to decide it is binary: a NUL byte in it, as git decides.
SNIFF = 8192
GIT_TIMEOUT = 20

OFF = "Machine access is off on this server."

#: A hint for the status bar when the editor has no grammar of its own for a name. The editor picks
#: its highlighting from the file name itself (CodeMirror's language data), so this is only a label.
LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".ipynb": "Jupyter", ".js": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".jsx": "JSX", ".ts": "TypeScript", ".tsx": "TSX", ".json": "JSON",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".less": "Less", ".md": "Markdown",
    ".mdx": "MDX", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#", ".fs": "F#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".m": "Objective-C",
    ".r": "R", ".jl": "Julia", ".lua": "Lua", ".pl": "Perl", ".sh": "Shell", ".bash": "Shell",
    ".zsh": "Shell", ".fish": "Shell", ".ps1": "PowerShell", ".sql": "SQL", ".yaml": "YAML",
    ".yml": "YAML", ".toml": "TOML", ".ini": "INI", ".cfg": "INI", ".xml": "XML", ".svg": "SVG",
    ".vue": "Vue", ".svelte": "Svelte", ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hs": "Haskell", ".clj": "Clojure", ".elm": "Elm", ".zig": "Zig",
    ".proto": "Protocol Buffers", ".graphql": "GraphQL", ".tf": "Terraform", ".csv": "CSV",
    ".txt": "Plain text", ".dockerfile": "Dockerfile",
}
NAMES = {"Dockerfile": "Dockerfile", "Makefile": "Makefile", "CMakeLists.txt": "CMake", "Gemfile": "Ruby",
         "Rakefile": "Ruby", "Jenkinsfile": "Groovy", ".gitignore": "Ignore file", ".env": "Environment"}


# ── the roots and the one check every path goes through ───────────
def enabled() -> None:
    """A server that turned machine access off answers as if these routes did not exist."""
    if not settings().machine_access:
        raise Refused(OFF, status=404)


def _real_roots() -> list[Path]:
    found: list[Path] = []
    for part in settings().machine_roots.split(os.pathsep):
        text = part.strip()
        if not text:
            continue
        real = Path(os.path.realpath(os.path.expanduser(text)))
        if real.is_dir() and real not in found:
            found.append(real)
    return found


def roots() -> list[Root]:
    """The folders the browser may open, each once, those that do not exist left out."""
    home = Path(os.path.realpath(Path.home()))
    return [Root(path=str(r), label="Home" if r == home else (r.name or str(r))) for r in _real_roots()]


def _within(real: Path, bounds: list[Path]) -> bool:
    return any(real == b or real.is_relative_to(b) for b in bounds)


def inside(path: str) -> Path:
    """The real path a request names, or a refusal. The only door from a request to the filesystem.

    `realpath` runs before the comparison, so `..` segments, `~` and every symlink on the way are
    resolved to where they really lead: a link inside the roots that points out of them is refused
    exactly like the outside path itself. Relative paths are refused rather than guessed at — relative
    to what would depend on the process, not on anything the person chose.
    """
    text = (path or "").strip()
    if not text or "\0" in text:
        raise Refused("Name a file or folder.", status=400)
    expanded = Path(os.path.expanduser(text))
    if not expanded.is_absolute():
        raise Refused(f"{text} is not a full path. Start it with / or ~.", status=400)
    real = Path(os.path.realpath(expanded))
    bounds = _real_roots()
    if not _within(real, bounds):
        where = ", ".join(str(b) for b in bounds) or "no folder at all"
        raise Refused(f"{text} is outside the folders this server opens ({where}).", status=403)
    return real


def _fresh(path: str) -> Path:
    """Where a new file or folder would go: its folder must be inside the roots, and its name one plain
    name — never `.`, `..` or a path — so a new entry can only ever appear in the folder it names."""
    text = (path or "").strip().rstrip("/")
    if not text or "\0" in text:
        raise Refused("Name the new file or folder.", status=400)
    expanded = Path(os.path.expanduser(text))
    if not expanded.is_absolute():
        raise Refused(f"{text} is not a full path. Start it with / or ~.", status=400)
    name = expanded.name
    if name in ("", ".", "..") or "/" in name or (os.sep != "/" and os.sep in name):
        raise Refused(f"{name or text} is not a name a file or folder can have.", status=400)
    folder = inside(str(expanded.parent))
    if not folder.is_dir():
        raise Refused(f"{folder} is not a folder.", status=404)
    target = folder / name
    if os.path.lexists(target):
        raise Refused(f"{target} already exists.", status=409)
    return target


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def language(path: Path) -> str | None:
    return NAMES.get(path.name) or LANGUAGES.get(path.suffix.lower())


# ── reading ───────────────────────────────────────────────────────
def _entry(folder: Path, name: str, bounds: list[Path]) -> Entry | None:
    """One row of a listing. Anything the process cannot stat (it vanished, no permission) is left out."""
    full = folder / name
    try:
        st = os.lstat(full)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode):
        real = Path(os.path.realpath(full))
        link_to = None
        size = None
        if _within(real, bounds):
            try:
                target = os.stat(real)
                if stat.S_ISDIR(target.st_mode):
                    link_to = "dir"
                elif stat.S_ISREG(target.st_mode):
                    link_to, size = "file", target.st_size
            except OSError:
                pass
        return Entry(name=name, path=str(full), kind="link", size=size, modified=st.st_mtime, link_to=link_to)
    if stat.S_ISDIR(st.st_mode):
        return Entry(name=name, path=str(full), kind="dir", size=None, modified=st.st_mtime,
                     git=os.path.lexists(full / ".git"))
    return Entry(name=name, path=str(full), kind="file", size=st.st_size, modified=st.st_mtime)


def listing(path: str, *, hidden: bool = False) -> Listing:
    """A folder's entries, folders first, then by name as a person reads them (case folded).

    Names are sorted before anything is stat'ed, so a folder of a hundred thousand files costs one
    directory read and 2 000 stats, not a hundred thousand.
    """
    folder = inside(path)
    if not folder.exists():
        raise Refused(f"{path} does not exist.", status=404)
    if not folder.is_dir():
        raise Refused(f"{path} is a file, not a folder.", status=409)
    try:
        with os.scandir(folder) as found:
            names = [(not e.is_dir(follow_symlinks=True), e.name.casefold(), e.name) for e in found]
    except PermissionError as denied:
        raise Refused(f"This server's account may not read {folder}.", status=403) from denied
    except OSError as failed:
        raise Refused(f"{folder} could not be read: {failed.strerror or failed}.", status=409) from failed
    shown = [n for n in names if hidden or not n[2].startswith(".")]
    shown.sort()
    bounds = _real_roots()
    entries = tuple(e for e in (_entry(folder, name, bounds) for _, _, name in shown[:MAX_ENTRIES]) if e)
    parent = None if folder in bounds else (str(folder.parent) if _within(folder.parent, bounds) else None)
    return Listing(path=str(folder), parent=parent, entries=entries, capped=len(shown) > MAX_ENTRIES,
                   total=len(shown), hidden=len(names) - len(shown))


def _regular(path: str) -> tuple[Path, os.stat_result]:
    file = inside(path)
    try:
        st = os.stat(file)
    except FileNotFoundError as gone:
        raise Refused(f"{path} does not exist.", status=404) from gone
    except PermissionError as denied:
        raise Refused(f"This server's account may not read {file}.", status=403) from denied
    if not stat.S_ISREG(st.st_mode):
        raise Refused(f"{file} is not a file the editor can open.", status=409)
    return file, st


def _read(file: Path) -> bytes:
    try:
        return file.read_bytes()
    except PermissionError as denied:
        raise Refused(f"This server's account may not read {file}.", status=403) from denied


def read(path: str) -> Opened:
    """A file for the editor: its text when it is UTF-8 text, and in any case the SHA-1 of its bytes,
    which the save sends back to prove the file is still the one that was opened."""
    file, st = _regular(path)
    if st.st_size > MAX_TEXT:
        raise Refused(f"{file.name} is {_size(st.st_size)}; the editor opens files up to {_size(MAX_TEXT)}.",
                      status=413)
    data = _read(file)
    sha1 = hashlib.sha1(data).hexdigest()
    common = {"path": str(file), "size": len(data), "modified": st.st_mtime, "language": language(file),
              "sha1": sha1}
    if b"\0" in data[:SNIFF]:
        return Opened(binary=True, text=None, reason="binary", **common)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Opened(binary=False, text=None, reason="not UTF-8", **common)
    return Opened(binary=False, text=text, **common)


def write(path: str, text: str, expect_sha1: str) -> tuple[Saved, str]:
    """Save the editor's text over the file it opened — only if the file is still the one it opened.

    Written in place rather than to a temporary file renamed over it: a rename would give the file a
    new inode, which breaks hard links, drops extended attributes and ownership the process cannot
    restore, and fails in a folder the process may not write to although the file itself is writable.
    Editors that care about those (VS Code among them) write in place too. The check-then-write has a
    window of microseconds; what it guards against is the minutes a tab stays open.
    """
    file, _ = _regular(path)
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT:
        raise Refused(f"The text is {_size(len(data))}; the editor saves files up to {_size(MAX_TEXT)}.",
                      status=413)
    before = hashlib.sha1(_read(file)).hexdigest()
    if before != expect_sha1.strip().lower():
        raise Refused(f"{file.name} changed on disk since you opened it.", status=409)
    try:
        with open(file, "r+b") as out:
            out.write(data)
            out.truncate()
    except PermissionError as denied:
        raise Refused(f"This server's account may not write {file}.", status=403) from denied
    st = os.stat(file)
    return Saved(path=str(file), size=st.st_size, modified=st.st_mtime,
                 sha1=hashlib.sha1(data).hexdigest()), before


def make_folder(path: str) -> Entry:
    target = _fresh(path)
    try:
        os.mkdir(target)
    except PermissionError as denied:
        raise Refused(f"This server's account may not write in {target.parent}.", status=403) from denied
    made = _entry(target.parent, target.name, _real_roots())
    assert made is not None
    return made


def make_file(path: str) -> Entry:
    target = _fresh(path)
    try:
        # "x": created here and now, or refused — never truncating something that appeared meanwhile.
        with open(target, "x", encoding="utf-8"):
            pass
    except FileExistsError as there:
        raise Refused(f"{target} already exists.", status=409) from there
    except PermissionError as denied:
        raise Refused(f"This server's account may not write in {target.parent}.", status=403) from denied
    made = _entry(target.parent, target.name, _real_roots())
    assert made is not None
    return made


# ── git ───────────────────────────────────────────────────────────
def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """One read that never takes an optional lock, so it cannot make an agent's own git call fail."""
    try:
        return git(["-c", "core.quotePath=false", "--no-optional-locks", *args], cwd, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as failed:
        log.warning("git %s in %s did not answer: %s", args[:1], cwd, failed)
        return None


def _repository(folder: Path) -> Path | None:
    """The repository holding a folder, when there is one and it is itself inside the roots — a
    repository above the roots would list the names of files the browser may not open."""
    done = _git(["rev-parse", "--show-toplevel"], folder)
    if done is None or done.returncode != 0 or not done.stdout.strip():
        return None
    top = Path(os.path.realpath(done.stdout.strip()))
    return top if _within(top, _real_roots()) else None


def _folder_of(path: str) -> Path:
    real = inside(path)
    if not real.exists():
        raise Refused(f"{path} does not exist.", status=404)
    return real if real.is_dir() else real.parent


def _letter(xy: str) -> tuple[str, bool]:
    """git's two columns (index, worktree) as one letter a tree can colour, and whether it is staged."""
    x, y = (xy + "..")[:2]
    return (y if y != "." else x), x != "."


def status(path: str) -> GitState | None:
    """`git status --porcelain=v2 --branch` for the repository containing a path, or None outside one."""
    top = _repository(_folder_of(path))
    if top is None:
        return None
    done = _git(["status", "--porcelain=v2", "--branch", "-z"], top)
    if done is None or done.returncode != 0:
        return None
    branch = head = upstream = None
    ahead = behind = 0
    changed: list[Change] = []
    records = done.stdout.split("\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        if record.startswith("# "):
            key, _, value = record[2:].partition(" ")
            if key == "branch.head":
                branch = None if value == "(detached)" else value
            elif key == "branch.oid":
                head = None if value == "(initial)" else value
            elif key == "branch.upstream":
                upstream = value
            elif key == "branch.ab":
                parts = value.split()
                if len(parts) == 2:
                    ahead, behind = abs(int(parts[0])), abs(int(parts[1]))
            continue
        kind = record[0]
        if kind == "1":
            fields = record.split(" ", 8)
            if len(fields) == 9:
                letter, staged = _letter(fields[1])
                changed.append(Change(path=fields[8], status=letter, staged=staged))
        elif kind == "2":
            fields = record.split(" ", 9)
            was = records[i] if i < len(records) else None
            i += 1                                   # a rename's second record is where it came from
            if len(fields) == 10:
                letter, staged = _letter(fields[1])
                changed.append(Change(path=fields[9], status=letter, staged=staged, was=was))
        elif kind == "u":
            fields = record.split(" ", 10)
            if len(fields) == 11:
                changed.append(Change(path=fields[10], status="U", staged=False))
        elif kind == "?":
            changed.append(Change(path=record[2:], status="?", staged=False))
    return GitState(root=str(top), branch=branch, head=head, upstream=upstream, ahead=ahead, behind=behind,
                    changed=tuple(changed[:MAX_CHANGES]), capped=len(changed) > MAX_CHANGES)


def files(path: str) -> FileList:
    """Every file under a folder, for quick-open, relative to it.

    Inside a repository the list is git's own — tracked files and untracked ones not ignored — so
    `node_modules` and build output stay out exactly as the project's `.gitignore` says. Elsewhere the
    folder is walked, skipping hidden folders, which is where caches and tool state live.
    """
    folder = inside(path)
    if not folder.is_dir():
        raise Refused(f"{path} is not a folder.", status=409)
    if _repository(folder) is not None:
        done = _git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"], folder)
        if done is not None and done.returncode == 0:
            names = sorted({n for n in done.stdout.split("\0") if n})
            return FileList(root=str(folder), files=tuple(names[:MAX_FILES]), capped=len(names) > MAX_FILES,
                            source="git")
    found: list[str] = []
    capped = False
    for here, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel = os.path.relpath(here, folder)
        for name in sorted(names):
            if len(found) >= MAX_FILES:
                capped = True
                break
            found.append(name if rel == "." else f"{rel}/{name}")
        if capped:
            break
    return FileList(root=str(folder), files=tuple(found), capped=capped, source="walk")


# ── what is audited ───────────────────────────────────────────────
class MachineService:
    """The writes, each in the audit log: who, what, where. Reads leave no trace beyond the access log."""

    def __init__(self, session: AsyncSession) -> None:
        self.audit = AuditRepository(session)

    async def saved(self, who: Person, saved: Saved, before: str, *, ip: str = "") -> None:
        await self.audit.record(action="machine.file.save", user_id=who.id, target=saved.path,
                                detail={"bytes": saved.size, "sha1": saved.sha1, "was": before}, ip=ip)

    async def made(self, who: Person, entry: Entry, *, ip: str = "") -> None:
        await self.audit.record(action=f"machine.{'folder' if entry.kind == 'dir' else 'file'}.create",
                                user_id=who.id, target=entry.path, ip=ip)
