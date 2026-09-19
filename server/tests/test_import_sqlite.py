"""Carrying an old SQLite workspace across, without the sample the old store seeded itself with.

The old store wrote its sample workspace into every file on every start, so a real file holds it beside
the real work. The importer names those rows and leaves them behind; everything else comes across
through the same loader the tests use.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.data.loader import load_seed

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import-sqlite.py"


def importer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_sqlite", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_file(path: Path, tables: dict[str, list[dict]]) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    for table, docs in tables.items():
        db.execute(f"CREATE TABLE {table} (seq INTEGER PRIMARY KEY, doc TEXT)")
        db.executemany(f"INSERT INTO {table}(doc) VALUES (?)", [(json.dumps(d),) for d in docs])
    db.commit()
    return db


async def test_the_sample_is_left_behind_and_the_real_work_comes_across(tmp_path: Path, session: AsyncSession):
    script = importer()
    db = old_file(tmp_path / "old.db", {
        "projects": [{"id": "erp", "name": "Legacy ERP"}, {"id": "shop", "name": "Shop"}],
        "tasks": [{"id": "t492", "ref": "TASK-492", "title": "Sample", "projectId": "erp"},
                  {"id": "t1", "ref": "TASK-1", "title": "Real", "projectId": "shop"}],
        "memory": [{"id": "m142", "ref": "MEM-142", "title": "Sample fact", "projectId": "erp"},
                   {"id": "m001", "ref": "MEM-001", "title": "Sample workspace fact", "projectId": "global"},
                   {"id": "m1", "ref": "MEM-1", "title": "Real fact", "projectId": "global"},
                   {"id": "m2", "ref": "MEM-2", "title": "Another real fact", "projectId": "shop"}],
        "conflicts": [{"id": "cf-1", "a": "m1", "b": "m2"}, {"id": "cf-9", "a": "m1", "b": "m001"},
                      {"id": "cf-10", "a": "m1", "b": "m2"}],
        "approvals": [{"id": "ap1", "ref": "APPR-118", "title": "Sample gate"},
                      {"id": "ap-real", "ref": "APPR-1", "title": "Real gate", "projectId": "shop"}],
        "mcp": [{"id": "jira", "name": "jira"}, {"id": "ledger-tools", "name": "ledger-tools"}],
        "activity": [{"actor": "Orchestrator", "action": "Sample", "projectId": "aios"},
                     {"actor": "Rajat", "action": "Real", "projectId": "shop"}],
        "decisions": [{"id": "release.1"}],
    })
    docs, skipped = script.without_sample(script.documents(db))
    db.close()

    assert skipped == {"projects": 1, "tasks": 1, "memory": 2, "conflicts": 2, "approvals": 1, "mcp": 1,
                       "activity": 1}
    assert "decisions" not in docs and "agents" not in script.DOCUMENTS    # the roster is the catalogue's

    await load_seed(session, docs)
    await session.flush()
    assert (await session.execute(select(m.Project.id))).scalars().all() == ["shop"]
    assert (await session.execute(select(m.Task.ref))).scalars().all() == ["TASK-1"]
    assert sorted((await session.execute(select(m.MemoryFact.ref))).scalars().all()) == ["MEM-1", "MEM-2"]
    assert (await session.execute(select(m.MemoryConflict.id))).scalars().all() == ["cf-10"]
    assert (await session.execute(select(m.Approval.ref))).scalars().all() == ["APPR-1"]
    assert (await session.execute(select(m.McpServer.id))).scalars().all() == ["ledger-tools"]
    assert (await session.execute(select(m.ActivityEvent.action))).scalars().all() == ["Real"]


async def test_a_file_with_no_documents_imports_nothing(tmp_path: Path, session: AsyncSession):
    """Handed nothing, the loader used to fall back to the sample file and import it as the workspace."""
    script = importer()
    db = old_file(tmp_path / "empty.db", {})
    docs, skipped = script.without_sample(script.documents(db))
    db.close()
    assert docs == {} and skipped == {}

    written = await load_seed(session, docs)
    await session.flush()
    assert set(written.values()) == {0}
    for model in (m.Project, m.Task, m.MemoryFact, m.Approval, m.McpServer, m.ActivityEvent):
        assert (await session.execute(select(model))).first() is None, model.__tablename__
