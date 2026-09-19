"""MCP servers and tool rules, in the shape the tools and permissions screens read."""
from __future__ import annotations

from typing import Any

from ..models import McpServer, ToolRule
from .work import when


def mcp_json(server: McpServer) -> dict[str, Any]:
    return {
        "id": server.id, "name": server.name, "transport": server.transport, "status": server.status,
        "scope": server.scope, "command": server.command,
        # Everything from here to `lastError` is what the last check found: the tools the server
        # listed, and nulls until a check has run.
        "tools": [{"name": t.name, "description": t.description, "risk": t.risk}
                  for t in sorted(server.tools, key=lambda t: t.name)],
        "resources": server.resources, "prompts": server.prompts, "latencyMs": server.latency_ms,
        "checkedAt": when(server.checked_at), "lastError": server.last_error,
        "untrusted": server.untrusted, "defaultEffect": server.default_effect,
        **({"config": server.config} if server.config else {}),
    }


def tool_rule_json(rule: ToolRule, *, project_name: str | None = None, author: str | None = None) -> dict[str, Any]:
    """A rule as the Permissions screen lists it. `projectId` null is the workspace's own rule."""
    return {"id": rule.id, "projectId": rule.project_id, "projectName": project_name, "tool": rule.tool,
            "pattern": rule.pattern, "action": rule.action, "note": rule.note, "createdBy": author,
            "createdAt": when(rule.created_at), "updatedAt": when(rule.updated_at)}
