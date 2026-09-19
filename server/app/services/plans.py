"""The requirement compiler, and what happens when a plan is dispatched.

A requirement in a person's own words goes in; a plan, a task and — once nothing is left open — a run
comes out. The compiler itself lives in `ai/compiler.py`. This service decides what it is handed: the
project's instruction files (`services/instructions.py`), the memory facts that match the requirement,
and the code and documents retrieval finds for it — and records all three on the plan (`cited` and
`grounding`), so a reader can check what the plan was based on. With no model there is no plan —
compiling is refused in words that say how to add one, and nothing is written.

A model names files it has not seen. So the files it names are checked against the code index before
they are kept: a path the index holds stays, a bare name that matches exactly one indexed path becomes
that path, and the rest are kept as **new files** the change would create — said so on the plan, never
dropped and never passed off as existing.

What this service adds is the part that must be true: a plan's questions are **rows**, so "nothing is
left open" is a query rather than a promise, and dispatching refuses until they are settled.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.compiler import Context, PlanOut, compile_plan, criteria
from ..ai.gateway import Gateway, Result
from ..data import roster
from ..models import (
    ChecklistItem,
    CodeFile,
    CodeIndexRun,
    Plan,
    PlanQuestion,
    PlanStep,
    Project,
    Task,
    TaskAgent,
    WorkflowDefinition,
)
from ..repositories import (
    ActivityRepository,
    MemoryRepository,
    NotFound,
    PlanRepository,
    ProjectRepository,
    TaskRepository,
)
from ..repositories.code import CodeIndexRepository
from . import instructions
from .code import checkout
from .errors import Refused, needs_a_model
from .instructions import Resolved
from .knowledge import MemoryService
from .retrieval import RetrievalService
from .runs import RunService, _setup

log = logging.getLogger(__name__)

FACTS_FOR_CONTEXT = 6
#: Code and document pieces the compiler is handed. Asked for with room to spare, because the search
#: also returns remembered facts, and those reach the compiler through memory instead.
PIECES_FOR_CONTEXT = 6
PIECES_ASKED = 16
#: Files a plan may name. A plan that names more is describing the repository, not a change to it.
MAX_FILES = 40
#: Indexed paths offered for a bare name that matches several, so the screen can show what it meant.
CANDIDATES = 3

Grounded = tuple[Result[PlanOut], list[str], list[dict[str, str]]]


def _plain(path: str) -> str:
    """A path as the index keeps it: forward slashes, no leading `./` or `/`."""
    path = path.strip().replace("\\", "/")
    while path.startswith(("./", "/")):
        path = path[2:] if path.startswith("./") else path[1:]
    return path


def _like(text: str) -> str:
    """Text for a LIKE pattern, its own % and _ taken literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _project_doc(project: Project) -> dict[str, Any]:
    """What the compiler is told about the project, from the row rather than a stored document."""
    return {"id": project.id, "name": project.name, "stack": project.stack or [],
            "description": project.description}


def _grounded(grounding: list[dict[str, str]]) -> str:
    """"2 instruction files, 5 code pieces" — what the activity line says a plan was read from."""
    counts = {kind: sum(1 for g in grounding if g["kind"] == kind) for kind in ("instructions", "code", "doc")}
    words = {"instructions": ("instruction file", "instruction files"), "code": ("code piece", "code pieces"),
             "doc": ("document piece", "document pieces")}
    said = [f"{n} {words[kind][n != 1]}" for kind, n in counts.items() if n]
    return ", ".join(said) or "memory only"


def _criteria_edited(plan: Plan) -> bool:
    """True when the plan's criteria are no longer the ones the compiler proposed — a person wrote them."""
    return list(plan.acceptance_criteria or []) != list((plan.compiler or {}).get("criteria") or [])


#: What a drafted AGENTS.md is asked to cover. The requirement is compiled like any other, so the
#: file is written in a run's worktree and lands only with a person's signature.
DRAFT_ASK = (
    "Write AGENTS.md at the root of the {name} repository: the instructions an AI coding agent reads "
    "before it works here. Cover what the project is, how the code is laid out, how to install, build, "
    "run and test it, and the conventions the code follows. Write only what the code shows — read the "
    "build files, the configuration and the tests for the commands — and leave out what it does not "
    "show rather than guess. Keep it under 200 lines. Change no other file.")
DRAFT_MODULES = 12
DRAFT_HOTSPOTS = 6


class PlanService:
    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.plans = PlanRepository(session)
        self.tasks = TaskRepository(session)
        self.projects = ProjectRepository(session)
        self.memory = MemoryRepository(session)
        self.activity = ActivityRepository(session)

    async def _facts(self, requirement: str, project_id: str) -> list[dict[str, Any]]:
        found = await self.memory.search(requirement, project=project_id, limit=FACTS_FOR_CONTEXT)
        return [{"ref": f.ref, "title": f.title, "body": f.body, "evidence": f.evidence or []}
                for f in found[:FACTS_FOR_CONTEXT]]

    async def _pieces(self, requirement: str, project_id: str) -> list[dict[str, Any]]:
        """Retrieval's code and document pieces for the requirement. A retrieval that fails leaves the
        compiler with less to read, not without a plan: the plan's grounding then says what it had."""
        try:
            found = await RetrievalService(self.session, self.gateway).search(
                project_id, requirement, limit=PIECES_ASKED)
        except Exception as failed:                  # a lane or the index misbehaving must not stop a compile
            log.warning("retrieval for a compile in %s did not answer: %s", project_id, failed)
            return []
        return [x for x in found if x["kind"] in ("code", "doc")][:PIECES_FOR_CONTEXT]

    async def _run_compiler(self, project: Project, requirement: str, answers: list[dict[str, str]],
                            actor: str | None, targets: Sequence[str] = ()) -> Grounded:
        """The plan, the memory refs it was handed, and the grounding — instruction files, code and
        documents — it was handed too.

        Refused before anything is written when no model can answer, so a failed compile leaves no
        task, no plan and no half-rewritten steps behind. A rule file scoped to some paths applies when
        the files retrieval found, or the files the plan already names, fall under them.
        """
        facts = await self._facts(requirement, project.id)
        pieces = await self._pieces(requirement, project.id)
        told: Resolved = await instructions.for_project(
            project, [*targets, *(x["path"] for x in pieces if x["kind"] == "code")])
        context = Context(project=_project_doc(project), facts=facts, answers=answers,
                          instructions=told.text, pieces=pieces)
        result, cited = await asyncio.to_thread(needs_a_model, lambda: compile_plan(
            self.gateway, requirement, context, actor=actor, project=project.id))
        grounding = [*told.grounding(),
                     *({"kind": x["kind"], "ref": x["ref"], "path": x["path"]} for x in pieces)]
        return result, cited, grounding

    async def _check_files(self, project_id: str, named: Sequence[str]) -> tuple[list[str], dict[str, Any]]:
        """The files a plan names, checked against the code index, and what the check found.

        Returns the paths to keep — an indexed path as it is, a bare name that matches exactly one
        indexed path as that path, anything else as the model wrote it — and `{checked, newFiles,
        ambiguous}`: `newFiles` are the paths the index does not hold (files the change would create),
        `ambiguous` maps a name that matches several indexed paths to a few of them. With no index,
        `checked` is false and nothing is called new, because nothing could be looked up.
        """
        wanted = list(dict.fromkeys(p for p in map(_plain, named) if p))[:MAX_FILES]
        if await self.session.get(CodeIndexRun, project_id) is None:
            return wanted, {"checked": False, "newFiles": [], "ambiguous": {}}
        exact = set((await self.session.execute(select(CodeFile.path).where(
            CodeFile.project_id == project_id, CodeFile.path.in_(wanted)))).scalars())
        kept: list[str] = []
        new: list[str] = []
        ambiguous: dict[str, list[str]] = {}
        for path in wanted:
            if path in exact:
                kept.append(path)
                continue
            tails = list((await self.session.execute(
                select(CodeFile.path).where(CodeFile.project_id == project_id,
                                            CodeFile.path.like(f"%/{_like(path)}", escape="\\"))
                .order_by(CodeFile.path).limit(CANDIDATES + 1))).scalars())
            if len(tails) == 1:
                kept.append(tails[0])
                continue
            kept.append(path)
            if tails:
                ambiguous[path] = tails[:CANDIDATES]
            else:
                new.append(path)
        return list(dict.fromkeys(kept)), {"checked": True, "newFiles": new, "ambiguous": ambiguous}

    async def _record(self, plan: Plan, result: Result[PlanOut], cited: list[str],
                      grounding: list[dict[str, str]], *, keep_criteria: bool = False) -> None:
        """What the compiler said and was handed, onto the plan. The files are checked first; the
        compiler's own criteria are kept beside the lane, so a later compile can tell whether a person
        has edited the plan's since — and leave a person's words alone."""
        out = result.data
        files, check = await self._check_files(plan.project_id, out.affectedFiles)
        plan.affected_modules, plan.affected_files = out.affectedModules, files
        plan.affected_db, plan.test_plan = out.affectedDb, out.testPlan
        plan.cited, plan.grounding = cited, grounding
        plan.compiler = {**result.meta(), "criteria": out.acceptanceCriteria, "fileCheck": check}
        if not keep_criteria:
            plan.acceptance_criteria = out.acceptanceCriteria

    # ── compiling ────────────────────────────────────────────────
    async def compile(self, project_id: str, requirement: str, *, by: str,
                      by_id: str | None = None) -> tuple[Plan, Task]:
        """One requirement becomes a plan and the task that carries it."""
        project = await self.projects.get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        text = requirement.strip()
        if len(text) < 3:
            raise Refused("Write the requirement in a sentence or two.", status=422)

        result, cited, grounding = await self._run_compiler(project, text, [], by_id)
        out = result.data
        task_ref = await self.tasks.next_ref()
        n = task_ref.split("-")[-1]

        # The empty collections are given on purpose: a freshly persisted object's relationships are
        # unloaded, and touching one inside async code raises instead of loading. Handed in here, they
        # are loaded from the start — so the steps and questions appended below are really in them.
        task = await self.tasks.add(Task(
            id=f"t{n}", ref=task_ref, title=out.title, project_id=project.id, status="planning",
            priority=out.priority, risk=out.risk, requirement=text,
            layers=list(out.layers), files=len(out.affectedFiles),
            checklist=[], assignees=[]))
        plan = await self.plans.add(Plan(
            id=f"p{n}", ref=f"PLAN-{n}", task_id=task.id, project_id=project.id, status="draft",
            steps=[], questions=[],
            raw_requirement=text, business_requirement=out.businessRequirement,
            technical_requirement=out.technicalRequirement, architecture_impact=out.architectureImpact,
            risk=out.risk, confidence=out.confidence, requested_by=by))
        await self._record(plan, result, cited, grounding)
        task.files = len(plan.affected_files)
        await self._write_steps(plan, task, out)
        await MemoryService(self.session).recall(cited, via="compile", context=plan.ref)

        await self.activity.record(
            actor=roster.COMMANDER, actor_kind="agent", action="Requirement compiled",
            detail=f"{plan.ref} · {len(out.steps)} steps · {len(out.openQuestions)} open questions · "
                   f"grounded in {_grounded(grounding)} · {result.provider.model}, {result.ms / 1000:.1f}s",
            level="ok", project_id=project.id, task_ref=task.ref)
        return plan, task

    async def _write_steps(self, plan: Plan, task: Task | None, out: PlanOut) -> None:
        await self._rows(plan, task, [(s.label, s.agent, s.detail) for s in out.steps], out.openQuestions)

    async def _rows(self, plan: Plan, task: Task | None, steps: list[tuple[str, str, str]],
                    questions: list[str]) -> None:
        """The plan's steps and questions, and the task's checklist, all as rows."""
        for i, (label, agent, detail) in enumerate(steps, 1):
            plan.steps.append(PlanStep(id=f"{plan.id}-s{i}", n=i, label=label, agent=agent,
                                       state="todo", detail=detail))
        for i, question in enumerate(questions):
            plan.questions.append(PlanQuestion(id=f"{plan.id}-q{i}", n=i, question=question))
        if task is not None:
            for i, (label, _, _) in enumerate(steps, 1):
                task.checklist.append(ChecklistItem(id=f"{task.id}-c{i}", n=i - 1, label=label, done=False))
            for name in dict.fromkeys(agent for _, agent, _ in steps if agent != roster.COMMANDER):
                task.assignees.append(TaskAgent(agent=name))
        await self.session.flush()

    # ── from a workflow ──────────────────────────────────────────
    async def preflight(self, project: Project) -> None:
        """Refuse before anything is written when a run could not start in this project.

        Dispatching on its own says "no run started" in the activity log and carries on, which is right
        for a plan that was worth keeping anyway. A workflow run is asked for in order to run: a plan
        and a task left dispatched with nothing working on them would be litter, so the runtime's own
        checks are asked first, and their words are the refusal.
        """
        await asyncio.to_thread(_setup, project)

    async def from_workflow(self, workflow: WorkflowDefinition, project: Project, text: str, *,
                            by: str) -> tuple[Plan, Task]:
        """A workflow's steps become an ordinary plan, copied rather than referenced, so editing the
        workflow later never rewrites what this run was asked to do. Nothing is compiled: the person
        who wrote the workflow already decided the steps, so there is nothing left to ask."""
        requirement = workflow.requirement_template.replace("{input}", text)
        task_ref = await self.tasks.next_ref()
        n = task_ref.split("-")[-1]
        steps = [(s.label, s.agent, s.detail) for s in workflow.steps]
        task = await self.tasks.add(Task(
            id=f"t{n}", ref=task_ref, title=f"{workflow.name}: {text}"[:300], project_id=project.id,
            status="planning", requirement=requirement, files=0, checklist=[], assignees=[]))
        plan = await self.plans.add(Plan(
            id=f"p{n}", ref=f"PLAN-{n}", task_id=task.id, project_id=project.id, status="draft",
            steps=[], questions=[], raw_requirement=requirement, business_requirement=workflow.description,
            requested_by=by, workflow_id=workflow.id))
        await self._rows(plan, task, steps, [])
        await self.activity.record(
            actor=by, actor_kind="human", action="Workflow started",
            detail=f"{workflow.name} → {plan.ref} · {len(steps)} steps", level="ok",
            project_id=project.id, task_ref=task.ref)
        return plan, task

    async def recompile(self, ref: str, *, by: str, by_id: str | None = None) -> Plan:
        """Compile it again with what has been answered since. Refused once it is under way."""
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        if plan.status == "dispatched":
            raise Refused(f"{ref} is already under way. Compile a new requirement instead.")
        project = await self.projects.get(plan.project_id)
        if project is None:
            raise NotFound(f"project {plan.project_id}")

        answers = [{"q": q.question, "a": q.answer} for q in plan.questions if q.answer]
        settled = {q.question for q in plan.questions if q.answer or q.deferred}
        # A person's criteria are theirs: they are kept unless they are still exactly what the compiler
        # last proposed. Read before `_record` replaces what the compiler proposed.
        edited = _criteria_edited(plan)
        result, cited, grounding = await self._run_compiler(project, plan.raw_requirement, answers, by_id,
                                                            targets=plan.affected_files or [])
        out = result.data

        for step in list(plan.steps):
            await self.session.delete(step)
        for question in list(plan.questions):
            if question.question not in settled:
                await self.session.delete(question)
        await self.session.flush()

        plan.business_requirement, plan.technical_requirement = out.businessRequirement, out.technicalRequirement
        plan.architecture_impact, plan.risk, plan.confidence = out.architectureImpact, out.risk, out.confidence
        await self._record(plan, result, cited, grounding, keep_criteria=edited)
        await MemoryService(self.session).recall(cited, via="compile", context=plan.ref)

        for i, step in enumerate(out.steps, 1):
            plan.steps.append(PlanStep(id=f"{plan.id}-s{i}", n=i, label=step.label, agent=step.agent,
                                       state="todo", detail=step.detail))
        start = max((q.n for q in plan.questions), default=-1) + 1
        for i, question in enumerate(q for q in out.openQuestions if q not in settled):
            plan.questions.append(PlanQuestion(id=f"{plan.id}-q{start + i}", n=start + i,
                                               question=question))
        await self.session.flush()

        await self.activity.record(actor=by, actor_kind="human", action="Plan re-compiled",
                                   detail=f"{ref} · {len(out.steps)} steps · grounded in "
                                          f"{_grounded(grounding)} · {result.provider.model}",
                                   level="ok", project_id=plan.project_id)
        return plan

    # ── acceptance criteria ──────────────────────────────────────
    async def set_criteria(self, ref: str, wanted: Sequence[str], *, by: str) -> Plan:
        """A person's own criteria for the plan, before it is under way. Trimmed and capped by the same
        rule as the compiler's; kept by a later re-compile."""
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        if plan.status == "dispatched":
            raise Refused(f"{ref} is already under way, so what it is judged against is settled.")
        before = list(plan.acceptance_criteria or [])
        plan.acceptance_criteria = criteria(list(wanted))
        await self.session.flush()
        added = len([c for c in plan.acceptance_criteria if c not in before])
        removed = len([c for c in before if c not in plan.acceptance_criteria])
        await self.activity.record(
            actor=by, actor_kind="human", action="Acceptance criteria edited",
            detail=f"{ref} · {len(plan.acceptance_criteria)} criteria · {added} added, {removed} removed",
            level="ok", project_id=plan.project_id, task_ref=plan.task.ref if plan.task else None)
        return plan

    # ── a first AGENTS.md ────────────────────────────────────────
    async def draft_instructions(self, project_id: str, *, by: str, by_id: str | None = None) -> tuple[Plan, Task]:
        """A plan that asks for an AGENTS.md written from what the code index measured.

        Refused when the project has no checkout here, when it already has one, or when it was never
        indexed — the requirement is built from the index, and without one there is nothing true to say.
        """
        project = await self.projects.get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        root = checkout(project)
        if root is None or not await asyncio.to_thread(root.is_dir):
            raise Refused(f"{project.name} has no code on this machine, so there is nowhere to write AGENTS.md.")
        if await asyncio.to_thread((root / "AGENTS.md").is_file):
            raise Refused(f"{project.name} already has an AGENTS.md. Edit it in the repository instead.")
        if await self.session.get(CodeIndexRun, project_id) is None:
            raise Refused(f"Index {project.name} first: the draft is written from what the code index found.")
        return await self.compile(project_id, await self._draft_requirement(project), by=by, by_id=by_id)

    async def _draft_requirement(self, project: Project) -> str:
        """The ask, and what the index measured — languages, the largest modules, the files most depended
        on — so the model starts from the repository's real shape rather than its name."""
        index = CodeIndexRepository(self.session)
        languages = await index.languages(project.id)
        modules = (await index.module_sizes(project.id))[:DRAFT_MODULES]
        hotspots = await index.hotspots(project.id, limit=DRAFT_HOTSPOTS)
        objects = await index.declared_object_count(project.id)
        lines = [DRAFT_ASK.format(name=project.name), "", "What the code index measured:"]
        if languages:
            lines.append("- Languages: " + ", ".join(
                f"{x['name'] or 'other'} ({x['files']:,} files, {x['lines']:,} lines)" for x in languages))
        if modules:
            lines.append("- Largest modules: " + ", ".join(
                f"{name or '(root)'} ({files:,} files, {size:,} lines)" for name, files, size, _ in modules))
        if hotspots:
            lines.append("- Most depended on: " + ", ".join(
                f"{path} (reached by {reached})" for path, _l, _c, _ch, reached in hotspots))
        if objects:
            lines.append(f"- Database objects declared in the code: {objects:,}")
        return "\n".join(lines)[:3_900]

    # ── dispatching ──────────────────────────────────────────────
    async def dispatch(self, ref: str, *, by: str, may_run: bool,
                       goal_budget: int | None = None) -> tuple[Plan, list[Any]]:
        """Settle the gate, then hand the work to the runtime. Returns the plan and the runs to start.

        `goal_budget` (1–5) is "run until done": the run ends with a completion check against the plan's
        acceptance criteria and tries again on its own while it misses and attempts remain. A goal
        needs criteria to be judged against, so a plan without them is refused before anything moves."""
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        if plan.status == "dispatched":
            raise Refused(f"{ref} is already under way.")
        open_questions = await self.plans.open_questions(plan.id)
        if open_questions:
            n = len(open_questions)
            raise Refused(f"{ref} still has {n} open question{'s' if n > 1 else ''}. "
                          f"Answer or defer {'them' if n > 1 else 'it'} first; the plan does not guess.")
        if goal_budget is not None:
            if not 1 <= goal_budget <= 5:
                raise Refused("Run until done takes 1 to 5 attempts.", status=422)
            if not [c for c in plan.acceptance_criteria or [] if str(c).strip()]:
                raise Refused(f"{ref} has no acceptance criteria, so nothing could say it is done. "
                              "Add them to the plan, then run it until done.", status=422)

        plan.status = "dispatched"
        if plan.steps:
            plan.steps[0].state = "active"
        task = await self.tasks.get(plan.task_id) if plan.task_id else None
        if task is not None and task.status in ("backlog", "planning"):
            task.status = "in_progress"
        await self.session.flush()

        first = plan.steps[0] if plan.steps else None
        await self.activity.record(
            actor=by, actor_kind="human", action="Plan dispatched",
            detail=f"{ref} → {task.ref if task else '—'}"
                   + (f" · {first.agent} starts: {first.label}" if first else ""),
            level="ok", project_id=plan.project_id, task_ref=task.ref if task else None)

        project = await self.projects.get(plan.project_id)
        if not may_run or project is None or not project.source_kind:
            return plan, []
        try:
            made = await RunService(self.session, self.gateway).plan_runs(plan, task, project, by,
                                                                          goal_budget=goal_budget)
        except Refused as refused:      # no code here, no git, nothing to branch from: say so, don't fail
            await self.activity.record(actor=roster.ORCHESTRATOR, actor_kind="agent", action="No run started",
                                       detail=str(refused), level="warn", project_id=plan.project_id)
            return plan, []
        lead = made[-1]
        await self.activity.record(
            actor=by, actor_kind="human", action="Run started",
            detail=f"{lead.ref} · " + (f"{len(made) - 1} agents in parallel, merging into {lead.branch}"
                                       if len(made) > 1 else f"worktree on {lead.branch}")
                   + (f" · until done, up to {goal_budget} attempts" if goal_budget else ""),
            level="ok", project_id=plan.project_id, task_ref=task.ref if task else None)
        return plan, made
