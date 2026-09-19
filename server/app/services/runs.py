"""The agent runtime, orchestrated.

The hands are in `app/agent` — blocking git, files and the project's own test command, none of which
know what a database is. This is the other half: it decides which step runs next, and writes down
what happened the moment it happens.

Three rules survive the move unchanged, because they are what make a run safe to leave alone:
nothing touches your working tree, nothing a model says is ever executed, and the only command that
runs is the project's own — the first time in a project, behind your approval.

Every step commits in a transaction of its own. A crash mid-run therefore loses the step in flight,
never the run: what was already done is already saved, and the screen shows exactly how far it got.

Every run is grounded and governed. Each agent step is handed the project's own instructions for the
files it may change and retrieval's pieces for its words, and the run keeps what it was handed. Every file
an agent writes and every command a run executes goes through the tool rules first: a deny ends the step
naming the rule, an ask stops the run at a gate a person answers (once, for the run, or always in the
project), and no rule leaves things as they were before rules existed. An agent may ask a question
instead of guessing; a plan may be walked step by step; and a run can be taken back to any of its steps.

A project with several sources is worked on as one. A run opens a worktree and a branch in every source
its plan's files fall in; each file an agent writes goes to the worktree its label names; each source's
own tests and checks run in its own worktree, behind approvals of their own; the review reads one
patch of every source with the paths under their labels; and a merge or a push acts on each source,
all of them or none. A project with one source runs exactly as it did before sources existed. A source
whose role is 'reference' is read for grounding from its own checkout and never gets a worktree.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import agent, onboarding
from ..agent import coverage as coverage_reports
from ..agent import testparse
from ..agent.testparse import all_expected
from ..agent.git import LEFTOVERS, SECRETS, TODO
from ..agent.review import brief as review_brief
from ..ai.gateway import REVIEW, WRITE, Gateway, NoModel, extract_json
from ..data import roster
from ..data.base import utcnow
from ..data.engine import Database
from ..models import Approval, CodeFile, CodeSymbol, Project, Run, RunConflict, RunStep, Setting
from ..repositories import (
    ActivityRepository,
    ApprovalRepository,
    NotFound,
    PlanRepository,
    ProjectRepository,
    RunLogRepository,
    RunRepository,
    TaskRepository,
)
from ..repositories.runtime import ResultsRepository
from ..schemas.work import gate_kind
from ..settings import settings
from . import instructions
from .taste import Applied, TasteService, applied_for
from .code import roots, writable
from .custom_agents import CustomAgentService
from .errors import Denied, Refused
from .retrieval import RetrievalService
from .tool_rules import decide as rule_for
from .tool_rules import normalise

log = logging.getLogger(__name__)



def worktrees_dir() -> Path:
    """Where runs make their worktrees: the setting DevOps reports, read when a run is made."""
    return settings().worktrees_dir


MAX_CONTEXT, MAX_DIFF, AGENT_TIMEOUT, TEST_LINES = 60_000, 200_000, 1800, 400
#: The largest coverage report read off disk. A report bigger than this is not one a runner wrote for us.
MAX_REPORT = 20_000_000
GATE_WORDS = ("approval", "approve", "sign-off", "sign off", "signature")
#: Who a goal run's completion check is filed under. It is a model judging, on a lane other than the
#: writer's, so it is not an agent on the roster.
GOAL_CHECK = "Completion check"
#: How many of a check's last lines are kept on the run, to show and to hand the reviewer.
CHECK_TAIL = 40
#: A person asking for the review again while one is being read: after this long it is taken as lost.
REVIEWING_FOR = 900
TEST_FILE = re.compile(r"(^|/)(tests?|spec)s?/|\.(test|spec)\.[jt]sx?$|_test\.py$|test_.*\.py$")
#: What one agent step is handed by retrieval: pieces asked for with room to spare, because the search
#: also returns remembered facts, which reach a model through memory instead; the ones kept; and how much
#: of each piece is shown. Code pieces also name files the step may read beyond the plan's list.
STEP_PIECES_ASKED, STEP_PIECES, PIECE_CHARS = 12, 5, 1_200
#: Files one step is handed to change, at most.
STEP_FILES = 8
#: How the runtime names its gates in `approvals.tool`; `schemas.work.gate_kind` reads the kind back. The
#: pause before a step is `Step(n)`, named by `plans.step_gate_for`, which says when a plan asks for one.
COMMAND_GATE, EDIT_GATE, QUESTION_GATE = "Command", "Edit", "Ask"

#: Runs a person stopped, in this process. A stop is a request, not a promise across a restart.
_STOPPED: dict[str, threading.Event] = {}


class FileOut(BaseModel):
    path: str
    content: str


class EditOut(BaseModel):
    summary: str = ""
    files: list[FileOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    #: Asked instead of writing, when the step needs a decision only the person can make.
    question: str = ""


class Finding(BaseModel):
    severity: str = "LOW"
    file: str = ""
    #: Where in the file, as the model wrote it — "42", "42-47" or 42 — made a number by `clean_findings`.
    line: int | str | None = None
    note: str


class ReviewOut(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    verdict: str = ""


EDIT_SYSTEM = """You are a senior engineer working inside NeuroCode. You get a requirement, one step of an agreed
plan, and the current contents of the files you may change. Return the complete new content of every file you
change — never a patch, never a fragment, never "// unchanged". Change as little as the step needs, keep the
file's existing style and imports, and never invent an API you cannot see in the files you were given. You may
add a new file beside the ones you are shown. You do not run commands and you never touch anything else.
A file marked read only belongs to a reference source: read it, never write it. When the step cannot be done
well without a decision only the person can make — a choice between behaviours, a value nobody wrote down — do
not guess: reply {"question": "one short question"} with no files, and you will be asked again with the answer.
Reply with one JSON object: {"summary": "what you changed and why", "files": [{"path": "...", "content": "..."}],
"notes": ["anything the operator must know"]}"""

REVIEW_SYSTEM = """You are the reviewer inside NeuroCode. You are given a real diff. Report only what a careful
engineer would stop at: correctness, a missing test for the behaviour that changed, a security or data risk, a
convention the surrounding code follows and this diff breaks. No style nits, no praise. Name the file as the diff
names it and the line in the new version of the file, when the finding is about one place.
Reply with one JSON object: {"findings": [{"severity": "HIGH|MEDIUM|LOW", "file": "...", "line": 42,
"note": "..."}], "verdict": "one sentence"}"""


#: The severities a finding may carry; anything else a model writes is read as LOW rather than dropped.
SEVERITIES = ("HIGH", "MEDIUM", "LOW")


def clean_findings(found: list[Finding]) -> list[dict[str, Any]]:
    """A model's findings as they are kept: at most twenty, a known severity, the line a number or nothing."""
    out: list[dict[str, Any]] = []
    for f in found[:20]:
        severity = f.severity.strip().upper()
        match = re.match(r"\s*(\d{1,7})", str(f.line)) if f.line is not None else None
        line = int(match.group(1)) if match and int(match.group(1)) > 0 else None
        out.append({"severity": severity if severity in SEVERITIES else "LOW", "file": f.file.strip()[:300],
                    **({"line": line} if line else {}), "note": f.note.strip()[:2000]})
    return out


def stopped(ref: str) -> threading.Event:
    return _STOPPED.setdefault(ref, threading.Event())


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "agent"


def _project_doc(project: Project) -> dict[str, Any]:
    """What `onboarding.source_root` needs, built from the model rather than a stored document."""
    return {"id": project.id, "name": project.name,
            "source": {"kind": project.source_kind, "repo": project.source_repo} if project.source_kind else None}


def _setup(project: Project) -> dict[str, Any]:
    """Where the code is, what a run branches from, and how the project runs its tests. Blocking."""
    root = onboarding.source_root(_project_doc(project))
    if root is None or not root.is_dir():
        raise Refused(f"{project.name} has no code on this machine, so there is nothing to work on. "
                      "Onboard a repository in Projects first.")
    found = agent.repo_of(root)
    if found is None:
        raise Refused(f"{project.name} is not a git repository, so a run has nothing to branch from.")
    repo, prefix = found
    head = agent.git(["rev-parse", "HEAD"], repo)
    if head.returncode != 0:
        raise Refused(f"{project.name} has no commit yet. Make one, and a run can branch from it.")
    return {"repo": repo, "prefix": prefix, "base": head.stdout.strip(), "tests": agent.detect_tests(root),
            "checks": agent.detect_checks(root)}


def _work(run: Run) -> Path:
    return Path(run.worktree) / run.prefix if run.prefix else Path(run.worktree)


# ── runs across several sources ──────────────────────────────────
@dataclass(slots=True)
class Part:
    """One checkout a run works in: its repository, the branch the run made there and the worktree it
    writes in. A run on a project with one source has one part, built from the run's own columns, whose
    label is empty — its paths are the checkout's own, exactly as before sources existed. A run that
    touches further sources has a part per source it touches, kept on the run (`review.sources`), and
    the label is how a project path is routed to it: `api/app/main.py` is `app/main.py` in the part
    labelled `api`. The first part is always the run's own columns."""

    label: str
    repo: Path
    prefix: str
    base: str
    branch: str
    worktree: Path

    @property
    def work(self) -> Path:
        return self.worktree / self.prefix if self.prefix else self.worktree

    @property
    def lead(self) -> str:
        return f"{self.label}/" if self.label else ""

    @property
    def name(self) -> str:
        """How the run's log and the signature call it."""
        return self.label or "the first source"

    def as_json(self) -> dict[str, Any]:
        return {"label": self.label, "repo": str(self.repo), "prefix": self.prefix, "base": self.base,
                "branch": self.branch, "worktree": str(self.worktree)}


def _parts(run: Run) -> list[Part]:
    listed = (run.review or {}).get("sources")
    if not listed:
        return [Part("", Path(run.repo), run.prefix, run.base, run.branch, Path(run.worktree))]
    return [Part(x["label"], Path(x["repo"]), x.get("prefix", ""), x["base"], x["branch"], Path(x["worktree"]))
            for x in listed]


def _elsewhere(run: Run) -> frozenset[str]:
    """The labels of the project's sources this run did not open — references among them: a path under
    one is refused, never taken for a folder of the first source."""
    return frozenset([*((run.review or {}).get("elsewhere") or []), *_references(run)])


def _references(run: Run) -> dict[str, Path]:
    """The project's reference sources as the run was made — label to checkout. A reference is read for
    grounding and never written: the run opens no worktree there, and an edit routed to one is refused."""
    return {x["label"]: Path(x["root"]) for x in (run.review or {}).get("references") or []}


def _route(parts: list[Part], path: str, elsewhere: frozenset[str] = frozenset()) -> tuple[Part, str] | None:
    """The part a project path belongs to and the path inside it — None when it is in a source this run
    did not open. On a single-source run every path is the one part's, unchanged."""
    head, _, rest = path.strip().replace("\\", "/").partition("/")
    for part in parts:
        if part.label and part.label == head:
            return part, rest
    if head in elsewhere:
        return None
    first = next((x for x in parts if not x.label), None)
    return (first, path) if first is not None else None


def _touched(labels: list[str], targets: list[str]) -> list[str]:
    """Which sources a plan's files fall in, in the project's order: "" is the first source, a label
    each further one. A plan that names no file works in the first source, as it always did."""
    hit: set[str] = set()
    for target in targets:
        head = str(target).strip().replace("\\", "/").split("/", 1)[0]
        hit.add(head if head in labels else "")
    return [x for x in ["", *labels] if x in hit]


def _setup_parts(project: Project, sources: list[Any], targets: list[str]) -> list[dict[str, Any]]:
    """Blocking. `_setup` for every source the plan's files fall in — each must be a git repository with
    a commit to branch from — the first one first. For a project with one source, exactly `_setup`."""
    # A reference source is never worked in, so it is never a source the run opens a worktree in.
    extras = {x.label: x for x in sources if not x.primary and writable(x)}
    if not extras:
        return [{**_setup(project), "label": ""}]
    wanted = _touched(list(extras), targets)
    first = next((x for x in sources if x.primary), None)
    if not wanted:
        wanted = [""] if first is not None else [next(iter(extras))]
    out: list[dict[str, Any]] = []
    for label in wanted:
        if not label:
            out.append({**_setup(project), "label": ""})
            continue
        source = extras[label]
        if not source.ready:
            raise Refused(f"The {label} source of {project.name} is still being onboarded, so a run cannot "
                          "branch from it yet.")
        if not source.root.is_dir():
            raise Refused(f"The {label} source of {project.name} is not on this machine any more.")
        found = agent.repo_of(source.root)
        if found is None:
            raise Refused(f"The {label} source of {project.name} is not a git repository, so a run has nothing "
                          "to branch from there.")
        repo, prefix = found
        head = agent.git(["rev-parse", "HEAD"], repo)
        if head.returncode != 0:
            raise Refused(f"The {label} source of {project.name} has no commit yet. Make one, and a run can "
                          "branch from it.")
        out.append({"repo": repo, "prefix": prefix, "base": head.stdout.strip(), "label": label,
                    "tests": agent.detect_tests(source.root), "checks": agent.detect_checks(source.root)})
    return out


def _extra_checks(setups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every labelled part's tests and checks, as checks named after its source — each behind a first-
    time approval of its own, because allowing the web app's `npm test` never allowed the API's command.
    A part's tests are marked so, and fail the run the way the tests of a single source do."""
    out: list[dict[str, Any]] = []
    for setup in setups:
        label = setup["label"]
        if not label:
            continue
        if setup["tests"]:
            out.append({"name": f"{label} tests", "command": setup["tests"]["command"], "label": label,
                        "tests": True})
        out += [{"name": f"{label} {c['name']}", "command": c["command"], "label": label}
                for c in setup["checks"]]
    return out


def _patch(parts: list[Part]) -> tuple[str, str]:
    """Blocking. The run's whole patch, read from its branches, and the commit the first part ends at.
    One part: exactly the branch's own patch. Several: each part's, its paths under its source's label,
    one after the other — so the review, the receipt, a merge and a push all read the same bytes."""
    if len(parts) == 1 and not parts[0].label:
        return agent.branch_diff(parts[0].repo, parts[0].base, parts[0].branch)
    patches: list[str] = []
    first = ""
    for n, part in enumerate(parts):
        patch, head = agent.branch_diff(part.repo, part.base, part.branch)
        if n == 0:
            first = head
        if not head:
            return "", ""
        patches.append(agent.label_patch(patch, part.label, part.prefix))
    return "".join(patches), first


def _stats(parts: list[Part]) -> dict[str, int]:
    """Blocking. What the run changed across every part, measured with git."""
    total = {"files": 0, "insertions": 0, "deletions": 0, "commits": 0}
    for part in parts:
        for key, value in agent.stats(part.worktree, part.base).items():
            total[key] += value
    return total


def _cleanup(run: Run) -> None:
    """Blocking. Remove every worktree and branch the run made, in every repository it touched — and any
    proposal kept beside the worktree while a person was asked about it."""
    for part in _parts(run):
        agent.cleanup(part.repo, part.worktree, part.branch)
    for kept in Path(run.worktree).parent.glob(f"{run.ref}.step-*.proposal.json"):
        kept.unlink(missing_ok=True)


def _by_agent(plan_steps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The plan's work, grouped by the agent that owns it. A gate belongs to no agent: you are the gate."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for step in plan_steps:
        label = (step.get("label") or "").lower()
        if any(word in label for word in GATE_WORDS) or step.get("agent") in roster.NOT_WRITERS:
            continue
        groups.setdefault(step.get("agent") or roster.UNNAMED, []).append(step)
    return groups


class RunService:
    """Making runs, and everything a person can ask of one from a screen."""

    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.runs = RunRepository(session)
        self.logs = RunLogRepository(session)
        self.approvals = ApprovalRepository(session)
        self.activity = ActivityRepository(session)
        self.projects = ProjectRepository(session)

    # ── making them ──────────────────────────────────────────────
    async def plan_runs(self, plan: Any, task: Any, project: Project, by: str,
                        brief: str | None = None, *, goal_budget: int | None = None,
                        attempt: int = 1) -> list[Run]:
        """One run when one agent owns the work; otherwise an agent per worktree, plus the run that
        merges them. The run that leads — the one to start — is last.

        `brief` replaces the plan's requirement as what every agent is told, when the same work is done
        again with a reviewer's notes; the plan's steps stay exactly as they were agreed. `goal_budget`
        makes it a goal run: the run that leads ends with a completion check, and may try again on its
        own up to that many attempts in all; `attempt` says which try this one is."""
        sources = await roots(self.session, project)
        setups = await asyncio.to_thread(_setup_parts, project, sources, list(plan.affected_files or []))
        opened = {x["label"] for x in setups}
        elsewhere = [x.label for x in sources if not x.primary and writable(x) and x.label not in opened]
        references = [{"label": x.label, "root": str(x.root)} for x in sources if not x.primary and not writable(x)]
        steps = [{"label": s.label, "agent": s.agent, "detail": s.detail} for s in plan.steps]
        groups = _by_agent(steps)
        lead = setups[0]
        # The first source's tests stay the run's own test step. A further source's run as checks named
        # after it — and so do the tests of a further source that leads the run — each behind its own
        # first-time approval.
        tests = None if lead["label"] else lead["tests"]
        checks = [*([] if lead["label"] else lead["checks"]), *_extra_checks(setups)]
        goal = goal_budget is not None
        setup = {"setups": setups, "elsewhere": elsewhere, "references": references}

        # A custom agent that prefers a lane works on it; the rest are spread across the open lanes as before.
        # The run keeps the lane, so its review is asked of another one.
        preferred = await self._preferred_lanes(project, list(groups))
        if len(groups) <= 1:
            work = [s for items in groups.values() for s in items]
            owner = next(iter(groups), "")
            solo = await self._new_run(plan, task, project, by, setup, role="solo",
                                       lane=preferred.get(owner) or self.gateway.spread(1, WRITE)[0], brief=brief,
                                       goal_budget=goal_budget, attempt=attempt)
            await self._add_steps(solo, [*self._edit_steps(work), *self._tail(len(work), tests, checks, goal)])
            return [solo]

        lanes = [preferred.get(name) or lane
                 for name, lane in zip(groups, self.gateway.spread(len(groups), WRITE), strict=True)]
        children: list[Run] = []
        for (name, items), lane in zip(groups.items(), lanes, strict=True):
            child = await self._new_run(plan, task, project, by, setup, role="agent", agent=name,
                                        lane=lane, suffix=_slug(name), brief=brief, attempt=attempt)
            await self._add_steps(child, self._edit_steps(items))
            children.append(child)

        integration = await self._new_run(plan, task, project, by, setup, role="integration", brief=brief,
                                          goal_budget=goal_budget, attempt=attempt)
        merges = [{"n": i + 1, "kind": "merge", "label": f"Merge what {c.agent} wrote", "agent": roster.ORCHESTRATOR,
                   "detail": c.branch, "child_run_id": c.id} for i, c in enumerate(children)]
        await self._add_steps(integration, [*merges, *self._tail(len(merges), tests, checks, goal)])
        for child in children:
            child.parent_id = integration.id
        await self.session.flush()
        return [*children, integration]

    async def _preferred_lanes(self, project: Project, owners: list[str]) -> dict[str, str]:
        """owner name → the lane a custom agent of that name prefers, for the owners that are custom agents."""
        agents = CustomAgentService(self.session)
        out: dict[str, str] = {}
        for name in owners:
            spec = await agents.by_name(project, name)
            if spec is not None and spec.lane:
                out[name] = spec.lane
        return out

    def _edit_steps(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"n": i + 1, "kind": "edit", "label": s["label"], "agent": s["agent"],
                 "detail": s.get("detail", "")} for i, s in enumerate(items)]

    def _tail(self, done: int, tests: dict[str, Any] | None, checks: list[dict[str, Any]] = (),
              goal: bool = False) -> list[dict[str, Any]]:
        """What every run ends with: the project's tests and its own checks, a read of the real diff,
        the completion check when a goal was set, and your signature.

        A check is a test step to the database — the step kinds are a closed set, and running one of
        the project's own commands behind the same gate is exactly what a test step is. Which step is
        which check is written on the run (`review.checks`), and the completion check the same way
        (`review.goal`), so nothing has to be read back out of a label."""
        out: list[dict[str, Any]] = []
        if tests:
            out.append({"n": done + 1, "kind": "test", "agent": roster.TESTER,
                        "label": f"Run the project's tests · {tests['command']}"})
        for check in checks:
            out.append({"n": done + len(out) + 1, "kind": "test", "agent": roster.TESTER,
                        "label": f"Run the project's {check['name']} · {check['command']}",
                        "check": {"name": check["name"], "command": check["command"],
                                  **({"label": check["label"]} if check.get("label") else {}),
                                  **({"tests": True} if check.get("tests") else {})}})
        out.append({"n": done + len(out) + 1, "kind": "review", "label": "Review the diff", "agent": roster.REVIEWER})
        if goal:
            out.append({"n": done + len(out) + 1, "kind": "review", "agent": GOAL_CHECK,
                        "label": "Check the goal against the plan's acceptance criteria", "goal": True})
        out.append({"n": done + len(out) + 1, "kind": "handoff", "label": "Your approval", "agent": roster.YOU})
        return out

    async def check_run(self, project: Project, by: str, *, at_branch: str | None = None) -> Run:
        """A run with no agent in it: the project's own tests, in a throwaway worktree.

        It branches from HEAD, or from `at_branch` while that branch still exists — so a failure an
        agent's branch produced is re-run on that branch's code. The first time in a project it stops
        at the same approval every run's test step does; it never goes around it. When it ends, however
        it ends, its worktree and branch are removed.
        """
        setup = await asyncio.to_thread(_setup, project)
        tests = setup["tests"]
        if not tests:
            raise Refused(f"No test command was found in {project.name}. NeuroCode runs a project's own "
                          "tests: a Makefile with a test target, pytest, npm test, go test or dotnet test.")
        answer = await self.session.get(Setting, f"runtime.tests.{project.id}")
        if answer is not None and answer.value == "refused":
            raise Refused(f"You chose not to run tests in {project.name}, so nothing was started.")
        busy = await self.runs.active_check(project.id)
        if busy is not None:
            raise Refused(f"{busy.ref} is already running {project.name}'s tests.")

        base = setup["base"]
        if at_branch:
            base = await asyncio.to_thread(branch_tip, setup["repo"], at_branch) or base
        ref = await self.runs.next_ref()
        branch = await asyncio.to_thread(agent_free_branch, setup["repo"], f"neurocode/check-{ref.lower()}")
        command = tests["command"]
        run = await self.runs.add(Run(
            id=f"r{ref.split('-')[-1]}-{int(time.time())}", ref=ref, project_id=project.id, status="queued",
            role="check", branch=branch, worktree=str(worktrees_dir() / project.id / ref), repo=str(setup["repo"]),
            prefix=setup["prefix"], base=base, requirement=f"Run {command} at {base[:7]}", requested_by=by,
            targets=[], tests_command=command, tests_status="not run",
            review={"findings": [], "verdict": "", "by": ""}))
        await self._add_steps(run, [{"n": 1, "kind": "test", "agent": roster.TESTER,
                                     "label": f"Run the project's tests · {command}"}])
        # Made in this session, so its steps were never loaded: load them before anyone reads them.
        await self.session.refresh(run, attribute_names=["steps", "conflicts"])
        return run

    async def _new_run(self, plan: Any, task: Any, project: Project, by: str, layout: dict[str, Any], *,
                       role: str, agent: str | None = None, lane: str | None = None,
                       suffix: str = "", brief: str | None = None, goal_budget: int | None = None,
                       attempt: int = 1) -> Run:
        setups: list[dict[str, Any]] = layout["setups"]
        setup = setups[0]
        ref = await self.runs.next_ref()
        stem = f"neurocode/{(task.ref if task else plan.ref).lower()}"
        wanted = f"{stem}-{suffix}" if suffix else stem
        branch = await asyncio.to_thread(agent_free_branch, setup["repo"], wanted)
        tree = worktrees_dir() / project.id / ref
        review: dict[str, Any] = {"findings": [], "verdict": "", "by": ""}
        if len(setups) > 1 or setup["label"]:
            # The parts are written on the run before it starts, so every step — and a restart after a
            # crash — reads the same worktrees and branches.
            parts = [Part(setup["label"], Path(setup["repo"]), setup["prefix"], setup["base"], branch, tree)]
            for extra in setups[1:]:
                parts.append(Part(extra["label"], Path(extra["repo"]), extra["prefix"], extra["base"],
                                  await asyncio.to_thread(agent_free_branch, extra["repo"], wanted),
                                  worktrees_dir() / project.id / f"{ref}+{extra['label']}"))
            review["sources"] = [x.as_json() for x in parts]
        if layout["elsewhere"]:
            # A path under a source the run did not open is refused — never written as a folder of the
            # first source that happens to share its name.
            review["elsewhere"] = list(layout["elsewhere"])
        if layout.get("references"):
            # Read for grounding, from their own checkouts; never a worktree, never a write.
            review["references"] = list(layout["references"])
        tests = {} if setup["label"] else setup["tests"] or {}
        return await self.runs.add(Run(
            id=f"r{ref.split('-')[-1]}-{int(time.time())}", ref=ref, project_id=project.id,
            task_id=getattr(task, "id", None), plan_id=plan.id, status="queued", role=role,
            agent=agent, lane=lane, branch=branch,
            worktree=str(tree), repo=str(setup["repo"]), prefix=setup["prefix"],
            base=setup["base"], requirement=brief or plan.raw_requirement, requested_by=by,
            targets=list(plan.affected_files or [])[:12],
            tests_command=tests.get("command", "") or "", tests_status="not run",
            review=review, attempt=attempt, goal_budget=goal_budget))

    async def _add_steps(self, run: Run, steps: list[dict[str, Any]]) -> None:
        checks: list[dict[str, Any]] = []
        goal: dict[str, Any] | None = None
        for step in steps:
            self.session.add(RunStep(run_id=run.id, n=step["n"], kind=step["kind"], label=step["label"],
                                     agent=step.get("agent", ""), detail=step.get("detail", ""),
                                     child_run_id=step.get("child_run_id")))
            if step.get("check"):
                checks.append({"step": step["n"], **step["check"], "status": "not run", "exit": None,
                               "summary": "", "output": []})
            if step.get("goal"):
                goal = {"step": step["n"], "verdict": "not run", "why": "", "criteria": [], "by": "", "at": None}
        if checks or goal:
            run.review = {**(run.review or {}), **({"checks": checks} if checks else {}),
                          **({"goal": goal} if goal else {})}
        await self.session.flush()

    # ── what a person can ask of a run ───────────────────────────
    async def cancel(self, ref: str, by: str) -> Run:
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        if run.status in ("done", "failed", "cancelled"):
            raise Refused(f"{ref} already {run.status}")
        children = (await self.runs.children_of([run.id])).get(run.id, [])
        for target in [run, *children]:                     # stopping a merge run stops its agents too
            stopped(target.ref).set()
            if target.status in ("queued", "waiting"):
                target.status, target.finished_at = "cancelled", utcnow()
                target.note = target.note or "Stopped by you."
                if target.role == "check":
                    await self._end_check(target)
                elif target.waiting_on:
                    # The gate it stopped at goes with it, or the inbox keeps asking about a run that ended.
                    gate = await self.approvals.waiting_on_person(target.ref)
                    if gate is not None:
                        gate.status, gate.decided_at = "denied", utcnow()
                    target.waiting_on = None
        await self.session.flush()
        # A check run leaves nothing behind — its worktree and branch go with it — so saying that the
        # worktree stays would send someone looking for a folder that is already gone.
        left = ("its worktree and branch were removed" if run.role == "check"
                else "the worktree stays for you to look at")
        await self.activity.record(actor=by, actor_kind="human", action="Run stopped",
                                   detail=f"{ref} · {run.branch} — {left}",
                                   level="warn", project_id=run.project_id)
        return run

    async def _end_check(self, run: Run) -> None:
        """A test-only run that is stopped before it works leaves nothing behind.

        A running one is cleaned up by `_finish` when its loop notices the stop; a queued or waiting
        one never reaches `_finish`, so its worktree and branch would stay — and the approval it was
        parked on would sit in the inbox asking to run a command for a run that no longer exists.
        """
        await asyncio.to_thread(agent.cleanup, Path(run.repo), Path(run.worktree), run.branch)
        run.removed = True
        gate = await self.approvals.waiting_on_person(run.ref)
        if gate is not None:
            gate.status, gate.decided_at = "denied", utcnow()
        run.waiting_on = None

    async def discard(self, ref: str, by: str) -> Run:
        """Remove the worktree and the branch. Only once the run has stopped."""
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        if run.status in ("queued", "running"):
            raise Refused(f"{ref} is still working. Stop it first.")
        if run.status == "waiting":
            raise Refused(f"{ref} is waiting for your decision. Answer it, or stop the run first.")
        if run.removed:
            return run
        children = (await self.runs.children_of([run.id])).get(run.id, [])
        for target in [run, *children]:                     # a merge run takes its agents' worktrees with it
            if not target.removed:
                await asyncio.to_thread(_cleanup, target)
                target.removed = True
        await self.session.flush()
        await self.activity.record(actor=by, actor_kind="human", action="Worktree discarded",
                                   detail=f"{ref} · {run.branch} removed", level="warn",
                                   project_id=run.project_id)
        return run

    async def rework(self, ref: str, notes: str, *, by: str, by_id: str, may_decide: bool,
                     actor_kind: str = "human") -> list[Run]:
        """Send a run back: the same plan, done again as a new run whose brief carries your notes and what
        the review found. The old run's worktree and branch are removed the way a discard removes them,
        and a signature it was waiting for is refused, because this is the answer to it.

        Returns the new runs, the one that leads last, exactly as dispatching does. The new ones are
        made before anything of the old one is removed, so a project that can no longer be worked in
        refuses with the old run intact.
        """
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        notes = notes.strip()
        if not notes:
            raise Refused("Say what should change, so the next run knows.", status=422)
        if run.role == "check":
            raise Refused(f"{ref} only ran the project's tests, so there is no work to send back. "
                          "Run them again from Testing.")
        if run.parent_id:
            parent = await self.runs.get(run.parent_id)
            raise Refused(f"{ref} is one agent's part of {parent.ref if parent else 'a larger run'}. "
                          "Send back the run that merges them instead.")
        if run.status in ("queued", "running"):
            raise Refused(f"{ref} is still working. Stop it first.")
        if run.merged:
            raise Refused(f"{ref} is already merged into {run.merged['into']}. Compile the change you want "
                          "as a new requirement instead.")
        again = (run.review or {}).get("reworkedAs")
        if again:
            raise Refused(f"{ref} was already sent back — {again} is doing it again.")
        if run.plan_id is None:
            raise Refused(f"{ref} was not started from a plan, so there is nothing to do again.")

        gate = None
        if run.status == "waiting":
            gate = await self.approvals.waiting_on_person(ref)
            gated = next((x for x in run.steps if gate is not None and x.n == gate.step), None)
            if gate is None or gated is None or gated.kind != "handoff":
                raise Refused(_waiting(ref, gate, gated, "about running its tests"))
            if not may_decide:
                raise Denied("approvals:decide", f"refuse the signature {ref} is waiting for")

        plan = await PlanRepository(self.session).get(run.plan_id)
        project = await self.projects.get(run.project_id)
        if plan is None or project is None:
            raise Refused(f"The plan behind {ref} is gone, so there is nothing to do again.")
        task = await TaskRepository(self.session).get(plan.task_id) if plan.task_id else None

        # A goal run's next try keeps its budget and counts on from this one, whoever sent it back.
        made = await self.plan_runs(plan, task, project, by, brief=_rework_brief(plan.raw_requirement, run, notes, by),
                                    goal_budget=run.goal_budget, attempt=run.attempt + 1)
        lead = made[-1]
        for new in made:
            # The first line of the new run's log, so reading it says what it is doing again, and why.
            await self.logs.write(new.id, level="info", line=f"rework of {ref}, sent back by {by}: {notes[:300]}")

        now = utcnow()
        if gate is not None:
            gate.status, gate.decided_at, gate.decided_by = "denied", now, by_id
            step = next(x for x in run.steps if x.n == gate.step)
            step.status, step.detail = "failed", f"Sent back for changes as {lead.ref}."
        if run.status == "waiting":
            run.status, run.finished_at, run.waiting_on = "cancelled", now, None
        run.note = f"Sent back for changes as {lead.ref}."
        run.review = {**(run.review or {"findings": [], "verdict": "", "by": ""}), "reworkedAs": lead.ref}
        await self.logs.write(run.id, level="warn", line=f"sent back for changes by {by} · {lead.ref} does it again")

        children = (await self.runs.children_of([run.id])).get(run.id, [])
        for target in [run, *children]:
            if not target.removed:
                await asyncio.to_thread(_cleanup, target)
                target.removed = True
        if task is not None and task.status in ("review", "blocked"):
            task.status = "in_progress"
        await self.session.flush()

        if actor_kind == "human":
            await _signal(self.session, lambda: TasteService(self.session).on_rework(
                run, notes, by=by, by_user_id=by_id or None, again=lead.ref))
        settled = f" · {gate.ref} refused" if gate is not None else ""
        await self.activity.record(actor=by, actor_kind=actor_kind, action="Sent back for changes",
                                   detail=f"{ref} → {lead.ref}{settled} · {run.branch} removed · {notes[:120]}",
                                   level="warn", project_id=run.project_id, task_ref=task.ref if task else None)
        for new in made:
            # Made in this session, so their steps were never loaded: load them before anyone reads them.
            await self.session.refresh(new, attribute_names=["steps", "conflicts"])
        return made

    async def merge(self, ref: str, by: str) -> dict[str, Any]:
        """Merge an accepted run into whatever branch the repository has checked out."""
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        if run.status != "done":
            raise Refused(f"{ref} has not finished, so there is nothing settled to merge.")
        if run.removed:
            raise Refused(f"{ref}'s branch was removed, so there is nothing to merge.")
        if run.merged:
            raise Refused(f"{ref} is already merged into {run.merged['into']}.")
        # The checkout is asked first, as the Git screen asks it, so the reason a person sees there is
        # the one the merge gives; then the branch, then whether it is still what was reviewed.
        parts = _parts(run)
        for part in parts:
            if await asyncio.to_thread(agent.dirty, part.repo):
                raise Refused(agent.DIRTY if len(parts) == 1 else f"{part.name}: {agent.DIRTY}")
        patch, head = await asyncio.to_thread(_patch, parts)
        if not head:
            raise Refused(f"{run.branch} no longer exists, so there is nothing to merge.")
        refusal = receipt_refusal(run, patch, "merge")
        if refusal:
            raise Refused(refusal)

        message = f"Merge {run.ref}: {(run.requirement or run.ref)[:80]}\n\nNeuroCode {run.branch}"
        try:
            result = await asyncio.to_thread(_merge_parts, parts, message)
        except agent.Refused as refused:                    # a dirty tree: the reason is for the person
            raise Refused(str(refused)) from refused
        if result["merged"]:
            run.merged = {"into": result["into"], "commit": result["commit"], "at": utcnow().isoformat(),
                          "by": by, "undo": result["undo"],
                          **({"sources": result["sources"]} if "sources" in result else {})}
            await self.logs.write(run.id, level="ok",
                                  line=f"merged into {result['into']} as {result['commit']} · undo: {result['undo']}")
            await self.activity.record(actor=by, actor_kind="human", action="Merged",
                                       detail=f"{ref} · {run.branch} → {result['into']} as {result['commit']} · "
                                              f"undo: {result['undo']}", level="ok", project_id=run.project_id)
            # The person's own commits on the branch, after the agent's last one, are what they changed.
            await _signal(self.session, lambda: TasteService(self.session).on_merged(run))
        else:
            await self.activity.record(actor=by, actor_kind="human", action="Merge collided",
                                       detail=f"{ref} · {len(result['conflicts'])} files collide with "
                                              f"{result['into']}; nothing was merged", level="warn",
                                       project_id=run.project_id)
        await self.session.flush()
        return result

    async def push(self, ref: str, by: str, remote: str | None = None) -> Run:
        """Push an accepted run's branch — its own branch, nothing else — to the project's remote, with
        the person's own git credentials, never forced. The pull request is theirs to open: the run
        keeps the compare link that opens one under their own account."""
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        if run.role == "check":
            raise Refused(f"{ref} only ran the project's tests, so it has no branch to push.")
        if run.parent_id:
            parent = await self.runs.get(run.parent_id)
            raise Refused(f"{ref} is one agent's part of {parent.ref if parent else 'a larger run'}. "
                          "Push the run that merges them, once you have accepted it.")
        gate = next((x for x in run.steps if x.kind == "handoff"), None)
        if run.status != "done" or gate is None or gate.status != "done":
            raise Refused(f"{ref} has not been accepted, so there is nothing settled to push. "
                          "Approve its signature first.")
        if run.removed:
            raise Refused(f"{ref}'s branch was removed, so there is nothing to push.")
        parts = _parts(run)
        patch, head = await asyncio.to_thread(_patch, parts)
        if not head:
            raise Refused(f"{run.branch} no longer exists, so there is nothing to push.")
        if not patch.strip():
            raise Refused(f"Nothing to push: {run.branch} changes nothing.")
        refusal = receipt_refusal(run, patch, "push")
        if refusal:
            raise Refused(refusal)
        try:
            pushed = await asyncio.to_thread(_push_parts, parts, remote)
        except agent.Refused as refused:                    # no remote, bad credentials, a moved branch
            await self.logs.write(run.id, level="err", line=f"push refused · {refused}"[:300])
            raise Refused(str(refused)) from refused

        run.pushed = {**pushed, "at": utcnow().isoformat(), "by": by}
        link = f" · open a pull request: {pushed['compareUrl']}" if pushed["compareUrl"] else ""
        await self.logs.write(run.id, level="ok",
                              line=f"pushed {run.branch} to {pushed['remote']} at {pushed['sha'][:7]}{link}")
        await self.activity.record(actor=by, actor_kind="human", action="Branch pushed",
                                   detail=f"{ref} · {run.branch} → {pushed['remote']} at {pushed['sha'][:7]}",
                                   level="ok", project_id=run.project_id)
        await self.session.flush()
        return run

    async def review_again(self, ref: str, by: str) -> tuple[Run, int]:
        """Mark a run's review to be read again on the branch as it is now. The reading happens in the
        background (`reread`); this says whether it may, and returns the run and the review step.

        This is how a stale receipt is renewed: when the branch moved after its review, merge and push
        refuse, and the new review's receipt is the one they check against."""
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        if run.role == "check":
            raise Refused(f"{ref} only ran the project's tests, so there is no diff to review.")
        if run.parent_id:
            raise Refused(f"{ref} is one agent's part of a larger run; its review is the merged one.")
        step = _review_step(run)
        if step is None:
            raise Refused(f"{ref} has no review step to run again.")
        if run.status in ("queued", "running"):
            raise Refused(f"{ref} is still working. Its review runs when it gets there.")
        if run.status == "waiting":
            gate = await self.approvals.waiting_on_person(ref)
            gated = next((x for x in run.steps if gate is not None and x.n == gate.step), None)
            if gated is None or gated.kind != "handoff":
                raise Refused(_waiting(ref, gate, gated, "about running a command"))
        elif run.status != "done":
            raise Refused(f"{ref} {run.status}, so there is nothing to sign. Send it back for changes instead.")
        if run.removed:
            raise Refused(f"{ref}'s branch was removed, so there is nothing to review.")
        if run.merged:
            raise Refused(f"{ref} is already merged into {run.merged['into']}, as it was reviewed.")
        reading = (run.review or {}).get("reviewing")
        if reading and time.time() - float(reading.get("since", 0)) < REVIEWING_FOR:
            raise Refused(f"{reading.get('by') or 'Someone'} already asked for it to be read again; "
                          "it is being read now.")
        run.review = {**(run.review or {}), "reviewing": {"by": by, "since": time.time()}}
        step.status, step.detail = "running", f"Reading the branch again, asked by {by}."
        await self.logs.write(run.id, level="info", step=step.n, line=f"review asked for again by {by}")
        await self.activity.record(actor=by, actor_kind="human", action="Review asked for again",
                                   detail=f"{ref} · {run.branch}", project_id=run.project_id)
        await self.session.flush()
        return run, step.n

    async def revert(self, ref: str, n: int, by: str, *, redo: bool = False) -> Run:
        """Take a run's worktree back to how it stood after step `n` — never the checkout, only the run's
        own worktree on its own branch — and mark every later step undone.

        Each edit step keeps the commit it left in each source it wrote to (`review.commits`), so "after
        step n" is exact: for every part, the last commit a step up to `n` made, or the commit the run
        branched from. With `redo`, the later steps are set to run again and the run is ready to start
        from step n + 1 (the caller starts it); otherwise the run ends here, cancelled, with its branch —
        a signature it was waiting for is closed, because what it asked about is gone."""
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        if run.role == "check":
            raise Refused(f"{ref} only ran the project's tests; there is no step to go back to.")
        if run.parent_id:
            parent = await self.runs.get(run.parent_id)
            raise Refused(f"{ref} is one agent's part of {parent.ref if parent else 'a larger run'}; its branch "
                          "was merged there. Send that run back for changes instead.")
        if run.role == "integration":
            raise Refused(f"{ref} merges several agents' branches; it is not taken back step by step. "
                          "Send it back for changes instead.")
        if run.status in ("queued", "running"):
            raise Refused(f"{ref} is still working. Stop it first, then revert it.")
        if run.removed:
            raise Refused(f"{ref}'s worktree was removed, so there is nothing to revert.")
        if run.merged:
            raise Refused(f"{ref} is already merged into {run.merged['into']}. Undo that merge with "
                          f"{run.merged.get('undo') or 'git'} first; a run's worktree is not the place to take "
                          "it back.")
        target = next((x for x in run.steps if x.n == n), None)
        if target is None:
            raise NotFound(f"step {n} of {ref}")
        later = [x for x in run.steps if x.n > n]
        if not later:
            raise Refused(f"Step {n} is {ref}'s last step; there is nothing after it to take back.", status=422)
        commits: dict[str, dict[str, str]] = (run.review or {}).get("commits") or {}
        old = [x.n for x in run.steps
               if x.kind == "edit" and x.n <= n and x.status == "done" and str(x.n) not in commits]
        if old:
            raise Refused(f"Step {old[0]} of {ref} finished before steps kept their commits, so there is no commit "
                          "to go back to. Send it back for changes instead.")
        parts = _parts(run)
        wanted: dict[str, str] = {}
        for part in parts:
            sha = part.base
            for x in run.steps:
                if x.kind == "edit" and x.n <= n and part.label in (commits.get(str(x.n)) or {}):
                    sha = commits[str(x.n)][part.label]
            wanted[part.label] = sha

        def reset() -> dict[str, str]:
            before = {part.label: agent.head(part.worktree) for part in parts}
            if not redo and all(before[p.label] == wanted[p.label] for p in parts):
                raise agent.Refused(f"Nothing to take back: no step after step {n} changed {ref}'s worktree.")
            for part in parts:
                if before[part.label] != wanted[part.label]:
                    agent.reset_worktree(part.worktree, part.branch, wanted[part.label])
            return before

        try:
            before = await asyncio.to_thread(reset)
        except agent.Refused as refused:
            raise Refused(str(refused)) from refused
        stat = await asyncio.to_thread(_stats, parts)

        now = utcnow()
        gate = await self.approvals.waiting_on_person(ref) if run.status == "waiting" else None
        if gate is not None:
            gate.status, gate.decided_at = "denied", now
        undone = [x.n for x in later]
        review = {k: v for k, v in (run.review or {}).items() if k != "reviewing"}
        for key in ("commits", "grounding", "asks"):
            if key in review:
                review[key] = {k: v for k, v in review[key].items() if int(k) <= n}
        review["reverts"] = [*(review.get("reverts") or []),
                             {"to": n, "by": by, "at": now.isoformat(), "steps": undone, "redo": redo,
                              "from": before, "sha": wanted}]
        for x in later:
            if redo:
                x.status, x.detail, x.ms = "todo", "", None
                x.commit_sha, x.question, x.answer = "", "", ""
            else:
                x.status, x.detail = "skipped", f"Taken back: {ref} was reverted to step {n} by {by}."
        if redo:
            # What the later steps found described code that is gone, so it goes with them.
            review.update({"findings": [], "verdict": "", "by": ""})
            review.pop("receipt", None)
            review.pop("instructions", None)
            review["checks"] = [{**c, "status": "not run", "exit": None, "summary": "", "output": []}
                                if c.get("step", 0) > n else c for c in review.get("checks") or []]
            if review.get("goal") and review["goal"].get("step", 0) > n:
                review["goal"] = {"step": review["goal"]["step"], "verdict": "not run", "why": "", "criteria": [],
                                  "by": "", "at": None}
            if any(x.kind == "test" and not _check_at(run, x.n) for x in later):
                run.tests_status, run.tests_summary = "not run", ""
            run.status, run.finished_at, run.waiting_on = "queued", None, None
            run.note = f"Reverted to step {n} by {by}; steps {undone[0]}–{undone[-1]} run again."
            stopped(ref).clear()
        else:
            run.status, run.finished_at, run.waiting_on = "cancelled", now, None
            run.note = (f"Reverted to step {n} by {by}: steps {undone[0]}–{undone[-1]} were taken back. The branch "
                        "stands as it did then — revert again and redo the later steps, send it back for changes, "
                        "or discard it.")
        run.review = review
        run.diff_files, run.diff_insertions = stat["files"], stat["insertions"]
        run.diff_deletions, run.diff_commits = stat["deletions"], stat["commits"]
        where = ", ".join(f"{(p.label or 'worktree')} at {wanted[p.label][:7]}" for p in parts)
        await self.logs.write(run.id, level="warn", step=n,
                              line=f"reverted to step {n} by {by} · {where}"
                                   + (" · the later steps run again" if redo else ""))
        await self.activity.record(actor=by, actor_kind="human", action="Run reverted",
                                   detail=f"{ref} · to step {n} ({target.label[:60]}) · {len(undone)} later "
                                          f"step{'s' if len(undone) != 1 else ''} {'to redo' if redo else 'taken back'}"
                                          + (f" · {gate.ref} closed" if gate is not None else ""),
                                   level="warn", project_id=run.project_id)
        await self.session.flush()
        return run

    async def diff(self, ref: str) -> dict[str, Any]:
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        stat = {"files": run.diff_files, "insertions": run.diff_insertions,
                "deletions": run.diff_deletions, "commits": run.diff_commits}
        tree = Path(run.worktree)
        if run.removed or not await asyncio.to_thread(tree.exists):
            return {"patch": "", "truncated": False, "stat": stat, "gone": True}
        parts = _parts(run)

        def read() -> str:
            if len(parts) == 1 and not parts[0].label:
                return agent.diff(tree, run.base)
            return "".join(agent.label_patch(agent.diff(x.worktree, x.base), x.label, x.prefix)
                           for x in parts if x.worktree.exists())

        patch = await asyncio.to_thread(read)
        return {"patch": patch[:MAX_DIFF], "truncated": len(patch) > MAX_DIFF, "stat": stat, "gone": False}


def _waiting(ref: str, gate: Approval | None, gated: RunStep | None, tests: str) -> str:
    """Why a run cannot be sent back or read again yet: it is stopped at a gate that is not its signature."""
    if gate is None or gated is None or gated.kind == "test":
        return f"{ref} is waiting for your answer {tests}. Answer that first."
    return f"{ref} is waiting for your answer at step {gated.n} ({gate.title}). Answer that first."


def _review_step(run: Run) -> RunStep | None:
    """The step that reads the diff — not the completion check, which is a review step too."""
    goal = (run.review or {}).get("goal") or {}
    return next((x for x in run.steps if x.kind == "review" and x.n != goal.get("step")), None)


def _check_at(run: Run, n: int) -> dict[str, Any] | None:
    return next((c for c in (run.review or {}).get("checks") or [] if c.get("step") == n), None)


def _put_check(run: Run, n: int, **fields: Any) -> None:
    """A JSONB value is replaced, not mutated in place, or the change is never written."""
    review = run.review or {}
    run.review = {**review, "checks": [{**c, **fields} if c.get("step") == n else c
                                       for c in review.get("checks") or []]}


def _short(fp: str) -> str:
    return fp.removeprefix("sha256:")[:12]


def receipt_refusal(run: Run, patch: str, verb: str) -> str | None:
    """Why a merge or a push of this branch would land something nobody reviewed — or None.

    The review keeps a receipt: the fingerprint of the exact patch the reviewer was handed. A branch
    whose patch is no longer that one — a commit added in the worktree, a rebase — is refused, so what
    a person signed is exactly what lands. Reviewing again renews the receipt. A branch that changes
    nothing needs no receipt: there is nothing to land."""
    receipt = (run.review or {}).get("receipt")
    if not receipt:
        if not patch.strip():
            return None
        if run.parent_id:
            return (f"{run.ref} is one agent's part of a larger run and was never reviewed on its own. "
                    f"{verb.capitalize()} the run that merges them.")
        return f"{run.ref} has no review of its current diff, so there is nothing signed to {verb}. Review it again."
    now = agent.fingerprint(patch)
    if receipt.get("sha256") != now:
        return (f"{run.branch} changed since it was reviewed: the review read {_short(receipt.get('sha256', ''))}, "
                f"the branch is now {_short(now)}. Review it again, then {verb}.")
    return None


def _rework_brief(requirement: str, run: Run, notes: str, by: str) -> str:
    """What a run sent back is told: the requirement, what the person asked to change, and what the
    review of the last attempt found — so the next attempt starts from both, not from scratch."""
    review = run.review or {}
    findings = [f"- {f.get('severity', 'LOW')}{' ' + f['file'] if f.get('file') else ''}: {f.get('note', '')}"
                for f in review.get("findings", [])]
    lines = [requirement, "", f"This is being done again. {by} sent back {run.ref} and asked for these changes:",
             notes]
    if findings or review.get("verdict"):
        lines += ["", f"What the review of {run.ref} found" + (f" ({review['by']})" if review.get("by") else "") + ":",
                  *findings]
        if review.get("verdict"):
            lines.append(f"Verdict: {review['verdict']}")
    return "\n".join(lines)


def _merge_parts(parts: list[Part], message: str) -> dict[str, Any]:
    """Blocking. Merge every part into what its repository has checked out — all of them or none. One
    part answers exactly as `merge_into_checkout` does. With several, a collision in one takes back the
    merges already made in the others (only while each checkout still stands on its merge), so a
    project is never left half-merged across its sources; each source's undo command is kept."""
    if len(parts) == 1 and not parts[0].label:
        return agent.merge_into_checkout(parts[0].repo, parts[0].branch, message)
    done: list[tuple[Part, dict[str, Any]]] = []
    for part in parts:
        result = agent.merge_into_checkout(part.repo, part.branch, message)
        if not result["merged"]:
            undone = [x.name for x, merged in done if agent.undo_merge(x.repo, merged["before"], merged["commit"])]
            kept = [f"{x.name}: {merged['undo']}" for x, merged in done if x.name not in undone]
            return {"merged": False, "into": result["into"], "commit": None, "undo": None,
                    "conflicts": [f"{part.lead}{f}" for f in result["conflicts"]],
                    "sources": [{"label": x.label, "merged": False, "undone": x.name in undone} for x, _ in done]
                    + [{"label": part.label, "merged": False, "conflicts": result["conflicts"]}],
                    "kept": kept}
        done.append((part, result))
    first = done[0][1]
    return {"merged": True, "into": first["into"], "conflicts": [], "commit": first["commit"], "undo": first["undo"],
            "sources": [{"label": x.label, "into": r["into"], "commit": r["commit"], "undo": r["undo"]}
                        for x, r in done]}


def _push_parts(parts: list[Part], remote: str | None) -> dict[str, Any]:
    """Blocking. Push every part's branch to its own repository's remote. One part answers exactly as
    `push` does; several answer with the first part's push and every source's under `sources`."""
    if len(parts) == 1 and not parts[0].label:
        return agent.push(parts[0].repo, parts[0].branch, remote)
    pushed: list[tuple[Part, dict[str, Any]]] = []
    for part in parts:
        try:
            pushed.append((part, agent.push(part.repo, part.branch, remote)))
        except agent.Refused as refused:
            # A push cannot be taken back from here, so the ones already made are named.
            went = ", ".join(x.name for x, _ in pushed)
            raise agent.Refused(f"{part.name}: {refused}" + (f" ({went} already pushed)" if went else "")) from refused
    return {**pushed[0][1], "sources": [{"label": part.label, **out} for part, out in pushed]}


def agent_free_branch(repo: Path, wanted: str) -> str:
    return agent.free_branch(repo, wanted)


def branch_tip(repo: Path, branch: str) -> str | None:
    """The commit a local branch points at, or None when it is gone. Blocking."""
    out = agent.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"], repo)
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _before_tests(work: Path) -> tuple[str, str, float]:
    """The commit about to be tested, the Go module path if there is one, and the moment the tests
    start — a coverage report older than that was not written by this run. Blocking."""
    sha = agent.git(["rev-parse", "HEAD"], work).stdout.strip()
    module = ""
    gomod = work / "go.mod"
    if gomod.is_file() and not gomod.is_symlink():
        try:
            first = next((ln for ln in gomod.read_text(errors="replace").splitlines()
                          if ln.startswith("module ")), "")
        except OSError as e:
            log.warning("could not read %s: %s", gomod, e)
        else:
            module = first.removeprefix("module ").strip().strip('"')
    return sha, module, time.time()


def _coverage(work: Path, since: float, module: str) -> list[tuple[str, int, int, str]]:
    """Coverage the test command just wrote, summed per top-level directory. Blocking.

    Only a report written after the tests started counts: one committed to the repository, or left by
    an earlier run, describes code that is not this code. A symlink is never followed — the report
    must be a file the command wrote inside the worktree, not a way to read something outside it.
    With nothing fresh on disk, the answer is no rows, not a zero.
    """
    roots = (str(work), os.path.realpath(work))
    inside = f"{os.path.realpath(work)}{os.sep}"
    files: dict[str, tuple[int, int, str]] = {}
    for name, kind in coverage_reports.REPORTS:
        report = work / name
        try:
            # A linked `coverage/` directory would lead there too, so where it really is is checked.
            if report.is_symlink() or not report.is_file() or not os.path.realpath(report).startswith(inside):
                continue
            info = report.stat()
            if info.st_mtime < since or info.st_size > MAX_REPORT:
                continue
            text = report.read_text(errors="replace")
        except OSError as e:
            log.warning("could not read the coverage report %s: %s", report, e)
            continue
        for path, (covered, total) in coverage_reports.parse(kind, text, roots=roots, module=module).items():
            files.setdefault(path, (covered, total, kind))       # the first report to name a file wins
    return coverage_reports.by_directory(files)


# ── working the steps, in the background ─────────────────────────
async def _log(db: Database, run_id: str, level: str, line: str, step: int | None = None) -> None:
    async with db.session() as s:
        await RunLogRepository(s).write(run_id, level=level, line=line, step=step)


async def _finish(db: Database, ref: str, status: str, note: str) -> None:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        leftover = (Path(run.repo), Path(run.worktree), run.branch) \
            if run is not None and run.role == "check" and not run.removed else None
    # A test-only run exists to produce a result, not a branch: whatever it ended as, nothing stays.
    if leftover is not None:
        await asyncio.to_thread(agent.cleanup, *leftover)
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None:
            return
        if leftover is not None:
            run.removed = True
        run.status, run.finished_at, run.waiting_on = status, utcnow(), None
        if note:
            run.note = note
        level = "ok" if status == "done" else "warn" if status == "cancelled" else "err"
        await RunLogRepository(s).write(run.id, level=level, line=f"run {status}")
        await ActivityRepository(s).record(
            actor=roster.ORCHESTRATOR, actor_kind="agent", action=f"Run {status}",
            detail=f"{ref} · {run.diff_files} files +{run.diff_insertions} −{run.diff_deletions} on "
                   f"{run.branch}" + (f" · {note}" if note else ""),
            level=level, project_id=run.project_id)
    _STOPPED.pop(ref, None)


@dataclass(slots=True)
class Handed:
    """One file a step is handed: its project path, its text, whether it is only to be read (a reference
    source's), and why it was chosen — the plan named it, retrieval found it, or the index matched it."""

    path: str
    text: str
    readonly: bool
    via: str


async def _context(session: AsyncSession, run: Run, step: RunStep,
                   retrieved: list[str] = ()) -> tuple[list[Handed], list[tuple[str, str]]]:
    """The files this step may change: what the plan named, then the code retrieval found for the step,
    then what the index finds for its words — each read from the worktree of the source its label names,
    or, for a reference source, from its own checkout and marked read only. Returns the files and the
    lines the caller writes to the run's log (a read session writes nothing)."""
    wanted: list[tuple[str, str]] = [(x, "plan") for x in run.targets or []]
    wanted += [(x, "retrieval") for x in retrieved]
    words = func.plainto_tsquery("simple", step.label)
    rows = (await session.execute(
        select(CodeFile.path).join(CodeSymbol, CodeSymbol.file_id == CodeFile.id)
        .where(CodeSymbol.project_id == run.project_id, CodeSymbol.search.op("@@")(words))
        .distinct().limit(6))).scalars()
    wanted += [(x, "index") for x in rows]

    refused: list[str] = []
    closed: list[str] = []
    parts, elsewhere, references = _parts(run), _elsewhere(run), _references(run)

    def read() -> list[Handed]:
        files: list[Handed] = []
        seen: set[str] = set()
        total = 0
        for rel, via in wanted:
            if rel in seen or len(files) >= STEP_FILES:
                continue
            seen.add(rel)
            # These paths come from the compiler and from retrieval — a model wrote the first, an index the
            # second — so they are checked exactly the way a write is. Reading is not the harmless half:
            # whatever is read here is sent to a provider, so "../../../.ssh/id_rsa" would be an
            # exfiltration, not a bad diff.
            try:
                agent.safe_path(rel)
            except agent.Refused:
                refused.append(rel)
                continue
            head, _, rest = rel.strip().replace("\\", "/").partition("/")
            readonly = head in references
            if readonly:
                # A reference is read where it is — it has no worktree — and only inside its own checkout.
                root = references[head]
                try:
                    f = root / agent.safe_path(rest)
                except agent.Refused:
                    refused.append(rel)
                    continue
                real, home = os.path.realpath(f), os.path.realpath(root)
                if not real.startswith(home + os.sep):
                    refused.append(rel)
                    continue
            else:
                hit = _route(parts, rel, elsewhere)
                if hit is None:
                    closed.append(rel)
                    continue
                try:
                    f = hit[0].work / agent.safe_path(hit[1])
                except agent.Refused:
                    refused.append(rel)
                    continue
            if not f.is_file():
                continue
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            if total + len(text) > MAX_CONTEXT:
                continue
            total += len(text)
            files.append(Handed(rel, text, readonly, via))
        return files

    found = await asyncio.to_thread(read)
    notes: list[tuple[str, str]] = []
    if refused:
        # Said out loud rather than dropped: a plan that names a path outside the worktree is worth
        # seeing in the run's log, whether it was a hallucination or something worse.
        notes.append(("warn", f"ignored {len(refused)} path(s) outside the worktree: {', '.join(refused[:5])}"))
    if closed:
        notes.append(("info", f"not read: {len(closed)} path(s) in sources this run did not open: "
                              f"{', '.join(closed[:5])}"))
    return found, notes


async def _pieces(session: AsyncSession, gateway: Gateway, run: Run, step: RunStep) -> list[dict[str, Any]]:
    """Retrieval's code and document pieces for this step — its words, then the requirement's. A search
    that fails leaves the agent with less to read, never without its step: the step records what it had."""
    query = "\n".join(x for x in (step.label, step.detail, run.requirement) if x)[:600]
    try:
        found = await RetrievalService(session, gateway).search(run.project_id, query, limit=STEP_PIECES_ASKED)
    except Exception as failed:                      # a lane or the index misbehaving must not stop a step
        log.warning("retrieval for %s step %s did not answer: %s", run.ref, step.n, failed)
        return []
    return [x for x in found if x.get("kind") in ("code", "doc")][:STEP_PIECES]


async def _told(session: AsyncSession, project_id: str, targets: list[str]) -> instructions.Resolved:
    """The project's instruction files for these targets — a rule with `paths:` applies only when one of
    them falls under it. None on disk is an empty answer, not a failure."""
    project = await ProjectRepository(session).get(project_id)
    try:
        return await instructions.for_project(project, targets, session=session)
    except Exception as failed:                      # a checkout that went away reads as no instructions
        log.warning("instructions for %s could not be read: %s", project_id, failed)
        return instructions.Resolved()


async def _taste(session: AsyncSession, project_id: str) -> Applied:
    """The taste rules a person adopted for this project (`services.taste`). None adopted, or a read that
    failed, is no block at all — never a reason to stop the step."""
    try:
        return await applied_for(session, project_id)
    except Exception as failed:                      # noqa: BLE001 — taste is advice; the step goes on without it
        log.warning("taste for %s could not be read: %s", project_id, failed)
        return Applied()


def _instructed(system: str, told: instructions.Resolved, taste: Applied | None = None) -> str:
    """The system text with the project's instructions after it, then the taste rules a person adopted —
    the stable part of the prompt first, so a provider's prompt cache can hit across one project's steps."""
    out = system
    if told.text:
        out += (f"\n\nThe project's own instructions, from files in its repository — follow them where they "
                f"bear on the work:\n{told.text}")
    if taste is not None and taste.text:
        out += ("\n\nHow the people on this project like the work done — rules they adopted from their own "
                f"decisions; follow them unless the step says otherwise:\n{taste.text}")
    return out


async def _signal(session: AsyncSession, capture: Any) -> None:
    """A taste signal kept where the moment happens (`services.taste`), in a savepoint of its own: a signal
    that cannot be kept is logged and left for the taste harvest, never a reason to fail the run."""
    try:
        async with session.begin_nested():
            await capture()
    except Exception as failed:                      # noqa: BLE001 — see the docstring
        log.warning("a taste signal was not kept: %s", failed)


def _grounding_doc(told: instructions.Resolved, pieces: list[dict[str, Any]], files: list[Handed],
                   taste: Applied | None = None) -> dict[str, Any]:
    """What a step was handed, as the run keeps it: small enough to travel with the run on every change."""
    return {"taste": list(taste.refs) if taste is not None else [],"instructions": [{"path": f["path"], "bytes": f["bytes"], "sha1": f["sha1"][:12], "scope": f["scope"],
                              "matched": f.get("matched")} for f in told.files if f["applied"]],
            "capped": told.capped,
            "pieces": [{"kind": x["kind"], "ref": x["ref"], "path": x["path"], "line": x.get("line"),
                        "how": x.get("how", ""), **({"project": x["project"]} if x.get("project") else {})}
                       for x in pieces],
            "files": [{"path": h.path, "via": h.via, **({"readonly": True} if h.readonly else {})} for h in files]}


def _put(run: Run, key: str, n: int, value: Any) -> None:
    """One step's entry in a per-step map on the run's review document, replaced whole so it is written."""
    review = run.review or {}
    run.review = {**review, key: {**(review.get(key) or {}), str(n): value}}


# ── what a person allowed beyond the rules ───────────────────────
def granted(grants: list[dict[str, Any]], tool: str, subject: str, n: int) -> dict[str, Any] | None:
    """The grant that covers doing `tool` to `subject` at step `n`: one for this run, or one for this
    step that has not been used yet. None when a person has not allowed it."""
    for grant in grants or []:
        if grant.get("tool") != tool or grant.get("subject") != subject:
            continue
        if grant.get("scope") == "run" or (grant.get("scope") == "once" and grant.get("step") == n
                                           and not grant.get("used")):
            return grant
    return None


def _spend(run: Run, tool: str, subjects: list[str], n: int) -> None:
    """A once-only grant is used up by the write or the command it allowed."""
    run.grants = [{**g, "used": True} if g.get("tool") == tool and g.get("subject") in subjects
                  and g.get("scope") == "once" and g.get("step") == n else g for g in run.grants or []]


@dataclass(slots=True)
class Verdict:
    """What the tool rules said about one thing a run wants to do."""

    subject: str
    action: str          # allow | ask | deny | none (no rule: the runtime's own behaviour)
    why: str
    rule_id: int | None
    grant: dict[str, Any] | None = None


async def _weigh(s: AsyncSession, run: Run, tool: str, subjects: list[str], n: int) -> list[Verdict]:
    """Each subject through `tool_rules.decide`, with the run's grants applied to an 'ask'. No rule is its
    own answer here — `none` — because a run did things before tool rules existed: it wrote in its
    worktree unasked, and ran a project's command after the project's one-time answer. Only a rule a person
    wrote changes that."""
    out: list[Verdict] = []
    for subject in (normalise(tool, x) for x in subjects):
        said = await rule_for(s, tool, subject, run.project_id)
        if said.rule_id is None:
            out.append(Verdict(subject, "none", "", None))
            continue
        grant = granted(run.grants or [], tool, subject, n) if said.action == "ask" else None
        out.append(Verdict(subject, "allow" if grant else said.action, said.why, said.rule_id, grant))
    return out


def _grant_words(grant: dict[str, Any]) -> str:
    return f"{grant.get('by') or 'a person'} allowed it " + ("for this run" if grant.get("scope") == "run" else "once")


# ── a step's proposal, kept while a person is asked about it ────
def _proposal_file(worktree: str, ref: str, n: int) -> Path:
    """Beside the worktree, never in it: what the model proposed for a step a rule asked about, so the
    answer applies exactly those files rather than asking the model again for different ones."""
    return Path(worktree).parent / f"{ref}.step-{n}.proposal.json"


def _save_proposal(path: Path, proposal: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposal))
    path.chmod(0o600)


def _load_proposal(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else None
    except (OSError, ValueError) as e:
        log.warning("could not read the kept proposal %s: %s", path, e)
        return None
    return loaded if isinstance(loaded, dict) else None


def _drop_proposal(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("could not remove the kept proposal %s: %s", path, e)


async def _pause(db: Database, ref: str, step_n: int, *, title: str, tool: str, risk: str, payload: str,
                 reason: str, asks: dict[str, Any] | None = None, question: str = "") -> None:
    """Stop and wait for a person. The run is not failed — it is waiting, and it says what for.

    `asks` is what a tool rule asked about — the tool and its subjects — kept on the run so the answer
    ("Allow for this run") grants exactly those; `question` is an agent's own question, kept on its step."""
    async with db.session() as s:
        runs, approvals = RunRepository(s), ApprovalRepository(s)
        run = await runs.by_ref(ref)
        if run is None:
            return
        step = next((x for x in run.steps if x.n == step_n), None)
        approval_ref = await approvals.next_ref()
        # One step may stop more than once — a question, then a rule asking about what the answer led to —
        # so the gate's own ref is part of its id.
        s.add(Approval(id=f"ap-{run.ref.lower()}-{step_n}-{approval_ref.split('-')[-1]}", ref=approval_ref,
                       title=title, agent=step.agent if step else "", tool=tool, risk=risk, status="pending",
                       project_id=run.project_id, payload=payload, reason=reason,
                       run_ref=run.ref, step=step_n))
        if step is not None:
            step.status, step.detail = "waiting", f"Waiting for you · {approval_ref}"
            if question:
                step.question = question
        if asks is not None:
            _put(run, "asks", step_n, asks)
        run.status, run.waiting_on = "waiting", approval_ref
        await RunLogRepository(s).write(run.id, level="warn", step=step_n,
                                        line=f"waiting for your decision · {approval_ref} · {title}")
        await ActivityRepository(s).record(actor=step.agent if step else roster.ORCHESTRATOR, actor_kind="agent",
                                           action="Agent asks" if question else "Approval needed",
                                           detail=f"{approval_ref} · {title}",
                                           level="warn", project_id=run.project_id)


def _apply(parts: list[Part], elsewhere: frozenset[str], files: list[tuple[str, str]],
           references: frozenset[str] = frozenset()) -> tuple[list[str], list[Part]]:
    """Blocking. Write what the model proposed, each file in the worktree of the source its path names.
    Every path is checked before any is written, so a refused one leaves nothing half-applied; a path in a
    source this run did not open — a reference above all — is refused the same way as one that leaves the
    worktree. Returns the written paths as the project names them, and the parts that were written to."""
    routed: dict[int, list[tuple[str, str]]] = {}
    for path, content in files:
        agent.safe_path(path)
        hit = _route(parts, path, elsewhere)
        if hit is None:
            head = path.strip().split("/", 1)[0]
            if head in references:
                raise agent.Refused(f"refused to write {path}: {head} is a reference source — agents read it "
                                    "for grounding and never write there")
            raise agent.Refused(f"refused to write {path}: {head} is a source this run did not open")
        routed.setdefault(parts.index(hit[0]), []).append((hit[1], content))
    written: list[str] = []
    for n, items in routed.items():
        written += [parts[n].lead + rel for rel in agent.apply_files(parts[n].work, items)]
    return written, [parts[n] for n in routed]


def _commit_parts(touched: list[Part], message: str) -> dict[str, str]:
    """Blocking. Commit what a step wrote in every part it wrote to, and the commit each part now stands
    on — kept on the step, so the run can be taken back to exactly here."""
    out: dict[str, str] = {}
    for part in touched:
        if agent.commit(part.work, message):
            out[part.label] = agent.head(part.worktree)
    return out


async def _edit(db: Database, gateway: Gateway, ref: str, step_n: int) -> bool:
    """One agent step. The agent is grounded — handed the project's instructions for the files it may
    change and retrieval's pieces for the step — and governed: every file it would write goes through the
    tool rules first. A deny ends the step naming the rule; an ask stops the run at a gate and keeps the
    proposal beside the worktree, so the answer applies exactly what was asked about. An agent may ask a
    question instead of writing; the run then waits for the person's answer and does the step again with it."""
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        parts, elsewhere, references = _parts(run), _elsewhere(run), frozenset(_references(run))
        kept = _proposal_file(run.worktree, ref, step_n)
        lane, run_id, project_id = run.lane, run.id, run.project_id
        label, who = step.label, step.agent or run.agent or ""
        answered = bool(step.question and step.answer)
        proposal = await asyncio.to_thread(_load_proposal, kept)
        # A step owned by a custom agent is written with its instructions and on the lane it prefers; one
        # the plan named that is not on the roster and that nobody defined is written as the roster writes.
        spec = await CustomAgentService(s).by_name(await ProjectRepository(s).get(project_id), who) \
            if who and who not in roster.NAMES else None
        if proposal is None and spec is not None and not spec.may("edit"):
            said = (f"{spec.name} may not write files: its tools are {', '.join(spec.tools)}. Give it edit on "
                    "Agents, or give the step to another agent.")
            spec_refused = said
        else:
            spec_refused = ""
        if proposal is None and not spec_refused:
            pieces = await _pieces(s, gateway, run, step)
            # A piece from a project this one references is read as a piece, never taken for a file here.
            files, notes = await _context(s, run, step, [x["path"] for x in pieces if x["kind"] == "code"
                                                         and x.get("project", project_id) == project_id])
            told = await _told(s, project_id, [*(run.targets or []), *(h.path for h in files)])
            taste = await _taste(s, project_id)
            asked = (f"\n\nYou asked: {step.question}\nThe person answered: {step.answer}" if answered else "")
            read = ("\n\nRead from retrieval for this step — quote a ref when you rely on it:\n\n"
                    + "\n\n".join(f"[{x['kind']} · {x['ref']}]\n{x['text'][:PIECE_CHARS]}" for x in pieces)
                    if pieces else "")
            system = _instructed(EDIT_SYSTEM, told, taste)
            if spec is not None:
                # The agent's own instructions come first; the runtime's rules follow and win where they differ.
                system = (f"{spec.as_prompt()}\n\nWhatever the instructions above say, these rules of the runtime "
                          f"hold and win:\n\n{system}")
            prompt = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"You are the {step.agent}.\nProject: {run.project_id}\n"
                                            f"Requirement: {run.requirement}\nStep {step.n}: {step.label}\n"
                                            f"{step.detail}\n\nFiles you may change:\n"
                                            + ("\n\n".join(f"--- {h.path}"
                                                           + (" (read only: a reference source)" if h.readonly else "")
                                                           + f"\n{h.text}" for h in files)
                                               or "(no file matched; create what the step needs)")
                                            + read + asked},
            ]
            grounding = _grounding_doc(told, pieces, files, taste)

    if spec_refused:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "failed", spec_refused[:300]
            await RunLogRepository(s).write(run_id, level="err", step=step_n, line=spec_refused)
        return False

    if proposal is None:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            _put(run, "grounding", step_n, grounding)
            if spec is not None:
                await RunLogRepository(s).write(
                    run_id, level="info", step=step_n,
                    line=f"working as {spec.name} ({spec.source}{' · ' + spec.path if spec.path else ''})"
                         + (f" · prefers {spec.lane}" if spec.lane else ""))
            logs = RunLogRepository(s)
            for level, line in notes:
                await logs.write(run_id, level=level, step=step_n, line=line)
            given = grounding["instructions"]
            if given:
                await logs.write(run_id, level="info", step=step_n,
                                 line=f"given {len(given)} instruction file{'s' if len(given) != 1 else ''}: "
                                      + ", ".join(x["path"] for x in given[:6])
                                      + (" (cut to fit)" if grounding["capped"] else ""))
            if grounding["taste"]:
                await logs.write(run_id, level="info", step=step_n,
                                 line="taste applied: " + ", ".join(grounding["taste"][:8]))
            if grounding["pieces"]:
                await logs.write(run_id, level="info", step=step_n,
                                 line="read from retrieval: " + ", ".join(
                                     f"{x['path']}" + (f":{x['line']}" if x.get("line") else "")
                                     for x in grounding["pieces"]))
        try:
            result = await asyncio.to_thread(
                gateway.ask, prompt, lambda raw: EditOut.model_validate(extract_json(raw, trim=False)),
                feature="agent", project=project_id, role=WRITE, lane=(spec.lane if spec else None) or lane,
                agent=who, run_id=run_id)
        except NoModel as e:
            async with db.session() as s:
                run = await RunRepository(s).by_ref(ref)
                step = next(x for x in run.steps if x.n == step_n)
                step.status = "skipped"
                step.detail = "Needs a model. NeuroCode will not pretend to write code it cannot write."
                await RunLogRepository(s).write(run_id, level="warn", step=step_n, line=str(e))
            return False

        question = result.data.question.strip()
        if question and not result.data.files and not answered:
            await _log(db, run_id, "tool", f"{result.provider.model} asks: {question[:300]}", step_n)
            await _pause(db, ref, step_n, title=f"{who or 'The agent'} asks: {question[:160]}",
                         tool=f"{QUESTION_GATE}({who or 'agent'})", risk="LOW", payload=question[:2000],
                         reason="The agent stopped to ask rather than guess. Your answer is kept on the step, "
                                "remembered as a decision in this project's memory, and the step is done again "
                                "with it. Declining skips the step.", question=question[:2000])
            return True
        if question and answered:
            await _log(db, run_id, "warn", "asked again after your answer; one question per step, so it goes on "
                                           "with what it wrote", step_n)
        proposal = {"summary": result.data.summary, "notes": result.data.notes[:5],
                    "files": [{"path": f.path, "content": f.content} for f in result.data.files],
                    "model": result.provider.model, "ms": result.ms, "read": len(files)}

    paths = [f["path"] for f in proposal["files"]]
    try:
        # The worktree's own rule comes first: a path that escapes is refused before any rule is weighed.
        for path in paths:
            agent.safe_path(path)
    except agent.Refused as refused:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "failed", str(refused)
            await RunLogRepository(s).write(run_id, level="err", step=step_n, line=str(refused))
        await asyncio.to_thread(_drop_proposal, kept)
        return False

    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        verdicts = await _weigh(s, run, "edit", paths, step_n)
    denied = [v for v in verdicts if v.action == "deny"]
    asking = [v for v in verdicts if v.action == "ask"]
    if denied:
        said = f"Not written: a tool rule refuses {denied[0].subject} — {denied[0].why}" + (
            f" (and {len(denied) - 1} more)" if len(denied) > 1 else "")
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "failed", said[:300]
            logs = RunLogRepository(s)
            for v in denied:
                await logs.write(run_id, level="err", step=step_n, line=f"refused to write {v.subject} · {v.why}")
        await asyncio.to_thread(_drop_proposal, kept)
        return False
    if asking:
        await asyncio.to_thread(_save_proposal, kept, proposal)
        n = len(asking)
        await _pause(db, ref, step_n, title=f"Write {n} file{'s' if n != 1 else ''} a tool rule asks about",
                     tool=f"{EDIT_GATE}({n} file{'s' if n != 1 else ''})", risk="MEDIUM",
                     payload="\n".join(f"{v.subject}  · {v.why}" for v in asking),
                     reason="A tool rule asks before an agent writes these files, even in its own worktree. Allow "
                            "once, for the rest of this run, or always in this project; refuse, and none of this "
                            "step's files are written.",
                     asks={"tool": "edit", "subjects": [v.subject for v in asking],
                           "rules": sorted({v.rule_id for v in asking if v.rule_id is not None})})
        return True

    try:
        written, touched = await asyncio.to_thread(_apply, parts, elsewhere,
                                                   [(f["path"], f["content"]) for f in proposal["files"]], references)
    except agent.Refused as refused:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "failed", str(refused)
            await RunLogRepository(s).write(run_id, level="err", step=step_n, line=str(refused))
        await asyncio.to_thread(_drop_proposal, kept)
        return False

    commits: dict[str, str] = {}
    if written:
        message = f"{label}\n\n{str(proposal['summary']).strip()[:500]}\n\nNeuroCode {ref}"
        commits = await asyncio.to_thread(_commit_parts, touched, message)
    stat = await asyncio.to_thread(_stats, parts) if commits else None
    await asyncio.to_thread(_drop_proposal, kept)

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        run.model = proposal["model"]
        logs = RunLogRepository(s)
        await logs.write(run.id, level="tool", step=step_n,
                         line=f"{proposal['model']} read {proposal.get('read', 0)} files and answered in "
                              f"{proposal['ms'] / 1000:.1f}s")
        for note in proposal["notes"]:
            await logs.write(run.id, level="info", step=step_n, line=str(note)[:300])
        for path in written:
            await logs.write(run.id, level="tool", step=step_n, line=f"wrote {path}")
        allowed = [v for v in verdicts if v.action == "allow"]
        by_rule: dict[int | None, list[Verdict]] = {}
        for v in allowed:
            by_rule.setdefault(None if v.grant else v.rule_id, []).append(v)
        for rule_id, items in by_rule.items():
            why = _grant_words(items[0].grant) if rule_id is None and items[0].grant else items[0].why
            await logs.write(run.id, level="info", step=step_n,
                             line=f"allowed to write {', '.join(v.subject for v in items[:4])} · {why}")
            if rule_id is not None:
                # A rule decided in the person's place, so the team's feed says which rule and what it let through.
                await ActivityRepository(s).record(
                    actor=who or roster.ORCHESTRATOR, actor_kind="agent", action="Allowed by a tool rule",
                    detail=f"{ref} · wrote {len(items)} file{'s' if len(items) != 1 else ''} · {why}"[:300],
                    level="info", project_id=run.project_id)
        _spend(run, "edit", [v.subject for v in allowed if v.grant], step_n)
        # Every edit step keeps what it committed — an empty map when it wrote nothing — so a revert can
        # tell "no commit here" from "made before steps kept their commits".
        _put(run, "commits", step_n, commits)
        first = next(iter(commits.values()), "")
        step.commit_sha = first
        if stat:
            run.diff_files, run.diff_insertions = stat["files"], stat["insertions"]
            run.diff_deletions, run.diff_commits = stat["deletions"], stat["commits"]
            await logs.write(run.id, level="ok", step=step_n,
                             line=f"committed · {stat['files']} files +{stat['insertions']} −{stat['deletions']}"
                                  + (f" · {first[:7]}" if first else ""))
        step.detail = (str(proposal["summary"]) or "The model proposed no change for this step.")[:300]
        if not written:
            await logs.write(run.id, level="warn", step=step_n, line="no file changed")
    return False


async def _merge_step(db: Database, ref: str, step_n: int) -> bool:
    """Bring one agent's branch into the merge run. A collision is reported, never half-applied."""
    async with db.read() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        child = await runs.get(step.child_run_id) if step.child_run_id else None
        details = (run.worktree, run.base, run.branch, child.branch if child else step.detail,
                   child.status if child else "done", child.agent if child else "")
        mine, theirs = _parts(run), (_parts(child) if child else [])

    worktree, base, into, branch, child_status, child_agent = details
    if child_status not in ("done", "failed", "cancelled"):
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "skipped", f"{child_agent} is still working; nothing was merged."
        return False

    if len(mine) == 1 and not mine[0].label:
        ok, conflicts = await asyncio.to_thread(agent.merge_branch, Path(worktree), branch, base, into)
        stat = await asyncio.to_thread(agent.stats, Path(worktree), base)
    else:
        # Each source's branch of the agent's run into the same source's worktree of this one.
        ok, conflicts = False, []
        for part in mine:
            other = next((x for x in theirs if x.label == part.label), None)
            if other is None:
                continue
            merged, clash = await asyncio.to_thread(agent.merge_branch, part.worktree, other.branch, part.base,
                                                    part.branch)
            ok = ok or merged
            conflicts += [part.lead + f for f in clash]
        if conflicts:
            ok = False
        stat = await asyncio.to_thread(_stats, mine)

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        logs = RunLogRepository(s)
        if ok:
            run.diff_files, run.diff_insertions = stat["files"], stat["insertions"]
            run.diff_deletions, run.diff_commits = stat["deletions"], stat["commits"]
            step.detail = f"{branch} merged."
            await logs.write(run.id, level="ok", step=step_n,
                             line=f"merged {branch} · {stat['files']} files so far")
        elif conflicts:
            s.add(RunConflict(run_id=run.id, branch=branch, agent=child_agent, files=conflicts))
            step.status = "failed"
            step.detail = (f"Collides in {', '.join(conflicts[:3])}{'…' if len(conflicts) > 3 else ''} — the "
                           "merge was undone, so nothing is half-applied.")
            await logs.write(run.id, level="err", step=step_n,
                             line=f"conflict merging {branch}: {' '.join(conflicts[:6])}")
        else:
            step.status, step.detail = "skipped", f"{branch} has no commits to merge."
    return False


async def _command_gate(db: Database, ref: str, step_n: int, command: str, work: Path,
                        check: str = "") -> str:
    """What the tool rules say about running one of the project's own commands at this step: 'run',
    'paused' (a rule asks, and the run now waits), 'skipped' (a rule refuses) — or 'setting' when no rule
    covers it, and the project's one-time answer decides exactly as it did before rules existed."""
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        [verdict] = await _weigh(s, run, "command", [command], step_n)
        who = next((x.agent for x in run.steps if x.n == step_n), "") or roster.TESTER
    if verdict.action == "none":
        return "setting"
    if verdict.action == "ask":
        await _pause(db, ref, step_n, title=f"Run `{command}` — a tool rule asks first",
                     tool=f"{COMMAND_GATE}({command})", risk="MEDIUM",
                     payload=f"cwd {work}\ncommand {command}\n{verdict.why}",
                     reason="A tool rule asks before this command runs in the run's worktree. Allow it once, for "
                            "the rest of this run, or always in this project; refuse, and this step is skipped.",
                     asks={"tool": "command", "subjects": [command], "rules": [verdict.rule_id]})
        return "paused"
    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        logs = RunLogRepository(s)
        if verdict.action == "deny":
            step.status = "skipped"
            step.detail = f"Not run: a tool rule refuses `{command}` — {verdict.why}"[:300]
            if check:
                _put_check(run, step_n, status="skipped", summary=step.detail)
            await logs.write(run.id, level="warn", step=step_n, line=f"refused to run {command} · {verdict.why}")
            return "skipped"
        why = _grant_words(verdict.grant) if verdict.grant else verdict.why
        await logs.write(run.id, level="info", step=step_n, line=f"allowed to run {command} · {why}")
        if verdict.grant:
            _spend(run, "command", [command], step_n)
        else:
            await ActivityRepository(s).record(actor=who, actor_kind="agent", action="Allowed by a tool rule",
                                               detail=f"{ref} · ran `{command}` · {why}"[:300], level="info",
                                               project_id=run.project_id)
    return "run"


async def _test(db: Database, ref: str, step_n: int) -> bool:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        command, project_id, run_id = run.tests_command, run.project_id, run.id
        work, allowed_key = _work(run), f"runtime.tests.{run.project_id}"
        setting = await s.get(Setting, allowed_key)
        allowed = setting.value if setting else None
        project = await ProjectRepository(s).get(project_id)
        project_name = project.name if project else project_id

    if not command:
        async with db.session() as s:
            step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == step_n)
            step.status, step.detail = "skipped", "No test command was found in this project."
        return False

    ruled = await _command_gate(db, ref, step_n, command, work)
    if ruled != "run":
        if ruled == "paused":
            return True
        if ruled == "skipped":
            return False
        # No rule covers it: the project's one-time answer, as it always was.
        if allowed is None:
            await _pause(db, ref, step_n, title=f"Run `{command}` in {project_name}", tool=f"Bash({command})",
                         risk="MEDIUM", payload=f"cwd {work}\ncommand {command}",
                         reason="The agent wants to run this project's own tests inside its worktree. Nothing "
                                "else is run, and your answer is remembered for this project.")
            return True
        if allowed != "allowed":
            async with db.session() as s:
                step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == step_n)
                step.status, step.detail = "skipped", "You chose not to run tests in this project."
            return False

    argv = command.split()
    await _log(db, run_id, "tool", f"$ {command}", step_n)
    sha, module, started = await asyncio.to_thread(_before_tests, work)
    lines: list[str] = []
    # Every line goes to the reader, not only the ones kept for the log: a failure printed after line
    # 400 is still a failure.
    reader = testparse.Reader(roots=(str(work), os.path.realpath(work)), module=module)

    def keep(i: int, text: str) -> None:
        if i < TEST_LINES:
            lines.append(text)
        reader.feed(text)

    code, tail = await asyncio.to_thread(agent.run_tests, argv, work, keep, stopped(ref))
    interrupted = stopped(ref).is_set()
    found = reader.result()
    measured = [] if interrupted else await asyncio.to_thread(_coverage, work, started, module)

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        logs = RunLogRepository(s)
        for text in lines:
            await logs.write(run.id, level="tool", step=step_n, line=text)
        if interrupted:
            # Killed half-way, the exit code and the output say nothing about the code; keeping them
            # would record a failure that never happened.
            step.status, step.detail = "skipped", "Stopped by you before the tests finished."
            await logs.write(run.id, level="warn", step=step_n, line="tests stopped before they finished")
            return False
        passed = code == 0
        run.tests_status = "passed" if passed else "failed"
        run.tests_summary = " · ".join(t for t in tail if t.strip())[:300] or f"exit code {code}"
        run.tests_sha, run.tests_runner = sha[:64], found.runner
        run.tests_passed, run.tests_failed = found.passed, found.failed
        run.tests_skipped, run.tests_total = found.skipped, found.total
        await ResultsRepository(s).replace(
            run.id, step_n,
            [{"name": f.name, "file": f.file, "line": f.line, "message": f.message, "excerpt": f.excerpt}
             for f in found.failures],
            measured)
        step.status = "done" if passed else "failed"
        step.detail = run.tests_summary
        if found.runner and found.total is not None:
            await logs.write(run.id, level="info", step=step_n,
                             line=f"read by the {found.runner} parser · {found.passed} passed · "
                                  f"{found.failed} failed · {found.skipped} skipped of {found.total}")
        elif found.runner:
            await logs.write(run.id, level="info", step=step_n,
                             line=f"read by the {found.runner} parser · {len(found.failures)} failures named; "
                                  "it printed no totals")
        else:
            await logs.write(run.id, level="info", step=step_n,
                             line="no runner's output was recognised: pass or fail comes from the exit code only")
        if measured:
            covered, total = sum(m[1] for m in measured), sum(m[2] for m in measured)
            await logs.write(run.id, level="info", step=step_n,
                             line=f"coverage from {', '.join(sorted({m[3] for m in measured}))}: "
                                  f"{covered} of {total} covered")
        await logs.write(run.id, level="ok" if passed else "err", step=step_n,
                         line=f"tests {'passed' if passed else f'failed (exit {code})'}")
    return False


def _rule_review(diff: str, checks: list[dict[str, Any]] = ()) -> tuple[list[dict[str, Any]], str, str]:
    """With no model, the diff is still read — by rules, and the review says so."""
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    paths = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
    findings: list[dict[str, Any]] = []
    for rx, severity, note in ((SECRETS, "HIGH", "A secret looks hard-coded:"),
                               (LEFTOVERS, "MEDIUM", "Debugging output left in:"),
                               (TODO, "LOW", "A TODO was added:")):
        for line in [ln.strip()[:120] for ln in added if rx.search(ln)][:3]:
            findings.append({"severity": severity, "file": "", "note": f"{note} {line}"})
    if paths and not any(TEST_FILE.search(p) for p in paths):
        findings.append({"severity": "MEDIUM", "file": "",
                         "note": "No test file was touched, so nothing new guards this change."})
    for check in checks:
        if check.get("status") == "failed":
            findings.append({"severity": "MEDIUM", "file": "",
                             "note": f"The project's {check['name']} failed ({check['command']}): "
                                     f"{check.get('summary') or 'exit ' + str(check.get('exit'))}"[:300]})
    return findings, "Checked by rules only — a model would read the diff properly.", "offline rules"


def _briefed(brief: tuple[str, str]) -> str:
    """The repository's REVIEW.md, as the reviewer's system text carries it — after the project's own
    instructions, the same place for a run's review and one asked for on demand."""
    return (f"\n\nThe repository's brief for reviewers ({brief[1]}) — it says what matters here, what to leave "
            f"alone and how severe things are; follow it:\n{brief[0]}")


def _evidence(run: Run) -> str:
    """What the project's own commands said about this branch, for whoever reads its diff next."""
    lines = [f"Tests: {run.tests_status}" + (f" · {run.tests_summary}" if run.tests_summary else "")
             if run.tests_command else "Tests: this project has no test command."]
    for check in (run.review or {}).get("checks") or []:
        lines.append(f"Check {check['name']} ({check['command']}): {check.get('status', 'not run')}")
        if check.get("status") == "failed":
            lines += [f"  {line}" for line in (check.get("output") or [])[-15:]]
    return "\n".join(lines)


async def _review(db: Database, gateway: Gateway, ref: str, step_n: int) -> bool:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        base, requirement = run.base, run.requirement
        lane, project_id, run_id = run.lane, run.project_id, run.id
        reviewer = next((x.agent for x in run.steps if x.n == step_n), "") or roster.REVIEWER
        evidence, checks = _evidence(run), list((run.review or {}).get("checks") or [])
        parts = _parts(run)

    # Read from the branch itself, the same way merge and push read it, so the receipt's fingerprint
    # is of exactly the patch the reviewer was handed — and of exactly what would land. Across several
    # sources it is one patch, every path under its source's label.
    whole, head = await asyncio.to_thread(_patch, parts)
    diff = whole[:MAX_DIFF]
    receipt = {"sha256": agent.fingerprint(whole), "head": head, "base": base, "bytes": len(whole),
               "truncated": len(whole) > MAX_DIFF, "at": utcnow().isoformat()}
    if not diff.strip():
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "skipped", "Nothing changed, so there is nothing to review."
            run.review = {**(run.review or {}), "receipt": {**receipt, "by": ""}}
        return False

    # The reviewer is held to the same instructions the agents were: the project's files, for the paths
    # this diff touches.
    touched = sorted(set(re.findall(r"^\+\+\+ b/(.+)$", whole, re.M)) | set(re.findall(r"^--- a/(.+)$", whole, re.M)))
    async with db.read() as s:
        told = await _told(s, project_id, touched)
        taste = await _taste(s, project_id)
    given = [{"path": f["path"], "bytes": f["bytes"]} for f in told.files if f["applied"]]
    # The repository's own brief for reviewers, read from the checkout the run branched from — never from
    # the branch under review, so a change cannot rewrite the rules it is read by.
    first = parts[0]
    brief = await asyncio.to_thread(review_brief, first.repo / first.prefix if first.prefix else first.repo)
    system = _instructed(REVIEW_SYSTEM, told, taste) + (_briefed(brief) if brief else "")
    prompt = [{"role": "system", "content": system},
              # What the project's own commands said comes after the diff, so the preamble the Models
              # screen shows (requirement, then the diff) stays exactly what is sent first.
              {"role": "user", "content": f"Requirement: {requirement}\n\nDiff:\n{diff}" + f"\n\n{evidence}"}]
    try:
        # A second opinion is worth more from a different model, and free lanes make that free.
        result = await asyncio.to_thread(
            gateway.ask, prompt, lambda raw: ReviewOut.model_validate(extract_json(raw, trim=False)),
            feature="review", project=project_id, role=REVIEW, avoid=lane, agent=reviewer, run_id=run_id)
        findings = clean_findings(result.data.findings)
        verdict, by = result.data.verdict[:300], result.provider.model
    except NoModel:
        findings, verdict, by = _rule_review(diff, checks)
    except Exception as e:                                   # a bad answer must not lose the review
        findings, verdict, by = _rule_review(diff, checks)
        verdict = f"{verdict} The model failed: {type(e).__name__}."

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        kept = {k: v for k, v in (run.review or {}).items() if k != "reviewing"}
        run.review = {**kept, "findings": findings, "verdict": verdict, "by": by, "receipt": {**receipt, "by": by},
                      "instructions": given, "taste": list(taste.refs),
                      **({"brief": {"path": brief[1], "bytes": len(brief[0].encode())}} if brief else {})}
        step.detail = verdict or f"{len(findings)} findings"
        high = sum(1 for f in findings if f["severity"] == "HIGH")
        if given or brief:
            await RunLogRepository(s).write(run.id, level="info", step=step_n,
                                            line="the reviewer was given " + ", ".join(
                                                [*(x["path"] for x in given[:6]), *([brief[1]] if brief else [])]))
        await RunLogRepository(s).write(run.id, level="warn" if high else "ok", step=step_n,
                                        line=f"{len(findings)} findings ({high} high) · reviewed by {by} · "
                                             f"receipt {_short(receipt['sha256'])}")
    return False


async def _check(db: Database, ref: str, step_n: int) -> bool:
    """One of the project's own checks — its lint or its typecheck — in the worktree, behind the same
    first-time gate as its tests, remembered per check. A failing check is recorded as failed and
    raises the signature's risk; it does not end the run, because a codebase's old lint debt is not
    this change's fault — the person signing sees it and decides."""
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        check = _check_at(run, step_n) or {}
        name, command = check.get("name", ""), check.get("command", "")
        run_id = run.id
        # A further source's check runs in that source's worktree; the rest in the run's own.
        work = next((x.work for x in _parts(run) if check.get("label") and x.label == check["label"]), _work(run))
        is_tests = bool(check.get("tests"))
        setting = await s.get(Setting, check_key(run.project_id, name)) if name else None
        allowed = setting.value if setting else None
        project = await ProjectRepository(s).get(run.project_id)
        project_name = project.name if project else run.project_id

    if not command:
        async with db.session() as s:
            step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == step_n)
            step.status, step.detail = "skipped", "This check has no command recorded."
        return False
    ruled = await _command_gate(db, ref, step_n, command, work, check=name)
    if ruled != "run":
        if ruled == "paused":
            return True
        if ruled == "skipped":
            return False
        if allowed is None:
            await _pause(db, ref, step_n, title=f"Run `{command}` in {project_name}", tool=f"Bash({command})",
                         risk="MEDIUM", payload=f"cwd {work}\ncommand {command}",
                         reason=f"The agent wants to run this project's own {name} check inside its worktree. "
                                "Nothing else is run, and your answer is remembered for this project.")
            return True
        if allowed != "allowed":
            async with db.session() as s:
                run = await RunRepository(s).by_ref(ref)
                step = next(x for x in run.steps if x.n == step_n)
                step.status, step.detail = "skipped", f"You chose not to run the {name} check in this project."
                _put_check(run, step_n, status="skipped", summary=step.detail)
            return False

    await _log(db, run_id, "tool", f"$ {command}", step_n)
    lines: list[str] = []
    tail: list[str] = []

    def keep(i: int, text: str) -> None:
        if i < TEST_LINES:
            lines.append(text)
        tail.append(text)
        del tail[:-CHECK_TAIL]

    code, _ = await asyncio.to_thread(agent.run_tests, command.split(), work, keep, stopped(ref))
    interrupted = stopped(ref).is_set()

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        logs = RunLogRepository(s)
        for text in lines:
            await logs.write(run.id, level="tool", step=step_n, line=text)
        if interrupted:
            step.status, step.detail = "skipped", "Stopped by you before the check finished."
            _put_check(run, step_n, status="skipped", summary=step.detail)
            await logs.write(run.id, level="warn", step=step_n, line=f"{name} stopped before it finished")
            return False
        passed = code == 0
        said = " · ".join(t for t in tail[-3:] if t.strip())[:300]
        summary = ("passed" if passed else f"failed (exit {code})") + (f" · {said}" if said else "")
        _put_check(run, step_n, status="passed" if passed else "failed", exit=code, summary=summary[:300],
                   output=tail[-CHECK_TAIL:])
        step.detail = summary[:300]
        if is_tests:
            # A source's tests are the run's tests: one failing fails the step and the run's tests,
            # exactly as the first source's own would.
            if not passed:
                step.status = "failed"
                run.tests_status = "failed"
                run.tests_summary = f"{name}: {summary}"[:300]
            elif run.tests_status == "not run":
                run.tests_status, run.tests_summary = "passed", f"{name}: {summary}"[:300]
        await logs.write(run.id, level="ok" if passed else "err", step=step_n,
                         line=f"{name} {'passed' if passed else f'failed (exit {code})'}")
    return False


def check_key(project_id: str, name: str) -> str:
    """Where a person's answer about one of a project's checks is kept, beside `runtime.tests.<project>`."""
    return f"runtime.checks.{project_id}.{name}"


async def _gate_summary(s: AsyncSession, run: Run) -> tuple[str, str, str, str]:
    """What the signature asks: its title, the tool it names, its risk, and the lines it shows."""
    high = [f for f in (run.review or {}).get("findings", []) if f.get("severity") == "HIGH"]
    recorded, expected = await ResultsRepository(s).expected(run.id, run.project_id)
    calm = run.tests_status == "failed" and all_expected(run.tests_failed, recorded, expected)
    # The fact is kept as it was — tests_status stays "failed" — but a failure a person already
    # marked legacy or quarantined does not raise the gate on its own.
    failed = run.tests_status == "failed" and not calm
    tests_line = (f"tests failed · {recorded} failure{'' if recorded == 1 else 's'}, all expected "
                  "(legacy/quarantined)" if calm
                  else f"tests {run.tests_status}" + (f" · {run.tests_summary}" if run.tests_summary else ""))
    conflicts = list(run.conflicts)
    review = run.review or {}
    extra = [f"{x.label}: branch {x.branch} from {x.base[:7]}" for x in _parts(run)[1:]]
    lines = [f"branch {run.branch} from {run.base[:7]}", *extra,
             f"{run.diff_files} files · +{run.diff_insertions} −{run.diff_deletions} · "
             f"{run.diff_commits} commits",
             tests_line,
             f"review by {review.get('by') or 'nobody'}: {review.get('verdict') or '—'}"]
    receipt = review.get("receipt")
    if receipt:
        lines.append(f"reviewed diff {_short(receipt.get('sha256', ''))} at {str(receipt.get('head', ''))[:7]}")
    checks = review.get("checks") or []
    if checks:
        lines.append("checks: " + " · ".join(f"{c['name']} {c.get('status', 'not run')}" for c in checks))
    goal = review.get("goal")
    if goal:
        lines.append(f"goal: {goal.get('verdict', 'not run')} on attempt {run.attempt} of {run.goal_budget}"
                     + (f" · {goal['why']}" if goal.get("why") else ""))
    if conflicts:
        lines.append("collisions: " + " · ".join(
            f"{c.agent or c.branch} in {', '.join((c.files or [])[:3])}" for c in conflicts))
    checks_failed = any(c.get("status") == "failed" for c in checks)
    goal_missed = bool(goal) and goal.get("verdict") == "not met"
    title = f"Accept {run.ref}: {run.diff_files} files on {run.branch}"
    risk = "HIGH" if (high or failed or conflicts or checks_failed or goal_missed) else "MEDIUM"
    return title, f"Merge({run.branch})", risk, "\n".join(lines)


async def _handoff(db: Database, ref: str, step_n: int) -> bool:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        nothing = run.diff_files == 0
        if not nothing:
            title, tool, risk, payload = await _gate_summary(s, run)
            branch = run.branch

    if nothing:
        async with db.session() as s:
            step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == step_n)
            step.status, step.detail = "skipped", "Nothing to accept: no file changed."
        return False

    await _pause(db, ref, step_n, title=title, tool=tool, risk=risk, payload=payload,
                 reason=f"Approve and the branch is yours to merge, from here or with git; refuse and the "
                        f"branch and its worktree are removed. Nothing has been merged into your "
                        f"repository, and nothing has left the worktree ({branch}).")
    return True


async def reread(db: Database, gateway: Gateway, ref: str, step_n: int, by: str) -> None:
    """The review, read again on the branch as it is now — asked for by a person, in the background.
    A run waiting at its signature has the signature's summary rewritten, so what it asks is current."""
    started = time.monotonic()
    try:
        await _review(db, gateway, ref, step_n)
    except Exception as e:                                   # the step says what happened, never hangs
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            if run is None:
                return
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "failed", f"{type(e).__name__}: {str(e)[:200]}"
            run.review = {k: v for k, v in (run.review or {}).items() if k != "reviewing"}
            await RunLogRepository(s).write(run.id, level="err", step=step_n,
                                            line=f"reading it again failed: {type(e).__name__}")
        return
    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        if step.status == "running":
            step.status = "done"
        step.ms = round((time.monotonic() - started) * 1000)
        run.review = {k: v for k, v in (run.review or {}).items() if k != "reviewing"}
        if run.status == "waiting":
            gate = await ApprovalRepository(s).waiting_on_person(ref)
            if gate is not None and gate.status == "pending":
                _title, _tool, gate.risk, gate.payload = await _gate_summary(s, run)
        await RunLogRepository(s).write(run.id, level="info", step=step_n,
                                        line=f"read again at the request of {by}")


# ── the completion check of a goal run ───────────────────────────
class CriterionOut(BaseModel):
    criterion: str = ""
    met: bool = False
    evidence: str = ""
    file: str = ""


class GoalOut(BaseModel):
    criteria: list[CriterionOut] = Field(default_factory=list)
    verdict: str = ""


GOAL_SYSTEM = """You are the completion check inside NeuroCode. You did not write this change. You are given the
plan's acceptance criteria, numbered, the real diff, and what the project's own tests and checks printed. For each
criterion decide whether what you were given PROVES it is met. Cite the evidence: the file and lines in the diff, or
the test that passed. A criterion you cannot prove from what you were given is not met — say what is missing.
Reply with one JSON object: {"criteria": [{"criterion": "the criterion, as given", "met": true|false,
"evidence": "what proves it, or what is missing", "file": "the file in the diff you cite, or empty"}],
"verdict": "one sentence"}. One entry per criterion, in the order given."""


def _judge(criteria: list[str], answer: GoalOut | None, paths: set[str]) -> list[dict[str, Any]]:
    """Each criterion with the model's word on it, held to the rule that met needs evidence — and a
    cited file the diff really touches. Taken in order: a model that renames a criterion does not get
    to judge a different one."""
    out: list[dict[str, Any]] = []
    given = answer.criteria if answer else []
    for i, criterion in enumerate(criteria):
        said = given[i] if i < len(given) else None
        if said is None:
            out.append({"criterion": criterion, "met": False, "evidence": "", "file": "",
                        "why": "not judged" if answer else "no model judged it"})
            continue
        evidence, cited = said.evidence.strip()[:400], said.file.strip()
        met, why = said.met, ""
        if met and not evidence:
            met, why = False, "said met, but cited no evidence"
        elif met and cited and cited not in paths:
            met, why = False, f"cites {cited}, which this diff does not touch"
        out.append({"criterion": criterion, "met": met, "evidence": evidence, "file": cited, "why": why})
    return out


async def _goal(db: Database, gateway: Gateway, ref: str, step_n: int) -> str:
    """Whether a goal run is done: 'met', 'rework' (not met, and attempts remain) or 'sign'.

    Two parts, and the model is only one of them. The deterministic part is the project's own word —
    its tests and checks — plus the plan having criteria at all. The model part asks a lane other than
    the writer's to judge each criterion against the diff and that output, and a criterion only counts
    as met with evidence. When no other lane can judge, the check says so and leaves it to the person
    rather than trusting the writer to mark its own work."""
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        plan = await PlanRepository(s).get(run.plan_id) if run.plan_id else None
        criteria = [str(c).strip() for c in (plan.acceptance_criteria if plan else None) or [] if str(c).strip()]
        children = (await RunRepository(s).children_of([run.id])).get(run.id, [])
        writers = {x for x in [run.lane, *(c.lane for c in children)] if x}
        recorded, expected = await ResultsRepository(s).expected(run.id, run.project_id)
        calm = run.tests_status == "failed" and all_expected(run.tests_failed, recorded, expected)
        tests = "passed" if run.tests_status == "passed" or calm else run.tests_status
        checks = [{"name": c["name"], "status": c.get("status", "not run"), "summary": c.get("summary", "")}
                  for c in (run.review or {}).get("checks") or []]
        requirement = run.requirement
        attempt, budget, run_id, project_id = run.attempt, run.goal_budget or 1, run.id, run.project_id
        evidence = _evidence(run)
        parts = _parts(run)

    patch, _ = await asyncio.to_thread(_patch, parts)
    paths = set(re.findall(r"^\+\+\+ b/(.+)$", patch, re.M)) | set(re.findall(r"^--- a/(.+)$", patch, re.M))
    problems: list[str] = []
    if tests == "failed":
        problems.append("the project's tests failed")
    problems += [f"the {c['name']} check failed" for c in checks if c["status"] == "failed"]

    answer: GoalOut | None = None
    by, unjudged = "", ""
    if not criteria:
        unjudged = "the plan has no acceptance criteria to judge against"
    else:
        others = [x.id for x in gateway.chain(role=REVIEW, limit=20) if x.id not in writers]
        if not others:
            unjudged = "no lane other than the one that wrote it can judge it"
        else:
            listed = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))
            prompt = [{"role": "system", "content": GOAL_SYSTEM},
                      {"role": "user", "content": f"Requirement: {requirement}\n\nAcceptance criteria:\n{listed}"
                                                  f"\n\n{evidence}\n\nDiff:\n{patch[:MAX_DIFF] or '(no change)'}"}]
            try:
                result = await asyncio.to_thread(
                    gateway.ask, prompt, lambda raw: GoalOut.model_validate(extract_json(raw, trim=False)),
                    # Ledgered as a review — it is one, of the goal — and named by its agent, so the
                    # completion check's spend is its own line in the per-agent usage.
                    feature="review", project=project_id, role=REVIEW, lane=others[0],
                    avoid=next(iter(writers), None), agent=GOAL_CHECK, run_id=run_id)
            except NoModel:
                unjudged = "no model can judge it"
            except Exception as e:                           # a provider that failed judges nothing
                unjudged = f"the model failed ({type(e).__name__})"
            else:
                if result.provider.id in writers:
                    unjudged = f"only {result.provider.id}, the lane that wrote it, answered"
                else:
                    answer, by = result.data, result.provider.model

    judged = _judge(criteria, answer, paths)
    unmet = [c for c in judged if not c["met"]]
    if answer is not None and not problems and not unmet:
        verdict, why = "met", (answer.verdict.strip()[:300] or "Every criterion is met, with evidence.")
    elif answer is None and not problems:
        verdict, why = "unjudged", unjudged[:1].upper() + unjudged[1:] + "; the person signing decides."
    else:
        verdict = "not met"
        said = problems + [f"criterion {judged.index(c) + 1} not met" for c in unmet if answer is not None]
        why = "; ".join(said)[:300] or "not met"
        if answer is None and unjudged:
            why = f"{why}; {unjudged}"[:300]
    next_step = "rework" if verdict == "not met" and attempt < budget else "sign"

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        goal = {**((run.review or {}).get("goal") or {"step": step_n}), "verdict": verdict, "why": why,
                "tests": tests, "checks": checks, "criteria": judged, "by": by, "at": utcnow().isoformat(),
                "next": next_step}
        run.review = {**(run.review or {}), "goal": goal}
        tail = (f" · attempt {attempt + 1} of {budget} starts" if next_step == "rework"
                else f" · attempt {attempt} of {budget}")
        step.detail = f"{verdict.capitalize()} · {why}"[:260] + tail
        logs = RunLogRepository(s)
        for i, c in enumerate(judged, 1):
            await logs.write(run.id, level="ok" if c["met"] else "warn", step=step_n,
                             line=f"criterion {i} {'met' if c['met'] else 'not met'}: {c['criterion'][:120]}"
                                  + (f" · {c['evidence'][:160]}" if c["evidence"] else "")
                                  + (f" · {c['why']}" if c["why"] else ""))
        judge = f" · judged by {by}" if by else ""
        await logs.write(run.id, level="ok" if verdict == "met" else "warn", step=step_n,
                         line=f"goal {verdict} on attempt {attempt} of {budget}{judge}")
    return "rework" if next_step == "rework" else ("met" if verdict == "met" else "sign")


def _goal_notes(goal: dict[str, Any], attempt: int, budget: int) -> str:
    """What the next attempt is told: what the completion check found, criterion by criterion."""
    lines = [f"The completion check of attempt {attempt} of {budget} found the goal not met: {goal.get('why', '')}"]
    if goal.get("tests") == "failed":
        lines.append("- The project's tests failed. Make them pass.")
    for check in goal.get("checks") or []:
        if check.get("status") == "failed":
            lines.append(f"- The {check['name']} check failed: {check.get('summary', '')}"[:300])
    for i, c in enumerate(goal.get("criteria") or [], 1):
        if not c.get("met"):
            reason = c.get("why") or c.get("evidence") or "no evidence it is met"
            lines.append(f"- Criterion {i} is not met — {c['criterion']}: {reason}"[:400])
    return "\n".join(lines)


async def _goal_rework(db: Database, gateway: Gateway, ref: str) -> bool:
    """Start the next attempt of a goal run that missed: the same plan, done again with what the
    completion check found, through the same rework a person uses. This attempt's signature is never
    asked — the next attempt ends at one. False when the next attempt could not be made; the run then
    goes on to your signature as it is."""
    try:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            goal = (run.review or {}).get("goal") or {}
            notes = _goal_notes(goal, run.attempt, run.goal_budget or 1)
            for step in run.steps:
                if step.kind == "handoff" and step.status == "todo":
                    step.status = "skipped"
                    step.detail = f"Not asked: the goal was not met, so attempt {run.attempt + 1} does it again."
            run.status, run.finished_at = "cancelled", utcnow()
            await s.flush()
            made = await RunService(s, gateway).rework(ref, notes, by=GOAL_CHECK, by_id="", may_decide=True,
                                                       actor_kind="agent")
            lead, batch = made[-1].ref, len(made) > 1
    except Refused as refused:
        await _log(db, (await _run_id(db, ref)), "err", f"the next attempt could not start: {refused}")
        return False
    _STOPPED.pop(ref, None)
    await (execute_batch if batch else execute)(db, gateway, lead)
    return True


async def _run_id(db: Database, ref: str) -> str:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        return run.id if run else ""


async def execute(db: Database, gateway: Gateway, ref: str, resume_from: int | None = None) -> None:
    """Work the steps in order, one transaction each. Returns when the run finishes or starts waiting."""
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None or run.status in ("done", "failed", "cancelled"):
            return
        if resume_from is None:
            stopped(ref).clear()
            parts = _parts(run)
            try:
                for part in parts:
                    await asyncio.to_thread(agent.open_worktree, part.repo, part.branch, part.base, part.worktree)
            except Exception as e:
                run.status, run.finished_at, run.note = "failed", utcnow(), str(e)[:200]
                await RunLogRepository(s).write(run.id, level="err", line=str(e)[:300])
                return
            for part in parts:
                where = f"{part.label} · " if part.label else ""
                await RunLogRepository(s).write(run.id, level="ok",
                                                line=f"worktree ready · {where}{part.branch} from {part.base[:7]}")
            await ActivityRepository(s).record(
                actor=roster.ORCHESTRATOR, actor_kind="agent", action="Run started",
                detail=f"{ref} · {len(run.steps)} steps on {run.branch}", project_id=run.project_id)
        run.status = "running"
        run_id = run.id
        plan = [(x.n, x.kind) for x in run.steps]
        checks = {c.get("step") for c in (run.review or {}).get("checks") or []}
        goal_step = ((run.review or {}).get("goal") or {}).get("step")

    from .plans import step_gate_for                  # plans imports this module
    for n, kind in plan:
        if resume_from is not None and n < resume_from:
            continue
        async with db.read() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next((x for x in run.steps if x.n == n), None)
            if step is None or step.status in ("done", "skipped", "failed"):
                continue
            # "Dispatch, pause before each step" (`plans.step_gate`): the plan says whether this step waits
            # for a person first, and what the gate asks; the runtime pauses and resumes.
            # One agent of several is never walked step by step: its merge run would go on without it.
            walk = await step_gate_for(s, run, step) if run.role != "agent" else None
        if stopped(ref).is_set():
            await _finish(db, ref, "cancelled", "Stopped by you.")
            return
        if walk is not None:
            await _pause(db, ref, n, title=walk.title, tool=walk.tool, risk=walk.risk, payload=walk.payload,
                         reason=walk.reason)
            return

        async with db.session() as s:
            step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == n)
            step.status = "running"
        started = time.monotonic()
        again = False
        try:
            if kind == "edit":
                paused = await _edit(db, gateway, ref, n)
            elif kind == "merge":
                paused = await _merge_step(db, ref, n)
            elif kind == "test":
                paused = await (_check(db, ref, n) if n in checks else _test(db, ref, n))
            elif kind == "review" and n == goal_step:
                paused, again = False, await _goal(db, gateway, ref, n) == "rework"
            elif kind == "review":
                paused = await _review(db, gateway, ref, n)
            else:
                paused = await _handoff(db, ref, n)
        except Exception as e:                               # a run never pretends to be alive
            async with db.session() as s:
                step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == n)
                step.status, step.detail = "failed", f"{type(e).__name__}: {str(e)[:200]}"
            await _log(db, run_id, "err", f"{type(e).__name__}: {str(e)[:300]}", n)
            paused = False

        async with db.session() as s:
            step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == n)
            if step.status == "running":
                step.status = "done"
            step.ms = round((time.monotonic() - started) * 1000)
        if paused:
            return
        if again and await _goal_rework(db, gateway, ref):
            return

    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        verdict = "failed" if any(x.status == "failed" for x in run.steps) else "done"
        note = run.note
        if run.role == "check" and run.tests_status == "not run":
            # A test-only run whose tests never ran did not succeed at anything: it was stopped, or
            # the tests were refused, and its step says which.
            verdict = "cancelled"
            note = note or next((x.detail for x in run.steps if x.detail), "The tests did not run.")
        child = bool(run.parent_id)
    await _finish(db, ref, verdict, note)
    if child:
        await _wake_merge(db, gateway, ref)


async def execute_batch(db: Database, gateway: Gateway, ref: str) -> None:
    """Every agent works at the same time, each in a worktree of its own. When they are done — or have
    run out of time — the merge run brings the branches together and the usual gates follow."""
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None:
            return
        children = (await runs.children_of([run.id])).get(run.id, [])
        refs = [c.ref for c in children]
        names = ", ".join(c.agent or "?" for c in children)
        run.status = "running"
        await RunLogRepository(s).write(run.id, level="info",
                                        line=f"{len(children)} agents working in parallel: {names}")
        await ActivityRepository(s).record(
            actor=roster.ORCHESTRATOR, actor_kind="agent", action="Agents started",
            detail=f"{run.ref} · {len(children)} agents, a worktree each", project_id=run.project_id)

    await asyncio.gather(*(execute(db, gateway, child) for child in refs), return_exceptions=True)
    if await _hold_for_agents(db, ref):
        return
    await execute(db, gateway, ref)


async def _hold_for_agents(db: Database, ref: str) -> bool:
    """True when an agent of this merge run stopped at a gate — a tool rule asking, or a question — so the
    merge run waits too, pointing at that gate, instead of going on and merging an agent that has not
    finished. The agent finishing wakes it (`_wake_merge`)."""
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None:
            return False
        children = (await runs.children_of([run.id])).get(run.id, [])
        held = [c for c in children if c.status == "waiting"]
        if not held:
            return False
        run.status, run.waiting_on = "waiting", held[0].waiting_on
        run.review = {**(run.review or {}), "awaiting": [c.ref for c in held]}
        await RunLogRepository(s).write(run.id, level="warn", line="waiting for " + ", ".join(
            f"{c.agent or c.ref} ({c.ref}, at {c.waiting_on})" for c in held) + " before merging")
        return True


async def _wake_merge(db: Database, gateway: Gateway, ref: str) -> None:
    """An agent of a merge run finished: when it was the last one the merge run waited for, the merge run
    starts. While another agent still waits for a person, the merge run points at that one's gate."""
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None or not run.parent_id:
            return
        parent = await runs.get(run.parent_id)
        if parent is None or parent.status != "waiting" or not (parent.review or {}).get("awaiting"):
            return
        siblings = (await runs.children_of([parent.id])).get(parent.id, [])
        if any(c.status in ("queued", "running", "waiting") for c in siblings):
            held = next((c for c in siblings if c.status == "waiting"), None)
            parent.waiting_on = held.waiting_on if held is not None else parent.waiting_on
            return
        parent.status, parent.waiting_on = "queued", None
        parent.review = {k: v for k, v in (parent.review or {}).items() if k != "awaiting"}
        lead = parent.ref
    await execute(db, gateway, lead)


async def _decided(s: AsyncSession, ref: str, step_n: int) -> Approval | None:
    """The gate a person just answered for this step of this run: the newest one, whatever it asked."""
    return (await s.execute(select(Approval).where(Approval.run_ref == ref, Approval.step == step_n)
                            .order_by(*ApprovalRepository.ORDER).limit(1))).scalars().first()


async def resume(db: Database, gateway: Gateway, ref: str, step_n: int, approved: bool) -> None:
    """Called when a person decides on an approval a run was waiting for.

    What the answer does depends on what the gate asked (`schemas.work.gate_kind`): a project's first
    test run or check is remembered as its standing answer; a tool rule's ask on a command or on files is
    answered by the grants the decision wrote (`services.gates`) and the step is done again; an agent's
    question has its answer on the step and the step is done again with it; a pause before a step lets it
    run or ends the run; and the signature accepts the branch or removes it."""
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None or run.status != "waiting":
            return
        step = next((x for x in run.steps if x.n == step_n), None)
        if step is None:
            return
        gate = await _decided(s, ref, step_n)
        kind = gate_kind(gate.tool) if gate is not None else ("tests" if step.kind == "test" else "signature")
        branch, project_id, run_id = run.branch, run.project_id, run.id
        run.waiting_on = None
        logs = RunLogRepository(s)
        check = _check_at(run, step_n)

        if kind == "tests":
            # The same gate answers a check: its answer is kept under the check's own name.
            key = check_key(project_id, check["name"]) if check else f"runtime.tests.{project_id}"
            what = f"{check['name']} check" if check else "tests"
            setting = await s.get(Setting, key)
            value = "allowed" if approved else "refused"
            if setting is None:
                s.add(Setting(key=key, value=value))
            else:
                setting.value = value
            step.status = "todo" if approved else "skipped"
            if not approved:
                step.detail = (f"You chose not to run the {what} in this project." if check
                               else "You chose not to run tests in this project.")
                if check:
                    _put_check(run, step_n, status="skipped", summary=step.detail)
            await logs.write(run_id, level="ok" if approved else "warn", step=step_n,
                             line=f"you allowed this project's {what} to run" if approved else f"{what} refused")
        elif kind in ("command", "edit", "question", "step"):
            step.status = "todo"
            if not approved:
                step.status = "failed" if kind == "edit" else "skipped"
                step.detail = {
                    "command": "You refused this command, so it did not run.",
                    "edit": "You refused the files a tool rule asked about; none of this step's files were written.",
                    "question": "You chose not to answer, so the step was skipped.",
                    "step": "Stopped here by you, before this step ran.",
                }[kind]
                if check:
                    _put_check(run, step_n, status="skipped", summary=step.detail)
            said = {"command": ("you allowed the command", "you refused the command"),
                    "edit": ("you allowed the files", "you refused the files"),
                    "question": ("answered — the step goes again with your answer", "you chose not to answer"),
                    "step": ("you let this step run", "you stopped the run here")}[kind][0 if approved else 1]
            await logs.write(run_id, level="ok" if approved else "warn", step=step_n,
                             line=f"{gate.ref if gate else 'the gate'}: {said}")
            worktree = run.worktree

    if kind == "tests":
        await execute(db, gateway, ref, resume_from=step_n if approved else step_n + 1)
        return
    if kind in ("command", "edit", "question"):
        if kind == "edit" and not approved:
            await asyncio.to_thread(_drop_proposal, _proposal_file(worktree, ref, step_n))
        await execute(db, gateway, ref, resume_from=step_n if approved else step_n + 1)
        return
    if kind == "step":
        if approved:
            await execute(db, gateway, ref, resume_from=step_n)
        else:
            await _finish(db, ref, "cancelled", f"You stopped the run before step {step_n}. Its branch stays "
                                                "for you to look at, or discard it.")
        return

    if approved:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "done", "Accepted by you."
            await RunLogRepository(s).write(run.id, level="ok", step=step_n,
                                            line=f"accepted · merge it with: git merge {branch}")
            signed = await _decided(s, ref, step_n)
            if signed is not None:
                await _signal(s, lambda: TasteService(s).on_verdict(run, signed, approved=True,
                                                                     by_user_id=signed.decided_by))
            if run.task_id:
                task = await TaskRepository(s).get(run.task_id)
                if task is not None and task.status != "review":
                    task.status = "review"
        async with db.read() as s:
            run = await RunRepository(s).by_ref(ref)
            verdict = "failed" if any(x.status == "failed" for x in run.steps) else "done"
        await _finish(db, ref, verdict, f"Accepted. Merge it with: git merge {branch}")
        return

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        step.status, step.detail = "failed", "You refused the changes."
        await RunLogRepository(s).write(run.id, level="warn", step=step_n,
                                        line="refused — removing the branch and its worktree")
        signed = await _decided(s, ref, step_n)
        if signed is not None:
            await _signal(s, lambda: TasteService(s).on_verdict(run, signed, approved=False,
                                                                 by_user_id=signed.decided_by))
    async with db.read() as s:
        refused = await RunRepository(s).by_ref(ref)
    await asyncio.to_thread(_cleanup, refused)
    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        run.removed = True
    await _finish(db, ref, "cancelled", "You refused the changes; the branch and its worktree were removed.")
