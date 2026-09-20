"""The client layer: every call `nc` makes, over HTTP, and the live stream read as it arrives.

It depends on httpx and the standard library only, so the server's own test suite can drive it against a
running API without installing the terminal UI. Nothing here prints; it returns what the API answered,
or raises `ApiError` with the API's own words.
"""
from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from . import __version__

#: The web app's session cookie. `nc login` holds it only for the moment it takes to make a token.
SESSION_COOKIE = "nc_session"
#: The transport every client uses when it is not handed one: None is the network. The CLI's own tests set
#: an in-memory API here, so every command runs end to end without a socket.
TRANSPORT: httpx.BaseTransport | None = None


class ApiError(Exception):
    """The API said no, or could not be reached (`status` 0). `detail` is written to be read."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _detail(response: httpx.Response) -> str:
    """The API's own words: `detail` as a string, or a validation error's messages joined."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:300] or f"HTTP {response.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                where = ".".join(str(p) for p in item.get("loc", [])[1:])
                parts.append(f"{where}: {item.get('msg')}" if where else str(item.get("msg")))
        return "; ".join(parts) or f"HTTP {response.status_code}"
    return f"HTTP {response.status_code}"


@dataclass(frozen=True)
class Event:
    """One server-sent event: `activity`, `change`, `run`, `chat` or `reset`, with its JSON."""

    kind: str
    data: Any


def parse_events(lines: Iterator[str]) -> Iterator[Event]:
    """Server-sent events from their lines. Comments (the keep-alives) are skipped; a data line that is
    not JSON is handed on as text rather than ending the stream."""
    kind, data = "message", []
    for line in lines:
        if line == "":
            if data:
                raw = "\n".join(data)
                try:
                    yield Event(kind, json.loads(raw))
                except ValueError:
                    yield Event(kind, raw)
            kind, data = "message", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            kind = value
        elif field == "data":
            data.append(value)


def _q(ref: str) -> str:
    return quote(ref, safe="")


class Client:
    """One NeuroCode server, as one person. `transport` is for tests (httpx's ASGI or mock transports)."""

    def __init__(self, base_url: str, token: str | None = None, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        # X-NC-Client is what the API asks of a change that rides on the session cookie; a bearer token
        # does not need it, and `nc login` does ride on a cookie for one request.
        headers = {"User-Agent": f"nc/{__version__}", "X-NC-Client": "nc"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=self.base_url, headers=headers, transport=transport or TRANSPORT,
                                 timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── the one way a call is made ───────────────────────────────
    def call(self, method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None,
             timeout: float | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = self.http.request(method, path, json=json_body, params=clean or None,
                                         timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.TimeoutException as e:
            raise ApiError(0, f"{self.base_url} did not answer in time ({type(e).__name__}).") from e
        except httpx.TransportError as e:
            raise ApiError(0, f"Could not reach {self.base_url}: {e}") from e
        if response.status_code >= 400:
            raise ApiError(response.status_code, _detail(response))
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, params=params)

    def post(self, path: str, body: Any = None, *, timeout: float | None = None) -> Any:
        return self.call("POST", path, json_body=body, timeout=timeout)

    # ── who, and where ───────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        return self.get("/health")

    def status(self) -> dict[str, Any]:
        return self.get("/auth/status")

    def me(self) -> dict[str, Any]:
        return self.get("/auth/me")

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        """A browser-style sign-in. The session it opens is held in this client's cookies only."""
        return self.post("/auth/login", {"email": email, "password": password})

    def sign_out(self) -> None:
        self.post("/auth/logout")

    # ── tokens (from a signed-in session only) ───────────────────
    def tokens(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self.get("/tokens", limit=limit, offset=offset)

    def make_token(self, name: str, scopes: list[str] | None = None, days: int | None = None) -> dict[str, Any]:
        return self.post("/tokens", {"name": name, "scopes": scopes or [], "expiresInDays": days})

    def revoke_token(self, token_id: str) -> dict[str, Any]:
        return self.post(f"/tokens/{_q(token_id)}/revoke")

    # ── projects ─────────────────────────────────────────────────
    def projects(self) -> list[dict[str, Any]]:
        return self.get("/projects")

    def project(self, pid: str) -> dict[str, Any]:
        return self.get(f"/projects/{_q(pid)}")

    # ── sessions ─────────────────────────────────────────────────
    def sessions(self, project: str | None = None, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        return self.get("/sessions", project=project, limit=limit, offset=offset or None)

    def start_session(self, project: str, title: str = "") -> dict[str, Any]:
        return self.post("/sessions", {"projectId": project, "title": title[:80]})

    def session(self, ref: str, *, after: int = 0) -> dict[str, Any]:
        return self.get(f"/sessions/{_q(ref)}", after=after)

    def ask(self, ref: str, text: str, attachments: list[dict[str, str]] | None = None) -> dict[str, Any]:
        return self.post(f"/sessions/{_q(ref)}/messages", {"text": text, "attachments": attachments or []})

    def permit(self, ref: str, message_id: int, decision: str) -> dict[str, Any]:
        return self.post(f"/sessions/{_q(ref)}/permissions/{message_id}", {"decision": decision})

    def stop(self, ref: str) -> dict[str, Any]:
        return self.post(f"/sessions/{_q(ref)}/cancel")

    def compact(self, ref: str) -> dict[str, Any]:
        return self.post(f"/sessions/{_q(ref)}/compact", timeout=180)

    def to_plan(self, ref: str) -> dict[str, Any]:
        return self.post(f"/sessions/{_q(ref)}/to-plan", timeout=180)

    def export(self, ref: str, fmt: str = "md") -> dict[str, Any]:
        return self.get(f"/sessions/{_q(ref)}/export", format=fmt)

    def mentions(self, project: str, q: str) -> list[dict[str, Any]]:
        return self.get(f"/projects/{_q(project)}/mentions", q=q).get("items", [])

    def commands(self, project: str | None) -> list[dict[str, Any]]:
        return self.get("/extensions/commands", projectId=project).get("commands", [])

    # ── plans ────────────────────────────────────────────────────
    def compile(self, project: str, requirement: str) -> dict[str, Any]:
        # A compile is one model call, sometimes a slow one; the API answers when the plan is written.
        return self.post("/plans/compile", {"projectId": project, "requirement": requirement}, timeout=180)

    def plans(self, project: str | None = None, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        return self.get("/plans", project=project, limit=limit, offset=offset or None)

    def plan(self, ref: str) -> dict[str, Any]:
        return self.get(f"/plans/{_q(ref)}")

    def dispatch(self, ref: str, *, goal_budget: int | None = None, step_gate: bool = False) -> dict[str, Any]:
        return self.post(f"/plans/{_q(ref)}/dispatch", {"goalBudget": goal_budget, "stepGate": step_gate})

    def answer_question(self, ref: str, index: int, answer: str) -> dict[str, Any]:
        """`index` counts the plan's *open* questions from zero, exactly as the screen shows them."""
        return self.post(f"/plans/{_q(ref)}/questions/{index}", {"answer": answer})

    # ── gates ────────────────────────────────────────────────────
    def approvals(self, *, pending: bool = True, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.get("/approvals", status="pending" if pending else None, limit=limit, offset=offset or None)

    def decide(self, ref: str, decision: str, *, scope: str | None = None,
               answer: str | None = None) -> dict[str, Any]:
        body = {k: v for k, v in (("scope", scope), ("answer", answer)) if v is not None}
        return self.post(f"/approvals/{_q(ref)}/{decision}", body or None)

    # ── runs ─────────────────────────────────────────────────────
    def runs(self, project: str | None = None, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        return self.get("/runs", project=project, limit=limit, offset=offset or None)

    def run(self, ref: str, *, after: int = 0) -> dict[str, Any]:
        return self.get(f"/runs/{_q(ref)}", after=after)

    def diff(self, ref: str) -> dict[str, Any]:
        """The run's real patch, its stat and whether the server cut it at its 200 KB ceiling."""
        return self.get(f"/runs/{_q(ref)}/diff")

    def rework(self, ref: str, notes: str) -> dict[str, Any]:
        """Send the run back: the same plan again, as a new run told these notes and the review's
        findings. A signature the old run was waiting for is refused by the server, not here."""
        return self.post(f"/runs/{_q(ref)}/rework", {"notes": notes}, timeout=120)

    def review_again(self, ref: str) -> dict[str, Any]:
        return self.post(f"/runs/{_q(ref)}/review", timeout=120)

    def cancel_run(self, ref: str) -> dict[str, Any]:
        return self.post(f"/runs/{_q(ref)}/cancel", timeout=120)

    def discard(self, ref: str) -> dict[str, Any]:
        return self.post(f"/runs/{_q(ref)}/discard", timeout=120)

    def revert(self, ref: str, n: int, *, redo: bool = False) -> dict[str, Any]:
        return self.post(f"/runs/{_q(ref)}/steps/{n}/revert", {"redo": redo}, timeout=120)

    def merge(self, ref: str) -> dict[str, Any]:
        return self.post(f"/runs/{_q(ref)}/merge", timeout=120)

    def push(self, ref: str, remote: str | None = None) -> dict[str, Any]:
        return self.post(f"/runs/{_q(ref)}/push", {"remote": remote} if remote else None, timeout=120)

    # ── memory ───────────────────────────────────────────────────
    def memory(self, q: str = "", *, project: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        return self.get("/memory", q=q, project=project, category=category)

    def remember(self, title: str, body: str, *, project: str | None = None, category: str = "project",
                 confidence: str = "MEDIUM", reason: str = "") -> list[dict[str, Any]]:
        fact = {"title": title, "body": body, "category": category, "confidence": confidence, "reason": reason}
        return self.post("/memory/facts", {"projectId": project, "facts": [fact]})

    # ── the live stream ──────────────────────────────────────────
    def events(self) -> Iterator[Event]:
        """The live stream, as it arrives, until the caller stops reading. No read timeout: the server
        sends a keep-alive every 15 seconds, and a quiet stream is not a dead one."""
        try:
            with self.http.stream("GET", "/activity/stream",
                                  timeout=httpx.Timeout(10.0, read=None)) as response:
                if response.status_code >= 400:
                    response.read()
                    raise ApiError(response.status_code, _detail(response))
                yield from parse_events(response.iter_lines())
        except httpx.TransportError as e:
            raise ApiError(0, f"The live stream from {self.base_url} ended: {e}") from e


class Listener:
    """The live stream read on a thread of its own, into a queue — so a command can subscribe *before* it
    asks for something and still hear the first word of the answer.

    `ready` is set once the stream's first line (the server's `retry:`) has arrived, which is the moment
    the subscription exists on the server. Errors end up in the queue as an `Event("error", message)`.
    """

    def __init__(self, client: Client) -> None:
        self.client = client
        self.events: queue.Queue[Event] = queue.Queue()
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._response: httpx.Response | None = None
        self._thread = threading.Thread(target=self._read, name="nc-stream", daemon=True)

    def start(self, wait: float = 5.0) -> Listener:
        self._thread.start()
        self.ready.wait(wait)
        return self

    def stop(self) -> None:
        """Stop reading. Closing the response ends a read that is waiting on the next keep-alive."""
        self._stop.set()
        if self._response is not None:
            try:
                self._response.close()
            except Exception:                    # noqa: BLE001 — closing a stream mid-read may complain; it is closed
                pass

    def _read(self) -> None:
        try:
            with self.client.http.stream("GET", "/activity/stream",
                                         timeout=httpx.Timeout(10.0, read=None)) as response:
                self._response = response
                if response.status_code >= 400:
                    response.read()
                    self.events.put(Event("error", _detail(response)))
                    return
                lines = response.iter_lines()

                def marked() -> Iterator[str]:
                    for line in lines:
                        self.ready.set()
                        if self._stop.is_set():
                            return
                        yield line

                for event in parse_events(marked()):
                    if self._stop.is_set():
                        return
                    self.events.put(event)
                if not self._stop.is_set():
                    self.events.put(Event("error", "The live stream closed."))
        except Exception as e:                   # noqa: BLE001 — whatever ended the stream is said, never raised
            if not self._stop.is_set():
                self.events.put(Event("error", f"The live stream ended: {e}"))
        finally:
            self.ready.set()
