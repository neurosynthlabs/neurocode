"""Workflows in the shape the Workflows screen reads.

Every number a workflow shows is handed in by the caller, already counted from runs. Nothing here
invents a default that could be read as a measurement: a workflow that has never run says `null`,
not zero agents in zero minutes.
"""
from __future__ import annotations

from typing import Any

from ..models import WorkflowDefinition
from ..repositories.workflows import WorkflowStats
from .work import when


def workflow_json(*, id: str, name: str, description: str, scope: str, project_id: str | None,
                  project_name: str | None, builtin: bool, trigger: str, phases: list[dict[str, Any]],
                  stats: WorkflowStats | None, last_result: str | None, tests: dict[str, Any],
                  referenced: bool = False) -> dict[str, Any]:
    return {
        "id": id, "name": name, "description": description, "trigger": trigger, "scope": scope,
        "projectId": project_id, "projectName": project_name, "builtin": builtin,
        "runs": stats.runs if stats else 0,
        "avgAgents": round(stats.avg_agents, 1) if stats and stats.avg_agents is not None else None,
        "avgMinutes": stats.avg_minutes if stats else None,
        "lastRun": when(stats.last_run) if stats else None,
        "lastResult": last_result, "phases": phases, "tests": tests,
        # What removing it will really do. A plan can point at a workflow without any run behind it — its
        # author could compile but not run — so a run count is the wrong thing to decide the button by.
        "referenced": referenced,
    }


def steps_json(workflow: WorkflowDefinition) -> list[dict[str, Any]]:
    return [{"n": s.n, "label": s.label, "agent": s.agent, "detail": s.detail} for s in workflow.steps]
