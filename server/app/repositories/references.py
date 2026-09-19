"""The projects a project reads from: a library the app uses, the service it calls, last year's version.

A reference is a pair — this project, the one it reads — with a note in a person's words. Both lists a
screen shows (what this project references, and what references it) stop at `MAX_REFERENCES`, and what
retrieval and grounding read stops lower still, at `READ_AT_MOST`, so the project's own code keeps the
room it needs in a model's prompt.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import Project, ProjectReference
from .base import Repository

#: How many projects one project may reference, and how many one list shows.
MAX_REFERENCES = 24
#: How many referenced projects retrieval and grounding search beside the project itself — the first
#: ones a person added. More than a handful would crowd the project's own code out of every answer.
READ_AT_MOST = 5


class ProjectReferenceRepository(Repository[ProjectReference]):
    model = ProjectReference

    async def of(self, project_id: str) -> list[tuple[ProjectReference, Project]]:
        """What this project references, with each referenced project, in the order they were added."""
        stmt = (select(ProjectReference, Project).join(Project, Project.id == ProjectReference.referenced_id)
                .where(ProjectReference.project_id == project_id)
                .order_by(ProjectReference.created_at, ProjectReference.id).limit(MAX_REFERENCES))
        return [(ref, project) for ref, project in (await self.session.execute(stmt)).all()]

    async def by(self, project_id: str) -> list[tuple[ProjectReference, Project]]:
        """What references this project, with each project that does."""
        stmt = (select(ProjectReference, Project).join(Project, Project.id == ProjectReference.project_id)
                .where(ProjectReference.referenced_id == project_id)
                .order_by(ProjectReference.created_at, ProjectReference.id).limit(MAX_REFERENCES))
        return [(ref, project) for ref, project in (await self.session.execute(stmt)).all()]

    async def in_project(self, project_id: str, reference_id: int) -> ProjectReference | None:
        return await self.one(ProjectReference.project_id == project_id, ProjectReference.id == reference_id)

    async def pair(self, project_id: str, referenced_id: str) -> ProjectReference | None:
        return await self.one(ProjectReference.project_id == project_id,
                              ProjectReference.referenced_id == referenced_id)

    async def read_by(self, project_id: str, *, limit: int = READ_AT_MOST) -> list[tuple[str, str]]:
        """(id, name) of the projects retrieval reads beside this one — the first ones added."""
        stmt = (select(Project.id, Project.name).join(ProjectReference, ProjectReference.referenced_id == Project.id)
                .where(ProjectReference.project_id == project_id)
                .order_by(ProjectReference.created_at, ProjectReference.id).limit(max(0, min(limit, MAX_REFERENCES))))
        return [(pid, name) for pid, name in (await self.session.execute(stmt)).all()]

    async def for_projects(self, ids: list[str]) -> dict[str, list[str]]:
        """project id → the ids it references, in order, for a whole list of projects in one query."""
        if not ids:
            return {}
        stmt = (select(ProjectReference.project_id, ProjectReference.referenced_id)
                .where(ProjectReference.project_id.in_(ids))
                .order_by(ProjectReference.project_id, ProjectReference.created_at, ProjectReference.id)
                .limit(MAX_REFERENCES * len(ids)))
        out: dict[str, list[str]] = {}
        for pid, referenced in (await self.session.execute(stmt)).all():
            out.setdefault(pid, []).append(referenced)
        return out
