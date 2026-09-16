"""Eval suites, their cases, their runs and what each case produced — read back the way the screen asks.

The questions worth a statement of their own are the ones about history. "The latest finished run of
each suite, the one before it, and the last seven scores" is one window over `eval_runs`, not a query
per suite, and it only ever reads runs that finished: a run that was stopped, or that died with the
process, never scored and never moves a trend.

Writing a result and stopping a run meet on the run's own row. The runner locks it before it writes,
so a stop that has committed is seen by the very next case — and a stop that arrives while a model is
still answering is seen before that answer is kept.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import raiseload

from ..models import EvalCase, EvalResult, EvalRun, EvalSuite, MemoryFact
from .base import Repository

#: How many suites the screen lists, how many cases a suite holds, and how far back a trend reaches.
MAX_SUITES = 200
MAX_CASES = 200
TREND = 7
MAX_RUNS = 20
MAX_LESSONS = 20
#: A run in one of these is someone's work in progress; a suite with one is not started again.
IN_FLIGHT = ("queued", "running")
#: Where a lesson learned from an eval says it came from: `eval:<suite>:<run ref>`.
LESSON_SOURCE = "eval:"


class EvalSuiteRepository(Repository[EvalSuite]):
    model = EvalSuite

    async def listed(self) -> list[EvalSuite]:
        """Every suite, by name — without its cases, which the list never shows and a count answers."""
        stmt = (select(EvalSuite).options(raiseload(EvalSuite.cases))
                .order_by(EvalSuite.name).limit(MAX_SUITES))
        return list((await self.session.execute(stmt)).scalars())

    async def by_name(self, name: str) -> EvalSuite | None:
        return await self.one(EvalSuite.name == name)

    async def case_counts(self) -> dict[str, int]:
        stmt = select(EvalCase.suite_id, func.count()).group_by(EvalCase.suite_id)
        return {suite_id: int(n) for suite_id, n in (await self.session.execute(stmt)).all()}


class EvalCaseRepository(Repository[EvalCase]):
    model = EvalCase

    async def count_in(self, suite_id: str) -> int:
        return await self.count(EvalCase.suite_id == suite_id)

    async def next_n(self, suite_id: str) -> int:
        stmt = select(func.coalesce(func.max(EvalCase.n), 0)).where(EvalCase.suite_id == suite_id)
        return int((await self.session.execute(stmt)).scalar_one()) + 1


class EvalRunRepository(Repository[EvalRun]):
    model = EvalRun

    async def by_ref(self, ref: str) -> EvalRun | None:
        return await self.one(EvalRun.ref == ref)

    async def next_ref(self, prefix: str = "EVAL-") -> str:
        return await super().next_ref(EvalRun.ref, prefix)

    async def history(self, suite_ids: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """suite id → its last finished runs, newest first: the score now, the one it is compared
        with, and the trend — in one statement however many suites are on screen."""
        ranked = (select(EvalRun.suite_id, EvalRun.id, EvalRun.ref, EvalRun.score, EvalRun.passed, EvalRun.failed,
                         EvalRun.partial, EvalRun.errored, EvalRun.finished_at,
                         func.row_number().over(partition_by=EvalRun.suite_id,
                                                order_by=EvalRun.finished_at.desc()).label("nth"))
                  .where(EvalRun.status == "done", EvalRun.finished_at.is_not(None)))
        if suite_ids is not None:
            ranked = ranked.where(EvalRun.suite_id.in_(suite_ids))
        sub = ranked.subquery()
        stmt = select(sub).where(sub.c.nth <= TREND).order_by(sub.c.suite_id, sub.c.nth)
        out: dict[str, list[dict[str, Any]]] = {}
        for row in (await self.session.execute(stmt)).mappings():
            out.setdefault(row["suite_id"], []).append(dict(row))
        return out

    async def in_flight(self) -> dict[str, str]:
        """suite id → the ref of the run someone is waiting on, for every suite that has one."""
        stmt = (select(EvalRun.suite_id, EvalRun.ref).where(EvalRun.status.in_(IN_FLIGHT))
                .order_by(EvalRun.created_at))
        return {suite_id: ref for suite_id, ref in (await self.session.execute(stmt)).all()}

    async def of_suite(self, suite_id: str) -> list[EvalRun]:
        """A suite's recent runs, newest first, without their results — the run picker needs none."""
        stmt = (select(EvalRun).options(raiseload(EvalRun.results)).where(EvalRun.suite_id == suite_id)
                .order_by(EvalRun.created_at.desc(), EvalRun.finished_at.desc().nulls_first()).limit(MAX_RUNS))
        return list((await self.session.execute(stmt)).scalars())

    async def locked(self, run_id: str) -> EvalRun | None:
        """The run's row, held until this transaction ends: a stop and a result cannot cross."""
        stmt = (select(EvalRun).options(raiseload(EvalRun.results)).where(EvalRun.id == run_id)
                .with_for_update())
        return (await self.session.execute(stmt)).scalar_one_or_none()


class EvalResultRepository(Repository[EvalResult]):
    model = EvalResult

    async def of_run(self, run_id: str) -> list[EvalResult]:
        stmt = select(EvalResult).where(EvalResult.run_id == run_id).limit(MAX_CASES)
        return list((await self.session.execute(stmt)).scalars())

    async def scores_of(self, run_id: str) -> dict[str, float]:
        """case id → the score it earned in that run. What a case's movement is measured against."""
        stmt = select(EvalResult.case_id, EvalResult.score).where(EvalResult.run_id == run_id).limit(MAX_CASES)
        return {case_id: float(score) for case_id, score in (await self.session.execute(stmt)).all()}


class LessonRepository(Repository[MemoryFact]):
    """Lessons are memory facts like any other; what makes one a lesson is where it says it came from."""

    model = MemoryFact

    async def recent(self) -> list[MemoryFact]:
        stmt = (select(MemoryFact).where(MemoryFact.source.startswith(LESSON_SOURCE, autoescape=True),
                                         MemoryFact.archived.is_(False))
                .order_by(MemoryFact.created_at.desc()).limit(MAX_LESSONS))
        return list((await self.session.execute(stmt)).scalars().unique())
