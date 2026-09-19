"""An in-memory NeuroCode API for the client's own tests.

It answers in the API's shapes (the server's suite proves those against the real thing, with `nc` itself
run against a live uvicorn in `server/tests/test_cli_live.py`). Here it lets every command, the stream
reader and the full-screen session run end to end with no socket: `client.TRANSPORT` is set to it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from neurocode_cli import client as client_module

URL = "http://nc.test"
API = URL + "/api"
TOKEN = "nc_pat_testtoken0000000000000000000000000000"


def sse(*events: tuple[str, Any]) -> bytes:
    return ("retry: 3000\n\n" + "".join(f"event: {k}\ndata: {json.dumps(d)}\n\n" for k, d in events)
            + ": keep-alive\n\n").encode()


@dataclass
class FakeApi:
    """What the API holds, and every request it was sent."""

    projects: list[dict[str, Any]] = field(default_factory=lambda: [
        {"id": "erp", "name": "ERP", "status": "active", "stack": ["python"], "work": {"tasks": 3},
         "lastActive": None}])
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: What the live stream sends, once, to the next reader.
    stream: list[tuple[str, Any]] = field(default_factory=list)
    #: What happens after a question: "answer" writes an answer, "card" a permission card.
    after_ask: str = "answer"
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    seen: list[tuple[str, str, Any]] = field(default_factory=list)
    tokens_made: list[dict[str, Any]] = field(default_factory=list)
    signed_in: bool = False
    needs_setup: bool = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, path, body))
        if not path.startswith("/api/"):
            return httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"})
        path = path[4:]
        auth = request.headers.get("authorization", "")
        cookie = "nc_session=" in request.headers.get("cookie", "")
        public = path in ("/health", "/auth/status", "/auth/login")
        if not public and auth != f"Bearer {TOKEN}" and not cookie:
            return httpx.Response(401, json={"detail": "Sign in to continue."})
        return self.route(request.method, path, body, request)

    def route(self, method: str, path: str, body: Any, request: httpx.Request) -> httpx.Response:
        ok = lambda data, code=200: httpx.Response(code, json=data)  # noqa: E731
        if path == "/health":
            return ok({"ok": True, "db": "127.0.0.1:5432/neurocode", "counts": {},
                       "compiler": {"provider": "groq", "model": "llama", "lanes": 2}})
        if path == "/auth/status":
            return ok({"needsSetup": self.needs_setup, "user": None, "workspace": {"name": "Acme"}})
        if path == "/auth/login":
            if body["password"] != "correct horse battery":
                return httpx.Response(401, json={"detail": "Wrong email or password."})
            self.signed_in = True
            return httpx.Response(200, json={"user": self.user()},
                                  headers={"set-cookie": "nc_session=abc; Path=/; HttpOnly"})
        if path == "/auth/logout":
            self.signed_in = False
            return ok({"ok": True})
        if path == "/auth/me":
            return ok({"user": self.user(), "workspace": {"name": "Acme"}})
        if path == "/tokens" and method == "POST":
            if request.headers.get("authorization"):
                return httpx.Response(403, json={"detail": "Access tokens are managed from a signed-in session, not with a token."})
            made = {"id": "tok_1", "name": body["name"], "prefix": TOKEN[:11], "scopes": body["scopes"],
                    "allScopes": not body["scopes"], "createdAt": "2026-09-19T10:00:00+00:00", "lastUsedAt": None,
                    "expiresAt": None, "revokedAt": None, "state": "active", "token": TOKEN}
            self.tokens_made.append(made)
            return ok(made, 201)
        if path == "/projects":
            return ok(self.projects)
        if m := re.fullmatch(r"/projects/([^/]+)", path):
            found = next((p for p in self.projects if p["id"] == m.group(1)), None)
            return ok(found) if found else httpx.Response(404, json={"detail": f"project {m.group(1)} not found"})
        if m := re.fullmatch(r"/projects/([^/]+)/mentions", path):
            q = request.url.params.get("q", "")
            items = [{"kind": "file", "ref": "src/invoice.py", "name": "src/invoice.py", "detail": "120 lines"},
                     {"kind": "fact", "ref": "MEM-1", "name": "Invoice rounding", "detail": "business_rules"}]
            return ok({"items": [i for i in items if q.lower() in i["name"].lower()]})
        if path == "/extensions/commands":
            return ok({"commands": [{"name": "review", "description": "Review the diff", "scope": "project"}]})
        if path == "/sessions" and method == "POST":
            ref = f"SES-{len(self.sessions) + 1}"
            self.sessions[ref] = {"id": ref.lower(), "ref": ref, "projectId": body["projectId"], "projectName": "ERP",
                                  "title": body["title"], "status": "idle", "turns": 0, "lane": "groq",
                                  "model": "llama", "contextTokens": 1200, "contextWindow": 128000,
                                  "lastAt": "2026-09-19T10:00:00+00:00", "waitingOn": None}
            self.messages[ref] = []
            return ok(self.sessions[ref], 201)
        if path == "/sessions":
            return ok(list(self.sessions.values()))
        if m := re.fullmatch(r"/sessions/([^/]+)", path):
            ref = m.group(1)
            if ref not in self.sessions:
                return httpx.Response(404, json={"detail": f"session {ref} not found"})
            after = int(request.url.params.get("after", 0))
            return ok({**self.sessions[ref], "messages": [x for x in self.messages[ref] if x["id"] > after]})
        if m := re.fullmatch(r"/sessions/([^/]+)/messages", path):
            return ok(self.ask(m.group(1), body), 201)
        if m := re.fullmatch(r"/sessions/([^/]+)/permissions/(\d+)", path):
            ref, mid = m.group(1), int(m.group(2))
            card = next(x for x in self.messages[ref] if x["id"] == mid)
            card["permission"]["state"] = "allowed" if body["decision"] != "refuse" else "refused"
            self.say(ref, {"role": "assistant", "text": "Read it: the tax is rounded once."})
            self.sessions[ref]["status"] = "idle"
            return ok(self.sessions[ref])
        if path == "/approvals":
            return ok(self.approvals)
        if m := re.fullmatch(r"/approvals/([^/]+)/(approve|deny)", path):
            gate = next(a for a in self.approvals if a["ref"] == m.group(1))
            gate["status"] = "approved" if m.group(2) == "approve" else "denied"
            return ok(gate)
        if path == "/runs":
            return ok(list(self.runs.values()))
        if m := re.fullmatch(r"/runs/([^/]+)", path):
            run = self.runs.get(m.group(1))
            if run is None:
                return httpx.Response(404, json={"detail": f"run {m.group(1)} not found"})
            after = int(request.url.params.get("after", 0))
            return ok({**run, "logs": [x for x in run["logs"] if x["id"] > after]})
        if path == "/memory/facts":
            made = [{"ref": f"MEM-{len(self.facts) + 1}", "projectId": body["projectId"], **f} for f in body["facts"]]
            self.facts += made
            return ok(made, 201)
        if path == "/memory":
            q = request.url.params.get("q", "")
            return ok([f for f in self.facts if q.lower() in (f["title"] + f["body"]).lower()])
        if path == "/activity/stream":
            events, self.stream = self.stream, []
            return httpx.Response(200, content=sse(*events), headers={"content-type": "text/event-stream"})
        return httpx.Response(404, json={"detail": f"no route {method} {path}"})

    def user(self) -> dict[str, Any]:
        return {"id": "u_1", "email": "owner@example.com", "name": "Rajat", "status": "active", "roles": ["owner"],
                "permissions": ["sessions:chat", "memory:write", "approvals:decide"]}

    def say(self, ref: str, message: dict[str, Any]) -> dict[str, Any]:
        made = {"id": sum(len(v) for v in self.messages.values()) + 1, "at": "2026-09-19T10:00:00+00:00", **message}
        self.messages[ref].append(made)
        return made

    def ask(self, ref: str, body: dict[str, Any]) -> dict[str, Any]:
        question = self.say(ref, {"role": "you", "text": body["text"], "attachments": body["attachments"]})
        if self.after_ask == "answer":
            self.say(ref, {"role": "tool", "tool": "grounding", "text": "…", "detail": "3 pieces from the index",
                           "ok": True})
            self.say(ref, {"role": "assistant", "text": "Tax is rounded **once**.", "lane": "groq", "model": "llama",
                           "ms": 900, "reasoning": "The rule says so."})
        elif self.after_ask == "card":
            self.say(ref, {"role": "tool", "tool": "permission", "text": "", "arguments": {"url": "https://x"},
                           "permission": {"tool": "web_fetch", "subject": "https://example.org", "why": "to read it",
                                          "state": "pending"}, "ok": None})
        return {"message": question, "session": self.sessions[ref]}


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeApi]:
    fake = FakeApi()
    monkeypatch.setattr(client_module, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setenv("NC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    for name in ("NC_URL", "NC_TOKEN", "NC_WEB", "NC_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    yield fake


@pytest.fixture
def signed(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """Signed in to the fake API with a token, through the environment."""
    monkeypatch.setenv("NC_URL", API)
    monkeypatch.setenv("NC_TOKEN", TOKEN)
    monkeypatch.setenv("NC_WEB", URL)
    return api
