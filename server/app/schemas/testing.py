"""The Testing screen's shapes: suites, failures with how often they recur, history and coverage.

Counts are passed through as they are stored — null when the runner's output was not understood — so
the screen can say "exit code only" instead of drawing a pass rate nobody measured.
"""
from __future__ import annotations

from typing import Any

from ..agent.testparse import all_expected
from ..models import Project, Run, TestCoverage, TestExpectation, TestFailure
from .work import when


def counts_json(run: Run) -> dict[str, int | None]:
    return {"passed": run.tests_passed, "failed": run.tests_failed, "skipped": run.tests_skipped,
            "total": run.tests_total}


def tool_of(command: str | None) -> str:
    """The tool a detected command runs, named the way a person would name it."""
    if not command:
        return "unknown"
    words = command.split()
    if "pytest" in words:
        return "pytest"
    return words[0] if words[0] in ("make", "npm", "go", "dotnet") else "unknown"


def expectation_json(expectation: TestExpectation, by: str | None) -> dict[str, Any]:
    return {"projectId": expectation.project_id, "testName": expectation.test_name,
            "kind": expectation.kind, "reason": expectation.reason, "by": by,
            "at": when(expectation.updated_at)}


def suite_json(project: Project, *, command: str | None, allowed: str | None,
               latest: tuple[Run, int | None] | None, checking: str | None,
               recorded: int = 0, expected: int = 0) -> dict[str, Any]:
    run, ms = latest if latest else (None, None)
    return {
        "projectId": project.id, "projectName": project.name, "command": command, "tool": tool_of(command),
        "allowed": allowed if allowed in ("allowed", "refused") else None,
        "checking": checking,
        "latest": {**counts_json(run), "runRef": run.ref, "status": run.tests_status, "ms": ms,
                   "sha": run.tests_sha, "branch": run.branch, "runner": run.tests_runner,
                   # The gate's own rule, not a guess at it: the screen once called a run calm whose
                   # signature still went HIGH, because it counted expectations without the totals.
                   "allExpected": run.tests_status == "failed" and all_expected(run.tests_failed, recorded, expected),
                   # Failed, but the failures were not all named — a crash, a build error, output nobody
                   # could read. That is never zero failures, whatever the list below says.
                   "unnamed": run.tests_status == "failed" and (
                       recorded == 0 or (run.tests_failed is not None and recorded < run.tests_failed)),
                   "at": when(run.created_at)} if run else None,
    }


def failure_json(failure: TestFailure, *, run_ref: str, project_id: str, project_name: str,
                 expectation: TestExpectation | None, by: str | None,
                 recurrence: tuple[int, str] | None, window: int) -> dict[str, Any]:
    failed_in, first = recurrence if recurrence else (1, run_ref)
    return {
        "id": failure.id, "runRef": run_ref, "projectId": project_id, "projectName": project_name,
        "name": failure.name, "file": failure.file, "line": failure.line, "message": failure.message,
        "excerpt": failure.excerpt, "at": when(failure.at),
        "expectation": expectation_json(expectation, by) if expectation else None,
        "failedIn": failed_in, "ofLast": max(window, failed_in), "firstFailedRef": first,
    }


def history_json(run: Run, ms: int | None, *, project_name: str, trigger: str) -> dict[str, Any]:
    return {**counts_json(run), "runRef": run.ref, "projectId": run.project_id, "projectName": project_name,
            "role": run.role, "trigger": trigger, "branch": run.branch, "ms": ms, "sha": run.tests_sha,
            "status": run.tests_status, "runner": run.tests_runner, "at": when(run.created_at)}


def coverage_json(row: TestCoverage, *, run_ref: str, project_id: str) -> dict[str, Any]:
    return {"runRef": run_ref, "projectId": project_id, "path": row.path, "covered": row.covered,
            "total": row.total, "source": row.source}
