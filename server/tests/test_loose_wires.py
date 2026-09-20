"""The wires between one builder's work and another's: started, linked, clamped, named.

Each test here is about a line that belongs in a file its author did not own, and every one of them is
the same kind of defect — code that is written, tested and correct, and never reached. A prune nothing
starts. A link nothing writes. A floor the schema insists on and the writer does not apply. A counter
row naming a prefix nobody asks for.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Gateway, Provider
from app.ai.ledger import MemoryLedger
from app.api.app import create_api
from app.repositories import ApprovalRepository
from app.repositories.runtime import ResultsRepository
from app.secrets import Secrets
from app.services import maintenance
from app.settings import SERVER_DIR, Settings

#: A database on a server that is there, with a name that is not: the app starts, and every chore it
#: takes says so and carries on. Nothing here needs a database — it needs the app's lifespan.
NOWHERE = "postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode_does_not_exist"


# ── the daily prune ──────────────────────────────────────────────
class Counted:
    """`Housekeeping`, counted. Its own tick is tested where it is written (test_history_speed.py);
    what is in question here is whether anything ever calls it."""

    made: list[Counted] = []

    def __init__(self, db, *, config=None, clock=None) -> None:
        self.db, self.config, self.ticks = db, config, 0
        self.ticked = asyncio.Event()
        Counted.made.append(self)

    async def tick(self) -> None:
        self.ticks += 1
        self.ticked.set()


@pytest.fixture
def housekeeping(monkeypatch: pytest.MonkeyPatch) -> list[Counted]:
    Counted.made = []
    monkeypatch.setattr(maintenance, "Housekeeping", Counted)
    return Counted.made


async def running(config: Settings):
    """The app, through its own lifespan, against a database that is not there."""
    from app.data.engine import Database

    return create_api(db=Database(url=NOWHERE, config=config), config=config)


async def test_the_daily_prune_is_started_by_the_app_that_promises_it(
        settings: Settings, tmp_path: Path, housekeeping: list[Counted]):
    """The retention settings promise history is pruned once a day. `Housekeeping` was written and
    tested to do it and nothing in the app ever started it, so the only thing that ever removed a row
    was an administrator pressing the button — and the promise was kept by hand or not at all."""
    config = settings.model_copy(update={"database_url": NOWHERE, "secrets_path": tmp_path / "s.json",
                                         "scheduler": False, "prune_daily": True})
    api = await running(config)
    async with api.router.lifespan_context(api):
        # The loop is a task, so it is built on the lifespan's first pause, not on the line that made it.
        for _ in range(200):
            if housekeeping:
                break
            await asyncio.sleep(0.01)
        assert housekeeping, "nothing built the housekeeping the settings promise"
        await asyncio.wait_for(housekeeping[0].ticked.wait(), 5)

    assert housekeeping[0].ticks >= 1
    assert housekeeping[0].config is config      # its own settings, not a second reading of the file


async def test_turning_the_daily_prune_off_leaves_no_loop_behind(
        settings: Settings, tmp_path: Path, housekeeping: list[Counted]):
    """NEUROCODE_PRUNE_DAILY=false is an off switch: no prune, and no loop waking for ever to decide
    it has nothing to do."""
    config = settings.model_copy(update={"database_url": NOWHERE, "secrets_path": tmp_path / "s.json",
                                         "scheduler": False, "prune_daily": False})
    api = await running(config)
    async with api.router.lifespan_context(api):
        await asyncio.sleep(0.05)                # long enough for a first tick, if there were one

    assert housekeeping == []


async def test_a_pass_that_fails_is_not_the_end_of_the_housekeeping():
    """A database that is not answering yet is a thing to say once and try again — the same discipline
    the routines' loop keeps. A loop that ends on the first failure never prunes again until a restart."""
    import logging

    from app.api.app import _housekeep

    # Listen on the logger itself rather than through caplog: another test in this suite configures logging
    # for a live uvicorn, and a logger that no longer propagates would make a warning that was written look
    # like one that never was.
    said: list[str] = []

    class Heard(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            said.append(record.getMessage())

    heard = Heard()
    logger = logging.getLogger("app.api.app")
    was, disabled, off = logger.level, logging.root.manager.disable, logger.disabled
    logger.addHandler(heard)
    logger.setLevel(logging.WARNING)             # a level another test raised would filter it before us
    logger.disabled = False                      # uvicorn's dictConfig disables every logger it does not name
    logging.disable(logging.NOTSET)

    class Broken(Counted):
        async def tick(self) -> None:
            await super().tick()
            raise RuntimeError("the database did not answer")

    keeper: list[Broken] = []

    class Made(Broken):
        def __init__(self, db, *, config=None, clock=None) -> None:
            super().__init__(db, config=config, clock=clock)
            keeper.append(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(maintenance, "Housekeeping", Made)
        loop = asyncio.create_task(_housekeep(None, Settings(), every=0.01))
        # Wait for the second pass rather than for a stretch of time: on a busy machine a fixed sleep is a
        # coin toss, and what is being proved here is that a pass after a failure happens at all.
        for _ in range(500):
            if keeper and keeper[0].ticks > 1:
                break
            await asyncio.sleep(0.01)
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)

    logger.removeHandler(heard)
    logger.setLevel(was)
    logger.disabled = off
    logging.disable(disabled)
    assert keeper and keeper[0].ticks > 1, "the loop stopped at the first failure"
    assert any("housekeeping" in m for m in said), said


# ── a gate and its run ───────────────────────────────────────────
async def test_a_gate_is_written_with_the_run_it_belongs_to(engine, schema: str):
    """The gate a person is waiting on carries the link, not only the label.

    On its own connection, committed, because `_pause` opens a session of its own — which is also the
    only way to see what the run really stopped with.
    """
    from app.data.engine import Database
    from app.services.runs import _pause

    db = Database(schema)
    try:
        async with db.session() as s:
            s.add(m.Project(id="p-wires", name="Loose wires", kind="greenfield", status="active"))
            await s.flush()
            s.add(m.Run(id="r-wires", ref="RUN-9101", project_id="p-wires", branch="neurocode/wires",
                        worktree="/nowhere", repo="/nowhere", base="abc1234", requested_by="Rajat",
                        status="running"))
            await s.flush()
            s.add(m.RunStep(run_id="r-wires", n=1, kind="test", label="Run the project's tests",
                            agent="QA Engineer"))

        await _pause(db, "RUN-9101", 1, title="Run the project's tests?", tool="Tests(pytest)",
                     risk="HIGH", payload="pytest -q", reason="The project's own test command.")

        async with db.read() as s:
            run = (await s.execute(select(m.Run).where(m.Run.ref == "RUN-9101"))).scalar_one()
            gate = await s.get(m.Approval, "ap-run-9101-1-" + run.waiting_on.rsplit("-", 1)[-1])
            found = await ApprovalRepository(s).waiting_on_person("RUN-9101")
            every = await ApprovalRepository(s).for_run("RUN-9101")

        assert gate is not None and gate.ref == run.waiting_on
        assert gate.run_id == "r-wires"                      # the link, which nothing used to write
        assert gate.run_ref == "RUN-9101"                    # and the label, which people read
        assert gate.seq == int(gate.ref.rsplit("-", 1)[-1])  # the number in the ref, to order by
        # And the inbox finds it: both readers now ask by the link, so a gate without one is a gate
        # nobody could decide.
        assert found is not None and found.ref == gate.ref
        assert [a.ref for a in every] == [gate.ref]
    finally:
        async with db.session() as s:
            await s.execute(delete(m.Approval).where(m.Approval.run_ref == "RUN-9101"))
            await s.execute(delete(m.Project).where(m.Project.id == "p-wires"))
        await db.engine.dispose()


async def test_a_gate_is_found_by_its_run_and_not_by_a_name_a_run_may_reuse(seeded: AsyncSession):
    """The link is the answer, and the label is only a label.

    A workspace carried in from elsewhere writes its rows without the counters, so `RUN-1` can be
    handed out a second time — after which matching the text found another run's gate, and missed the
    one that was really waiting.
    """
    seeded.add(m.Run(id="r-link", ref="RUN-9102", project_id="erp", branch="b", worktree="/nowhere",
                     repo="/nowhere", base="abc1234", requested_by="Rajat", status="waiting"))
    await seeded.flush()
    seeded.add(m.Approval(id="ap-link", ref="APPR-900", title="Deploy to production?", risk="HIGH",
                          status="pending", project_id="erp", run_id="r-link", run_ref="RUN-1",
                          step=1, seq=900))
    await seeded.flush()

    gates = ApprovalRepository(seeded)
    assert (await gates.waiting_on_person("RUN-9102")).ref == "APPR-900"
    assert [a.ref for a in await gates.for_run("RUN-9102")] == ["APPR-900"]
    # The stale label belongs to no run here, so it finds nothing rather than someone else's gate.
    assert await gates.waiting_on_person("RUN-1") is None


async def test_who_decided_a_test_gate_is_read_through_the_same_link(seeded: AsyncSession):
    """The standing answer on the Testing screen is credited to a person by joining the gate to its
    run. That join is the gate's `run_id` now, so a gate written today is one it can find at all."""
    seeded.add(m.User(id="u-wires", email="dev@example.com", name="Rajat", password_hash="x"))
    seeded.add(m.Run(id="r-decided", ref="RUN-9103", project_id="erp", branch="b", worktree="/nowhere",
                     repo="/nowhere", base="abc1234", requested_by="Rajat", status="running"))
    await seeded.flush()
    seeded.add(m.RunStep(run_id="r-decided", n=2, kind="test", label="Run the tests", agent="QA"))
    # The label is a stale one, as it is on any workspace carried in with its refs already used: only
    # the link says which run this gate belongs to.
    seeded.add(m.Approval(id="ap-decided", ref="APPR-901", title="Run the tests?", risk="HIGH",
                          status="approved", project_id="erp", run_id="r-decided", run_ref="RUN-1",
                          step=2, seq=901, decided_by="u-wires", decided_at=datetime.now(UTC)))
    await seeded.flush()

    deciders = await ResultsRepository(seeded).gate_deciders(["erp"])
    who, when = deciders[("erp", "approved")]
    assert who == "Rajat" and when is not None


# ── the ledger's floor ───────────────────────────────────────────
NONSENSE = {"in": -40, "out": "12", "cached": 9_000, "reasoning": 500}


def test_a_provider_reporting_nonsense_still_has_its_call_recorded(tmp_path: Path):
    """A negative count, a count as text, a cached part bigger than the whole prompt.

    The ledger's table refuses every one of these outright, and `_record` swallows what the ledger
    raises so that a ledger outage can never fail the feature it measures — so believing a provider
    here did not price a call wrongly, it lost the line altogether, and the spend for the day quietly
    read low. Each count is taken to the nearest number that can be true instead.
    """
    gateway = Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))
    gateway._record("chat", Provider("deepseek", "deepseek-chat"), True, -3, NONSENSE, None, None)

    line = gateway.store.calls[-1]
    assert line["tokens_in"] == 0                     # a count below zero is no count
    assert line["tokens_out"] == 12                   # a count sent as text is still a count
    assert line["tokens_cached"] == 0                 # never more of the prompt than the prompt
    assert line["tokens_reasoning"] == 12             # never more of the answer than the answer
    assert line["ms"] == 0
    assert line["feature"] == "chat" and line["ok"] is True


async def test_the_floor_the_ledger_is_given_is_the_floor_the_table_insists_on(seeded: AsyncSession):
    """The other half of the same line: what the clamp produces is exactly what the schema allows, and
    what a provider said is exactly what it refuses. Asserted against the real constraints, because a
    clamp agreeing with a constraint that has been imagined is worth nothing."""
    from sqlalchemy.exc import IntegrityError

    seeded.add(m.AiCall(feature="chat", lane="deepseek", model="deepseek-chat", ok=True, ms=0,
                        tokens_in=0, tokens_out=12, tokens_cached=0, tokens_reasoning=12))
    await seeded.flush()                                   # the clamped line goes in

    with pytest.raises(IntegrityError):
        async with seeded.begin_nested():
            seeded.add(m.AiCall(feature="chat", lane="deepseek", model="deepseek-chat", ok=True, ms=0,
                                tokens_in=-40, tokens_out=12, tokens_cached=9_000, tokens_reasoning=500))
            await seeded.flush()                           # the provider's own numbers do not


# ── the counters ─────────────────────────────────────────────────
def _counter_migration():
    """The migration that seeds the counters, loaded by path — a revision file is not an importable module."""
    import glob
    import importlib.util

    path = glob.glob(str(SERVER_DIR / "alembic/versions/a17f3c92d4be*.py"))[0]
    spec = importlib.util.spec_from_file_location("counter_seed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_every_counter_names_a_prefix_some_repository_asks_for(session: AsyncSession):
    """`ref_counters` is where a person looks to find out where a number came from.

    The first seed named `SESSION-`, which nothing asks for — `ChatRepository` asks for `CHAT-` — and left
    `EVAL-` out. Nothing broke: a counter with no row seeds itself from its own table's highest ref the
    first time one is asked for. The rows were simply not true, which is its own kind of broken.

    The migration's own statements are run here, in this test's transaction, rather than read back from the
    database: `test_cli_live` truncates every table on the shared test database, so what a migration
    committed is not there to find. What is proved is the thing that matters — those statements, against a
    workspace, leave a row for every prefix the code asks for and none for a prefix it does not.
    """
    from app.repositories.evals import EvalRunRepository
    from app.repositories.runtime import ChatRepository

    assert ChatRepository(session).next_ref.__defaults__[0] == "CHAT-"
    assert EvalRunRepository(session).next_ref.__defaults__[0] == "EVAL-"
    await session.execute(insert(m.RefCounter).values(prefix="SESSION-", next=7))
    for statement in _counter_migration().statements():
        await session.execute(text(statement))

    held = {c.prefix for c in (await session.execute(select(m.RefCounter))).scalars()}
    assert {"CHAT-", "EVAL-"} <= held
    assert "SESSION-" not in held


async def test_a_seeded_counter_carries_on_from_the_refs_its_table_already_holds(seeded: AsyncSession):
    """The point of seeding one at all: a workspace with sessions in it does not start again at 1."""
    for statement in _counter_migration().statements():
        await seeded.execute(text(statement))

    highest = (await seeded.execute(select(m.Chat.ref))).scalars().all()
    counter = await seeded.get(m.RefCounter, "CHAT-")
    assert counter is not None
    assert counter.next >= max((int(ref.rsplit("-", 1)[-1]) for ref in highest), default=0)
