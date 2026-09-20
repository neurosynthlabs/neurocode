"""Evals: does a feature still answer the way it should — measured, not asserted.

A suite points at one of the five things in this app that can actually be called: the requirement
compiler, ask memory, retrieval, the reviewer's prompt, or a bare prompt on a lane. Each case is an
input and the checks its answer must pass. A check is stated and deterministic (the answer contains
this, the JSON at that path equals this, that ref is in the top k) or it is a judge — and a judge is a
real model call of its own, with its own line in the usage ledger, labelled as a judge wherever its
verdict is shown.

The rules that keep a score honest:

* **The model is called the way the feature calls it.** The compiler and ask memory are the same
  functions the screens use, handed the same facts; only the ledger label changes, so eval traffic
  never inflates what the compile and ask figures say.
* **The rules are not a model.** Ask falls back to offline memory search when no lane answers. An
  answer like that is marked offline; a suite that does not allow offline answers counts it as an
  error, never as a pass. The compile, review and prompt targets have no offline version at all, so
  with no model they error — nothing is invented to fill the gap.
* **Blocking work never runs on the event loop.** Every target call and every judge call crosses a
  worker thread, because a provider can take two minutes to answer and the API has other requests.
* **Stopping stops.** Each case begins by asking whether the run is still wanted, and each result is
  written under a lock on the run's row, so an answer that arrives after a stop is not kept.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import features, lanes
from ..ai.compiler import Context, compile_plan
from ..ai.gateway import REVIEW, Gateway, NoModel, Result, extract_json
from ..data.base import utcnow
from ..data.engine import Database
from ..models import EvalCase, EvalResult, EvalRun, EvalSuite, Project
from ..repositories.base import NotFound
from ..repositories.evals import (
    IN_FLIGHT,
    MAX_CASES,
    EvalCaseRepository,
    EvalResultRepository,
    EvalRunRepository,
    EvalSuiteRepository,
    LessonRepository,
)
from ..repositories.knowledge import MemoryRepository
from ..repositories.work import ActivityRepository, PlanRepository, ProjectRepository
from ..schemas.evals import (
    Check,
    case_json,
    lesson_json,
    lesson_source,
    run_json,
    suite_json,
)
from .ai_features import AiFeatureService
from .errors import Refused
from .identity import Person
from .knowledge import MemoryService, NewFact
from .retrieval import RetrievalService
from .runs import REVIEW_SYSTEM, ReviewOut

log = logging.getLogger(__name__)

#: How eval traffic is labelled in the usage ledger, kept apart from the features it measures.
FEATURE, JUDGE_FEATURE = "eval", "eval-judge"
#: Targets that read one project's workspace, and so need one.
READS_A_PROJECT = ("compile", "ask", "retrieval")
#: Checks that only mean something for one target. The three retrieval ones ask about a list of refs,
#: which only the retrieval target produces — and `retrieves_nothing` is the unanswerable set, which
#: would be nonsense asked of a compiler.
ONLY_FOR = {"cites": ("ask",), "retrieves": ("retrieval",), "retrieves_all": ("retrieval",),
            "retrieves_nothing": ("retrieval",)}
#: Retrieval calls no model, so a check about which model answered cannot say anything about it.
NOT_FOR_RETRIEVAL = ("free_lane", "not_offline", "judge")
#: The same number of facts the compiler is handed when a person compiles a requirement.
COMPILE_FACTS = 6
#: How much of an answer is stored, and how many results retrieval is asked for when no check says.
MAX_OUTPUT = 20_000
DEFAULT_K = 8
#: The checks that ask retrieval for a list of refs, and so say how far down that list they look.
RETRIEVAL_CHECKS = ("retrieves", "retrieves_all", "retrieves_nothing")
#: How many agents a case captured from a plan checks for.
PLAN_AGENTS = 6

JUDGE_SYSTEM = """You are a strict evaluator inside NeuroCode. You are given a rubric and an answer another model
produced. Decide only whether the answer satisfies the rubric; do not rewrite it, and do not reward effort.
Reply with one JSON object: {"pass": true or false, "reason": "one sentence naming what decided it"}"""


@dataclass(slots=True)
class Verdict:
    passed: bool
    reason: str

    @classmethod
    def parse(cls, raw: str) -> Verdict:
        data = extract_json(raw)
        if not isinstance(data.get("pass"), bool):
            raise ValueError("the judge did not say pass or fail")
        return cls(passed=data["pass"], reason=str(data.get("reason") or ""))


class _Labelled:
    """The gateway as the compiler and ask memory see it, with every call ledgered as an eval and the
    suite's lane asked first. Ask memory calls `run`, which has an offline answer; the compiler calls
    `ask`, which has none. That is all either needs of it."""

    def __init__(self, gateway: Gateway, lane: str | None) -> None:
        self.gateway, self.lane = gateway, lane

    def run(self, messages: list[dict[str, str]], parse: Any, fallback: Any, **kwargs: Any) -> Result[Any]:
        return self.gateway.run(messages, parse, fallback, **{**kwargs, "feature": FEATURE, "lane": self.lane})

    def ask(self, messages: list[dict[str, str]], parse: Any, **kwargs: Any) -> Result[Any]:
        return self.gateway.ask(messages, parse, **{**kwargs, "feature": FEATURE, "lane": self.lane})


# ── authoring ────────────────────────────────────────────────────

@dataclass(slots=True)
class SuiteDraft:
    name: str
    kind: str
    target_kind: str
    project_id: str | None = None
    lane: str | None = None
    system_prompt: str = ""
    threshold: int = 90
    allow_offline: bool = False
    description: str = ""


@dataclass(slots=True)
class CaseDraft:
    name: str
    input: str
    checks: list[Check]
    weight: int = 1
    source_plan_id: str | None = None


def _refuse_checks(target: str, checks: list[Check]) -> None:
    for given in checks:
        allowed = ONLY_FOR.get(given.kind)
        if allowed and target not in allowed:
            raise Refused(f"A {given.kind} check only means something on a {' or '.join(allowed)} suite.",
                          status=422)
        if target == "retrieval" and given.kind in NOT_FOR_RETRIEVAL:
            raise Refused(f"Retrieval calls no model, so a {given.kind} check has nothing to judge.", status=422)


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


class EvalService:
    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.suites = EvalSuiteRepository(session)
        self.cases = EvalCaseRepository(session)
        self.runs = EvalRunRepository(session)
        self.results = EvalResultRepository(session)
        self.activity = ActivityRepository(session)

    # ── reading ──────────────────────────────────────────────────
    async def overview(self) -> dict[str, Any]:
        """Every suite with its last finished score and trend, and the lessons written from runs."""
        suites = await self.suites.listed()
        counts = await self.suites.case_counts()
        history = await self.runs.history()
        running = await self.runs.in_flight()
        return {
            "suites": [suite_json(s, cases=counts.get(s.id, 0), history=history.get(s.id, []),
                                  running_ref=running.get(s.id)) for s in suites],
            "trend": {s.id: [h["score"] for h in reversed(history.get(s.id, []))] for s in suites},
            "lessons": [lesson_json(f) for f in await LessonRepository(self.session).recent()],
            # Which lanes could answer right now, so a person can pin one — asked of the gateway, which
            # counts today's spend through its own blocking pool.
            "lanes": [{"id": x["id"], "label": x["label"], "ready": x["ready"], "blocked": x["blocked"]}
                      for x in await asyncio.to_thread(self.gateway.report)],
        }

    async def suite(self, suite_id: str) -> EvalSuite:
        found = await self.suites.get(suite_id)
        if found is None:
            raise NotFound(f"eval suite {suite_id}")
        return found

    async def detail(self, suite_id: str, run_ref: str | None = None) -> dict[str, Any]:
        """A suite, its cases, its recent runs, and what each case produced in the run on screen —
        the one asked for, or else the latest that got past the queue."""
        suite = await self.suite(suite_id)
        runs = await self.runs.of_suite(suite.id)
        if run_ref:
            view = next((r for r in runs if r.ref == run_ref), None) or await self.runs.by_ref(run_ref)
            if view is None or view.suite_id != suite.id:
                raise NotFound(f"eval run {run_ref}")
        else:
            view = next((r for r in runs if r.status != "queued"), None)
        history = (await self.runs.history([suite.id])).get(suite.id, [])
        results = {r.case_id: r for r in await self.results.of_run(view.id)} if view else {}

        # A case moves against the finished run before the one on screen.
        before = next((h for h in history if view is not None and h["ref"] != view.ref
                       and (view.finished_at is None or h["finished_at"] < view.finished_at)), None)
        previous = await self.results.scores_of(before["id"]) if before else {}
        total = sum(c.weight for c in suite.cases)

        def moved(case: EvalCase) -> float | None:
            result = results.get(case.id)
            if result is None or case.id not in previous:
                return None
            return float(result.score) - previous[case.id]

        return {
            **suite_json(suite, cases=len(suite.cases), history=history,
                         running_ref=next((r.ref for r in runs if r.status in IN_FLIGHT), None)),
            "description": suite.description, "systemPrompt": suite.system_prompt,
            "trend": [h["score"] for h in reversed(history)],
            "run": run_json(view) if view else None, "comparedWith": before["ref"] if before else None,
            "runs": [run_json(r) for r in runs],
            # `cases` is already the count every suite carries; the rows themselves go beside it.
            "caseRows": [case_json(c, suite.name, results.get(c.id), moved=moved(c), total_weight=total)
                      for c in suite.cases],
        }

    # ── suites ───────────────────────────────────────────────────
    async def create_suite(self, draft: SuiteDraft, who: Person) -> EvalSuite:
        name = draft.name.strip()
        if await self.suites.by_name(name) is not None:
            raise Refused(f"There is already a suite called {name}.")
        await self._refuse_suite(draft.target_kind, draft.project_id, draft.lane, draft.system_prompt)
        suite = await self.suites.add(EvalSuite(
            id=_new_id("es"), name=name, kind=draft.kind, target_kind=draft.target_kind,
            project_id=draft.project_id, lane=draft.lane, system_prompt=draft.system_prompt.strip(),
            threshold=draft.threshold, allow_offline=draft.allow_offline,
            description=draft.description.strip(), created_by=who.id, cases=[]))
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval suite created",
                                   detail=f"{suite.name} · {draft.target_kind}", project_id=suite.project_id)
        return suite

    async def update_suite(self, suite_id: str, changes: dict[str, Any], who: Person) -> EvalSuite:
        """Everything but the target, which is what the cases were written against."""
        suite = await self.suite(suite_id)
        if "name" in changes:
            name = str(changes["name"]).strip()
            clash = await self.suites.by_name(name)
            if clash is not None and clash.id != suite.id:
                raise Refused(f"There is already a suite called {name}.")
            suite.name = name
        for key in ("kind", "project_id", "lane", "threshold", "allow_offline"):
            if key in changes:
                setattr(suite, key, changes[key])
        for key in ("system_prompt", "description"):
            if key in changes:
                setattr(suite, key, str(changes[key]).strip())
        await self._refuse_suite(suite.target_kind, suite.project_id, suite.lane, suite.system_prompt)
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval suite changed",
                                   detail=suite.name, project_id=suite.project_id)
        return suite

    async def delete_suite(self, suite_id: str, who: Person) -> None:
        suite = await self.suite(suite_id)
        if suite.id in await self.runs.in_flight():
            raise Refused(f"{suite.name} is running. Stop the run first.")
        name, project_id = suite.name, suite.project_id
        await self.suites.remove(suite)
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval suite deleted",
                                   detail=f"{name} · its runs and results with it", level="warn",
                                   project_id=project_id)

    async def _refuse_suite(self, target: str, project_id: str | None, lane: str | None,
                            system_prompt: str) -> None:
        if target in READS_A_PROJECT:
            if not project_id:
                raise Refused(f"A {target} suite reads one project's workspace, so it needs a project.",
                              status=422)
        if project_id and await ProjectRepository(self.session).get(project_id) is None:
            raise NotFound(f"project {project_id}")
        if lane is not None and lane not in lanes.IDS:
            raise Refused(f"There is no lane called {lane}.", status=422)
        if target == "prompt" and not system_prompt.strip():
            raise Refused("A prompt suite needs the system prompt its cases are answered with.", status=422)

    # ── cases ────────────────────────────────────────────────────
    async def add_case(self, suite_id: str, draft: CaseDraft, who: Person) -> tuple[EvalSuite, EvalCase]:
        suite = await self.suite(suite_id)
        if await self.cases.count_in(suite.id) >= MAX_CASES:
            raise Refused(f"{suite.name} already holds {MAX_CASES} cases. Split it into two suites.")
        _refuse_checks(suite.target_kind, draft.checks)
        await self._refuse_plan(suite, draft.source_plan_id)
        case = await self.cases.add(EvalCase(
            id=_new_id("ec"), suite_id=suite.id, n=await self.cases.next_n(suite.id), name=draft.name.strip(),
            input=draft.input, checks=[c.stored() for c in draft.checks], weight=draft.weight,
            source_plan_id=draft.source_plan_id))
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval case added",
                                   detail=f"{suite.name} · {case.name}", project_id=suite.project_id)
        return suite, case

    async def update_case(self, case_id: str, changes: dict[str, Any], who: Person) -> tuple[EvalSuite, EvalCase]:
        case = await self._case(case_id)
        suite = await self.suite(case.suite_id)
        if "checks" in changes:
            checks = cast(list[Check], changes["checks"])
            _refuse_checks(suite.target_kind, checks)
            case.checks = [c.stored() for c in checks]
        for key in ("name", "input", "weight"):
            if key in changes:
                setattr(case, key, changes[key].strip() if key == "name" else changes[key])
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval case changed",
                                   detail=f"{suite.name} · {case.name}", project_id=suite.project_id)
        return suite, case

    async def delete_case(self, case_id: str, who: Person) -> None:
        case = await self._case(case_id)
        suite = await self.suite(case.suite_id)
        if suite.id in await self.runs.in_flight():
            raise Refused(f"{suite.name} is running. Stop the run before removing a case from it.")
        name = case.name
        await self.cases.remove(case)
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval case removed",
                                   detail=f"{suite.name} · {name}", level="warn", project_id=suite.project_id)

    async def _case(self, case_id: str) -> EvalCase:
        found = await self.cases.get(case_id)
        if found is None:
            raise NotFound(f"eval case {case_id}")
        return found

    async def _refuse_plan(self, suite: EvalSuite, plan_id: str | None) -> None:
        if plan_id is None:
            return
        plan = await PlanRepository(self.session).get(plan_id)
        if plan is None:
            raise NotFound(f"plan {plan_id}")
        if plan.project_id != suite.project_id:
            raise Refused(f"{plan.ref} belongs to another project than {suite.name} reads.", status=422)

    async def case_from_plan(self, suite_id: str, plan_ref: str) -> dict[str, Any]:
        """A regression case drafted from a plan a person accepted: its requirement as the input, its
        risk and its owners as the checks. Nothing is saved — the person edits it first."""
        suite = await self.suite(suite_id)
        if suite.target_kind != "compile":
            raise Refused("Only a compiler suite can take a case from a plan.", status=422)
        plan = await PlanRepository(self.session).by_ref(plan_ref)
        if plan is None:
            raise NotFound(f"plan {plan_ref}")
        await self._refuse_plan(suite, plan.id)
        if not plan.raw_requirement.strip():
            raise Refused(f"{plan.ref} keeps no requirement to replay.", status=422)
        agents = list(dict.fromkeys(s.agent for s in plan.steps if s.agent))[:PLAN_AGENTS]
        checks = [{"kind": "json_equals", "path": "$.risk", "value": plan.risk},
                  *({"kind": "json_contains", "path": "$.steps[*].agent", "value": a} for a in agents)]
        title = plan.task.title if plan.task else plan.raw_requirement[:80]
        return {"name": f"{plan.ref} · {title}"[:200], "input": plan.raw_requirement, "checks": checks,
                "weight": 1, "sourcePlanId": plan.id}

    # ── running ──────────────────────────────────────────────────
    async def start(self, suite_id: str, lane: str | None, who: Person) -> EvalRun:
        suite = await self.suite(suite_id)
        if lane is not None and lane not in lanes.IDS:
            raise Refused(f"There is no lane called {lane}.", status=422)
        running = (await self.runs.in_flight()).get(suite.id)
        if running:
            raise Refused(f"{suite.name} is already running as {running}.")
        if not suite.cases:
            raise Refused(f"{suite.name} has no cases yet. Add one, then run it.")
        return await self._queue(suite, lane or suite.lane, who)

    async def start_all(self, kind: str | None, who: Person) -> list[EvalRun]:
        """One queued run per suite that has cases and is not already running. They run one after
        another, not at once, so a free lane's per-minute allowance is not spent in a burst."""
        running = await self.runs.in_flight()
        counts = await self.suites.case_counts()
        chosen = [s for s in await self.suites.listed()
                  if (kind is None or s.kind == kind) and s.id not in running and counts.get(s.id)]
        if not chosen:
            raise Refused("There is no suite to run: none has cases, or every one is running already.")
        return [await self._queue(suite, suite.lane, who) for suite in chosen]

    async def _queue(self, suite: EvalSuite, lane: str | None, who: Person) -> EvalRun:
        ref = await self.runs.next_ref()
        run = await self.runs.add(EvalRun(id=_new_id("er"), ref=ref, suite_id=suite.id, status="queued",
                                          lane=lane, requested_by=who.id, results=[]))
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval run queued",
                                   detail=f"{ref} · {suite.name}" + (f" · on {lane}" if lane else ""),
                                   project_id=suite.project_id)
        return run

    async def cancel(self, ref: str, who: Person) -> EvalRun:
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"eval run {ref}")
        if run.status not in IN_FLIGHT:
            raise Refused(f"{ref} already {run.status}.")
        run.status, run.finished_at = "cancelled", utcnow()
        run.note = f"Stopped by {who.name}."
        await self.session.flush()
        suite = await self.suite(run.suite_id)
        await self.activity.record(actor=who.name, actor_kind="human", action="Eval run stopped",
                                   detail=f"{ref} · {suite.name} · the cases it had not reached are not run",
                                   level="warn", project_id=suite.project_id)
        return run

    # ── what a person adds to a result ───────────────────────────
    async def _result(self, result_id: int) -> tuple[EvalResult, EvalCase, EvalSuite, EvalRun]:
        result = await self.results.get(result_id)
        if result is None:
            raise NotFound(f"eval result {result_id}")
        case = await self._case(result.case_id)
        run = await self.runs.get(result.run_id)
        if run is None:
            raise NotFound(f"eval run of result {result_id}")
        return result, case, await self.suite(case.suite_id), run

    async def override(self, result_id: int, status: str | None, note: str, who: Person) -> dict[str, Any]:
        """A person's verdict beside the machine's. The computed score is left as it was computed."""
        result, case, suite, run = await self._result(result_id)
        if status is not None and not note.strip():
            raise Refused("Say why you disagree with the verdict — the note is what the table shows.", status=422)
        result.override_status = status
        result.override_note = note.strip() if status else ""
        result.override_by = who.id if status else None
        result.overridden_at = utcnow() if status else None
        await self.session.flush()
        await self.activity.record(
            actor=who.name, actor_kind="human", action="Eval verdict overridden" if status else "Eval override removed",
            detail=f"{run.ref} · {case.name} · {result.status} → {status}" if status else f"{run.ref} · {case.name}",
            project_id=suite.project_id)
        return case_json(case, suite.name, result, moved=None, total_weight=0)

    async def lesson(self, result_id: int, text: str, category: str, who: Person) -> str:
        """A lesson a person draws from a result, saved to memory where the compiler and ask memory
        will find it next time — with where it came from, so it can be traced back to the case."""
        _result, case, suite, run = await self._result(result_id)
        body = text.strip()
        if len(body) < 10:
            raise Refused("Write the lesson in a sentence.", status=422)
        title = re.split(r"(?<=[.!?])\s", body)[0][:120]
        # Evidence is a list of words like every other fact's: the compiler and the Memory screen both
        # read it as text, and a lesson that broke either would defeat the point of writing it.
        added = await MemoryService(self.session).add(
            [NewFact(title=title, body=body, category=category, confidence="MEDIUM",
                     reason=f"Learned from {case.name} in {run.ref}.",
                     evidence=cast(list[dict[str, Any]], [f"{run.ref} · {suite.name} · {case.name}"]))],
            project_id=suite.project_id, by=who.name, source=lesson_source(suite.name, run.ref))
        return added[0].ref


# ── the runner ───────────────────────────────────────────────────

@dataclass(slots=True)
class RunPlan:
    """What a run needs of its suite, read once, so no model call ever holds a database session."""

    run_id: str
    ref: str
    suite_id: str
    suite: str
    target: str
    project: dict[str, Any] | None
    lane: str | None
    system_prompt: str
    allow_offline: bool
    actor: str | None
    cases: list[dict[str, Any]]


@dataclass(slots=True)
class Answer:
    output: str = ""
    data: Any = None
    lane: str = ""
    model: str = ""
    ms: int | None = None
    offline: bool = False
    error: str = ""
    cited: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Scored:
    status: str
    score: float
    checks: list[dict[str, Any]]
    error: str = ""
    judge_model: str | None = None
    judge_reason: str = ""


async def execute(db: Database, gateway: Gateway, refs: list[str]) -> None:
    """Run these eval runs one after another. One that breaks is failed with the reason and the next
    one still runs — a background job has nobody to raise to."""
    for ref in refs:
        try:
            await _execute(db, gateway, ref)
        except Exception as e:          # noqa: BLE001 — recorded on the run, and logged in full
            log.exception("eval run %s broke", ref)
            async with db.session() as s:
                run = await EvalRunRepository(s).by_ref(ref)
                if run is not None and run.status in IN_FLIGHT:
                    run.status, run.finished_at = "failed", utcnow()
                    run.note = f"The runner broke: {type(e).__name__}: {str(e)[:200]}"


async def _execute(db: Database, gateway: Gateway, ref: str) -> None:
    plan = await _begin(db, ref)
    if plan is None:
        return
    for case in plan.cases:
        async with db.read() as s:
            still = (await s.execute(select(EvalRun.status).where(EvalRun.id == plan.run_id))).scalar_one()
        if still != "running":
            return
        answer = await _answer(db, gateway, plan, case)
        scored = await _score(gateway, plan, case, answer)
        if not await _keep(db, plan, case, answer, scored):
            return
    await _finish(db, plan)


async def _begin(db: Database, ref: str) -> RunPlan | None:
    async with db.session() as s:
        runs = EvalRunRepository(s)
        run = await runs.by_ref(ref)
        if run is None or run.status != "queued":
            return None                           # stopped before it began, or picked up already
        locked = await runs.locked(run.id)
        if locked is None or locked.status != "queued":
            return None
        suite = await s.get(EvalSuite, run.suite_id)
        if suite is None:
            return None
        project = await s.get(Project, suite.project_id) if suite.project_id else None
        locked.status = "running"
        await ActivityRepository(s).record(actor="Eval Runner", actor_kind="system", action="Eval run started",
                                           detail=f"{ref} · {suite.name} · {len(suite.cases)} cases",
                                           project_id=suite.project_id)
        return RunPlan(
            run_id=run.id, ref=ref, suite_id=suite.id, suite=suite.name, target=suite.target_kind,
            project={"id": project.id, "name": project.name, "stack": project.stack or [],
                     "description": project.description} if project else None,
            lane=run.lane, system_prompt=suite.system_prompt, allow_offline=suite.allow_offline,
            actor=run.requested_by,
            cases=[{"id": c.id, "name": c.name, "input": c.input, "checks": list(c.checks), "weight": c.weight}
                   for c in suite.cases])


def _text_evidence(evidence: Any) -> list[str]:
    return [e if isinstance(e, str) else json.dumps(e, sort_keys=True) for e in (evidence or [])]


async def _answer(db: Database, gateway: Gateway, plan: RunPlan, case: dict[str, Any]) -> Answer:
    """Call the target once, as the feature would be called. An exception is an answer too: an error."""
    project_id = plan.project["id"] if plan.project else None
    t0 = time.monotonic()
    try:
        if plan.target == "compile" and plan.project:
            async with db.read() as s:
                found = await MemoryRepository(s).search(case["input"], project=project_id, limit=COMPILE_FACTS)
                facts = [{"ref": f.ref, "title": f.title, "body": f.body, "evidence": _text_evidence(f.evidence)}
                         for f in found[:COMPILE_FACTS]]
            compiled, _cited = await asyncio.to_thread(
                compile_plan, cast(Gateway, _Labelled(gateway, plan.lane)), case["input"],
                Context(project=plan.project, facts=facts), actor=plan.actor, project=project_id)
            return _from_result(compiled, compiled.data.model_dump_json())
        if plan.target == "ask":
            async with db.read() as s:
                facts = await AiFeatureService(s, gateway)._facts(case["input"], project_id)
            asked = await asyncio.to_thread(features.ask, cast(Gateway, _Labelled(gateway, plan.lane)),
                                            case["input"], facts, actor=plan.actor, project_id=project_id)
            answer = _from_result(asked, asked.data.model_dump_json())
            answer.cited = list(asked.data.citations)
            return answer
        if plan.target == "retrieval" and project_id:
            k = max([int(c.get("k") or 0) for c in case["checks"] if c.get("kind") in RETRIEVAL_CHECKS]
                    or [DEFAULT_K])
            # Grounding, not the bare search: what a case measures has to be what a session is really
            # handed, which is the list after the relevance floor has refused what is too far away.
            # Measuring the search behind it would score pieces no feature would ever have seen.
            async with db.read() as s:
                _text, hits, searched = await RetrievalService(s, gateway).grounded(
                    project_id, case["input"], k)
            refs = [h["ref"] for h in hits]
            meaning = any(h["how"] != "lexical" for h in hits)
            answered = {"refs": refs, "floored": int(searched.get("floored") or 0)}
            return Answer(output=json.dumps(answered), data=answered, refs=refs,
                          model="lexical + semantic" if meaning else "lexical",
                          ms=round((time.monotonic() - t0) * 1000))
        if plan.target == "review":
            reviewed = await asyncio.to_thread(
                gateway.ask, [{"role": "system", "content": REVIEW_SYSTEM},
                              {"role": "user", "content": f"Diff:\n{case['input']}"}],
                lambda raw: ReviewOut.model_validate(extract_json(raw)), feature=FEATURE, role=REVIEW,
                lane=plan.lane, actor=plan.actor, project=project_id)
            return _from_result(reviewed, reviewed.data.model_dump_json())
        if plan.target == "prompt":
            prompted = await asyncio.to_thread(
                gateway.ask, [{"role": "system", "content": plan.system_prompt},
                              {"role": "user", "content": case["input"]}],
                extract_json, feature=FEATURE, lane=plan.lane, actor=plan.actor, project=project_id)
            return _from_result(prompted, json.dumps(prompted.data, ensure_ascii=False))
    except NoModel as e:
        return Answer(error=f"No model could answer: {e}", ms=round((time.monotonic() - t0) * 1000))
    except Exception as e:              # noqa: BLE001 — every lane failed, or the target broke: an error
        log.warning("eval %s · %s: the target failed: %s", plan.ref, case["name"], e)
        return Answer(error=f"{type(e).__name__}: {str(e)[:300]}", ms=round((time.monotonic() - t0) * 1000))
    return Answer(error=f"A {plan.target} suite has no project to read.")


def _from_result(result: Result[Any], output: str) -> Answer:
    offline = result.provider.id == "rules"
    return Answer(output=output[:MAX_OUTPUT], data=json.loads(output), ms=result.ms, offline=offline,
                  lane="rules" if offline else result.provider.id, model=result.provider.model)


def _at(data: Any, path: str) -> list[Any]:
    """Every value a JSONPath-lite path reaches: `$.steps[*].agent` is each step's agent."""
    found = [data]
    for name, index in re.findall(r"\.([A-Za-z_][\w-]*)|\[(\d+|\*)\]", path):
        nxt: list[Any] = []
        for value in found:
            if name and isinstance(value, dict) and name in value:
                nxt.append(value[name])
            elif index == "*" and isinstance(value, list):
                nxt.extend(value)
            elif index and index != "*" and isinstance(value, list) and int(index) < len(value):
                nxt.append(value[int(index)])
        found = nxt
    return found


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return str(a).strip().casefold() == str(b).strip().casefold()
    return bool(a == b)


def _brief(value: Any) -> str:
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))[:200]


def check(spec: dict[str, Any], answer: Answer) -> tuple[bool, str, float]:
    """One stated check against one answer: whether it held, what was actually there, and how much of
    it held.

    The score is what a check is worth to the case, from 0 to 1. Most checks can only be true or
    false and score 1 or 0 — but "did retrieval bring back the five pieces that actually answered
    this" has an answer between them, and counting it as a failure until all five arrive throws away
    the only measurement that says whether a change made retrieval better or worse.
    """
    ok, observed = _check(spec, answer)
    if spec["kind"] == "retrieves_all":
        wanted = list(dict.fromkeys(spec.get("refs") or []))
        top = answer.refs[:int(spec["k"])]
        found = [r for r in wanted if r in top]
        recall = len(found) / len(wanted) if wanted else 0.0
        return ok, observed, recall
    return ok, observed, 1.0 if ok else 0.0


def _check(spec: dict[str, Any], answer: Answer) -> tuple[bool, str]:
    """Whether one check held, and what was actually there."""
    kind, value, out = spec["kind"], spec.get("value"), answer.output
    if kind == "exact":
        return out.strip() == str(value).strip(), _brief(out)
    if kind in ("contains", "not_contains"):
        hit = str(value).casefold() in out.casefold()
        seen = f"“{value}” {'present' if hit else 'absent'}"
        return (hit, seen) if kind == "contains" else (not hit, seen)
    if kind == "regex":
        found = re.search(str(value), out)
        return found is not None, found.group(0)[:200] if found else "no match"
    if kind in ("json_equals", "json_contains"):
        reached = _at(answer.data, spec["path"])
        if kind == "json_equals":
            return bool(reached) and _same(reached[0], value), (_brief(reached[0]) if reached else "path not found")
        flat = [x for r in reached for x in (r if isinstance(r, list) else [r])]
        return any(_same(x, value) for x in flat), _brief(flat) if flat else "path not found"
    if kind == "cites":
        return spec["ref"] in answer.cited, f"cited {', '.join(answer.cited) or 'nothing'}"
    if kind == "retrieves":
        top = answer.refs[:int(spec["k"])]
        rank = top.index(spec["ref"]) + 1 if spec["ref"] in top else None
        return rank is not None, f"rank {rank}" if rank else f"not in top {spec['k']}"
    if kind == "retrieves_all":
        k = int(spec["k"])
        wanted = list(dict.fromkeys(spec.get("refs") or []))
        top = answer.refs[:k]
        missing = [r for r in wanted if r not in top]
        seen = f"{len(wanted) - len(missing)} of {len(wanted)} in top {k}"
        return not missing, seen if not missing else f"{seen} · missing {', '.join(missing)[:120]}"
    if kind == "retrieves_nothing":
        # The unanswerable set. It passes only when retrieval refused: nothing survived the relevance
        # floor, so the session is told there is nothing rather than handed pieces about the words.
        top = answer.refs[:int(spec["k"])]
        return not top, "nothing was handed over" if not top else f"handed {len(top)}: {', '.join(top)[:120]}"
    if kind == "max_ms":
        return answer.ms is not None and answer.ms <= int(value), f"{answer.ms} ms"
    if kind == "free_lane":
        lane = lanes.BY_ID.get(answer.lane)
        free = answer.lane == "rules" or (lane is not None and lane.free)
        return free, f"{answer.lane or 'no lane'} is {'free' if free else 'paid'}"
    if kind == "not_offline":
        return not answer.offline, "the offline rules answered" if answer.offline else f"{answer.model} answered"
    raise ValueError(f"no check called {kind}")


async def _score(gateway: Gateway, plan: RunPlan, case: dict[str, Any], answer: Answer) -> Scored:
    if answer.error:
        return Scored(status="error", score=0.0, checks=[], error=answer.error)
    if answer.offline and not plan.allow_offline:
        return Scored(status="error", score=0.0, checks=[],
                      error="No model answered — the offline rules did, and this suite does not count them.")
    results: list[dict[str, Any]] = []
    judge_model, judge_reason = None, ""
    for spec in case["checks"]:
        if spec["kind"] != "judge":
            ok, observed, earned = check(spec, answer)
            results.append({"kind": spec["kind"], "ok": ok, "observed": observed, "score": round(earned, 3)})
            continue
        try:
            judged = await asyncio.to_thread(
                gateway.ask, [{"role": "system", "content": JUDGE_SYSTEM},
                              {"role": "user", "content": f"Rubric:\n{spec['rubric']}\n\nInput:\n{case['input'][:4000]}"
                                                          f"\n\nAnswer:\n{answer.output[:12000]}"}],
                Verdict.parse, feature=JUDGE_FEATURE, role=REVIEW, avoid=answer.lane or None,
                actor=plan.actor, project=plan.project["id"] if plan.project else None)
        except Exception as e:          # noqa: BLE001 — a judge that cannot judge is an error, never a pass
            reason = f"The judge could not run: {str(e)[:300] or type(e).__name__}"
            results.append({"kind": "judge", "ok": None, "observed": reason, "score": 0.0})
            return Scored(status="error", score=0.0, checks=results, error=reason)
        judge_model, judge_reason = judged.provider.model, judged.data.reason
        results.append({"kind": "judge", "ok": judged.data.passed, "observed": judged.data.reason[:200],
                        "score": 1.0 if judged.data.passed else 0.0})
    # The case's score is what its checks earned, not how many of them were true: a graded check —
    # "three of the five refs that mattered came back" — is worth what it measured.
    earned = sum(float(r.get("score") or 0.0) for r in results)
    score = earned / len(results)
    status = "pass" if all(r["ok"] for r in results) else "fail" if earned == 0 else "partial"
    return Scored(status=status, score=score, checks=results, judge_model=judge_model, judge_reason=judge_reason)


async def _keep(db: Database, plan: RunPlan, case: dict[str, Any], answer: Answer, scored: Scored) -> bool:
    """Write one result, unless the run was stopped while its answer was on the way."""
    async with db.session() as s:
        run = await EvalRunRepository(s).locked(plan.run_id)
        if run is None or run.status != "running":
            return False
        s.add(EvalResult(run_id=plan.run_id, case_id=case["id"], status=scored.status,
                         score=round(scored.score, 3), output=answer.output[:MAX_OUTPUT], checks=scored.checks,
                         lane=answer.lane, model=answer.model[:120], ms=answer.ms, offline=answer.offline,
                         error=scored.error, judge_model=scored.judge_model, judge_reason=scored.judge_reason))
        counter = {"pass": "passed", "fail": "failed", "partial": "partial", "error": "errored"}[scored.status]
        setattr(run, counter, getattr(run, counter) + 1)
        return True


async def _finish(db: Database, plan: RunPlan) -> None:
    async with db.session() as s:
        runs = EvalRunRepository(s)
        run = await runs.locked(plan.run_id)
        if run is None or run.status != "running":
            return
        results = await EvalResultRepository(s).of_run(plan.run_id)
        weights = {c["id"]: c["weight"] for c in plan.cases}
        earned = sum(weights.get(r.case_id, 0) * float(r.score) for r in results)
        total = sum(weights.values())
        run.score = round(100 * earned / total) if total else 0
        run.status = "failed" if results and all(r.status == "error" for r in results) else "done"
        run.finished_at = utcnow()
        answered = [r for r in results if not r.error]
        offline = sum(1 for r in results if r.offline)
        # Two runs of a retrieval suite are only the same experiment when the same halves were working.
        # A run with an embedding lane and one without are not comparable, and a score that moved
        # between them moved for that reason — so the run says how many answers had no lane at all.
        words_only = sum(1 for r in answered if r.model == "lexical")
        if run.status == "failed":
            run.note = f"Every case errored. The first: {results[0].error[:200]}"
        elif offline and offline == len(answered):
            run.note = "Every answer came from the offline rules, not a model."
        elif offline:
            run.note = f"{offline} of {len(results)} answers came from the offline rules, not a model."
        elif words_only:
            run.note = (f"{words_only} of {len(answered)} cases were answered by words only — no lane "
                        "embedded the question, so this run is not comparable with one that had a lane.")
        await s.flush()
        history = (await runs.history([plan.suite_id])).get(plan.suite_id, [])
        before = history[1]["score"] if run.status == "done" and len(history) > 1 else None
        delta = run.score - before if before is not None else None
        moved = "" if delta is None else f" · {'+' if delta >= 0 else ''}{delta} since {history[1]['ref']}"
        await ActivityRepository(s).record(
            actor="Eval Runner", actor_kind="system",
            action="Eval run finished" if run.status == "done" else "Eval run failed",
            detail=f"{plan.ref} · {plan.suite} · score {run.score}{moved} · {run.passed} passed, {run.failed} failed, "
                   f"{run.partial} partial, {run.errored} errored",
            level="warn" if run.status == "failed" or (delta is not None and delta < 0) else "ok",
            project_id=plan.project["id"] if plan.project else None)
