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

Until it is dispatched a plan is a draft a person shapes: its steps are edited, added, removed and
reordered by hand, and comments on it — "split this", "why this?", "risky" — are handed back to the
compiler, which writes the next **revision**. The steps a revision replaced are kept on the plan
(`compiler.revisions`), with the comments it answered, so what changed between two revisions is a diff a
reader can check rather than a claim. Every edit is an activity line with its before and after, and a
taste signal (`services/taste.py`): how a person reshapes a plan says how they like the work done.

A source kept as a reference, and a project this one reads from, are never written to: a file the plan
names inside one is kept and shown as read only, never as a file the change creates.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.compiler import Context, PlanOut, RevisionOut, compile_plan, criteria, revise_plan
from ..ai.gateway import Gateway, Result
from ..data import roster
from ..data.base import utcnow
from ..models import (
    Approval,
    ChecklistItem,
    CodeFile,
    CodeIndexRun,
    Plan,
    PlanComment,
    PlanQuestion,
    PlanStep,
    Project,
    ProjectReference,
    Run,
    RunStep,
    Task,
    TaskAgent,
    User,
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
from ..schemas.work import step_changes
from ..repositories.sources import ProjectSourceRepository
from ..repositories.words import terms
from . import instructions
from .code import Source, checkout, writable
from .custom_agents import CustomAgentService
from .errors import Refused, needs_a_model
from .instructions import Resolved
from .knowledge import MemoryService
from .retrieval import RetrievalService, near_enough
from .runs import RunService, _setup
from .taste import TasteService

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

#: Revisions whose replaced steps a plan keeps. Older ones fall off; the activity log still names them.
MAX_REVISIONS = 20
#: Comments one plan may hold, and what one may say.
MAX_COMMENTS = 200
COMMENT_KINDS = ("comment", "split", "remove", "why", "risky")
#: Steps a person may give one plan by hand, and the words each part of one may have.
MAX_STEPS = 40
MAX_LABEL = 200
MAX_DETAIL = 2_000
#: What `plans.compiler` keeps beside the lane that are not the lane: the plan JSON sends them apart.
KEPT = ("criteria", "fileCheck", "revisions")

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
            "description": project.description, "files": project.files_count}


def _grounded(grounding: list[dict[str, str]]) -> str:
    """"2 instruction files, 5 code pieces" — what the activity line says a plan was read from."""
    counts = {kind: sum(1 for g in grounding if g["kind"] == kind)
              for kind in ("instructions", "taste", "code", "doc")}
    words = {"instructions": ("instruction file", "instruction files"), "code": ("code piece", "code pieces"),
             "doc": ("document piece", "document pieces"), "taste": ("taste rule", "taste rules")}
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
        #: Why the last dispatch started no run, in words, for the screen that asked; None when it did.
        self.no_run: str | None = None

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
        # The same relevance floor the session grounds by. A plan quoting a file that merely shares one
        # word with the requirement is worse than a plan that quotes nothing: the compiler is told those
        # pieces are what the repository holds about it, and writes steps against them.
        words = len(terms(requirement))
        return [x for x in found
                if x["kind"] in ("code", "doc") and near_enough(x, words)][:PIECES_FOR_CONTEXT]

    async def _ground(self, project: Project, requirement: str, answers: list[dict[str, str]],
                      targets: Sequence[str] = ()) -> tuple[Context, list[dict[str, str]]]:
        """What the compiler is handed, and the grounding to record: instruction files, the taste rules a
        person adopted, code and documents. A rule file scoped to some paths applies when the files
        retrieval found, or the files the plan already names, fall under them."""
        facts = await self._facts(requirement, project.id)
        pieces = await self._pieces(requirement, project.id)
        told: Resolved = await instructions.for_project(
            project, [*targets, *(x["path"] for x in pieces if x["kind"] == "code")])
        taste = await TasteService(self.session).applied(project.id)
        closed = await self._read_only(project.id)
        context = Context(project=_project_doc(project), facts=facts, answers=answers,
                          instructions=told.text, pieces=pieces, taste=taste.text,
                          readonly=[f"{head}/ — {why}" for head, why in closed.items()],
                          agents=await CustomAgentService(self.session).for_compiler(project))
        grounding = [*told.grounding(), *taste.grounding(),
                     *({"kind": x["kind"], "ref": x["ref"], "path": x["path"]} for x in pieces)]
        return context, grounding

    async def _run_compiler(self, project: Project, requirement: str, answers: list[dict[str, str]],
                            actor: str | None, targets: Sequence[str] = ()) -> Grounded:
        """The plan, the memory refs it was handed, and the grounding it was handed too.

        Refused before anything is written when no model can answer, so a failed compile leaves no
        task, no plan and no half-rewritten steps behind.
        """
        context, grounding = await self._ground(project, requirement, answers, targets)
        result, cited = await asyncio.to_thread(needs_a_model, lambda: compile_plan(
            self.gateway, requirement, context, actor=actor, project=project.id))
        return result, cited, grounding

    async def _read_only(self, project_id: str) -> dict[str, str]:
        """The first folder of a path that no plan may change, and why: a source kept as a reference, or
        a project this one reads from. A source's label wins over a referenced project's id."""
        closed: dict[str, str] = {}
        for row in await ProjectSourceRepository(self.session).of(project_id):
            if not writable(Source(label=row.label, root=Path(row.repo), kind=row.kind, primary=False, id=row.id,
                                   role=row.role)):
                closed[row.label] = "reference, read only"
        referenced = await self.session.execute(
            select(Project.id, Project.name).join(ProjectReference, ProjectReference.referenced_id == Project.id)
            .where(ProjectReference.project_id == project_id).limit(MAX_FILES))
        for pid, name in referenced.all():
            closed.setdefault(pid, f"referenced project {name}, read only")
        return closed

    async def _check_files(self, project_id: str, named: Sequence[str]) -> tuple[list[str], dict[str, Any]]:
        """The files a plan names, checked against the code index, and what the check found.

        Returns the paths to keep — an indexed path as it is, a bare name that matches exactly one
        indexed path as that path, anything else as the model wrote it — and `{checked, newFiles,
        ambiguous}`: `newFiles` are the paths the index does not hold (files the change would create),
        `ambiguous` maps a name that matches several indexed paths to a few of them. With no index,
        `checked` is false and nothing is called new, because nothing could be looked up. A path inside
        a reference source or a referenced project is kept and listed under `readOnly` with why — never
        called new, because nothing will be written there — and the key is present only when one is.
        """
        wanted = list(dict.fromkeys(p for p in map(_plain, named) if p))[:MAX_FILES]
        closed = await self._read_only(project_id)
        read_only = {p: closed[p.split("/", 1)[0]] for p in wanted if "/" in p and p.split("/", 1)[0] in closed}
        extra = {"readOnly": read_only} if read_only else {}
        if await self.session.get(CodeIndexRun, project_id) is None:
            return wanted, {"checked": False, "newFiles": [], "ambiguous": {}, **extra}
        exact = set((await self.session.execute(select(CodeFile.path).where(
            CodeFile.project_id == project_id, CodeFile.path.in_(wanted)))).scalars())
        kept: list[str] = []
        new: list[str] = []
        ambiguous: dict[str, list[str]] = {}
        for path in wanted:
            if path in exact or path in read_only:
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
        return list(dict.fromkeys(kept)), {"checked": True, "newFiles": new, "ambiguous": ambiguous, **extra}

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
        kept = {"revisions": plan.compiler["revisions"]} if (plan.compiler or {}).get("revisions") else {}
        plan.compiler = {**result.meta(), "criteria": out.acceptanceCriteria, "fileCheck": check, **kept}
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

    # ── shaping a plan before dispatch ───────────────────────────
    async def _draft(self, ref: str) -> Plan:
        """A plan a person may still shape: it exists and is not under way."""
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        if plan.status == "dispatched":
            raise Refused(f"{ref} is already under way, so its steps are settled. Compile a new requirement "
                          "to change the work.")
        return plan

    @staticmethod
    def _step(plan: Plan, step_id: str) -> PlanStep:
        found = next((x for x in plan.steps if x.id == step_id), None)
        if found is None:
            raise NotFound(f"step {step_id} of {plan.ref}")
        return found

    async def _owner(self, agent: str, project_id: str) -> str:
        """A step's owner as a person picks it: a roster name exactly, or by its exact name one of the custom
        agents the compiler is offered — a subagent of this project or the workspace that may write files, so a
        step is never handed to one that would fail it for want of the edit tool. Never a guess from a word."""
        wanted = agent.strip().casefold()
        name = next((n for n in roster.NAMES if n.casefold() == wanted), None)
        if name is None:
            writers = await CustomAgentService(self.session).for_compiler(await self.projects.get(project_id))
            name = next((a["name"] for a in writers if a["name"].casefold() == wanted), None)
        if name is None:
            raise Refused(f"{agent!r} is not an agent here. Pick one of: {', '.join(roster.NAMES)}, or a custom "
                          "agent of this project that may write files.", status=422)
        return name

    @staticmethod
    def _text(value: str, what: str, limit: int, *, required: bool) -> str:
        clean = " ".join(value.split()) if what == "label" else value.strip()
        if required and not clean:
            raise Refused(f"A step needs a {what}.", status=422)
        if len(clean) > limit:
            raise Refused(f"A step's {what} is at most {limit:,} characters.", status=422)
        return clean

    async def _renumber(self, plan: Plan, order: list[PlanStep]) -> None:
        """Number the steps 1..n in this order. Each step's number is unique within its plan, so the rows
        step aside to negative numbers first and take their places after."""
        for i, step in enumerate(order, 1):
            step.n = -i
        await self.session.flush()
        for i, step in enumerate(order, 1):
            step.n = i
        await self.session.flush()
        plan.steps.sort(key=lambda x: x.n)          # the list as loaded is in `n` order; keep it so

    async def _follow(self, plan: Plan) -> None:
        """What a plan's edit moves elsewhere: its task's checklist and agents follow its steps (an item
        already ticked stays ticked while its step is there), and the plan is marked changed, so every
        open tab is sent it — a step's row alone is not a document anyone holds."""
        task = await self.tasks.get(plan.task_id) if plan.task_id else None
        if task is not None:
            done = {i.label for i in task.checklist if i.done}
            # Emptied, then flushed, then filled: the new items take the old ids, and a row must be gone
            # before another with its key can be written.
            task.checklist.clear()
            task.assignees.clear()
            await self.session.flush()
            steps = sorted(plan.steps, key=lambda x: x.n)
            for step in steps:
                task.checklist.append(ChecklistItem(id=f"{task.id}-c{step.n}", n=step.n - 1, label=step.label,
                                                    done=step.label in done))
            for name in dict.fromkeys(x.agent for x in steps if x.agent != roster.COMMANDER):
                task.assignees.append(TaskAgent(agent=name))
        plan.updated_at = utcnow()
        await self.session.flush()

    async def _said(self, plan: Plan, action: str, detail: str, by: str) -> None:
        await self.activity.record(actor=by, actor_kind="human", action=action, detail=f"{plan.ref} · {detail}",
                                   level="ok", project_id=plan.project_id,
                                   task_ref=plan.task.ref if plan.task else None)

    async def edit_step(self, ref: str, step_id: str, *, label: str | None = None, agent: str | None = None,
                        detail: str | None = None, by: str, by_id: str | None) -> Plan:
        """Change a step's words or its owner. The activity line and the taste signal carry what it said
        before and after."""
        plan = await self._draft(ref)
        step = self._step(plan, step_id)
        before = _step_doc(step)
        if label is not None:
            step.label = self._text(label, "label", MAX_LABEL, required=True)
        if agent is not None:
            step.agent = await self._owner(agent, plan.project_id)
        if detail is not None:
            step.detail = self._text(detail, "detail", MAX_DETAIL, required=False)
        after = _step_doc(step)
        if after == before:
            return plan
        await self._follow(plan)
        changed = [f"{k} \"{_short(before[k])}\" → \"{_short(after[k])}\"" for k in ("label", "agent", "detail")
                   if before[k] != after[k]]
        await self._said(plan, "Plan step edited", f"step {step.n} · " + " · ".join(changed), by)
        await TasteService(self.session).on_plan_edit(plan.ref, plan.project_id, "edit", by_user_id=by_id,
                                                      step=step.n, before=before, after=after)
        return plan

    async def add_step(self, ref: str, *, label: str, agent: str, detail: str = "", at: int | None = None,
                       by: str, by_id: str | None) -> Plan:
        """A step of a person's own, at position `at` (1 is first), or last when no position is given."""
        plan = await self._draft(ref)
        if len(plan.steps) >= MAX_STEPS:
            raise Refused(f"{ref} already has {MAX_STEPS} steps. A plan that long is several plans.", status=422)
        doc = {"label": self._text(label, "label", MAX_LABEL, required=True),
               "agent": await self._owner(agent, plan.project_id),
               "detail": self._text(detail, "detail", MAX_DETAIL, required=False)}
        order = sorted(plan.steps, key=lambda x: x.n)
        place = len(order) if at is None else max(0, min(at - 1, len(order)))
        taken = {x.id for x in plan.steps}
        k = len(order) + 1
        while f"{plan.id}-s{k}" in taken:
            k += 1
        step = PlanStep(id=f"{plan.id}-s{k}", n=len(order) + 1, state="todo", **doc)
        plan.steps.append(step)
        await self.session.flush()
        await self._renumber(plan, [*order[:place], step, *order[place:]])
        await self._follow(plan)
        await self._said(plan, "Plan step added",
                         f"step {step.n} · \"{_short(step.label)}\" ({step.agent})" + (
                             f" · {_short(step.detail)}" if step.detail else ""), by)
        await TasteService(self.session).on_plan_edit(plan.ref, plan.project_id, "add", by_user_id=by_id,
                                                      step=step.n, after=_step_doc(step))
        return plan

    async def remove_step(self, ref: str, step_id: str, *, by: str, by_id: str | None) -> Plan:
        """Drop a step. A plan keeps at least one: with none there is nothing to dispatch."""
        plan = await self._draft(ref)
        step = self._step(plan, step_id)
        if len(plan.steps) <= 1:
            raise Refused(f"{step.label!r} is {ref}'s only step. Edit it instead, or compile a new requirement.")
        gone, n = _step_doc(step), step.n
        plan.steps.remove(step)                     # delete-orphan: out of the plan is out of the table
        await self.session.flush()
        await self._renumber(plan, sorted(plan.steps, key=lambda x: x.n))
        await self._follow(plan)
        await self._said(plan, "Plan step removed", f"step {n} · \"{_short(gone['label'])}\" ({gone['agent']})", by)
        await TasteService(self.session).on_plan_edit(plan.ref, plan.project_id, "remove", by_user_id=by_id,
                                                      step=n, before=gone)
        return plan

    async def reorder_steps(self, ref: str, order: Sequence[str], *, by: str, by_id: str | None) -> Plan:
        """Put the steps in a person's order: every step's id, each once."""
        plan = await self._draft(ref)
        by_id_ = {x.id: x for x in plan.steps}
        if sorted(order) != sorted(by_id_) or len(set(order)) != len(order):
            raise Refused(f"Name every step of {ref} once, in the order you want them.", status=422)
        before = [x.label for x in sorted(plan.steps, key=lambda x: x.n)]
        steps = [by_id_[i] for i in order]
        after = [x.label for x in steps]
        if before == after:
            return plan
        await self._renumber(plan, steps)
        await self._follow(plan)
        moved = [f"\"{_short(x.label, 40)}\" {before.index(x.label) + 1}→{x.n}" for x in steps
                 if before.index(x.label) + 1 != x.n]
        await self._said(plan, "Plan steps reordered", " · ".join(moved[:6]), by)
        await TasteService(self.session).on_plan_edit(plan.ref, plan.project_id, "move", by_user_id=by_id,
                                                      before=before, order=after)
        return plan

    # ── comments, and the revision they ask for ──────────────────
    async def comments(self, ref: str) -> tuple[Plan, list[PlanComment], dict[str, str]]:
        """A plan's comments, oldest first, and the names of the people who wrote them."""
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        found = list((await self.session.execute(
            select(PlanComment).where(PlanComment.plan_id == plan.id)
            .order_by(PlanComment.created_at, PlanComment.id).limit(MAX_COMMENTS))).scalars())
        return plan, found, await self.names([c.by_user_id for c in found])

    async def names(self, ids: Sequence[str | None]) -> dict[str, str]:
        """Account id → the person's name, for the ids given."""
        wanted = {i for i in ids if i}
        if not wanted:
            return {}
        return dict((await self.session.execute(select(User.id, User.name).where(User.id.in_(wanted)))).all())

    async def comment(self, ref: str, *, kind: str, body: str, step_id: str | None, by: str,
                      by_id: str | None) -> tuple[Plan, PlanComment]:
        """A person's note on the plan or on one step, kept with the revision it was written on."""
        plan = await self._draft(ref)
        if kind not in COMMENT_KINDS:
            raise Refused(f"A comment is one of: {', '.join(COMMENT_KINDS)}.", status=422)
        text = body.strip()
        if not text:
            raise Refused("Write what you want changed, or asked.", status=422)
        step = self._step(plan, step_id) if step_id else None
        if kind in ("split", "remove") and step is None:
            raise Refused(f"A {kind!r} comment is about one step. Put it on the step.", status=422)
        held = (await self.session.execute(select(func.count()).select_from(PlanComment)
                                           .where(PlanComment.plan_id == plan.id))).scalar_one()
        if held >= MAX_COMMENTS:
            raise Refused(f"{ref} already holds {MAX_COMMENTS} comments. Revise it, or compile it anew.")
        made = PlanComment(plan_id=plan.id, step_id=step.id if step else None, kind=kind, body=text[:4_000],
                           revision=plan.revision, by_user_id=by_id)
        self.session.add(made)
        await self.session.flush()
        where = f"step {step.n}" if step else "the plan"
        await self._said(plan, "Plan commented", f"{kind} on {where} · {_short(text, 120)}", by)
        return plan, made

    async def resolve_comment(self, ref: str, comment_id: int, *, resolved: bool, by: str) -> tuple[Plan, PlanComment]:
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        found = await self.session.get(PlanComment, comment_id)
        if found is None or found.plan_id != plan.id:
            raise NotFound(f"comment {comment_id} on {ref}")
        if found.resolved != resolved:
            found.resolved = resolved
            await self.session.flush()
            await self._said(plan, "Plan comment resolved" if resolved else "Plan comment reopened",
                             f"{found.kind} · {_short(found.body, 120)}", by)
        return plan, found

    async def revise(self, ref: str, *, by: str, by_id: str | None) -> tuple[Plan, list[dict[str, Any]]]:
        """Hand the open comments to the compiler and write what it returns as the next revision.

        The steps being replaced are kept on the plan with the comments that replaced them and the
        compiler's replies; the comments are resolved and keep the revision they were written on.
        Answered and deferred questions stay settled; criteria a person wrote stay theirs. Refused, with
        nothing changed, when there is no open comment or no model can answer. Returns the plan and what
        changed between the two revisions, step by step."""
        plan = await self._draft(ref)
        project = await self.projects.get(plan.project_id)
        if project is None:
            raise NotFound(f"project {plan.project_id}")
        _, found, _ = await self.comments(ref)
        open_ = [c for c in found if not c.resolved]
        if not open_:
            raise Refused(f"{ref} has no open comments to revise with. Comment on a step or on the plan first.")
        steps = {x.id: x for x in plan.steps}
        handed = [{"id": c.id, "kind": c.kind, "body": c.body,
                   "step": {"n": steps[c.step_id].n, "label": steps[c.step_id].label}
                   if c.step_id in steps else None} for c in open_]
        before = [_step_doc(x) for x in sorted(plan.steps, key=lambda x: x.n)]
        current = {"steps": before, "openQuestions": [q.question for q in plan.questions if not q.answer and not q.deferred],
                   "affectedFiles": list(plan.affected_files or []),
                   "acceptanceCriteria": list(plan.acceptance_criteria or [])}
        answers = [{"q": q.question, "a": q.answer} for q in plan.questions if q.answer]
        context, grounding = await self._ground(project, plan.raw_requirement, answers, plan.affected_files or [])
        result: Result[RevisionOut] = await asyncio.to_thread(needs_a_model, lambda: revise_plan(
            self.gateway, plan.raw_requirement, context, current, handed, actor=by_id, project=project.id))
        out = result.data

        replies = {r.comment: r.reply.strip()[:1_000] for r in out.replies}
        edited = _criteria_edited(plan)
        settled = {q.question for q in plan.questions if q.answer or q.deferred}
        # Taken out of the collections, not only deleted: the plan is read again before this request ends,
        # and a deleted row still sitting in its list would be shown beside its replacement.
        plan.steps.clear()
        for question in [q for q in plan.questions if q.question not in settled]:
            plan.questions.remove(question)
        # The database lets go of a deleted step's comments (SET NULL); the ones held here are told the
        # same, so a new step that takes an old step's id is never mistaken for the step they were on.
        for c in found:
            c.step_id = None
        await self.session.flush()
        for i, step in enumerate(out.steps, 1):
            plan.steps.append(PlanStep(id=f"{plan.id}-s{i}", n=i, label=step.label[:MAX_LABEL], agent=step.agent,
                                       state="todo", detail=step.detail[:MAX_DETAIL]))
        start = max((q.n for q in plan.questions), default=-1) + 1
        for i, question in enumerate(q for q in dict.fromkeys(out.openQuestions) if q not in settled):
            plan.questions.append(PlanQuestion(id=f"{plan.id}-q{start + i}", n=start + i, question=question))

        compiled = dict(plan.compiler or {})
        if out.affectedFiles:
            files, check = await self._check_files(plan.project_id, out.affectedFiles)
            plan.affected_files, compiled["fileCheck"] = files, check
        if out.acceptanceCriteria and not edited:
            plan.acceptance_criteria = out.acceptanceCriteria
            compiled["criteria"] = out.acceptanceCriteria
        after = [_step_doc(x) for x in sorted(plan.steps, key=lambda x: x.n)]
        snapshot = {"revision": plan.revision, "at": utcnow().isoformat(timespec="seconds"), "by": by,
                    "steps": before, "summary": out.summary,
                    "model": f"{result.provider.id} · {result.provider.model}",
                    "comments": [{**c, "reply": replies.get(c["id"], "")} for c in handed]}
        compiled["revisions"] = [*(compiled.get("revisions") or []), snapshot][-MAX_REVISIONS:]
        plan.compiler = compiled
        plan.grounding = grounding
        plan.revision += 1
        for c in open_:
            c.resolved = True
        await self.session.flush()
        await self._follow(plan)

        changes = step_changes(before, after)
        tally = {op: sum(1 for x in changes if x["op"] == op) for op in ("added", "removed", "changed", "moved")}
        said = ", ".join(f"{n} {op}" for op, n in tally.items() if n) or "no step changed"
        await self._said(plan, "Plan revised", f"revision {plan.revision - 1} → {plan.revision} · "
                         f"{len(open_)} comment{'s' if len(open_) != 1 else ''} · {len(after)} steps ({said}) · "
                         f"{result.provider.model}", by)
        await TasteService(self.session).on_plan_edit(
            plan.ref, plan.project_id, "revise", by_user_id=by_id, revision=plan.revision,
            comments=[{"kind": c["kind"], "body": _short(c["body"], 300),
                       "step": c["step"]["label"] if c["step"] else None} for c in handed])
        return plan, changes

    # ── dispatching ──────────────────────────────────────────────
    async def dispatch(self, ref: str, *, by: str, may_run: bool, goal_budget: int | None = None,
                       step_gate: bool = False, skip_questions: bool = False) -> tuple[Plan, list[Any]]:
        """Settle the gate, then hand the work to the runtime. Returns the plan and the runs to start.

        `goal_budget` (1–5) is "run until done": the run ends with a completion check against the plan's
        acceptance criteria and tries again on its own while it misses and attempts remain. A goal
        needs criteria to be judged against, so a plan without them is refused before anything moves.
        `step_gate` is "pause before each step": the runtime stops at an approval between one step and
        the next (`step_gate_for`), so a person reads each step's work before the next one starts.
        `skip_questions` is a person's "skip and start": every open question is deferred in their name — it
        stays on the plan, marked, and nothing is answered for them — and the plan goes."""
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        if plan.status == "dispatched":
            raise Refused(f"{ref} is already under way.")
        open_questions = await self.plans.open_questions(plan.id)
        if open_questions and skip_questions:
            for question in open_questions:
                question.deferred = True
            await self.session.flush()
            await self.activity.record(
                actor=by, actor_kind="human", action="Questions skipped",
                detail=f"{ref} · {len(open_questions)} deferred at dispatch: "
                       + "; ".join(q.question for q in open_questions)[:400], level="warn",
                project_id=plan.project_id)
            open_questions = []
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
        plan.step_gate = step_gate
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
                   + (f" · {first.agent} starts: {first.label}" if first else "")
                   + (" · pausing before each step" if step_gate else ""),
            level="ok", project_id=plan.project_id, task_ref=task.ref if task else None)

        project = await self.projects.get(plan.project_id)
        if not may_run:
            self.no_run = "Starting its runs needs the runs:run permission; the plan waits for someone who has it."
            return plan, []
        if project is None or not project.source_kind:
            self.no_run = "This project has no code on this machine, so there is nothing for an agent to work in."
            return plan, []
        try:
            made = await RunService(self.session, self.gateway).plan_runs(plan, task, project, by,
                                                                          goal_budget=goal_budget)
        except Refused as refused:      # no code here, no git, nothing to branch from: say so, don't fail
            self.no_run = str(refused)
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


def _short(value: Any, limit: int = 80) -> str:
    said = " ".join(str(value or "").split())
    return said if len(said) <= limit else said[: limit - 1].rstrip() + "…"


def _step_doc(step: PlanStep) -> dict[str, Any]:
    return {"n": step.n, "label": step.label, "agent": step.agent, "detail": step.detail}


# ── pausing between steps ─────────────────────────────────────────
#: The tool a step gate's approval names, as `Step(<n>)`: `schemas/work.gate_kind` reads it as a "step" gate.
STEP_GATE_TOOL = "Step"


@dataclass(frozen=True, slots=True)
class StepGate:
    """What the runtime's pause for a step gate says: the fields of the approval it waits on."""

    title: str
    tool: str
    risk: str
    payload: str
    reason: str


def is_step_gate(tool: str) -> bool:
    """Whether an approval is a step gate's — the runtime's resume reads its answer as "go on" or "stop"."""
    return tool.split("(", 1)[0].strip() == STEP_GATE_TOOL


async def step_gate_for(session: AsyncSession, run: Run, step: RunStep) -> StepGate | None:
    """The approval a run must wait on before `step`, or None to go straight on.

    A plan dispatched "pause before each step" (`plans.step_gate`) stops its runs between edit steps: before
    every edit step but a run's first — dispatching was the go-ahead for that one — the runtime pauses on
    the approval this returns, and starts the step only once a person approved it. None for a plan without
    the gate, a step that is not an edit, a run not made from a plan, or a step already approved.

    The runtime owns the pause and the resume (`services/runs.py`): before running an edit step it asks
    this, and pauses with the fields it returns; on the answer, an approval runs the step, a refusal stops
    the run with the worktree kept. Pure reads — it writes nothing.
    """
    if step.kind != "edit" or run.plan_id is None:
        return None
    gated = (await session.execute(select(Plan.step_gate).where(Plan.id == run.plan_id))).scalar_one_or_none()
    if not gated:
        return None
    edits = sorted((x for x in run.steps if x.kind == "edit"), key=lambda x: x.n)
    earlier = [x for x in edits if x.n < step.n]
    if not earlier:
        return None
    answered = (await session.execute(
        select(Approval.status).where(Approval.run_ref == run.ref, Approval.step == step.n,
                                      Approval.tool.like(f"{STEP_GATE_TOOL}(%"), Approval.status == "approved")
        .limit(1))).scalar_one_or_none()
    if answered is not None:
        return None
    last = earlier[-1]
    position = edits.index(step) + 1
    lines = [f"done: step {last.n} · {last.label} ({last.agent}) — {last.status}"
             + (f" · {last.detail}" if last.detail else ""),
             f"next: step {step.n} · {step.label} ({step.agent})",
             *([f"asked: {step.detail}"] if step.detail else []),
             f"so far on {run.branch}: {run.diff_files} files · +{run.diff_insertions} −{run.diff_deletions}"]
    return StepGate(
        title=f"Continue {run.ref}: step {position} of {len(edits)} · {_short(step.label, 120)}",
        tool=f"{STEP_GATE_TOOL}({step.n})", risk="LOW", payload="\n".join(lines),
        reason=f"This plan was dispatched to pause before each step. Read what step {last.n} left in the "
               f"worktree ({run.branch}); approve and {step.agent} starts step {step.n}, refuse and the run "
               f"stops here with its worktree kept for you to read.")
