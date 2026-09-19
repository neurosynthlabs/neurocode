"""The web: reading a page, searching through a provider, and the guard and rules in front of both.

Every page here is served by a standard-library HTTP server on a free local port, and the search
provider is one too, answering in Brave's shape. A local address is exactly what the guard refuses, so
the tests that need to reach one say so by letting loopback count as public for that test only — and
the tests of the guard itself run without that, against the same server, and prove it saw nothing.
Nothing leaves the machine, and nothing is mocked below the socket.
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.secrets import Secrets
from app.services import mcp as mcp_service
from app.services import web
from app.services.identity import IdentityService
from app.services.tool_rules import Decision
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}

PAGE = b"""<!doctype html><html><head><title>Invoice tax &amp; rounding</title>
<script>window.secret = "never-read"</script><style>body{color:red}</style></head>
<body><nav>Home | Pricing | Login</nav>
<h1>Rounding invoice tax</h1><p>Tax is rounded once, on the invoice total.</p>
<ul><li>Never per line</li><li>Half up</li></ul><footer>Copyright</footer></body></html>"""


class Site(BaseHTTPRequestHandler):
    """A small web site: a page, redirects, a large page, a slow one, a PDF, and the search provider."""

    hits: list[str] = []
    searches: list[dict[str, Any]] = []
    search_status = 200

    def log_message(self, *_: Any) -> None:
        return None

    def _send(self, status: int, body: bytes, kind: str = "text/html; charset=utf-8", **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        type(self).hits.append(path)
        if path == "/page":
            self._send(200, PAGE)
        elif path == "/moved":
            self._send(302, b"", Location="/page")
        elif path == "/to-secret":
            self._send(301, b"", Location="/secret/payroll")
        elif path == "/secret/payroll":
            self._send(200, b"<p>payroll</p>")
        elif path.startswith("/loop"):
            self._send(302, b"", Location=f"/loop{len(type(self).hits)}")
        elif path == "/large":
            self._send(200, b"x" * 4096, "text/plain")
        elif path == "/slow":
            time.sleep(1.5)
            self._send(200, b"late")
        elif path == "/doc.pdf":
            self._send(200, b"%PDF-1.4", "application/pdf")
        elif path == "/missing":
            self._send(404, b"<title>Not found</title><p>No such page.</p>")
        elif path == "/search":
            query = parse_qs(urlsplit(self.path).query)
            type(self).searches.append({"q": query.get("q", [""])[0], "count": query.get("count", [""])[0],
                                        "key": self.headers.get("X-Subscription-Token")})
            if type(self).search_status != 200:
                self._send(type(self).search_status, b"{}", "application/json")
                return
            results = {"web": {"results": [
                {"title": "Rounding <strong>tax</strong>", "url": "https://example.org/tax",
                 "description": "How <strong>invoice</strong> tax is rounded &amp; why."},
                {"title": "Not a web link", "url": "ftp://example.org/x", "description": "skipped"}]}}
            self._send(200, json.dumps(results).encode(), "application/json")
        else:
            self._send(404, b"")


@pytest.fixture
def site() -> Iterator[str]:
    Site.hits, Site.searches, Site.search_status = [], [], 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def loopback_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """For this test only, the local test server counts as the internet. The guard is still the one
    that connects — it just judges 127.0.0.1 as allowed."""
    real = mcp_service._public
    monkeypatch.setattr(mcp_service, "_public", lambda ip: ip == ipaddress.ip_address("127.0.0.1") or real(ip))


# ── reading a page ───────────────────────────────────────────────
def test_html_is_read_down_to_its_words():
    title, text = web.readable(PAGE.decode())
    assert title == "Invoice tax & rounding"
    assert "# Rounding invoice tax" in text and "Tax is rounded once, on the invoice total." in text
    assert "- Never per line" in text
    for gone in ("never-read", "color:red", "Pricing", "Copyright"):
        assert gone not in text


def test_a_local_address_is_never_fetched(site: str):
    with pytest.raises(web.FetchFailed) as refused:
        web.fetch_page(f"{site}/page")
    assert refused.value.status == 422 and "local or private address" in refused.value.reason
    assert Site.hits == []                                           # refused before a byte was sent
    for inside in ("http://169.254.169.254/latest/meta-data", "http://10.0.0.8/", "http://[::1]:9/",
                   "http://0.0.0.0:9/"):
        with pytest.raises(web.FetchFailed, match="local or private"):
            web.fetch_page(inside)


@pytest.mark.parametrize("address", ["ftp://example.org/x", "file:///etc/passwd", "https://user:pw@example.org/",
                                     "not a url", ""])
def test_what_is_not_a_web_address_is_refused(address: str):
    with pytest.raises(web.FetchFailed) as refused:
        web.fetch_page(address)
    assert refused.value.status == 422


def test_a_page_is_read_with_its_title_and_size(site: str, loopback_is_public: None):
    page = web.fetch_page(f"{site}/page")
    assert (page["status"], page["title"], page["contentType"], page["hops"]) == (200, "Invoice tax & rounding",
                                                                                  "text/html", 0)
    assert "Tax is rounded once" in page["text"] and page["bytes"] == len(PAGE) and not page["truncated"]


def test_a_redirect_is_followed_and_checked_again_at_every_hop(site: str, loopback_is_public: None):
    page = web.fetch_page(f"{site}/moved")
    assert page["hops"] == 1 and page["url"] == f"{site}/page" and page["requested"] == f"{site}/moved"

    def permit(url: str) -> Decision:
        return Decision("deny", 7, "Rule #7 denies it.") if "/secret/" in url else Decision("ask", None, "")

    with pytest.raises(web.FetchFailed) as refused:
        web.fetch_page(f"{site}/to-secret", permit)
    assert refused.value.status == 403 and "redirected" in refused.value.reason and "Rule #7" in refused.value.reason
    assert "/secret/payroll" not in Site.hits                        # denied before it was opened

    with pytest.raises(web.FetchFailed, match="redirects"):
        web.fetch_page(f"{site}/loop")
    assert len([h for h in Site.hits if h.startswith("/loop")]) == web.MAX_HOPS + 1


def test_size_time_and_kind_are_capped(site: str, loopback_is_public: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web, "MAX_BYTES", 1000)
    page = web.fetch_page(f"{site}/large")
    assert page["truncated"] and page["bytes"] == 1000 and len(page["text"]) == 1000

    with pytest.raises(web.FetchFailed) as slow:
        web.fetch_page(f"{site}/slow", timeout=0.5)
    assert slow.value.status == 504

    with pytest.raises(web.FetchFailed) as pdf:
        web.fetch_page(f"{site}/doc.pdf")
    assert pdf.value.status == 415 and "application/pdf" in pdf.value.reason

    missing = web.fetch_page(f"{site}/missing")                     # an error page is still an answer
    assert (missing["status"], missing["title"]) == (404, "Not found")


# ── searching ────────────────────────────────────────────────────
def test_a_search_answers_titles_links_and_snippets_in_plain_text(site: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web, "BRAVE_URL", f"{site}/search")
    found = web.brave_search("key-1234", "invoice tax rounding", 3)
    assert found == [{"title": "Rounding tax", "url": "https://example.org/tax",
                      "snippet": "How invoice tax is rounded & why."}]
    assert Site.searches == [{"q": "invoice tax rounding", "count": "3", "key": "key-1234"}]

    Site.search_status = 401
    with pytest.raises(web.FetchFailed, match="refused the key"):
        web.brave_search("wrong", "x")


# ── the routes ───────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(session: AsyncSession, tmp_path: Path) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)
    held = Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    made.dependency_overrides[deps.gateway] = lambda: held
    made.state.test_gateway = held
    return made


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def headers_for(session: AsyncSession, role: str) -> dict[str, str]:
    identity = IdentityService(session)
    person = await identity.create(f"{role}@example.com", role.title(), "correct horse battery", [role])
    return {**HEADERS, "Authorization": f"Bearer {await identity.start_session(person.id)}"}


async def test_search_says_it_is_not_configured_until_an_admin_sets_a_key(
        api: FastAPI, client: AsyncClient, session: AsyncSession, site: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web, "BRAVE_URL", f"{site}/search")
    status = (await client.get("/web")).json()
    assert (status["configured"], status["keyMask"], status["provider"]) == (False, None, "brave")
    refused = await client.post("/web/search", json={"q": "invoice tax"})
    assert refused.status_code == 409 and "Settings → Web" in refused.json()["detail"]
    assert Site.searches == []

    saved = await client.put("/web", json={"key": "bsa-secret-9876"})
    assert saved.status_code == 200 and saved.json()["keyMask"] == "••••9876"
    assert "bsa-secret-9876" not in saved.text
    assert api.state.test_gateway.secrets.get(web.SECRET) == "bsa-secret-9876"   # the file, not the database
    audited = (await session.execute(select(m.AuditEntry.detail).where(m.AuditEntry.action == "web.update"))
               ).scalars().all()
    assert audited == [{"key": "set"}]

    found = await client.post("/web/search", json={"q": "invoice   tax", "projectId": "erp"})
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["query"] == "invoice tax" and body["results"][0]["url"] == "https://example.org/tax"
    assert body["decision"] == {"action": "ask", "ruleId": None,
                                "why": "No tool rule covers this query, so it asks."}
    said = (await session.execute(select(m.ActivityEvent.detail).where(
        m.ActivityEvent.action == "Web searched"))).scalars().all()
    assert said and "invoice tax" in said[0] and "1 results" in said[0]

    viewer = await headers_for(session, "viewer")
    seen = (await client.get("/web", headers=viewer)).json()
    assert seen["configured"] is True and "keyMask" not in seen          # masked key only for an admin
    assert (await client.put("/web", headers=viewer, json={"key": ""})).status_code == 403
    assert (await client.post("/web/search", headers=viewer, json={"q": "x"})).status_code == 403

    removed = (await client.put("/web", json={"key": ""})).json()
    assert removed["configured"] is False


async def test_a_deny_rule_stops_a_search_and_a_fetch_before_anything_is_sent(
        client: AsyncClient, site: str, monkeypatch: pytest.MonkeyPatch, loopback_is_public: None):
    monkeypatch.setattr(web, "BRAVE_URL", f"{site}/search")
    await client.put("/web", json={"key": "bsa-secret-9876"})
    await client.post("/permissions/tool-rules", json={"tool": "web_search", "pattern": "*salary*",
                                                       "action": "deny", "projectId": "erp"})
    await client.post("/permissions/tool-rules", json={"tool": "web_fetch", "pattern": f"{site}/page*",
                                                       "action": "deny"})

    refused = await client.post("/web/search", json={"q": "salary bands", "projectId": "erp"})
    assert refused.status_code == 403 and "denies web_search" in refused.json()["detail"]
    assert (await client.post("/web/search", json={"q": "salary bands", "projectId": "hims"})).status_code == 200
    assert [s["q"] for s in Site.searches] == ["salary bands"]      # only the one no rule denied

    denied = await client.post("/web/fetch", json={"url": f"{site}/page"})
    assert denied.status_code == 403 and "/page" not in Site.hits
    moved = await client.post("/web/fetch", json={"url": f"{site}/moved"})    # the redirect lands on /page
    assert moved.status_code == 403 and "redirected" in moved.json()["detail"]
    assert "/page" not in Site.hits


async def test_a_fetch_answers_the_page_and_is_in_the_activity_log(
        client: AsyncClient, session: AsyncSession, site: str, loopback_is_public: None):
    fetched = await client.post("/web/fetch", json={"url": f"{site}/page", "projectId": "erp"})
    assert fetched.status_code == 200, fetched.text
    page = fetched.json()
    assert page["title"] == "Invoice tax & rounding" and "rounded once" in page["text"]
    assert page["decision"]["action"] == "ask"
    said = (await session.execute(select(m.ActivityEvent.detail, m.ActivityEvent.project_id).where(
        m.ActivityEvent.action == "Web page fetched"))).all()
    assert [(d.startswith(f"{site}/page · HTTP 200"), p) for d, p in said] == [(True, "erp")]
    assert (await client.post("/web/fetch", json={"url": f"{site}/page", "projectId": "nope"})).status_code == 404


async def test_a_fetch_of_a_local_address_is_refused_even_for_the_owner(client: AsyncClient, site: str):
    refused = await client.post("/web/fetch", json={"url": f"{site}/page"})
    assert refused.status_code == 422 and "never fetches a page there" in refused.json()["detail"]
    assert Site.hits == []
