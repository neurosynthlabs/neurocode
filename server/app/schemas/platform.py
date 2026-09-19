"""MCP servers, in the shape the tools screens read."""
from __future__ import annotations

from typing import Any

from ..models import McpServer
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
