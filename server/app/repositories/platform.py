"""The platform's own rows — for now, the ideas this workspace has argued with itself.

A brainstorm used to be one JSON document with the whole brief inside it, so "the newest ideas" was a
sort in Python and a new reference was a count of the rows. Here it is a row whose stages, case
against, MVP and roadmap are columns of their own: a list is a query, and the next reference is one
statement the database answers, so two people brainstorming in the same second cannot claim the same
one.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, Integer, cast, func, select

from ..models import Brainstorm
from .base import Page, Repository


class BrainstormRepository(Repository[Brainstorm]):
    model = Brainstorm

    async def by_ref(self, ref: str) -> Brainstorm | None:
        return await self.one(Brainstorm.ref == ref)

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0) -> Page[Brainstorm]:
        where: list[ColumnElement[bool]] = [Brainstorm.project_id == project_id] if project_id else []
        return await self.page(*where, order_by=Brainstorm.created_at.desc(), limit=limit, offset=offset)

    async def next_ref(self, prefix: str = "IDEA-") -> str:
        """The next reference: the highest number any ref carries, plus one — worked out in SQL rather
        than by counting rows in Python, which is what let two of them collide before."""
        digits = func.nullif(func.regexp_replace(Brainstorm.ref, r"\D", "", "g"), "")
        stmt = select(func.coalesce(func.max(cast(digits, Integer)), 0))
        return f"{prefix}{int((await self.session.execute(stmt)).scalar_one()) + 1}"
