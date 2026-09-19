"""The MCP registry: checking a server, and calling one of its tools.

A check connects once, the way any MCP client would — `initialize`, `notifications/initialized`, then
`tools/list` (and `resources/list` / `prompts/list` when the server says it has them) — and writes down
what really happened: connected or not, how long the listing took, what the server offers, and the
reason when it failed. Before this, a server's status, latency and tools were numbers only the sample
workspace ever wrote.

Two transports:

* **stdio** launches the registered command, split like a shell would split it but never run through
  one, speaks newline-delimited JSON-RPC over its pipes, and always kills the whole process group
  afterwards — an `npx` wrapper that forked the real server must not outlive the check. Launching a
  program is the reason a stdio server is only checked once a person has trusted it.
* **http / sse** posts JSON-RPC to the URL, accepts an answer as JSON or as an event stream, and carries
  the `Mcp-Session-Id` the server hands back. A server that only speaks the older two-endpoint SSE
  transport refuses those posts, and the check records the refusal as its reason. The check is a request
  this server makes on someone's behalf, so it never follows a redirect, never goes through a proxy, and
  connects only to public addresses — checked after DNS resolution, on the address it actually connects
  to — unless the person asking may administer the workspace (a local MCP server on 127.0.0.1 is theirs to
  check). What it writes down as a reason is its own words, never what the server sent back: the reason
  is shown to everyone who can sign in.

The whole conversation has one ceiling, `TIMEOUT_S`. A check that hits it is an error with that reason.

**Calling a tool** (`call_tool`) opens the same conversation — the same two transports, the same address
guard — and asks `tools/call` instead of listing. It is only ever done on a server a person trusted and a
check found connected, for a tool that check listed, and only after the tool rules had their say
(`decide('mcp', 'server/tool')`); with no rule, the server's own default effect stands. What comes back
is capped and handed to the person who called it, never written down: a tool's answer may carry
anything its server can read.

The address guard lives here and nowhere else: `guarded_opener` is what the web fetcher uses too.
"""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import logging
import os
import shlex
import signal
import socket
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import McpServer, McpTool
from ..repositories import ActivityRepository, NotFound
from ..repositories.platform import McpRepository
from ..repositories.identity import AuditRepository
from .errors import Refused
from .identity import Person
from .tool_rules import Decision, decide

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0
PROTOCOL = "2025-06-18"
CLIENT = {"name": "NeuroCode", "version": "0.4.0"}
#: A tools/list answer can be large; a line longer than this is a server misbehaving, not a listing.
LINE_LIMIT = 8 * 1024 * 1024
MAX_PAGES = 10
MAX_TOOLS = 500
MAX_REASON = 500
STDERR_LINES = 20
#: What launching a program on this machine needs, beyond managing the registry: the same permission
#: trusting a server needs, because trusting one is exactly what allows it to be launched.
LAUNCH = "workspace:admin"
#: A tool call does work a listing does not, so it is given longer — and still one ceiling for the whole call.
CALL_TIMEOUT_S = 30.0
#: How much of a tool's answer is handed back. A tool that returns a database dump is answered with its start.
RESULT_CHARS = 20_000
#: How large the arguments a person types may be, serialised.
MAX_ARGUMENTS = 20_000

Status = Literal["connected", "error", "auth_required"]


class CheckFailed(Exception):
    def __init__(self, reason: str, status: Status = "error") -> None:
        super().__init__(reason)
        self.reason = reason[:MAX_REASON]
        self.status = status


@dataclass(slots=True)
class SeenTool:
    name: str
    description: str
    risk: str


@dataclass(slots=True)
class Probe:
    """What one check found. Everything but `status` and `error` is None when it did not get that far."""

    status: Status
    error: str = ""
    latency_ms: int | None = None
    resources: int | None = None
    prompts: int | None = None
    tools: list[SeenTool] = field(default_factory=list)


class Channel(Protocol):
    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...
    async def notify(self, method: str, params: dict[str, Any]) -> None: ...


def _risk(annotations: Any) -> str:
    """A tool's risk from what the server declares about it. The protocol's own defaults apply to a tool
    that declares nothing: it is not read-only, and it may destroy — so it is HIGH until it says otherwise."""
    hints = annotations if isinstance(annotations, dict) else {}
    if hints.get("readOnlyHint") is True:
        return "LOW"
    if hints.get("destructiveHint") is False:
        return "MEDIUM"
    return "HIGH"


def _answer(message: Any, wanted: int, method: str) -> dict[str, Any] | None:
    """The result carried by a JSON-RPC message when it is the response to `wanted`."""
    if not isinstance(message, dict) or message.get("id") != wanted:
        return None
    if isinstance(message.get("error"), dict):
        error = message["error"]
        raise CheckFailed(f"{method} was refused: {error.get('message') or error.get('code')}")
    result = message.get("result")
    return result if isinstance(result, dict) else {}


async def _listed(channel: Channel, method: str, key: str, cap: int) -> list[dict[str, Any]]:
    """Every page of a list, up to a ceiling a misbehaving server cannot push past."""
    found: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        page = await channel.request(method, {"cursor": cursor} if cursor else {})
        found += [item for item in page.get(key) or [] if isinstance(item, dict)]
        cursor = page.get("nextCursor") if isinstance(page.get("nextCursor"), str) else None
        if not cursor or len(found) >= cap:
            break
    return found[:cap]


async def _hello(channel: Channel) -> dict[str, Any]:
    """The handshake every conversation opens with. Returns what the server says it offers."""
    hello = await channel.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                 "clientInfo": CLIENT})
    offers = hello.get("capabilities") if isinstance(hello.get("capabilities"), dict) else {}
    await channel.notify("notifications/initialized", {})
    return offers


async def converse(channel: Channel) -> Probe:
    """The handshake and the listing, over whichever transport the channel is."""
    offers = await _hello(channel)

    started = time.monotonic()
    if "tools" in offers:
        listed = await _listed(channel, "tools/list", "tools", MAX_TOOLS)
    else:
        # Nothing to list, but the round trip is still worth measuring, and ping is what every server answers.
        await channel.request("ping", {})
        listed = []
    latency = round((time.monotonic() - started) * 1000)

    tools: dict[str, SeenTool] = {}
    for tool in listed:
        name = tool.get("name")
        if isinstance(name, str) and 0 < len(name) <= 120 and name not in tools:
            tools[name] = SeenTool(name, str(tool.get("description") or "")[:2000], _risk(tool.get("annotations")))
    resources = len(await _listed(channel, "resources/list", "resources", 10_000)) if "resources" in offers else 0
    prompts = len(await _listed(channel, "prompts/list", "prompts", 10_000)) if "prompts" in offers else 0
    return Probe(status="connected", latency_ms=latency, resources=resources, prompts=prompts,
                 tools=list(tools.values()))


# ── stdio ────────────────────────────────────────────────────────
class StdioChannel:
    def __init__(self, process: asyncio.subprocess.Process, stderr: deque[str]) -> None:
        self.process = process
        self.stderr = stderr
        self.next_id = 0

    def _gone(self, doing: str) -> CheckFailed:
        said = " ".join(line.strip() for line in self.stderr if line.strip())
        code = self.process.returncode
        return CheckFailed(f"The server exited{f' with code {code}' if code is not None else ''} before "
                           f"{doing}{f': {said}' if said else '.'}")

    async def _send(self, message: dict[str, Any]) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.is_closing():
            raise self._gone("it could be written to")
        try:
            stdin.write(json.dumps(message).encode() + b"\n")
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            raise self._gone("it could be written to") from e

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        wanted = self.next_id
        await self._send({"jsonrpc": "2.0", "id": wanted, "method": method, "params": params})
        stdout = self.process.stdout
        assert stdout is not None                  # opened with stdout=PIPE below
        while True:
            try:
                line = await stdout.readline()
            except ValueError as e:                # a line past LINE_LIMIT
                raise CheckFailed(f"The server's answer to {method} was too large to read.") from e
            if not line:
                await self._reaped()
                raise self._gone(f"answering {method}")
            try:
                message = json.loads(line)
            except ValueError:
                continue                           # a log line on stdout; servers do it
            if isinstance(message, dict) and "method" in message and "id" in message:
                # The server asking us something (roots, sampling). This client offers none of it.
                await self._send({"jsonrpc": "2.0", "id": message["id"],
                                  "error": {"code": -32601, "message": "Not offered by this client"}})
                continue
            found = _answer(message, wanted, method)
            if found is not None:
                return found

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _reaped(self) -> None:
        """Give an exiting process a moment to report its code, so the reason can carry it."""
        try:
            await asyncio.wait_for(self.process.wait(), timeout=0.5)
        except TimeoutError:
            log.debug("mcp: stdout closed but the process is still running")


async def _drain(stream: asyncio.StreamReader, into: deque[str]) -> None:
    """Keep reading stderr, so a chatty server never blocks on a full pipe, and keep its last lines.

    A line longer than the reader's limit makes readline raise; the reader has already dropped what it
    read, so draining carries on with the rest — a check must not die of what a server logged."""
    while True:
        try:
            line = await stream.readline()
        except ValueError:
            into.append("(a line too long to keep)")
            continue
        if not line:
            return
        into.append(line.decode(errors="replace")[:300])


@dataclass(slots=True)
class Called:
    """What one tool call came back with. `error` is this client's own words when the call itself failed;
    `is_error` is the tool saying its work failed, with `text` saying why."""

    ok: bool
    text: str = ""
    is_error: bool = False
    truncated: bool = False
    ms: int | None = None
    error: str = ""


def _content_text(result: dict[str, Any]) -> str:
    """A tools/call result as text: its text blocks as they are, anything else named for what it is."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} · {block.get('mimeType') or 'unknown type'}]")
        elif kind == "resource" and isinstance(block.get("resource"), dict):
            resource = block["resource"]
            parts.append(str(resource["text"]) if isinstance(resource.get("text"), str)
                         else f"[resource · {resource.get('uri') or 'no uri'}]")
        elif kind == "resource_link":
            parts.append(f"[link · {block.get('name') or ''} {block.get('uri') or ''}]".replace("  ", " "))
    if not parts and "structuredContent" in result:
        parts.append(json.dumps(result["structuredContent"], indent=2, ensure_ascii=False))
    return "\n".join(parts)


def invoke(tool: str, arguments: dict[str, Any]):
    """A conversation that calls one tool, for `_check_stdio` / `_check_http` to hold."""
    async def talk(channel: Channel) -> Called:
        await _hello(channel)
        started = time.monotonic()
        result = await channel.request("tools/call", {"name": tool, "arguments": arguments})
        ms = round((time.monotonic() - started) * 1000)
        text = _content_text(result)
        return Called(ok=True, text=text[:RESULT_CHARS], is_error=result.get("isError") is True,
                      truncated=len(text) > RESULT_CHARS, ms=ms)
    return talk


async def _check_stdio(command: str, talk: Any = converse, timeout: float | None = None) -> Any:
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise CheckFailed(f"The command could not be read: {e}.") from e
    if not argv:
        raise CheckFailed("There is no command to launch.")
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=LINE_LIMIT)
    except FileNotFoundError as e:
        raise CheckFailed(f"There is no program called {argv[0]} on this machine.") from e
    except PermissionError as e:
        raise CheckFailed(f"{argv[0]} is not allowed to run: {e.strerror}.") from e

    stderr: deque[str] = deque(maxlen=STDERR_LINES)
    assert process.stderr is not None
    draining = asyncio.create_task(_drain(process.stderr, stderr))
    try:
        async with asyncio.timeout(TIMEOUT_S if timeout is None else timeout):
            return await talk(StdioChannel(process, stderr))
    finally:
        # The group, not just the process: `start_new_session` made the server its own group leader,
        # so whatever it spawned goes with it.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass                                   # it had already exited, group and all
        await process.wait()
        draining.cancel()
        try:
            await draining
        except asyncio.CancelledError:
            pass
        except Exception:                          # noqa: BLE001 — stderr is a courtesy; it never fails a check
            log.warning("mcp: reading a server's stderr failed", exc_info=True)


# ── http ─────────────────────────────────────────────────────────
@dataclass(slots=True)
class Reply:
    status: int
    headers: Mapping[str, str]
    body: bytes


def _public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """An address on the internet at large: not loopback, private, link-local (where cloud metadata
    lives), shared, reserved or multicast. An IPv4 address wrapped in IPv6 is judged as itself."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


#: What the guard says when a name resolves somewhere it may not go, unless a caller says otherwise.
ADMIN_ONLY = "Only a workspace admin can check a server there."


def _connector(allow_private: bool, refusal: str = ADMIN_ONLY):
    """What an http connection uses to open its socket: resolve the name, refuse the whole name when any
    address it resolves to is not public (unless allowed), then connect to one of the addresses just
    vetted. Connecting to the vetted address, not the name, is what stops a name that resolves to a
    public address when asked and a private one when used."""
    def connect(address: tuple[str, int], timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,  # noqa: SLF001
                source_address: Any = None, *_: Any, **__: Any) -> socket.socket:
        host, port = address
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        vetted: list[str] = []
        for *_rest, sockaddr in found:
            ip = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
            if not allow_private and not _public(ip):
                raise CheckFailed(f"{host} is a local or private address. {refusal}".strip())
            vetted.append(str(sockaddr[0]))
        failed: OSError = OSError(f"{host} has no address")
        for ip_text in vetted:
            try:
                return socket.create_connection((ip_text, port), timeout, source_address)
            except OSError as e:
                failed = e
        raise failed
    return connect


class _GuardedHttp(urllib.request.HTTPHandler):
    def __init__(self, allow_private: bool, refusal: str = ADMIN_ONLY) -> None:
        super().__init__()
        self.allow_private = allow_private
        self.refusal = refusal

    def _connection(self, host: str, **kwargs: Any) -> http.client.HTTPConnection:
        made = http.client.HTTPConnection(host, **kwargs)
        made._create_connection = _connector(self.allow_private, self.refusal)  # type: ignore[attr-defined]  # noqa: SLF001
        return made

    def http_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(self._connection, req)


class _GuardedHttps(urllib.request.HTTPSHandler):
    def __init__(self, allow_private: bool, refusal: str = ADMIN_ONLY) -> None:
        super().__init__()
        self.allow_private = allow_private
        self.refusal = refusal

    def _connection(self, host: str, **kwargs: Any) -> http.client.HTTPSConnection:
        made = http.client.HTTPSConnection(host, **kwargs)
        made._create_connection = _connector(self.allow_private, self.refusal)  # type: ignore[attr-defined]  # noqa: SLF001
        return made

    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(self._connection, req, context=self._context)  # type: ignore[attr-defined]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would follow a 30x by sending a GET somewhere else — somewhere nobody registered."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise CheckFailed(f"The server answered with a redirect (HTTP {code}). A check never follows one; "
                          f"register the address it points to instead.")


def guarded_opener(allow_private: bool, *, redirects: urllib.request.HTTPRedirectHandler | None = None,
                   refusal: str = ADMIN_ONLY) -> urllib.request.OpenerDirector:
    """An opener that connects only to vetted addresses, through no proxy, and follows no redirect on its
    own. `redirects` decides what a 30x does (by default it fails the request); `refusal` is the sentence
    added when an address is refused. The web fetcher is the other caller."""
    # No proxy: a proxy would make the connection, and the address it reached would go unvetted.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), redirects or _NoRedirect(),
                                       _GuardedHttp(allow_private, refusal), _GuardedHttps(allow_private, refusal))


def _opener(allow_private: bool) -> urllib.request.OpenerDirector:
    return guarded_opener(allow_private)


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float, allow_private: bool = False) -> Reply:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with _opener(allow_private).open(request, timeout=timeout) as r:
            return Reply(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(LINE_LIMIT))
    except urllib.error.HTTPError as e:            # a status is an answer: its code and body say why
        return Reply(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b"")
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, CheckFailed):        # refused by the guard, below urllib's own wrapping
            raise reason from e
        raise CheckFailed(f"Could not reach {url}: {reason}.") from e


def _messages(reply: Reply) -> list[Any]:
    """The JSON-RPC messages in an answer, whether it came as JSON or as an event stream."""
    text = reply.body.decode(errors="replace")
    if reply.headers.get("content-type", "").startswith("text/event-stream"):
        found: list[Any] = []
        for event in text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.split("\n") if line.startswith("data:"))
            if data:
                try:
                    found.append(json.loads(data))
                except ValueError:
                    continue                       # a keep-alive or a comment, not a message
        return found
    try:
        parsed = json.loads(text)
    except ValueError as e:
        # Not what it said: a reason is shown to everyone, and an answer that is not JSON-RPC may be anything.
        raise CheckFailed("The server answered with something that is not JSON.") from e
    return parsed if isinstance(parsed, list) else [parsed]


class HttpChannel:
    def __init__(self, url: str, deadline: float, allow_private: bool = False) -> None:
        self.url = url
        self.deadline = deadline
        self.allow_private = allow_private
        self.session_id: str | None = None
        self.version: str | None = None
        self.next_id = 0

    async def _exchange(self, message: dict[str, Any], what: str) -> Reply:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        left = max(0.1, self.deadline - time.monotonic())
        reply = await asyncio.to_thread(_post, self.url, json.dumps(message).encode(), headers, left,
                                        self.allow_private)
        if reply.status in (401, 403):
            raise CheckFailed(f"The server asked for credentials (HTTP {reply.status}) at {what}.",
                              status="auth_required")
        if reply.status >= 400:
            raise CheckFailed(f"The server refused {what} with HTTP {reply.status}.")
        return reply

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        wanted = self.next_id
        reply = await self._exchange({"jsonrpc": "2.0", "id": wanted, "method": method, "params": params}, method)
        if method == "initialize":
            self.session_id = reply.headers.get("mcp-session-id") or None
        for message in _messages(reply):
            found = _answer(message, wanted, method)
            if found is not None:
                if method == "initialize" and isinstance(found.get("protocolVersion"), str):
                    self.version = found["protocolVersion"]
                return found
        raise CheckFailed(f"The server's answer to {method} did not carry a response to it.")

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._exchange({"jsonrpc": "2.0", "method": method, "params": params}, method)


async def _check_http(url: str, allow_private: bool, talk: Any = converse, timeout: float | None = None) -> Any:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CheckFailed(f"{url} is not an http or https address.")
    ceiling = TIMEOUT_S if timeout is None else timeout
    async with asyncio.timeout(ceiling):
        return await talk(HttpChannel(url.strip(), time.monotonic() + ceiling, allow_private))


async def probe(transport: str, command: str, *, allow_private: bool = False) -> Probe:
    """Check one server and say what happened. Never raises for anything the server did.

    `allow_private` lets an http check reach loopback, private and link-local addresses; only a person
    who may administer the workspace is given it."""
    try:
        if transport == "stdio":
            return await _check_stdio(command)
        return await _check_http(command, allow_private)
    except CheckFailed as failed:
        return Probe(status=failed.status, error=failed.reason)
    except TimeoutError:
        return Probe(status="error", error=f"No complete answer within {TIMEOUT_S:.0f} s.")


async def call_tool(transport: str, command: str, tool: str, arguments: dict[str, Any], *,
                    allow_private: bool = False) -> Called:
    """Call one tool and say what came back. Never raises for anything the server did, or failed to do."""
    try:
        if transport == "stdio":
            return await _check_stdio(command, invoke(tool, arguments), CALL_TIMEOUT_S)
        return await _check_http(command, allow_private, invoke(tool, arguments), CALL_TIMEOUT_S)
    except CheckFailed as failed:
        return Called(ok=False, error=failed.reason)
    except TimeoutError:
        return Called(ok=False, error=f"No complete answer within {CALL_TIMEOUT_S:.0f} s.")


# ── the registry ─────────────────────────────────────────────────
class McpService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.servers = McpRepository(session)
        self.activity = ActivityRepository(session)

    async def _server(self, server_id: str) -> McpServer:
        server = await self.servers.get(server_id)
        if server is None:
            raise NotFound(f"MCP server {server_id}")
        return server

    async def trust(self, server_id: str, trusted: bool, who: Person) -> McpServer:
        """Trusting a server is what lets its command be launched, so it needs what launching needs."""
        who.must(LAUNCH, "trust an MCP server")
        server = await self._server(server_id)
        if server.untrusted is not trusted:
            raise Refused(f"{server.id} is already {'trusted' if trusted else 'untrusted'}.")
        server.untrusted = not trusted
        await self.session.flush()
        await self.activity.record(
            actor=who.name, actor_kind="human", action="MCP server trusted" if trusted else "MCP server untrusted",
            detail=f"{server.id} · {'its command may be launched to check it' if trusted else 'its command will not be launched'}",
            level="warn" if trusted else "info")
        return server

    async def check(self, server_id: str, who: Person) -> McpServer:
        server = await self._server(server_id)
        if server.transport == "stdio":
            who.must(LAUNCH, "launch an MCP server's command")
            if server.untrusted:
                raise Refused(f"{server.id} is untrusted, so its command is never launched. Trust it first.")
        found = await probe(server.transport, server.command, allow_private=who.can(LAUNCH))

        server.status, server.checked_at, server.last_error = found.status, utcnow(), found.error
        server.latency_ms, server.resources, server.prompts = found.latency_ms, found.resources, found.prompts
        # Replaced, never merged: what a server listed at an earlier check is not what it offers now.
        # Cleared and flushed first, because a tool listed again keeps its name, and its name is the key.
        server.tools.clear()
        await self.session.flush()
        server.tools.extend(McpTool(name=t.name, description=t.description, risk=t.risk) for t in found.tools)
        await self.session.flush()

        await self.activity.record(
            actor=who.name, actor_kind="human", action="MCP server checked",
            detail=(f"{server.id} · connected · {len(found.tools)} tools · {found.latency_ms} ms"
                    if found.status == "connected" else f"{server.id} · {found.status} · {found.error}"),
            level="ok" if found.status == "connected" else "warn")
        return server

    async def call_tool(self, server_id: str, tool: str, arguments: dict[str, Any], who: Person, *,
                        project_id: str | None = None, ip: str = "") -> dict[str, Any]:
        """Call a tool a check listed, on a server a person trusted, once the tool rules allow it.

        Refused, in words, before anything is sent: an untrusted server, one the last check did not
        find connected, a tool it did not list, a rule or the server's default effect that denies it.
        With no rule the person pressing the button is the one who was asked, so it goes ahead. Once
        sent, a failure is an answer (200, `ok: false`), the way a failed check is."""
        server = await self._server(server_id)
        if server.untrusted:
            raise Refused(f"{server.id} is untrusted, so none of its tools is called. Trust it first.")
        if server.transport == "stdio":
            who.must(LAUNCH, "launch an MCP server's command")
        if server.checked_at is None or server.status != "connected":
            raise Refused(f"{server.id} was not connected at its last check. Check it first.")
        listed = {t.name: t for t in server.tools}
        if tool not in listed:
            raise Refused(f"{server.id} did not list a tool called {tool} at its last check.", status=404)
        if not isinstance(arguments, dict):
            raise Refused("A tool's arguments are a JSON object.", status=422)
        if len(json.dumps(arguments)) > MAX_ARGUMENTS:
            raise Refused(f"The arguments are larger than {MAX_ARGUMENTS // 1000} KB.", status=422)

        decision = await decide(self.session, "mcp", f"{server.id}/{tool}", project_id)
        if decision.rule_id is None and server.default_effect == "deny":
            decision = Decision("deny", None, f"No tool rule covers {server.id}/{tool}, and {server.id}'s "
                                              f"default effect is deny.")
        if decision.action == "deny":
            raise Refused(decision.why, status=403)

        called = await call_tool(server.transport, server.command, tool, arguments,
                                 allow_private=who.can(LAUNCH))
        outcome = ("failed: " + called.error if not called.ok else
                   "the tool reported an error" if called.is_error else "ok")
        await self.activity.record(
            actor=who.name, actor_kind="human", action="MCP tool called",
            detail=f"{server.id}/{tool} · {listed[tool].risk} risk · {outcome}"
                   + (f" · {called.ms} ms" if called.ms is not None else ""),
            level="ok" if called.ok and not called.is_error else "warn", project_id=project_id)
        # A tool may change the world outside this app, so who called what is audited. What it was
        # given and what it answered are not: either may carry what the person typed or the server read.
        await AuditRepository(self.session).record(
            action="mcp.call", user_id=who.id, target=f"{server.id}/{tool}",
            detail={"risk": listed[tool].risk, "ok": called.ok, "isError": called.is_error,
                    "rule": decision.rule_id}, ip=ip)
        return {"server": server.id, "tool": tool, "ok": called.ok, "isError": called.is_error,
                "text": called.text, "truncated": called.truncated, "ms": called.ms, "error": called.error,
                "decision": decision.json()}
