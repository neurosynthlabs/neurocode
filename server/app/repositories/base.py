"""Data access, one aggregate at a time.

A repository is the only place that knows how a thing is stored. Services ask it for objects and get
objects back — never a row, never a cursor, never a half-built query someone else has to finish. That
is what keeps a service readable and testable: it can be handed a repository against a throwaway
database and never know the difference.

Two rules hold here. **Nothing returns everything**: every list is paged, with a ceiling the caller
cannot raise, because "it was only ever a few rows" is how a screen becomes slow a year later.
**Nothing here commits**: the unit of work belongs to the request, so several repositories can take
part in one transaction and fail together.
"""
from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from sqlalchemy import ColumnElement, Integer, Select, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import Base

M = TypeVar("M", bound=Base)

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
#: The most rows `everything()` will walk to. A workspace with more people than this has outgrown a
#: single-page admin screen, and saying so beats scrolling forever or stopping without a word.
EVERYTHING_CAP = 5_000


class NotFound(LookupError):
    """Asked for something that is not there. Routes turn this into a 404 with its own words."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what} not found")
        self.what = what


@dataclass(slots=True)
class Page(Generic[M]):
    """What a list answer actually is: some rows, and the truth about how many there are."""

    items: list[M]
    total: int
    limit: int
    offset: int

    @property
    def more(self) -> bool:
        return self.offset + len(self.items) < self.total

    @property
    def next_offset(self) -> int | None:
        return self.offset + self.limit if self.more else None


def bounded(limit: int | None) -> int:
    """`None` means "you did not say"; 0 means "none of them", which clamps to one rather than
    falling through to the default — `limit or DEFAULT_LIMIT` read a deliberate 0 as an absence."""
    return max(1, min(DEFAULT_LIMIT if limit is None else limit, MAX_LIMIT))


class Repository(Generic[M]):
    """The parts every aggregate needs. A concrete repository adds the questions only it can answer."""

    model: type[M]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── reading ──────────────────────────────────────────────────
    async def get(self, pk: Any) -> M | None:
        return await self.session.get(self.model, pk)

    async def require(self, pk: Any) -> M:
        found = await self.get(pk)
        if found is None:
            raise NotFound(f"{self.model.__tablename__.rstrip('s')} {pk}")
        return found

    async def one(self, *where: ColumnElement[bool]) -> M | None:
        return (await self.session.execute(select(self.model).where(*where).limit(1))).scalar_one_or_none()

    async def list(self, *where: ColumnElement[bool], order_by: Any = None, limit: int | None = None,
                   offset: int = 0) -> list[M]:
        stmt: Select[tuple[M]] = select(self.model).where(*where).limit(bounded(limit)).offset(max(0, offset))
        if order_by is not None:
            stmt = stmt.order_by(*(order_by if isinstance(order_by, Sequence) else [order_by]))
        return list((await self.session.execute(stmt)).scalars().unique())

    async def page(self, *where: ColumnElement[bool], order_by: Any = None, limit: int | None = None,
                   offset: int = 0) -> Page[M]:
        """The rows and the count, so a screen can say "20 of 412" instead of guessing."""
        size, start = bounded(limit), max(0, offset)
        items = await self.list(*where, order_by=order_by, limit=size, offset=start)
        return Page(items=items, total=await self.count(*where), limit=size, offset=start)

    async def everything(self, fetch: Any, *, cap: int = EVERYTHING_CAP) -> tuple[list[M], bool]:
        """Walk a paged query to its end, and say whether it reached one.

        The rule elsewhere is that nothing returns everything, and it is the right rule: a list a
        screen scrolls must not be able to become a scan. The admin lists are the exception the rule
        was never about — everyone in the workspace, every role, every team — and a *silent* stop at
        500 was the worst of both: the screen said "500 people" next to a header saying 601, and a
        team member past the cut rendered as a raw id.

        So they are walked to the end, with a ceiling of their own, and the caller is told plainly
        whether it got everything. `fetch` is any `(limit, offset) -> Page` — a repository method.
        """
        found: list[M] = []
        offset = 0
        while len(found) < cap:
            page: Page[M] = await fetch(limit=MAX_LIMIT, offset=offset)
            found.extend(page.items)
            if not page.more or not page.items:
                return found, True
            offset = page.next_offset or (offset + len(page.items))
        return found[:cap], False

    async def count(self, *where: ColumnElement[bool]) -> int:
        stmt = select(func.count()).select_from(self.model).where(*where)
        return int((await self.session.execute(stmt)).scalar_one())

    async def exists(self, *where: ColumnElement[bool]) -> bool:
        return (await self.session.execute(select(1).select_from(self.model).where(*where).limit(1))
                ).scalar_one_or_none() is not None

    async def next_ref(self, column: Any, prefix: str) -> str:
        """The next reference of its kind: the highest number any ref carries, plus one.

        Worked out by the database rather than by reading every ref into Python — and taken under a
        lock, because "read the maximum, then insert it" is a race however fast the read is. Two
        requests arriving together both saw the same maximum and both tried to claim it; the unique
        index then failed the second one, so a perfectly ordinary second click became a 500.

        The lock is per kind of reference and is held only to the end of this transaction, so tasks
        and runs never wait on each other and nothing can be left locked by a request that died.
        """
        key = zlib.crc32(prefix.encode()) - 2 ** 31              # advisory keys are signed 32-bit
        await self.session.execute(select(func.pg_advisory_xact_lock(key)))
        digits = func.nullif(func.regexp_replace(column, r"\D", "", "g"), "")
        highest = (await self.session.execute(
            select(func.coalesce(func.max(cast(digits, Integer)), 0)))).scalar_one()
        return f"{prefix}{int(highest) + 1}"

    # ── writing ──────────────────────────────────────────────────
    async def add(self, obj: M) -> M:
        """Hand back the object with whatever the database filled in — its id, its timestamps."""
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def add_all(self, objs: Sequence[M]) -> Sequence[M]:
        self.session.add_all(list(objs))
        await self.session.flush()
        return objs

    async def remove(self, obj: M) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def remove_where(self, *where: ColumnElement[bool]) -> int:
        result = await self.session.execute(delete(self.model).where(*where))
        return int(result.rowcount or 0)
