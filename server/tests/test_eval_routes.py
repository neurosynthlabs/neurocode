"""Evals, over HTTP and in the runner behind it.

The routes are tested inside the rolled-back transaction, with the background job replaced by a
recorder: what a route promises is that the run is queued, committed and handed off — not what a model
says. The runner is tested on its own against committed rows, because it opens database sessions of
its own and cannot see another transaction's uncommitted writes. No model is ever called: the gateway
is a stand-in that answers as it is told and remembers how it was asked.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import NoModel, Provider, Result
from app.api import deps
from app.api.app import create_api
from app.data.engine import Database
from app.data.loader import load_seed, sync_roles
from app.services import evals as eval_jobs
from app.services.evals import Answer, EvalService, check
from app.services.identity import Person

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ENGINEER = {"email": "dev@example.com", "name": "Dev", "password": "another long passphrase", "roles": ["engineer"]}
VIEWER = {"email": "view@example.com", "name": "Vee", "password": "a third long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
PROMPT = {"name": "Tax words", "targetKind": "prompt", "kind": "regression",
          "systemPrompt": 'Reply as JSON: {"answer": "..."}', "threshold": 80}
CASE = {"name": "names IGST", "input": "What tax applies interstate?",
        "checks": [{"kind": "contains", "value": "IGST"}, {"kind": "json_equals", "path": "$.risk", "value": "HIGH"}]}


class FakeGateway:
    """Answers with what it is told, and keeps every call's keyword arguments."""

    def __init__(self, answers: list[str] | None = None, *, raises: Exception | None = None,
                 on_ask: Callable[[], None] | None = None) -> None:
        self.answers = list(answers or [])
        self.raises = raises
        self.on_ask = on_ask
        self.calls: list[dict[str, Any]] = []

    def report(self) -> list[dict[str, Any]]:
        return [{"id": "groq", "label": "Groq", "ready": True, "blocked": None}]

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, str]], parse: Any, **kwargs: Any) -> Result[Any]:
        self.calls.append(kwargs)
        if self.on_ask is not None:
            self.on_ask()
        if self.raises is not None:
            raise self.raises
        return Result(parse(self.answers.pop(0)), Provider("groq", "llama-3.3-70b-versatile"), 40)

    def run(self, messages: list[dict[str, str]], parse: Any, fallback: Any, **kwargs: Any) -> Result[Any]:
        self.calls.append(kwargs)
        return Result(fallback(), Provider("rules", "offline planner"), 3)


# ── over HTTP ────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def api(session: AsyncSession) -> FastAPI:
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
    built = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    built.dependency_overrides[deps.session] = use_the_test_session
    built.dependency_overrides[deps.gateway] = lambda: FakeGateway()
    return built


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """What was handed to the runner. The job itself is tested below, against committed rows."""
    handed: list[list[str]] = []

    async def record(_db: Any, _gw: Any, refs: list[str]) -> None:
        handed.append(refs)

    monkeypatch.setattr(eval_jobs, "execute", record)
    return handed


def _client(api: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def test_an_empty_workspace_says_so_rather_than_showing_scores(client: AsyncClient):
    body = (await client.get("/evals")).json()
    assert body["suites"] == [] and body["lessons"] == [] and body["trend"] == {}
    assert body["lanes"] == [{"id": "groq", "label": "Groq", "ready": True, "blocked": None}]


async def test_a_suite_is_written_checked_and_never_scored_until_it_runs(client: AsyncClient):
    made = await client.post("/evals/suites", json=PROMPT)
    assert made.status_code == 201
    suite = made.json()
    assert suite["status"] == "never" and suite["score"] is None and suite["delta"] is None
    assert suite["target"] == "Lane prompt" and suite["cases"] == 0 and suite["caseRows"] == []
    assert suite["run"] is None

    added = await client.post(f"/evals/suites/{suite['id']}/cases", json=CASE)
    assert added.status_code == 201
    case = added.json()
    assert case["expected"] == "contains “IGST”; $.risk = HIGH"
    assert case["status"] is None and case["judge"] == "rule · contains, json_equals"

    listed = (await client.get("/evals")).json()["suites"]
    assert [(s["name"], s["cases"], s["status"]) for s in listed] == [("Tax words", 1, "never")]


async def test_what_a_suite_or_a_case_cannot_be(client: AsyncClient):
    assert (await client.post("/evals/suites", json={"name": "Plans", "targetKind": "compile"})).status_code == 422
    assert (await client.post("/evals/suites", json={"name": "Bare", "targetKind": "prompt"})).status_code == 422
    assert (await client.post("/evals/suites", json={**PROMPT, "lane": "nowhere"})).status_code == 422
    sid = (await client.post("/evals/suites", json=PROMPT)).json()["id"]
    assert (await client.post("/evals/suites", json={**PROMPT, "name": "TAX WORDS"})).status_code == 409

    def with_checks(*checks: dict[str, Any]) -> dict[str, Any]:
        return {**CASE, "checks": list(checks)}
    cases = f"/evals/suites/{sid}/cases"
    assert (await client.post(cases, json=with_checks({"kind": "regex", "value": "(unclosed"}))).status_code == 422
    assert (await client.post(cases, json=with_checks({"kind": "json_equals", "path": "risk", "value": 1}))).status_code == 422
    assert (await client.post(cases, json=with_checks())).status_code == 422
    refused = await client.post(cases, json=with_checks({"kind": "cites", "ref": "MEM-142"}))
    assert refused.status_code == 422 and "ask" in refused.json()["detail"]


async def test_running_queues_commits_and_hands_off_and_cannot_run_twice(client: AsyncClient,
                                                                        queued: list[list[str]]):
    sid = (await client.post("/evals/suites", json=PROMPT)).json()["id"]
    empty = await client.post(f"/evals/suites/{sid}/run")
    assert empty.status_code == 409 and "no cases" in empty.json()["detail"]
    await client.post(f"/evals/suites/{sid}/cases", json=CASE)

    started = await client.post(f"/evals/suites/{sid}/run", json={"lane": "gemini"})
    assert started.status_code == 202
    run = started.json()
    assert run["status"] == "queued" and run["lane"] == "gemini" and run["score"] is None
    assert queued == [[run["ref"]]]
    assert (await client.post(f"/evals/suites/{sid}/run")).status_code == 409
    assert (await client.get("/evals")).json()["suites"][0]["status"] == "running"

    stopped = await client.post(f"/evals/runs/{run['ref']}/cancel")
    assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
    assert "Stopped by Rajat" in stopped.json()["note"]
    assert (await client.post(f"/evals/runs/{run['ref']}/cancel")).status_code == 409
    assert (await client.get("/evals")).json()["suites"][0]["status"] == "never"   # a stop never scores


async def test_run_all_queues_only_what_can_run(client: AsyncClient, queued: list[list[str]]):
    ready = (await client.post("/evals/suites", json=PROMPT)).json()["id"]
    await client.post(f"/evals/suites/{ready}/cases", json=CASE)
    await client.post("/evals/suites", json={**PROMPT, "name": "No cases yet"})
    await client.post("/evals/suites", json={**PROMPT, "name": "Safety words", "kind": "safety"})

    assert (await client.post("/evals/run-all", json={"kind": "safety"})).status_code == 409
    body = (await client.post("/evals/run-all")).json()
    assert len(body["runRefs"]) == 1 and queued == [body["runRefs"]]


async def test_who_may_do_what(api: FastAPI, client: AsyncClient, queued: list[list[str]]):
    sid = (await client.post("/evals/suites", json=PROMPT)).json()["id"]
    await client.post(f"/evals/suites/{sid}/cases", json=CASE)
    await client.post("/admin/users", json=VIEWER)
    await client.post("/admin/users", json=ENGINEER)

    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get(f"/evals/{sid}")).status_code == 200
        denied = await viewer.post("/evals/suites", json={**PROMPT, "name": "Mine"})
        assert denied.status_code == 403 and "evals:write" in denied.json()["detail"]
        assert (await viewer.post(f"/evals/suites/{sid}/run")).status_code == 403
        assert queued == []
    async with _client(api) as engineer:
        await engineer.post("/auth/login", json={"email": ENGINEER["email"], "password": ENGINEER["password"]})
        assert (await engineer.post("/evals/suites", json={**PROMPT, "name": "Theirs"})).status_code == 201
        assert (await engineer.post(f"/evals/suites/{sid}/run")).status_code == 202
    async with _client(api) as stranger:
        assert (await stranger.get("/evals")).status_code == 401


async def test_a_compiler_case_is_drafted_from_a_plan_a_person_accepted(client: AsyncClient,
                                                                         session: AsyncSession):
    sid = (await client.post("/evals/suites", json={"name": "Compiler", "targetKind": "compile",
                                                     "projectId": "erp"})).json()["id"]
    draft = await client.post(f"/evals/suites/{sid}/cases/from-plan", json={"planRef": "PLAN-501"})
    assert draft.status_code == 200
    body = draft.json()
    plan = (await session.execute(select(m.Plan).where(m.Plan.ref == "PLAN-501"))).scalar_one()
    assert body["input"] == plan.raw_requirement and body["sourcePlanId"] == plan.id
    assert body["checks"][0] == {"kind": "json_equals", "path": "$.risk", "value": "HIGH"}
    assert [c["value"] for c in body["checks"][1:]] == list(dict.fromkeys(s.agent for s in plan.steps if s.agent))[:6]
    assert (await client.get(f"/evals/{sid}")).json()["caseRows"] == []          # a draft, nothing saved

    other = await client.post(f"/evals/suites/{sid}/cases/from-plan", json={"planRef": "PLAN-503"})
    assert other.status_code == 422                                           # hims, not erp
    saved = await client.post(f"/evals/suites/{sid}/cases", json=body)
    assert saved.status_code == 201 and saved.json()["sourcePlanId"] == plan.id


async def _finished_runs(session: AsyncSession, suite_id: str, case_id: str,
                         scores: list[tuple[str, int, str, str]]) -> list[m.EvalResult]:
    """Runs that really finished, oldest first: (ref, run score, case status, case score)."""
    made = []
    start = datetime(2026, 9, 1, tzinfo=UTC)
    for i, (ref, score, status, case_score) in enumerate(scores):
        session.add(m.EvalRun(id=f"er-{ref}", ref=ref, suite_id=suite_id, status="done", score=score,
                              passed=int(status == "pass"), failed=int(status == "fail"),
                              finished_at=start + timedelta(hours=i)))
        await session.flush()
        result = m.EvalResult(run_id=f"er-{ref}", case_id=case_id, status=status, score=Decimal(case_score),
                              output='{"answer": "CGST and SGST"}',
                              checks=[{"kind": "contains", "ok": status == "pass", "observed": "“IGST” absent"}],
                              lane="groq", model="llama-3.3-70b-versatile", ms=812)
        session.add(result)
        await session.flush()
        made.append(result)
    return made


async def test_scores_move_against_the_run_before_and_a_person_can_disagree(client: AsyncClient,
                                                                           session: AsyncSession):
    suite = (await client.post("/evals/suites", json=PROMPT)).json()
    case = (await client.post(f"/evals/suites/{suite['id']}/cases", json=CASE)).json()
    _, latest = await _finished_runs(session, suite["id"], case["id"],
                                     [("EVAL-1", 100, "pass", "1.000"), ("EVAL-2", 50, "partial", "0.500")])

    listed = (await client.get("/evals")).json()
    row = listed["suites"][0]
    assert (row["score"], row["delta"], row["status"], row["lastRunRef"]) == (50, -50, "fail", "EVAL-2")
    assert listed["trend"][suite["id"]] == [100, 50]

    detail = (await client.get(f"/evals/{suite['id']}")).json()
    assert detail["run"]["ref"] == "EVAL-2" and detail["comparedWith"] == "EVAL-1"
    shown = detail["caseRows"][0]
    assert shown["status"] == "partial" and shown["scoreDelta"] == -50 and shown["got"] == "“IGST” absent"
    older = (await client.get(f"/evals/{suite['id']}", params={"run": "EVAL-1"})).json()
    assert older["caseRows"][0]["status"] == "pass" and older["comparedWith"] is None
    assert (await client.get(f"/evals/{suite['id']}", params={"run": "EVAL-404"})).status_code == 404

    bare = await client.post(f"/evals/results/{latest.id}/override", json={"status": "pass"})
    assert bare.status_code == 422                                            # say why
    overridden = await client.post(f"/evals/results/{latest.id}/override",
                                   json={"status": "pass", "note": "CGST+SGST is right for this state"})
    assert overridden.status_code == 200
    body = overridden.json()
    assert body["status"] == "pass" and body["machineStatus"] == "partial"
    assert body["judge"] == "Human override — CGST+SGST is right for this state"
    assert (await client.get("/evals")).json()["suites"][0]["score"] == 50   # the computed score stays

    taken_back = (await client.post(f"/evals/results/{latest.id}/override", json={"status": None})).json()
    assert taken_back["status"] == "partial" and taken_back["overridden"] is False


async def test_a_lesson_goes_to_memory_where_the_features_will_read_it(client: AsyncClient, session: AsyncSession):
    suite = (await client.post("/evals/suites", json=PROMPT)).json()
    case = (await client.post(f"/evals/suites/{suite['id']}/cases", json=CASE)).json()
    (result,) = await _finished_runs(session, suite["id"], case["id"], [("EVAL-7", 0, "fail", "0.000")])

    saved = await client.post(f"/evals/results/{result.id}/lesson",
                              json={"text": "Interstate supply is always IGST. Never split it into CGST and SGST."})
    assert saved.status_code == 201
    ref = saved.json()["ref"]
    fact = (await session.execute(select(m.MemoryFact).where(m.MemoryFact.ref == ref))).scalar_one()
    assert fact.source == "eval:Tax words:EVAL-7" and fact.category == "bugs" and fact.project_id is None
    assert fact.evidence == ["EVAL-7 · Tax words · names IGST"]         # words, as every fact's evidence is

    lessons = (await client.get("/evals")).json()["lessons"]
    assert [(x["ref"], x["from"], x["runRef"]) for x in lessons] == [(ref, "Tax words", "EVAL-7")]
    found = (await client.get("/memory", params={"q": "interstate IGST"})).json()
    assert ref in [f["ref"] for f in found]


# ── the runner ───────────────────────────────────────────────────

PROJECT = "eval-runner-project"


@pytest_asyncio.fixture
async def live(schema: str) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(m.Project(id=PROJECT, name="Eval Runner"))
    yield db
    async with db.session() as s:
        await s.execute(delete(m.EvalSuite).where(m.EvalSuite.name.like("runner %")))
        await s.execute(delete(m.ActivityEvent).where(m.ActivityEvent.detail.like("%runner %")))
        await s.execute(delete(m.Project).where(m.Project.id == PROJECT))
    await db.close()


SOMEONE = Person(id="nobody", email="", name="Rajat", status="active", roles=("owner",),
                 permissions=frozenset({"evals:write", "ai:use"}))


async def _suite(db: Database, *, target: str = "prompt", allow_offline: bool = False,
                 cases: list[list[dict[str, Any]]]) -> tuple[str, str]:
    """A committed suite and a queued run of it: (suite id, run ref)."""
    name = f"runner {target} {allow_offline} {len(cases)}"
    async with db.session() as s:
        suite = m.EvalSuite(id=f"es-{name.replace(' ', '-')}", name=name, target_kind=target,
                            project_id=PROJECT, system_prompt="Reply as JSON.", allow_offline=allow_offline)
        for n, checks in enumerate(cases, start=1):
            suite.cases.append(m.EvalCase(id=f"{suite.id}-{n}", n=n, name=f"case {n}", input=f"question {n}",
                                          checks=checks))
        s.add(suite)
        await s.flush()
        run = m.EvalRun(id=f"er-{suite.id}", ref=f"EVAL-R-{suite.id}", suite_id=suite.id,
                        status="queued", lane="groq", results=[])
        s.add(run)
        return suite.id, run.ref


async def _run(db: Database, ref: str) -> tuple[m.EvalRun, list[m.EvalResult]]:
    async with db.read() as s:
        run = (await s.execute(select(m.EvalRun).where(m.EvalRun.ref == ref))).scalar_one()
        results = list((await s.execute(select(m.EvalResult).where(m.EvalResult.run_id == run.id)
                                        .order_by(m.EvalResult.id))).scalars())
        return run, results


async def test_a_run_scores_its_cases_and_the_judge_is_a_labelled_call_of_its_own(live: Database):
    _, ref = await _suite(live, cases=[
        [{"kind": "contains", "value": "IGST"}, {"kind": "judge", "rubric": "Names the interstate tax."}],
        [{"kind": "json_equals", "path": "$.risk", "value": "high"}, {"kind": "not_contains", "value": "CGST"}],
    ])
    gateway = FakeGateway(['{"answer": "IGST applies", "risk": "HIGH"}',
                           '{"pass": true, "reason": "it names IGST"}',
                           '{"answer": "CGST", "risk": "LOW"}'])

    await eval_jobs.execute(live, gateway, [ref])

    run, results = await _run(live, ref)
    assert run.status == "done" and run.score == 50 and (run.passed, run.failed) == (1, 1)
    assert [r.status for r in results] == ["pass", "fail"]
    assert results[0].judge_model == "llama-3.3-70b-versatile" and results[0].judge_reason == "it names IGST"
    assert results[1].checks == [{"kind": "json_equals", "ok": False, "observed": "LOW"},
                                 {"kind": "not_contains", "ok": False, "observed": "“CGST” present"}]
    assert [c["feature"] for c in gateway.calls] == ["eval", "eval-judge", "eval"]
    assert gateway.calls[0]["lane"] == "groq" and gateway.calls[1]["avoid"] == "groq"


async def test_no_model_is_an_error_never_an_answer(live: Database):
    _, ref = await _suite(live, cases=[[{"kind": "contains", "value": "IGST"}]])

    await eval_jobs.execute(live, FakeGateway(raises=NoModel("No model is configured.")), [ref])

    run, (result,) = await _run(live, ref)
    assert run.status == "failed" and run.errored == 1 and "Every case errored" in run.note
    assert result.status == "error" and result.output == "" and "No model could answer" in result.error


@pytest.mark.parametrize("allow_offline", [False, True])
async def test_the_offline_rules_count_only_where_the_suite_says_they_may(live: Database, allow_offline: bool):
    _, ref = await _suite(live, target="compile", allow_offline=allow_offline,
                          cases=[[{"kind": "json_contains", "path": "$.steps[*].agent", "value": "QA Engineer"}]])

    await eval_jobs.execute(live, FakeGateway(), [ref])

    run, (result,) = await _run(live, ref)
    assert result.offline is True and result.lane == "rules"
    if allow_offline:
        assert result.status == "pass" and run.score == 100 and "offline rules" in run.note
    else:
        assert result.status == "error" and "offline rules" in result.error and run.status == "failed"


async def test_stopping_a_run_drops_the_answer_in_flight_and_never_asks_again(live: Database):
    _, ref = await _suite(live, cases=[[{"kind": "contains", "value": "a"}]] * 3)
    asked, release = threading.Event(), threading.Event()

    def hold() -> None:
        asked.set()
        release.wait(10)

    gateway = FakeGateway(['{"answer": "a"}'] * 3, on_ask=hold)
    job = asyncio.create_task(eval_jobs.execute(live, gateway, [ref]))
    assert await asyncio.to_thread(asked.wait, 10)
    async with live.session() as s:
        await EvalService(s, gateway).cancel(ref, SOMEONE)
    release.set()
    await job

    run, results = await _run(live, ref)
    assert run.status == "cancelled" and run.score is None
    assert results == [] and len(gateway.calls) == 1


def test_the_checks_say_what_they_saw():
    answer = Answer(output='{"steps": [{"agent": "Architect"}, {"agent": "QA Engineer"}]}',
                    data={"steps": [{"agent": "Architect"}, {"agent": "QA Engineer"}]},
                    refs=["MEM-1", "MEM-142", "code:a.py"], lane="deepseek", ms=900)
    assert check({"kind": "json_contains", "path": "$.steps[*].agent", "value": "qa engineer"}, answer)[0]
    assert check({"kind": "json_equals", "path": "$.steps[1].agent", "value": "Architect"}, answer) == (False, "QA Engineer")
    assert check({"kind": "retrieves", "ref": "MEM-142", "k": 2}, answer) == (True, "rank 2")
    assert check({"kind": "retrieves", "ref": "code:a.py", "k": 2}, answer) == (False, "not in top 2")
    assert check({"kind": "regex", "value": r"QA \w+"}, answer) == (True, "QA Engineer")
    assert check({"kind": "free_lane"}, answer) == (False, "deepseek is paid")
    assert check({"kind": "max_ms", "value": 500}, answer) == (False, "900 ms")


#: Every route that changes something, and the permission it must ask for. The ids do not exist: the
#: permission is checked before anything is looked up, so a refusal here can only be the gate.
GATES = [
    ("patch", "/evals/suites/es-none", "evals:write"),
    ("delete", "/evals/suites/es-none", "evals:write"),
    ("post", "/evals/suites/es-none/cases", "evals:write"),
    ("post", "/evals/suites/es-none/cases/from-plan", "evals:write"),
    ("patch", "/evals/cases/ec-none", "evals:write"),
    ("delete", "/evals/cases/ec-none", "evals:write"),
    ("post", "/evals/results/1/override", "evals:write"),
    ("post", "/evals/run-all", "ai:use"),
    ("post", "/evals/runs/EVAL-0/cancel", "ai:use"),
    ("post", "/evals/results/1/lesson", "memory:write"),
]


@pytest.mark.parametrize(("method", "path", "permission"), GATES)
async def test_a_viewer_is_refused_every_change_by_its_own_gate(api: FastAPI, client: AsyncClient,
                                                                queued: list[list[str]],
                                                                method: str, path: str, permission: str):
    """Only two of these gates were ever tested; a route that forgot its require() would have passed."""
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        kwargs = {} if method == "delete" else {"json": {}}
        refused = await getattr(viewer, method)(path, **kwargs)
        assert refused.status_code == 403, f"{method.upper()} {path} -> {refused.status_code} {refused.text[:160]}"
        assert permission in refused.json()["detail"]
    assert queued == []
