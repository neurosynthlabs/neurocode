"""The agent runtime, orchestrated.

The hands are in `app/agent` — blocking git, files and the project's own test command, none of which
know what a database is. This is the other half: it decides which step runs next, and writes down
what happened the moment it happens.

Three rules survive the move unchanged, because they are what make a run safe to leave alone:
nothing touches your working tree, nothing a model says is ever executed, and the only command that
runs is the project's own — the first time in a project, behind your approval.

Every step commits in a transaction of its own. A crash mid-run therefore loses the step in flight,
never the run: what was already done is already saved, and the screen shows exactly how far it got.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import agent, onboarding
from ..agent.git import LEFTOVERS, SECRETS, TODO
from ..ai.gateway import REVIEW, WRITE, Gateway, NoModel, extract_json
from ..data.base import utcnow
from ..data.engine import Database
from ..models import Approval, CodeFile, CodeSymbol, Project, Run, RunConflict, RunStep, Setting
from ..repositories import (
    ActivityRepository,
    ApprovalRepository,
    NotFound,
    ProjectRepository,
    RunLogRepository,
    RunRepository,
    TaskRepository,
)
from .errors import Refused

WORKTREES = Path(__file__).resolve().parent.parent.parent / ".worktrees"
MAX_CONTEXT, MAX_DIFF, AGENT_TIMEOUT, TEST_LINES = 60_000, 200_000, 1800, 400
GATE_WORDS = ("approval", "approve", "sign-off", "sign off", "signature")
TEST_FILE = re.compile(r"(^|/)(tests?|spec)s?/|\.(test|spec)\.[jt]sx?$|_test\.py$|test_.*\.py$")

#: Runs a person stopped, in this process. A stop is a request, not a promise across a restart.
_STOPPED: dict[str, threading.Event] = {}


class FileOut(BaseModel):
    path: str
    content: str


class EditOut(BaseModel):
    summary: str = ""
    files: list[FileOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    severity: str = "LOW"
    file: str = ""
    note: str


class ReviewOut(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    verdict: str = ""


EDIT_SYSTEM = """You are a senior engineer working inside NeuroCode. You get a requirement, one step of an agreed
plan, and the current contents of the files you may change. Return the complete new content of every file you
change — never a patch, never a fragment, never "// unchanged". Change as little as the step needs, keep the
file's existing style and imports, and never invent an API you cannot see in the files you were given. You may
add a new file beside the ones you are shown. You do not run commands and you never touch anything else.
Reply with one JSON object: {"summary": "what you changed and why", "files": [{"path": "...", "content": "..."}],
"notes": ["anything the operator must know"]}"""

REVIEW_SYSTEM = """You are the reviewer inside NeuroCode. You are given a real diff. Report only what a careful
engineer would stop at: correctness, a missing test for the behaviour that changed, a security or data risk, a
convention the surrounding code follows and this diff breaks. No style nits, no praise.
Reply with one JSON object: {"findings": [{"severity": "HIGH|MEDIUM|LOW", "file": "...", "note": "..."}],
"verdict": "one sentence"}"""


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
    return {"repo": repo, "prefix": prefix, "base": head.stdout.strip(), "tests": agent.detect_tests(root)}


def _work(run: Run) -> Path:
    return Path(run.worktree) / run.prefix if run.prefix else Path(run.worktree)


def _by_agent(plan_steps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The plan's work, grouped by the agent that owns it. A gate belongs to no agent: you are the gate."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for step in plan_steps:
        label = (step.get("label") or "").lower()
        if any(word in label for word in GATE_WORDS) or step.get("agent") in ("AI Commander", "AI Project Manager"):
            continue
        groups.setdefault(step.get("agent") or "Engineer", []).append(step)
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
    async def plan_runs(self, plan: Any, task: Any, project: Project, by: str) -> list[Run]:
        """One run when one agent owns the work; otherwise an agent per worktree, plus the run that
        merges them. The run that leads — the one to start — is last."""
        setup = await asyncio.to_thread(_setup, project)
        steps = [{"label": s.label, "agent": s.agent, "detail": s.detail} for s in plan.steps]
        groups = _by_agent(steps)
        tests = setup["tests"]

        if len(groups) <= 1:
            work = [s for items in groups.values() for s in items]
            solo = await self._new_run(plan, task, project, by, setup, role="solo",
                                       lane=self.gateway.spread(1, WRITE)[0])
            await self._add_steps(solo, [*self._edit_steps(work), *self._tail(len(work), tests)])
            return [solo]

        lanes = self.gateway.spread(len(groups), WRITE)
        children: list[Run] = []
        for (name, items), lane in zip(groups.items(), lanes, strict=True):
            child = await self._new_run(plan, task, project, by, setup, role="agent", agent=name,
                                        lane=lane, suffix=_slug(name))
            await self._add_steps(child, self._edit_steps(items))
            children.append(child)

        integration = await self._new_run(plan, task, project, by, setup, role="integration")
        merges = [{"n": i + 1, "kind": "merge", "label": f"Merge what {c.agent} wrote", "agent": "Orchestrator",
                   "detail": c.branch, "child_run_id": c.id} for i, c in enumerate(children)]
        await self._add_steps(integration, [*merges, *self._tail(len(merges), tests)])
        for child in children:
            child.parent_id = integration.id
        await self.session.flush()
        return [*children, integration]

    def _edit_steps(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"n": i + 1, "kind": "edit", "label": s["label"], "agent": s["agent"],
                 "detail": s.get("detail", "")} for i, s in enumerate(items)]

    def _tail(self, done: int, tests: dict[str, Any] | None) -> list[dict[str, Any]]:
        """What every run ends with: the project's tests, a read of the real diff, and your signature."""
        out: list[dict[str, Any]] = []
        if tests:
            out.append({"n": done + 1, "kind": "test", "agent": "QA Engineer",
                        "label": f"Run the project's tests · {tests['command']}"})
        out.append({"n": done + len(out) + 1, "kind": "review", "label": "Review the diff", "agent": "Code Reviewer"})
        out.append({"n": done + len(out) + 1, "kind": "handoff", "label": "Your approval", "agent": "You"})
        return out

    async def _new_run(self, plan: Any, task: Any, project: Project, by: str, setup: dict[str, Any], *,
                       role: str, agent: str | None = None, lane: str | None = None,
                       suffix: str = "") -> Run:
        ref = await self.runs.next_ref()
        stem = f"neurocode/{(task.ref if task else plan.ref).lower()}"
        wanted = f"{stem}-{suffix}" if suffix else stem
        branch = await asyncio.to_thread(agent_free_branch, setup["repo"], wanted)
        tests = setup["tests"] or {}
        return await self.runs.add(Run(
            id=f"r{ref.split('-')[-1]}-{int(time.time())}", ref=ref, project_id=project.id,
            task_id=getattr(task, "id", None), plan_id=plan.id, status="queued", role=role,
            agent=agent, lane=lane, branch=branch,
            worktree=str(WORKTREES / project.id / ref), repo=str(setup["repo"]), prefix=setup["prefix"],
            base=setup["base"], requirement=plan.raw_requirement, requested_by=by,
            targets=list(plan.affected_files or [])[:12],
            tests_command=tests.get("command", "") or "", tests_status="not run",
            review={"findings": [], "verdict": "", "by": ""}))

    async def _add_steps(self, run: Run, steps: list[dict[str, Any]]) -> None:
        for step in steps:
            self.session.add(RunStep(run_id=run.id, n=step["n"], kind=step["kind"], label=step["label"],
                                     agent=step.get("agent", ""), detail=step.get("detail", ""),
                                     child_run_id=step.get("child_run_id")))
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
        await self.session.flush()
        await self.activity.record(actor=by, actor_kind="human", action="Run stopped",
                                   detail=f"{ref} · {run.branch} — the worktree stays for you to look at",
                                   level="warn", project_id=run.project_id)
        return run

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
                await asyncio.to_thread(agent.cleanup, Path(target.repo), Path(target.worktree), target.branch)
                target.removed = True
        await self.session.flush()
        await self.activity.record(actor=by, actor_kind="human", action="Worktree discarded",
                                   detail=f"{ref} · {run.branch} removed", level="warn",
                                   project_id=run.project_id)
        return run

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

        message = f"Merge {run.ref}: {(run.requirement or run.ref)[:80]}\n\nNeuroCode {run.branch}"
        try:
            result = await asyncio.to_thread(agent.merge_into_checkout, Path(run.repo), run.branch, message)
        except agent.Refused as refused:                    # a dirty tree: the reason is for the person
            raise Refused(str(refused)) from refused
        if result["merged"]:
            run.merged = {"into": result["into"], "commit": result["commit"], "at": utcnow().isoformat(),
                          "by": by, "undo": result["undo"]}
            await self.logs.write(run.id, level="ok",
                                  line=f"merged into {result['into']} as {result['commit']} · undo: {result['undo']}")
            await self.activity.record(actor=by, actor_kind="human", action="Merged",
                                       detail=f"{ref} · {run.branch} → {result['into']} as {result['commit']} · "
                                              f"undo: {result['undo']}", level="ok", project_id=run.project_id)
        else:
            await self.activity.record(actor=by, actor_kind="human", action="Merge collided",
                                       detail=f"{ref} · {len(result['conflicts'])} files collide with "
                                              f"{result['into']}; nothing was merged", level="warn",
                                       project_id=run.project_id)
        await self.session.flush()
        return result

    async def diff(self, ref: str) -> dict[str, Any]:
        run = await self.runs.by_ref(ref)
        if run is None:
            raise NotFound(f"run {ref}")
        stat = {"files": run.diff_files, "insertions": run.diff_insertions,
                "deletions": run.diff_deletions, "commits": run.diff_commits}
        tree = Path(run.worktree)
        if run.removed or not await asyncio.to_thread(tree.exists):
            return {"patch": "", "truncated": False, "stat": stat, "gone": True}
        patch = await asyncio.to_thread(agent.diff, tree, run.base)
        return {"patch": patch[:MAX_DIFF], "truncated": len(patch) > MAX_DIFF, "stat": stat, "gone": False}


def agent_free_branch(repo: Path, wanted: str) -> str:
    return agent.free_branch(repo, wanted)


# ── working the steps, in the background ─────────────────────────
async def _log(db: Database, run_id: str, level: str, line: str, step: int | None = None) -> None:
    async with db.session() as s:
        await RunLogRepository(s).write(run_id, level=level, line=line, step=step)


async def _finish(db: Database, ref: str, status: str, note: str) -> None:
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None:
            return
        run.status, run.finished_at, run.waiting_on = status, utcnow(), None
        if note:
            run.note = note
        level = "ok" if status == "done" else "warn" if status == "cancelled" else "err"
        await RunLogRepository(s).write(run.id, level=level, line=f"run {status}")
        await ActivityRepository(s).record(
            actor="Orchestrator", actor_kind="agent", action=f"Run {status}",
            detail=f"{ref} · {run.diff_files} files +{run.diff_insertions} −{run.diff_deletions} on "
                   f"{run.branch}" + (f" · {note}" if note else ""),
            level=level, project_id=run.project_id)
    _STOPPED.pop(ref, None)


async def _context(session: AsyncSession, run: Run, step: RunStep, work: Path) -> list[tuple[str, str]]:
    """The files this step may change: what the plan named, plus what the index finds for its words."""
    wanted: list[str] = list(run.targets or [])
    words = func.plainto_tsquery("simple", step.label)
    rows = (await session.execute(
        select(CodeFile.path).join(CodeSymbol, CodeSymbol.file_id == CodeFile.id)
        .where(CodeSymbol.project_id == run.project_id, CodeSymbol.search.op("@@")(words))
        .distinct().limit(6))).scalars()
    wanted += list(rows)

    refused: list[str] = []

    def read() -> list[tuple[str, str]]:
        files: list[tuple[str, str]] = []
        seen: set[str] = set()
        total = 0
        for rel in wanted:
            if rel in seen or len(files) >= 8:
                continue
            seen.add(rel)
            # These paths come from the compiler — a model wrote them — so they are checked exactly
            # the way a write is. Reading is not the harmless half: whatever is read here is sent to
            # a provider, so "../../../.ssh/id_rsa" would be an exfiltration, not a bad diff.
            try:
                f = work / agent.safe_path(rel)
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
            files.append((rel, text))
        return files

    found = await asyncio.to_thread(read)
    if refused:
        # Said out loud rather than dropped: a plan that names a path outside the worktree is worth
        # seeing in the run's log, whether it was a hallucination or something worse.
        await RunLogRepository(session).write(
            run.id, level="warn", step=step.n,
            line=f"ignored {len(refused)} path(s) outside the worktree: {', '.join(refused[:5])}")
    return found


async def _pause(db: Database, ref: str, step_n: int, *, title: str, tool: str, risk: str, payload: str,
                 reason: str) -> None:
    """Stop and wait for a person. The run is not failed — it is waiting, and it says what for."""
    async with db.session() as s:
        runs, approvals = RunRepository(s), ApprovalRepository(s)
        run = await runs.by_ref(ref)
        if run is None:
            return
        step = next((x for x in run.steps if x.n == step_n), None)
        approval_ref = await approvals.next_ref()
        s.add(Approval(id=f"ap-{run.ref.lower()}-{step_n}", ref=approval_ref, title=title,
                       agent=step.agent if step else "", tool=tool, risk=risk, status="pending",
                       project_id=run.project_id, payload=payload, reason=reason,
                       run_ref=run.ref, step=step_n))
        if step is not None:
            step.status, step.detail = "waiting", f"Waiting for you · {approval_ref}"
        run.status, run.waiting_on = "waiting", approval_ref
        await RunLogRepository(s).write(run.id, level="warn", step=step_n,
                                        line=f"waiting for your decision · {approval_ref} · {title}")
        await ActivityRepository(s).record(actor=step.agent if step else "Orchestrator", actor_kind="agent",
                                           action="Approval needed", detail=f"{approval_ref} · {title}",
                                           level="warn", project_id=run.project_id)


async def _edit(db: Database, gateway: Gateway, ref: str, step_n: int) -> bool:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        work = _work(run)
        files = await _context(s, run, step, work)
        prompt = [
            {"role": "system", "content": EDIT_SYSTEM},
            {"role": "user", "content": f"You are the {step.agent}.\nProject: {run.project_id}\n"
                                        f"Requirement: {run.requirement}\nStep {step.n}: {step.label}\n"
                                        f"{step.detail}\n\nFiles you may change:\n"
                                        + ("\n\n".join(f"--- {rel}\n{text}" for rel, text in files)
                                           or "(no file matched; create what the step needs)")},
        ]
        lane, run_id, base = run.lane, run.id, run.base
        project_id, label, worktree = run.project_id, step.label, run.worktree
        who = step.agent or run.agent or ""

    try:
        result = await asyncio.to_thread(
            gateway.ask, prompt, lambda raw: EditOut.model_validate(extract_json(raw, trim=False)),
            feature="agent", project=project_id, role=WRITE, lane=lane, agent=who)
    except NoModel as e:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status = "skipped"
            step.detail = "Needs a model. NeuroCode will not pretend to write code it cannot write."
            await RunLogRepository(s).write(run_id, level="warn", step=step_n, line=str(e))
        return False

    try:
        written = await asyncio.to_thread(agent.apply_files, work,
                                          [(f.path, f.content) for f in result.data.files])
    except agent.Refused as refused:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "failed", str(refused)
            await RunLogRepository(s).write(run_id, level="err", step=step_n, line=str(refused))
        return False

    committed = False
    if written:
        message = f"{label}\n\n{result.data.summary.strip()[:500]}\n\nNeuroCode {ref}"
        committed = await asyncio.to_thread(agent.commit, work, message)
    stat = await asyncio.to_thread(agent.stats, Path(worktree), base) if committed else None

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        run.model = result.provider.model
        logs = RunLogRepository(s)
        await logs.write(run.id, level="tool", step=step_n,
                         line=f"{result.provider.model} read {len(files)} files and answered in "
                              f"{result.ms / 1000:.1f}s")
        for note in result.data.notes[:5]:
            await logs.write(run.id, level="info", step=step_n, line=str(note)[:300])
        for path in written:
            await logs.write(run.id, level="tool", step=step_n, line=f"wrote {path}")
        if stat:
            run.diff_files, run.diff_insertions = stat["files"], stat["insertions"]
            run.diff_deletions, run.diff_commits = stat["deletions"], stat["commits"]
            await logs.write(run.id, level="ok", step=step_n,
                             line=f"committed · {stat['files']} files +{stat['insertions']} −{stat['deletions']}")
        step.detail = (result.data.summary or "The model proposed no change for this step.")[:300]
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

    worktree, base, into, branch, child_status, child_agent = details
    if child_status not in ("done", "failed", "cancelled"):
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "skipped", f"{child_agent} is still working; nothing was merged."
        return False

    ok, conflicts = await asyncio.to_thread(agent.merge_branch, Path(worktree), branch, base, into)
    stat = await asyncio.to_thread(agent.stats, Path(worktree), base)

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
    lines: list[str] = []

    def keep(i: int, text: str) -> None:
        if i < TEST_LINES:
            lines.append(text)

    code, tail = await asyncio.to_thread(agent.run_tests, argv, work, keep, stopped(ref))

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        logs = RunLogRepository(s)
        for text in lines:
            await logs.write(run.id, level="tool", step=step_n, line=text)
        passed = code == 0
        run.tests_status = "passed" if passed else "failed"
        run.tests_summary = " · ".join(t for t in tail if t.strip())[:300] or f"exit code {code}"
        step.status = "done" if passed else "failed"
        step.detail = run.tests_summary
        await logs.write(run.id, level="ok" if passed else "err", step=step_n,
                         line=f"tests {'passed' if passed else f'failed (exit {code})'}")
    return False


def _rule_review(diff: str) -> tuple[list[dict[str, Any]], str, str]:
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
    return findings, "Checked by rules only — a model would read the diff properly.", "offline rules"


async def _review(db: Database, gateway: Gateway, ref: str, step_n: int) -> bool:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        tree, base, requirement, lane, project_id = Path(run.worktree), run.base, run.requirement, run.lane, run.project_id
        reviewer = next((x.agent for x in run.steps if x.n == step_n), "") or "Code Reviewer"

    diff = (await asyncio.to_thread(agent.diff, tree, base))[:MAX_DIFF]
    if not diff.strip():
        async with db.session() as s:
            step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == step_n)
            step.status, step.detail = "skipped", "Nothing changed, so there is nothing to review."
        return False

    prompt = [{"role": "system", "content": REVIEW_SYSTEM},
              {"role": "user", "content": f"Requirement: {requirement}\n\nDiff:\n{diff}"}]
    try:
        # A second opinion is worth more from a different model, and free lanes make that free.
        result = await asyncio.to_thread(
            gateway.ask, prompt, lambda raw: ReviewOut.model_validate(extract_json(raw, trim=False)),
            feature="review", project=project_id, role=REVIEW, avoid=lane, agent=reviewer)
        findings = [f.model_dump() for f in result.data.findings][:20]
        verdict, by = result.data.verdict[:300], result.provider.model
    except NoModel:
        findings, verdict, by = _rule_review(diff)
    except Exception as e:                                   # a bad answer must not lose the review
        findings, verdict, by = _rule_review(diff)
        verdict = f"{verdict} The model failed: {type(e).__name__}."

    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        step = next(x for x in run.steps if x.n == step_n)
        run.review = {"findings": findings, "verdict": verdict, "by": by}
        step.detail = verdict or f"{len(findings)} findings"
        high = sum(1 for f in findings if f["severity"] == "HIGH")
        await RunLogRepository(s).write(run.id, level="warn" if high else "ok", step=step_n,
                                        line=f"{len(findings)} findings ({high} high) · reviewed by {by}")
    return False


async def _handoff(db: Database, ref: str, step_n: int) -> bool:
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        if run.diff_files == 0:
            nothing = True
        else:
            nothing = False
            high = [f for f in (run.review or {}).get("findings", []) if f.get("severity") == "HIGH"]
            failed = run.tests_status == "failed"
            conflicts = list(run.conflicts)
            lines = [f"branch {run.branch} from {run.base[:7]}",
                     f"{run.diff_files} files · +{run.diff_insertions} −{run.diff_deletions} · "
                     f"{run.diff_commits} commits",
                     f"tests {run.tests_status}" + (f" · {run.tests_summary}" if run.tests_summary else ""),
                     f"review by {(run.review or {}).get('by') or 'nobody'}: "
                     f"{(run.review or {}).get('verdict') or '—'}"]
            if conflicts:
                lines.append("collisions: " + " · ".join(
                    f"{c.agent or c.branch} in {', '.join((c.files or [])[:3])}" for c in conflicts))
            title = f"Accept {run.ref}: {run.diff_files} files on {run.branch}"
            tool, risk = f"Merge({run.branch})", "HIGH" if (high or failed or conflicts) else "MEDIUM"
            payload, branch = "\n".join(lines), run.branch

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


async def execute(db: Database, gateway: Gateway, ref: str, resume_from: int | None = None) -> None:
    """Work the steps in order, one transaction each. Returns when the run finishes or starts waiting."""
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None or run.status in ("done", "failed", "cancelled"):
            return
        if resume_from is None:
            stopped(ref).clear()
            try:
                await asyncio.to_thread(agent.open_worktree, Path(run.repo), run.branch, run.base,
                                        Path(run.worktree))
            except Exception as e:
                run.status, run.finished_at, run.note = "failed", utcnow(), str(e)[:200]
                await RunLogRepository(s).write(run.id, level="err", line=str(e)[:300])
                return
            await RunLogRepository(s).write(run.id, level="ok",
                                            line=f"worktree ready · {run.branch} from {run.base[:7]}")
            await ActivityRepository(s).record(
                actor="Orchestrator", actor_kind="agent", action="Run started",
                detail=f"{ref} · {len(run.steps)} steps on {run.branch}", project_id=run.project_id)
        run.status = "running"
        run_id = run.id
        plan = [(x.n, x.kind) for x in run.steps]

    for n, kind in plan:
        if resume_from is not None and n < resume_from:
            continue
        async with db.read() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next((x for x in run.steps if x.n == n), None)
            if step is None or step.status in ("done", "skipped", "failed"):
                continue
        if stopped(ref).is_set():
            await _finish(db, ref, "cancelled", "Stopped by you.")
            return

        async with db.session() as s:
            step = next(x for x in (await RunRepository(s).by_ref(ref)).steps if x.n == n)
            step.status = "running"
        started = time.monotonic()
        try:
            if kind == "edit":
                paused = await _edit(db, gateway, ref, n)
            elif kind == "merge":
                paused = await _merge_step(db, ref, n)
            elif kind == "test":
                paused = await _test(db, ref, n)
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

    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        verdict = "failed" if any(x.status == "failed" for x in run.steps) else "done"
        note = run.note
    await _finish(db, ref, verdict, note)


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
            actor="Orchestrator", actor_kind="agent", action="Agents started",
            detail=f"{run.ref} · {len(children)} agents, a worktree each", project_id=run.project_id)

    await asyncio.gather(*(execute(db, gateway, child) for child in refs), return_exceptions=True)
    await execute(db, gateway, ref)


async def resume(db: Database, gateway: Gateway, ref: str, step_n: int, approved: bool) -> None:
    """Called when a person decides on an approval a run was waiting for."""
    async with db.session() as s:
        runs = RunRepository(s)
        run = await runs.by_ref(ref)
        if run is None or run.status != "waiting":
            return
        step = next((x for x in run.steps if x.n == step_n), None)
        if step is None:
            return
        kind, branch, project_id, run_id = step.kind, run.branch, run.project_id, run.id
        run.waiting_on = None

        if kind == "test":
            key = f"runtime.tests.{project_id}"
            setting = await s.get(Setting, key)
            value = "allowed" if approved else "refused"
            if setting is None:
                s.add(Setting(key=key, value=value))
            else:
                setting.value = value
            step.status = "todo" if approved else "skipped"
            if not approved:
                step.detail = "You chose not to run tests in this project."
            await RunLogRepository(s).write(run_id, level="ok" if approved else "warn", step=step_n,
                                            line="you allowed this project's tests to run" if approved
                                            else "tests refused")

    if kind == "test":
        await execute(db, gateway, ref, resume_from=step_n if approved else step_n + 1)
        return

    if approved:
        async with db.session() as s:
            run = await RunRepository(s).by_ref(ref)
            step = next(x for x in run.steps if x.n == step_n)
            step.status, step.detail = "done", "Accepted by you."
            await RunLogRepository(s).write(run.id, level="ok", step=step_n,
                                            line=f"accepted · merge it with: git merge {branch}")
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
        repo, tree, branch_name = Path(run.repo), Path(run.worktree), run.branch
    await asyncio.to_thread(agent.cleanup, repo, tree, branch_name)
    async with db.session() as s:
        run = await RunRepository(s).by_ref(ref)
        run.removed = True
    await _finish(db, ref, "cancelled", "You refused the changes; the branch and its worktree were removed.")
