"""An archive on this machine — a zip lying on the Desktop, a tarball in Downloads — made into a project.

An archive is somebody else's idea of where files go, so nothing in one is trusted. `survey` reads every
entry's header before a single byte is written, and refuses the whole archive — never a part of it —
when any entry would:

* **leave the folder** (zip slip): a name with `..`, an absolute name, a drive letter;
* **be a link**, a device or a pipe: a symlink written now is a door out of the folder later;
* **be too many or too large**: more than `MAX_ENTRIES` entries, more than `MAX_TOTAL` bytes once
  unpacked, or — the archive bomb — far more unpacked than packed (`MAX_RATIO`) once it is large;
* **be there twice**: the second would silently replace the first.

`extract` then writes into a folder that did not exist a moment ago (the caller made it, and a folder
that already exists is refused, never merged), creating every file exclusively and counting the bytes
it really writes, so an entry that says it is small and is not stops there. A single folder at the top
of the archive — `shop-main/` from a GitHub download — is dropped, so the project's files sit at the
project's root. Finder's `__MACOSX/` shadow folder is left out: it holds resource forks, not the project.

The two are blocking and pure (no database); `import_archive` is the job that runs them and then hands
the folder to the ordinary onboarding, which measures and indexes it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

from .. import onboarding
from ..ai.gateway import Gateway
from ..data import roster
from ..data.base import utcnow
from ..data.engine import Database
from ..repositories import ActivityRepository, ProjectRepository
from .errors import Refused

log = logging.getLogger(__name__)

#: What an archive may be called, and so read as.
SUFFIXES = (".zip", ".tar.gz", ".tgz")
#: The most entries one archive may hold, and the most bytes it may unpack to.
MAX_ENTRIES = 50_000
MAX_TOTAL = 2 * 1024 ** 3
#: Unpacked over packed. Source code compresses five to ten times; a thousand times is a bomb. Judged
#: only past RATIO_FLOOR unpacked, because a small file of spaces compresses absurdly and harms nobody.
MAX_RATIO = 200
RATIO_FLOOR = 64 * 1024 ** 2
#: How much is copied at a time.
CHUNK = 1024 * 1024
#: Finder's shadow folder in a zip made on a Mac: resource forks and `._` files, never the project.
SHADOW = "__MACOSX"


def kind_of(name: str) -> str | None:
    """"zip", "tar" or None — from the name, the only thing a person chose."""
    lower = name.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith((".tar.gz", ".tgz")):
        return "tar"
    return None


def stem_of(name: str) -> str:
    """The archive's name without its suffix: `shop-main.tar.gz` → `shop-main`."""
    base = PurePosixPath(name.replace("\\", "/")).name
    for suffix in SUFFIXES:
        if base.lower().endswith(suffix):
            return base[: -len(suffix)]
    return base


def _size(n: int) -> str:
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


@dataclass(frozen=True, slots=True)
class Entry:
    #: The entry's name as it will be written: relative, '/'-separated, checked — before the top folder is dropped.
    name: str
    #: The name in the archive, to read it back by.
    source: str
    dir: bool
    size: int
    executable: bool = False


@dataclass(slots=True)
class Survey:
    """What an archive holds, every entry checked. Nothing has been written."""

    kind: str
    archive_bytes: int
    entries: list[Entry] = field(default_factory=list)
    total: int = 0
    #: The single top folder that will be dropped, when there is one.
    top: str | None = None

    @property
    def files(self) -> int:
        return sum(1 for e in self.entries if not e.dir)

    @property
    def dirs(self) -> int:
        return sum(1 for e in self.entries if e.dir)

    def placed(self, entry: Entry) -> str:
        """Where an entry goes inside the project folder, with the top folder dropped."""
        if self.top is None:
            return entry.name
        return entry.name[len(self.top) + 1:] if entry.name != self.top else ""

    def brief(self) -> dict[str, Any]:
        return {"kind": self.kind, "bytes": self.archive_bytes, "entries": len(self.entries), "files": self.files,
                "folders": self.dirs, "total": self.total, "top": self.top,
                "sample": [self.placed(e) + ("/" if e.dir else "") for e in self.entries if self.placed(e)][:12]}


def clean_name(raw: str) -> str | None:
    """An entry's name as a relative path, or a refusal when it would leave the folder. None for a name
    that is only the folder itself (`./`)."""
    if "\0" in raw:
        raise Refused(f"An entry's name holds a NUL byte ({raw[:60]!r}); the archive is refused.", status=422)
    text = raw.replace("\\", "/")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":" and text[0].isalpha()):
        raise Refused(f"{raw} is an absolute path inside the archive — it would be written outside the "
                      "project's folder. The archive is refused.", status=422)
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise Refused(f"{raw} climbs out of the folder with '..' — the archive is refused, nothing was "
                      "written.", status=422)
    return "/".join(parts) or None


def _zip_entries(path: Path) -> Iterator[tuple[str, str, bool, int, int]]:
    """(raw name, kind, is dir, size, mode bits) of every entry, from the central directory alone."""
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            mode = info.external_attr >> 16
            # The type bits, when the tool that made the zip wrote any (Unix tools do; Windows ones leave
            # them empty, and then the name's trailing '/' is all there is).
            kind_bits = stat.S_IFMT(mode)
            if kind_bits == stat.S_IFLNK:
                kind = "link"
            elif info.is_dir() or kind_bits == stat.S_IFDIR:
                kind = "dir"
            elif kind_bits not in (0, stat.S_IFREG):
                kind = "special"
            else:
                kind = "file"
            yield info.filename, kind, kind == "dir", info.file_size, mode


def _tar_entries(path: Path) -> Iterator[tuple[str, str, bool, int, int]]:
    with tarfile.open(path, "r:gz") as tf:
        for member in tf:
            if member.issym() or member.islnk():
                kind = "link"
            elif member.isdir():
                kind = "dir"
            elif member.isreg():
                kind = "file"
            else:
                kind = "special"
            yield member.name, kind, kind == "dir", member.size if member.isreg() else 0, member.mode


def survey(path: Path, kind: str) -> Survey:
    """Read every entry's header and check it; refuse the whole archive at the first one that fails.

    Blocking. A tarball has no index, so its headers are read by walking it — decompressing as it goes —
    which is why the counts and the unpacked total are checked as each entry arrives, not at the end:
    a bomb stops at the ceiling, not after it has been read to its last byte.
    """
    try:
        packed = path.stat().st_size
    except PermissionError:
        raise
    except OSError as failed:
        raise Refused(f"{path.name} could not be read: {failed.strerror or failed}.", status=409) from failed
    found = Survey(kind=kind, archive_bytes=packed)
    ceiling = max(RATIO_FLOOR, MAX_RATIO * max(packed, 1))
    seen: set[str] = set()
    entries = _zip_entries(path) if kind == "zip" else _tar_entries(path)
    try:
        for raw, what, is_dir, size, mode in entries:
            name = clean_name(raw)
            if name is None or name.split("/", 1)[0] == SHADOW or name.rsplit("/", 1)[-1] == ".DS_Store":
                continue
            if what == "link":
                raise Refused(f"{raw} is a link. Links in an archive can point outside the project's folder, "
                              "so the archive is refused.", status=422)
            if what == "special":
                raise Refused(f"{raw} is a device, a pipe or another special file; the archive is refused.",
                              status=422)
            if name in seen:
                raise Refused(f"{name} is in the archive twice; the second would replace the first, so the "
                              "archive is refused.", status=422)
            seen.add(name)
            found.entries.append(Entry(name=name, source=raw, dir=is_dir, size=size,
                                       executable=bool(mode & 0o111) and not is_dir))
            found.total += size
            if len(found.entries) > MAX_ENTRIES:
                raise Refused(f"{path.name} holds more than {MAX_ENTRIES:,} entries, the most one import may "
                              "unpack.", status=413)
            if found.total > MAX_TOTAL:
                raise Refused(f"{path.name} unpacks to more than {_size(MAX_TOTAL)}, the most one import may "
                              "write.", status=413)
            if found.total > ceiling:
                raise Refused(f"{path.name} is {_size(packed)} and unpacks to more than {_size(found.total)} — "
                              f"over {MAX_RATIO} times its size. That is how an archive bomb looks, so it is "
                              "refused.", status=422)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError) as broken:
        if isinstance(broken, PermissionError):
            raise
        raise Refused(f"{path.name} is not a readable {'zip' if kind == 'zip' else 'tar.gz'} archive: "
                      f"{broken}.", status=422) from broken
    if not any(not e.dir for e in found.entries):
        raise Refused(f"{path.name} holds no files.", status=422)
    found.top = _single_top(found.entries)
    return found


def _single_top(entries: list[Entry]) -> str | None:
    """The one folder every entry sits in, when there is exactly one and something inside it."""
    tops = {e.name.split("/", 1)[0] for e in entries}
    if len(tops) != 1:
        return None
    top = next(iter(tops))
    inside = [e for e in entries if e.name != top]
    if not inside or any(e.name == top and not e.dir for e in entries):
        return None
    return top


@dataclass(frozen=True, slots=True)
class Extracted:
    files: int
    folders: int
    bytes: int
    top: str | None


def _members(path: Path, found: Survey) -> Iterator[tuple[Entry, Callable[[], IO[bytes]] | None]]:
    """Each entry to write, in the archive's own order, with a way to open its bytes (None for a folder).

    A tarball is read once, front to back: asking it for a member by name would decompress it from the
    start again for every file."""
    wanted = {e.source: e for e in found.entries if found.placed(e)}
    if found.kind == "zip":
        with zipfile.ZipFile(path) as zf:
            for entry in wanted.values():
                yield entry, (None if entry.dir else (lambda e=entry: zf.open(e.source)))
        return
    with tarfile.open(path, "r:gz") as tf:
        for member in tf:
            entry = wanted.get(member.name)
            if entry is None:
                continue
            if entry.dir:
                yield entry, None
                continue
            got = tf.extractfile(member)
            if got is None:
                raise Refused(f"{member.name} could not be read from the archive.", status=422)
            yield entry, (lambda g=got: g)


def extract(path: Path, target: Path, found: Survey,
            progress: Callable[[int, int], None] | None = None) -> Extracted:
    """Write the surveyed entries into `target`, a folder the caller has just made and that is empty.

    Every file is created with O_EXCL — so nothing already there is ever replaced — and its real path is
    checked against the folder's before it is opened. The bytes written are counted against the entry's
    own size and the import's ceiling, whatever the headers claimed. Blocking."""
    home = os.path.realpath(target)
    written = files = folders = 0
    wanted = sum(1 for e in found.entries if found.placed(e) and not e.dir)
    for entry, open_bytes in _members(path, found):
        rel = found.placed(entry)
        dest = Path(home, *rel.split("/"))
        parent = dest if entry.dir else dest.parent
        parent.mkdir(parents=True, exist_ok=True)
        real = os.path.realpath(parent)
        if real != home and not real.startswith(home + os.sep):
            raise Refused(f"{entry.source} would be written outside the project's folder.", status=422)
        if open_bytes is None:
            folders += 1
            continue
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                     0o755 if entry.executable else 0o644)
        with os.fdopen(fd, "wb") as out, open_bytes() as source:
            mine = 0
            while chunk := source.read(CHUNK):
                mine += len(chunk)
                written += len(chunk)
                if mine > entry.size or written > MAX_TOTAL:
                    raise Refused(f"{entry.source} holds more than its header says; the import stopped.",
                                  status=422)
                out.write(chunk)
        files += 1
        if progress is not None:
            progress(files, wanted)
    return Extracted(files=files, folders=folders, bytes=written, top=found.top)


# ── the job: extract, then onboard ───────────────────────────────
async def _say(db: Database, project_id: str, action: str, detail: str, level: str = "info") -> None:
    async with db.session() as s:
        await ActivityRepository(s).record(actor=roster.ARCHITECT, actor_kind="agent", action=action,
                                           detail=detail, level=level, project_id=project_id)


async def import_archive(db: Database, gateway: Gateway, project_id: str, archive: Path, target: Path,
                         found: Survey, spec: Any, *, uploaded: bool = False) -> None:
    """Unpack the archive into the project's new folder, then onboard the folder like any other.

    Progress lands in Activity as it goes — the entries to unpack, then each quarter done — so the open
    tabs follow it on the same feed the onboarding stages arrive on. A failure removes the folder this
    import made (it was empty before, and only this import wrote in it), pauses the project with the
    reason, and says so. An uploaded archive is deleted either way: it was only ever a copy."""
    from .onboarding import onboard

    async with db.read() as s:
        project = await ProjectRepository(s).get(project_id)
        if project is None:
            return
        name = project.name
    loop = asyncio.get_running_loop()
    marks = {max(1, (found.files * q) // 4) for q in (1, 2, 3)}

    def progress(n: int, of: int) -> None:
        if n in marks:
            asyncio.run_coroutine_threadsafe(
                _say(db, project_id, "Extracting", f"{name} · {n:,} of {of:,} files"), loop).result(timeout=30)

    try:
        await _say(db, project_id, "Extracting",
                   f"{name} · {found.files:,} files, {_size(found.total)}, into {target}"
                   + (f" · its top folder {found.top}/ dropped" if found.top else ""))
        done = await asyncio.to_thread(extract, archive, target, found, progress)
    except Exception as e:             # a lying header, a full disk, a folder that vanished: undo and say why
        reason = onboarding.redact(str(e))[:200] or type(e).__name__
        await asyncio.to_thread(shutil.rmtree, target, True)
        async with db.session() as s:
            project = await ProjectRepository(s).get(project_id)
            if project is not None:
                project.status = "paused"
                project.description = f"Import stopped: {reason}"
                project.last_active_at = utcnow()
        await _say(db, project_id, "Import failed", f"{name} · {reason}", "err")
        return
    finally:
        if uploaded:
            await asyncio.to_thread(_forget, archive)
    await _say(db, project_id, "Extracted", f"{name} · {done.files:,} files, {_size(done.bytes)}", "ok")
    await onboard(db, gateway, project_id, spec)


def _forget(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as failed:
        log.warning("the uploaded archive %s was not removed: %s", path, failed)
