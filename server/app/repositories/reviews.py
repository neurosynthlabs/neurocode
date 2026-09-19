"""Reviews a person asked for, of any diff: a project's list, newest first, and one by its ref."""
from __future__ import annotations

from sqlalchemy import select

from ..models import CodeReview, Run
from .base import Page, Repository


class CodeReviewRepository(Repository[CodeReview]):
    model = CodeReview

    async def by_ref(self, ref: str) -> CodeReview | None:
        return await self.one(CodeReview.ref == ref)

    async def of(self, project_id: str, *, limit: int | None = None, offset: int = 0) -> Page[CodeReview]:
        return await self.page(CodeReview.project_id == project_id,
                               order_by=[CodeReview.created_at.desc(), CodeReview.ref.desc()],
                               limit=limit, offset=offset)

    async def same_running(self, project_id: str, source: str, target: str, base: str,
                           head: str) -> CodeReview | None:
        """A review of exactly this diff still being read — asking twice reads it once."""
        return await self.one(CodeReview.project_id == project_id, CodeReview.source == source,
                              CodeReview.target == target, CodeReview.base == base, CodeReview.head == head,
                              CodeReview.status == "running")

    async def next_ref(self, prefix: str = "REV-") -> str:
        return await super().next_ref(CodeReview.ref, prefix)

    async def run_on_branch(self, project_id: str, branch: str) -> Run | None:
        """The agent run that made this branch, newest first — whose lane the reviewer should avoid."""
        return (await self.session.execute(
            select(Run).where(Run.project_id == project_id, Run.branch == branch)
            .order_by(Run.created_at.desc()).limit(1))).scalars().first()

    async def run_by_ref(self, project_id: str, ref: str) -> Run | None:
        return (await self.session.execute(
            select(Run).where(Run.project_id == project_id, Run.ref == ref).limit(1))).scalars().first()
