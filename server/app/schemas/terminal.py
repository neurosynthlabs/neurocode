"""The shapes of the Workbench's terminals, run configurations and debug sessions.

A run configuration's environment goes out as its variable *names* only. People put keys there, and a
value the screen does not need is a value that cannot leak from it: editing sends a patch — a value to
set, null to remove — and a variable not named keeps what it had.
"""
from __future__ import annotations

from typing import Any

from ..models import RunConfig
from .work import when


def run_config_json(config: RunConfig, author: str | None) -> dict[str, Any]:
    return {"id": config.id, "projectId": config.project_id, "name": config.name, "kind": config.kind,
            "language": config.language, "command": config.command, "args": list(config.args or []),
            "cwd": config.cwd, "env": sorted((config.env or {}).keys()), "createdBy": author,
            "createdAt": when(config.created_at), "updatedAt": when(config.updated_at)}
