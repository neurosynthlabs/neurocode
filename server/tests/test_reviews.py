"""Reviews on demand: any diff read the way the runtime reads a run's — against real git repositories,
with nothing mocked but the model.

The routes are driven over HTTP inside the rolled-back transaction, with the background reading recorded
rather than run. The reading itself (`perform`) runs for real against committed rows and a stand-in
gateway that keeps every prompt, so what the reviewer was handed is checked, not assumed.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.agent import review as reading
from app.ai.gateway import Gateway, NoModel, Provider, Result
from app.ai.ledger import MemoryLedger
from app.api import deps, routes_agents
from app.api.app import create_api
from app.data.engine import Database
from app.repositories.reviews import CodeReviewRepository
from app.secrets import Secrets
from app.services import reviews as review_service
from tests.fixtures.lanes import PLAN, answering
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
ORIGINAL = "def total(x):\n    return x\n"
ROUNDED = "def total(x):\n    return round(x, 2)\n"
BRIEF = "# Review rules\nA money value that is not rounded is HIGH. Ignore docs/.\n"


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


def shop_repo(root: Path) -> Path:
    """main with one commit; `feature` rounds the total and adds a file; REVIEW.md on main."""
    root.mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "pkg" / "core.py").write_text(ORIGINAL)
    (root / "REVIEW.md").write_text(BRIEF)
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    run_git(["checkout", "-qb", "feature"], root)
    (root / "pkg" / "core.py").write_text(ROUNDED)
    (root / "pkg" / "tax.py").write_text("RATE = 0.2\n")
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "round\n\nNeuroCode RUN-9801"], root)
    run_git(["checkout", "-q", "main"], root)
    return root


# ── the reading, blocking ────────────────────────────────────────
def test_a_branch_diff_is_what_it_changed_since_it_left_its_base(tmp_path: Path):
    root = shop_repo(tmp_path / "shop")
    diff = reading.build(root, "branch", "main", "feature")
    assert [f["path"] for f in diff.files] == ["pkg/core.py", "pkg/tax.py"]
    assert diff.stats == {"files": 2, "insertions": 2, "deletions": 1, "commits": 1, "untracked": 0}
    assert "+    return round(x, 2)" in diff.patch
    assert reading.run_refs(root, diff.base, diff.head) == ["RUN-9801"]

    ranged = reading.build(root, "commit-range", "main", "feature")
    assert ranged.stats["files"] == 2 and ranged.head == diff.head

    (root / "pkg" / "core.py").write_text(ROUNDED)            # a change not committed, and a new file
    (root / "notes.txt").write_text("todo\n")
    tree = reading.build(root, "working-tree", "HEAD", "")
    assert {f["path"] for f in tree.files} == {"pkg/core.py", "notes.txt"} and tree.untracked == 1
    assert run_git(["status", "--porcelain"], root).count("??") == 1   # nothing was staged to read it

    brief = reading.brief(root)
    assert brief is not None and brief[1] == "REVIEW.md" and "HIGH" in brief[0]


def test_a_ref_that_is_not_one_is_refused_before_git_sees_it(tmp_path: Path):
    root = shop_repo(tmp_path / "shop")
    for bad in ("--output=/tmp/x", "main..feature", "", "a b"):
        with pytest.raises(reading.Unreviewable):
            reading.checked_ref(root, bad, "head")
    with pytest.raises(reading.Unreviewable, match="no branch, tag or commit called nope"):
        reading.checked_ref(root, "nope", "head")


# ── over HTTP ────────────────────────────────────────────────────
@pytest.fixture
def api_gateway(tmp_path: Path) -> Gateway:
    return Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))


@pytest_asyncio.fixture
async def api(session: AsyncSession, api_gateway: Gateway) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    made.dependency_overrides[deps.gateway] = lambda: api_gateway
    return made


def _client(made: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=made), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """The jobs the routes hand off, recorded instead of run."""
    jobs: list[tuple[Any, ...]] = []

    async def record(open_session: AsyncSession, background: Any, job: Any, *args: Any) -> None:
        jobs.append((job, *args))

    monkeypatch.setattr(routes_agents, "hand_off", record)
    return jobs


@pytest_asyncio.fixture
async def shop(session: AsyncSession, tmp_path: Path) -> Path:
    root = shop_repo(tmp_path / "shop")
    session.add(m.Project(id="review-shop", name="Review Shop", source_kind="local", source_repo=str(root)))
    await session.flush()
    return root


async def test_what_can_be_reviewed_is_read_from_the_checkout(client: AsyncClient, shop: Path):
    body = (await client.get("/projects/review-shop/review/targets")).json()
    assert body["git"] is True and body["current"] == "main" and body["dirty"] == 0
    assert {b["name"] for b in body["branches"]} == {"main", "feature"}
    assert next(b for b in body["branches"] if b["name"] == "main")["current"] is True
    assert body["brief"] == {"path": "REVIEW.md", "bytes": len(BRIEF.encode())}
    assert body["sources"] == [{"label": "", "name": "review-shop", "primary": True, "ready": True}]
    assert (await client.get("/projects/nope/review/targets")).status_code == 404


async def test_asking_for_a_review_writes_it_running_and_reads_it_in_the_background(
        client: AsyncClient, shop: Path, started: list[tuple[Any, ...]], session: AsyncSession):
    asked = await client.post("/projects/review-shop/review", json={"target": "branch", "head": "feature"})
    assert asked.status_code == 202
    body = asked.json()
    assert body["ref"].startswith("REV-") and body["status"] == "running"
    assert body["base"] == "main" and body["head"] == "feature"          # the checkout's branch is the base
    assert body["title"] == "feature against main" and body["requestedBy"] == "Rajat"
    assert started == [(review_service.perform, started[0][1], started[0][2], body["ref"])]

    again = await client.post("/projects/review-shop/review", json={"target": "branch", "head": "feature"})
    assert again.json()["ref"] == body["ref"] and len(started) == 1        # the same diff is read once

    listed = (await client.get("/projects/review-shop/reviews")).json()
    assert [r["ref"] for r in listed["reviews"]] == [body["ref"]] and listed["total"] == 1
    assert (await client.get(f"/reviews/{body['ref']}")).json()["status"] == "running"
    assert (await client.get("/reviews/REV-999999")).status_code == 404
    assert (await session.execute(select(m.ActivityEvent).where(m.ActivityEvent.action == "Review requested"))).first()


async def test_a_review_that_cannot_be_read_is_refused_at_once(client: AsyncClient, shop: Path,
                                                                started: list[tuple[Any, ...]]):
    same = await client.post("/projects/review-shop/review", json={"target": "branch", "head": "main"})
    assert same.status_code == 409 and "same commit" in same.json()["detail"]
    unknown = await client.post("/projects/review-shop/review", json={"target": "branch", "head": "nope"})
    assert unknown.status_code == 422 and "nope" in unknown.json()["detail"]
    option = await client.post("/projects/review-shop/review", json={"target": "commit-range", "base": "main",
                                                                     "head": "--upload-pack=touch"})
    assert option.status_code == 422
    clean = await client.post("/projects/review-shop/review", json={"target": "working-tree"})
    assert clean.status_code == 409 and "Nothing in the working tree" in clean.json()["detail"]
    assert (await client.post("/projects/review-shop/review", json={"target": "nothing"})).status_code == 422
    assert (await client.post("/projects/erp/review", json={"target": "working-tree"})).status_code == 409
    assert (await client.post("/projects/nope/review", json={"target": "working-tree"})).status_code == 404
    assert started == []


async def test_asking_for_a_review_needs_runs_run(api: FastAPI, client: AsyncClient, shop: Path,
                                                  started: list[tuple[Any, ...]]):
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/projects/review-shop/reviews")).status_code == 200
        denied = await viewer.post("/projects/review-shop/review", json={"target": "branch", "head": "feature"})
        assert denied.status_code == 403
    assert started == []


async def _done(session: AsyncSession, findings: list[dict[str, Any]]) -> str:
    session.add(m.CodeReview(id="rv-done", ref="REV-9901", project_id="review-shop", target="branch", base="main",
                             head="feature", status="done", verdict="Rounding is right; one test is missing.",
                             fingerprint="ab" * 32, findings=findings, stats={"files": 2}))
    await session.flush()
    return "REV-9901"


FOUND = [{"severity": "MEDIUM", "file": "pkg/core.py", "line": 2, "note": "No test covers the rounding."}]


async def test_findings_go_to_a_new_session_that_goes_through_them(client: AsyncClient, shop: Path,
                                                                   session: AsyncSession,
                                                                   started: list[tuple[Any, ...]]):
    ref = await _done(session, FOUND)
    made = await client.post(f"/reviews/{ref}/session")
    assert made.status_code == 201
    chat = made.json()
    assert chat["projectId"] == "review-shop" and chat["title"] == f"Findings of {ref}"
    detail = (await client.get(f"/sessions/{chat['ref']}")).json()
    question = detail["messages"][0]["text"]
    assert "[MEDIUM] pkg/core.py:2 — No test covers the rounding." in question
    assert "feature against main" in question and "abababababab" in question
    assert len(started) == 1 and started[0][3] == chat["ref"]              # the answer is handed off
    sent = (await client.get(f"/reviews/{ref}")).json()["sent"]
    assert [(s["kind"], s["ref"], s["by"]) for s in sent] == [("session", chat["ref"], "Rajat")]


async def test_findings_become_a_plan_through_the_compiler(client: AsyncClient, shop: Path, session: AsyncSession,
                                                           monkeypatch: pytest.MonkeyPatch):
    ref = await _done(session, FOUND)
    assert (await client.post(f"/reviews/{ref}/plan")).status_code == 409       # no model yet: nothing invented
    sent = answering(monkeypatch, PLAN)
    made = await client.post(f"/reviews/{ref}/plan")
    assert made.status_code == 201 and made.json()["ref"].startswith("PLAN-")
    assert "Fix what review REV-9901 found" in sent[0][1]["content"]
    assert "pkg/core.py:2" in sent[0][1]["content"]
    assert (await client.get(f"/reviews/{ref}")).json()["sent"][0]["kind"] == "plan"


async def test_a_review_with_nothing_found_or_not_done_has_nothing_to_send(client: AsyncClient, shop: Path,
                                                                           session: AsyncSession):
    ref = await _done(session, [])
    assert (await client.post(f"/reviews/{ref}/session")).status_code == 409
    review = await CodeReviewRepository(session).by_ref(ref)
    review.status = "running"
    await session.flush()
    running = await client.post(f"/reviews/{ref}/plan")
    assert running.status_code == 409 and "still being read" in running.json()["detail"]


# ── the reading, in the background ───────────────────────────────
PID = "review-live"


class FakeGateway:
    """Answers with a script, or raises; keeps every prompt and what it was told to avoid."""

    def __init__(self, *script: str, raises: Exception | None = None) -> None:
        self.script = list(script)
        self.raises = raises
        self.prompts: list[list[dict[str, str]]] = []
        self.avoided: list[str | None] = []

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, str]], parse: Any, **kw: Any) -> Result[Any]:
        self.prompts.append(messages)
        self.avoided.append(kw.get("avoid"))
        if self.raises is not None:
            raise self.raises
        return Result(parse(self.script.pop(0)), Provider("gemini", "gemini-2.5-flash"), 30)


@pytest_asyncio.fixture
async def live(schema: str, tmp_path: Path) -> AsyncIterator[tuple[Database, Path]]:
    root = shop_repo(tmp_path / "live-shop")
    db = Database(url=schema)
    await _forget(db)
    async with db.session() as s:
        s.add(m.Project(id=PID, name="Review Live", source_kind="local", source_repo=str(root)))
        await s.flush()
        s.add(m.Run(id="r-9801", ref="RUN-9801", project_id=PID, status="done", role="solo", branch="elsewhere",
                    worktree=str(tmp_path / "wt"), repo=str(root), lane="groq", requirement="Round",
                    steps=[], conflicts=[]))
    yield db, root
    await _forget(db)
    await db.close()


async def _forget(db: Database) -> None:
    async with db.session() as s:
        await s.execute(delete(m.CodeReview).where(m.CodeReview.project_id == PID))
        await s.execute(delete(m.Run).where(m.Run.project_id == PID))
        await s.execute(delete(m.Project).where(m.Project.id == PID))


async def _asked(db: Database, **fields: Any) -> str:
    async with db.session() as s:
        s.add(m.CodeReview(id="rv-live", ref="REV-9902", project_id=PID, status="running", **fields))
    return "REV-9902"


async def _read(db: Database, ref: str) -> m.CodeReview:
    async with db.read() as s:
        found = await CodeReviewRepository(s).by_ref(ref)
        assert found is not None
        return found


async def test_the_reviewer_reads_the_branch_with_the_brief_on_another_lane(live: tuple[Database, Path]):
    db, _ = live
    ref = await _asked(db, target="branch", base="main", head="feature")
    gateway = FakeGateway(json.dumps({"findings": [
        {"severity": "high", "file": "pkg/core.py", "line": "2-3", "note": "Rounds half to even."},
        {"severity": "odd", "file": "pkg/tax.py", "note": "No test."}], "verdict": "Close, not done."}))
    await review_service.perform(db, gateway, ref)

    done = await _read(db, ref)
    assert done.verdict == "Close, not done."
    assert done.status == "done" and done.finished_at is not None
    assert done.findings == [{"severity": "HIGH", "file": "pkg/core.py", "line": 2, "note": "Rounds half to even."},
                             {"severity": "LOW", "file": "pkg/tax.py", "note": "No test."}]
    assert done.lane == "gemini" and done.model == "gemini-2.5-flash"
    assert len(done.fingerprint) == 64 and done.stats["files"] == 2 and done.stats["commits"] == 1
    assert done.stats["brief"] == {"path": "REVIEW.md", "bytes": len(BRIEF.encode())}
    # The run that wrote it is found by its commits' trailer, and its lane is the one the reviewer avoids.
    assert done.stats["writer"] == {"run": "RUN-9801", "lane": "groq"} and gateway.avoided == ["groq"]
    system, user = gateway.prompts[0][0]["content"], gateway.prompts[0][1]["content"]
    assert system.startswith("You are the reviewer inside NeuroCode") and "brief for reviewers (REVIEW.md)" in system
    assert "A money value that is not rounded is HIGH" in system
    assert user.startswith("Under review: feature against main")
    assert "+    return round(x, 2)" in user


async def test_with_no_model_rules_read_it_and_say_so(live: tuple[Database, Path]):
    db, root = live
    (root / "pkg" / "debug.py").write_text("print('here')\n")
    ref = await _asked(db, target="working-tree", base="HEAD", head="")
    await review_service.perform(db, FakeGateway(raises=NoModel("none")), ref)
    done = await _read(db, ref)
    assert done.status == "done" and done.model == "offline rules" and done.lane is None
    assert "by rules only" in done.verdict
    notes = [f["note"] for f in done.findings]
    assert any(n.startswith("Debugging output left in") for n in notes)


async def test_a_review_that_cannot_be_read_fails_in_words(live: tuple[Database, Path]):
    db, root = live
    run_git(["branch", "-D", "feature"], root)
    ref = await _asked(db, target="branch", base="main", head="feature")
    gateway = FakeGateway()
    await review_service.perform(db, gateway, ref)
    failed = await _read(db, ref)
    assert failed.status == "failed" and "no branch, tag or commit called feature" in failed.verdict
    assert gateway.prompts == []


async def test_a_pushed_branch_is_fetched_first(live: tuple[Database, Path], tmp_path: Path):
    db, root = live
    other = tmp_path / "their-clone"
    run_git(["clone", "-q", str(root), str(other)], tmp_path)
    run_git(["checkout", "-qb", "pushed"], other)
    (other / "pkg" / "core.py").write_text("def total(x):\n    return int(x)\n")
    run_git(["commit", "-qam", "truncate"], other)
    run_git(["remote", "add", "team", str(other)], root)
    ref = await _asked(db, target="branch", base="main", head="team/pushed", stats={"fetch": True})
    gateway = FakeGateway(json.dumps({"findings": [], "verdict": "Truncates instead of rounding."}))
    await review_service.perform(db, gateway, ref)
    done = await _read(db, ref)
    assert done.status == "done" and done.stats["fetched"] == "team/pushed"
    assert "+    return int(x)" in gateway.prompts[0][1]["content"]

