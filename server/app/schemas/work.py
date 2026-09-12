"""Rows to the documents the screens already read.

Two kinds of field live here. Most are a straight copy. The interesting ones are the fields the old
store *kept* but should never have: a project's open-task counts, which drifted whenever a task moved
in a way nobody remembered to count, and its size as a label. Those are derived now, from the numbers
the database holds — so they cannot disagree with reality.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import ActivityEvent, Approval, Decision, Plan, Pref, Project, Task

SIZES = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
#: Which task statuses the project card counts as "running", "in review" and "blocked".
RUNNING = ("in_progress",)


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
    """Questions are rows now, so the three lists the screens read are rebuilt from their state."""
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
        "answered": answered, "deferred": deferred, "cited": plan.cited or [],
        "compiler": plan.compiler or None, "requestedBy": plan.requested_by,
        **({"runRef": run_ref} if run_ref else {}),
    }


def project_json(project: Project, *, tasks: dict[str, int] | None = None,
                 index: dict[str, Any] | None = None) -> dict[str, Any]:
    """`work` and `lines` are computed, not stored: a count that is kept is a count that drifts."""
    counts = tasks or {}
    return {
        "id": project.id, "name": project.name, "codename": project.codename,
        "stack": project.stack or [], "kind": project.kind, "status": project.status,
        "memoryPct": project.memory_pct, "understoodPct": project.understood_pct,
        "lines": fmt_lines(project.lines_count), "modules": project.modules,
        "dbTables": project.db_tables, "storedProcs": project.stored_procs, "repo": project.repo,
        "lastActive": when(project.last_active_at), "coverage": project.coverage or [],
        "work": {"tasks": sum(counts.values()), "running": sum(counts.get(s, 0) for s in RUNNING),
                 "review": counts.get("review", 0), "blocked": counts.get("blocked", 0)},
        "description": project.description,
        **({"source": {"kind": project.source_kind, "repo": project.source_repo,
                       **({"branch": project.source_branch} if project.source_branch else {})}}
           if project.source_kind else {}),
        "rules": project.rules or [], "languages": project.languages or [],
        "files": project.files_count, "excluded": project.excluded or [],
        **({"codeIndex": index} if index else {}),
    }


def approval_json(approval: Approval) -> dict[str, Any]:
    return {
        "id": approval.id, "ref": approval.ref, "title": approval.title, "agent": approval.agent,
        "tool": approval.tool, "risk": approval.risk, "requestedAt": when(approval.created_at),
        "projectId": approval.project_id, "payload": approval.payload, "reason": approval.reason,
        "status": approval.status,
        **({"decidedAt": when(approval.decided_at)} if approval.decided_at else {}),
        **({"decidedBy": approval.decided_by} if approval.decided_by else {}),
        **({"runRef": approval.run_ref, "step": approval.step} if approval.run_ref else {}),
    }


def activity_json(event: ActivityEvent) -> dict[str, Any]:
    return {"id": str(event.seq), "t": event.at.strftime("%H:%M:%S"), "actor": event.actor,
            "actorKind": event.actor_kind, "action": event.action, "detail": event.detail,
            "projectId": event.project_id, "level": event.level,
            **({"taskRef": event.task_ref} if event.task_ref else {})}


def decision_json(decision: Decision) -> dict[str, Any]:
    return {"id": decision.id, "value": decision.verdict, "decidedAt": when(decision.created_at),
            "decidedBy": decision.by_user_id, "subject": decision.subject, "note": decision.note}


def pref_json(pref: Pref) -> dict[str, Any]:
    return {"id": pref.id, "value": pref.value}
