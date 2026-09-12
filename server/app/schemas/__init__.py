"""The shapes the screens read.

The database is normalised and typed; the frontend reads documents. This package is the seam between
them, and keeping it in one place is the point: a screen's JSON is written here once, so a column
rename cannot quietly change an API, and a field the screens still need cannot quietly disappear.

Everything the old store kept *stored* but derived — a project's task counts, its size as "412K" — is
computed here from what the database actually knows.
"""
from .knowledge import conflict_json, fact_json
from .platform import mcp_json
from .runtime import chat_json, chat_message_json, run_json, run_log_json, run_step_json
from .work import (
    activity_json,
    approval_json,
    decision_json,
    fmt_lines,
    plan_json,
    pref_json,
    project_json,
    task_json,
)

__all__ = [
    "activity_json", "approval_json", "chat_json", "chat_message_json", "conflict_json",
    "decision_json", "fact_json", "fmt_lines", "mcp_json", "plan_json", "pref_json", "project_json",
    "run_json", "run_log_json", "run_step_json", "task_json",
]
