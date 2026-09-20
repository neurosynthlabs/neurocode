"""Projects with several sources: a web app and its API held as one project, and worked on as one.

Two halves. The routes are driven over HTTP inside a rolled-back transaction, with the slow jobs
recorded rather than run. The pipeline itself — onboarding a further source, indexing the whole project
under its labels, searching across it, and a run that opens a worktree in each source it touches — runs
for real against throwaway git repositories and the test database, with nothing mocked but the model.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import Provider, Result
from app.api import deps, routes_platform
from app.api.app import create_api
from app.data.engine import Database
from app.models import (
    Approval,
    Chunk,
    CodeFile,
    Plan,
    PlanStep,
    Project,
    ProjectSource,
    Run,
    Setting,
)
from app.repositories import ApprovalRepository, ProjectRepository, RunRepository
from app.services import code, instructions
from app.services.code import CodeService, Source, locate, resolve_in, roots, split
from app.services.errors import Refused
from app.services.onboarding import SourceService, SourceSpec, Spec, onboard, onboard_source
from app.services.retrieval import RetrievalService
from app.services.runs import RunService, check_key, execute, resume
from app.settings import settings
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ADMIN = {"email": "admin@example.com", "name": "Asha", "password": "another long passphrase", "roles": ["admin"]}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    run_git(["init", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-m", "first"], root)
    return root


WEB = {"pkg/core.py": "def total(x):\n    return x\n",
       "web/panel.ts": "export function panel() {\n  return fetch('/api/charge');\n}\n",
       "README.md": "# Shop web\nThe storefront.\n"}
API = {"app/main.py": "def charge(amount):\n    return amount\n",
       "app/billing.py": "from app.main import charge\n\n\ndef invoice(x):\n    return charge(x)\n",
       "AGENTS.md": "# API rules\nEvery handler validates its input.\n",
       "docs/tax.md": "# Tax\nThe API charges tax on every invoice.\n",
       "Makefile": f"test:\n\t{sys.executable} -c \"print('api tests ok')\"\n"}


# ── the routes ───────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(session: AsyncSession):
    await load_workspace(session)
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: Any) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest_asyncio.fixture
async def shop(session: AsyncSession, tmp_path: Path, monkeypatch) -> dict[str, Any]:
    """A project whose first source is on disk, and the jobs the routes would start, recorded."""
    web = make_repo(tmp_path / "web", WEB)
    api_dir = make_repo(tmp_path / "api", API)
    session.add(Project(id="shop", name="Shop", source_kind="local", source_repo=str(web), status="active"))
    await session.flush()
    jobs: list[tuple[str, tuple[Any, ...]]] = []

    async def onboarded(_db, _gw, pid, sid):
        jobs.append(("onboard", (pid, sid)))

    async def reread(_db, _gw, pid):
        jobs.append(("reread", (pid,)))

    monkeypatch.setattr(routes_platform, "onboard_source", onboarded)
    monkeypatch.setattr(routes_platform, "reread", reread)
    return {"web": web, "api": api_dir, "jobs": jobs}


async def test_a_project_lists_its_first_source_and_adds_another(client: AsyncClient, shop: dict[str, Any]):
    first = (await client.get("/projects/shop/sources")).json()
    assert first == [{"id": None, "label": "shop", "kind": "local", "repo": str(shop["web"]), "branch": "",
                      "position": 0, "status": "active", "note": "", "primary": True,
                      "createdAt": first[0]["createdAt"], "role": "code", "root": str(shop["web"])}]

    made = await client.post("/projects/shop/sources", json={"label": "api", "kind": "local",
                                                            "repo": str(shop["api"])})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["label"] == "api" and body["status"] == "onboarding" and body["primary"] is False
    assert body["root"] == str(shop["api"]) and body["position"] == 1
    assert shop["jobs"] == [("onboard", ("shop", body["id"]))]       # onboarded after the answer

    listed = (await client.get("/projects/shop/sources")).json()
    assert [x["label"] for x in listed] == ["shop", "api"]
    card = (await client.get("/projects/shop")).json()
    assert card["sources"] == [{"id": None, "label": "shop", "kind": "local", "status": "active", "role": "code"},
                               {"id": body["id"], "label": "api", "kind": "local", "status": "onboarding",
                                "role": "code"}]
    assert next(p for p in (await client.get("/projects")).json() if p["id"] == "shop")["sources"] == card["sources"]
    feed = (await client.get("/activity", params={"project": "shop"})).json()
    assert any(e["action"] == "Source added" and "api" in e["detail"] for e in feed)


async def test_a_label_must_be_a_free_folder_name(client: AsyncClient, shop: dict[str, Any]):
    add = lambda **kw: client.post("/projects/shop/sources", json={"kind": "local", "repo": str(shop["api"]), **kw})
    assert (await add(label="Api Server")).status_code == 422               # not a folder name
    assert (await add(label="shop")).status_code == 409                     # the first source's own name
    clash = await add(label="pkg")                                          # a folder at the first source's top
    assert clash.status_code == 409 and "pkg" in clash.json()["detail"]
    assert (await add(label="api")).status_code == 201
    again = await client.post("/projects/shop/sources", json={"label": "api", "kind": "local",
                                                               "repo": str(shop["api"])})
    assert again.status_code == 409 and "already has a source called api" in again.json()["detail"]
    inside = await add(label="inner", repo=str(shop["web"] / "web"))       # would read the same files twice
    assert inside.status_code == 409
    missing = await add(label="gone", repo=str(shop["api"] / "nowhere"))
    assert missing.status_code == 422 and "not a folder" in missing.json()["detail"]
    url = await client.post("/projects/shop/sources", json={"label": "ml", "kind": "git", "repo": "not a url"})
    assert url.status_code == 422


async def test_rename_move_and_remove_keep_the_files(client: AsyncClient, session: AsyncSession,
                                                     shop: dict[str, Any]):
    one = (await client.post("/projects/shop/sources", json={"label": "api", "kind": "local",
                                                             "repo": str(shop["api"])})).json()
    other = make_repo(shop["api"].parent / "ml", {"train.py": "def fit():\n    return 1\n"})
    two = (await client.post("/projects/shop/sources", json={"label": "ml", "kind": "local",
                                                             "repo": str(other)})).json()
    for sid in (one["id"], two["id"]):
        (await session.get(ProjectSource, sid)).status = "active"
    session.add(CodeFile(project_id="shop", path="api/app/main.py", lang="Python", module="api/app"))
    session.add(CodeFile(project_id="shop", path="pkg/core.py", lang="Python", module="pkg"))
    session.add(Chunk(project_id="shop", kind="code", ref="api/app/main.py#charge:1", path="api/app/main.py",
                      body="def charge"))
    session.add(Setting(key=check_key("shop", "api tests"), value="allowed"))
    session.add(Setting(key=check_key("shop", "apiary tests"), value="allowed"))
    await session.flush()
    shop["jobs"].clear()

    moved = await client.patch(f"/projects/shop/sources/{two['id']}", json={"position": 0})
    assert moved.status_code == 200 and moved.json()["position"] == 1
    assert [x["label"] for x in (await client.get("/projects/shop/sources")).json()] == ["shop", "ml", "api"]
    assert shop["jobs"] == []                                # a move reads nothing again

    renamed = await client.patch(f"/projects/shop/sources/{one['id']}", json={"label": "server"})
    assert renamed.status_code == 200 and renamed.json()["label"] == "server"
    assert shop["jobs"] == [("reread", ("shop",))]           # its files are named under the new label
    paths = set((await session.execute(select(CodeFile.path).where(CodeFile.project_id == "shop"))).scalars())
    assert paths == {"pkg/core.py"}                          # the old label's rows went; the first's stayed
    # A repository added later as `api` must be asked about its own commands, not inherit an "allowed".
    assert await session.get(Setting, check_key("shop", "api tests")) is None
    assert await session.get(Setting, check_key("shop", "apiary tests")) is not None
    assert (await client.patch(f"/projects/shop/sources/{one['id']}", json={})).status_code == 422

    gone = await client.delete(f"/projects/shop/sources/{two['id']}")
    assert gone.status_code == 200 and gone.json()["label"] == "ml"
    assert (other / "train.py").is_file()                    # never deletes a file
    assert [x["label"] for x in (await client.get("/projects/shop/sources")).json()] == ["shop", "server"]
    assert (await client.delete(f"/projects/shop/sources/{two['id']}")).status_code == 404
    assert (await client.post(f"/projects/shop/sources/{one['id']}/reindex")).json() == {"ok": True,
                                                                                         "onboarding": False}


async def test_sources_need_their_permissions(api: Any, client: AsyncClient, shop: dict[str, Any]):
    made = (await client.post("/projects/shop/sources", json={"label": "api", "kind": "local",
                                                              "repo": str(shop["api"])})).json()
    await client.post("/admin/users", json=ADMIN)
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        listed = (await admin.get("/projects/shop/sources")).json()
        assert all("root" not in x for x in listed)          # only machine:access holders see where it is
        assert (await admin.patch(f"/projects/shop/sources/{made['id']}", json={"position": 0})).status_code == 200
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/projects/shop/sources")).status_code == 200
        for answer in (await viewer.post("/projects/shop/sources", json={"label": "ml", "kind": "local",
                                                                          "repo": str(shop["api"])}),
                       await viewer.patch(f"/projects/shop/sources/{made['id']}", json={"label": "x"}),
                       await viewer.delete(f"/projects/shop/sources/{made['id']}"),
                       await viewer.post(f"/projects/shop/sources/{made['id']}/reindex")):
            assert answer.status_code == 403 and "projects:onboard" in answer.json()["detail"]
    async with _client(api) as stranger:
        assert (await stranger.get("/projects/shop/sources")).status_code == 401
    assert (await client.get("/projects/nope/sources")).status_code == 404


async def test_the_root_is_not_said_when_machine_access_is_off(client: AsyncClient, shop: dict[str, Any],
                                                               monkeypatch):
    monkeypatch.setattr(settings(), "machine_access", False)
    assert all("root" not in x for x in (await client.get("/projects/shop/sources")).json())


# ── one project path, the right checkout ─────────────────────────
def test_a_path_goes_to_the_source_its_label_names(tmp_path: Path):
    web, api_dir = tmp_path / "web", tmp_path / "api"
    (web / "src").mkdir(parents=True)
    (api_dir / "app").mkdir(parents=True)
    (api_dir / "app" / "main.py").write_text("x = 1\n")
    (tmp_path / "secret.txt").write_text("no")
    (api_dir / "leak").symlink_to(tmp_path / "secret.txt")
    sources = [Source("shop", web, "local", True), Source("api", api_dir, "local", False, 1),
               Source("ml", tmp_path / "ml", "git", False, 2, ready=False)]

    assert split(sources, "api/app/main.py") == (sources[1], "app/main.py")
    assert split(sources, "src/app.ts") == (sources[0], "src/app.ts")
    assert split(sources, "ml/train.py") is None                       # still being cloned
    assert resolve_in(sources, "api/app/main.py") == api_dir / "app" / "main.py"
    assert resolve_in(sources, "src/app.ts") == web / "src" / "app.ts"
    for escape in ("api/../../secret.txt", "../secret.txt", "/etc/passwd", "api/.git/config", "api/leak"):
        with pytest.raises(Refused):
            resolve_in(sources, escape)


# ── the pipeline, for real ───────────────────────────────────────
class FakeGateway:
    """Answers with a script and remembers what it was asked. No provider is ever called."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.asked: list[str] = []
        self.prompts: list[str] = []

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq"] * n

    def embed_lane(self) -> None:
        return None

    def chain(self, role: str | None = None, limit: int = 20) -> list[Any]:
        return []

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Result[Any]:
        self.asked.append(kw.get("feature", ""))
        self.prompts.append("\n".join(m["content"] for m in messages))
        raw = self.script.pop(0) if self.script else '{"summary": "nothing", "files": []}'
        return Result(parse(raw), Provider("groq", "openai/gpt-oss-120b"), 20)


PID = "shop-live"


@pytest_asyncio.fixture
async def live(schema: str, tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """A web app onboarded as a project, and an API added to it as a second source — both read, measured
    and indexed by the real jobs."""
    web = make_repo(tmp_path / "web", WEB)
    api_dir = make_repo(tmp_path / "api", API)
    db = Database(url=schema)
    gateway = FakeGateway()
    await _forget(db)                    # a run that was interrupted must not leave this test a project
    async with db.session() as s:
        s.add(Project(id=PID, name="Shop Live", source_kind="local", source_repo=str(web), status="onboarding"))
    await onboard(db, gateway, PID, Spec(source="local", repo=str(web)))
    async with db.session() as s:
        added = await SourceService(s).add(PID, SourceSpec(label="api", kind="local", repo=str(api_dir)), "Rajat")
        sid = added.id
    await onboard_source(db, gateway, PID, sid)
    yield {"db": db, "web": web, "api": api_dir, "sid": sid, "gateway": gateway}
    await _forget(db)
    await db.close()


async def _forget(db: Database) -> None:
    async with db.session() as s:
        for run in (await s.execute(select(Run).where(Run.project_id == PID))).scalars().unique():
            shutil.rmtree(Path(run.worktree), ignore_errors=True)
            for part in (run.review or {}).get("sources") or []:
                shutil.rmtree(Path(part["worktree"]), ignore_errors=True)
        await s.execute(delete(Approval).where(Approval.project_id == PID))
        await s.execute(delete(Run).where(Run.project_id == PID))
        await s.execute(delete(Plan).where(Plan.project_id == PID))
        await s.execute(delete(Setting).where(Setting.key.like(f"runtime.%.{PID}%")))
        await s.execute(delete(Project).where(Project.id == PID))


async def test_a_second_source_is_indexed_under_its_label(live: dict[str, Any]):
    async with live["db"].read() as s:
        files = {f.path: f for f in (await s.execute(select(CodeFile).where(CodeFile.project_id == PID))).scalars()}
        project = await ProjectRepository(s).get(PID)
        source = await s.get(ProjectSource, live["sid"])

    assert source.status == "active" and source.note == ""
    assert {"pkg/core.py", "web/panel.ts", "api/app/main.py", "api/app/billing.py"} <= set(files)
    assert files["api/app/billing.py"].module == "api/app"            # a module of the API, not of the web app
    assert files["pkg/core.py"].module == "pkg"                       # the first source's paths are unchanged
    assert project.files_count == 4 and project.understood_pct == 100  # measured across both sources
    assert {x["name"] for x in project.languages} >= {"Python", "TypeScript"}
    assert project.status == "active"


async def test_search_impact_and_retrieval_span_the_sources(live: dict[str, Any]):
    async with live["db"].session() as s:
        service = CodeService(s)
        found = await service.search(PID, "charge")
        assert any(x["path"] == "api/app/main.py" and x["name"] == "charge" for x in found)
        hit = await service.impact(PID, path="api/app/main.py")
        assert "api/app/billing.py" in hit["blastRadius"][0]["items"]    # the edge stayed inside the API
        shown = await service.file(PID, "api/app/main.py", source=True)
        assert shown["text"] == API["app/main.py"]                        # read from the API's checkout

        pieces = await RetrievalService(s, live["gateway"]).search(PID, "charge tax invoice", limit=10)
        assert any(x["path"].startswith("api/") for x in pieces)
        docs = await service.docs(PID, live["gateway"])
        paths = {d["ref"] for d in docs["docs"]}
        assert {"README.md", "api/docs/tax.md", "api/AGENTS.md"} <= paths
        assert all(d["indexed"] for d in docs["docs"] if d["ref"] in {"README.md", "api/docs/tax.md"})

        project = await ProjectRepository(s).get(PID)
        where = await locate(s, project, "api/app/main.py")
        assert where == live["api"] / "app" / "main.py"
        told = await instructions.for_project(project, ["api/app/main.py"])
        assert [f["path"] for f in told.files] == ["api/AGENTS.md"]
        assert "=== api/AGENTS.md ===" in told.text


async def test_removing_a_source_takes_it_out_of_the_index_only(live: dict[str, Any]):
    async with live["db"].session() as s:
        await SourceService(s).remove(PID, live["sid"], "Rajat")
    async with live["db"].read() as s:
        paths = set((await s.execute(select(CodeFile.path).where(CodeFile.project_id == PID))).scalars())
        chunks = set((await s.execute(select(Chunk.path).where(Chunk.project_id == PID))).scalars())
        project = await ProjectRepository(s).get(PID)
        assert [x.label for x in await roots(s, project)] == [PID]
    assert not any(p.startswith("api/") for p in paths) and "pkg/core.py" in paths
    assert not any(p.startswith("api/") for p in chunks)
    assert (live["api"] / "app" / "main.py").is_file()                  # the folder is untouched


# ── a run across two sources ─────────────────────────────────────
async def _plan(db: Database, ref: str, files: list[str]) -> str:
    async with db.session() as s:
        s.add(Plan(id=f"p-{ref.lower()}", ref=ref, project_id=PID, status="draft",
                   raw_requirement="Round the totals and the charges", affected_files=files))
        await s.flush()
        s.add(PlanStep(id=f"p-{ref.lower()}-1", plan_id=f"p-{ref.lower()}", n=1, label="Round both",
                       agent="Backend Engineer"))
    async with db.session() as s:
        project = await ProjectRepository(s).get(PID)
        plan = (await s.execute(select(Plan).where(Plan.ref == ref))).scalar_one()
        made = await RunService(s, FakeGateway()).plan_runs(plan, None, project, "Rajat")
        return made[-1].ref


WEB_NEW = "def total(x):\n    return round(x, 2)\n"
API_NEW = "def charge(amount):\n    return round(amount, 2)\n"


async def test_a_run_opens_a_worktree_in_each_source_it_touches(live: dict[str, Any]):
    db = live["db"]
    async with db.session() as s:
        s.add(Setting(key=check_key(PID, "api tests"), value="allowed"))   # the API's own tests, allowed
    gateway = FakeGateway(
        json.dumps({"summary": "Rounded both.", "files": [{"path": "pkg/core.py", "content": WEB_NEW},
                                                          {"path": "api/app/main.py", "content": API_NEW}]}),
        json.dumps({"findings": [], "verdict": "Both read fine."}))
    ref = await _plan(db, "PLAN-9301", ["pkg/core.py", "api/app/main.py"])
    await execute(db, gateway, ref)

    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
        steps = {x.kind: x for x in run.steps}
        gate = await ApprovalRepository(s).waiting_on_person(ref)
    parts = run.review["sources"]
    assert [x["label"] for x in parts] == ["", "api"]
    web_tree, api_tree = Path(parts[0]["worktree"]), Path(parts[1]["worktree"])
    assert (web_tree / "pkg" / "core.py").read_text() == WEB_NEW
    assert (api_tree / "app" / "main.py").read_text() == API_NEW
    # the checkouts are untouched: the change lives on each source's own branch
    assert (live["web"] / "pkg" / "core.py").read_text() == WEB["pkg/core.py"]
    assert (live["api"] / "app" / "main.py").read_text() == API["app/main.py"]

    assert steps["edit"].status == "done" and run.diff_files == 2 and run.diff_commits == 2
    check = next(c for c in run.review["checks"] if c["name"] == "api tests")
    assert check["status"] == "passed" and check["label"] == "api"      # ran in the API's worktree
    assert run.tests_status == "passed"
    review_prompt = gateway.prompts[-1]
    assert "+++ b/pkg/core.py" in review_prompt and "+++ b/api/app/main.py" in review_prompt
    assert run.status == "waiting" and gate is not None and "api: branch" in gate.payload

    async with db.read() as s:
        patch = (await RunService(s, gateway).diff(ref))["patch"]
    assert "a/api/app/main.py" in patch and "a/pkg/core.py" in patch

    await resume(db, gateway, ref, gate.step, approved=True)
    async with db.session() as s:
        merged = await RunService(s, gateway).merge(ref, "Rajat")
    assert merged["merged"] is True and [x["label"] for x in merged["sources"]] == ["", "api"]
    assert (live["web"] / "pkg" / "core.py").read_text() == WEB_NEW
    assert (live["api"] / "app" / "main.py").read_text() == API_NEW
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
    assert len(run.merged["sources"]) == 2 and all(x["undo"].startswith("git reset --hard") for x in run.merged["sources"])


async def test_a_run_never_writes_into_a_source_it_did_not_open(live: dict[str, Any]):
    db = live["db"]
    gateway = FakeGateway(
        json.dumps({"summary": "…", "files": [{"path": "api/app/main.py", "content": API_NEW}]}),
        json.dumps({"findings": [], "verdict": "Fine."}))
    ref = await _plan(db, "PLAN-9302", ["pkg/core.py"])                   # the web app only
    await execute(db, gateway, ref)
    async with db.read() as s:
        run = await RunRepository(s).by_ref(ref)
    assert "sources" not in run.review and run.review["elsewhere"] == ["api"]
    edit = next(x for x in run.steps if x.kind == "edit")
    assert edit.status == "failed" and "api is a source this run did not open" in edit.detail
    assert not (Path(run.worktree) / "api").exists()
    assert (live["api"] / "app" / "main.py").read_text() == API["app/main.py"]


async def test_a_collision_in_one_source_merges_none_of_them(live: dict[str, Any]):
    db = live["db"]
    async with db.session() as s:
        s.add(Setting(key=check_key(PID, "api tests"), value="refused"))
    gateway = FakeGateway(
        json.dumps({"summary": "Both.", "files": [{"path": "pkg/core.py", "content": WEB_NEW},
                                                  {"path": "api/app/main.py", "content": API_NEW}]}),
        json.dumps({"findings": [], "verdict": "Fine."}))
    ref = await _plan(db, "PLAN-9303", ["pkg/core.py", "api/app/main.py"])
    await execute(db, gateway, ref)
    async with db.read() as s:
        gate = await ApprovalRepository(s).waiting_on_person(ref)
    await resume(db, gateway, ref, gate.step, approved=True)

    # Someone changes the API's checkout the other way in the meantime.
    (live["api"] / "app" / "main.py").write_text("def charge(amount):\n    return int(amount)\n")
    run_git(["commit", "-am", "theirs"], live["api"])
    web_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=live["web"], capture_output=True, text=True).stdout

    async with db.session() as s:
        result = await RunService(s, gateway).merge(ref, "Rajat")
    assert result["merged"] is False and result["conflicts"] == ["api/app/main.py"]
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=live["web"], capture_output=True,
                          text=True).stdout == web_head                   # the web app's merge was taken back
    assert (live["web"] / "pkg" / "core.py").read_text() == WEB["pkg/core.py"]
    assert not subprocess.run(["git", "status", "--porcelain"], cwd=live["api"], capture_output=True,
                              text=True).stdout.strip()


async def test_a_project_with_one_source_runs_as_it_always_did(schema: str, tmp_path: Path):
    """No `sources` on the run, one worktree, one branch — the shape every existing run test asserts."""
    web = make_repo(tmp_path / "solo", WEB)
    db = Database(url=schema)
    try:
        async with db.session() as s:
            s.add(Project(id="solo-src", name="Solo", source_kind="local", source_repo=str(web)))
            await s.flush()
            s.add(Plan(id="p-solo-src", ref="PLAN-9310", project_id="solo-src", status="draft",
                       raw_requirement="x", affected_files=["pkg/core.py"]))
            await s.flush()
            s.add(PlanStep(id="p-solo-src-1", plan_id="p-solo-src", n=1, label="x", agent="Backend Engineer"))
        async with db.session() as s:
            project = await ProjectRepository(s).get("solo-src")
            assert [x.label for x in await roots(s, project)] == ["solo-src"]
            plan = (await s.execute(select(Plan).where(Plan.ref == "PLAN-9310"))).scalar_one()
            run = (await RunService(s, FakeGateway()).plan_runs(plan, None, project, "Rajat"))[-1]
            assert run.review == {"findings": [], "verdict": "", "by": ""}
            assert run.repo == os.path.realpath(web) and run.prefix == ""
    finally:
        async with db.session() as s:
            await s.execute(delete(Run).where(Run.project_id == "solo-src"))
            await s.execute(delete(Plan).where(Plan.project_id == "solo-src"))
            await s.execute(delete(Project).where(Project.id == "solo-src"))
        await db.close()


def test_the_checkout_helper_is_the_first_source():
    project = Project(id="p1", name="P", source_kind="local", source_repo="~/code/p1")
    assert code.checkout(project) == Path(os.path.expanduser("~/code/p1"))
    assert code.sources_from(project, [])[0].primary is True
