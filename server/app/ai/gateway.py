"""The one door to language models — now with several lanes behind it.

Every AI feature goes through here, so keys, routing, time-outs, failure handling and the fallback
live in one place. A **lane** is a provider and a model together (see `lanes.py`). The router picks
the lane: free lanes first, the paid one as a backstop, the local model last, skipping any lane whose
key is missing, whose free allowance is spent for the minute or the day, or that refused its key.

Two things follow from having many lanes. A call that fails moves to the next lane instead of falling
straight to the offline rules. And agents working at the same time are *spread* across lanes, so four
agents are four providers answering at once, not four requests queued behind one rate limit.

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
from .ledger import Ledger

log = logging.getLogger(__name__)
from .lanes import CHAT, PLAN, REVIEW, WRITE, Lane

T = TypeVar("T")
__all__ = ["CHAT", "PLAN", "REVIEW", "WRITE", "Gateway", "NoModel", "OutOfBudget", "Provider", "ProviderError",
           "Reply", "Result", "Stopped", "clip", "extract_json"]

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


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
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
    for _ in range(len(OPTIONAL) + 1):
        try:
            out = _post(url, body, headers, 120)
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


def _lines(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float):
    """The response's lines as they arrive — a context manager, so a stop closes the socket."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
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
    for _ in range(len(OPTIONAL) + 1):
        try:
            response = _lines(url, body, headers, 120)
            break
        except ProviderError as e:
            if (name := _refusal(e, body)) is None:
                raise
            body = {k: v for k, v in body.items() if k != name}
    got = _Collect(on_delta, stop)
    with response:
        for raw in response:
            got.check()
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
                {"Authorization": f"Bearer {cfg['key']}"}, 120)
    return [row["embedding"] for row in out["data"]], (out.get("usage") or {}).get("prompt_tokens", 0)


def embed_ollama(texts: list[str], cfg: dict[str, Any]) -> tuple[list[list[float]], int]:
    out = _post(f"{cfg['url']}/api/embed", {"model": cfg["embed"], "input": texts}, {}, 300)
    return out["embeddings"], out.get("prompt_eval_count", 0)


def call_ollama(messages: list[dict[str, str]], cfg: dict[str, Any]) -> Reply:
    body = _post(f"{cfg['url']}/api/chat", {"model": cfg["model"], "messages": messages, "format": "json",
                                            "stream": False, "options": {"temperature": 0.2}}, {}, 300)
    message = body.get("message") or {}
    # A thinking model on Ollama puts its reasoning in `message.thinking`; it is shown when it is there.
    return Reply(message.get("content") or "", {"in": body.get("prompt_eval_count", 0),
                                                "out": body.get("eval_count", 0)},
                 message.get("thinking") or "", body.get("done_reason") or "")


def stream_ollama(messages: list[dict[str, str]], cfg: dict[str, Any], on_delta: Delta,
                  stop: Callable[[], bool] | None = None) -> Reply:
    """Ollama streams one JSON object a line, and counts the tokens in the last one (`done: true`)."""
    response = _lines(f"{cfg['url']}/api/chat", {"model": cfg["model"], "messages": messages, "format": "json",
                                                 "stream": True, "options": {"temperature": 0.2}}, {}, 300)
    got = _Collect(on_delta, stop)
    with response:
        for raw in response:
            got.check()
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


class Gateway:
    def __init__(self, ledger: Ledger, secrets: Secrets) -> None:
        self.store, self.secrets = ledger, secrets
        self._rejected: dict[str, str] = {}          # lane id → fingerprint of the key it refused
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

    def _asked(self, lane_id: str, feature: str) -> dict[str, Any]:
        """The provider call's configuration for this feature: the lane's, plus how hard to think."""
        lane = self.lane(lane_id)
        return {**self.config(lane_id), "thinking": self.thinking(feature), "thinks": lane.thinks if lane else ""}

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
        self._rejected.pop(lane_id, None) if lane_id else self._rejected.clear()

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
              on_delta: Delta | None, stop: Callable[[], bool] | None) -> Reply:
        """The provider call itself: streamed when asked for and the lane's real call is in place."""
        cfg = self._asked(provider.id, feature)
        real, streaming = STREAMS.get(provider.id, (None, None))
        if on_delta is not None and streaming is not None and CALLS.get(provider.id) is real:
            return streaming(messages, cfg, on_delta, stop)
        return _split(CALLS[provider.id](messages, cfg))

    def _try(self, provider: Provider, messages: list[dict[str, str]], parse: Callable[[str], T], feature: str,
             actor: str | None, project: str | None, agent: str = "", run_id: str | None = None,
             on_delta: Delta | None = None, stop: Callable[[], bool] | None = None) -> Result[T] | str:
        """One lane, one attempt. Returns the answer, or the reason it could not be used.

        A stop is not a failure of the lane, so it is not handed to the next one: it is ledgered with the
        tokens that had been reported (usually none — the count comes in the last chunk) and raised."""
        t0, reply = time.monotonic(), Reply("")
        self._recent.setdefault(provider.id, []).append(time.monotonic())
        try:
            reply = self._call(provider, messages, feature, on_delta, stop)
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
        for candidate in self.chain(role=role, lane=lane, avoid=avoid):
            out = self._try(Provider(candidate.id, candidate.model), messages, parse, feature, actor,
                            project, agent, run_id)
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
        reason = ""
        for n, candidate in enumerate(chain):
            if n and on_delta is not None:
                on_delta("restart", candidate.id)
            out = self._try(Provider(candidate.id, candidate.model), messages, parse, feature, actor,
                            project, agent, run_id, on_delta, stop)
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
            self._rejected.pop(lane_id, None)
            self._record("test", provider, True, _ms(t0), usage, actor, None)
            return {"ok": True, "ms": _ms(t0), "detail": f"{cfg['model']} answered."}
        except Exception as e:  # report whatever went wrong; this is a diagnostic
            if isinstance(e, ProviderError) and e.status in (401, 403) and cfg["key"]:
                self._rejected[lane_id] = _fp(cfg["key"])
            detail = (str(e) or type(e).__name__)[:200]
            self._record("test", provider, False, _ms(t0), usage, actor, None, detail)
            return {"ok": False, "ms": _ms(t0), "detail": detail}

    def _record(self, feature: str, provider: Provider, ok: bool, ms: int, usage: Usage, actor: str | None,
                project: str | None, error: str = "", agent: str = "", run_id: str | None = None) -> None:
        """One line in the usage ledger. The ledger must never break the feature it measures."""
        try:
            self.store.record(feature=feature, lane=provider.id, model=provider.model, ok=ok, ms=ms,
                              tokens_in=int(usage.get("in") or 0), tokens_out=int(usage.get("out") or 0),
                              user_id=actor, project_id=project, agent=agent, error=error, run_id=run_id,
                              tokens_cached=int(usage.get("cached") or 0),
                              tokens_reasoning=int(usage.get("reasoning") or 0))
        except Exception as e:                   # noqa: BLE001 — a ledger outage must not fail the call
            # ...but it must not vanish either. Swallowed in silence, a refused insert looked exactly
            # like a feature that was never used, and the usage screen said so.
            log.warning("usage ledger: could not record a %s call on %s: %s", feature, provider.id, e)
