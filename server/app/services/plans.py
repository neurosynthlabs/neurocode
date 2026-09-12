"""The requirement compiler, and what happens when a plan is dispatched.

A requirement in English or Hinglish goes in; a plan, a task and — once nothing is left open — a run
comes out. The compiler itself is unchanged and still lives in `ai/compiler.py`: it is handed the
memory facts that match the requirement and records which ones, so a reader can check what the plan
was based on. With no model, the keyword planner stands in and the plan says so.

What this service adds is the part that must be true: a plan's questions are **rows**, so "nothing is
left open" is a query rather than a promise, and dispatching refuses until they are settled.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.compiler import AGENTS, Context, PlanOut, compile_plan
from ..ai.gateway import Gateway, Result
from ..models import ChecklistItem, Plan, PlanQuestion, PlanStep, Project, Task, TaskAgent
from ..repositories import (
    ActivityRepository,
    MemoryRepository,
    NotFound,
    PlanRepository,
    ProjectRepository,
    TaskRepository,
)
from .errors import Refused
from .runs import RunService

FACTS_FOR_CONTEXT = 6


def _project_doc(project: Project) -> dict[str, Any]:
    """What the compiler is told about the project, from the row rather than a stored document."""
    return {"id": project.id, "name": project.name, "stack": project.stack or [],
            "description": project.description}


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

    async def _run_compiler(self, project: Project, requirement: str, answers: list[dict[str, str]],
                            actor: str | None) -> tuple[Result[PlanOut], list[str]]:
        facts = await self._facts(requirement, project.id)
        result, cited = await asyncio.to_thread(
            compile_plan, self.gateway, requirement,
            Context(project=_project_doc(project), facts=facts, answers=answers),
            actor=actor, project=project.id)
        if result.fallback:
            await self.activity.record(
                actor="AI Commander", actor_kind="agent", action="Compiler fell back",
                detail=f"{result.fallback}. The offline planner stood in.", level="warn",
                project_id=project.id)
        return result, cited

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

        result, cited = await self._run_compiler(project, text, [], by_id)
        out = result.data
        task_ref = await self.tasks.next_ref()
        n = task_ref.split("-")[-1]

        # The empty collections are given on purpose: a freshly persisted object's relationships are
        # unloaded, and touching one inside async code raises instead of loading. Handed in here, they
        # are loaded from the start — so the steps and questions appended below are really in them.
        task = await self.tasks.add(Task(
            id=f"t{n}", ref=task_ref, title=out.title, project_id=project.id, status="planning",
            priority=out.priority, risk=out.risk, requirement=text,
            layers=out.layers or ["Backend"], files=len(out.affectedFiles),
            checklist=[], assignees=[]))
        plan = await self.plans.add(Plan(
            id=f"p{n}", ref=f"PLAN-{n}", task_id=task.id, project_id=project.id, status="draft",
            steps=[], questions=[],
            raw_requirement=text, business_requirement=out.businessRequirement,
            technical_requirement=out.technicalRequirement, architecture_impact=out.architectureImpact,
            risk=out.risk, confidence=out.confidence, requested_by=by,
            affected_modules=out.affectedModules, affected_files=out.affectedFiles,
            affected_db=out.affectedDb, test_plan=out.testPlan, cited=cited, compiler=result.meta()))
        await self._write_steps(plan, task, out)

        await self.activity.record(
            actor="AI Commander", actor_kind="agent", action="Requirement compiled",
            detail=f"{plan.ref} · {len(out.steps)} steps · {len(out.openQuestions)} open questions · "
                   f"{result.provider.model}, {result.ms / 1000:.1f}s",
            level="ok", project_id=project.id, task_ref=task.ref)
        return plan, task

    async def _write_steps(self, plan: Plan, task: Task | None, out: PlanOut) -> None:
        """The plan's steps and questions, and the task's checklist, all as rows."""
        for i, step in enumerate(out.steps, 1):
            plan.steps.append(PlanStep(id=f"{plan.id}-s{i}", n=i, label=step.label, agent=step.agent,
                                       state="todo", detail=step.detail))
        for i, question in enumerate(out.openQuestions):
            plan.questions.append(PlanQuestion(id=f"{plan.id}-q{i}", n=i, question=question))
        if task is not None:
            for i, step in enumerate(out.steps, 1):
                task.checklist.append(ChecklistItem(id=f"{task.id}-c{i}", n=i - 1, label=step.label,
                                                    done=False))
            for name in dict.fromkeys(s.agent for s in out.steps if s.agent != "AI Commander"):
                task.assignees.append(TaskAgent(agent=name))
        await self.session.flush()

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
        result, cited = await self._run_compiler(project, plan.raw_requirement, answers, by_id)
        out = result.data

        for step in list(plan.steps):
            await self.session.delete(step)
        for question in list(plan.questions):
            if question.question not in settled:
                await self.session.delete(question)
        await self.session.flush()

        plan.business_requirement, plan.technical_requirement = out.businessRequirement, out.technicalRequirement
        plan.architecture_impact, plan.risk, plan.confidence = out.architectureImpact, out.risk, out.confidence
        plan.affected_modules, plan.affected_files = out.affectedModules, out.affectedFiles
        plan.affected_db, plan.test_plan = out.affectedDb, out.testPlan
        plan.cited, plan.compiler = cited, result.meta()

        for i, step in enumerate(out.steps, 1):
            plan.steps.append(PlanStep(id=f"{plan.id}-s{i}", n=i, label=step.label, agent=step.agent,
                                       state="todo", detail=step.detail))
        start = max((q.n for q in plan.questions), default=-1) + 1
        for i, question in enumerate(q for q in out.openQuestions if q not in settled):
            plan.questions.append(PlanQuestion(id=f"{plan.id}-q{start + i}", n=start + i,
                                               question=question))
        await self.session.flush()

        await self.activity.record(actor=by, actor_kind="human", action="Plan re-compiled",
                                   detail=f"{ref} · {len(out.steps)} steps · {result.provider.model}",
                                   level="ok", project_id=plan.project_id)
        return plan

    # ── dispatching ──────────────────────────────────────────────
    async def dispatch(self, ref: str, *, by: str, may_run: bool) -> tuple[Plan, list[Any]]:
        """Settle the gate, then hand the work to the runtime. Returns the plan and the runs to start."""
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
            made = await RunService(self.session, self.gateway).plan_runs(plan, task, project, by)
        except Refused as refused:      # no code here, no git, nothing to branch from: say so, don't fail
            await self.activity.record(actor="Orchestrator", actor_kind="agent", action="No run started",
                                       detail=str(refused), level="warn", project_id=plan.project_id)
            return plan, []
        lead = made[-1]
        await self.activity.record(
            actor=by, actor_kind="human", action="Run started",
            detail=f"{lead.ref} · " + (f"{len(made) - 1} agents in parallel, merging into {lead.branch}"
                                       if len(made) > 1 else f"worktree on {lead.branch}"),
            level="ok", project_id=plan.project_id, task_ref=task.ref if task else None)
        return plan, made
