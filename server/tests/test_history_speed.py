"""What a year of use costs, and does not now: references, gates, the feed, the chunks, and the prune.

Every test here is about a query whose cost grew with the workspace's whole history. They are written
against the real schema, inside the transaction the fixtures roll back, because the whole point of each
one is what the database actually does with it — a plan a fake database would not have.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.repositories import ApprovalRepository, TaskRepository
from app.repositories.runtime import ChatRepository
from app.services import maintenance
from app.services.maintenance import HISTORIES, Housekeeping, MaintenanceService
from app.settings import Settings


@pytest.fixture
def statements() -> Iterator[list[str]]:
    """Every statement the database is asked, while this test runs. The engine is the async one's own
    synchronous engine underneath, which is where SQLAlchemy's events fire."""
    seen: list[str] = []

    def watch(conn, cursor, statement, parameters, context, executemany):   # SQLAlchemy's own shape
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", watch)
    yield seen
    event.remove(Engine, "before_cursor_execute", watch)


# ── references ───────────────────────────────────────────────────
async def test_a_reference_is_one_statement_and_reads_no_table(seeded: AsyncSession, statements: list[str]):
    """The counter is the whole answer: no scan, no regular expression, no advisory lock."""
    tasks = TaskRepository(seeded)
    first = await tasks.next_ref()
    statements.clear()
    second = await tasks.next_ref()

    assert int(second.removeprefix("TASK-")) == int(first.removeprefix("TASK-")) + 1
    asked = [s for s in statements if "ref_counters" in s or "FROM tasks" in s]
    assert len(asked) == 2                                  # the bump, and the one probe for a taken ref
    assert not any("regexp_replace" in s for s in statements)
    assert not any("pg_advisory" in s for s in statements)
    counter = await seeded.get(m.RefCounter, "TASK-")
    assert counter is not None and counter.next == int(second.removeprefix("TASK-"))


async def test_a_counter_that_does_not_exist_starts_from_the_refs_that_do(seeded: AsyncSession):
    """A workspace carried over from the old stack, or a prefix the migration's seed never named: the
    first ref of its kind is taken from the table, once, rather than starting again at 1."""
    await seeded.execute(delete(m.RefCounter).where(m.RefCounter.prefix == "TASK-"))
    highest = max(int(t.ref.removeprefix("TASK-")) for t in await TaskRepository(seeded).list(limit=500))

    assert await TaskRepository(seeded).next_ref() == f"TASK-{highest + 1}"


async def test_a_counter_left_behind_by_rows_written_around_it_catches_up(seeded: AsyncSession):
    """Rows loaded straight into the table cannot make the next reference collide with one of them."""
    tasks = TaskRepository(seeded)
    taken = (await tasks.list(limit=500))[0].ref
    # Ask once so the counter exists — the fixture holds tasks, not counters, and another test truncates
    # what earlier ones committed — then put it back behind a number the table already holds.
    await tasks.next_ref()
    counter = await seeded.get(m.RefCounter, "TASK-")
    assert counter is not None
    counter.next = int(taken.removeprefix("TASK-")) - 1      # the very next number is one already in use
    await seeded.flush()

    ref = await TaskRepository(seeded).next_ref()
    assert ref != taken
    assert await TaskRepository(seeded).by_ref(ref) is None


async def test_gates_start_at_a_hundred_and_never_hand_out_the_same_number_twice(catalogued: AsyncSession):
    """The floor used to be applied after the counter, so the first two gates were both APPR-101."""
    gates = ApprovalRepository(catalogued)
    refs = [await gates.next_ref() for _ in range(3)]
    numbers = [int(r.removeprefix("APPR-")) for r in refs]

    assert len(set(refs)) == 3 and numbers == [numbers[0], numbers[0] + 1, numbers[0] + 2]
    assert numbers[0] > 100                          # gates never read like a task, however new the workspace


async def test_every_kind_of_reference_keeps_its_own_count(catalogued: AsyncSession):
    """One counter per prefix: a session's number is not moved on by a task's."""
    first = await TaskRepository(catalogued).next_ref()
    session_ref = await ChatRepository(catalogued).next_ref()
    second = await TaskRepository(catalogued).next_ref()

    assert session_ref.startswith("SESSION-") or session_ref.startswith("CHAT-")
    assert int(second.removeprefix("TASK-")) == int(first.removeprefix("TASK-")) + 1


# ── the gates' list ──────────────────────────────────────────────
async def test_the_gate_list_orders_by_the_stored_number_not_the_text(seeded: AsyncSession):
    """Gates written in one transaction share a timestamp, so the order needs a second key — and that
    key used to be a regular expression over the ref, which no index could serve."""
    same_moment = datetime.now(UTC) + timedelta(days=1)      # after everything the fixture holds
    for n in (9, 10, 11):
        seeded.add(m.Approval(id=f"ap-order-{n}", ref=f"APPR-{200 + n}", seq=200 + n, title=f"Gate {n}",
                              status="pending", created_at=same_moment))
    await seeded.flush()

    listed = [a.ref for a in (await ApprovalRepository(seeded).pending(limit=3)).items]
    assert listed == ["APPR-211", "APPR-210", "APPR-209"]    # by number, newest first — not by text


# ── the feed's figures ───────────────────────────────────────────
async def test_the_feed_figures_come_back_from_one_pass(seeded: AsyncSession, statements: list[str]):
    """The totals and the rollup by kind are one statement over the log, not two. The figures are
    still the log's own, over every row: nothing here silently became "today"."""
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    from app.repositories import ActivityRepository

    events = await ActivityRepository(seeded).list(limit=500)
    statements.clear()
    figures = await ActivityRepository(seeded).summary(person="Rajat", day_start=day_start)

    assert len([s for s in statements if "FROM activity" in s]) == 2      # the rollup, and the agents
    assert figures["total"] == len(events)
    assert sum(figures["byKind"].values()) == len(events)
    assert figures["byKind"]["agent"] == sum(1 for e in events if e.actor_kind == "agent")
    assert figures["through"] == max(e.seq for e in events)


# ── how long history is kept ─────────────────────────────────────
async def test_retention_counts_what_is_past_its_keeping_before_anything_goes(seeded: AsyncSession):
    """The number on the button is counted, not estimated — and a history set to "keep" reports none."""
    old = datetime.now(UTC) - timedelta(days=400)
    run = m.Run(id="r-prune", ref="RUN-9001", project_id="erp", branch="b", worktree="/nowhere",
                repo="/nowhere", base="abc1234", requested_by="Rajat")
    seeded.add(run)
    await seeded.flush()
    seeded.add_all([m.RunLog(run_id=run.id, level="info", line=f"old line {n}", at=old) for n in range(7)])
    seeded.add(m.RunLog(run_id=run.id, level="info", line="today's line"))
    seeded.add(m.AiCall(feature="chat", lane="groq", model="llama", ok=True, at=old))
    await seeded.flush()

    config = Settings(run_log_days=90, ledger_days=0)
    by_table = {row["table"]: row for row in await MaintenanceService(seeded, config).retention()}

    assert by_table["run_logs"]["rows"] == 7 and by_table["run_logs"]["days"] == 90
    assert by_table["run_logs"]["cutoff"] is not None
    assert by_table["ai_calls"]["rows"] == 0 and by_table["ai_calls"]["cutoff"] is None   # kept, so none
    assert {h.table for h in HISTORIES} == set(by_table)
    assert "audit_log" not in by_table            # append-only: the database itself refuses the delete


async def test_the_prune_removes_only_what_is_past_its_keeping_in_bounded_chunks(
        engine, schema, statements: list[str]):
    """Committed as it goes, a few thousand rows to a statement, so nothing else waits behind it.

    On a connection of its own rather than the rolled-back fixture transaction, because that is the
    whole point: one DELETE over a year of run output is a transaction holding row locks on millions of
    rows while everything that writes a log line queues behind it. It cleans up after itself.
    """
    from app.data.engine import Database

    db = Database(schema)
    old, recent = datetime.now(UTC) - timedelta(days=400), datetime.now(UTC) - timedelta(days=2)
    try:
        async with db.session() as s:
            s.add(m.Project(id="p-prune", name="Prune", kind="greenfield", status="active"))
            await s.flush()          # runs point at it by a key, with no relationship to order the two
            run = m.Run(id="r-prune-2", ref="RUN-9002", project_id="p-prune", branch="b",
                        worktree="/nowhere", repo="/nowhere", base="abc1234", requested_by="Rajat")
            s.add(run)
            await s.flush()
            s.add_all([m.RunLog(run_id=run.id, level="info", line=f"old {n}", at=old) for n in range(250)])
            s.add_all([m.RunLog(run_id=run.id, level="info", line=f"new {n}", at=recent) for n in range(3)])

        config = Settings(database_url=schema, run_log_days=90, prune_rows=100)
        statements.clear()
        async with db.session() as s:
            done = await MaintenanceService(s, config).prune(db.engine)

        by_table = {row["table"]: row for row in done["tables"]}
        assert by_table["run_logs"]["removed"] == 250 and done["removed"] == 250
        assert by_table["run_logs"]["note"] == ""
        # Three statements of a hundred, not one of two hundred and fifty: the chunk is the promise.
        assert len([x for x in statements if x.startswith("DELETE FROM \"run_logs\"")]) == 3

        async with db.read() as s:
            left = list((await s.execute(select(m.RunLog.line).where(m.RunLog.run_id == run.id))).scalars())
        assert sorted(left) == ["new 0", "new 1", "new 2"]
    finally:
        async with db.session() as s:
            await s.execute(delete(m.Project).where(m.Project.id == "p-prune"))
        await db.engine.dispose()


async def test_the_daily_prune_is_claimed_once_a_day(engine, schema, monkeypatch: pytest.MonkeyPatch):
    """Three API processes ticking, or one restarted three times: the day is claimed by one statement,
    so the history is pruned once between them."""
    from app.data.engine import Database

    db = Database(schema)
    day = "2026-09-20"
    try:
        async with db.session() as s:
            await s.execute(delete(m.Setting).where(m.Setting.key == maintenance.PRUNED_KEY))
        keeper = Housekeeping(db, config=Settings(database_url=schema))
        async with db.session() as s:
            assert await keeper.claim(s, day) is True
        async with db.session() as s:
            assert await keeper.claim(s, day) is False            # same day, second process
        async with db.session() as s:
            assert await keeper.claim(s, "2026-09-21") is True    # tomorrow

        off = Housekeeping(db, config=Settings(database_url=schema, prune_daily=False))
        assert await off.tick() is None
    finally:
        async with db.session() as s:
            await s.execute(delete(m.Setting).where(m.Setting.key == maintenance.PRUNED_KEY))
        await db.engine.dispose()


# ── rebuilding the chunks ────────────────────────────────────────
def _chunk(ref: str, body: str) -> dict[str, object]:
    return {"kind": "code", "ref": ref, "path": ref.split("#")[0], "line": 1, "title": ref, "body": body}


async def test_a_rebuild_only_touches_the_chunks_that_changed(seeded: AsyncSession):
    """Deleting a scope and writing it again re-entered every unchanged chunk into the full-text and
    HNSW indexes, and asked the embedding model to price the same text over again. The vector a chunk
    already carries is the proof: it survives a rebuild that did not change its text, and only a chunk
    whose text really changed loses it."""
    from app.services.retrieval import RetrievalService

    service = RetrievalService(seeded, gateway=None)      # nothing here reaches a model
    first = await service._replace("erp", [_chunk("a.py#one", "one"), _chunk("b.py#two", "two")])
    assert first.total == 2 and len(first.fresh) == 2 and first.embedded == 0

    for chunk in first.fresh:                             # as a lane would leave them
        chunk.embedding, chunk.dim, chunk.model = [0.5] * 1536, 1536, "test-embed"
    await seeded.flush()
    kept_id = next(c.id for c in first.fresh if c.ref == "a.py#one")

    again = await service._replace("erp", [_chunk("a.py#one", "one"), _chunk("b.py#two", "TWO, changed"),
                                           _chunk("c.py#three", "three")])
    assert again.total == 3
    assert {c.ref for c in again.fresh} == {"b.py#two", "c.py#three"}   # only what is new or changed
    assert again.embedded == 1                                         # the one nobody touched
    assert again.gone == 0

    unchanged = await seeded.get(m.Chunk, kept_id)
    assert unchanged is not None and unchanged.embedding is not None   # the same row, not written again
    changed = next(c for c in again.fresh if c.ref == "b.py#two")
    assert changed.embedding is None and changed.model == ""           # its vector described other words

    gone = await service._replace("erp", [_chunk("a.py#one", "one")])
    assert gone.total == 1 and gone.gone == 2 and gone.fresh == []
