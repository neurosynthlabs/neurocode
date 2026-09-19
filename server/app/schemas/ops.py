"""The DevOps screen's shapes: this machine's services, its checks, what was delivered, and its logs.

Each shape is what really exists on this machine — a health check, a pipeline stage, a log line, a
container. There is no production environment to card, so a service is a process on this machine;
nothing records a rollback, so a delivery can be discarded but never "rolled back".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from ..models import Run, RunStep
from .work import when

Status = Literal["ok", "warn", "down"]
#: A run_step status as a pipeline stage names it.
STAGE = {"done": "pass", "failed": "fail", "running": "running", "todo": "queued", "skipped": "skipped",
         "waiting": "waiting"}


def fact(k: str, v: Any) -> dict[str, str]:
    return {"k": k, "v": str(v)}


def check(id: str, name: str, target: str, status: Status, note: str, *, ms: int | None = None,
          at: datetime) -> dict[str, Any]:
    return {"id": id, "name": name, "target": target, "status": status,
            "latencyMs": ms, "lastRun": when(at), "note": note}


def delivery_status(run: Run) -> str:
    if run.merged:
        return "success"
    if run.removed:
        return "discarded"
    return {"failed": "failed", "cancelled": "cancelled", "queued": "running", "running": "running",
            "waiting": "awaiting_approval", "done": "ready"}.get(run.status, run.status)


def delivery_json(run: Run, project_name: str) -> dict[str, Any]:
    merged = run.merged or {}
    finished = run.finished_at
    return {
        "id": run.ref, "project": project_name, "branch": run.branch, "status": delivery_status(run),
        "by": merged.get("by") or run.requested_by,
        "at": merged.get("at") or when(finished or run.created_at),
        "durationS": round((finished - run.created_at).total_seconds()) if finished else 0,
        "commit": merged.get("commit") or run.base[:7],
        "note": merged.get("undo") or run.note or run.tests_summary or (run.review or {}).get("verdict") or "",
        "projectId": run.project_id, "role": run.role,
    }


def stage_json(step: RunStep) -> dict[str, Any]:
    return {"id": str(step.id), "name": step.label, "state": STAGE.get(step.status, step.status),
            "durationS": round((step.ms or 0) / 1000), "detail": step.detail, "kind": step.kind}
