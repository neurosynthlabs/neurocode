"""Runs and sessions, in the shape the Live Runs and Sessions screens read.

A run's children arrive as an argument rather than through a relationship, on purpose: the caller has
already fetched them in one query for the whole list, and touching an unloaded relationship inside
async code raises rather than loading.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..models import Chat, ChatMessage, Run, RunLog, RunStep
from .work import when


def run_step_json(step: RunStep) -> dict[str, Any]:
    return {"n": step.n, "kind": step.kind, "label": step.label, "agent": step.agent,
            "status": step.status, "detail": step.detail, "ms": step.ms,
            **({"child": step.child_run_id} if step.child_run_id else {})}


def run_log_json(line: RunLog) -> dict[str, Any]:
    return {"id": line.id, "at": when(line.at), "step": line.step, "level": line.level,
            "line": line.line}


def run_json(run: Run, *, project_name: str = "", children: Sequence[Run] = (),
             task_ref: str | None = None, plan_ref: str | None = None) -> dict[str, Any]:
    return {
        "id": run.id, "ref": run.ref, "projectId": run.project_id, "projectName": project_name,
        "taskRef": task_ref, "planRef": plan_ref, "requirement": run.requirement,
        "status": run.status, "branch": run.branch, "worktree": run.worktree, "repo": run.repo,
        "prefix": run.prefix, "base": run.base, "shortBase": run.base[:7],
        "startedAt": when(run.created_at), "finishedAt": when(run.finished_at),
        "requestedBy": run.requested_by, "targets": run.targets or [],
        "steps": [run_step_json(s) for s in run.steps],
        # The totals are null when the runner's output was in no shape the parser knows.
        "tests": {"command": run.tests_command or None, "argv": None, "status": run.tests_status,
                  "summary": run.tests_summary, "passed": run.tests_passed, "failed": run.tests_failed,
                  "skipped": run.tests_skipped, "total": run.tests_total, "sha": run.tests_sha,
                  "runner": run.tests_runner},
        "review": run.review or {"findings": [], "verdict": "", "by": ""},
        "diff": {"files": run.diff_files, "insertions": run.diff_insertions,
                 "deletions": run.diff_deletions, "commits": run.diff_commits},
        "model": run.model, "lane": run.lane, "note": run.note, "removed": run.removed,
        "role": run.role, "agent": run.agent, "group": task_ref or plan_ref or run.ref,
        "parent": run.parent_id, "children": [c.ref for c in children],
        "conflicts": [{"branch": c.branch, "agent": c.agent, "files": c.files or []}
                      for c in run.conflicts],
        "merged": run.merged,
        **({"waitingOn": run.waiting_on} if run.waiting_on else {}),
    }


def chat_message_json(message: ChatMessage) -> dict[str, Any]:
    """One turn. The fields a tool call carries are only there when it *is* a tool call."""
    out: dict[str, Any] = {"id": message.id, "at": when(message.at), "role": message.role,
                           "text": message.body}
    for key, value in (("by", message.by), ("model", message.model), ("lane", message.lane),
                       ("ms", message.ms), ("tool", message.tool), ("why", message.why),
                       ("detail", message.detail)):
        if value:
            out[key] = value
    if message.tool:
        out["arguments"] = message.arguments or {}
        out["ok"] = message.ok
    return out


def chat_json(chat: Chat, *, project_name: str = "") -> dict[str, Any]:
    return {
        "id": chat.id, "ref": chat.ref, "projectId": chat.project_id, "projectName": project_name,
        "title": chat.title, "status": chat.status, "startedAt": when(chat.created_at),
        "lastAt": when(chat.last_at), "startedBy": chat.started_by, "turns": chat.turns,
        "toolCalls": chat.tool_calls, "model": chat.model, "lane": chat.lane, "note": chat.note,
    }
