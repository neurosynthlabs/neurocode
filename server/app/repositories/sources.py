"""A project's further sources: the folders and repositories that belong to it beside its first one.

A project rarely has more than a handful, but the rule that nothing returns everything holds here too:
`MAX_SOURCES` is the ceiling one project may hold, and every list stops there.
"""
from __future__ import annotations

from sqlalchemy import func, select

from ..models import ProjectSource
from .base import Repository

#: How many further sources one project may hold. A project of more than this is several projects.
MAX_SOURCES = 24


class ProjectSourceRepository(Repository[ProjectSource]):
    model = ProjectSource

    async def of(self, project_id: str) -> list[ProjectSource]:
        """In the order a person arranged them, then the order they were added."""
        return await self.list(ProjectSource.project_id == project_id,
                               order_by=[ProjectSource.position, ProjectSource.id], limit=MAX_SOURCES)

    async def for_projects(self, ids: list[str]) -> dict[str, list[ProjectSource]]:
        """project id → its sources, in order, for a whole list of projects in one query."""
        if not ids:
            return {}
        stmt = (select(ProjectSource).where(ProjectSource.project_id.in_(ids))
                .order_by(ProjectSource.project_id, ProjectSource.position, ProjectSource.id)
                .limit(MAX_SOURCES * len(ids)))
        out: dict[str, list[ProjectSource]] = {}
        for source in (await self.session.execute(stmt)).scalars():
            out.setdefault(source.project_id, []).append(source)
        return out

    async def in_project(self, project_id: str, source_id: int) -> ProjectSource | None:
        return await self.one(ProjectSource.project_id == project_id, ProjectSource.id == source_id)

    async def labelled(self, project_id: str, label: str) -> ProjectSource | None:
        return await self.one(ProjectSource.project_id == project_id, ProjectSource.label == label)

    async def next_position(self, project_id: str) -> int:
        stmt = select(func.coalesce(func.max(ProjectSource.position), 0)).where(
            ProjectSource.project_id == project_id)
        return int((await self.session.execute(stmt)).scalar_one()) + 1
