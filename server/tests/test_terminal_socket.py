"""The terminal and debugger sockets: who may open one, and what travels over it.

A socket looks its person up in a session of its own (a terminal stays open for hours, and must not hold
a request's transaction that long), so these tests run against a workspace that really commits, and
clean it away afterwards — as test_stream.py does.

The socket is driven over plain ASGI messages inside the test's own event loop: the terminal's pty
reader, the database pool and the socket must all live on one loop, which a threaded test client would
not give them.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.app import create_api
from app.api.deps import COOKIE
from app.api.routes_terminal import debuggers_of, terminals_of
from app.data.engine import Database
from app.data.loader import sync_roles
from app.settings import Settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "socket-owner@example.com",
         "password": "correct horse battery"}
ADMIN = {"email": "socket-admin@example.com", "name": "Asha", "password": "another long passphrase",
         "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}
ORIGIN = "http://localhost:5180"


class Socket:
    """A WebSocket client speaking ASGI to the app directly."""

    def __init__(self, app: FastAPI, path: str, headers: dict[str, str]) -> None:
        self.app = app
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.scope = {
            "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "scheme": "ws", "server": ("testserver", 80), "client": ("127.0.0.1", 50123), "root_path": "",
            "path": path, "raw_path": path.encode(), "query_string": b"", "subprotocols": [],
            "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in
                        {"host": "testserver", **headers}.items()],
        }
        self.task: asyncio.Task[None] | None = None
        self.closed: dict[str, Any] | None = None

    async def __aenter__(self) -> Socket:
        self.task = asyncio.create_task(self.app(self.scope, self.inbox.get, self.outbox.put))
        await self.inbox.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self.outbox.get(), 10)
        assert first["type"] == "websocket.accept", first
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.inbox.put({"type": "websocket.disconnect", "code": 1000})
        assert self.task is not None
        await asyncio.wait_for(self.task, 10)

    async def receive(self, seconds: float = 10) -> dict[str, Any]:
        message = await asyncio.wait_for(self.outbox.get(), seconds)
        if message["type"] == "websocket.close":
            self.closed = message
        return message

    async def send_bytes(self, data: bytes) -> None:
        await self.inbox.put({"type": "websocket.receive", "bytes": data})

    async def send_json(self, data: dict[str, Any]) -> None:
        await self.inbox.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def output_until(self, wanted: bytes, seconds: float = 15) -> tuple[bytes, list[dict[str, Any]]]:
        """Read frames until the output holds `wanted`: every byte seen, and the JSON messages between."""
        seen = b""
        said: list[dict[str, Any]] = []
        deadline = asyncio.get_running_loop().time() + seconds
        while wanted not in seen:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                raise AssertionError(f"never saw {wanted!r}; saw {seen[-400:]!r}")
            message = await self.receive(left)
            if message.get("bytes") is not None:
                seen += message["bytes"]
            elif message.get("text") is not None:
                said.append(json.loads(message["text"]))
            elif message["type"] == "websocket.close":
                raise AssertionError(f"closed: {message}")
        return seen, said


@pytest_asyncio.fixture
async def live(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    """The API over a real, committing database, with the machine's roots narrowed to a temp folder."""
    monkeypatch.setenv("SHELL", "/bin/sh")
    db = Database(url=settings.test_database_url)
    async with db.session() as s:
        await sync_roles(s)
    app = create_api(db=db)
    root = tmp_path / "machine"
    root.mkdir()
    app.state.settings = settings.model_copy(update={"database_url": settings.test_database_url,
                                                     "machine_roots": os.path.realpath(root),
                                                     "machine_access": True})
    try:
        yield app
    finally:
        await terminals_of(app).close_all()
        await debuggers_of(app).close_all()
        async with db.session() as s:
            await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM activity"))
            await s.execute(text("DELETE FROM projects WHERE id = 'sock'"))
            await s.execute(text("DELETE FROM sessions"))
            await s.execute(text("DELETE FROM user_roles"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM workspace"))
        app.state.ledger.close()
        await db.close()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def owner(live: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(live) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


def _cookie(client: AsyncClient) -> str:
    token = client.cookies.get(COOKIE)
    assert token, "signing in set no session cookie"
    return f"{COOKIE}={token}"


async def _terminal(client: AsyncClient, live: FastAPI) -> str:
    folder = live.state.settings.machine_roots
    answer = await client.post("/machine/terminals", json={"cwd": folder, "cols": 80, "rows": 24})
    assert answer.status_code == 201, answer.text
    return answer.json()["id"]


async def test_a_socket_echoes_what_it_is_sent(live: FastAPI, owner: AsyncClient):
    """The round trip: keystrokes in as binary frames, the shell's answer out as binary frames, and a
    resize as a small JSON message the shell then reports."""
    terminal = await _terminal(owner, live)
    headers = {"cookie": _cookie(owner), "origin": ORIGIN}
    async with Socket(live, f"/machine/terminals/{terminal}/ws", headers) as ws:
        hello = json.loads((await ws.receive())["text"])
        assert hello["type"] == "hello" and hello["terminal"]["id"] == terminal
        await ws.send_bytes(b"echo socket-$((20+3))\n")
        await ws.output_until(b"socket-23")
        await ws.send_json({"type": "resize", "cols": 120, "rows": 33})
        await ws.send_json({"type": "input", "data": "stty size; echo 'ünïcødé ✓'\n"})
        await ws.output_until("33 120".encode())
        await ws.output_until("ünïcødé ✓".encode())

    # A second tab — or the same one after a reload — is repainted from what the terminal printed.
    async with Socket(live, f"/machine/terminals/{terminal}/ws", headers) as again:
        assert json.loads((await again.receive())["text"])["type"] == "hello"
        replay = await again.receive()
        assert b"socket-23" in replay["bytes"]


async def test_a_socket_hears_the_program_end(live: FastAPI, owner: AsyncClient):
    terminal = await _terminal(owner, live)
    async with Socket(live, f"/machine/terminals/{terminal}/ws",
                      {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        await ws.receive()
        await ws.send_bytes(b"exit 3\n")
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            message = await ws.receive(max(0.1, deadline - asyncio.get_running_loop().time()))
            if message.get("text") and json.loads(message["text"])["type"] == "exit":
                assert json.loads(message["text"])["code"] == 3
                break
    assert (await owner.get(f"/machine/terminals/{terminal}")).json()["status"] == "exited"


async def test_a_socket_without_a_session_is_refused(live: FastAPI, owner: AsyncClient):
    terminal = await _terminal(owner, live)
    async with Socket(live, f"/machine/terminals/{terminal}/ws", {"origin": ORIGIN}) as ws:
        closed = await ws.receive()
        assert closed["type"] == "websocket.close" and closed["code"] == 4401
        assert closed["reason"] == "Sign in to continue."
    async with Socket(live, f"/machine/terminals/{terminal}/ws",
                      {"cookie": f"{COOKIE}=not-a-real-token", "origin": ORIGIN}) as ws:
        assert (await ws.receive())["code"] == 4401


async def test_a_socket_from_another_origin_is_refused(live: FastAPI, owner: AsyncClient):
    """A page on another site can make the browser send the cookie with a socket; it cannot pass this."""
    terminal = await _terminal(owner, live)
    for origin in ("https://evil.example", "http://localhost.evil.example", "null"):
        async with Socket(live, f"/machine/terminals/{terminal}/ws",
                          {"cookie": _cookie(owner), "origin": origin}) as ws:
            closed = await ws.receive()
            assert closed["type"] == "websocket.close" and closed["code"] == 4403, origin
    # With the cookie and no Origin at all, a caller could be anything: refused too.
    async with Socket(live, f"/machine/terminals/{terminal}/ws", {"cookie": _cookie(owner)}) as ws:
        assert (await ws.receive())["code"] == 4403
    # Its own host is same-origin, whatever the CORS list says.
    async with Socket(live, f"/machine/terminals/{terminal}/ws",
                      {"cookie": _cookie(owner), "origin": "http://testserver"}) as ws:
        assert json.loads((await ws.receive())["text"])["type"] == "hello"


async def test_a_socket_needs_the_permission_and_the_terminal(live: FastAPI, owner: AsyncClient):
    terminal = await _terminal(owner, live)
    await owner.post("/admin/users", json=ADMIN)
    async with _client(live) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        async with Socket(live, f"/machine/terminals/{terminal}/ws",
                          {"cookie": _cookie(admin), "origin": ORIGIN}) as ws:
            closed = await ws.receive()
            assert closed["code"] == 4403 and "machine:access" in closed["reason"]
    async with Socket(live, "/machine/terminals/nope/ws", {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        assert (await ws.receive())["code"] == 4404
    live.state.settings = live.state.settings.model_copy(update={"machine_access": False})
    async with Socket(live, f"/machine/terminals/{terminal}/ws", {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        closed = await ws.receive()
        assert closed["code"] == 4404 and closed["reason"] == "Machine access is off on this server"


async def test_closing_a_terminal_closes_its_sockets(live: FastAPI, owner: AsyncClient):
    terminal = await _terminal(owner, live)
    async with Socket(live, f"/machine/terminals/{terminal}/ws",
                      {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        await ws.receive()
        assert (await owner.delete(f"/machine/terminals/{terminal}")).json()["ok"] is True
        kinds: list[str] = []
        while ws.closed is None:
            message = await ws.receive()
            if message.get("text"):
                kinds.append(json.loads(message["text"])["type"])
        assert "closed" in kinds and ws.closed["code"] == 1000


async def test_the_debug_socket_is_refused_the_same_way(live: FastAPI, owner: AsyncClient):
    async with Socket(live, "/debug/anything/ws", {"origin": ORIGIN}) as ws:
        assert (await ws.receive())["code"] == 4401
    async with Socket(live, "/debug/anything/ws", {"cookie": _cookie(owner), "origin": "https://evil.example"}) as ws:
        assert (await ws.receive())["code"] == 4403
    async with Socket(live, "/debug/anything/ws", {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        assert (await ws.receive())["code"] == 4404


async def test_the_debug_socket_streams_a_stop_its_output_and_the_end(live: FastAPI, owner: AsyncClient,
                                                                    tmp_path: Path):
    """Events as they happen: the stop at the breakpoint, the program's output, and its end."""
    from app.models import Project

    checkout = Path(live.state.settings.machine_roots) / "sock"
    checkout.mkdir()
    script = checkout / "count.py"
    script.write_text("total = 0\nfor n in range(4):\n    total += n\nprint('counted', total)\n")
    async with live.state.db.session() as s:
        s.add(Project(id="sock", name="Socket", source_kind="local", source_repo=str(checkout)))
    started = await owner.post("/projects/sock/debug", json={"program": "count.py",
                                                            "breakpoints": {str(script): [4]}})
    assert started.status_code == 201, started.text
    debug = started.json()["id"]
    async with Socket(live, f"/debug/{debug}/ws", {"cookie": _cookie(owner), "origin": ORIGIN}) as ws:
        seen: list[dict[str, Any]] = []

        async def until(check: Any) -> dict[str, Any]:
            while True:
                event = json.loads((await ws.receive(45))["text"])
                seen.append(event)
                if check(event):
                    return event

        paused = await until(lambda e: e["type"] == "state" and e["session"]["status"] == "paused")
        assert paused["session"]["frames"][0]["line"] == 4
        assert (await owner.post(f"/debug/{debug}/continue", json={})).json() == {"ok": True}
        await until(lambda e: e["type"] == "output" and "counted 6" in e["text"])
        ended = await until(lambda e: e["type"] == "state" and e["session"]["status"] == "ended")
        assert ended["session"]["exitCode"] == 0
