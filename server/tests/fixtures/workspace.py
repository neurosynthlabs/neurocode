"""The workspace the tests work against.

The product ships no sample work, so the tests bring their own: two projects with no code on this
machine, their tasks, plans, gates and activity, the facts they rest on and the conflicts between
them, and a few MCP servers. It is loaded through the same loader the SQLite importer uses, and the
timestamps in it are written out in full, so nothing about it depends on when a test runs.

Assertions about how much there is are made against this file rather than against numbers typed into
the tests, so trimming it or adding to it never quietly changes what a test proves.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.loader import load_seed, sync_agents, sync_roles

PATH = Path(__file__).resolve().parent / "workspace.json"
WORKSPACE: dict[str, Any] = json.loads(PATH.read_text())


def rows(key: str, **where: Any) -> list[dict[str, Any]]:
    """The fixture's rows of one kind, filtered on equal fields: `rows("tasks", projectId="erp")`."""
    return [row for row in WORKSPACE[key] if all(row.get(k) == v for k, v in where.items())]


async def load_workspace(session: AsyncSession) -> None:
    """The fixture and the catalogue, inside the caller's transaction."""
    await load_seed(session, WORKSPACE)
    await sync_roles(session)
    await sync_agents(session)
    await session.flush()
