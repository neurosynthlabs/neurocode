"""The shared ground the screens' back ends stand on.

Each new table takes a representative row and refuses what its keys say it must refuse; the run vocabulary
has `check`; the usage ledger learns which run a model call was for; a restart fails the work it
interrupted and leaves the work parked on a person alone; and a remembered fact can say what it rests on.

The models named Test* are reached through the module, not imported by name, so pytest does not take
them for test classes.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai import gateway as gateway_module
from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api.app import INTERRUPTED, reconcile_interrupted
from app.secrets import Secrets
from app.services.knowledge import MemoryService, NewFact

PROJECT = "erp"


def a_run(ref: str, **fields: Any) -> m.Run:
    return m.Run(id=ref.lower(), ref=ref, project_id=PROJECT, branch=f"neurocode/{ref}",
                 worktree=f"/tmp/{ref}", repo="/tmp/repo", **fields)


#: SQLSTATE per kind of rule, so a row refused for some other reason — a NOT NULL, a bad enum — cannot
#: pass a test that is about a foreign key.
FOREIGN_KEY, UNIQUE, CHECK = "23503", "23505", "23514"


async def refused(session: AsyncSession, because: str, *rows: Any) -> None:
    """Adding these rows must fail in the database, for this reason and no other. Rolled back by hand: a
    refused statement aborts the transaction, and a savepoint Postgres has already thrown away cannot be
    released."""
    savepoint = await session.begin_nested()
    session.add_all(rows)
    with pytest.raises(DBAPIError) as raised:
        await session.flush()
    await savepoint.rollback()
    state = getattr(raised.value.orig, "sqlstate", None) or getattr(raised.value.orig, "pgcode", None)
    assert state == because, f"refused with SQLSTATE {state}, not {because}: {raised.value.orig}"


# ── the tables ───────────────────────────────────────────────────

async def test_every_new_table_takes_a_representative_row(seeded: AsyncSession):
    run = a_run("RUN-F1", role="check", status="done", tests_passed=40, tests_failed=1, tests_skipped=2,
                tests_total=43, tests_sha="a" * 40, tests_runner="pytest")
    run.failures.append(m.TestFailure(step_n=1, name="test_total", file="tests/test_tax.py", line=12,
                                      message="assert 1 == 2", excerpt="E   assert 1 == 2"))
    run.coverage.append(m.TestCoverage(path="app", covered=80, total=100, source="cobertura"))
    seeded.add(run)
    seeded.add(m.TestExpectation(id="te1", project_id=PROJECT, test_name="test_total", kind="legacy",
                                 reason="Red since the 2019 rounding change."))

    workflow = m.WorkflowDefinition(id="wf1", name="Add an endpoint", project_id=PROJECT,
                                    requirement_template="Add an endpoint for {input}")
    workflow.steps.append(m.WorkflowStep(n=1, label="Write the route", agent="Backend Engineer"))
    seeded.add(workflow)
    await seeded.flush()
    seeded.add(m.Plan(id="p-wf", ref="PLAN-9101", project_id=PROJECT, workflow_id="wf1"))

    suite = m.EvalSuite(id="es1", name="Compiler basics", target_kind="compile", project_id=PROJECT)
    suite.cases.append(m.EvalCase(id="ec1", n=1, name="rounding", input="Round tax to 2 places",
                                  checks=[{"kind": "contains", "value": "round"}]))
    seeded.add(suite)
    await seeded.flush()
    eval_run = m.EvalRun(id="er1", ref="EVAL-1", suite_id="es1", status="done", score=100, passed=1)
    eval_run.results.append(m.EvalResult(case_id="ec1", status="pass", score=Decimal("1.000"),
                                         checks=[{"kind": "contains", "ok": True, "observed": "round"}]))
    seeded.add(eval_run)

    report = m.ResearchReport(id="rr1", ref="RES-1", project_id=PROJECT, question="How is tax rounded?",
                              kinds=["code", "memory"], risks=["float drift"], alternatives=[])
    angle = m.ResearchAngle(n=1, question="Where is rounding done?", status="done", hits=1)
    # Added through the angle, in the same flush: the relationships order the inserts report → angle →
    # citation, and fill in angle_id. report_id is named by hand, as the model says it must be.
    angle.citations.append(m.ResearchCitation(report_id="rr1", n=1, kind="code", ref="pkg/core.py#total",
                                              path="pkg/core.py", line=1, excerpt="def total(x):"))
    report.angles.append(angle)
    seeded.add(report)
    await seeded.flush()

    assert (await seeded.execute(text("SELECT role::text, tests_total FROM runs WHERE id = 'run-f1'"))).one() \
        == ("check", 43)
    counts = (await seeded.execute(text(
        "SELECT (SELECT count(*) FROM test_failures WHERE run_id = 'run-f1'), "
        "(SELECT count(*) FROM test_coverage WHERE run_id = 'run-f1'), "
        "(SELECT count(*) FROM workflow_steps WHERE workflow_id = 'wf1'), "
        "(SELECT count(*) FROM eval_results WHERE run_id = 'er1'), "
        "(SELECT count(*) FROM research_citations WHERE report_id = 'rr1'), "
        "(SELECT kinds::text FROM research_reports WHERE id = 'rr1')"))).one()
    assert tuple(counts) == (1, 1, 1, 1, 1, "{code,memory}")


async def test_a_report_reads_every_kind_unless_told_otherwise(seeded: AsyncSession):
    seeded.add(m.ResearchReport(id="rr2", ref="RES-2", project_id=PROJECT, question="What calls total?"))
    await seeded.flush()
    kinds = (await seeded.execute(text("SELECT kinds::text FROM research_reports WHERE id = 'rr2'"))).scalar_one()
    assert kinds == "{code,doc,memory}"


async def test_the_new_keys_refuse_what_they_should(seeded: AsyncSession):
    # A foreign key: a failure cannot belong to a run that does not exist.
    await refused(seeded, FOREIGN_KEY, m.TestFailure(run_id="no-such-run", step_n=1, name="test_x"))

    # A unique key: one standing word per test per project.
    seeded.add(m.TestExpectation(id="te-a", project_id=PROJECT, test_name="test_flaky", kind="quarantine",
                                 reason="Times out on CI."))
    await seeded.flush()
    await refused(seeded, UNIQUE, m.TestExpectation(id="te-b", project_id=PROJECT, test_name="test_flaky",
                                            kind="legacy", reason="Said twice."))

    # Checks: coverage cannot exceed its total, and a case must check something.
    seeded.add(a_run("RUN-F2"))
    seeded.add(m.EvalSuite(id="es-k", name="Keys", target_kind="prompt"))
    await seeded.flush()
    await refused(seeded, CHECK, m.TestCoverage(run_id="run-f2", path="app", covered=11, total=10, source="lcov"))
    await refused(seeded, CHECK, m.EvalCase(id="ec-empty", suite_id="es-k", n=1, name="nothing", input="x", checks=[]))
    await refused(seeded, CHECK, m.WorkflowDefinition(id="wf-x", name="No slot", requirement_template="Fixed text"))
    await refused(seeded, CHECK, m.EvalSuite(id="es-x", name="Compile with nothing to read", target_kind="compile"))

    # Names compare without case, so two suites cannot differ by capitals alone.
    await refused(seeded, UNIQUE, m.EvalSuite(id="es-dup", name="KEYS", target_kind="prompt"))


async def test_the_ledger_forgets_a_run_rather_than_losing_its_lines(seeded: AsyncSession):
    seeded.add(a_run("RUN-F3"))
    await seeded.flush()
    seeded.add(m.AiCall(feature="agent", lane="groq", run_id="run-f3"))
    await seeded.flush()
    await seeded.execute(text("DELETE FROM runs WHERE id = 'run-f3'"))
    left = (await seeded.execute(text("SELECT run_id FROM ai_calls WHERE feature = 'agent' AND lane = 'groq' "
                                      "ORDER BY id DESC LIMIT 1"))).one()
    assert left == (None,)
    await refused(seeded, FOREIGN_KEY, m.AiCall(feature="agent", lane="groq", run_id="no-such-run"))


# ── the ledger knows the run ─────────────────────────────────────

def test_the_ledger_records_which_run_a_call_was_for(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NEUROCODE_COMPILER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setitem(gateway_module.CALLS, "groq",
                        lambda messages, cfg: ('{"ok": true}', {"in": 12, "out": 3}))
    ledger = MemoryLedger()
    gateway = Gateway(ledger, Secrets(tmp_path / "secrets.json"))

    result = gateway.ask([{"role": "user", "content": "hi"}], gateway_module.extract_json,
                         feature="agent", agent="Backend Engineer", run_id="run-7")
    assert result.data == {"ok": True}
    line = ledger.calls[-1]
    assert (line["run_id"], line["agent"], line["tokens_in"]) == ("run-7", "Backend Engineer", 12)

    gateway.ask([{"role": "user", "content": "hi"}], gateway_module.extract_json, feature="ask")
    assert ledger.calls[-1]["run_id"] is None


# ── start-up reconcile ───────────────────────────────────────────

async def test_a_restart_fails_what_it_interrupted_and_leaves_what_waits_on_a_person(seeded: AsyncSession):
    running = a_run("RUN-F4", status="running")
    running.steps.append(m.RunStep(n=1, kind="edit", label="Write it", status="running"))
    waiting = a_run("RUN-F5", status="waiting", waiting_on="APR-1")
    waiting.steps.append(m.RunStep(n=1, kind="test", label="Run the tests", status="waiting"))
    seeded.add_all([running, waiting, m.EvalSuite(id="es-r", name="Restart", target_kind="prompt")])
    await seeded.flush()
    seeded.add_all([m.EvalRun(id="er-q", ref="EVAL-Q", suite_id="es-r", status="queued"),
                    m.EvalRun(id="er-d", ref="EVAL-D", suite_id="es-r", status="done", score=90),
                    m.ResearchReport(id="rr-r", ref="RES-R", project_id=PROJECT, question="Still going?",
                                     status="running")])
    await seeded.flush()

    counts = await reconcile_interrupted(seeded)
    assert counts["runs"] >= 1 and counts["evals"] >= 1 and counts["research"] >= 1

    runs = dict((await seeded.execute(text(
        "SELECT ref, status::text FROM runs WHERE ref IN ('RUN-F4', 'RUN-F5')"))).all())
    assert runs == {"RUN-F4": "failed", "RUN-F5": "waiting"}
    steps = dict((await seeded.execute(text(
        "SELECT r.ref, s.status::text FROM run_steps s JOIN runs r ON r.id = s.run_id "
        "WHERE r.ref IN ('RUN-F4', 'RUN-F5')"))).all())
    assert steps == {"RUN-F4": "failed", "RUN-F5": "waiting"}
    said = (await seeded.execute(select(m.RunLog.line).where(m.RunLog.run_id == "run-f4"))).scalars().all()
    assert said == [INTERRUPTED]

    evals = dict((await seeded.execute(text(
        "SELECT ref, status::text || '|' || note FROM eval_runs WHERE suite_id = 'es-r'"))).all())
    assert evals == {"EVAL-Q": f"failed|{INTERRUPTED}", "EVAL-D": "done|"}
    assert (await seeded.execute(text("SELECT status::text FROM research_reports WHERE id = 'rr-r'"))) \
        .scalar_one() == "failed"


# ── memory evidence ──────────────────────────────────────────────

async def test_a_fact_can_say_what_it_rests_on(seeded: AsyncSession):
    evidence = [{"kind": "eval", "run": "EVAL-3", "case": "rounding"}]
    added = await MemoryService(seeded).add(
        [NewFact(title="Tax rounds half-up", body="The eval caught a banker's rounding.", evidence=evidence),
         NewFact(title="No evidence given", body="Plain.")],
        project_id=PROJECT, by="Rajat", source="eval:Compiler basics:EVAL-3")
    stored = (await seeded.execute(select(m.MemoryFact.ref, m.MemoryFact.evidence)
                                   .where(m.MemoryFact.id.in_([f.id for f in added]))
                                   .execution_options(populate_existing=True))).all()
    assert sorted(e for _, e in stored) == [[], evidence]


# ── the production ledger, not the test one ─────────────────────

async def test_the_postgres_ledger_writes_the_run_into_its_line(schema: str):
    """The in-memory ledger accepts any keyword, so a test through it proves the gateway threads run_id
    and nothing about the INSERT the app actually runs. This one goes through PostgresLedger."""
    from app.ai.ledger import PostgresLedger
    from app.data.engine import Database

    db = Database(url=schema)
    ledger = PostgresLedger(schema.replace("+asyncpg", "+psycopg"))
    try:
        async with db.session() as s:
            s.add(m.Project(id="ledger-p", name="Ledger"))
            await s.flush()
            s.add(m.Run(id="run-ledger", ref="RUN-LEDGER", project_id="ledger-p", branch="neurocode/ledger",
                        worktree="/tmp/ledger", repo="/tmp/repo"))
        ledger.record(feature="agent", lane="groq", model="llama", ok=True, ms=5, tokens_in=1, tokens_out=2,
                      user_id=None, project_id="ledger-p", agent="Backend Engineer", run_id="run-ledger",
                      error="")
        async with db.read() as s:
            row = (await s.execute(text("SELECT run_id, agent, tokens_out FROM ai_calls "
                                        "WHERE project_id = 'ledger-p'"))).one()
        assert tuple(row) == ("run-ledger", "Backend Engineer", 2)
    finally:
        async with db.session() as s:
            await s.execute(text("DELETE FROM ai_calls WHERE project_id = 'ledger-p'"))
            await s.execute(text("DELETE FROM runs WHERE project_id = 'ledger-p'"))
            await s.execute(text("DELETE FROM projects WHERE id = 'ledger-p'"))
        ledger.close()
        await db.close()
