"""Onboarding a repository: get the code onto this machine, measure it, and read it.

The measuring itself is unchanged — `app/onboarding.py` walks the tree and records only what can be
proven without a parser, and the code index adds symbols and the dependency graph. What this service
adds is the order of events and the honesty at each stage: every step lands in the activity log as it
happens, a failure sets the project to `paused` with the reason instead of leaving it half-made, and
the project record says plainly what was measured and what was not.

The work runs in the background with database sessions of its own, so a clone that takes a minute
never holds a request open.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..ai.gateway import Gateway
from ..data import roster
from ..data.base import utcnow
from ..data.engine import Database
from ..models import Project
from ..repositories import ActivityRepository, ProjectRepository
from .errors import Refused
from .indexing import build_index
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


async def onboard(db: Database, gateway: Gateway, project_id: str, spec: Spec) -> None:
    """Clone or read, measure, index, and build retrieval. Every stage is reported as it finishes."""
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

    try:
        async with db.session() as s:
            idx = await build_index(s, project_id, root, spec.excluded)
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
