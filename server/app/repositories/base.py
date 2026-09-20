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

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from sqlalchemy import ColumnElement, Integer, Select, cast, delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import Base
from ..models.identity import RefCounter

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


def fence(column: Any, hidden: Collection[str]) -> list[ColumnElement[bool]]:
    """What keeps a restricted project's rows out of a list that crosses projects.

    `hidden` is the answer `api.deps.unseen_by` gives: the restricted projects this person holds no
    grant in. Two things follow from saying it in SQL rather than filtering the rows afterwards. A
    page of 200 is 200 rows the person may see, so `offset` keeps walking the same list it started
    on instead of skipping at every page; and a `total` taken beside it counts those rows and no
    others, so the figure on the screen is about the list under it.

    A row belonging to no project at all stays: the workspace's own facts, its gates and its
    decisions belong to everyone. `project_id NOT IN (…)` alone would drop them, because in SQL a
    null is not "not in" anything — the same trap `routes_system._readable` names for the activity
    log. On a column that cannot be null the `IS NULL` arm simply never matches.

    Spliced into a `where` as a list rather than ANDed with a constant, so a workspace where nothing
    is restricted — which is every workspace until somebody restricts something — runs exactly the
    statement it ran before, and an Owner or Admin, whom `unseen_by` never narrows, does too.
    """
    if not hidden:
        return []
    return [column.is_(None) | column.not_in(sorted(hidden))]


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

    async def next_ref(self, column: Any, prefix: str, *, floor: int = 0) -> str:
        """The next reference of its kind: one row in `ref_counters`, bumped by one statement.

        It used to be `max(regexp_replace(ref, …))` over the whole table, under a per-prefix advisory
        lock — a regular expression evaluated on every row ever written, on the path a person waits on
        while a run starts. At a hundred thousand runs that was about a tenth of a second, and it grew
        with the workspace's whole history; two agents dispatched together queued for the lock as well.

        `INSERT … ON CONFLICT DO UPDATE … RETURNING` is atomic on its own, so the advisory lock is
        gone. The row it touches stays locked to the end of this transaction, which is what still makes
        two requests asking at the same moment take turns rather than both claiming the same number —
        but they take turns over one row, not over a scan.

        `floor` is a number the kind never goes below, whatever the counter says.

        The old scan survives in one place: seeding a counter that does not exist yet. A workspace
        migrated, restored or imported with references already in it must not start again at 1, and a
        prefix the migration's seed never named — `EVAL-`, or one a later feature adds — has no row
        until the first ref is asked for. `xmax = 0` is how Postgres says "this row was inserted, not
        updated", which is the only way to tell the two apart from inside one statement. The look-up
        that follows is the same idea with the same answer: it is one probe of the unique index the ref
        already has, and it is what keeps this honest when something writes rows behind the counter's
        back.
        """
        bump = (upsert(RefCounter).values(prefix=prefix, next=floor + 1)
                .on_conflict_do_update(index_elements=[RefCounter.prefix],
                                       set_={"next": func.greatest(RefCounter.next, floor) + 1})
                .returning(RefCounter.next, literal_column("(xmax = 0)").label("born")))
        number, born = (await self.session.execute(bump)).one()
        if born:
            number = await self._seed_ref_counter(column, prefix, floor)
        ref = f"{prefix}{int(number)}"
        if await self.exists(column == ref):
            # Rows written without asking the counter: a workspace carried over from the old stack by
            # `scripts/import-sqlite.py`, or a fixture loaded straight into the tables. One indexed
            # look-up against the unique index catches it — and the counter is then brought up to what
            # the table really holds, so it is caught once and never again.
            ref = f"{prefix}{await self._seed_ref_counter(column, prefix, floor)}"
        return ref

    async def _seed_ref_counter(self, column: Any, prefix: str, floor: int) -> int:
        """A counter's number, taken from the refs the table already carries — the only scan left here.

        Reached twice in the life of a prefix at most: when its row is born, and if a ref it handed out
        turned out to be taken already. Either way the row is locked by the statement that just touched
        it, so a second request asking for this prefix waits and then reads what this one wrote.
        """
        digits = func.nullif(func.regexp_replace(column, r"\D", "", "g"), "")
        highest = int((await self.session.execute(
            select(func.coalesce(func.max(cast(digits, Integer)), 0)))).scalar_one())
        number = max(highest, floor) + 1
        await self.session.execute(
            update(RefCounter).where(RefCounter.prefix == prefix).values(next=number))
        return number

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
