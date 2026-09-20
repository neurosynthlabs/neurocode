"""Research: the routes that start, read and stop one, and the investigation that runs behind them.

The routes are tested inside the rolled-back transaction, with the background job replaced by a note
of what was handed to it — a route test that waited on a model would be testing the model. The
investigation itself runs against committed rows, because it opens sessions of its own that cannot see
another transaction's writes; it cleans up after itself. No model is ever called: the gateway is a
stand-in that either answers from a script or has no lane at all, which is the offline path for real.
"""
from __future__ import annotations

import ipaddress
import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Provider, Result
from app.api import deps
from app.api.app import create_api
from app.data.engine import Database
from app.repositories.research import ResearchRepository
from app.schemas.research import UNSEARCHABLE, UNSEARCHABLE_WITH_WEB, report_json
from app.secrets import Secrets
from app.services import mcp as mcp_service
from app.services import research as research_jobs
from app.services import web as web_service
from app.services.research import coverage_notes, investigate, split_question
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "viewer@example.com", "name": "Neha", "password": "another long passphrase",
          "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
PROJECT = "research-test-project"


# ── the routes ───────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(session: AsyncSession) -> FastAPI:
    await load_workspace(session)
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
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest.fixture
def handed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    async def record(_db: Any, _gw: Any, ref: str, _user: str | None, _web: bool = False) -> None:
        asked.append(ref)

    monkeypatch.setattr(research_jobs, "investigate", record)
    return asked


async def test_a_research_is_queued_listed_and_handed_to_the_job(client: AsyncClient, handed: list[str]):
    made = await client.post("/research", json={"question": "Where is GST calculated, and who rounds it?",
                                                "projectId": "erp", "kinds": ["code", "memory"]})
    assert made.status_code == 201
    body = made.json()
    assert body["ref"].startswith("RES-") and body["status"] == "queued"
    assert body["agents"] == 0 and body["confidence"] == 0 and body["durationS"] == 0
    assert body["projectId"] == "erp" and body["requestedBy"] == "Rajat"
    assert body["id"] not in {f"r{n}" for n in range(1, 10)}          # never a bare number
    assert handed == [body["ref"]]

    listed = (await client.get("/research", params={"project": "erp"})).json()
    assert [r["ref"] for r in listed] == [body["ref"]]
    assert (await client.get("/research", params={"project": "hims"})).json() == []

    detail = (await client.get(f"/research/{body['ref']}")).json()
    assert detail["kinds"] == ["code", "memory"] and detail["citations"] == [] and detail["gaps"] == []
    assert (await client.get("/research/RES-9999")).status_code == 404


async def test_what_cannot_be_researched_is_refused(client: AsyncClient, handed: list[str]):
    ask = {"question": "Where is GST calculated?", "projectId": "erp"}
    assert (await client.post("/research", json={**ask, "kinds": []})).status_code == 422
    assert (await client.post("/research", json={**ask, "kinds": ["web"]})).status_code == 422
    assert (await client.post("/research", json={**ask, "question": "hi"})).status_code == 422
    assert (await client.post("/research", json={**ask, "projectId": "nope"})).status_code == 404
    assert handed == []


async def test_starting_or_stopping_needs_ai_use_but_reading_does_not(api: FastAPI, client: AsyncClient,
                                                                      handed: list[str]):
    ref = (await client.post("/research", json={"question": "Where is GST calculated?",
                                                "projectId": "erp"})).json()["ref"]
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/research")).status_code == 200
        assert (await viewer.get(f"/research/{ref}")).status_code == 200
        refused = await viewer.post("/research", json={"question": "Why?", "projectId": "erp"})
        assert refused.status_code == 403 and "ai:use" in refused.json()["detail"]
        assert (await viewer.post(f"/research/{ref}/cancel")).status_code == 403
    async with _client(api) as stranger:
        assert (await stranger.get("/research")).status_code == 401
    assert handed == [ref]


async def test_a_finished_research_cannot_be_stopped(client: AsyncClient, session: AsyncSession,
                                                     handed: list[str]):
    ref = (await client.post("/research", json={"question": "Where is GST calculated?",
                                                "projectId": "erp"})).json()["ref"]
    stopping = await client.post(f"/research/{ref}/cancel")
    assert stopping.status_code == 200 and stopping.json()["status"] == "queued"
    research_jobs._STOPPED.pop(ref, None)

    report = await ResearchRepository(session).by_ref(ref)
    assert report is not None
    report.status = "done"
    await session.flush()
    assert (await client.post(f"/research/{ref}/cancel")).status_code == 409


# ── the pieces with no database ──────────────────────────────────
def test_the_offline_split_keeps_the_questions_own_clauses():
    assert split_question("Where is GST calculated and who rounds the invoice total?") == [
        "Where is GST calculated", "who rounds the invoice total"]
    assert split_question("Why is it slow?") == ["Why is it slow?"]


def test_coverage_is_decided_per_kind_not_from_the_total():
    """Memory chunks belong to every project, so a project never built still has chunks in scope."""
    never_built = {"built": False, "chunks": 40, "byKind": {"memory": 40}}
    searchable, notes, by_words = coverage_notes(["code", "doc", "memory"], never_built)
    assert searchable == ["memory"] and not by_words
    assert any("code is not indexed" in n for n in notes) and any("documentation" in n for n in notes)

    nothing = {"built": False, "chunks": 0, "byKind": {}}
    searchable, notes, by_words = coverage_notes(["memory"], nothing)
    assert searchable == [] and by_words and "own words" in notes[0]


# ── the investigation ────────────────────────────────────────────
class ScriptedGateway:
    """A model that answers from a script, per phase. Nothing leaves the machine."""

    def __init__(self, decompose: list[str], angle: dict[str, Any], synthesis: dict[str, Any]) -> None:
        self.decompose, self.angle, self.synthesis = decompose, angle, synthesis
        self.lanes_asked: list[str | None] = []

    def embed_lane(self) -> None:
        return None

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return ["groq", "cerebras", "gemini", "mistral"][:n]

    def run(self, messages: list[dict[str, str]], parse: Any, fallback: Any, **kw: Any) -> Result[Any]:
        system = messages[0]["content"]
        if "plan research" in system:
            raw = json.dumps({"questions": self.decompose})
        elif "sub-question" in system:
            self.lanes_asked.append(kw.get("lane"))
            raw = json.dumps(self.angle)
        else:
            raw = json.dumps(self.synthesis)
        return Result(parse(raw), Provider(kw.get("lane") or "groq", "openai/gpt-oss-120b"), 7)


class NoLanes:
    """No key, no local model: every call is answered by the rules, as it is on a fresh machine."""

    def embed_lane(self) -> None:
        return None

    def spread(self, n: int, role: str | None = None) -> list[str | None]:
        return [None] * n

    def run(self, messages: list[dict[str, str]], parse: Any, fallback: Any, **kw: Any) -> Result[Any]:
        return Result(fallback(), Provider("rules", kw["offline"]), 0, "No lane can answer")


@pytest_asyncio.fixture
async def live(schema: str) -> AsyncIterator[Database]:
    db = Database(url=schema)
    async with db.session() as s:
        s.add(m.Project(id=PROJECT, name="Research Test"))
    yield db
    async with db.session() as s:
        await s.execute(delete(m.ActivityEvent).where(m.ActivityEvent.project_id == PROJECT))
        await s.execute(delete(m.MemoryFact).where(m.MemoryFact.ref.like("RTMEM-%")))
        await s.execute(delete(m.Project).where(m.Project.id == PROJECT))
    await db.close()


async def _index(db: Database, *, code: int, docs: bool) -> None:
    """A built index: many code chunks about invoice tax, and one document about it."""
    async with db.session() as s:
        s.add(m.RetrievalRun(project_id=PROJECT, chunks=code + int(docs)))
        for i in range(code):
            s.add(m.Chunk(project_id=PROJECT, kind="code", ref=f"billing/tax_{i}.py#apply_gst:{i + 1}",
                          path=f"billing/tax_{i}.py", line=i + 1, title=f"apply_gst_{i} · function",
                          body=f"def apply_gst_{i}(invoice):\n    # invoice tax rounding\n    return round(invoice.tax)"))
        if docs:
            s.add(m.Chunk(project_id=PROJECT, kind="doc", ref="docs/tax.md#0", path="docs/tax.md", line=1,
                          title="docs/tax.md · Invoice tax", body="Invoice tax is rounded once, on the total."))


async def _start(db: Database, question: str, kinds: list[str]) -> str:
    async with db.session() as s:
        reports = ResearchRepository(s)
        ref = await reports.next_ref()
        await reports.add(m.ResearchReport(id=f"rt-{ref}", ref=ref, project_id=PROJECT, question=question,
                                           kinds=kinds, requested_by="Rajat"))
    return ref


async def _read(db: Database, ref: str) -> dict[str, Any]:
    async with db.read() as s:
        report = await ResearchRepository(s).by_ref(ref)
        assert report is not None
        return report_json(report)


async def test_a_documentation_research_is_not_crowded_out_by_code(live: Database):
    """Filtering after the top results let code fill every slot, and the doc angle found 'nothing'."""
    await _index(live, code=40, docs=True)
    ref = await _start(live, "How is invoice tax rounded?", ["doc"])
    await investigate(live, NoLanes(), ref, None)  # type: ignore[arg-type]

    report = await _read(live, ref)
    assert report["status"] == "complete" and report["agents"] == 1
    assert report["sweep"][0]["hits"] == 1
    assert [c["url"] for c in report["citations"]] == ["docs/tax.md#0"]
    assert report["citations"][0]["via"] == "docs" and report["sources"] == [{"kind": "docs", "count": 1}]
    assert report["confidence"] == 100
    # The rules wrote it, and nothing the rules cannot honestly write is filled in.
    assert report["provider"] == "rules" and report["model"] == "offline research"
    assert report["alternatives"] == [] and report["architecture"] == "" and report["risks"] == []
    assert UNSEARCHABLE in report["gaps"]
    async with live.read() as s:
        said = (await s.execute(select(m.ActivityEvent.action).where(
            m.ActivityEvent.project_id == PROJECT))).scalars().all()
    assert "AI fell back" in said and "Research finished" in said


async def test_citations_are_only_what_the_angle_was_handed_and_listed_once(live: Database):
    await _index(live, code=3, docs=True)
    gw = ScriptedGateway(
        decompose=["Where is invoice tax rounded?", "Invoice tax on the total"],
        angle={"finding": "Rounded once, on the total.",
               "citations": ["docs/tax.md#0", "docs/tax.md#0", "invented/file.py#nope:1"]},
        synthesis={"summary": "Tax is rounded on the total.", "recommendation": "Keep it there.",
                   "risks": ["Line-level rounding elsewhere"], "architecture": "",
                   "alternatives": [{"name": "Round per line", "pros": ["simple"], "cons": ["drift"],
                                     "verdict": "Rejected"}], "gaps": ["No test covers it"]})
    ref = await _start(live, "How is invoice tax rounded, and where is it written down?", ["code", "doc"])
    await investigate(live, gw, ref, None)  # type: ignore[arg-type]

    report = await _read(live, ref)
    assert report["status"] == "complete" and report["agents"] == 2
    # The angles run at the same time, so which reached the gateway first is up to the scheduler: what
    # matters is that they went to two different lanes, and the next line pins which angle got which.
    assert sorted(gw.lanes_asked) == ["cerebras", "groq"]
    assert [a["lane"] for a in report["angles"]] == ["groq", "cerebras"]
    assert [a["cited"] for a in report["angles"]] == [1, 1]             # the invented ref was dropped
    assert [c["url"] for c in report["citations"]] == ["docs/tax.md#0"]  # two angles, one source
    assert report["sources"] == [{"kind": "docs", "count": 1}]
    assert report["alternatives"][0]["verdict"] == "Rejected" and report["risks"]
    assert "No test covers it" in report["gaps"]
    async with live.read() as s:
        rows = (await s.execute(select(m.ResearchCitation.report_id, m.ResearchCitation.n).where(
            m.ResearchCitation.report_id == f"rt-{ref}"))).all()
    assert sorted(n for _r, n in rows) == [1, 2]


async def test_an_unindexed_project_says_so_and_still_searches_memory(live: Database):
    async with live.session() as s:
        s.add(m.MemoryFact(id="rt-mem-1", ref="RTMEM-1", category="business_rules", project_id=PROJECT,
                           title="Invoice tax rounding", body="Round invoice tax once, on the total."))
    ref = await _start(live, "How is invoice tax rounded?", ["code", "memory"])
    await investigate(live, NoLanes(), ref, None)  # type: ignore[arg-type]

    report = await _read(live, ref)
    assert report["status"] == "complete"
    assert any("code is not indexed" in g for g in report["gaps"])
    memory = [c for c in report["citations"] if c["url"] == "RTMEM-1"]
    assert memory and memory[0]["via"] == "memory"
    # Cited, so recalled — once for the research, however many angles cited it.
    async with live.read() as s:
        recalled = (await s.execute(select(m.MemoryHit.feature, m.MemoryHit.ref).where(
            m.MemoryHit.fact_id == "rt-mem-1"))).all()
    assert [tuple(r) for r in recalled] == [("research", ref)]


async def test_an_angle_nothing_bore_on_is_a_gap_not_a_finding(live: Database):
    await _index(live, code=2, docs=False)
    ref = await _start(live, "What does the payroll export do?", ["code"])
    await investigate(live, NoLanes(), ref, None)  # type: ignore[arg-type]

    report = await _read(live, ref)
    assert report["sweep"][0]["hits"] == 0 and report["citations"] == [] and report["confidence"] == 0
    assert "Nothing in code bore on: What does the payroll export do?" in report["gaps"]


async def test_a_stopped_research_says_who_stopped_it(live: Database):
    ref = await _start(live, "How is invoice tax rounded?", ["memory"])
    research_jobs._STOPPED[ref] = "Rajat"
    await investigate(live, NoLanes(), ref, None)  # type: ignore[arg-type]

    report = await _read(live, ref)
    assert report["status"] == "cancelled" and report["note"] == "Stopped by Rajat"
    assert ref not in research_jobs._STOPPED


# ── the web as a source ──────────────────────────────────────────
class Web(BaseHTTPRequestHandler):
    """A search provider in Brave's shape, and the two pages its results point to — one of them gone."""

    base = ""
    searched: list[str] = []
    read: list[str] = []

    def log_message(self, *_: Any) -> None:
        return None

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/search":
            type(self).searched.append(parse_qs(urlsplit(self.path).query)["q"][0])
            results = [{"title": "GST rounding guide", "url": f"{type(self).base}/gst",
                        "description": "Round <b>GST</b> on the invoice total."},
                       {"title": "Old post", "url": f"{type(self).base}/gone", "description": "An old post on GST."}]
            self._send(200, json.dumps({"web": {"results": results}}).encode(), "application/json")
            return
        type(self).read.append(path)
        if path == "/gst":
            self._send(200, b"<title>GST rounding, explained</title><nav>Menu</nav>"
                            b"<p>Cookie notice: this site uses cookies for many many things.</p>"
                            b"<p>Invoice tax is rounded once on the invoice total, never on each line of it.</p>",
                       "text/html")
        else:
            self._send(404, b"<p>gone</p>", "text/html")


@pytest.fixture
def the_web(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    Web.searched, Web.read = [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Web)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Web.base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(web_service, "BRAVE_URL", f"{Web.base}/search")
    # The pages are on this machine, which the fetch guard refuses; for these tests only it counts as public.
    real = mcp_service._public
    monkeypatch.setattr(mcp_service, "_public", lambda ip: ip == ipaddress.ip_address("127.0.0.1") or real(ip))
    yield Web.base
    server.shutdown()
    server.server_close()


class WebLanes(NoLanes):
    def __init__(self, tmp: Path) -> None:
        self.secrets = Secrets(tmp / "secrets.json")
        self.secrets.set(web_service.SECRET, "bsa-test-key")


class WebScripted(ScriptedGateway):
    """Answers each angle by citing the web page it was handed, and keeps what the angle was shown."""

    def __init__(self, tmp: Path, cite: str) -> None:
        super().__init__(decompose=["How is GST rounded?", "Where is GST rounding written down?"],
                         angle={"finding": "Rounded once, on the total.", "citations": [cite]},
                         synthesis={"summary": "GST is rounded on the total."})
        self.secrets = WebLanes(tmp).secrets
        self.shown: list[str] = []

    def run(self, messages: list[dict[str, str]], parse: Any, fallback: Any, **kw: Any) -> Result[Any]:
        if "answer one research sub-question" in messages[0]["content"]:
            self.shown.append(messages[1]["content"])
        return super().run(messages, parse, fallback, **kw)


async def test_a_research_with_the_web_cites_the_pages_it_read(live: Database, the_web: str, tmp_path: Path):
    ref = await _start(live, "How is GST rounded on an invoice?", ["code"])
    gw = WebScripted(tmp_path, f"{the_web}/gst")
    await investigate(live, gw, ref, None, True)  # type: ignore[arg-type]

    report = await _read(live, ref)
    assert report["status"] == "complete" and report["web"] is True
    assert Web.searched == ["How is GST rounded?", "Where is GST rounding written down?"]
    assert sorted(Web.read) == ["/gone", "/gst"]            # the top two of each angle, each page read once
    # What the angle was shown: the page, marked as the web, cut down to the paragraph that bears on it.
    assert f"[web · {the_web}/gst]" in gw.shown[0] and "rounded once on the invoice total" in gw.shown[0]
    assert "Menu" not in gw.shown[0]
    # A page that answered 404 is handed as the result's snippet, not as its error page.
    assert "An old post on GST." in gw.shown[0] and "gone" not in gw.shown[0].split(f"{the_web}/gone]")[1][:40]
    web_cites = [c for c in report["citations"] if c["via"] == "web"]
    assert [(c["url"], c["kind"], c["label"]) for c in web_cites] == [
        (f"{the_web}/gst", "web", "GST rounding, explained")]
    assert {"kind": "web", "count": 1} in report["sources"]
    assert UNSEARCHABLE_WITH_WEB in report["gaps"] and UNSEARCHABLE not in report["gaps"]
    async with live.read() as s:
        said = (await s.execute(select(m.ActivityEvent.action).where(
            m.ActivityEvent.project_id == PROJECT))).scalars().all()
    assert said.count("Web searched") == 2 and said.count("Web page fetched") == 2
    assert said.count("Web page not read") == 0                       # a 404 is a page that answered


async def test_a_rule_that_denies_the_web_is_obeyed_and_said(live: Database, the_web: str, tmp_path: Path):
    async with live.session() as s:
        s.add(m.ToolRule(project_id=PROJECT, tool="web_search", pattern="*GST*", action="deny", note="Not for GST"))
    ref = await _start(live, "How is GST rounded on an invoice?", ["code"])
    await investigate(live, WebLanes(tmp_path), ref, None, True)  # type: ignore[arg-type]

    report = await _read(live, ref)
    assert Web.searched == [] and Web.read == []
    assert report["web"] is True and [c for c in report["citations"] if c["via"] == "web"] == []
    assert any(g.startswith("The web was not searched for") and "Not for GST" in g for g in report["gaps"])


async def test_asking_for_the_web_needs_a_search_provider(client: AsyncClient, api: FastAPI, tmp_path: Path,
                                                          handed: list[str]):
    ask = {"question": "How is GST rounded?", "projectId": "erp", "web": True}
    gw = api.state.gateway
    gw.secrets = Secrets(tmp_path / "secrets.json")
    refused = await client.post("/research", json=ask)
    assert refused.status_code == 409 and "Settings → Web" in refused.json()["detail"] and handed == []
    gw.secrets.set(web_service.SECRET, "bsa-test-key")
    assert (await client.post("/research", json=ask)).status_code == 201 and len(handed) == 1
