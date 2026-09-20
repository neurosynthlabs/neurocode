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


#: A real patch, as `git diff` writes one: two files, a hunk each, and a line longer than any window.
PATCH = """diff --git a/src/tax.py b/src/tax.py
index 1111111..2222222 100644
--- a/src/tax.py
+++ b/src/tax.py
@@ -1,4 +1,5 @@
 def total(lines):
-    return sum(round(line.amount * line.rate, 2) for line in lines)
+    # Rounded once, on the total, because that is what the invoice rule says and the tests now prove.
+    return round(sum(line.amount * line.rate for line in lines), 2)
 
 
diff --git a/tests/test_tax.py b/tests/test_tax.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/test_tax.py
@@ -0,0 +1,2 @@
+def test_tax_is_rounded_once():
+    assert total([Line(1.005, 1), Line(1.005, 1)]) == 2.01
"""


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
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: What GET /runs/{ref}/diff answers with. `patch` is a real two-file patch, so what `nc diff`
    #: prints and what `git apply` would take are the same bytes.
    patch: str = PATCH
    truncated: bool = False
    gone: bool = False
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
            return ok(self.page([s for s in reversed(self.sessions.values())
                                 if not request.url.params.get("project")
                                 or s["projectId"] == request.url.params.get("project")], request))
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
            wanted = [a for a in self.approvals
                      if request.url.params.get("status") != "pending" or a["status"] == "pending"]
            return ok(self.page(wanted, request))
        if m := re.fullmatch(r"/approvals/([^/]+)/(approve|deny)", path):
            gate = next(a for a in self.approvals if a["ref"] == m.group(1))
            gate["status"] = "approved" if m.group(2) == "approve" else "denied"
            return ok(gate)
        if path == "/runs":
            return ok(self.page(list(self.runs.values()), request))
        if m := re.fullmatch(r"/runs/([^/]+)/diff", path):
            run = self.runs.get(m.group(1))
            if run is None:
                return httpx.Response(404, json={"detail": f"run {m.group(1)} not found"})
            stat = run.get("diff") or {}
            return ok({"patch": "" if self.gone else self.patch, "truncated": self.truncated,
                       "stat": stat, "gone": self.gone})
        if m := re.fullmatch(r"/runs/([^/]+)/(cancel|discard|review)", path):
            run = self.runs[m.group(1)]
            if m.group(2) == "cancel":
                run["status"] = "cancelled"
            elif m.group(2) == "discard":
                run["removed"] = True
            return ok(run)
        if m := re.fullmatch(r"/runs/([^/]+)/steps/(\d+)/revert", path):
            run = self.runs[m.group(1)]
            run["reverts"] = [*(run.get("reverts") or []), {"to": int(m.group(2)), "redo": body.get("redo")}]
            return ok(run)
        if m := re.fullmatch(r"/runs/([^/]+)/rework", path):
            old_run = self.runs.get(m.group(1))
            if old_run is None:
                return httpx.Response(404, json={"detail": f"run {m.group(1)} not found"})
            made = {**old_run, "ref": f"RUN-{len(self.runs) + 100}", "status": "queued", "logs": [],
                    "review": {"findings": [], "verdict": "", "by": ""}, "notes": body["notes"]}
            self.runs[made["ref"]] = made
            for gate in self.approvals:                       # the signature it was waiting for is refused
                if gate.get("runRef") == old_run["ref"] and gate["status"] == "pending":
                    gate["status"] = "denied"
            return ok(made, 201)
        if m := re.fullmatch(r"/runs/([^/]+)", path):
            run = self.runs.get(m.group(1))
            if run is None:
                return httpx.Response(404, json={"detail": f"run {m.group(1)} not found"})
            after = int(request.url.params.get("after", 0))
            return ok({**run, "logs": [x for x in run["logs"] if x["id"] > after]})
        if path == "/plans/compile":
            ref = f"PLAN-{len(self.plans) + 1}"
            self.plans[ref] = {"ref": ref, "projectId": body["projectId"], "status": "draft", "risk": "LOW",
                               "confidence": "HIGH", "rawRequirement": body["requirement"], "steps": [],
                               "openQuestions": [], "answered": [], "affectedFiles": [],
                               "businessRequirement": body["requirement"][:200]}
            return ok(self.plans[ref], 201)
        if path == "/plans":
            return ok(self.page(list(self.plans.values()), request))
        if m := re.fullmatch(r"/plans/([^/]+)", path):
            found = self.plans.get(m.group(1))
            return ok(found) if found else httpx.Response(404, json={"detail": f"plan {m.group(1)} not found"})
        if m := re.fullmatch(r"/plans/([^/]+)/questions/(\d+)", path):
            found, index = self.plans.get(m.group(1)), int(m.group(2))
            if found is None:
                return httpx.Response(404, json={"detail": f"plan {m.group(1)} not found"})
            if not 0 <= index < len(found["openQuestions"]):
                return httpx.Response(404, json={"detail": f"open question #{index} of {m.group(1)} not found"})
            found["answered"] = [*found.get("answered", []),
                                 {"q": found["openQuestions"][index], "a": body["answer"]}]
            found["openQuestions"] = [q for i, q in enumerate(found["openQuestions"]) if i != index]
            return ok(found)
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

    def page(self, items: list[dict[str, Any]], request: httpx.Request) -> list[dict[str, Any]]:
        """Every list route cuts its page in the query, so the fake does too — a list that came back
        full is the only way `nc` can tell there may be more."""
        limit = int(request.url.params.get("limit", 50))
        offset = int(request.url.params.get("offset", 0))
        return items[offset:offset + limit]

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
