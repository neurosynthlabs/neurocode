"""The code family on the new stack: same paths, same JSON, the walking done in SQL.

A small index is written by hand here rather than built from a real repository, because what is under
test is the *reading* — the rollups, the tree, the backwards walk over the edges — and a hand-made
index is the only way to know in advance what the right answer is. The checkout it describes is real
though: `GET /code/file` opens those files, so they exist on disk.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.data.loader import load_seed, sync_roles
from app.models import CodeEdge, CodeFile, CodeIndexRun, CodeSymbol, Project
from app.services import code as code_jobs

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ENGINEER = {"email": "dev@example.com", "name": "Dev", "password": "another long passphrase",
            "roles": ["engineer"]}                        # an Engineer cannot onboard a project
HEADERS = {"X-NC-Client": "test"}
PID = "codelab"

#: path, language, module, lines, complexity, churn — and the source written to the checkout.
FILES = [
    ("main.py", "Python", "main", 10, 1, 0, "def main():\n    return load()\n"),
    ("app/service.py", "Python", "app", 120, 40, 12, "class TaxService:\n    def charge(self): ...\n"),
    ("app/repo.py", "Python", "app", 60, 8, 1, "def load():\n    return TaxService()\n"),
    ("web/panel.ts", "TypeScript", "web", 80, 15, 4, "export function Panel() { return null; }\n"),
    ("tests/test_service.py", "Python", "tests", 30, 2, 0, "def test_charge():\n    assert load()\n"),
    ("db/schema.sql", "T-SQL", "db", 45, 0, 0, "CREATE TABLE Invoices (id INT);\n"),
]
#: file path, name, kind, line, exported
SYMBOLS = [
    ("main.py", "main", "function", 4, True),
    ("app/service.py", "TaxService", "class", 1, True),
    ("app/service.py", "charge", "function", 5, True),
    ("app/repo.py", "load", "function", 4, True),
    ("web/panel.ts", "Panel", "function", 1, True),
    ("tests/test_service.py", "test_charge", "function", 4, False),
    ("db/schema.sql", "Invoices", "table", 1, True),
    ("db/schema.sql", "sp_Rebill", "procedure", 20, True),
]
#: from path, to path (None: outside the repository), target, kind
EDGES = [
    ("app/repo.py", "app/service.py", "app.service", "imports"),
    ("web/panel.ts", "app/service.py", "service", "uses"),
    ("tests/test_service.py", "app/repo.py", "app.repo", "imports"),
    ("main.py", "app/repo.py", "app.repo", "imports"),
    ("app/service.py", None, "Invoices", "writes"),
    ("app/service.py", None, "Invoices", "reads"),
    ("web/panel.ts", None, "react", "imports"),
]


@pytest_asyncio.fixture
async def api(session: AsyncSession) -> FastAPI:
    """The Postgres app, answering out of this test's transaction."""
    await load_seed(session)
    await sync_roles(session)
    await session.flush()
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


def _client(api: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)           # an Owner holds every permission
        yield c


@pytest_asyncio.fixture
async def indexed(client: AsyncClient, session: AsyncSession, tmp_path: Path) -> Path:
    """An onboarded project, its checkout on disk, and an index that describes it exactly."""
    session.add(Project(id=PID, name="Code Lab", source_kind="local", source_repo=str(tmp_path),
                        coverage=[{"label": "Syntax & symbols", "pct": 0},
                                  {"label": "Business rules", "pct": 40}]))
    await session.flush()

    rows: dict[str, CodeFile] = {}
    for path, lang, module, lines, complexity, churn, body in FILES:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(body)
        rows[path] = CodeFile(project_id=PID, path=path, lang=lang, module=module, lines=lines,
                              bytes=len(body), sha1="0" * 40, complexity=complexity, churn=churn,
                              changed_at=datetime(2026, 8, 1, tzinfo=UTC))
        session.add(rows[path])
    await session.flush()                                 # symbols and edges point at files by id

    session.add_all([CodeSymbol(project_id=PID, file_id=rows[path].id, name=name, kind=kind,
                                line=line, exported=exported)
                     for path, name, kind, line, exported in SYMBOLS])
    session.add_all([CodeEdge(project_id=PID, from_file=rows[a].id,
                              to_file=rows[b].id if b else None, target=target, kind=kind)
                     for a, b, target, kind in EDGES])
    session.add(CodeIndexRun(project_id=PID, root=str(tmp_path), ms=1_240, files=len(FILES),
                             symbols=len(SYMBOLS), edges=len(EDGES), unresolved=1,
                             finished_at=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
                             parsers={"Python": "python-ast", "TypeScript": "patterns"}))
    await session.flush()
    return tmp_path


async def test_the_summary_is_rolled_up_from_the_rows_not_stored(client: AsyncClient, indexed: Path):
    body = (await client.get(f"/projects/{PID}/code")).json()
    assert body["indexed"] is True and body["indexing"] is False and body["canIndex"] is True
    assert body["run"] == {"files": 6, "symbols": 8, "edges": 7, "unresolved": 1, "ms": 1_240,
                           "finishedAt": "2026-09-01T10:30:00+00:00",
                           "parsers": {"Python": "python-ast", "TypeScript": "patterns"}}

    assert body["languages"][0] == {"name": "Python", "files": 4, "lines": 220}
    app_module = next(m for m in body["modules"] if m["name"] == "app")
    assert app_module == {"name": "app", "files": 2, "lines": 180, "complexity": 48, "symbols": 3,
                          "fanIn": 3, "fanOut": 0}       # web, tests and main all reach into it

    assert [h["path"] for h in body["hotspots"]] == ["app/service.py", "app/repo.py"]
    assert body["hotspots"][0]["fanIn"] == 2 and body["hotspots"][0]["risk"] == "MEDIUM"
    assert body["database"]["objects"] == 2
    assert body["database"]["top"][0] == {"name": "Invoices", "kind": "table", "path": "db/schema.sql",
                                          "readers": 1, "writers": 1, "callers": 0}


async def test_a_project_that_was_never_indexed_answers_honestly(client: AsyncClient):
    answer = await client.get("/projects/erp/code")
    assert answer.status_code == 200
    assert answer.json() == {"indexed": False, "indexing": False, "canIndex": False}
    assert (await client.get("/projects/nope/code")).status_code == 404


async def test_the_tree_lists_one_level_and_counts_the_whole_subtree(client: AsyncClient, indexed: Path):
    root = (await client.get(f"/projects/{PID}/code/files")).json()
    assert root["dir"] == ""
    assert [d["name"] for d in root["dirs"]] == ["app", "db", "tests", "web"]
    assert next(d for d in root["dirs"] if d["name"] == "app") == {
        "name": "app", "path": "app", "files": 2, "lines": 180}
    assert [f["name"] for f in root["files"]] == ["main.py"]

    inside = (await client.get(f"/projects/{PID}/code/files", params={"dir": "app"})).json()
    assert inside["dir"] == "app" and inside["dirs"] == []
    assert [f["path"] for f in inside["files"]] == ["app/repo.py", "app/service.py"]
    assert next(f["fanIn"] for f in inside["files"] if f["path"] == "app/service.py") == 2
    assert set(inside["files"][0]) == {"id", "name", "path", "lang", "lines", "complexity", "fanIn"}


async def test_search_finds_a_symbol_by_its_words_and_a_file_by_its_path(client: AsyncClient,
                                                                        indexed: Path):
    hits = (await client.get(f"/projects/{PID}/code/search", params={"q": "tax service"})).json()
    assert {"name": "TaxService", "path": "app/service.py", "kind": "class", "line": 1} in hits

    paths = (await client.get(f"/projects/{PID}/code/search", params={"q": "panel"})).json()
    assert {"name": "panel.ts", "path": "web/panel.ts", "kind": "file", "line": 0} in paths
    assert (await client.get(f"/projects/{PID}/code/search", params={"q": "  "})).json() == []


async def test_a_file_comes_back_with_its_relations_and_its_source(client: AsyncClient, indexed: Path):
    body = (await client.get(f"/projects/{PID}/code/file",
                             params={"path": "app/service.py"})).json()
    source = (indexed / "app/service.py").read_text()
    assert body["file"] == {"path": "app/service.py", "lang": "Python", "module": "app", "lines": 120,
                            "bytes": len(source), "complexity": 40, "churn": 12,
                            "changedAt": "2026-08-01T00:00:00+00:00", "fanIn": 2, "fanOut": 0,
                            "test": False}
    assert [s["name"] for s in body["symbols"]] == ["TaxService", "charge"]
    assert body["dependsOn"] == []                        # it imports nothing inside the repository
    assert sorted(d["kind"] for d in body["database"]) == ["reads", "writes"]
    assert sorted(u["path"] for u in body["usedBy"]) == ["app/repo.py", "web/panel.ts"]
    assert next(u for u in body["usedBy"] if u["path"] == "app/repo.py")["kinds"] == ["imports"]
    assert body["impact"]["target"] == "app/service.py"

    assert body["text"] == source and body["truncated"] is False
    assert (await client.get(f"/projects/{PID}/code/file", params={"path": "app/gone.py"})
            ).status_code == 404


async def test_a_path_that_leaves_the_checkout_is_refused_not_rewritten(client: AsyncClient,
                                                                       indexed: Path):
    refused = await client.get(f"/projects/{PID}/code/file", params={"path": "../../etc/passwd"})
    assert refused.status_code == 403 and "outside this project" in refused.json()["detail"]
    assert (await client.get(f"/projects/{PID}/code/file", params={"path": ".git/config"})
            ).status_code == 403


async def test_impact_walks_the_edges_backwards_through_the_whole_repository(client: AsyncClient,
                                                                            indexed: Path):
    body = (await client.get(f"/projects/{PID}/code/impact",
                             params={"path": "app/service.py"})).json()
    assert body["kind"] == "file" and body["risk"] == "HIGH"
    assert body["counts"] == {"direct": 2, "dependents": 4, "modules": 4, "tests": 1, "data": 2}

    groups = {g["label"]: g["items"] for g in body["blastRadius"]}
    assert groups["Uses it directly"] == ["app/repo.py", "web/panel.ts"]
    # main.py and the test reach it only through app/repo.py — the recursive walk is what finds them.
    assert groups["Reached through others"] == ["main.py", "tests/test_service.py"]
    assert groups["Tests that reach it"] == ["tests/test_service.py"]
    assert any("It writes Invoices" in w for w in body["warnings"])
    assert any("could not be resolved" in w for w in body["warnings"])
    assert 40 <= body["confidence"] <= 100


async def test_impact_on_a_database_object_counts_its_users_as_direct(client: AsyncClient,
                                                                     indexed: Path):
    body = (await client.get(f"/projects/{PID}/code/impact", params={"object": "invoices"})).json()
    assert body["kind"] == "object" and body["target"] == "invoices"
    groups = {g["label"]: g["items"] for g in body["blastRadius"]}
    assert groups["Uses it directly"] == ["app/service.py"]
    assert body["counts"]["dependents"] == 5              # everything downstream of the writer, too

    assert (await client.get(f"/projects/{PID}/code/impact")).status_code == 422
    assert (await client.get(f"/projects/{PID}/code/impact", params={"path": "nope.py"})
            ).status_code == 404
    assert (await client.get(f"/projects/{PID}/code/impact", params={"object": "Nothing"})
            ).status_code == 404


async def test_the_graph_draws_the_modules_and_the_objects_they_touch(client: AsyncClient,
                                                                     indexed: Path):
    body = (await client.get(f"/projects/{PID}/code/graph")).json()
    assert body["modules"] == 5 and body["truncated"] is False
    assert {"id": "m:app", "label": "app", "kind": "module", "files": 2, "lines": 180,
            "risk": "MEDIUM"} in body["nodes"]
    assert {"id": "d:Invoices", "label": "Invoices", "kind": "table", "files": 0, "lines": 0,
            "risk": "HIGH"} in body["nodes"]
    assert {"from": "m:web", "to": "m:app", "kind": "depends", "weight": 1} in body["edges"]
    assert {"from": "m:app", "to": "d:Invoices", "kind": "writes", "weight": 1} in body["edges"]


async def test_retrieval_reports_what_it_holds_before_anything_is_built(client: AsyncClient,
                                                                       indexed: Path):
    body = (await client.get(f"/projects/{PID}/code/retrieval")).json()
    assert body == {"built": False, "chunks": 0, "byKind": {}, "semantic": False, "q": "",
                    "results": []}


async def test_reindexing_is_queued_and_answered_at_once(client: AsyncClient, indexed: Path,
                                                         monkeypatch):
    queued: list[tuple[str, str, list[str]]] = []

    async def record(_db, _gateway, project_id, root, excluded):
        queued.append((project_id, str(root), excluded))

    monkeypatch.setattr(code_jobs, "reindex", record)
    answer = await client.post(f"/projects/{PID}/code/reindex")
    assert answer.status_code == 202 and answer.json() == {"ok": True}
    assert queued == [(PID, str(indexed), [])]

    feed = (await client.get("/activity", params={"project": PID})).json()
    assert any(e["action"] == "Re-indexing" and e["actorKind"] == "human" for e in feed)


async def test_a_project_with_no_code_on_this_machine_cannot_be_indexed(client: AsyncClient):
    refused = await client.post("/projects/erp/code/reindex")
    assert refused.status_code == 409 and "sample project" in refused.json()["detail"]


async def test_building_retrieval_is_queued_behind_the_onboarding_permission(
        api, client: AsyncClient, indexed: Path, monkeypatch):
    asked: list[str] = []

    async def record(_db, _gateway, project_id):
        asked.append(project_id)

    monkeypatch.setattr(code_jobs, "build_retrieval", record)
    assert (await client.post(f"/projects/{PID}/code/retrieval/build")).status_code == 202
    assert asked == [PID]

    await client.post("/admin/users", json=ENGINEER)
    async with _client(api) as engineer:
        await engineer.post("/auth/login", json={"email": ENGINEER["email"],
                                                 "password": ENGINEER["password"]})
        assert (await engineer.get(f"/projects/{PID}/code")).status_code == 200
        refused = await engineer.post(f"/projects/{PID}/code/retrieval/build")
        assert refused.status_code == 403 and "projects:onboard" in refused.json()["detail"]
        assert (await engineer.post(f"/projects/{PID}/code/reindex")).status_code == 403


async def test_reading_the_index_needs_a_session(api):
    async with _client(api) as stranger:
        assert (await stranger.get(f"/projects/{PID}/code")).status_code == 401
