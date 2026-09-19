"""Workflows: steps a person wrote once, run as ordinary plans.

There is no second engine here, and that is the design rather than a shortcut. A workflow is a list of
write steps — which agent does what — and a requirement with a slot for today's input. Running one
writes a plan with those steps and dispatches it, so the runtime that already keeps a run safe is the
one that runs it: a worktree per agent, the merge, the project's own tests behind your approval, the
review, and your signature.

That also fixes what a workflow can honestly promise. The runtime's shape after the writing is fixed,
so the phases shown for a workflow are derived from what the runtime *will* do in a given project — a
Test phase only where that project has a test command to run — and never stored, so they cannot drift
from the code that decides them.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import agent
from ..ai.gateway import Gateway
from ..ai.lanes import price_of, priced_call
from ..data import roster
from ..data.base import utcnow
from ..models import Plan, Project, Run, Task, WorkflowDefinition, WorkflowStep
from ..repositories import ActivityRepository, NotFound, ProjectRepository
from ..repositories.workflows import Spend, WorkflowRepository
from ..schemas.work import when
from ..schemas.workflows import steps_json, workflow_json
from .code import checkout
from .custom_agents import CustomAgentService
from .errors import Refused
from .plans import PlanService
from .runs import GATE_WORDS

#: The workflow nobody wrote: a requirement compiled into a plan. It has no row; its runs are the runs
#: of plans with no workflow.
BUILTIN = "requirement-to-pr"
MAX_STEPS = 20
SLOT = "{input}"
MANUAL = "manual · Run button"


@dataclass(slots=True)
class StepDraft:
    label: str
    agent: str
    detail: str = ""


@dataclass(slots=True)
class WorkflowDraft:
    name: str
    description: str
    project_id: str | None
    requirement_template: str
    steps: list[StepDraft]


@dataclass(slots=True)
class Started:
    """What running a workflow produced. `run` is None when nothing started, and `note` says why."""

    plan: Plan
    task: Task
    run: Run | None
    agents: int
    open_questions: int
    note: str


def _cost(lines: list[Spend]) -> float | None:
    """What the lines cost, or None when any of them ran a model its lane has no price for: a partial sum
    shown as the whole would read as a measured total."""
    if not all(priced_call(s.lane, s.model) for s in lines if s.tokens_in or s.tokens_out):
        return None
    return round(sum(s.tokens_in / 1e6 * price_of(s.lane)[0] + s.tokens_out / 1e6 * price_of(s.lane)[1]
                     for s in lines), 4)


def _tokens(lines: list[Spend] | None) -> int | None:
    """None when the ledger holds no line for it: nothing was asked of a model, so nothing was measured."""
    return sum(s.tokens_in + s.tokens_out for s in lines) if lines else None


def _result(run: Run, flawed: set[str]) -> str:
    if run.status != "done":
        return "failed"
    return "partial" if run.id in flawed else "success"


def _phase(n: int, title: str, detail: str, mode: str, agents: int | None) -> dict[str, Any]:
    # Only a live run has states. A library entry describes what would happen, so every phase is todo.
    return {"id": f"ph{n}", "title": title, "detail": detail, "mode": mode, "agents": agents, "state": "todo"}


def phases(steps: list[StepDraft] | None, tests: dict[str, Any]) -> list[dict[str, Any]]:
    """What the runtime will do, in order. `steps` is None for the built-in, whose steps the compiler
    decides; `tests` is what was found in the project the phases are drawn for."""
    out: list[dict[str, Any]] = []
    if steps is None:
        out.append(_phase(1, "Compile", "The requirement becomes a plan. A question it cannot settle stops "
                                        "here for you; nothing is guessed.", "single", 0))
        out.append(_phase(2, "Write", "One agent per owner the plan names, each in a worktree of its own.",
                          "parallel", None))
        out.append(_phase(3, "Merge", "Only when the plan names more than one agent: their branches come "
                                      "together one by one, and a collision is named, never half-applied.",
                          "single", 0))
    else:
        owners = list(dict.fromkeys(s.agent for s in steps))
        if len(owners) > 1:
            out.append(_phase(1, "Write", f"{', '.join(owners)} at once, each in a worktree of its own.",
                              "parallel", len(owners)))
            out.append(_phase(2, "Merge", "Their branches come together one by one; a collision is named, "
                                          "never half-applied.", "single", 0))
        else:
            out.append(_phase(1, "Write", f"{owners[0] if owners else 'One agent'} · {len(steps)} "
                                          f"step{'s' if len(steps) != 1 else ''}, one after another.",
                              "pipeline", 1))
    if tests.get("command"):
        out.append(_phase(len(out) + 1, "Test", f"`{tests['command']}` in {tests['project']}, the project's own "
                                                "command. The first time, it waits for your approval.",
                          "single", 0))
    out.append(_phase(len(out) + 1, "Review", "Reads the real diff. Prefers another lane than the one that "
                                              "wrote it, and falls back to rules when no model answers.",
                      "single", 1))
    out.append(_phase(len(out) + 1, "Gate", "Stops for your signature when a file changed. Nothing is merged "
                                            "until you merge it.", "single", 0))
    return out


def definition_text(steps: list[StepDraft] | None, template: str, tests: dict[str, Any]) -> str:
    """The definition the runtime executes, in words — there is no script behind it to show instead."""
    lines = [f"Requirement: {template}", ""]
    if steps is None:
        lines += ["Compile: the requirement becomes a plan; its steps and their owners are the compiler's.",
                  "Write: one agent per owner, each on its own branch in its own worktree.",
                  "Merge: when there is more than one agent, their branches, one by one."]
    else:
        lines += [f"Write {s.agent} · {s.label}" + (f"\n    {s.detail}" if s.detail else "") for s in steps]
        if len({s.agent for s in steps}) > 1:
            lines.append("Merge their branches, one by one")
    if tests.get("command"):
        lines.append(f"Test: {tests['command']} (in {tests['project']}, behind your approval the first time)")
    elif tests.get("project"):
        lines.append(f"Test: none — no test command was found in {tests['project']}")
    else:
        lines.append("Test: the project's own command, if the project it runs in has one")
    lines += ["Review the diff (prefers another lane; rules when no model answers)",
              "Wait for your approval"]
    return "\n".join(lines)


class WorkflowService:
    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.workflows = WorkflowRepository(session)
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    # ── reading ──────────────────────────────────────────────────
    async def _tests(self, project: Project | None) -> dict[str, Any]:
        """The test command the runtime would find in this project's checkout, found the same way."""
        if project is None:
            return {"project": None, "command": None}
        root = checkout(project)
        if root is None:
            return {"project": project.name, "command": None}
        found = await asyncio.to_thread(_detect, root)
        return {"project": project.name, "command": found["command"] if found else None}

    async def _results(self) -> dict[str | None, str]:
        last = await self.workflows.last_finished()
        flawed = await self.workflows.blemishes([r.id for r in last.values()])
        return {wid: _result(run, flawed) for wid, run in last.items()}

    async def library(self, project_id: str | None) -> list[dict[str, Any]]:
        """Every workflow, the built-in first, with phases drawn for the project named, or their own."""
        rows = await self.workflows.active()
        stats, results = await self.workflows.stats(), await self._results()
        used = await self.workflows.referenced_ids()
        wanted = await self.projects.get(project_id) if project_id else None
        owners = {p.id: p for p in [await self.projects.get(pid) for pid in
                                    {w.project_id for w in rows if w.project_id}] if p is not None}
        tests_for: dict[str | None, dict[str, Any]] = {}
        for pid, project in [(wanted.id if wanted else None, wanted), *owners.items()]:
            if pid not in tests_for:
                tests_for[pid] = await self._tests(project)

        out = [self._builtin(stats, results, tests_for[wanted.id if wanted else None])]
        for w in rows:
            tests = tests_for[w.project_id if w.project_id else (wanted.id if wanted else None)]
            out.append(self._json(w, owners.get(w.project_id or ""), stats, results, tests,
                                  referenced=w.id in used))
        return out

    def _builtin(self, stats: dict[str | None, Any], results: dict[str | None, str],
                 tests: dict[str, Any]) -> dict[str, Any]:
        return workflow_json(
            id=BUILTIN, name=BUILTIN, builtin=True, scope="global", project_id=None, project_name=None,
            description="A requirement in your own words, compiled into a plan, written by the agents it "
                        "names, tested, reviewed, and stopped at your signature. The compiler decides the "
                        "steps, so a question it cannot settle waits for you in Plans.",
            trigger="on dispatch · Plans, or the Run button", phases=phases(None, tests),
            stats=stats.get(None), last_result=results.get(None), tests=tests)

    def _json(self, w: WorkflowDefinition, project: Project | None, stats: dict[str | None, Any],
              results: dict[str | None, str], tests: dict[str, Any], *, referenced: bool) -> dict[str, Any]:
        drafts = [StepDraft(s.label, s.agent, s.detail) for s in w.steps]
        return workflow_json(
            id=w.id, name=str(w.name), description=w.description, builtin=False,
            scope="project" if w.project_id else "global", project_id=w.project_id,
            project_name=project.name if project else None, trigger=MANUAL, phases=phases(drafts, tests),
            stats=stats.get(w.id), last_result=results.get(w.id), tests=tests, referenced=referenced)

    async def detail(self, workflow_id: str, project_id: str | None) -> dict[str, Any]:
        wanted = await self.projects.get(project_id) if project_id else None
        stats, results = await self.workflows.stats(), await self._results()
        if workflow_id == BUILTIN:
            tests = await self._tests(wanted)
            return {**self._builtin(stats, results, tests), "requirementTemplate": SLOT, "steps": [],
                    "archived": False, "definitionText": definition_text(None, SLOT, tests)}
        w = await self._get(workflow_id)
        owner = await self.projects.get(w.project_id) if w.project_id else None
        tests = await self._tests(owner or wanted)
        drafts = [StepDraft(s.label, s.agent, s.detail) for s in w.steps]
        return {**self._json(w, owner, stats, results, tests, referenced=await self.workflows.referenced(w.id)),
                "requirementTemplate": w.requirement_template,
                "steps": steps_json(w), "archived": w.archived,
                "definitionText": definition_text(drafts, w.requirement_template, tests)}

    async def _get(self, workflow_id: str) -> WorkflowDefinition:
        found = await self.workflows.get(workflow_id)
        if found is None:
            raise NotFound(f"workflow {workflow_id}")
        return found

    async def overview(self) -> dict[str, Any]:
        """The numbers above the library, the run under way, and the last runs that finished."""
        ready = await asyncio.to_thread(self.gateway.report)
        today = await self.workflows.spend_today()
        live = await self.workflows.live()
        history = await self.workflows.history()
        return {
            "stats": {"workflows": await self.workflows.active_count() + 1,
                      "runsTotal": await self.workflows.lead_count(),
                      "liveNow": await self.workflows.under_way_count(),
                      "agentsInFlight": await self.workflows.running_agents(),
                      "lanesOpen": sum(1 for lane in ready if lane.get("ready")),
                      "tokensToday": sum(s.tokens_in + s.tokens_out for s in today),
                      "costToday": _cost(today)},
            "live": await self._live(live) if live else None,
            "history": await self._history(history),
            "writers": [name for name in await self.workflows.roster() if name not in roster.NOT_WRITERS]
                       + [a["name"] for a in await CustomAgentService(self.session).for_compiler(None)],
        }

    async def _live(self, lead: Run) -> dict[str, Any]:
        children = await self.workflows.children(lead.id)
        workers = children or [lead]
        spend = await self.workflows.spend([lead.id, *(c.id for c in children)])
        wid = await self.workflows.workflow_of(lead.plan_id)
        name = (await self.workflows.names([wid])).get(wid, wid) if wid else BUILTIN
        now = utcnow()

        def elapsed(run: Run) -> int:
            return round(((run.finished_at or now) - run.created_at).total_seconds() * 1000)

        rows: list[dict[str, Any]] = [
            {"label": run.agent or "solo", "runRef": run.ref, "phase": "write", "state": RUN_STATE[run.status],
             "ms": elapsed(run),
             "tokens": _tokens([s for s in spend.get(run.id, []) if run is not lead or s.feature == "agent"])}
            for run in workers]
        for step in lead.steps:
            if step.kind == "edit":
                continue
            lines = [s for s in spend.get(lead.id, []) if s.feature == "review"] if step.kind == "review" else []
            rows.append({"label": step.label, "runRef": lead.ref, "phase": step.kind,
                         "state": STEP_STATE[step.status], "ms": step.ms, "tokens": _tokens(lines)})

        current = next((s.kind for s in lead.steps if s.status in ("running", "waiting")), None)
        if current is None:
            current = "write" if any(r.status in ("queued", "running") for r in workers) else "queued"
        skipped = [f"{s.label}: {s.detail}" for run in [lead, *children] for s in run.steps
                   if s.status == "skipped"]
        skipped += [f"{c.agent or c.branch} collided in {', '.join((c.files or [])[:5])}" for c in lead.conflicts]
        return {
            "ref": lead.ref, "workflow": name, "workflowId": wid or BUILTIN, "status": lead.status,
            "startedAt": when(lead.created_at), "phase": current, "waitingOn": lead.waiting_on,
            "completed": sum(1 for r in rows if r["state"] in ("done", "skipped", "failed")),
            "running": sum(1 for r in rows if r["state"] in ("active", "waiting")),
            "queued": sum(1 for r in rows if r["state"] == "todo"),
            "skipped": skipped, "agents": rows,
        }

    async def _history(self, rows: list[tuple[Run, str | None, str | None, int]]) -> list[dict[str, Any]]:
        leads = [run for run, _, _, _ in rows]
        families = await self.workflows.family_ids([r.id for r in leads])
        spend = await self.workflows.spend([rid for ids in families.values() for rid in ids])
        names = await self.workflows.names([wid for _, wid, _, _ in rows if wid])
        flawed = await self.workflows.blemishes([r.id for r in leads])
        out = []
        for run, wid, task_ref, agents in rows:
            lines = [s for rid in families.get(run.id, [run.id]) for s in spend.get(rid, [])]
            out.append({
                "id": run.id, "runRef": run.ref, "workflowId": wid or BUILTIN,
                "workflow": names.get(wid, wid) if wid else BUILTIN,
                "trigger": " · ".join(x for x in (run.requested_by, task_ref) if x),
                "agents": agents,
                "durationS": round((run.finished_at - run.created_at).total_seconds()) if run.finished_at else None,
                "tokens": _tokens(lines), "cost": _cost(lines) if lines else None,
                "result": _result(run, flawed), "status": run.status, "at": when(run.created_at),
            })
        return out

    # ── writing ──────────────────────────────────────────────────
    async def _check(self, draft: WorkflowDraft, *, current: str | None = None) -> None:
        name = draft.name.strip()
        if not name:
            raise Refused("Give the workflow a name.", status=422)
        if name.lower() == BUILTIN:
            raise Refused(f"{BUILTIN} is the built-in workflow. Choose another name.", status=422)
        same = await self.workflows.by_name(name)
        if same is not None and same.id != current:
            raise Refused(f"A workflow named {same.name} exists already"
                          + (" (archived)." if same.archived else "."))
        if SLOT not in draft.requirement_template:
            raise Refused(f"The requirement needs {SLOT} where the run's input goes; without it every run "
                          "would ask for the same thing.", status=422)
        if not 1 <= len(draft.steps) <= MAX_STEPS:
            raise Refused(f"A workflow has between 1 and {MAX_STEPS} steps.", status=422)
        project = await self.projects.get(draft.project_id) if draft.project_id else None
        if draft.project_id and project is None:
            raise NotFound(f"project {draft.project_id}")
        on_roster = set(await self.workflows.roster())
        # Beyond the roster, the custom agents that may write — the project's and the workspace's, or the
        # workspace's alone for a workflow that belongs to no project — exactly as the compiler is offered them.
        custom = {a["name"] for a in await CustomAgentService(self.session).for_compiler(project)}
        for i, step in enumerate(draft.steps, 1):
            if not step.label.strip():
                raise Refused(f"Step {i} needs a label.", status=422)
            # The runtime drops these owners' steps — you are the gate, and the commander only plans —
            # so a workflow step owned by one would be saved and then silently never run.
            if step.agent in roster.NOT_WRITERS:
                raise Refused(f"Step {i} is given to {step.agent}, and the runtime never gives a writing step "
                              "to them — it would be saved and never run.", status=422)
            if step.agent not in on_roster and step.agent not in custom:
                raise Refused(f"Step {i}: {step.agent} is neither on the roster nor a custom agent that may write "
                              "files here.", status=422)
            if any(word in step.label.lower() for word in GATE_WORDS):
                raise Refused(f"Step {i} asks for an approval. Every run already ends at your signature, and "
                              "the runtime skips a step that asks for one — so it would never run.", status=422)

    def _steps(self, draft: WorkflowDraft) -> list[WorkflowStep]:
        return [WorkflowStep(n=i, label=s.label.strip(), agent=s.agent, detail=s.detail.strip())
                for i, s in enumerate(draft.steps, 1)]

    async def create(self, draft: WorkflowDraft, *, by: str, by_id: str | None) -> WorkflowDefinition:
        await self._check(draft)
        workflow = await self.workflows.add(WorkflowDefinition(
            id=f"wf-{secrets.token_hex(5)}", name=draft.name.strip(), description=draft.description.strip(),
            project_id=draft.project_id or None, requirement_template=draft.requirement_template.strip(),
            created_by=by_id, steps=self._steps(draft)))
        await self.activity.record(actor=by, actor_kind="human", action="Workflow created",
                                   detail=f"{workflow.name} · {len(draft.steps)} steps", level="ok",
                                   project_id=workflow.project_id)
        return workflow

    async def update(self, workflow_id: str, draft: WorkflowDraft, *, by: str) -> WorkflowDefinition:
        """Fields and steps replaced together. Past runs are untouched: their steps were copied."""
        if workflow_id == BUILTIN:
            raise Refused("The built-in workflow is the compiler's; it cannot be edited.")
        workflow = await self._get(workflow_id)
        if workflow.archived:
            raise Refused(f"{workflow.name} is archived.")
        await self._check(draft, current=workflow.id)
        workflow.name, workflow.description = draft.name.strip(), draft.description.strip()
        workflow.project_id = draft.project_id or None
        workflow.requirement_template = draft.requirement_template.strip()
        # The old steps go before the new ones arrive: (workflow_id, n) is unique, so both at once collide.
        workflow.steps.clear()
        await self.session.flush()
        workflow.steps.extend(self._steps(draft))
        await self.session.flush()
        await self.activity.record(actor=by, actor_kind="human", action="Workflow changed",
                                   detail=f"{workflow.name} · {len(draft.steps)} steps", level="info",
                                   project_id=workflow.project_id)
        return workflow

    async def remove(self, workflow_id: str, *, by: str) -> bool:
        """Archived when a plan came from it, so the history keeps its name; deleted when none did.
        Returns whether it was archived."""
        if workflow_id == BUILTIN:
            raise Refused("The built-in workflow is how Plans dispatch; it cannot be removed.")
        workflow = await self._get(workflow_id)
        keep = await self.workflows.referenced(workflow.id)
        if keep:
            workflow.archived = True
            await self.session.flush()
        else:
            await self.workflows.remove(workflow)
        await self.activity.record(actor=by, actor_kind="human",
                                   action="Workflow archived" if keep else "Workflow deleted",
                                   detail=str(workflow.name), level="warn", project_id=workflow.project_id)
        return keep

    # ── running ──────────────────────────────────────────────────
    async def run(self, workflow_id: str, project_id: str, text: str, *, by: str, by_id: str | None,
                  may_run: bool) -> Started:
        text = text.strip()
        if len(text) < 3:
            raise Refused("Say what this run is for, in a sentence or two.", status=422)
        project = await self.projects.get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        workflow = None
        if workflow_id != BUILTIN:
            workflow = await self._get(workflow_id)
            if workflow.archived:
                raise Refused(f"{workflow.name} is archived, so it no longer runs.")
            if workflow.project_id and workflow.project_id != project.id:
                raise Refused(f"{workflow.name} belongs to another project and runs only there.")

        plans = PlanService(self.session, self.gateway)
        await plans.preflight(project)
        if workflow is None:
            plan, task = await plans.compile(project.id, text, by=by, by_id=by_id)
            waiting = await plans.plans.open_questions(plan.id)
            if waiting:
                n = len(waiting)
                return Started(plan=plan, task=task, run=None, agents=0, open_questions=n,
                               note=f"{plan.ref} has {n} open question{'s' if n > 1 else ''}. Answer "
                                    f"{'them' if n > 1 else 'it'} in Plans, then dispatch; nothing is guessed.")
        else:
            plan, task = await plans.from_workflow(workflow, project, text, by=by)

        plan, made = await plans.dispatch(plan.ref, by=by, may_run=may_run)
        if made:
            return Started(plan=plan, task=task, run=made[-1], agents=max(1, len(made) - 1),
                           open_questions=0, note="")
        note = ("Your role cannot run agents, so the plan is dispatched and no run started."
                if not may_run else "No run started. The activity log says why.")
        return Started(plan=plan, task=task, run=None, agents=0, open_questions=0, note=note)


RUN_STATE = {"queued": "todo", "running": "active", "waiting": "waiting", "done": "done",
             "failed": "failed", "cancelled": "failed"}
STEP_STATE = {"todo": "todo", "running": "active", "waiting": "waiting", "done": "done",
              "failed": "failed", "skipped": "skipped"}


def _detect(root: Path) -> dict[str, Any] | None:
    """Blocking: a checkout that has gone away has no test command rather than an error."""
    return agent.detect_tests(root) if root.is_dir() else None
