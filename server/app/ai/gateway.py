"""The one door to language models — now with several lanes behind it.

Every AI feature goes through here, so keys, routing, time-outs, failure handling and the fallback
live in one place. A **lane** is a provider and a model together (see `lanes.py`). The router picks
the lane: free lanes first, the paid one as a backstop, the local model last, skipping any lane whose
key is missing, whose free allowance is spent for the minute or the day, or that refused its key.

Two things follow from having many lanes. A call that fails moves to the next lane instead of falling
straight to the offline rules. And agents working at the same time are *spread* across lanes, so four
agents are four providers answering at once, not four requests queued behind one rate limit.

Slow counts as failed. Every call carries a **wall-clock budget** — one lane's, and the whole chain's
— because a socket timeout is per read and a provider that dribbles a byte at a time resets it for
ever. And a lane that fails for its own reasons rather than this request's — no answer, a refused
connection, a 429, a 5xx — is **rested** for a minute instead of being chosen first again on the very
next call, and says so on Models & Router rather than still reading "ready".

A call may also **stream**: given `on_delta`, the provider's answer and its reasoning arrive as they are
written, and `stop` can end it halfway. Only the real provider calls stream — a stand-in, or a lane with
no streaming shape, answers whole, and the result says it did (`streamed`). Either way the ledger gets
one line, with the tokens the provider reported: prompt, answer, cached and reasoning.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from ..secrets import Secrets
from . import lanes
from .ledger import Ledger, Remembered

log = logging.getLogger(__name__)
from .lanes import CHAT, PLAN, REVIEW, WRITE, Lane

T = TypeVar("T")
__all__ = ["CHAT", "PLAN", "REVIEW", "WRITE", "Gateway", "LaneTooSlow", "NoModel", "OutOfBudget", "Provider",
           "ProviderError", "Reply", "Result", "Stopped", "clip", "extract_json"]

# "auto" spreads over every usable lane; "free" refuses to spend money; "local" never leaves this
# machine; "rules" asks no model at all; a lane's own id pins every call to it.
PREFERENCES = ("auto", "free", "local", "rules", *lanes.IDS)


@dataclass
class Provider:
    id: str      # the lane: groq | cerebras | gemini | mistral | openrouter | github | deepseek | ollama | rules
    model: str   # the name the UI shows


@dataclass
class Result(Generic[T]):
    data: T
    provider: Provider
    ms: int
    fallback: str | None = None
    #: What the model reasoned before it answered, when the lane returned it — never synthesised.
    reasoning: str = ""
    #: The tokens the provider reported: in, out, cached (of in), reasoning (of out).
    usage: dict[str, int] = field(default_factory=dict)
    #: Whether the answer arrived as it was written, or whole.
    streamed: bool = False
    #: Milliseconds from the call to the first word of the answer after the reasoning, when streamed.
    thought_ms: int | None = None

    def meta(self) -> dict[str, Any]:
        return {"provider": self.provider.id, "model": self.provider.model, "ms": self.ms}


class NoModel(RuntimeError):
    """Nothing can answer: no lane has a key, and no local model is pulled."""


class ProviderError(RuntimeError):
    """A provider answered with an HTTP error. 401 or 403 means the key itself is bad."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class LaneTooSlow(TimeoutError):
    """This lane spent its whole wall-clock budget and had not finished.

    `urlopen`'s timeout is per socket operation, not a deadline: a provider that sends one byte every
    thirty seconds resets it every time and is never cut off. The budget below is the real limit, and
    a lane that hits it is sick rather than wrong — the caller rests it and asks the next lane."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"it had not finished after {seconds:g} seconds")
        self.seconds = seconds


class OutOfBudget(RuntimeError):
    """The model spent its whole token budget reasoning and wrote no answer.

    Named, because it used to surface as "the answer holds no JSON object" — true, and no help at all.
    The fix is on Models & Router: less thinking for that feature, or a lane that thinks less."""

    def __init__(self, model: str, budget: int, reply: Reply | None = None) -> None:
        super().__init__(f"{model} thought past its budget: it spent all {budget:,} tokens reasoning and "
                         "wrote no answer. Lower this feature's thinking on Models & Router.")
        #: What came back — its tokens were spent and are ledgered like any others.
        self.reply = reply


class Stopped(RuntimeError):
    """A person stopped the answer while it was being written. `reply` is what had arrived by then."""

    def __init__(self, reply: Reply) -> None:
        super().__init__("stopped by a person")
        self.reply = reply


#: How long one lane may take to answer, and how long a whole chain of lanes may take, before the
#: caller stops waiting. A person watching a session tires long before a socket does.
LANE_SECONDS = 90.0
CHAIN_SECONDS = 180.0
#: With less than this left of the chain's budget, the next lane is not worth opening a socket for:
#: it would be cut off before it finished, and the person would have waited for nothing.
LEAST_SECONDS = 1.0
#: How long a lane rests after a failure that is about the lane rather than about the request — a
#: timeout, a refused connection, a 429, a 5xx. Short, because a provider that went soft usually
#: comes back; long enough that a chain of three lanes does not pick the sick one first every turn.
COOLDOWN_SECONDS = 60.0


class _Budget:
    """One lane's wall clock. Every read is bounded by what is left of it, so the total really is.

    Handed around rather than a plain number of seconds: the point is the deadline, and a per-socket
    timeout re-derived from it is the only way `urllib` can be made to respect one."""

    def __init__(self, seconds: float | None = None) -> None:
        self.seconds = float(seconds) if seconds else LANE_SECONDS
        self.until = time.monotonic() + self.seconds

    def left(self) -> float:
        left = self.until - time.monotonic()
        if left <= 0:
            raise LaneTooSlow(self.seconds)
        return left


def _read(response: Any, clock: _Budget) -> bytes:
    """The whole body, in pieces, so a provider that dribbles is cut off at the deadline rather than
    resetting the socket timeout with every byte it sends."""
    out = bytearray()
    while True:
        clock.left()
        piece = response.read(65_536)
        if not piece:
            return bytes(out)
        out += piece


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], clock: _Budget) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=clock.left()) as r:
            return json.loads(_read(r, clock))
    except urllib.error.HTTPError as e:  # the body says why: a bad key, no balance, an unknown model
        raise ProviderError(e.code, e.read()[:200].decode(errors="replace")) from e


Usage = dict[str, int]      # {"in": prompt, "out": completion, "cached": of in, "reasoning": of out}
Answer = tuple[str, Usage]


@dataclass
class Reply:
    """What a provider call gives back: the answer, the tokens it counted, and what it reasoned."""

    text: str
    usage: Usage = field(default_factory=dict)
    reasoning: str = ""
    finish: str = ""
    streamed: bool = False
    thought_ms: int | None = None


#: Request fields a lane may refuse by name, and so are dropped and asked again without: not every free
#: model takes JSON mode, and a lane pointed at a model that does not reason refuses its effort setting.
OPTIONAL = ("response_format", "stream_options", "reasoning_effort", "thinking")


def _refusal(e: ProviderError, body: dict[str, Any]) -> str | None:
    """The optional field a 400 named, if it named one this request carries."""
    if e.status != 400:
        return None
    return next((name for name in OPTIONAL if name in body and name in e.body), None)


def _counted(usage: dict[str, Any] | None) -> Usage:
    """The provider's own token counts. DeepSeek reports cache hits as `prompt_cache_hit_tokens` (at the
    top, and inside `prompt_tokens_details`); OpenAI-style lanes as `prompt_tokens_details.cached_tokens`;
    reasoning as `completion_tokens_details.reasoning_tokens`. Nothing reported is 0, never a guess."""
    usage = usage or {}
    prompt = usage.get("prompt_tokens_details") or {}
    completion = usage.get("completion_tokens_details") or {}
    cached = usage.get("prompt_cache_hit_tokens") or prompt.get("prompt_cache_hit_tokens") \
        or prompt.get("cached_tokens") or 0
    return {"in": int(usage.get("prompt_tokens") or 0), "out": int(usage.get("completion_tokens") or 0),
            "cached": int(cached), "reasoning": int(completion.get("reasoning_tokens") or 0)}


def _body(messages: list[dict[str, str]], cfg: dict[str, Any], stream: bool) -> dict[str, Any]:
    level, thinks = cfg.get("thinking", "off"), cfg.get("thinks", "")
    body: dict[str, Any] = {"model": cfg["model"], "messages": messages, "temperature": 0.2,
                            "max_tokens": lanes.max_tokens(thinks, level),
                            "response_format": {"type": "json_object"}, **lanes.thinking_params(thinks, level)}
    if stream:
        body.update(stream=True, stream_options={"include_usage": True})
    return body


def _answered(reply: Reply, cfg: dict[str, Any], budget: int) -> Reply:
    """An empty answer cut off at the budget is a model that thought past it — said so, by name."""
    if not reply.text.strip() and reply.finish == "length":
        raise OutOfBudget(cfg["model"], budget, reply)
    return reply


def call_openai(messages: list[dict[str, str]], cfg: dict[str, Any]) -> Reply:
    """Chat completions, the shape every lane here speaks — DeepSeek, Groq, Cerebras, Mistral,
    OpenRouter, GitHub Models, and Gemini through its OpenAI-compatible endpoint."""
    body = _body(messages, cfg, stream=False)
    url = f"{cfg['baseUrl']}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['key']}"}
    clock = _Budget(cfg.get("seconds"))
    for _ in range(len(OPTIONAL) + 1):
        try:
            out = _post(url, body, headers, clock)
            break
        except ProviderError as e:  # a field this lane does not take: ask again without it
            if (name := _refusal(e, body)) is None:
                raise
            body = {k: v for k, v in body.items() if k != name}
    choice = out["choices"][0]
    message = choice.get("message") or {}
    # DeepSeek names it `reasoning_content`; Groq and OpenRouter `reasoning`.
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    return _answered(Reply(message.get("content") or "", _counted(out.get("usage")),
                           reasoning if isinstance(reasoning, str) else "", choice.get("finish_reason") or ""),
                     cfg, body.get("max_tokens", 0))


#: ("answer" | "reasoning", the words that just arrived), or ("restart", the next lane) after a failure.
Delta = Callable[[str, str], None]


def _lines(url: str, payload: dict[str, Any], headers: dict[str, str], clock: _Budget):
    """The response's lines as they arrive — a context manager, so a stop closes the socket."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        return urllib.request.urlopen(req, timeout=clock.left())
    except urllib.error.HTTPError as e:
        raise ProviderError(e.code, e.read()[:200].decode(errors="replace")) from e


class _Collect:
    """Gathers a streamed answer, times the thinking, and hands every piece to `on_delta`."""

    def __init__(self, on_delta: Delta, stop: Callable[[], bool] | None) -> None:
        self.on_delta, self.stop = on_delta, stop
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.t0 = time.monotonic()
        self.thought_ms: int | None = None
        self.usage: Usage = {}
        self.finish = ""

    def add(self, answer: str, reasoning: str) -> None:
        if reasoning:
            self.reasoning.append(reasoning)
            self.on_delta("reasoning", reasoning)
        if answer:
            if self.reasoning and self.thought_ms is None:
                self.thought_ms = _ms(self.t0)
            self.text.append(answer)
            self.on_delta("answer", answer)

    def reply(self) -> Reply:
        if self.reasoning and self.thought_ms is None:     # it reasoned and never answered
            self.thought_ms = _ms(self.t0)
        return Reply("".join(self.text), self.usage, "".join(self.reasoning), self.finish, True, self.thought_ms)

    def check(self) -> None:
        if self.stop is not None and self.stop():
            raise Stopped(self.reply())


def stream_openai(messages: list[dict[str, str]], cfg: dict[str, Any], on_delta: Delta,
                  stop: Callable[[], bool] | None = None) -> Reply:
    """The same call with `stream: true`: server-sent chunks, the reasoning first when there is any,
    and the usage in the last chunk (`stream_options.include_usage`)."""
    body = _body(messages, cfg, stream=True)
    url = f"{cfg['baseUrl']}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['key']}", "Accept": "text/event-stream"}
    clock = _Budget(cfg.get("seconds"))
    for _ in range(len(OPTIONAL) + 1):
        try:
            response = _lines(url, body, headers, clock)
            break
        except ProviderError as e:
            if (name := _refusal(e, body)) is None:
                raise
            body = {k: v for k, v in body.items() if k != name}
    got = _Collect(on_delta, stop)
    with response:
        for raw in response:
            got.check()
            clock.left()           # a lane that trickles for ever is a lane that never finishes
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue                      # a keep-alive comment, or the blank line between events
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("usage"):
                got.usage = _counted(chunk["usage"])
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                got.add(delta.get("content") or "", thought if isinstance(thought, str) else "")
                got.finish = choice.get("finish_reason") or got.finish
    return _answered(got.reply(), cfg, body.get("max_tokens", 0))


def embed_openai(texts: list[str], cfg: dict[str, Any]) -> tuple[list[list[float]], int]:
    """Vectors for a batch of texts, in the shape every OpenAI-compatible lane speaks."""
    out = _post(f"{cfg['baseUrl']}/embeddings", {"model": cfg["embed"], "input": texts},
                {"Authorization": f"Bearer {cfg['key']}"}, _Budget(cfg.get("seconds") or 120))
    return [row["embedding"] for row in out["data"]], (out.get("usage") or {}).get("prompt_tokens", 0)


def embed_ollama(texts: list[str], cfg: dict[str, Any]) -> tuple[list[list[float]], int]:
    out = _post(f"{cfg['url']}/api/embed", {"model": cfg["embed"], "input": texts}, {},
                _Budget(cfg.get("seconds") or 300))
    return out["embeddings"], out.get("prompt_eval_count", 0)


def call_ollama(messages: list[dict[str, str]], cfg: dict[str, Any]) -> Reply:
    # The local lane gets longer by default: it loads a model off this machine's disk before it starts.
    body = _post(f"{cfg['url']}/api/chat", {"model": cfg["model"], "messages": messages, "format": "json",
                                            "stream": False, "options": {"temperature": 0.2}}, {},
                 _Budget(cfg.get("seconds") or 300))
    message = body.get("message") or {}
    # A thinking model on Ollama puts its reasoning in `message.thinking`; it is shown when it is there.
    return Reply(message.get("content") or "", {"in": body.get("prompt_eval_count", 0),
                                                "out": body.get("eval_count", 0)},
                 message.get("thinking") or "", body.get("done_reason") or "")


def stream_ollama(messages: list[dict[str, str]], cfg: dict[str, Any], on_delta: Delta,
                  stop: Callable[[], bool] | None = None) -> Reply:
    """Ollama streams one JSON object a line, and counts the tokens in the last one (`done: true`)."""
    clock = _Budget(cfg.get("seconds") or 300)
    response = _lines(f"{cfg['url']}/api/chat", {"model": cfg["model"], "messages": messages, "format": "json",
                                                 "stream": True, "options": {"temperature": 0.2}}, {}, clock)
    got = _Collect(on_delta, stop)
    with response:
        for raw in response:
            got.check()
            clock.left()
            if not raw.strip():
                continue
            chunk = json.loads(raw)
            message = chunk.get("message") or {}
            got.add(message.get("content") or "", message.get("thinking") or "")
            if chunk.get("done"):
                got.usage = {"in": int(chunk.get("prompt_eval_count") or 0), "out": int(chunk.get("eval_count") or 0)}
                got.finish = chunk.get("done_reason") or ""
                break
    return got.reply()


# A provider returns its text and the tokens it counted. A stand-in (a test) may return just the text.
CALLS: dict[str, Callable[[list[dict[str, str]], dict[str, Any]], Reply | Answer | str]] = {
    **{lane.id: call_openai for lane in lanes.LANES if lane.api == "openai"},
    **{lane.id: call_ollama for lane in lanes.LANES if lane.api == "ollama"}}

#: The streaming shape of each real call. A lane streams only while CALLS still holds its real call: a
#: stand-in put there answers whole, which is exactly what a lane that cannot stream does.
STREAMS: dict[str, tuple[Callable[..., Any], Callable[..., Reply]]] = {
    **{lane.id: (call_openai, stream_openai) for lane in lanes.LANES if lane.api == "openai"},
    **{lane.id: (call_ollama, stream_ollama) for lane in lanes.LANES if lane.api == "ollama"}}

# The same, for embeddings. Only some lanes serve them; a test may stand in for any of them.
EMBEDS: dict[str, Callable[[list[str], dict[str, Any]], tuple[list[list[float]], int]]] = {
    lane.id: (embed_openai if lane.api == "openai" else embed_ollama) for lane in lanes.LANES if lane.embed}


def _split(out: Reply | Answer | str) -> Reply:
    if isinstance(out, Reply):
        return out
    return Reply(out[0], out[1] or {}) if isinstance(out, tuple) else Reply(out)


def extract_json(raw: str, trim: bool = True) -> dict[str, Any]:
    """The first JSON object in a model's answer, even when it is wrapped in prose or a code fence.
    `trim` bounds what we store; code an agent wrote is kept whole and bounded by the runtime instead."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the answer holds no JSON object")
    parsed = json.loads(raw[start:end + 1])
    return clip(parsed) if trim else parsed


def clip(v: Any) -> Any:
    """Bound what a model can make us store: strings to 1,500 characters, lists to 16 items."""
    if isinstance(v, str):
        return v.strip()[:1500]
    if isinstance(v, list):
        return [clip(x) for x in v[:16]]
    if isinstance(v, dict):
        return {k: clip(x) for k, x in v.items()}
    return v


def _fp(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def _int(value: Any) -> int:
    """A count as a whole number, whatever a provider sent. A token count arriving as `"1200"`, as a
    float, or not at all is not worth losing a ledger line over — and `int()` alone raises on the
    first two. What cannot be read as a number at all is no count, which is zero."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _out_of_time(reason: str | None) -> str:
    """What to say when the lanes left are not worth starting: the chain's budget is spent.

    The reason the last lane gave is kept in front of it — "groq failed (…)" and then "and there was
    no time left to ask another" is the whole story; either half alone is a puzzle."""
    spent = f"no lane answered within {CHAIN_SECONDS:g} seconds"
    return f"{reason}, and there was no time left to ask another lane" if reason else spent


def _sick(e: Exception) -> str | None:
    """Why this failure is the *lane's* and not this request's — or None when it is the request's.

    The difference decides whether the lane rests. A model that wrote invalid JSON, or thought past
    its budget, will very likely do better on the next prompt; a lane that did not answer at all, or
    that is rate-limiting the key, or whose provider returned a 5xx, will do the same thing again in
    a second's time, and choosing it first every turn is how one soft provider makes every session
    slow. A 4xx that is not a 429 is about what we sent, so the lane keeps its place."""
    if isinstance(e, ProviderError):
        if e.status == 429:
            return "it is rate-limiting this key"
        return f"its provider answered HTTP {e.status}" if e.status >= 500 else None
    if isinstance(e, LaneTooSlow):
        return f"it did not answer within {e.seconds:g} seconds"
    if isinstance(e, (TimeoutError, OSError)):   # URLError, a refused connection, a dropped socket
        return "it is not answering"
    return None


class Gateway:
    def __init__(self, ledger: Ledger, secrets: Secrets) -> None:
        # Wrapped, not used raw: routing asks the same handful of settings dozens of times to answer
        # one question, and every one of them was a round trip (see `ledger.Remembered`).
        self.store, self.secrets = Remembered(ledger), secrets
        self._rejected: dict[str, str] = {}          # lane id → fingerprint of the key it refused
        self._resting: dict[str, tuple[float, str]] = {}   # lane id → until when, and why it is resting
        self._recent: dict[str, list[float]] = {}    # lane id → when it was called, this last minute
        self._ollama_seen: tuple[float, str, bool] = (-1e9, "", False)
        self._turn = 0                               # so two agents starting together get two lanes

    # ── configuration ────────────────────────────────────────────
    def preference(self) -> str:
        """The environment (NEUROCODE_COMPILER) wins over the workspace setting; tests and CI rely on it."""
        pref = os.environ.get("NEUROCODE_COMPILER") or self.store.setting("ai.preference", "auto")
        return pref if pref in PREFERENCES else "auto"

    def lane(self, lane_id: str) -> Lane | None:
        return lanes.settled(self.store, lane_id)

    def lanes(self) -> list[Lane]:
        return lanes.every(self.store)

    def config(self, lane_id: str) -> dict[str, Any]:
        """What the provider call needs. Named `config` since the runtime's stand-ins patch by lane id."""
        lane = self.lane(lane_id)
        return lanes.config(lane, self.secrets) if lane else {"id": lane_id, "model": "", "key": None}

    def thinking(self, feature: str) -> str:
        """How hard this feature asks a model to think: off | low | high | max (see `lanes.thinking`)."""
        return lanes.thinking(self.store, feature)

    def thinking_levels(self) -> dict[str, str]:
        """Every feature's thinking level, as it stands — for Models & Router."""
        return {feature: self.thinking(feature) for feature in lanes.THINKING_FEATURES}

    def _asked(self, lane_id: str, feature: str, seconds: float | None = None) -> dict[str, Any]:
        """The provider call's configuration for this feature: the lane's, how hard to think, and how
        long it may take — what is left of the chain's budget, so three lanes cannot each spend it."""
        lane = self.lane(lane_id)
        return {**self.config(lane_id), "thinking": self.thinking(feature),
                "thinks": lane.thinks if lane else "", "seconds": seconds}

    def deepseek(self) -> dict[str, Any]:
        cfg = self.config("deepseek")
        return {"key": cfg["key"], "model": cfg["model"], "baseUrl": cfg["baseUrl"]}

    def ollama(self) -> dict[str, Any]:
        cfg = self.config("ollama")
        return {"url": cfg["url"], "model": cfg["model"]}

    def key_source(self, lane_id: str = "deepseek") -> str | None:
        lane = self.lane(lane_id)
        return lanes.key_source(lane, self.secrets) if lane else None

    def rejected(self, lane_id: str = "deepseek") -> bool:
        """Did this lane refuse the key it holds now? A new key gets a fresh chance."""
        lane = self.lane(lane_id)
        key = lanes.key_of(lane, self.secrets) if lane else None
        return bool(key) and self._rejected.get(lane_id) == _fp(key)

    def forget_rejection(self, lane_id: str | None = None) -> None:
        """A key or an address was just changed, so give the lane a clean slate — both the refused key
        and the rest it was put on. The thing that made it sick may be exactly what was edited."""
        if lane_id:
            self._rejected.pop(lane_id, None)
            self._resting.pop(lane_id, None)
        else:
            self._rejected.clear()
            self._resting.clear()

    def resting(self, lane_id: str) -> str | None:
        """Why this lane is being left alone for a moment, in words, or None when it is not.

        The breaker only ever knew about a rejected key, so a lane that had gone quiet or was
        answering 5xx was chosen first again on the very next call and every screen still called it
        ready. This is the other half: a lane that failed for its own reasons says so until it is
        worth trying again."""
        until, why = self._resting.get(lane_id, (0.0, ""))
        left = until - time.monotonic()
        if left <= 0:
            self._resting.pop(lane_id, None)
            return None
        return f"{why} — trying it again in {max(1, round(left))}s"

    def _rest(self, lane_id: str, why: str) -> None:
        self._resting[lane_id] = (time.monotonic() + COOLDOWN_SECONDS, why)

    def ollama_ready(self) -> bool:
        """Is an Ollama server up with the configured model pulled? Remembered for 30 seconds."""
        cfg = self.ollama()
        at, seen_for, ok = self._ollama_seen
        signature = cfg["url"] + cfg["model"]
        if seen_for == signature and time.monotonic() - at < 30:
            return ok
        try:
            with urllib.request.urlopen(f"{cfg['url']}/api/tags", timeout=0.4) as r:
                names = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
            ok = cfg["model"] in names or f"{cfg['model']}:latest" in names
        except (OSError, ValueError):
            ok = False
        self._ollama_seen = (time.monotonic(), signature, ok)
        return ok

    # ── what a lane has spent ────────────────────────────────────
    def _this_minute(self, lane_id: str) -> int:
        cutoff = time.monotonic() - 60
        recent = [t for t in self._recent.get(lane_id, []) if t > cutoff]
        self._recent[lane_id] = recent
        return len(recent)

    def _today(self, lane_id: str) -> int:
        return self.store.calls_today(lane_id)

    def spent(self, lane: Lane) -> dict[str, int]:
        return {"minute": self._this_minute(lane.id), "today": self._today(lane.id)}

    def why_not(self, lane: Lane) -> str | None:
        """Why this lane cannot take the next call — or None, meaning it can."""
        if not lanes.enabled(self.store, lane.id):
            return "switched off"
        # Before the key and the allowances, because a lane that is not answering is not answering
        # whatever its key says — and this is the one the admin screen most needs to see.
        if (rest := self.resting(lane.id)) is not None:
            return rest
        if lane.api == "ollama":
            return None if self.ollama_ready() else "no model pulled on this machine"
        if lane.needs_key and not lanes.key_of(lane, self.secrets):
            return "no API key"
        if self.rejected(lane.id):
            return "the key was refused"
        if lane.rpm and self._this_minute(lane.id) >= lane.rpm:
            return f"{lane.rpm} calls this minute — its free allowance"
        if lane.rpd and self._today(lane.id) >= lane.rpd:
            return f"{lane.rpd} calls today — its free allowance"
        return None

    # ── routing ──────────────────────────────────────────────────
    def _allowed(self, lane: Lane) -> bool:
        want = self.preference()
        if want == "rules":
            return False
        if want == "free":
            return lane.free
        if want == "local":
            return lane.api == "ollama"
        return want == "auto" or want == lane.id

    def chain(self, role: str | None = None, lane: str | None = None, avoid: str | None = None,
              limit: int = 3) -> list[Lane]:
        """The lanes that could answer this call, best first: the one asked for, then the lanes that
        are good at this role, then the rest. A lane the caller wants to avoid goes last, not away —
        one tired lane is still better than no answer."""
        open_lanes = [x for x in self.lanes() if self._allowed(x) and self.why_not(x) is None]
        if not open_lanes:
            return []
        cursor = self._turn % len(open_lanes)
        rotated = open_lanes[cursor:] + open_lanes[:cursor]      # so parallel agents do not all start at Groq

        def rank(x: Lane) -> tuple[int, int, int]:
            return (0 if x.id == lane else 1,
                    1 if x.id == avoid else 0,
                    0 if role is None or role in x.good_at else 1)
        return sorted(rotated, key=rank)[:limit]

    def pick(self, role: str | None = None) -> Provider | None:
        """The lane that would answer now, or None when only the offline rules are available."""
        chain = self.chain(role=role, limit=1)
        return Provider(chain[0].id, chain[0].model) if chain else None

    def spread(self, n: int, role: str | None = WRITE) -> list[str | None]:
        """One lane for each of `n` agents about to work at the same time, different wherever it can
        be: this is what makes parallel agents actually parallel instead of a queue."""
        open_lanes = [x.id for x in self.lanes() if self._allowed(x) and self.why_not(x) is None
                      and (role is None or role in x.good_at)] or \
                     [x.id for x in self.lanes() if self._allowed(x) and self.why_not(x) is None]
        if not open_lanes:
            return [None] * n
        out = [open_lanes[(self._turn + i) % len(open_lanes)] for i in range(n)]
        self._turn += n
        return out

    def status(self) -> dict[str, Any]:
        p = self.pick()
        out: dict[str, Any] = {"provider": p.id, "model": p.model} if p else {"provider": "rules", "model": "no model"}
        open_now = [x for x in self.lanes() if self._allowed(x) and self.why_not(x) is None]
        out["lanes"] = len(open_now)
        if p is None:
            if self.rejected("deepseek") and self.preference() in ("auto", "deepseek"):
                out["note"] = "DeepSeek rejected the API key. Set a valid key in Admin → AI providers."
            elif self.preference() not in ("rules",):
                out["note"] = "No lane can answer: add a free key, or pull an Ollama model."
        return out

    def report(self) -> list[dict[str, Any]]:
        """Every lane, for the admin screen: what it is, whether it can answer, what it has spent."""
        out = []
        for lane in self.lanes():
            blocked = self.why_not(lane)
            out.append({**lanes.describe(lane), "enabled": lanes.enabled(self.store, lane.id),
                        "hasKey": bool(lanes.key_of(lane, self.secrets)),
                        "keyMask": Secrets.mask(lanes.key_of(lane, self.secrets)),
                        "keySource": lanes.key_source(lane, self.secrets),
                        "rejected": self.rejected(lane.id), "ready": blocked is None, "blocked": blocked,
                        "allowed": self._allowed(lane), "spent": self.spent(lane)})
        return out

    # ── calls ────────────────────────────────────────────────────
    def _call(self, provider: Provider, messages: list[dict[str, str]], feature: str,
              on_delta: Delta | None, stop: Callable[[], bool] | None,
              seconds: float | None = None) -> Reply:
        """The provider call itself: streamed when asked for and the lane's real call is in place."""
        cfg = self._asked(provider.id, feature, seconds)
        real, streaming = STREAMS.get(provider.id, (None, None))
        if on_delta is not None and streaming is not None and CALLS.get(provider.id) is real:
            return streaming(messages, cfg, on_delta, stop)
        return _split(CALLS[provider.id](messages, cfg))

    def _try(self, provider: Provider, messages: list[dict[str, str]], parse: Callable[[str], T], feature: str,
             actor: str | None, project: str | None, agent: str = "", run_id: str | None = None,
             on_delta: Delta | None = None, stop: Callable[[], bool] | None = None,
             seconds: float | None = None) -> Result[T] | str:
        """One lane, one attempt. Returns the answer, or the reason it could not be used.

        A stop is not a failure of the lane, so it is not handed to the next one: it is ledgered with the
        tokens that had been reported (usually none — the count comes in the last chunk) and raised."""
        t0, reply = time.monotonic(), Reply("")
        self._recent.setdefault(provider.id, []).append(time.monotonic())
        try:
            reply = self._call(provider, messages, feature, on_delta, stop, seconds)
            data = parse(reply.text)
        except Stopped as stopped:
            self._record(feature, provider, False, _ms(t0), stopped.reply.usage, actor, project,
                         "stopped by a person", agent, run_id)
            raise
        except Exception as e:  # network, key, quota, malformed JSON, schema: unusable either way
            if isinstance(e, ProviderError) and e.status in (401, 403):
                key = lanes.key_of(self.lane(provider.id), self.secrets) if self.lane(provider.id) else None
                if key:
                    self._rejected[provider.id] = _fp(key)
            elif (ill := _sick(e)) is not None:
                self._rest(provider.id, ill)
            if isinstance(e, OutOfBudget) and e.reply is not None:
                reply = e.reply
            reason = f"{provider.model} failed ({type(e).__name__}: {str(e)[:200]})"
            self._record(feature, provider, False, _ms(t0), reply.usage, actor, project, reason, agent, run_id)
            return reason
        ms = _ms(t0)
        self._record(feature, provider, True, ms, reply.usage, actor, project, agent=agent, run_id=run_id)
        return Result(data, provider, ms, reasoning=reply.reasoning, usage=dict(reply.usage),
                      streamed=reply.streamed, thought_ms=reply.thought_ms)

    def run(self, messages: list[dict[str, str]], parse: Callable[[str], T], fallback: Callable[[], T], *,
            offline: str = "offline rules", feature: str = "compile", actor: str | None = None,
            project: str | None = None, role: str | None = None, lane: str | None = None,
            avoid: str | None = None, agent: str = "", run_id: str | None = None) -> Result[T]:
        """Ask the best lane and validate its answer. A lane that fails hands the call to the next one;
        when every lane is spent or silent, the rules stand in and say so. Every attempt is ledgered."""
        t0, reason = time.monotonic(), None
        until = t0 + CHAIN_SECONDS
        for candidate in self.chain(role=role, lane=lane, avoid=avoid):
            left = until - time.monotonic()
            if left <= LEAST_SECONDS:
                reason = _out_of_time(reason)
                break
            out = self._try(Provider(candidate.id, candidate.model), messages, parse, feature, actor,
                            project, agent, run_id, seconds=min(LANE_SECONDS, left))
            if isinstance(out, str):
                reason = out
                continue
            return out
        t1 = time.monotonic()
        result = Result(fallback(), Provider("rules", offline), _ms(t0), reason)
        self._record(feature, result.provider, True, _ms(t1), {}, actor, project, agent=agent, run_id=run_id)
        return result

    def ask(self, messages: list[dict[str, str]], parse: Callable[[str], T], *, feature: str = "agent",
            actor: str | None = None, project: str | None = None, role: str | None = None,
            lane: str | None = None, avoid: str | None = None, agent: str = "",
            run_id: str | None = None, on_delta: Delta | None = None,
            stop: Callable[[], bool] | None = None) -> Result[T]:
        """For work with no honest offline version — writing code, reviewing a diff. A lane answers,
        or the next lane does, or this raises; nothing is ever invented to fill the gap.

        With `on_delta`, the answer and its reasoning are handed over as they arrive. A lane that fails
        halfway leaves what it wrote behind — the next lane starts afresh, and `on_delta("restart", …)`
        says so, so a screen never shows two lanes' words stitched into one answer."""
        chain = self.chain(role=role, lane=lane, avoid=avoid)
        if not chain:
            raise NoModel("No model is configured. Add a free key in Admin → AI providers, or pull an Ollama model.")
        reason, until = "", time.monotonic() + CHAIN_SECONDS
        for n, candidate in enumerate(chain):
            left = until - time.monotonic()
            if left <= LEAST_SECONDS:
                reason = _out_of_time(reason)
                break
            if n and on_delta is not None:
                on_delta("restart", candidate.id)
            out = self._try(Provider(candidate.id, candidate.model), messages, parse, feature, actor,
                            project, agent, run_id, on_delta, stop, min(LANE_SECONDS, left))
            if isinstance(out, str):
                reason = out
                continue
            return out
        raise ProviderError(502, reason or "every lane failed")

    # ── embeddings ───────────────────────────────────────────────
    def embed_lane(self) -> Lane | None:
        """The lane that makes vectors: the first open one that serves an embedding model."""
        for lane in self.lanes():
            if lane.embed and lane.id in EMBEDS and self._allowed(lane) and self.why_not(lane) is None:
                return lane
        return None

    def embed(self, texts: list[str], *, project: str | None = None, actor: str | None = None,
              lane: Lane | None = None) -> tuple[list[list[float]], str, str]:
        """Vectors for these texts, or an empty list when no lane makes them — retrieval still works
        without vectors, it is only lexical then, and it says so rather than pretending."""
        chosen = lane or self.embed_lane()
        if chosen is None or not texts:
            return [], "", ""
        cfg = {**lanes.config(chosen, self.secrets), "embed": chosen.embed}
        provider, t0 = Provider(chosen.id, chosen.embed), time.monotonic()
        self._recent.setdefault(chosen.id, []).append(time.monotonic())
        try:
            vectors, tokens = EMBEDS[chosen.id](texts, cfg)
        except Exception as e:
            if isinstance(e, ProviderError) and e.status in (401, 403) and cfg["key"]:
                self._rejected[chosen.id] = _fp(cfg["key"])
            elif (ill := _sick(e)) is not None:
                self._rest(chosen.id, ill)
            self._record("embed", provider, False, _ms(t0), {}, actor, project, f"{type(e).__name__}: {str(e)[:160]}")
            raise
        self._record("embed", provider, True, _ms(t0), {"in": tokens, "out": 0}, actor, project)
        return vectors, chosen.embed, chosen.id

    def test(self, lane_id: str, actor: str | None = None) -> dict[str, Any]:
        """One tiny round trip, so the admin screen can say whether a lane really answers."""
        if lane_id == "rules":
            return {"ok": True, "ms": 0, "detail": "The offline rules need no model."}
        lane = self.lane(lane_id)
        if lane is None:
            return {"ok": False, "ms": 0, "detail": f"There is no lane called {lane_id}."}
        cfg = lanes.config(lane, self.secrets)
        if lane.needs_key and not cfg["key"]:
            return {"ok": False, "ms": 0, "detail": "No API key is set."}
        provider, t0, usage = Provider(lane_id, cfg["model"]), time.monotonic(), {}
        try:
            reply = _split(CALLS[lane_id](
                [{"role": "system", "content": 'Reply with the JSON object {"ok": true} and nothing else.'},
                 {"role": "user", "content": "ping"}], {**cfg, "thinking": "off", "thinks": lane.thinks}))
            usage = reply.usage
            extract_json(reply.text)
            # It just answered, so whatever it was resting from is over: an admin pressing Test is the
            # fastest honest way to bring a lane back before its minute is up.
            self._rejected.pop(lane_id, None)
            self._resting.pop(lane_id, None)
            self._record("test", provider, True, _ms(t0), usage, actor, None)
            return {"ok": True, "ms": _ms(t0), "detail": f"{cfg['model']} answered."}
        except Exception as e:  # report whatever went wrong; this is a diagnostic
            if isinstance(e, ProviderError) and e.status in (401, 403) and cfg["key"]:
                self._rejected[lane_id] = _fp(cfg["key"])
            elif (ill := _sick(e)) is not None:
                self._rest(lane_id, ill)
            detail = (str(e) or type(e).__name__)[:200]
            self._record("test", provider, False, _ms(t0), usage, actor, None, detail)
            return {"ok": False, "ms": _ms(t0), "detail": detail}

    def _record(self, feature: str, provider: Provider, ok: bool, ms: int, usage: Usage, actor: str | None,
                project: str | None, error: str = "", agent: str = "", run_id: str | None = None) -> None:
        """One line in the usage ledger. The ledger must never break the feature it measures.

        The counts are a provider's word, and a provider is occasionally wrong: a negative token count,
        or a cached part reported larger than the whole it came out of. The ledger's table now refuses
        both outright, so believing one of these would not price the call wrongly — it would lose the
        line entirely, which is the quieter and worse of the two failures. Each count is taken down to
        the nearest number that can be true before it is written: never below zero, and a part never
        larger than its whole. Nothing is invented; an impossible figure is read as the floor.
        """
        counted = {k: max(0, _int(usage.get(k))) for k in ("in", "out", "cached", "reasoning")}
        try:
            self.store.record(feature=feature, lane=provider.id, model=provider.model, ok=ok,
                              ms=max(0, int(ms)),
                              tokens_in=counted["in"], tokens_out=counted["out"],
                              user_id=actor, project_id=project, agent=agent, error=error, run_id=run_id,
                              tokens_cached=min(counted["cached"], counted["in"]),
                              tokens_reasoning=min(counted["reasoning"], counted["out"]))
        except Exception as e:                   # noqa: BLE001 — a ledger outage must not fail the call
            # ...but it must not vanish either. Swallowed in silence, a refused insert looked exactly
            # like a feature that was never used, and the usage screen said so.
            log.warning("usage ledger: could not record a %s call on %s: %s", feature, provider.id, e)
