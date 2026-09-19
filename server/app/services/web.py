"""The web: fetching one page, and searching through a provider someone configured.

**Fetching** is a request this server makes on someone's behalf — a person today, a session or a run
next — so it is held to the same guard as checking an MCP server, and uses that guard rather than a
second copy of it (`mcp.guarded_opener`): http and https only, no proxy, and a connection only to an
address that is public once the name is resolved — never loopback, private, link-local (where cloud
metadata lives) or shared. Unlike an MCP check, nobody may lift that for a page: an admin who wants a
local page read can read it. Redirects are followed by hand, at most `MAX_HOPS`, and every hop is
checked again — its scheme, its address (by the guard, when it connects) and the tool rules — so an
allowed page cannot bounce the request somewhere a rule denies. What comes back is capped in bytes and
in time, and HTML is read down to its text: the title, the headings, the paragraphs, without scripts,
styles or navigation.

**Searching** goes through the Brave Search API — one GET with the key in a header, answering JSON, the
simplest of the keyed providers to call — with the key kept in the secrets file beside the model keys,
set on Settings → Web, and only ever reported masked. With no key, search says so and does nothing.
It answers titles, links and the provider's snippets; reading a result is a fetch of its own.

Both ask `tool_rules.decide` first — `web_search` with the query, `web_fetch` with the URL — and refuse
on deny. With no rule, the person who asked is the one who answered; research asks only when the person
who started it ticked the web as a source. Every search and fetch that happened is in the activity log.
Neither is written to the usage ledger: the ledger counts model calls and what they cost, and a search's
price depends on the plan the key belongs to, which nothing here can know.
"""
from __future__ import annotations

import asyncio
import codecs
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import ActivityRepository, AuditRepository
from ..repositories.platform import ToolRuleRepository
from ..secrets import Secrets
from . import mcp
from .errors import Refused
from .identity import Person
from .tool_rules import Decision, weigh

PROVIDER = "brave"
PROVIDER_LABEL = "Brave Search API"
#: Where a key is made: the Brave Search API dashboard.
PROVIDER_KEYS = "https://api-dashboard.search.brave.com/app/keys"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
SECRET = "brave_api_key"
NOT_CONFIGURED = ("Web search is not configured. Add a Brave Search API key in Settings → Web — the free plan "
                  "takes a minute.")

TIMEOUT_S = 15.0
MAX_HOPS = 5
#: What is read of a page at most. A page larger than this is read from its start, and says it was cut.
MAX_BYTES = 2 * 1024 * 1024
#: What of a page's text is handed back at most.
MAX_TEXT = 60_000
MAX_URL = 2000
MAX_QUERY = 400
MAX_RESULTS = 10
USER_AGENT = "NeuroCode/0.4 (+a person's request; reads one page)"
#: What a fetch reads as text. A PDF or an image is not a page this reads; it says so instead.
READABLE = ("text/", "application/xhtml+xml", "application/json", "application/xml", "application/rss+xml",
            "application/atom+xml", "application/ld+json")
REDIRECTS = (301, 302, 303, 307, 308)
REFUSAL = "NeuroCode never fetches a page there."


class FetchFailed(Exception):
    """A fetch or a search that did not happen, in words for the screen. `status` is what HTTP makes of it."""

    def __init__(self, reason: str, status: int = 502) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


# ── HTML, read down to its text ──────────────────────────────────
SKIP = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "canvas", "nav", "footer",
                  "aside", "form", "button", "select", "head"})
BLOCK = frozenset({"p", "div", "section", "article", "main", "header", "br", "hr", "li", "ul", "ol", "table",
                   "tr", "td", "th", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd",
                   "figcaption", "summary", "details"})
VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track",
                  "wbr"})


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.og_title = ""
        self.skipping: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            found = dict(attrs)
            if found.get("property") == "og:title" and found.get("content"):
                self.og_title = found["content"] or ""
            return
        if tag == "title":
            self.in_title = True
            return
        if tag in SKIP and tag not in VOID:
            self.skipping.append(tag)
        if tag in BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skipping:
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "li" and not self.skipping:
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif self.skipping and self.skipping[-1] == tag:
            self.skipping.pop()
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif not self.skipping:
            self.parts.append(data)


def readable(markup: str) -> tuple[str, str]:
    """(title, text) from an HTML page: its words in reading order, one paragraph a line."""
    reader = _Reader()
    try:
        reader.feed(markup)
        reader.close()
    except Exception:  # noqa: BLE001 — a page that breaks the parser still gave up what it had read
        pass
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(reader.parts).split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line not in ("", "-", "#")))
    title = re.sub(r"\s+", " ", reader.title or reader.og_title).strip()
    return title, text.strip()


# ── fetching ─────────────────────────────────────────────────────
class _Stop(urllib.request.HTTPRedirectHandler):
    """Hand every 30x back as it is, so the fetch can check where it points before it goes there."""

    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def _checked(url: str) -> str:
    text = url.strip()
    if not text or len(text) > MAX_URL:
        raise FetchFailed(f"A web address is 1 to {MAX_URL} characters.", 422)
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchFailed("Only http and https addresses are fetched.", 422)
    if parsed.username or parsed.password:
        raise FetchFailed("An address that carries a user name or password is never fetched.", 422)
    return text


def _charset(content_type: str) -> str:
    """The page's declared charset when Python knows it; utf-8 otherwise, rather than failing the read."""
    found = re.search(r"charset=([\w.-]+)", content_type, re.I)
    try:
        return codecs.lookup(found.group(1)).name if found else "utf-8"
    except LookupError:
        return "utf-8"


def fetch_page(url: str, permit: Callable[[str], Decision] | None = None, *,
               timeout: float | None = None) -> dict[str, Any]:
    """Read one page: `{url, requested, status, title, text, bytes, truncated, contentType, hops}`.

    Blocking; callers hand it to a worker thread. `permit` is asked about every address the fetch is
    about to open, the first and each redirect's, and a deny stops it there."""
    deadline = time.monotonic() + (TIMEOUT_S if timeout is None else timeout)
    opener = mcp.guarded_opener(False, redirects=_Stop(), refusal=REFUSAL)
    requested = current = _checked(url)
    for hop in range(MAX_HOPS + 1):
        if permit is not None:
            decision = permit(current)
            if decision.action == "deny":
                raise FetchFailed(decision.why if hop == 0 else f"It redirected to {current}, and {decision.why}",
                                  403)
        request = urllib.request.Request(current, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"})
        left = deadline - time.monotonic()
        if left <= 0:
            raise FetchFailed(f"No complete answer within {TIMEOUT_S:.0f} s.", 504)
        try:
            response = opener.open(request, timeout=left)
        except urllib.error.HTTPError as answered:
            if answered.code in REDIRECTS:
                location = answered.headers.get("Location") if answered.headers else None
                answered.close()
                if not location:
                    raise FetchFailed(f"The page answered HTTP {answered.code} with nowhere to go.") from answered
                current = _checked(urllib.parse.urljoin(current, location))
                continue
            response = answered            # an error page is still an answer: its status says what it is
        except mcp.CheckFailed as refused:     # the guard, refusing the address before a byte was sent
            raise FetchFailed(refused.reason, 422) from refused
        except (urllib.error.URLError, OSError) as failed:
            reason = getattr(failed, "reason", failed)
            if isinstance(reason, mcp.CheckFailed):
                raise FetchFailed(reason.reason, 422) from failed
            if isinstance(reason, TimeoutError) or isinstance(failed, TimeoutError):
                raise FetchFailed(f"No complete answer within {TIMEOUT_S:.0f} s.", 504) from failed
            host = urllib.parse.urlsplit(current).hostname
            raise FetchFailed(f"Could not reach {host}: {reason}.") from failed
        with response:
            return _read(response, requested, current, hop, deadline)
    raise FetchFailed(f"More than {MAX_HOPS} redirects; the page was not read.")


def _read(response: Any, requested: str, final: str, hops: int, deadline: float) -> dict[str, Any]:
    status = int(getattr(response, "status", None) or getattr(response, "code", 0) or 0)
    content_type = (response.headers.get("Content-Type") or "") if response.headers else ""
    kind = content_type.split(";")[0].strip().lower()
    if kind and not kind.startswith(READABLE):
        raise FetchFailed(f"The page is {kind}, not something NeuroCode reads as text.", 415)
    body = bytearray()
    while len(body) <= MAX_BYTES:
        if time.monotonic() > deadline:
            raise FetchFailed(f"No complete answer within {TIMEOUT_S:.0f} s.", 504)
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        body += chunk
    cut = len(body) > MAX_BYTES
    raw = bytes(body[:MAX_BYTES]).decode(_charset(content_type), errors="replace")
    if kind in ("text/html", "application/xhtml+xml") or (not kind and "<html" in raw[:2000].lower()):
        title, text = readable(raw)
    else:
        title, text = "", raw.strip()
    return {"url": final, "requested": requested, "status": status, "title": title[:300],
            "text": text[:MAX_TEXT], "bytes": len(body[:MAX_BYTES]), "truncated": cut or len(text) > MAX_TEXT,
            "contentType": kind or "unknown", "hops": hops}


# ── searching ────────────────────────────────────────────────────
def _plain(snippet: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", snippet)).strip()


def brave_search(key: str, query: str, count: int = 5, *, timeout: float | None = None) -> list[dict[str, str]]:
    """One search through the Brave Search API: `[{title, url, snippet}]`, http(s) links only. Blocking."""
    params = urllib.parse.urlencode({"q": query, "count": max(1, min(count, MAX_RESULTS))})
    request = urllib.request.Request(f"{BRAVE_URL}?{params}", headers={
        "Accept": "application/json", "X-Subscription-Token": key, "User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_Stop())
    try:
        with opener.open(request, timeout=TIMEOUT_S if timeout is None else timeout) as r:
            body = r.read(MAX_BYTES)
    except urllib.error.HTTPError as answered:
        answered.close()
        if answered.code in (401, 403):
            raise FetchFailed(f"Brave Search refused the key (HTTP {answered.code}). Check it in Settings → Web.",
                              502) from answered
        if answered.code == 429:
            raise FetchFailed("Brave Search says this key is over its rate limit for now (HTTP 429).", 429) \
                from answered
        raise FetchFailed(f"Brave Search answered HTTP {answered.code}.") from answered
    except (urllib.error.URLError, OSError) as failed:
        raise FetchFailed(f"Could not reach Brave Search: {getattr(failed, 'reason', failed)}.") from failed
    try:
        found = json.loads(body)
    except ValueError as e:
        raise FetchFailed("Brave Search answered with something that is not JSON.") from e
    results = ((found.get("web") or {}).get("results") or []) if isinstance(found, dict) else []
    out: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        link = str(item.get("url") or "")
        if urllib.parse.urlsplit(link).scheme not in ("http", "https"):
            continue
        out.append({"title": _plain(str(item.get("title") or link))[:300], "url": link[:MAX_URL],
                    "snippet": _plain(str(item.get("description") or ""))[:600]})
    return out[:MAX_RESULTS]


# ── the service ──────────────────────────────────────────────────
class WebService:
    def __init__(self, session: AsyncSession, secrets: Secrets) -> None:
        self.session = session
        self.secrets = secrets
        self.activity = ActivityRepository(session)

    def key(self) -> str | None:
        return self.secrets.get(SECRET)

    def status(self, *, masked: bool) -> dict[str, Any]:
        """Whether search can answer. The masked key only for someone who may change it."""
        key = self.key()
        return {"provider": PROVIDER, "label": PROVIDER_LABEL, "configured": bool(key), "keysAt": PROVIDER_KEYS,
                **({"keyMask": Secrets.mask(key)} if masked else {}),
                "fetch": {"timeoutS": TIMEOUT_S, "maxBytes": MAX_BYTES, "maxHops": MAX_HOPS}}

    async def set_key(self, key: str, who: Person, ip: str = "") -> dict[str, Any]:
        who.must("workspace:admin", "set the web search key")
        text = key.strip()
        await asyncio.to_thread(self.secrets.set, SECRET, text or None)
        await AuditRepository(self.session).record(action="web.update", user_id=who.id, target="Web search",
                                                   detail={"key": "set" if text else "removed"}, ip=ip)
        return self.status(masked=True)

    async def search(self, query: str, *, actor: str, project_id: str | None = None,
                     actor_kind: str = "human", count: int = 5) -> dict[str, Any]:
        """Search, once the rules allow it. Raises Refused with the words for the screen."""
        text = " ".join(query.split())
        if not text or len(text) > MAX_QUERY:
            raise Refused(f"A search is 1 to {MAX_QUERY} characters.", status=422)
        rules = await ToolRuleRepository(self.session).applicable("web_search", project_id)
        decision = weigh(rules, "web_search", text)
        if decision.action == "deny":
            raise Refused(decision.why, status=403)
        key = self.key()
        if not key:
            raise Refused(NOT_CONFIGURED, status=409)
        try:
            results = await asyncio.to_thread(brave_search, key, text, count)
        except FetchFailed as failed:
            raise Refused(failed.reason, status=failed.status) from failed
        await self.activity.record(actor=actor, actor_kind=actor_kind, action="Web searched",
                                   detail=f"“{text[:120]}” · {len(results)} results · {PROVIDER_LABEL}",
                                   project_id=project_id)
        return {"query": text, "results": results, "decision": decision.json()}

    async def fetch(self, url: str, *, actor: str, project_id: str | None = None,
                    actor_kind: str = "human") -> dict[str, Any]:
        """Fetch one page, once the rules allow it and every redirect it takes."""
        rules = await ToolRuleRepository(self.session).applicable("web_fetch", project_id)
        try:
            first = weigh(rules, "web_fetch", _checked(url))
            page = await asyncio.to_thread(fetch_page, url, lambda at: weigh(rules, "web_fetch", at))
        except FetchFailed as failed:
            raise Refused(failed.reason, status=failed.status) from failed
        await self.activity.record(
            actor=actor, actor_kind=actor_kind, action="Web page fetched",
            detail=f"{page['url'][:200]} · HTTP {page['status']} · {max(1, round(page['bytes'] / 1024))} KB"
                   + (" · cut" if page["truncated"] else ""), project_id=project_id)
        return {**page, "decision": first.json()}
