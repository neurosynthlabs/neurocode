"""Research, read and written: the reports, and the retrieval each angle is allowed to see.

A report's list row is one aggregate — how many angles it has and how many of them cite anything — so
the list never loads the angles and citations it does not show. The detail loads everything, because
it shows everything.

Retrieval here is the same hybrid search sessions use, narrowed to the kinds of chunk the person chose.
The narrowing has to happen inside the search rather than after it: filtering the top results
afterwards let thousands of code chunks fill every slot, so a documentation-only research found
nothing and reported a gap that was not true.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, exists, func, select
from sqlalchemy.orm import lazyload

from ..models import Chunk, ResearchAngle, ResearchCitation, ResearchReport
from .base import Repository, bounded
from .retrieval import ChunkRepository


class KindChunkRepository(ChunkRepository):
    """Chunk search that only ever sees the kinds it was given — in both halves of the hybrid search,
    because both are built on the scope this narrows."""

    def __init__(self, session: Any, kinds: list[str]) -> None:
        super().__init__(session)
        self.kinds = list(kinds)

    def _scope(self, project_id: str) -> ColumnElement[bool]:
        return and_(super()._scope(project_id), Chunk.kind.in_(self.kinds))


@dataclass(slots=True)
class ReportRow:
    """A report as the list shows it: the row, and the two counts the list needs from its angles."""

    report: ResearchReport
    angles: int
    cited: int


class ResearchRepository(Repository[ResearchReport]):
    model = ResearchReport

    async def next_ref(self, prefix: str = "RES-") -> str:
        return await super().next_ref(ResearchReport.ref, prefix)

    async def by_ref(self, ref: str) -> ResearchReport | None:
        """The whole report: its angles and their citations come with it (both are selectin)."""
        return await self.one(ResearchReport.ref == ref)

    def _counted(self) -> Select[tuple[ResearchReport, int, int]]:
        """A report with how many angles it has and how many of them cite anything."""
        cited = exists().where(ResearchCitation.angle_id == ResearchAngle.id)
        angles = (select(func.count(ResearchAngle.id))
                  .where(ResearchAngle.report_id == ResearchReport.id).scalar_subquery())
        with_citation = (select(func.count(ResearchAngle.id))
                         .where(ResearchAngle.report_id == ResearchReport.id, cited).scalar_subquery())
        # The angles relationship is selectin; a list must not drag every angle up with every row.
        return select(ResearchReport, angles, with_citation).options(lazyload(ResearchReport.angles))

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0) -> list[ReportRow]:
        where: list[ColumnElement[bool]] = [ResearchReport.project_id == project_id] if project_id else []
        stmt = (self._counted().where(*where).order_by(ResearchReport.created_at.desc())
                .limit(bounded(limit)).offset(max(0, offset)))
        return [ReportRow(report, int(angles or 0), int(cited or 0))
                for report, angles, cited in (await self.session.execute(stmt)).all()]

    async def row(self, report_id: str) -> ReportRow | None:
        found = (await self.session.execute(
            self._counted().where(ResearchReport.id == report_id))).first()
        if found is None:
            return None
        report, angles, cited = found
        return ReportRow(report, int(angles or 0), int(cited or 0))

    async def angles(self, report_id: str) -> list[ResearchAngle]:
        stmt = (select(ResearchAngle).where(ResearchAngle.report_id == report_id)
                .order_by(ResearchAngle.n))
        return list((await self.session.execute(stmt)).scalars().unique())
