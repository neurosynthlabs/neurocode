"""The code index, read back.

The only method that earns its own file is `for_projects`: a list of projects used to mean one index
lookup per project, and this asks once for all of them. The rest of the index — search, impact, the
graph — still belongs to the indexer, which moves in its own phase.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..models import CodeIndexRun
from .base import Repository


def _index_json(run: CodeIndexRun) -> dict[str, Any]:
    """The `codeIndex` field the project screens read. `at` changes with every index."""
    return {"files": run.files, "symbols": run.symbols, "edges": run.edges,
            "unresolved": run.unresolved, "ms": run.ms,
            "at": run.finished_at.isoformat(timespec="seconds")}


class CodeIndexRepository(Repository[CodeIndexRun]):
    model = CodeIndexRun

    async def summary(self, project_id: str) -> dict[str, Any] | None:
        run = await self.get(project_id)
        return _index_json(run) if run else None

    async def for_projects(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """project id → its last index. One statement, however many projects are on screen."""
        if not ids:
            return {}
        rows = (await self.session.execute(
            select(CodeIndexRun).where(CodeIndexRun.project_id.in_(ids)))).scalars()
        return {run.project_id: _index_json(run) for run in rows}
