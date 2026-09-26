"""Agent runs and sessions, read back.

Two things are deliberate here. Children are fetched **by query**, never through a relationship: a
serialiser that touches an unloaded relationship inside async code raises, and a run's children are
read on every list. And logs are paged from an id you already hold, which is what lets a screen
reconnect after a drop and ask only for what it missed.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by, array_agg, insert

from ..models import (
    Approval,
    Chat,
    ChatMessage,
    Project,
    Run,
    RunLog,
    RunStep,
    Setting,
    TestCoverage,
    TestExpectation,
    TestFailure,
    User,
)
from .base import MAX_LIMIT, Page, Repository, bounded, fence


class RunRepository(Repository[Run]):
    model = Run

    async def by_ref(self, ref: str) -> Run | None:
        return await self.one(Run.ref == ref)

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0, hidden: frozenset[str] = frozenset()) -> Page[Run]:
        where: list[ColumnElement[bool]] = [Run.project_id == project_id] if project_id else []
        return await self.page(*where, *fence(Run.project_id, hidden),
                               order_by=Run.created_at.desc(), limit=limit, offset=offset)

    async def children_of(self, run_ids: list[str]) -> dict[str, list[Run]]:
        """parent id → its agent runs. One statement for a whole list of runs, not one per run."""
        if not run_ids:
            return {}
        rows = (await self.session.execute(
            select(Run).where(Run.parent_id.in_(run_ids)).order_by(Run.created_at))).scalars().unique()
        out: dict[str, list[Run]] = {}
        for child in rows:
            out.setdefault(child.parent_id or "", []).append(child)
        return out

    async def active_check(self, project_id: str) -> Run | None:
        """A test-only run of this project that has not finished — queued, working, or at its gate."""
        return await self.one(Run.project_id == project_id, Run.role == "check",
                              Run.status.in_(("queued", "running", "waiting")))

    async def waiting(self) -> list[Run]:
        return await self.list(Run.status == "waiting", order_by=Run.created_at)

    async def next_ref(self, prefix: str = "RUN-") -> str:
        return await super().next_ref(Run.ref, prefix)


class RunLogRepository(Repository[RunLog]):
    model = RunLog

    async def after(self, run_id: str, last_id: int = 0, *, limit: int = 1000) -> list[RunLog]:
        stmt = (select(RunLog).where(RunLog.run_id == run_id, RunLog.id > last_id)
                .order_by(RunLog.id).limit(min(limit, 2000)))
        return list((await self.session.execute(stmt)).scalars())

    async def of_step(self, run_id: str, step: int, *, limit: int = 400) -> list[RunLog]:
        """What one step printed, in order. The test step keeps at most its first 400 lines anyway."""
        stmt = (select(RunLog).where(RunLog.run_id == run_id, RunLog.step == step)
                .order_by(RunLog.id).limit(min(limit, 1000)))
        return list((await self.session.execute(stmt)).scalars())

    async def write(self, run_id: str, *, level: str, line: str, step: int | None = None) -> RunLog:
        """One line of a run's output, written and announced together.

        The screens key output by the run's *reference*, which is looked up here rather than passed in
        by fifteen call sites — one of which would eventually forget, and that line would arrive with
        nowhere to go. The run is already in this session almost every time, so it costs nothing.
        """
        written = await self.add(RunLog(run_id=run_id, level=level, line=line[:2000], step=step))
        feed = self.session.info.get("bus")
        if feed is not None:
            run = await self.session.get(Run, run_id)
            if run is not None:
                from ..schemas.runtime import run_log_json
                feed.publish("run", {"runRef": run.ref, **run_log_json(written)})
        return written


class ChatRepository(Repository[Chat]):
    model = Chat

    async def by_ref(self, ref: str) -> Chat | None:
        return await self.one(Chat.ref == ref)

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0, hidden: frozenset[str] = frozenset()) -> Page[Chat]:
        where: list[ColumnElement[bool]] = [Chat.project_id == project_id] if project_id else []
        return await self.page(*where, *fence(Chat.project_id, hidden),
                               order_by=Chat.last_at.desc(), limit=limit, offset=offset)

    async def messages(self, chat_id: str, after: int = 0, *, limit: int = 500) -> list[ChatMessage]:
        stmt = (select(ChatMessage).where(ChatMessage.chat_id == chat_id, ChatMessage.id > after)
                .order_by(ChatMessage.id).limit(min(limit, 1000)))
        return list((await self.session.execute(stmt)).scalars())

    async def tail(self, chat_id: str, limit: int = 500, *, before: int | None = None,
                   roles: Sequence[str] | None = None, folded: bool = False) -> list[ChatMessage]:
        """The newest turns of a session's current line, oldest first: what a model is sent and where an
        answer resumes. `messages` pages forward from an id, which is what a screen catching up wants; read
        that way and cut, a long session handed the model its oldest turns and never the question just
        asked. A replaced turn is never here, and a folded one only with `folded` — its summary stands in
        for it. `before` reads the line up to a turn; `roles`, only turns of those kinds."""
        where: list[ColumnElement[bool]] = [ChatMessage.chat_id == chat_id, ChatMessage.superseded_by.is_(None)]
        if not folded:
            where.append(ChatMessage.compacted.is_(False))
        if before is not None:
            where.append(ChatMessage.id < before)
        if roles is not None:
            where.append(ChatMessage.role.in_(list(roles)))
        stmt = select(ChatMessage).where(*where).order_by(ChatMessage.id.desc()).limit(min(limit, 1000))
        return list(reversed(list((await self.session.execute(stmt)).scalars())))

    async def say(self, chat_id: str, *, role: str, body: str, **extra: object) -> ChatMessage:
        """One turn, written the moment it happens — that is the whole promise of a session — and
        announced in the same breath, so an open tab sees it arrive rather than on the next reload."""
        said = await self.add(ChatMessage(chat_id=chat_id, role=role, body=body, **extra))
        feed = self.session.info.get("bus")
        if feed is not None:
            chat = await self.session.get(Chat, chat_id)
            if chat is not None:
                from ..schemas.runtime import chat_message_json
                feed.publish("chat", {"sessionRef": chat.ref, **chat_message_json(said)})
        return said

    async def next_ref(self, prefix: str = "CHAT-") -> str:
        return await super().next_ref(Chat.ref, prefix)


#: The runs a test step really ran in: the command started and an exit code came back.
TESTED = ("passed", "failed")


class ResultsRepository(Repository[TestFailure]):
    """What test steps found — failures, coverage, totals — and the expectations people set on them.

    Failures and coverage are read from their own tables, never through `Run.failures`: that
    relationship refuses to load lazily, because a run is fetched dozens of times while it works and
    none of those reads want two hundred excerpts dragged along.
    """

    model = TestFailure

    # ── written by the test step ─────────────────────────────────
    async def replace(self, run_id: str, step_n: int, failures: Sequence[dict[str, Any]],
                      coverage: Sequence[tuple[str, int, int, str]]) -> None:
        """This step's findings, replacing any an earlier attempt at the same step left."""
        await self.session.execute(delete(TestFailure).where(TestFailure.run_id == run_id,
                                                              TestFailure.step_n == step_n))
        await self.session.execute(delete(TestCoverage).where(TestCoverage.run_id == run_id))
        self.session.add_all([TestFailure(run_id=run_id, step_n=step_n, **f) for f in failures])
        self.session.add_all([TestCoverage(run_id=run_id, path=path, covered=covered, total=total, source=source)
                              for path, covered, total, source in coverage])
        await self.session.flush()

    async def expected(self, run_id: str, project_id: str) -> tuple[int, int]:
        """(failures recorded for this run, how many of them a person expects)."""
        row = (await self.session.execute(
            select(func.count(TestFailure.id), func.count(TestExpectation.id))
            .select_from(TestFailure)
            .outerjoin(TestExpectation, and_(TestExpectation.project_id == project_id,
                                             TestExpectation.test_name == TestFailure.name))
            .where(TestFailure.run_id == run_id))).one()
        return int(row[0]), int(row[1])

    # ── read by the Testing screen ───────────────────────────────
    async def onboarded(self, project_id: str | None, *, limit: int = 50,
                        hidden: frozenset[str] = frozenset()) -> list[Project]:
        """Projects with code on this machine — the only ones that have tests to run.

        The whole Testing report is keyed on the ids this returns, so fencing the projects here
        fences the failures, the history, the coverage and the standing answers with them."""
        where: list[ColumnElement[bool]] = [Project.source_kind.is_not(None)]
        if project_id:
            where.append(Project.id == project_id)
        where += fence(Project.id, hidden)
        stmt = select(Project).where(*where).order_by(Project.name).limit(min(limit, MAX_LIMIT))
        return list((await self.session.execute(stmt)).scalars())

    async def answers(self, project_ids: Sequence[str]) -> dict[str, str]:
        """project id → 'allowed' | 'refused', for projects where someone answered the first-run gate."""
        if not project_ids:
            return {}
        keys = {f"runtime.tests.{pid}": pid for pid in project_ids}
        rows = (await self.session.execute(select(Setting).where(Setting.key.in_(keys)))).scalars()
        return {keys[row.key]: str(row.value) for row in rows}

    async def standing_answers(self, *, limit: int | None = None, offset: int = 0,
                               hidden: frozenset[str] = frozenset()) -> list[tuple[Project, Setting]]:
        """Every project's kept answer to the test gate, with the setting that holds it — the row the
        runtime reads before it runs a command, so what is listed is exactly what is applied.

        Every row names a project, so a project this person may not see has no row here either."""
        stmt = (select(Project, Setting)
                .join(Setting, Setting.key == func.concat("runtime.tests.", Project.id))
                .where(*fence(Project.id, hidden))
                .order_by(Project.name).limit(bounded(limit)).offset(max(0, offset)))
        return [(project, setting) for project, setting in (await self.session.execute(stmt)).all()]

    async def gate_deciders(self, project_ids: Sequence[str]) -> dict[tuple[str, str], tuple[str, datetime]]:
        """(project id, 'approved' | 'denied') → who last decided a test-step gate that way, and when.

        Only a person's decision counts: a gate closed because its check run was stopped carries no
        one and wrote no answer, so it can never be the one a standing rule is credited to. The time
        comes from the same approval as the name: the setting's own `updated_at` does not move when a
        second person writes the answer it already holds, so pairing the two put one person's name on
        another's moment."""
        if not project_ids:
            return {}
        # Joined on the gate's own `run_id`, not on the ref it prints. The refs matched as text until
        # every writer kept the link (services/runs.py, data/loader.py) and the old rows were backfilled;
        # now the join is a key with an index under it, and a gate whose run has been deleted is gone
        # with it rather than left pointing at a name that may be handed out again.
        stmt = (select(Approval.project_id, Approval.status, User.name, Approval.decided_at)
                .join(Run, Run.id == Approval.run_id)
                .join(RunStep, and_(RunStep.run_id == Run.id, RunStep.n == Approval.step, RunStep.kind == "test"))
                .join(User, User.id == Approval.decided_by)
                .where(Approval.project_id.in_(project_ids), Approval.status.in_(("approved", "denied")))
                .order_by(Approval.project_id, Approval.status, Approval.decided_at.desc())
                .distinct(Approval.project_id, Approval.status))
        return {(pid, status): (name, at) for pid, status, name, at in (await self.session.execute(stmt)).all()}

    def _tested(self, project_ids: Sequence[str]) -> Select[tuple[Run, int | None]]:
        return (select(Run, RunStep.ms)
                .join(RunStep, and_(RunStep.run_id == Run.id, RunStep.kind == "test"))
                .where(Run.project_id.in_(project_ids), Run.tests_status.in_(TESTED)))

    async def latest(self, project_ids: Sequence[str]) -> dict[str, tuple[Run, int | None]]:
        """Each project's most recent run whose tests really ran, with how long the step took."""
        if not project_ids:
            return {}
        stmt = (self._tested(project_ids).order_by(Run.project_id, Run.created_at.desc())
                .distinct(Run.project_id))
        return {run.project_id: (run, ms) for run, ms in (await self.session.execute(stmt)).all()}

    async def history(self, project_ids: Sequence[str], *, limit: int = 50) -> list[tuple[Run, int | None]]:
        if not project_ids:
            return []
        stmt = self._tested(project_ids).order_by(Run.created_at.desc()).limit(bounded(limit))
        return [(run, ms) for run, ms in (await self.session.execute(stmt)).all()]

    async def checking(self, project_ids: Sequence[str]) -> dict[str, str]:
        """project id → the ref of a test-only run still in flight there."""
        if not project_ids:
            return {}
        stmt = (select(Run.project_id, Run.ref)
                .where(Run.project_id.in_(project_ids), Run.role == "check",
                       Run.status.in_(("queued", "running", "waiting")))
                .order_by(Run.created_at))
        return {pid: ref for pid, ref in (await self.session.execute(stmt)).all()}

    def _with_expectation(self) -> Select[tuple[TestFailure, str, str, TestExpectation | None, str | None]]:
        return (select(TestFailure, Run.ref, Run.project_id, TestExpectation, User.name)
                .join(Run, Run.id == TestFailure.run_id)
                .outerjoin(TestExpectation, and_(TestExpectation.project_id == Run.project_id,
                                                 TestExpectation.test_name == TestFailure.name))
                .outerjoin(User, User.id == TestExpectation.by_user_id))

    async def failures(self, run_ids: Sequence[str], *, limit: int = MAX_LIMIT) -> list[tuple[Any, ...]]:
        """(failure, run ref, project id, expectation or None, who set it) for these runs."""
        if not run_ids:
            return []
        stmt = (self._with_expectation().where(TestFailure.run_id.in_(run_ids))
                .order_by(Run.project_id, TestFailure.id).limit(bounded(limit)))
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def failure(self, failure_id: int) -> tuple[Any, ...] | None:
        row = (await self.session.execute(
            self._with_expectation().where(TestFailure.id == failure_id))).one_or_none()
        return tuple(row) if row else None

    async def recurrence(self, project_ids: Sequence[str], window: int
                         ) -> tuple[dict[str, int], dict[tuple[str, str], tuple[int, str]]]:
        """How often each test failed across each project's last `window` tested runs.

        Returns (project → how many runs the window holds, (project, test) → (runs it failed in, the
        earliest of those runs' refs)). A measured count, not anyone's opinion of what is flaky.
        """
        if not project_ids:
            return {}, {}
        ranked = (select(Run.id, Run.ref, Run.project_id, Run.created_at,
                         func.row_number().over(partition_by=Run.project_id,
                                                order_by=Run.created_at.desc()).label("n"))
                  .where(Run.project_id.in_(project_ids), Run.tests_status.in_(TESTED))).subquery()
        recent = select(ranked).where(ranked.c.n <= window).subquery()
        sizes = (await self.session.execute(
            select(recent.c.project_id, func.count()).group_by(recent.c.project_id))).all()
        counts = (await self.session.execute(
            select(recent.c.project_id, TestFailure.name, func.count(func.distinct(TestFailure.run_id)),
                   array_agg(aggregate_order_by(recent.c.ref, recent.c.created_at.asc()))[1])
            .join(TestFailure, TestFailure.run_id == recent.c.id)
            .group_by(recent.c.project_id, TestFailure.name)
            .limit(MAX_LIMIT * 4))).all()
        return ({pid: int(n) for pid, n in sizes},
                {(pid, name): (int(n), first) for pid, name, n, first in counts})

    async def coverage(self, run_ids: Sequence[str]) -> list[TestCoverage]:
        if not run_ids:
            return []
        stmt = (select(TestCoverage).where(TestCoverage.run_id.in_(run_ids))
                .order_by(TestCoverage.run_id, TestCoverage.path).limit(MAX_LIMIT))
        return list((await self.session.execute(stmt)).scalars())


class ExpectationRepository(Repository[TestExpectation]):
    model = TestExpectation

    async def put(self, *, expectation_id: str, project_id: str, test_name: str, kind: str, reason: str,
                  by_user_id: str | None) -> TestExpectation:
        """One standing word per test per project: a second one replaces the first, in one statement."""
        stmt = (insert(TestExpectation)
                .values(id=expectation_id, project_id=project_id, test_name=test_name, kind=kind, reason=reason,
                        by_user_id=by_user_id)
                .on_conflict_do_update(index_elements=[TestExpectation.project_id, TestExpectation.test_name],
                                       set_={"kind": kind, "reason": reason, "by_user_id": by_user_id,
                                             "updated_at": func.now()})
                .returning(TestExpectation.id))
        kept = (await self.session.execute(stmt)).scalar_one()
        return (await self.session.execute(
            select(TestExpectation).where(TestExpectation.id == kept)
            .execution_options(populate_existing=True))).scalar_one()

    async def remove_for(self, project_id: str, test_name: str) -> int:
        return await self.remove_where(TestExpectation.project_id == project_id,
                                       TestExpectation.test_name == test_name)

    async def of_projects(self, project_ids: Sequence[str], *, limit: int = 200
                          ) -> list[tuple[TestExpectation, str | None]]:
        if not project_ids:
            return []
        stmt = (select(TestExpectation, User.name)
                .outerjoin(User, User.id == TestExpectation.by_user_id)
                .where(TestExpectation.project_id.in_(project_ids))
                .order_by(TestExpectation.updated_at.desc()).limit(bounded(limit)))
        return [(e, name) for e, name in (await self.session.execute(stmt)).all()]
