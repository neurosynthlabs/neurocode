"""Onboarding a repository: get the code onto this machine, measure it, and read it.

The measuring itself is unchanged — `app/onboarding.py` walks the tree and records only what can be
proven without a parser, and the code index adds symbols and the dependency graph. What this service
adds is the order of events and the honesty at each stage: every step lands in the activity log as it
happens, a failure sets the project to `paused` with the reason instead of leaving it half-made, and
the project record says plainly what was measured and what was not.

The work runs in the background with database sessions of its own, so a clone that takes a minute
never holds a request open.

A project can hold several sources — its web app, its API, its data repository — and each further one
goes through the same pipeline: cloned or read, then the whole project measured and indexed again, so
its files join the project's index under the source's label. Removing a source takes its rows out of
the index and never touches a file on disk.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..ai.gateway import Gateway
from ..data import roster
from ..data.base import utcnow
from ..data.engine import Database
from ..models import Chunk, CodeFile, Project, ProjectSource, Setting
from ..repositories import ActivityRepository, NotFound, ProjectRepository
from ..repositories.sources import MAX_SOURCES, ProjectSourceRepository
from .code import LABEL, Source, checkout, roots, source_root
from .errors import Refused
from .indexing import INDEXING, build_project_index
from .retrieval import RetrievalService


@dataclass(slots=True)
class Spec:
    """What the wizard asked for."""

    source: str
    repo: str
    branch: str = "main"
    excluded: list[str] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)


class OnboardingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def create(self, spec: Spec, by: str) -> Project:
        """Check the request, then write the project row. Nothing touches git or the disk yet."""
        problem = onboarding.problem(spec.source, spec.repo, spec.branch)
        if problem:
            raise Refused(problem, status=422)

        base = onboarding.slug(spec.repo)
        pid, n = base, 1
        while await self.projects.get(pid) is not None:
            n += 1
            pid = f"{base}-{n}"
        where = onboarding.redact(spec.repo.strip())

        project = await self.projects.add(Project(
            id=pid, name=onboarding.title(pid), codename=pid.upper(), kind="greenfield",
            status="onboarding", description=f"Onboarding from {where}.", repo=where,
            source_kind=spec.source, source_repo=where,
            source_branch=spec.branch.strip() if spec.source == "git" else "",
            rules=spec.rules, excluded=spec.excluded, last_active_at=utcnow()))
        await self.activity.record(actor=by, actor_kind="human", action="Onboarding started",
                                   detail=f"{project.name} · {where}", project_id=pid)
        return project


async def _say(db: Database, project_id: str, name: str, action: str, detail: str,
               level: str = "ok") -> None:
    async with db.session() as s:
        await ActivityRepository(s).record(actor=roster.ARCHITECT, actor_kind="agent", action=action,
                                           detail=f"{name} · {detail}", level=level,
                                           project_id=project_id)


#: One reading of a project at a time. Adding two sources at once, or one while the first is still being
#: onboarded, starts two jobs that would each replace the project's index — and the second insert of
#: the same file would fail the first. Keyed by the event loop too, because a lock belongs to the loop
#: it was first waited on in.
_READING: dict[tuple[int, str], asyncio.Lock] = {}


def reading(project_id: str) -> asyncio.Lock:
    return _READING.setdefault((id(asyncio.get_running_loop()), project_id), asyncio.Lock())


async def onboard(db: Database, gateway: Gateway, project_id: str, spec: Spec) -> None:
    """Clone or read, measure, index, and build retrieval. Every stage is reported as it finishes."""
    async with reading(project_id):
        await _onboard(db, gateway, project_id, spec)


async def _onboard(db: Database, gateway: Gateway, project_id: str, spec: Spec) -> None:
    async with db.read() as s:
        project = await ProjectRepository(s).get(project_id)
        if project is None:
            return
        name = project.name
    where = onboarding.redact(spec.repo)

    try:
        if spec.source == "git":
            root = onboarding.REPOS_DIR / project_id
            await _say(db, project_id, name, "Cloning", f"{where} @ {spec.branch}", "info")
            await asyncio.to_thread(onboarding.clone, spec.repo, spec.branch, root)
            await _say(db, project_id, name, "Repository cloned", f"shallow clone of {spec.branch}")
        else:
            root = Path(os.path.expanduser(spec.repo.strip()))
        found = await asyncio.to_thread(onboarding.scan, root, spec.excluded)
    except Exception as e:      # a refused clone, a vanished folder, a full disk: say why, and stop
        reason = onboarding.redact(str(e))[:200] or type(e).__name__
        async with db.session() as s:
            project = await ProjectRepository(s).get(project_id)
            if project is not None:
                project.status = "paused"
                project.description = f"Onboarding stopped: {reason}"
                project.last_active_at = utcnow()
        await _say(db, project_id, name, "Onboarding failed", reason, "err")
        return

    measured = onboarding.measured(found)
    async with db.session() as s:
        project = await ProjectRepository(s).get(project_id)
        if project is None:
            return
        project.stack, project.languages = measured["stack"], measured["languages"]
        project.files_count, project.lines_count = found["files"], found["lines"]
        project.modules, project.db_tables = found["modules"], found["dbTables"]
        project.stored_procs = found["storedProcs"]
        project.kind, project.status = measured["kind"], "active"
        project.coverage, project.description = measured["coverage"], measured["description"]
        project.last_active_at = utcnow()

    langs = " · ".join(f"{lang['name']} {lang['pct']}%" for lang in found["languages"][:4])
    await _say(db, project_id, name, "Stack detected", langs or "no source files recognised")
    await _say(db, project_id, name, "Tree mapped",
               f"{found['files']:,} files · {onboarding.fmt_lines(found['lines'])} lines · "
               f"{found['modules']} modules")
    if found["dbTables"] or found["storedProcs"]:
        await _say(db, project_id, name, "SQL objects found",
                   f"{found['dbTables']} tables · {found['storedProcs']} procedures", "info")

    async with db.read() as s:
        project = await ProjectRepository(s).get(project_id)
        checkouts = await roots(s, project) if project is not None else []
    if any(not x.primary and x.ready for x in checkouts):
        # Sources added while the first one was still being read: the project is measured and indexed
        # with all of them, rather than with the first alone and again straight after.
        await refresh(db, gateway, project_id)
        return
    if not any(x.primary for x in checkouts):
        checkouts = [Source(label=project_id, root=root, kind=spec.source, primary=True), *checkouts]
    try:
        async with db.session() as s:
            idx = await build_project_index(s, project_id, checkouts, spec.excluded)
    except Exception as e:      # a parser must never undo onboarding: the measured project stays
        await _say(db, project_id, name, "Indexing failed",
                   onboarding.redact(str(e))[:200] or type(e).__name__, "err")
        return
    await _say(db, project_id, name, "Code indexed", idx.describe())

    async with db.session() as s:
        project = await ProjectRepository(s).get(project_id)
        if project is not None:
            full = onboarding.measured(found, coverage=idx.coverage(), index=idx.stats())
            project.coverage, project.description = full["coverage"], full["description"]

    try:
        async with db.session() as s:
            built = await RetrievalService(s, gateway).build(project_id)
    except Exception as e:      # retrieval is an improvement on the index, never a condition of it
        await _say(db, project_id, name, "Retrieval failed",
                   onboarding.redact(str(e))[:160] or type(e).__name__, "warn")
        return
    await _say(db, project_id, name, "Retrieval ready",
               f"{built['chunks']} chunks · "
               f"{'meaning and words' if built['semantic'] else 'words only, no embedding lane'}")
    await _say(db, project_id, name, "Onboarding paused",
               "business rules and test mapping are not connected yet", "warn")


# ── further sources ──────────────────────────────────────────────
@dataclass(slots=True)
class SourceSpec:
    """What a person asked for when adding a folder or a repository to a project."""

    label: str
    kind: str
    repo: str
    branch: str = "main"
    #: `code` is worked on; `reference` is read for search and grounding and never written to.
    role: str = "code"


#: What a source's role is called on screen and in the activity log.
ROLE_WORDS = {"code": "code, worked on", "reference": "a reference, read only"}


def _same_or_inside(a: Path, b: Path) -> bool:
    """True when one folder is the other, or holds it — two sources that would read the same files."""
    real_a, real_b = os.path.realpath(a), os.path.realpath(b)
    return real_a == real_b or real_a.startswith(real_b + os.sep) or real_b.startswith(real_a + os.sep)


class SourceService:
    """Adding, renaming, reordering and removing a project's further sources. The slow part — cloning,
    measuring, indexing — is the `onboard_source` and `refresh` jobs below; this only checks and writes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.sources = ProjectSourceRepository(session)
        self.activity = ActivityRepository(session)

    async def project(self, project_id: str) -> Project:
        found = await self.projects.get(project_id)
        if found is None:
            raise NotFound(f"project {project_id}")
        return found

    async def source(self, project_id: str, source_id: int) -> ProjectSource:
        found = await self.sources.in_project(project_id, source_id)
        if found is None:
            raise NotFound(f"source {source_id} of project {project_id}")
        return found

    async def _check_label(self, project: Project, label: str, *, keep: int | None = None) -> str:
        """A label is a folder name inside the project, so it must read as one and be free there."""
        label = label.strip()
        if not LABEL.match(label):
            raise Refused("A label is lower-case letters, digits, dots, dashes or underscores, starting with a "
                          "letter or digit — it is the folder this source's files appear under.", status=422)
        if label == project.id:
            raise Refused(f"{label} is the project's own name for its first source. Choose another label.",
                          status=409)
        taken = await self.sources.labelled(project.id, label)
        if taken is not None and taken.id != keep:
            raise Refused(f"{project.name} already has a source called {label}.", status=409)
        first = checkout(project)
        # The first source's paths carry no label, so a folder of that name at its top would be two
        # things at once: `api/x.py` could be its own file or the new source's.
        if first is not None and await asyncio.to_thread(os.path.lexists, first / label):
            raise Refused(f"The first source already has a folder or file called {label} at its top, so its "
                          "files and this source's would share one name. Choose another label.", status=409)
        return label

    async def add(self, project_id: str, spec: SourceSpec, by: str) -> ProjectSource:
        """Check the request and write the row. Nothing touches git or the disk until the job runs."""
        project = await self.project(project_id)
        if not project.source_kind:
            raise Refused(f"{project.name} has no code on this machine yet. Onboard its first repository, "
                          "then add more beside it.")
        label = await self._check_label(project, spec.label)
        if await self.sources.count(ProjectSource.project_id == project_id) >= MAX_SOURCES:
            raise Refused(f"{project.name} already holds {MAX_SOURCES} sources, the most one project may.")
        problem = onboarding.problem(spec.kind, spec.repo, spec.branch)
        if problem:
            raise Refused(problem, status=422)
        if spec.role not in ROLE_WORDS:
            raise Refused("A source is either code, worked on, or a reference, read only.", status=422)
        where = onboarding.redact(spec.repo.strip())
        if spec.kind == "local":
            folder = Path(os.path.expanduser(spec.repo.strip()))
            for other in await roots(self.session, project):
                if other.kind == "local" and await asyncio.to_thread(_same_or_inside, folder, other.root):
                    name = "the first source" if other.primary else other.label
                    raise Refused(f"{where} is {name}'s folder, or inside it or around it — its files would be "
                                  "read twice.", status=409)

        source = await self.sources.add(ProjectSource(
            project_id=project_id, label=label, kind=spec.kind, repo=where,
            branch=spec.branch.strip() if spec.kind == "git" else "", status="onboarding",
            position=await self.sources.next_position(project_id), role=spec.role))
        project.last_active_at = utcnow()
        kind = " · a reference, read only" if spec.role == "reference" else ""
        await self.activity.record(actor=by, actor_kind="human", action="Source added",
                                   detail=f"{project.name} · {label} · {where}{kind}", project_id=project_id)
        return source

    async def update(self, project_id: str, source_id: int, by: str, *, label: str | None = None,
                     position: int | None = None, role: str | None = None) -> tuple[ProjectSource, bool]:
        """Rename, move or change the role of a source. Returns it, and whether its files must be indexed
        again under the new label — the index names every file by the label it had when it was read.

        A role changes nothing on disk and nothing in the index: a reference is indexed exactly as code
        is. It changes what agents may do there — the runtime opens no worktree on a reference, and the
        compiler reports a target inside one as not writable — from the next plan on."""
        project = await self.project(project_id)
        source = await self.source(project_id, source_id)
        renamed = False
        said: list[str] = []
        if label is not None and label.strip() != source.label:
            if source.status == "onboarding" or project_id in INDEXING:
                raise Refused(f"{source.label} is being read right now. Rename it once that finishes.")
            new = await self._check_label(project, label, keep=source.id)
            said.append(f"renamed {source.label} → {new}")
            await self._forget(project_id, source.label)
            source.label, renamed = new, True
        if role is not None and role != source.role:
            if role not in ROLE_WORDS:
                raise Refused("A source is either code, worked on, or a reference, read only.", status=422)
            said.append(f"{source.label} is now {ROLE_WORDS[role]}")
            source.role = role
        if position is not None:
            ordered = [x for x in await self.sources.of(project_id) if x.id != source.id]
            at = max(0, min(position, len(ordered)))
            ordered.insert(at, source)
            for n, row in enumerate(ordered, 1):
                row.position = n
            said.append(f"moved to place {at + 2}")        # the first source is always place 1
        if said:
            project.last_active_at = utcnow()
            await self.session.flush()
            await self.activity.record(actor=by, actor_kind="human", action="Source changed",
                                       detail=f"{project.name} · {' · '.join(said)}", project_id=project_id)
        return source, renamed

    async def _forget(self, project_id: str, label: str) -> None:
        """Take a source's files out of the project's index and retrieval, and forget the answers a person
        gave about running its commands. Files on disk are untouched.

        The answers are keyed by the source's label (`runtime.checks.<project>.<label> tests`), so a
        different repository added later under the same label would otherwise inherit an "allowed" and
        run its own command without anyone being asked."""
        prefix = f"{label}/"
        await self.session.execute(delete(CodeFile).where(
            CodeFile.project_id == project_id, CodeFile.path.startswith(prefix, autoescape=True)))
        await self.session.execute(delete(Chunk).where(
            Chunk.project_id == project_id, Chunk.path.startswith(prefix, autoescape=True)))
        await self.session.execute(delete(Setting).where(
            Setting.key.startswith(f"runtime.checks.{project_id}.{label} ", autoescape=True)))

    async def remove(self, project_id: str, source_id: int, by: str) -> ProjectSource:
        """Remove a source from the project: its row and its rows in the index go; its folder — and a clone
        of it — stay exactly where they are."""
        project = await self.project(project_id)
        source = await self.source(project_id, source_id)
        if source.status == "onboarding":
            raise Refused(f"{source.label} is still being onboarded. Remove it once that finishes.")
        await self._forget(project_id, source.label)
        where = source_root(source)
        await self.session.delete(source)
        project.last_active_at = utcnow()
        await self.session.flush()
        kept = f" · its files stay at {onboarding.redact(str(where))}" if where is not None else ""
        await self.activity.record(actor=by, actor_kind="human", action="Source removed",
                                   detail=f"{project.name} · {source.label}{kept}", level="warn",
                                   project_id=project_id)
        return source

    async def reindex(self, project_id: str, source_id: int, by: str) -> tuple[ProjectSource, bool]:
        """Read a source again. One that failed is onboarded again from the start (cloned afresh); one
        that is active is read where it is. Returns it, and whether it starts over."""
        project = await self.project(project_id)
        source = await self.source(project_id, source_id)
        if source.status == "onboarding" or project_id in INDEXING:
            raise Refused(f"{project.name} is being read right now.")
        again = source.status == "failed"
        if again:
            source.status, source.note = "onboarding", ""
        await self.activity.record(actor=by, actor_kind="human", action="Re-indexing",
                                   detail=f"{project.name} · {source.label}"
                                          + (" · onboarding it again" if again else " · reading it again"),
                                   project_id=project_id)
        return source, again


async def _touch(s: AsyncSession, project_id: str) -> None:
    """A source's state is shown on its project's card, so the project is written too — which is what
    sends the card to every open tab."""
    project = await ProjectRepository(s).get(project_id)
    if project is not None:
        project.last_active_at = utcnow()


async def onboard_source(db: Database, gateway: Gateway, project_id: str, source_id: int) -> None:
    """Clone or check one further source, then measure and index the whole project again with it in.
    A failure is written on the source — with the reason — and never takes the project down with it."""
    async with db.read() as s:
        source = await ProjectSourceRepository(s).in_project(project_id, source_id)
        project = await ProjectRepository(s).get(project_id)
        if source is None or project is None:
            return
        name, label, kind, repo, branch = project.name, source.label, source.kind, source.repo, source.branch
        where = source_root(source)

    try:
        if where is None:
            raise RuntimeError("it names no folder")
        if kind == "git":
            await _say(db, project_id, name, "Cloning", f"{label} · {repo} @ {branch}", "info")
            await asyncio.to_thread(onboarding.clone, repo, branch, where)
        elif not await asyncio.to_thread(where.is_dir):
            raise RuntimeError(f"{repo} is not a folder on this machine")
    except Exception as e:      # a refused clone or a vanished folder: said on the source, and stop
        reason = onboarding.redact(str(e))[:200] or type(e).__name__
        async with db.session() as s:
            source = await ProjectSourceRepository(s).in_project(project_id, source_id)
            if source is not None:
                source.status, source.note = "failed", reason
            await _touch(s, project_id)
        await _say(db, project_id, name, "Source failed", f"{label} · {reason}", "err")
        return

    async with db.session() as s:
        source = await ProjectSourceRepository(s).in_project(project_id, source_id)
        if source is None:
            return
        source.status, source.note = "active", ""
        await _touch(s, project_id)
    await _say(db, project_id, name, "Source ready", f"{label} · {'cloned' if kind == 'git' else 'read in place'}")
    async with reading(project_id):
        await refresh(db, gateway, project_id)


async def refresh(db: Database, gateway: Gateway, project_id: str) -> None:
    """Measure every source of the project, index them as one, and rebuild retrieval over them — what
    onboarding does for the first source, done again for all of them together."""
    async with db.read() as s:
        project = await ProjectRepository(s).get(project_id)
        if project is None:
            return
        name, excluded = project.name, list(project.excluded or [])
        sources = [x for x in await roots(s, project) if x.ready]

    def measure() -> dict[str, Any] | None:
        parts = [(x.prefix, onboarding.scan(x.root, excluded)) for x in sources if x.root.is_dir()]
        return onboarding.combine(parts) if parts else None

    found = await asyncio.to_thread(measure)
    if found is None:
        await _say(db, project_id, name, "Indexing failed", "none of its checkouts is on this machine", "err")
        return
    try:
        async with db.session() as s:
            project = await ProjectRepository(s).get(project_id)
            if project is None:
                return
            measured = onboarding.measured(found)
            project.stack, project.languages = measured["stack"], measured["languages"]
            project.files_count, project.lines_count = found["files"], found["lines"]
            project.modules, project.db_tables = found["modules"], found["dbTables"]
            project.stored_procs = found["storedProcs"]
            project.last_active_at = utcnow()
            await s.flush()
            idx = await build_project_index(s, project_id, await roots(s, project), excluded)
            full = onboarding.measured(found, coverage=idx.coverage(), index=idx.stats())
            project.coverage, project.description = full["coverage"], full["description"]
    except Exception as e:      # the previous index stays; the reason is in the feed
        await _say(db, project_id, name, "Indexing failed", onboarding.redact(str(e))[:200] or type(e).__name__,
                   "err")
        return
    await _say(db, project_id, name, "Code indexed", f"{len(sources)} sources · {idx.describe()}")
    try:
        async with db.session() as s:
            built = await RetrievalService(s, gateway).build(project_id)
    except Exception as e:      # retrieval is an improvement on the index, never a condition of it
        await _say(db, project_id, name, "Retrieval failed", onboarding.redact(str(e))[:160] or type(e).__name__,
                   "warn")
        return
    await _say(db, project_id, name, "Retrieval ready",
               f"{built['chunks']} chunks · "
               f"{'meaning and words' if built['semantic'] else 'words only, no embedding lane'}")


async def reread(db: Database, gateway: Gateway, project_id: str) -> None:
    """`refresh`, one reading of the project at a time — for a rename, a removal or a re-index."""
    async with reading(project_id):
        await refresh(db, gateway, project_id)
