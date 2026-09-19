"""Checking an MCP server for real.

The stdio servers here are small Python programs the tests write: one that speaks the protocol, one
that dies before it can, one that never answers, one that leaves a child behind. The http server is a
standard-library HTTP server on a free local port, answering the streamable HTTP transport the way a
real one does — a session id on `initialize`, and an event stream for the listing. Nothing leaves the
machine, and nothing is mocked below the socket.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.services import mcp as mcp_service
from app.services.identity import IdentityService
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}

#: A stdio MCP server in a few lines: answers initialize, then lists two tools across two pages.
SPEAKS = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, cursor = msg["method"], (msg.get("params") or {}).get("cursor")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "prompts": {}},
                  "serverInfo": {"name": "ledger", "version": "1"}}
    elif method == "tools/list" and not cursor:
        print("log line that is not json", flush=True)
        result = {"tools": [{"name": "read_ledger", "description": "Read a ledger row",
                             "annotations": {"readOnlyHint": True}}], "nextCursor": "p2"}
    elif method == "tools/list":
        result = {"tools": [{"name": "post_entry", "description": "Post an entry"}]}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "close_month"}]}
    elif method == "tools/call" and msg["params"]["name"] == "post_entry":
        result = {"content": [{"type": "text", "text": "The ledger is closed for the month."}], "isError": True}
    elif method == "tools/call":
        said = "row for " + json.dumps(msg["params"].get("arguments"), sort_keys=True)
        result = {"content": [{"type": "text", "text": said}, {"type": "image", "mimeType": "image/png", "data": ""}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''

DIES = "import sys; sys.stderr.write('missing LEDGER_URL\\n'); sys.exit(3)"
SILENT = "import time; time.sleep(60)"


def command(script: str) -> str:
    return shlex.join([sys.executable, "-c", script])


@pytest.fixture
def speaks(tmp_path: Path) -> str:
    """The speaking server, launched from a file: a registered command is capped at 1000 characters."""
    path = tmp_path / "ledger server.py"             # a space in the path: the command is split, not shell-run
    path.write_text(SPEAKS)
    return shlex.join([sys.executable, str(path)])


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def register(client: AsyncClient, name: str, transport: str, cmd: str, *, trust: bool) -> None:
    made = await client.post("/mcp/servers", json={"name": name, "transport": transport, "command": cmd,
                                                    "scope": "global", "defaultEffect": "ask", "config": "{}"})
    assert made.status_code == 201, made.text
    if trust:
        trusted = await client.post(f"/mcp/servers/{name}/trust", json={"trusted": True})
        assert trusted.status_code == 200 and trusted.json()["untrusted"] is False


async def headers_for(session: AsyncSession, role: str) -> dict[str, str]:
    identity = IdentityService(session)
    person = await identity.create(f"{role}@example.com", role.title(), "correct horse battery", [role])
    return {**HEADERS, "Authorization": f"Bearer {await identity.start_session(person.id)}"}


# ── stdio ────────────────────────────────────────────────────────
async def test_a_trusted_stdio_server_is_launched_listed_and_recorded(client: AsyncClient, speaks: str):
    await register(client, "ledger", "stdio", speaks, trust=True)

    checked = await client.post("/mcp/servers/ledger/check")
    assert checked.status_code == 200
    doc = checked.json()
    assert doc["status"] == "connected" and doc["lastError"] == "" and doc["checkedAt"]
    assert doc["tools"] == [{"name": "post_entry", "description": "Post an entry", "risk": "HIGH"},
                            {"name": "read_ledger", "description": "Read a ledger row", "risk": "LOW"}]
    assert doc["prompts"] == 1 and doc["resources"] == 0            # resources were never offered
    assert isinstance(doc["latencyMs"], int) and doc["latencyMs"] >= 0
    listed = next(s for s in (await client.get("/mcp/servers")).json() if s["id"] == "ledger")
    assert listed == doc


async def test_a_command_that_exits_is_an_error_with_its_reason(client: AsyncClient):
    await register(client, "broken", "stdio", command(DIES), trust=True)

    doc = (await client.post("/mcp/servers/broken/check")).json()
    assert doc["status"] == "error"
    assert "code 3" in doc["lastError"] and "missing LEDGER_URL" in doc["lastError"]
    assert doc["tools"] == [] and doc["latencyMs"] is None and doc["checkedAt"]


async def test_a_program_that_is_not_there_is_an_error_not_a_crash(client: AsyncClient):
    await register(client, "ghost", "stdio", "definitely-not-a-program-neurocode --stdio", trust=True)
    doc = (await client.post("/mcp/servers/ghost/check")).json()
    assert doc["status"] == "error" and "definitely-not-a-program-neurocode" in doc["lastError"]


async def test_an_untrusted_stdio_server_is_never_launched(client: AsyncClient, tmp_path: Path):
    marker = tmp_path / "launched"
    await register(client, "stranger", "stdio",
                   command(f"open({str(marker)!r}, 'w').write('x')"), trust=False)

    refused = await client.post("/mcp/servers/stranger/check")
    assert refused.status_code == 409 and "Trust it first" in refused.json()["detail"]
    assert not marker.exists()
    listed = next(s for s in (await client.get("/mcp/servers")).json() if s["id"] == "stranger")
    assert listed["checkedAt"] is None and listed["status"] == "disconnected"


async def test_launching_and_trusting_need_more_than_managing_the_registry(client: AsyncClient,
                                                                         session: AsyncSession, speaks: str):
    await register(client, "ledger", "stdio", speaks, trust=True)
    approver = await headers_for(session, "approver")                 # holds mcp:manage, not workspace:admin

    check = await client.post("/mcp/servers/ledger/check", headers=approver)
    assert check.status_code == 403 and "workspace:admin" in check.json()["detail"]
    trust = await client.post("/mcp/servers/ledger/trust", json={"trusted": False}, headers=approver)
    assert trust.status_code == 403
    viewer = await headers_for(session, "viewer")
    assert (await client.post("/mcp/servers/ledger/check", headers=viewer)).status_code == 403
    assert (await client.post("/mcp/servers/nope/check")).status_code == 404


async def test_a_stderr_line_past_the_limit_is_survived_not_raised(client: AsyncClient, tmp_path: Path):
    """A line longer than the pipe's limit used to kill the stderr reader, and the check with it (a 500)."""
    path = tmp_path / "chatty.py"
    path.write_text("import sys\n"
                    f"sys.stderr.write('x' * {mcp_service.LINE_LIMIT + 1024} + '\\n')\n"
                    "sys.stderr.write('missing LEDGER_URL\\n')\n"
                    "sys.stderr.flush()\n"
                    "sys.exit(3)\n")
    await register(client, "chatty", "stdio", shlex.join([sys.executable, str(path)]), trust=True)

    checked = await client.post("/mcp/servers/chatty/check")
    assert checked.status_code == 200, checked.text
    doc = checked.json()
    assert doc["status"] == "error" and "code 3" in doc["lastError"]


async def test_a_server_that_never_answers_is_stopped_at_the_ceiling(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(mcp_service, "TIMEOUT_S", 1.0)
    await register(client, "silent", "stdio", command(SILENT), trust=True)

    started = time.monotonic()
    doc = (await client.post("/mcp/servers/silent/check")).json()
    assert time.monotonic() - started < 5
    assert doc["status"] == "error" and "within 1 s" in doc["lastError"]


async def test_what_the_server_started_is_killed_with_it(client: AsyncClient, tmp_path: Path, monkeypatch):
    """An `npx` wrapper forks the real server; killing only the wrapper would leave that running."""
    monkeypatch.setattr(mcp_service, "TIMEOUT_S", 1.0)
    pidfile = tmp_path / "child.pid"
    spawns = (f"import subprocess, sys; child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              f"open({str(pidfile)!r}, 'w').write(str(child.pid)); sys.stdin.read()")
    await register(client, "forker", "stdio", command(spawns), trust=True)

    doc = (await client.post("/mcp/servers/forker/check")).json()
    assert doc["status"] == "error"                                    # it never spoke the protocol
    child = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(child, 9)
        pytest.fail("the server's child outlived the check")


async def test_trusting_twice_is_refused_in_words(client: AsyncClient, speaks: str):
    await register(client, "ledger", "stdio", speaks, trust=True)
    again = await client.post("/mcp/servers/ledger/trust", json={"trusted": True})
    assert again.status_code == 409 and "already trusted" in again.json()["detail"]


async def test_a_new_check_replaces_what_the_last_one_found(client: AsyncClient, speaks: str):
    await register(client, "ledger", "stdio", speaks, trust=True)
    assert len((await client.post("/mcp/servers/ledger/check")).json()["tools"]) == 2
    assert len((await client.post("/mcp/servers/ledger/check")).json()["tools"]) == 2   # replaced, not doubled


# ── http ─────────────────────────────────────────────────────────
class Streamable(BaseHTTPRequestHandler):
    """The streamable HTTP transport: JSON for initialize with a session id, an event stream after."""

    seen: list[dict[str, Any]] = []
    demand_auth = False

    def log_message(self, *_: Any) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802 — the name http.server calls
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"method": body.get("method"), "session": self.headers.get("Mcp-Session-Id")})
        if type(self).demand_auth:
            self.send_response(401)
            self.end_headers()
            return
        if "id" not in body:
            self.send_response(202)
            self.end_headers()
            return
        if body["method"] == "initialize":
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "resources": {}}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "sess-42")
        else:
            if self.headers.get("Mcp-Session-Id") != "sess-42":
                self.send_response(400)
                self.end_headers()
                return
            result = ({"tools": [{"name": "search_issues", "annotations": {"readOnlyHint": True}}]}
                      if body["method"] == "tools/list"
                      else {"content": [{"type": "text", "text": "3 issues match " + body["params"]["arguments"]["q"]}]}
                      if body["method"] == "tools/call" else {"resources": [{"uri": "a"}, {"uri": "b"}]})
            message = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
            payload = f"event: message\ndata: {message}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def http_server() -> Iterator[str]:
    Streamable.seen, Streamable.demand_auth = [], False
    server = ThreadingHTTPServer(("127.0.0.1", 0), Streamable)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()
    server.server_close()


async def test_an_http_server_is_checked_with_its_session_id(client: AsyncClient, http_server: str):
    await register(client, "issues", "http", http_server, trust=False)   # nothing is launched for http

    doc = (await client.post("/mcp/servers/issues/check")).json()
    assert doc["status"] == "connected", doc["lastError"]
    assert doc["tools"] == [{"name": "search_issues", "description": "", "risk": "LOW"}]
    assert doc["resources"] == 2 and doc["prompts"] == 0
    assert [s["method"] for s in Streamable.seen] == ["initialize", "notifications/initialized",
                                                      "tools/list", "resources/list"]
    assert [s["session"] for s in Streamable.seen] == [None, "sess-42", "sess-42", "sess-42"]


async def test_an_http_server_that_wants_credentials_says_so(client: AsyncClient, http_server: str):
    Streamable.demand_auth = True
    await register(client, "locked", "http", http_server, trust=False)
    doc = (await client.post("/mcp/servers/locked/check")).json()
    assert doc["status"] == "auth_required" and "401" in doc["lastError"]


async def test_a_local_address_is_checked_only_for_a_workspace_admin(client: AsyncClient, http_server: str,
                                                                    session: AsyncSession):
    """Checking over http launches nothing, so managing the registry lets a person check a server — but
    the request comes from this machine, so a local or private address is an admin's to reach."""
    await register(client, "issues", "http", http_server, trust=False)
    approver = await headers_for(session, "approver")                 # holds mcp:manage, not workspace:admin

    doc = (await client.post("/mcp/servers/issues/check", headers=approver)).json()
    assert doc["status"] == "error" and "local or private address" in doc["lastError"]
    assert Streamable.seen == []                                       # refused before a byte was sent

    for n, address in enumerate(("http://169.254.169.254/latest/meta-data", "http://10.0.0.8/mcp",
                                 "http://[::1]:9/mcp", "http://[::ffff:127.0.0.1]:9/mcp", "http://0.0.0.0:9/mcp")):
        await register(client, f"inside-{n}", "http", address, trust=False)
        doc = (await client.post(f"/mcp/servers/inside-{n}/check", headers=approver)).json()
        assert doc["status"] == "error" and "local or private address" in doc["lastError"], address

    assert (await client.post("/mcp/servers/issues/check")).json()["status"] == "connected"   # the owner may


def test_only_internet_addresses_count_as_public():
    import ipaddress
    public = mcp_service._public
    assert public(ipaddress.ip_address("93.184.216.34"))
    assert public(ipaddress.ip_address("2606:4700:4700::1111"))
    for local in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                  "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00:ec2::254", "::ffff:169.254.169.254"):
        assert not public(ipaddress.ip_address(local)), local


class Leaky(BaseHTTPRequestHandler):
    """A server that answers with what should never reach the screen: a redirect to somewhere internal,
    a page that is not JSON, or an error page — each carrying a secret."""

    mode = "redirect"
    hits: list[str] = []

    def log_message(self, *_: Any) -> None:
        return None

    def _answer(self, status: int, body: bytes, location: str = "") -> None:
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits.append(f"GET {self.path}")
        self._answer(200, b"INTERNAL-ONLY secret=hunter2")

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        type(self).hits.append(f"POST {self.path}")
        if type(self).mode == "redirect":
            self._answer(302, b"", location="/internal-secret")
        elif type(self).mode == "html":
            self._answer(200, b"INTERNAL-ONLY secret=hunter2")
        else:
            self._answer(500, b"Traceback: secret=hunter2")


@pytest.fixture
def leaky() -> Iterator[str]:
    Leaky.hits, Leaky.mode = [], "redirect"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Leaky)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()
    server.server_close()


async def test_a_redirect_is_never_followed(client: AsyncClient, leaky: str):
    await register(client, "moved", "http", leaky, trust=False)
    doc = (await client.post("/mcp/servers/moved/check")).json()
    assert doc["status"] == "error" and "redirect" in doc["lastError"]
    assert Leaky.hits == ["POST /mcp"]                                 # nothing fetched where it pointed
    assert "internal-secret" not in doc["lastError"]


@pytest.mark.parametrize("mode", ["html", "error"])
async def test_what_the_server_sent_back_is_never_the_recorded_reason(client: AsyncClient, leaky: str, mode: str):
    Leaky.mode = mode
    await register(client, "noisy", "http", leaky, trust=False)
    doc = (await client.post("/mcp/servers/noisy/check")).json()
    assert doc["status"] == "error" and doc["lastError"]
    assert "hunter2" not in doc["lastError"] and "INTERNAL" not in doc["lastError"]
    listed = next(s for s in (await client.get("/mcp/servers")).json() if s["id"] == "noisy")
    assert "hunter2" not in json.dumps(listed)


async def test_an_address_that_is_not_http_is_an_error(client: AsyncClient):
    await register(client, "odd", "http", "ftp://example.com/mcp", trust=False)
    doc = (await client.post("/mcp/servers/odd/check")).json()
    assert doc["status"] == "error" and "not an http" in doc["lastError"]


# ── calling a tool ───────────────────────────────────────────────
async def _checked(client: AsyncClient, name: str, transport: str, cmd: str, *, trust: bool = True) -> None:
    await register(client, name, transport, cmd, trust=trust)
    assert (await client.post(f"/mcp/servers/{name}/check")).json()["status"] == "connected"


async def test_a_listed_tool_is_called_and_what_it_said_comes_back(client: AsyncClient, session: AsyncSession,
                                                                  speaks: str):
    await _checked(client, "ledger", "stdio", speaks)
    called = await client.post("/mcp/servers/ledger/tools/read_ledger/call",
                               json={"arguments": {"row": 7, "book": "sales"}, "projectId": "erp"})
    assert called.status_code == 200, called.text
    body = called.json()
    assert (body["ok"], body["isError"], body["truncated"], body["error"]) == (True, False, False, "")
    assert body["text"] == 'row for {"book": "sales", "row": 7}\n[image · image/png]'
    assert isinstance(body["ms"], int) and body["decision"]["action"] == "ask"

    # The tool saying its work failed is an answer, not a failure of the call.
    failed = (await client.post("/mcp/servers/ledger/tools/post_entry/call", json={"arguments": {}})).json()
    assert (failed["ok"], failed["isError"], failed["text"]) == (True, True, "The ledger is closed for the month.")

    said = (await session.execute(select(m.ActivityEvent.detail).where(
        m.ActivityEvent.action == "MCP tool called").order_by(m.ActivityEvent.seq))).scalars().all()
    assert said[0].startswith("ledger/read_ledger · LOW risk · ok") and "reported an error" in said[1]
    audited = (await session.execute(select(m.AuditEntry.target, m.AuditEntry.detail).where(
        m.AuditEntry.action == "mcp.call").order_by(m.AuditEntry.seq))).all()
    assert [t for t, _ in audited] == ["ledger/read_ledger", "ledger/post_entry"]
    assert "sales" not in json.dumps([d for _, d in audited])        # what it was given is never kept


async def test_a_call_is_refused_before_anything_is_sent(client: AsyncClient, session: AsyncSession, speaks: str,
                                                        tmp_path: Path):
    marker = tmp_path / "launched"
    await register(client, "stranger", "stdio", command(f"open({str(marker)!r}, 'w').write('x')"), trust=False)
    untrusted = await client.post("/mcp/servers/stranger/tools/anything/call", json={"arguments": {}})
    assert untrusted.status_code == 409 and "Trust it first" in untrusted.json()["detail"]
    assert not marker.exists()

    await register(client, "ledger", "stdio", speaks, trust=True)
    unchecked = await client.post("/mcp/servers/ledger/tools/read_ledger/call", json={"arguments": {}})
    assert unchecked.status_code == 409 and "Check it first" in unchecked.json()["detail"]
    await client.post("/mcp/servers/ledger/check")
    unknown = await client.post("/mcp/servers/ledger/tools/drop_tables/call", json={"arguments": {}})
    assert unknown.status_code == 404 and "did not list" in unknown.json()["detail"]
    assert (await client.post("/mcp/servers/nope/tools/x/call", json={"arguments": {}})).status_code == 404

    rule = (await client.post("/permissions/tool-rules", json={
        "tool": "mcp", "pattern": "ledger/post_*", "action": "deny", "projectId": "erp"})).json()
    denied = await client.post("/mcp/servers/ledger/tools/post_entry/call",
                               json={"arguments": {}, "projectId": "erp"})
    assert denied.status_code == 403 and f"Rule #{rule['id']}" in denied.json()["detail"]
    # The rule is erp's: in another project the same call asks, and the person pressing Try is the answer.
    elsewhere = await client.post("/mcp/servers/ledger/tools/post_entry/call",
                                  json={"arguments": {}, "projectId": "hims"})
    assert elsewhere.status_code == 200

    approver = await headers_for(session, "approver")                 # mcp:manage, but may not launch
    launch = await client.post("/mcp/servers/ledger/tools/read_ledger/call", headers=approver,
                               json={"arguments": {}})
    assert launch.status_code == 403 and "workspace:admin" in launch.json()["detail"]
    viewer = await headers_for(session, "viewer")
    assert (await client.post("/mcp/servers/ledger/tools/read_ledger/call", headers=viewer,
                              json={"arguments": {}})).status_code == 403
    assert (await client.post("/mcp/servers/ledger/tools/read_ledger/call",
                              json={"arguments": {"blob": "x" * 30_000}})).status_code == 422


async def test_a_server_whose_default_is_deny_needs_a_rule_that_allows(client: AsyncClient, speaks: str):
    made = await client.post("/mcp/servers", json={"name": "vault", "transport": "stdio", "command": speaks,
                                                   "scope": "global", "defaultEffect": "deny", "config": "{}"})
    assert made.status_code == 201
    await client.post("/mcp/servers/vault/trust", json={"trusted": True})
    await client.post("/mcp/servers/vault/check")

    refused = await client.post("/mcp/servers/vault/tools/read_ledger/call", json={"arguments": {}})
    assert refused.status_code == 403 and "default effect is deny" in refused.json()["detail"]
    await client.post("/permissions/tool-rules", json={"tool": "mcp", "pattern": "vault/read_*", "action": "allow"})
    allowed = (await client.post("/mcp/servers/vault/tools/read_ledger/call", json={"arguments": {}})).json()
    assert allowed["ok"] and allowed["decision"]["action"] == "allow"


async def test_an_http_tool_is_called_in_the_same_session_and_its_answer_capped(
        client: AsyncClient, http_server: str, monkeypatch: pytest.MonkeyPatch):
    await _checked(client, "issues", "http", http_server, trust=True)
    Streamable.seen = []
    monkeypatch.setattr(mcp_service, "RESULT_CHARS", 10)
    body = (await client.post("/mcp/servers/issues/tools/search_issues/call",
                              json={"arguments": {"q": "gst rounding"}})).json()
    assert (body["ok"], body["text"], body["truncated"]) == (True, "3 issues m", True)
    assert [s["method"] for s in Streamable.seen] == ["initialize", "notifications/initialized", "tools/call"]
    assert [s["session"] for s in Streamable.seen] == [None, "sess-42", "sess-42"]


async def test_a_server_that_went_away_is_an_answer_not_a_crash(client: AsyncClient, http_server: str):
    await _checked(client, "issues", "http", http_server, trust=True)
    Streamable.demand_auth = True
    body = (await client.post("/mcp/servers/issues/tools/search_issues/call", json={"arguments": {"q": "x"}})).json()
    assert body["ok"] is False and "credentials" in body["error"] and body["text"] == ""
