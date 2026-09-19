"""A project reading another project: its code, documents and memory handed to models as context.

The shop's web app references the payments service it calls; a rewrite references the old system it
replaces. What this adds is read-only reach, never a second place to write: retrieval and grounding
search a referenced project's pieces beside the project's own (labelled "<name> · reference", and
bounded so the project's own code still leads), a session reads its files through a `<project id>:`
prefix, and no agent ever writes there — the runtime and the compiler hold referenced files to the same
`writable()` rule as a reference source.

Adding, changing and removing a reference are project changes: each one is in the activity log and the
audit log, and touches the project row, so every open tab's project card learns of it.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import Project, ProjectReference
from ..repositories import ActivityRepository, AuditRepository, NotFound, ProjectRepository
from ..repositories.references import MAX_REFERENCES, READ_AT_MOST, ProjectReferenceRepository
from ..schemas.work import when
from .errors import Refused
from .identity import Person

#: A note is a line or two for a person and a model, not a document.
MAX_NOTE = 500


def reference_json(reference: ProjectReference, project: Project) -> dict[str, Any]:
    """One reference as the screens read it: the other project (in either direction), and the note."""
    return {"id": reference.id,
            "project": {"id": project.id, "name": project.name, "status": project.status,
                        "understoodPct": project.understood_pct},
            "note": reference.note, "createdAt": when(reference.created_at)}


async def referenced_ids(session: AsyncSession, project_id: str, *, limit: int = READ_AT_MOST) -> list[str]:
    """The projects a project reads from, the first ones added first, at most `limit` (retrieval's
    bound). The compiler asks this to hand a plan the referenced projects' memory facts; retrieval asks
    it for their pieces."""
    return [pid for pid, _ in await ProjectReferenceRepository(session).read_by(project_id, limit=limit)]


async def referenced(session: AsyncSession, project_id: str, *,
                     limit: int = READ_AT_MOST) -> list[tuple[str, str]]:
    """(id, name) of the projects a project reads from — what a label and a prefix are made from."""
    return await ProjectReferenceRepository(session).read_by(project_id, limit=limit)


class ReferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.references = ProjectReferenceRepository(session)
        self.activity = ActivityRepository(session)
        self.audit = AuditRepository(session)

    async def project(self, project_id: str) -> Project:
        found = await self.projects.get(project_id)
        if found is None:
            raise NotFound(f"project {project_id}")
        return found

    async def listing(self, project_id: str) -> dict[str, Any]:
        """Both directions: what this project reads from, and what reads from it. `readAtMost` is how many
        of the first retrieval and grounding search — the screen says which ones are past it."""
        await self.project(project_id)
        return {"references": [reference_json(r, p) for r, p in await self.references.of(project_id)],
                "referencedBy": [reference_json(r, p) for r, p in await self.references.by(project_id)],
                "readAtMost": READ_AT_MOST, "max": MAX_REFERENCES}

    async def _said(self, who: Person, project: Project, action: str, detail: str, audit: str,
                    what: dict[str, Any], *, ip: str = "", level: str = "info") -> None:
        project.last_active_at = utcnow()          # the card carries its references: this sends it again
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action=action,
                                   detail=f"{project.name} · {detail}", level=level, project_id=project.id)
        await self.audit.record(action=audit, user_id=who.id, target=project.id, detail=what, ip=ip)

    async def add(self, project_id: str, referenced_id: str, note: str, who: Person, *,
                  ip: str = "") -> tuple[ProjectReference, Project]:
        """Reference another project. Refused for the project itself, a project already referenced, one
        that does not exist, and past the ceiling."""
        project = await self.project(project_id)
        other_id = referenced_id.strip()
        if other_id == project_id:
            raise Refused(f"{project.name} cannot reference itself.", status=422)
        other = await self.projects.get(other_id)
        if other is None:
            raise Refused(f"There is no project {other_id} to reference.", status=404)
        if await self.references.pair(project_id, other_id) is not None:
            raise Refused(f"{project.name} already references {other.name}.")
        if await self.references.count(ProjectReference.project_id == project_id) >= MAX_REFERENCES:
            raise Refused(f"{project.name} already references {MAX_REFERENCES} projects, the most one may.")
        made = await self.references.add(ProjectReference(project_id=project_id, referenced_id=other_id,
                                                          note=note.strip()[:MAX_NOTE], created_by=who.id))
        await self._said(who, project, "Reference added", f"reads {other.name} (read only)",
                         "project.reference.add", {"referenced": other_id, "note": made.note}, ip=ip)
        return made, other

    async def update(self, project_id: str, reference_id: int, note: str, who: Person, *,
                     ip: str = "") -> tuple[ProjectReference, Project]:
        project = await self.project(project_id)
        found = await self.references.in_project(project_id, reference_id)
        if found is None:
            raise NotFound(f"reference {reference_id} of project {project_id}")
        other = await self.project(found.referenced_id)
        was, found.note = found.note, note.strip()[:MAX_NOTE]
        await self._said(who, project, "Reference changed", f"note on {other.name}",
                         "project.reference.note", {"referenced": other.id, "note": found.note, "was": was}, ip=ip)
        return found, other

    async def remove(self, project_id: str, reference_id: int, who: Person, *, ip: str = "") -> Project:
        project = await self.project(project_id)
        found = await self.references.in_project(project_id, reference_id)
        if found is None:
            raise NotFound(f"reference {reference_id} of project {project_id}")
        other = await self.project(found.referenced_id)
        await self.session.delete(found)
        await self._said(who, project, "Reference removed", f"no longer reads {other.name}",
                         "project.reference.remove", {"referenced": other.id}, ip=ip, level="warn")
        return other
