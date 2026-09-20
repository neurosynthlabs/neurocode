"""Rows to the documents the screens already read.

Two kinds of field live here. Most are a straight copy. The interesting ones are the fields the old
store *kept* but should never have: a project's open-task counts, which drifted whenever a task moved
in a way nobody remembered to count, and its size as a label. Those are derived now, from the numbers
the database holds — so they cannot disagree with reality.
"""
from __future__ import annotations

import difflib
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ..models import ActivityEvent, Approval, Decision, Plan, PlanComment, Pref, Project, ProjectSource, Setting, Task

SIZES = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
#: Which task statuses the project card counts as "running", "in review" and "blocked".
RUNNING = ("in_progress",)
#: What the plan service keeps beside the lane in `plans.compiler`, sent as fields of their own.
PLAN_CHECKS = ("criteria", "fileCheck", "revisions")


def fmt_lines(n: int) -> str:
    """412_000 → "412K". The screens show a size, not a number."""
    for size, suffix in SIZES:
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + suffix
    return str(n or 0)


def when(value: datetime | None) -> str | None:
    """One format for every time this API hands out: ISO 8601, with its timezone said out loud."""
    return value.isoformat(timespec="seconds") if value else None


def task_json(task: Task) -> dict[str, Any]:
    return {
        "id": task.id, "ref": task.ref, "title": task.title, "projectId": task.project_id,
        "status": task.status, "priority": task.priority, "risk": task.risk, "epic": task.epic,
        "layers": task.layers or [], "agents": [a.agent for a in task.assignees],
        "files": task.files, "tests": task.tests, "progress": task.progress,
        "createdAt": when(task.created_at), "updatedAt": when(task.updated_at),
        "requirement": task.requirement, "worktree": task.worktree or None,
        "checklist": [{"id": i.id, "label": i.label, "done": i.done} for i in task.checklist],
        "blockedReason": task.blocked_reason or None,
    }


def plan_json(plan: Plan, *, task_ref: str | None = None, run_ref: str | None = None) -> dict[str, Any]:
    """Questions are rows now, so the three lists the screens read are rebuilt from their state.

    `fileCheck` is what checking the named files against the code index found — `{checked, newFiles,
    ambiguous, readOnly?}` — or null for a plan nobody checked (a workflow's, or one compiled before the
    check). `criteriaEdited` is true once the acceptance criteria are no longer the ones the compiler
    proposed. `revision` counts the revisions written from comments; `revisions` keeps each earlier one's
    steps, the comments it answered with the compiler's replies, and what changed into the next
    (`step_changes`). `updatedAt` is to the microsecond, so a screen can tell which of two copies is newer."""
    compiled = plan.compiler or {}
    current = [{"n": s.n, "label": s.label, "agent": s.agent, "detail": s.detail} for s in plan.steps]
    kept = list(compiled.get("revisions") or [])
    revisions = [{**r, "changes": step_changes(r.get("steps") or [],
                                               kept[i + 1].get("steps") or [] if i + 1 < len(kept) else current)}
                 for i, r in enumerate(kept)]
    criteria = list(plan.acceptance_criteria or [])
    answered = [{"q": q.question, "a": q.answer} for q in plan.questions if q.answer]
    deferred = [q.question for q in plan.questions if q.deferred]
    open_questions = [q.question for q in plan.questions if not q.answer and not q.deferred]
    return {
        "id": plan.id, "ref": plan.ref, "taskRef": task_ref or (plan.task.ref if plan.task else None),
        "projectId": plan.project_id, "rawRequirement": plan.raw_requirement,
        "businessRequirement": plan.business_requirement,
        "technicalRequirement": plan.technical_requirement,
        "affectedModules": plan.affected_modules or [], "affectedFiles": plan.affected_files or [],
        "affectedDb": plan.affected_db or [], "architectureImpact": plan.architecture_impact,
        "risk": plan.risk, "confidence": plan.confidence, "createdAt": when(plan.created_at),
        "steps": [{"id": s.id, "n": s.n, "label": s.label, "agent": s.agent, "state": s.state,
                   "detail": s.detail, **({"durationS": s.duration_s} if s.duration_s else {})}
                  for s in plan.steps],
        "testPlan": plan.test_plan or [], "openQuestions": open_questions, "status": plan.status,
        "answered": answered, "deferred": deferred, "cited": plan.cited or [], "grounding": plan.grounding or [],
        "compiler": {k: v for k, v in compiled.items() if k not in PLAN_CHECKS} or None, "requestedBy": plan.requested_by, "workflowId": plan.workflow_id,
        "acceptanceCriteria": criteria, "criteriaEdited": criteria != list(compiled.get("criteria") or []),
        "fileCheck": compiled.get("fileCheck"),
        "revision": plan.revision or 1, "stepGate": bool(plan.step_gate), "revisions": revisions,
        "updatedAt": plan.updated_at.isoformat() if plan.updated_at else None,
        **({"runRef": run_ref} if run_ref else {}),
    }


def comment_json(comment: PlanComment, plan: Plan, *, by: str | None = None) -> dict[str, Any]:
    """A comment on a plan. `step` is the step it is on as the plan stands — null for one on the whole
    plan, or once a revision replaced its step. `reply` is what the compiler answered when a revision took
    it up, from the revision it was handed to."""
    step = next((s for s in plan.steps if s.id == comment.step_id), None) if comment.step_id else None
    handed = next((c for r in (plan.compiler or {}).get("revisions") or [] for c in r.get("comments") or []
                   if c.get("id") == comment.id), None)
    return {"id": comment.id, "planRef": plan.ref, "stepId": step.id if step else None,
            "step": {"n": step.n, "label": step.label} if step else (handed or {}).get("step"),
            "kind": comment.kind, "body": comment.body, "revision": comment.revision,
            "resolved": comment.resolved, "by": by, "createdAt": when(comment.created_at),
            "reply": (handed or {}).get("reply") or None}


def first_source_status(project: Project) -> str:
    """The first source's state, read off the project: it is onboarded with the project itself."""
    if project.status == "onboarding":
        return "onboarding"
    return "failed" if project.description.startswith("Onboarding stopped") else "active"


def source_summary(project: Project, sources: list[ProjectSource]) -> list[dict[str, Any]]:
    """What the project picker shows of each source, first source first: `id` null is the first."""
    first = ([{"id": None, "label": project.id, "kind": project.source_kind, "status": first_source_status(project),
               "role": "code"}] if project.source_kind else [])
    return first + [{"id": x.id, "label": x.label, "kind": x.kind, "status": x.status, "role": x.role}
                    for x in sources]


def source_json(project: Project, source: ProjectSource | None, *, root: str | None = None,
                show_root: bool = False) -> dict[str, Any]:
    """One source as the Sources section and the Workbench read it. `source` None is the first source,
    built from the project's own columns. `root` is where it is on this machine — sent only to someone
    who may browse the machine (`show_root`), and null when it is not on this machine."""
    if source is None:
        doc: dict[str, Any] = {
            "id": None, "label": project.id, "kind": project.source_kind, "repo": project.source_repo,
            "branch": project.source_branch, "position": 0, "status": first_source_status(project),
            "note": "", "primary": True, "createdAt": when(project.created_at), "role": "code"}
    else:
        doc = {"id": source.id, "label": source.label, "kind": source.kind, "repo": source.repo,
               "branch": source.branch, "position": source.position, "status": source.status,
               "note": source.note, "primary": False, "createdAt": when(source.created_at), "role": source.role}
    if show_root:
        doc["root"] = root
    return doc


def project_json(project: Project, *, tasks: dict[str, int] | None = None,
                 index: dict[str, Any] | None = None,
                 sources: list[ProjectSource] | None = None,
                 references: list[str] | None = None) -> dict[str, Any]:
    """`work` and `lines` are computed, not stored: a count that is kept is a count that drifts.
    `sources` is the project's further sources; given, the document carries every source in order
    (`sources: [{id, label, kind, status, role}]`, the first with id null). `references` is the ids of
    the projects it reads from, in the order they were added.

    `restricted` says the project is closed to everyone but the people listed on it. It is never what
    hides a project — a document only reaches someone who may already see it — it is what lets the
    card and the Access tab say so out loud, rather than leaving a person to wonder why a colleague
    cannot find the project they were just sent."""
    counts = tasks or {}
    return {
        "id": project.id, "name": project.name, "codename": project.codename,
        "stack": project.stack or [], "kind": project.kind, "status": project.status,
        "understoodPct": project.understood_pct,
        "lines": fmt_lines(project.lines_count), "modules": project.modules,
        "dbTables": project.db_tables, "storedProcs": project.stored_procs, "repo": project.repo,
        "lastActive": when(project.last_active_at), "coverage": project.coverage or [],
        "work": {"tasks": sum(counts.values()), "running": sum(counts.get(s, 0) for s in RUNNING),
                 "review": counts.get("review", 0), "blocked": counts.get("blocked", 0)},
        "description": project.description, "restricted": project.restricted,
        **({"source": {"kind": project.source_kind, "repo": project.source_repo,
                       **({"branch": project.source_branch} if project.source_branch else {})}}
           if project.source_kind else {}),
        "rules": project.rules or [], "languages": project.languages or [],
        "files": project.files_count, "excluded": project.excluded or [],
        **({"codeIndex": index} if index else {}),
        **({"sources": source_summary(project, sources)} if sources is not None else {}),
        **({"references": references} if references is not None else {}),
    }


#: What a gate is, read from the tool it names. The runtime writes the tool — `Bash(npm test)` for a
#: project's first test run, `Command(npm test)` when a tool rule asks about a command, `Edit(3 files)` when
#: one asks about files an agent wants to write, `Ask(Backend Engineer)` when an agent stops to ask a
#: question, `Step(4)` when a plan pauses before each step, and `Merge(branch)` for the signature — and
#: the inbox and the decision read the kind back from it, so the three can never disagree.
GATE_KINDS = {"Bash": "tests", "Command": "command", "Edit": "edit", "Ask": "question", "Step": "step",
              "Merge": "signature"}
#: The answers each kind of gate takes. `once`, `run` and `project` are "Allow once", "Allow for this
#: run" and "Always allow in this project" (the last needs rules:manage, which the decision checks);
#: `answer` is an agent's question answered in words.
GATE_OPTIONS: dict[str, tuple[str, ...]] = {
    "tests": ("approve", "deny"), "signature": ("approve", "deny"), "step": ("approve", "deny"),
    "command": ("once", "run", "project", "deny"), "edit": ("once", "run", "project", "deny"),
    "question": ("answer", "deny"), "other": ("approve", "deny"),
}


def gate_kind(tool: str) -> str:
    """`Command(npm test)` → "command". A tool no runtime gate writes is "other": approve or deny."""
    return GATE_KINDS.get(tool.split("(", 1)[0].strip(), "other")


def approval_json(approval: Approval) -> dict[str, Any]:
    kind = gate_kind(approval.tool)
    return {
        "id": approval.id, "ref": approval.ref, "title": approval.title, "agent": approval.agent,
        "tool": approval.tool, "risk": approval.risk, "requestedAt": when(approval.created_at),
        "projectId": approval.project_id, "payload": approval.payload, "reason": approval.reason,
        "status": approval.status, "kind": kind, "options": list(GATE_OPTIONS[kind]),
        **({"decidedAt": when(approval.decided_at)} if approval.decided_at else {}),
        **({"decidedBy": approval.decided_by} if approval.decided_by else {}),
        **({"runRef": approval.run_ref, "step": approval.step} if approval.run_ref else {}),
    }


def activity_json(event: ActivityEvent) -> dict[str, Any]:
    return {"id": str(event.seq), "t": event.at.strftime("%H:%M:%S"),
            # `t` is the clock time the log prints; `at` is the moment, to sort and to say "3 days ago".
            "at": event.at.isoformat(), "actor": event.actor,
            "actorKind": event.actor_kind, "action": event.action, "detail": event.detail,
            "projectId": event.project_id, "level": event.level,
            **({"taskRef": event.task_ref} if event.task_ref else {})}


def test_rule_json(project: Project, setting: Setting, *, answer: str, command: str | None,
                   decided_by: str | None) -> dict[str, Any]:
    """A project's standing answer to running its tests. `command` is what the project would run now,
    null when none is found on this machine; `decidedBy` is null when no person's decision wrote it."""
    return {"projectId": project.id, "projectName": project.name, "rule": "tests", "command": command,
            "answer": answer, "decidedAt": when(setting.updated_at), "decidedBy": decided_by}


def decision_json(decision: Decision, *, by: str | None = None) -> dict[str, Any]:
    """`decidedBy` is the name of the person who decided — what the screen shows and what it writes
    itself before the server's copy arrives — looked up by the caller (`DecisionRepository.names`).
    It is absent when no person decided, or the account is gone. The id is not sent: nothing reads it."""
    return {"id": decision.id, "value": decision.verdict, "decidedAt": when(decision.created_at),
            **({"decidedBy": by} if by else {}), "subject": decision.subject, "note": decision.note}


def pref_json(pref: Pref) -> dict[str, Any]:
    return {"id": pref.id, "value": pref.value}


def step_changes(before: Sequence[dict[str, Any]], after: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """What changed between two revisions' steps, in the new order, with the steps that went last.

    Steps are matched by their label, as a reader would: the same label is the same step — `same`, or
    `changed` when its owner or detail moved, or `moved` when only its place did. A label that differs
    inside a run of replaced steps is the same step `changed`, paired in order; what is left over is
    `added` or `removed`. Each item: `{op, n, was, label, agent, detail, before?}` — `n` its number now
    (null when removed), `was` its number before (null when added)."""
    keys_a = [" ".join(x["label"].casefold().split()) for x in before]
    keys_b = [" ".join(x["label"].casefold().split()) for x in after]
    out: list[dict[str, Any]] = []
    gone: list[dict[str, Any]] = []

    def item(op: str, new: dict[str, Any] | None, old: dict[str, Any] | None) -> dict[str, Any]:
        shown = new or old or {}
        doc = {"op": op, "n": new["n"] if new else None, "was": old["n"] if old else None,
               "label": shown.get("label", ""), "agent": shown.get("agent", ""), "detail": shown.get("detail", "")}
        if op == "changed" and old is not None:
            doc["before"] = {k: old.get(k) for k in ("label", "agent", "detail")}
        return doc

    matcher = difflib.SequenceMatcher(a=keys_a, b=keys_b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for old, new in zip(before[i1:i2], after[j1:j2], strict=True):
                same = old["agent"] == new["agent"] and old["detail"] == new["detail"]
                op = ("same" if old["n"] == new["n"] else "moved") if same else "changed"
                out.append(item(op, new, old))
            continue
        olds, news = list(before[i1:i2]), list(after[j1:j2])
        paired = min(len(olds), len(news)) if tag == "replace" else 0
        for old, new in zip(olds[:paired], news[:paired], strict=True):
            out.append(item("changed", new, old))
        out += [item("added", new, None) for new in news[paired:]]
        gone += [item("removed", None, old) for old in olds[paired:]]
    # A step that went and came back further down under the same label is one step that moved.
    for removed in list(gone):
        twin = next((x for x in out if x["op"] == "added" and x["label"].casefold() == removed["label"].casefold()), None)
        if twin is not None:
            gone.remove(removed)
            same = twin["agent"] == removed["agent"] and twin["detail"] == removed["detail"]
            twin.update({"op": "moved" if same else "changed", "was": removed["was"]})
            if not same:
                twin["before"] = {k: removed[k] for k in ("label", "agent", "detail")}
    return out + gone
