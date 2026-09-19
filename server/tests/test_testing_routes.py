"""The Testing screen, from what a runner prints to what the screen reads.

The parsers are checked against real output: each sample at the bottom of this file was captured from
the runner it names, run on a small project with a pass, a failure and a skip, with only the machine's
own path shortened to /work/shop. The routes are checked over real HTTP against real Postgres, with a
real git repository where a command is detected — and a run's rows are written by hand, because what
is under test here is the reading: which run is latest, how often a test failed, who may say a failure
is expected, and that a project that never ran its tests shows no numbers at all.
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app import onboarding
from app.agent import coverage, testparse
from app.api import deps
from app.api.app import create_api
from app.data.engine import Database
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ENGINEER = {"email": "dev@example.com", "name": "Dev", "password": "another long passphrase",
            "roles": ["engineer"]}                        # runs:run, but not decisions:make
HEADERS = {"X-NC-Client": "test"}
PID, BARE = "tlab", "tbare"
ROOT = ("/work/shop",)


# ── reading what a runner printed ────────────────────────────────

def test_pytest_totals_and_every_failure_with_its_own_line():
    found = testparse.parse(PYTEST_Q, ROOT)
    assert (found.runner, found.passed, found.failed, found.skipped, found.total) == ("pytest", 2, 3, 1, 6)
    assert [(f.name, f.file, f.line) for f in found.failures] == [
        ("tests/test_core.py::test_total", "tests/test_core.py", 8),
        ("tests/test_core.py::TestTax::test_param[2]", "tests/test_core.py", 13),
        ("tests/test_core.py::test_error", "tests/test_core.py", 19),     # an error at setup is red too
    ]
    assert found.failures[0].message == "assert 1.1 == 1.05"
    assert "E        +  where 1.1 = total(1.05)" in found.failures[0].excerpt
    assert found.failures[2].message == "fixture 'tmp' not found"


def test_a_pytest_collection_error_is_named_by_its_file():
    found = testparse.parse(PYTEST_COLLECTION, ROOT)
    assert (found.passed, found.failed, found.total) == (0, 1, 1)
    [failure] = found.failures
    assert (failure.name, failure.file, failure.line) == ("tests/test_core.py", "tests/test_core.py", 2)
    assert "No module named 'pkg'" in failure.message


def test_jest_counts_tests_and_names_the_suites_that_did_not_run():
    found = testparse.parse(JEST, ROOT)
    assert (found.runner, found.passed, found.failed, found.skipped, found.total) == ("jest", 2, 1, 1, 4)
    names = [(f.name, f.line) for f in found.failures]
    assert names[0] == ("src/tax.test.js › tax › rounds", 5)
    assert ("src/broken.test.js › Test suite failed to run", 1) in names and len(names) == 4
    assert found.failures[0].message.startswith("expect(received).toBe(expected)")


def test_vitest():
    found = testparse.parse(VITEST, ROOT)
    assert (found.runner, found.passed, found.failed, found.skipped, found.total) == ("vitest", 2, 1, 1, 4)
    [failure] = found.failures
    assert (failure.name, failure.file, failure.line) == ("vt/tax.test.js > tax > rounds", "vt/tax.test.js", 6)
    assert failure.message == "AssertionError: expected 1.1 to be 1.05 // Object.is equality"


def test_go_names_failures_but_claims_no_totals_without_verbose_output():
    found = testparse.parse(GO, ROOT, module="example.com/shop")
    assert found.runner == "go" and found.total is None and found.passed is None
    assert [(f.name, f.file, f.line, f.message) for f in found.failures] == [
        ("example.com/shop/tax.TestGst", "tax/tax_test.go", 14, "Total(200) = 236, want 200"),
        ("example.com/shop/tax.TestSub/half", "tax/tax_test.go", 24, "half broke"),   # the subtest, not its parent
        ("example.com/shop/broken [build failed]", "broken/b.go", 3,
         'broken/b.go:3:23: cannot use "a" (untyped string constant) as int value in return statement'),
    ]


def test_go_keeps_a_failure_apart_from_a_passing_namesake_in_another_package():
    """Keyed by bare name, the TestNew that passed in package b overwrote the one that failed in package a:
    no failure, and a total of one."""
    out = ("=== RUN   TestNew\n    new_test.go:12: want 2, got 3\n--- FAIL: TestNew (0.00s)\nFAIL\n"
           "FAIL\texample.com/m/a\t0.012s\n"
           "=== RUN   TestNew\n--- PASS: TestNew (0.00s)\nPASS\nok  \texample.com/m/b\t0.010s\n")
    found = testparse.parse(out, (), "example.com/m")
    assert (found.passed, found.failed, found.total) == (1, 1, 2)
    assert [(f.name, f.file, f.line) for f in found.failures] == [("example.com/m/a.TestNew", "a/new_test.go", 12)]

def test_go_verbose_output_carries_its_totals():
    found = testparse.parse(GO_VERBOSE, ROOT, module="example.com/shop")
    assert (found.passed, found.failed, found.skipped, found.total) == (2, 2, 1, 5)


def test_dotnet():
    found = testparse.parse(DOTNET, ROOT)
    assert (found.runner, found.passed, found.failed, found.skipped, found.total) == ("dotnet", 1, 1, 1, 3)
    [failure] = found.failures
    assert (failure.name, failure.file, failure.line) == ("Shop.Tests.TaxTests.Rounds", "Shop.Tests/UnitTest1.cs", 14)
    assert failure.message == "Assert.Equal() Failure: Values differ"


def test_output_in_no_known_shape_reports_nothing_rather_than_zero():
    found = testparse.parse("building ledger…\nall good\nmake: *** [test] Error 2\n", ROOT)
    assert found == testparse.Report()
    assert found.failed is None and found.total is None and not found.failures


def test_a_colour_code_does_not_hide_a_summary():
    found = testparse.parse("\x1b[31m1 failed\x1b[0m, \x1b[32m3 passed\x1b[0m in 0.20s\n", ROOT)
    assert (found.passed, found.failed, found.total) == (3, 1, 4)


# ── reading the coverage a runner wrote ──────────────────────────

def test_coverage_reports_in_each_format_land_on_the_same_paths():
    assert coverage.cobertura(COBERTURA, ROOT) == {"pkg/__init__.py": (0, 0), "pkg/core.py": (3, 6)}
    assert coverage.lcov(LCOV, ROOT) == {"src/tax.js": (2, 3)}
    assert coverage.istanbul(ISTANBUL, ROOT) == {"src/tax.js": (2, 3)}
    assert coverage.go_profile(GO_PROFILE, "example.com/shop") == {"ok/ok.go": (1, 1), "tax/tax.go": (3, 3)}


def test_a_project_with_no_source_has_no_checkout_and_so_no_test_command():
    # Path("") is the current directory: a project never onboarded from here used to answer with the
    # API's own folder, and so with the API's own test command.
    for source in (None, {"kind": None, "repo": ""}, {"kind": "local", "repo": "  "}):
        assert onboarding.source_root({"id": "p", "source": source}) is None
    assert onboarding.source_root({"id": "p", "source": {"kind": "git", "repo": "x"}}) == onboarding.REPOS_DIR / "p"


def test_coverage_outside_the_project_is_dropped_and_sums_by_directory():
    outside = "SF:/etc/secrets.py\nLF:4\nLH:4\nend_of_record\nSF:../other/x.js\nLF:2\nLH:1\nend_of_record\n"
    assert coverage.lcov(outside, ROOT) == {}
    assert coverage.cobertura('<!DOCTYPE lol [<!ENTITY a "a">]><coverage/>', ROOT) == {}
    assert coverage.by_directory({"pkg/a.py": (3, 4, "lcov"), "pkg/b.py": (1, 4, "lcov"), "setup.py": (0, 2, "lcov")}) \
        == [("", 0, 2, "lcov"), ("pkg", 4, 8, "lcov")]


# ── the routes ───────────────────────────────────────────────────

@pytest_asyncio.fixture
async def api(session: AsyncSession, schema: str) -> AsyncIterator[FastAPI]:
    await load_workspace(session)
    app = create_api(db=None)
    # A started run is handed to a job with a database of its own: the test database, never the real one.
    jobs_db = Database(url=schema)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    app.dependency_overrides[deps.database] = lambda: jobs_db
    yield app
    await jobs_db.close()


def _client(api: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest_asyncio.fixture
async def lab(client: AsyncClient, session: AsyncSession, tmp_path: Path) -> Path:
    """Two onboarded projects — one with tests on record, one that never ran any — and one with no code here."""
    repo = tmp_path / "lab"
    repo.mkdir()
    (repo / "Makefile").write_text("test:\n\t@echo testing\n")
    git(["init", "-b", "main"], repo)
    git(["add", "-A"], repo)
    git(["commit", "-m", "first"], repo)
    bare = tmp_path / "bare"
    bare.mkdir()
    session.add_all([m.Project(id=PID, name="Test Lab", source_kind="local", source_repo=str(repo)),
                     m.Project(id=BARE, name="Bare", source_kind="local", source_repo=str(bare))])
    await session.flush()

    day = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)

    def run(ref: str, hours: int, **fields: object) -> m.Run:
        return m.Run(id=ref.lower(), ref=ref, project_id=PID, branch=f"neurocode/{ref.lower()}", worktree="/gone",
                     repo=str(repo), status="failed", created_at=day + timedelta(hours=hours), **fields)

    session.add_all([
        run("RUN-701", 0, role="solo", agent=None, tests_status="failed", tests_command="make test",
            tests_passed=10, tests_failed=1, tests_skipped=0, tests_total=11, tests_sha="a" * 40, tests_runner="pytest"),
        run("RUN-702", 1, role="check", requested_by="Rajat", tests_status="failed", tests_command="make test",
            tests_passed=9, tests_failed=2, tests_skipped=1, tests_total=12, tests_sha="b" * 40, tests_runner="pytest",
            removed=True),
        # Its test step was skipped: it ran nothing, so it is nobody's latest result.
        run("RUN-703", 2, role="solo", tests_status="not run"),
    ])
    await session.flush()
    session.add_all([
        m.RunStep(run_id="run-701", n=1, kind="test", label="Run the project's tests", status="failed", ms=4_200),
        m.RunStep(run_id="run-702", n=1, kind="test", label="Run the project's tests", status="failed", ms=3_900),
        m.RunStep(run_id="run-703", n=1, kind="test", label="Run the project's tests", status="skipped"),
        m.TestFailure(run_id="run-701", step_n=1, name="tests/test_tax.py::test_round", file="tests/test_tax.py",
                      line=12, message="assert 1.1 == 1.05", excerpt="E   assert 1.1 == 1.05"),
        m.TestFailure(run_id="run-702", step_n=1, name="tests/test_tax.py::test_round", file="tests/test_tax.py",
                      line=12, message="assert 1.1 == 1.05", excerpt="E   assert 1.1 == 1.05"),
        m.TestFailure(run_id="run-702", step_n=1, name="tests/test_gst.py::test_split", file="tests/test_gst.py",
                      line=30, message="KeyError: 'IGST'", excerpt="E   KeyError: 'IGST'"),
        m.TestCoverage(run_id="run-702", path="app", covered=410, total=520, source="cobertura"),
        m.TestCoverage(run_id="run-701", path="app", covered=1, total=520, source="cobertura"),
        m.RunLog(run_id="run-702", step=1, level="tool", line="FAILED tests/test_gst.py::test_split"),
        m.RunLog(run_id="run-702", step=None, level="ok", line="worktree ready"),
    ])
    await session.flush()
    return repo


async def test_the_report_reads_the_latest_run_that_really_tested(client: AsyncClient, lab: Path):
    body = (await client.get("/testing")).json()
    suites = {s["projectId"]: s for s in body["suites"]}
    assert set(suites) == {PID, BARE}                           # a project with no code has no tests to run
    lab_suite = suites[PID]
    assert lab_suite["command"] == "make test" and lab_suite["tool"] == "make" and lab_suite["allowed"] is None
    assert lab_suite["latest"] == {"passed": 9, "failed": 2, "skipped": 1, "total": 12, "runRef": "RUN-702",
                                   "status": "failed", "ms": 3_900, "sha": "b" * 40,
                                   "branch": "neurocode/run-702", "runner": "pytest",
                                   "allExpected": False, "unnamed": False,
                                   "at": "2026-09-10T10:00:00+00:00"}
    # Never tested and no command: nothing to show, and nothing made up.
    assert suites[BARE]["latest"] is None and suites[BARE]["command"] is None

    failures = {f["name"]: f for f in body["failures"]}
    assert set(failures) == {"tests/test_tax.py::test_round", "tests/test_gst.py::test_split"}
    assert failures["tests/test_tax.py::test_round"]["failedIn"] == 2
    assert failures["tests/test_tax.py::test_round"]["ofLast"] == 2
    assert failures["tests/test_tax.py::test_round"]["firstFailedRef"] == "RUN-701"
    assert failures["tests/test_gst.py::test_split"]["failedIn"] == 1
    assert failures["tests/test_gst.py::test_split"]["expectation"] is None

    assert [h["runRef"] for h in body["history"]] == ["RUN-702", "RUN-701"]
    assert body["history"][0]["trigger"] == "Rajat" and body["history"][1]["trigger"] == "QA Engineer"
    assert body["coverage"] == [{"runRef": "RUN-702", "projectId": PID, "path": "app", "covered": 410,
                                 "total": 520, "source": "cobertura"}]

    only = (await client.get(f"/testing?project={BARE}")).json()
    assert [s["projectId"] for s in only["suites"]] == [BARE] and only["history"] == []
    assert (await client.get("/testing?project=nope")).status_code == 404



async def test_a_suite_is_calm_only_by_the_gates_own_rule(client: AsyncClient, lab: Path, session: AsyncSession):
    """The screen once called a run calm when every listed failure had an expectation, while the gate — which
    also needs the runner's count read and every counted failure recorded — still raised the signature."""
    for name in ("tests/test_tax.py::test_round", "tests/test_gst.py::test_split"):
        saved = await client.put(f"/projects/{PID}/tests/expectations",
                                 json={"testName": name, "kind": "legacy", "reason": "Red on purpose."})
        assert saved.status_code in (200, 201), saved.text
    latest = {s["projectId"]: s for s in (await client.get("/testing")).json()["suites"]}[PID]["latest"]
    assert latest["allExpected"] is True and latest["unnamed"] is False

    # The runner counted a third failure that no line named: not calm, and not "nothing unexplained".
    run = (await session.execute(select(m.Run).where(m.Run.ref == "RUN-702"))).scalar_one()
    run.tests_failed = 3
    await session.flush()
    latest = {s["projectId"]: s for s in (await client.get("/testing")).json()["suites"]}[PID]["latest"]
    assert latest["allExpected"] is False and latest["unnamed"] is True

async def test_one_failure_comes_with_the_lines_its_step_kept(client: AsyncClient, lab: Path, session: AsyncSession):
    fid = (await session.execute(select(m.TestFailure.id).where(m.TestFailure.name == "tests/test_gst.py::test_split"))
           ).scalar_one()
    body = (await client.get(f"/testing/failures/{fid}")).json()
    assert body["runRef"] == "RUN-702" and body["line"] == 30 and body["projectName"] == "Test Lab"
    assert [line["line"] for line in body["log"]] == ["FAILED tests/test_gst.py::test_split"]
    assert (await client.get("/testing/failures/999999")).status_code == 404


async def test_marking_a_failure_expected_is_a_decision_and_is_audited(client: AsyncClient, api: FastAPI,
                                                                       lab: Path, session: AsyncSession):
    name = "tests/test_tax.py::test_round"
    too_short = await client.put(f"/projects/{PID}/tests/expectations",
                                 json={"testName": name, "kind": "legacy", "reason": "old"})
    assert too_short.status_code == 422

    made = await client.put(f"/projects/{PID}/tests/expectations",
                            json={"testName": name, "kind": "legacy", "reason": "Rounds half-down since 2019 on purpose."})
    assert made.status_code == 200, made.text
    assert made.json()["kind"] == "legacy" and made.json()["by"] == "Rajat"
    # Saying it again replaces the word rather than adding a second one.
    again = await client.put(f"/projects/{PID}/tests/expectations",
                             json={"testName": name, "kind": "quarantine", "reason": "Flaky on the shared runner."})
    assert again.json()["kind"] == "quarantine"

    body = (await client.get("/testing")).json()
    failure = next(f for f in body["failures"] if f["name"] == name)
    assert failure["expectation"]["kind"] == "quarantine" and failure["expectation"]["by"] == "Rajat"
    assert [e["testName"] for e in body["expectations"]] == [name]
    audited = (await session.execute(select(m.AuditEntry.action).where(m.AuditEntry.target == name))).scalars().all()
    assert audited.count("test.expect") == 2

    await client.post("/admin/users", json=ENGINEER)
    async with _client(api) as engineer:
        await engineer.post("/auth/login", json={"email": ENGINEER["email"], "password": ENGINEER["password"]})
        assert (await engineer.get("/testing")).status_code == 200
        refused = await engineer.put(f"/projects/{PID}/tests/expectations",
                                     json={"testName": name, "kind": "legacy", "reason": "I would like it green."})
        assert refused.status_code == 403 and "decisions:make" in refused.json()["detail"]
        assert (await engineer.delete(f"/projects/{PID}/tests/expectations",
                                      params={"testName": name})).status_code == 403

    removed = await client.delete(f"/projects/{PID}/tests/expectations", params={"testName": name})
    assert removed.status_code == 200 and removed.json() == {"ok": True}
    assert (await client.delete(f"/projects/{PID}/tests/expectations", params={"testName": name})).status_code == 404


async def test_starting_tests_makes_a_queued_check_run(client: AsyncClient, lab: Path, session: AsyncSession):
    started = await client.post(f"/projects/{PID}/tests")
    assert started.status_code == 200, started.text
    run = started.json()
    assert run["role"] == "check" and run["status"] == "queued" and run["agent"] is None
    assert run["branch"].startswith("neurocode/check-run-") and run["tests"]["command"] == "make test"
    assert [(s["kind"], s["agent"]) for s in run["steps"]] == [("test", "QA Engineer")]

    again = await client.post(f"/projects/{PID}/tests")
    assert again.status_code == 409 and "already running" in again.json()["detail"]

    fid = (await session.execute(select(m.TestFailure.id).limit(1))).scalar_one()
    assert (await client.post(f"/testing/failures/{fid}/rerun")).status_code == 409   # one at a time


async def test_a_project_without_a_test_command_or_without_code_is_refused_in_words(client: AsyncClient, lab: Path,
                                                                                   tmp_path: Path, session: AsyncSession):
    git(["init", "-b", "main"], tmp_path / "bare")
    (tmp_path / "bare" / "README").write_text("no tests here\n")
    git(["add", "-A"], tmp_path / "bare")
    git(["commit", "-m", "first"], tmp_path / "bare")
    none = await client.post(f"/projects/{BARE}/tests")
    assert none.status_code == 409 and "No test command was found in Bare" in none.json()["detail"]

    sample = (await session.execute(select(m.Project.id).where(m.Project.source_kind.is_(None)).limit(1))).scalar_one()
    refused = await client.post(f"/projects/{sample}/tests")
    assert refused.status_code == 409 and "no code on this machine" in refused.json()["detail"]


async def test_starting_tests_needs_runs_run_and_reading_needs_a_session(api: FastAPI, client: AsyncClient,
                                                                        lab: Path, session: AsyncSession):
    async with _client(api) as stranger:
        assert (await stranger.get("/testing")).status_code == 401
        assert (await stranger.post(f"/projects/{PID}/tests")).status_code == 401

    # Signed in, but holding nothing: both ways of starting a test run ask for runs:run by name.
    viewer = {"email": "vik@example.com", "name": "Vik", "password": "a viewer's password", "roles": ["viewer"]}
    assert (await client.post("/admin/users", json=viewer)).status_code == 201
    fid = (await session.execute(select(m.TestFailure.id).limit(1))).scalar_one()
    async with _client(api) as someone:
        await someone.post("/auth/login", json={"email": viewer["email"], "password": viewer["password"]})
        assert (await someone.get("/testing")).status_code == 200
        for path in (f"/projects/{PID}/tests", f"/testing/failures/{fid}/rerun"):
            refused = await someone.post(path)
            assert refused.status_code == 403 and "runs:run" in refused.json()["detail"], (path, refused.text)


# ── the real outputs, captured from each runner ──────────────────

PYTEST_Q = """.F.FsE                                                                   [100%]
==================================== ERRORS ====================================
_________________________ ERROR at setup of test_error _________________________
file /work/shop/tests/test_core.py, line 19
  def test_error(tmp):
E       fixture 'tmp' not found
>       available fixtures: _asyncio_loop_factory, _class_scoped_runner, _function_scoped_runner, _module_scoped_runner, _package_scoped_runner, _session_scoped_runner, anyio_backend, anyio_backend_name, anyio_backend_options, capfd, capfdbinary, caplog, capsys, capsysbinary, capteesys, doctest_namespace, event_loop_policy, free_tcp_port, free_tcp_port_factory, free_udp_port, free_udp_port_factory, monkeypatch, pytestconfig, record_property, record_testsuite_property, record_xml_attribute, recwarn, subtests, tmp_path, tmp_path_factory, tmpdir, tmpdir_factory, unused_tcp_port, unused_tcp_port_factory, unused_udp_port, unused_udp_port_factory
>       use 'pytest --fixtures [testpath]' for help on them.

/work/shop/tests/test_core.py:19
=================================== FAILURES ===================================
__________________________________ test_total __________________________________

    def test_total():
>       assert total(1.05) == 1.05
E       assert 1.1 == 1.05
E        +  where 1.1 = total(1.05)

tests/test_core.py:8: AssertionError
____________________________ TestTax.test_param[2] _____________________________

self = <test_core.TestTax object at 0x10b18c550>, v = 2

    @pytest.mark.parametrize("v", [1, 2])
    def test_param(self, v):
>       assert v == 1
E       assert 2 == 1

tests/test_core.py:13: AssertionError
=========================== short test summary info ============================
FAILED tests/test_core.py::test_total - assert 1.1 == 1.05
FAILED tests/test_core.py::TestTax::test_param[2] - assert 2 == 1
ERROR tests/test_core.py::test_error
2 failed, 2 passed, 1 skipped, 1 error in 0.01s
"""

PYTEST_COLLECTION = """
==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_core.py ______________________
ImportError while importing test module '/work/shop/tests/test_core.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/opt/python/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_core.py:2: in <module>
    from pkg.core import total
E   ModuleNotFoundError: No module named 'pkg'
=========================== short test summary info ============================
ERROR tests/test_core.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.03s
"""

JEST = """FAIL src/tax.test.js
  ● tax › rounds

    expect(received).toBe(expected) // Object.is equality

    Expected: 1.05
    Received: 1.1

      3 |   test('adds', () => { expect(total(1)).toBe(1); });
      4 |   test('rounds', () => {
    > 5 |     expect(total(1.05)).toBe(1.05);
        |                         ^
      6 |   });
      7 |   test.skip('later', () => {});
      8 | });

      at Object.toBe (src/tax.test.js:5:25)

FAIL vt/tax.test.js
  ● Test suite failed to run

    Jest encountered an unexpected token

    Jest failed to parse a file. This happens e.g. when your code or its dependencies use non-standard JavaScript syntax, or when Jest is not configured to support such syntax.

    Out of the box Jest supports Babel, which will be used to transform your files into valid JS based on your Babel configuration.

    By default "node_modules" folder is ignored by transformers.

    Here's what you can do:
     • If you are trying to use ECMAScript Modules, see https://jestjs.io/docs/ecmascript-modules for how to enable it.
     • If you are trying to use TypeScript, see https://jestjs.io/docs/getting-started#using-typescript
     • To have some of your "node_modules" files transformed, you can specify a custom "transformIgnorePatterns" in your config.
     • If you need a custom transformation specify a "transform" option in your config.
     • If you simply want to mock your non-JS modules (e.g. binary assets) you can stub them out with the "moduleNameMapper" config option.

    You'll find more details and examples of these config options in the docs:
    https://jestjs.io/docs/configuration
    For information about custom transformations, see:
    https://jestjs.io/docs/code-transformation

    Details:

    /work/shop/vt/tax.test.js:1
    ({"Object.<anonymous>":function(module,exports,require,__dirname,__filename,jest){import { describe, test, expect } from 'vitest';
                                                                                      ^^^^^^

    SyntaxError: Cannot use import statement outside a module

      at Runtime.createScriptFromCode (node_modules/jest-runtime/build/index.js:1505:14)

FAIL vt/ok.test.js
  ● Test suite failed to run

    Jest encountered an unexpected token

    Jest failed to parse a file. This happens e.g. when your code or its dependencies use non-standard JavaScript syntax, or when Jest is not configured to support such syntax.

    Out of the box Jest supports Babel, which will be used to transform your files into valid JS based on your Babel configuration.

    By default "node_modules" folder is ignored by transformers.

    Here's what you can do:
     • If you are trying to use ECMAScript Modules, see https://jestjs.io/docs/ecmascript-modules for how to enable it.
     • If you are trying to use TypeScript, see https://jestjs.io/docs/getting-started#using-typescript
     • To have some of your "node_modules" files transformed, you can specify a custom "transformIgnorePatterns" in your config.
     • If you need a custom transformation specify a "transform" option in your config.
     • If you simply want to mock your non-JS modules (e.g. binary assets) you can stub them out with the "moduleNameMapper" config option.

    You'll find more details and examples of these config options in the docs:
    https://jestjs.io/docs/configuration
    For information about custom transformations, see:
    https://jestjs.io/docs/code-transformation

    Details:

    /work/shop/vt/ok.test.js:1
    ({"Object.<anonymous>":function(module,exports,require,__dirname,__filename,jest){import { test, expect } from 'vitest';
                                                                                      ^^^^^^

    SyntaxError: Cannot use import statement outside a module

      at Runtime.createScriptFromCode (node_modules/jest-runtime/build/index.js:1505:14)

FAIL src/broken.test.js
  ● Test suite failed to run

    Cannot find module './nope' from 'src/broken.test.js'

    > 1 | const x = require('./nope');
        |                            ^
      2 | test('a', () => {});
      3 |

      at Resolver._throwModNotFoundError (node_modules/jest-resolve/build/resolver.js:427:11)
      at Object.<anonymous> (src/broken.test.js:1:28)

PASS src/ok.test.js

Test Suites: 4 failed, 1 passed, 5 total
Tests:       1 failed, 1 skipped, 2 passed, 4 total
Snapshots:   0 total
Time:        0.219 s, estimated 1 s
Ran all test suites.
"""

VITEST = """
 RUN  v2.1.9 /work/shop

 ✓ vt/ok.test.js (1 test) 1ms
 ❯ vt/tax.test.js (3 tests | 1 failed | 1 skipped) 3ms
   × tax > rounds 2ms
     → expected 1.1 to be 1.05 // Object.is equality

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  vt/tax.test.js > tax > rounds
AssertionError: expected 1.1 to be 1.05 // Object.is equality

- Expected
+ Received

- 1.05
+ 1.1

 ❯ vt/tax.test.js:6:25
      4|   test('adds', () => { expect(total(1)).toBe(1); });
      5|   test('rounds', () => {
      6|     expect(total(1.05)).toBe(1.05);
       |                         ^
      7|   });
      8|   test.skip('later', () => {});

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯

 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 2 passed | 1 skipped (4)
   Start at  15:34:41
   Duration  131ms (transform 14ms, setup 0ms, collect 11ms, tests 4ms, environment 0ms, prepare 65ms)

"""

GO = """# example.com/shop/broken
broken/b.go:3:23: cannot use "a" (untyped string constant) as int value in return statement
FAIL	example.com/shop/broken [build failed]
ok  	example.com/shop/ok	(cached)
--- FAIL: TestGst (0.00s)
    tax_test.go:14: Total(200) = 236, want 200
--- FAIL: TestSub (0.00s)
    --- FAIL: TestSub/half (0.00s)
        tax_test.go:24: half broke
FAIL
FAIL	example.com/shop/tax	0.228s
FAIL
"""

GO_VERBOSE = """=== RUN   TestOne
--- PASS: TestOne (0.00s)
PASS
ok  	example.com/shop/ok	0.205s
=== RUN   TestTotal
--- PASS: TestTotal (0.00s)
=== RUN   TestGst
    tax_test.go:14: Total(200) = 236, want 200
--- FAIL: TestGst (0.00s)
=== RUN   TestSkip
    tax_test.go:19: later
--- SKIP: TestSkip (0.00s)
=== RUN   TestSub
=== RUN   TestSub/half
    tax_test.go:24: half broke
--- FAIL: TestSub (0.00s)
    --- FAIL: TestSub/half (0.00s)
FAIL
FAIL	example.com/shop/tax	0.352s
FAIL
"""

DOTNET = """  Determining projects to restore...
  All projects are up-to-date for restore.
  Shop.Tests -> /work/shop/Shop.Tests/bin/Debug/net9.0/Shop.Tests.dll
Test run for /work/shop/Shop.Tests/bin/Debug/net9.0/Shop.Tests.dll (.NETCoreApp,Version=v9.0)
VSTest version 17.12.0 (arm64)

Starting test execution, please wait...
A total of 1 test files matched the specified pattern.
[xUnit.net 00:00:00.04]     Shop.Tests.TaxTests.Later [SKIP]
[xUnit.net 00:00:00.06]     Shop.Tests.TaxTests.Rounds [FAIL]
  Skipped Shop.Tests.TaxTests.Later [1 ms]
  Failed Shop.Tests.TaxTests.Rounds [8 ms]
  Error Message:
   Assert.Equal() Failure: Values differ
Expected: 1.05
Actual:   1
  Stack Trace:
     at Shop.Tests.TaxTests.Rounds() in /work/shop/Shop.Tests/UnitTest1.cs:line 14
   at System.RuntimeMethodHandle.InvokeMethod(Object target, Void** arguments, Signature sig, Boolean isConstructor)
   at System.Reflection.MethodBaseInvoker.InvokeWithNoArgs(Object obj, BindingFlags invokeAttr)

Failed!  - Failed:     1, Passed:     1, Skipped:     1, Total:     3, Duration: 16 ms - Shop.Tests.dll (net9.0)
"""

COBERTURA = """<?xml version="1.0" ?>
<coverage version="7.16.1" timestamp="1789553107460" lines-valid="6" lines-covered="3" line-rate="0.5" branches-covered="0" branches-valid="0" branch-rate="0" complexity="0">
	<!-- Generated by coverage.py: https://coverage.readthedocs.io/en/7.16.1 -->
	<!-- Based on https://raw.githubusercontent.com/cobertura/web/master/htdocs/xml/coverage-04.dtd -->
	<sources>
		<source>/work/shop/pkg</source>
	</sources>
	<packages>
		<package name="." line-rate="0.5" branch-rate="0" complexity="0">
			<classes>
				<class name="__init__.py" filename="__init__.py" complexity="0" line-rate="1" branch-rate="0">
					<methods/>
					<lines/>
				</class>
				<class name="core.py" filename="core.py" complexity="0" line-rate="0.5" branch-rate="0">
					<methods/>
					<lines>
						<line number="1" hits="1"/>
						<line number="2" hits="1"/>
						<line number="4" hits="1"/>
						<line number="5" hits="0"/>
						<line number="6" hits="0"/>
						<line number="7" hits="0"/>
					</lines>
				</class>
			</classes>
		</package>
	</packages>
</coverage>
"""

LCOV = """TN:
SF:src/tax.js
FN:1,total
FN:2,unused
FNF:2
FNH:1
FNDA:3,total
FNDA:0,unused
DA:1,3
DA:2,0
DA:3,2
LF:3
LH:2
BRDA:2,0,0,0
BRDA:2,0,1,0
BRF:2
BRH:0
end_of_record
"""

ISTANBUL = """{"total": {"lines":{"total":3,"covered":2,"skipped":0,"pct":66.66},"statements":{"total":5,"covered":2,"skipped":0,"pct":40},"functions":{"total":2,"covered":1,"skipped":0,"pct":50},"branches":{"total":2,"covered":0,"skipped":0,"pct":0},"branchesTrue":{"total":0,"covered":0,"skipped":0,"pct":"Unknown"}}
,"/work/shop/src/tax.js": {"lines":{"total":3,"covered":2,"skipped":0,"pct":66.66},"functions":{"total":2,"covered":1,"skipped":0,"pct":50},"statements":{"total":5,"covered":2,"skipped":0,"pct":40},"branches":{"total":2,"covered":0,"skipped":0,"pct":0}}
}
"""

GO_PROFILE = """mode: set
example.com/shop/ok/ok.go:3.16,3.28 1 1
example.com/shop/tax/tax.go:3.31,4.13 1 1
example.com/shop/tax/tax.go:4.13,6.3 1 1
example.com/shop/tax/tax.go:7.2,7.10 1 1
"""
