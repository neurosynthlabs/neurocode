"""MCP servers, in the shape the tools screens read."""
from __future__ import annotations

from typing import Any

from ..models import McpServer


def mcp_json(server: McpServer) -> dict[str, Any]:
    return {
        "id": server.id, "name": server.name, "transport": server.transport, "status": server.status,
        "scope": server.scope, "command": server.command,
        "tools": [{"name": t.name, "description": t.description, "risk": t.risk} for t in server.tools],
        "resources": server.resources, "prompts": server.prompts, "latencyMs": server.latency_ms,
        "calls24h": server.calls_24h, "errorRate": float(server.error_rate or 0),
        "untrusted": server.untrusted, "defaultEffect": server.default_effect,
        **({"config": server.config} if server.config else {}),
    }
