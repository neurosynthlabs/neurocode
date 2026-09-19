"""Many small free models instead of one big paid one.

A **lane** is a provider and a model together, with the free tier's limits written down next to it.
The gateway routes each call into a lane, and the runtime spreads agents that work at the same time
across *different* lanes — because the real ceiling on parallel agents is not intelligence, it is one
provider's requests-per-minute. Four free lanes are four times the road.

The limits here are what the router imposes on itself, not what a provider promises: free tiers
change, so every number is editable in Admin → AI providers, and a lane that has spent its allowance
is treated as busy, not broken. Nothing here holds a key; keys live in `secrets.json` (or an
environment variable), and a lane with no key is invisible to the router.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any

from ..secrets import Secrets
from .ledger import Ledger

# What a lane is asked for. A lane offers what it is good at; the router asks for what it needs.
WRITE, REVIEW, PLAN, CHAT = "write", "review", "plan", "chat"
ROLES = (WRITE, REVIEW, PLAN, CHAT)


@dataclass(frozen=True)
class Price:
    """What one model costs on a lane, per million tokens, at its full (peak) rate.

    `per_m_cached` is an input token the provider served from its prompt cache — DeepSeek bills one at
    about a fiftieth of a miss, so a ledger that priced every input token alike overstated a session's
    cost many times over. `window` is the model's context, in tokens, as its provider publishes it."""

    model: str
    per_m_in: float
    per_m_out: float
    per_m_cached: float
    window: int = 0


@dataclass(frozen=True)
class OffPeak:
    """A time-of-day discount: outside the peak hours (UTC) of the peak weekdays (ISO, Monday = 1), a
    lane's prices are multiplied by `factor`."""

    factor: float
    weekdays: tuple[int, ...]
    hours: tuple[int, ...]
    words: str


@dataclass(frozen=True)
class Lane:
    id: str
    label: str
    api: str                    # "openai" — chat completions — or "ollama"
    model: str
    base_url: str
    free: bool
    rpm: int                    # calls a minute this lane allows itself (0 = unmetered)
    rpd: int                    # calls a day (0 = unmetered)
    good_at: tuple[str, ...]
    secret: str = ""            # its name in secrets.json ("" = the lane needs no key)
    env: str = ""               # the environment variable that stands in for it
    signup: str = ""            # where a free key comes from, shown in the UI
    note: str = ""
    embed: str = ""             # the embedding model it serves, if it serves one
    #: US dollars per million tokens, in and out. Zero is the honest figure for every free lane in
    #: the catalogue below — which is all of them but one — and it is what makes a cost of $0 on a
    #: usage screen a fact rather than a placeholder. A paid lane fills these in with its own price.
    usd_per_m_in: float = 0.0
    usd_per_m_out: float = 0.0
    #: A prompt-cache hit on the catalogue model, when the provider prices one apart; None is "as a miss".
    usd_per_m_cached: float | None = None
    #: Other models this lane is priced for, beside its catalogue model — the ones an admin may pick.
    prices: tuple[Price, ...] = ()
    #: When the provider discounts by the clock. None: one price all day.
    off_peak: OffPeak | None = None
    #: The catalogue model's context window in tokens, from its provider's documentation. 0 is "not
    #: published for this model": the context meter then shows tokens without a percentage, rather
    #: than a window somebody guessed.
    window: int = 0
    #: How the lane is told how hard to think: "deepseek" (`thinking` + `reasoning_effort`), "effort"
    #: (OpenAI-style `reasoning_effort` alone) or "" — it takes no such setting, and none is sent.
    thinks: str = ""
    #: The model names an admin is offered for this lane, beside typing one.
    models: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_key(self) -> bool:
        return bool(self.secret)


# The order is the order the router prefers when nothing else decides: free lanes first, the paid one
# as a backstop, and the local model last — it never runs out, it is only slower.
LANES: tuple[Lane, ...] = (
    # Window: https://console.groq.com/docs/models — llama-3.3-70b-versatile, 131,072 tokens.
    Lane("groq", "Groq", "openai", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1",
         True, 28, 900, (WRITE, PLAN, CHAT), "groq_api_key", "GROQ_API_KEY", "https://console.groq.com/keys",
         "Free and very fast. A good first lane for writing code.", window=131_072),
    Lane("cerebras", "Cerebras", "openai", "qwen-3-coder-480b", "https://api.cerebras.ai/v1",
         True, 20, 500, (WRITE, REVIEW), "cerebras_api_key", "CEREBRAS_API_KEY", "https://cloud.cerebras.ai",
         "Free tier, a coder model, output faster than anything else here."),
    Lane("gemini", "Google Gemini", "openai", "gemini-2.5-flash",
         "https://generativelanguage.googleapis.com/v1beta/openai",
         True, 10, 200, (PLAN, REVIEW, CHAT), "gemini_api_key", "GEMINI_API_KEY", "https://aistudio.google.com/apikey",
         "Free tier with a large context window. Good at planning and at reading a long diff.",
         # https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash — input limit 1,048,576 tokens.
         # https://ai.google.dev/gemini-api/docs/openai — `reasoning_effort`, and "none" turns 2.5's
         # thinking off; the endpoint returns no thoughts unless asked, so none are shown.
         embed="text-embedding-004", window=1_048_576, thinks="effort"),
    Lane("mistral", "Mistral", "openai", "mistral-small-latest", "https://api.mistral.ai/v1",
         True, 10, 400, (WRITE, REVIEW), "mistral_api_key", "MISTRAL_API_KEY", "https://console.mistral.ai/api-keys",
         "Free experimental tier.", embed="mistral-embed"),
    Lane("openrouter", "OpenRouter", "openai", "deepseek/deepseek-chat-v3.1:free", "https://openrouter.ai/api/v1",
         True, 15, 50, (WRITE, PLAN), "openrouter_api_key", "OPENROUTER_API_KEY", "https://openrouter.ai/keys",
         "One key, many `:free` models. The daily allowance is small — a good overflow lane."),
    Lane("github", "GitHub Models", "openai", "openai/gpt-4.1-mini", "https://models.github.ai/inference",
         True, 10, 120, (REVIEW, CHAT), "github_models_token", "GITHUB_MODELS_TOKEN",
         "github.com/settings/tokens · fine-grained, Models: read",
         "Free with a GitHub token you already have. Modest limits.", embed="openai/text-embedding-3-small"),
    # https://api-docs.deepseek.com/quick_start/pricing (read 2026-09-19): deepseek-flash and
    # deepseek-v4-pro, 1M context each; per million tokens at peak — flash $0.30 miss, $0.006 cache
    # hit, $1.20 out; v4-pro $1.32, $0.044, $3.96. Off-peak is half. Thinking is on by default at high
    # effort (https://api-docs.deepseek.com/guides/thinking_mode), so it is always said explicitly here.
    Lane("deepseek", "DeepSeek", "openai", "deepseek-flash", "https://api.deepseek.com",
         False, 0, 0, (WRITE, REVIEW, PLAN, CHAT), "deepseek_api_key", "DEEPSEEK_API_KEY",
         "https://platform.deepseek.com", "Paid, and the strongest lane here for writing code. Cheaper off-peak, "
         "and a cached prompt costs a fiftieth of a fresh one.",
         usd_per_m_in=0.30, usd_per_m_out=1.20, usd_per_m_cached=0.006,
         prices=(Price("deepseek-v4-pro", 1.32, 3.96, 0.044, 1_000_000),
                 # Retired on 2026-09-10 and served by V4.1 Flash "at the Flash price" for now.
                 Price("deepseek-v4-flash", 0.30, 1.20, 0.006, 1_000_000)),
         off_peak=OffPeak(0.5, (1, 2, 3, 4, 5), (1, 2, 3, 6, 7, 8, 9),
                          "Peak is 01:00–04:00 and 06:00–10:00 UTC, Monday to Friday; every other hour "
                          "costs half. Chinese public holidays are off-peak too, and are priced here "
                          "at peak — so on those days the figure is an upper bound."),
         window=1_000_000, thinks="deepseek", models=("deepseek-flash", "deepseek-v4-pro")),
    Lane("ollama", "Ollama · this Mac", "ollama", "qwen2.5-coder:7b", "http://127.0.0.1:11434",
         True, 0, 0, (WRITE, REVIEW, PLAN, CHAT), "", "", "https://ollama.com/download",
         "Local, free and unmetered. Slower, and bounded by this machine's memory.", embed="nomic-embed-text"),
)

BY_ID: dict[str, Lane] = {lane.id: lane for lane in LANES}
IDS: tuple[str, ...] = tuple(lane.id for lane in LANES)
FREE_IDS: tuple[str, ...] = tuple(lane.id for lane in LANES if lane.free)

# Where a lane's settings were kept before lanes existed. Read, never written: an admin saving a
# lane now writes `ai.lane.<id>`, and these two keys stay as they are for an older database.
LEGACY = {"deepseek": ("ai.deepseek", {"model": "model", "baseUrl": "base_url"}),
          "ollama": ("ai.ollama", {"model": "model", "url": "base_url"})}


def _apply(fields: dict[str, Any], saved: dict[str, Any], mapping: dict[str, str]) -> None:
    for key, field in mapping.items():
        value = saved.get(key)
        if field in ("model", "base_url") and isinstance(value, str) and value.strip():
            fields[field] = value.strip().rstrip("/") if field == "base_url" else value.strip()
        elif field in ("rpm", "rpd") and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            fields[field] = value


#: Model names a provider has withdrawn, with what that means for a lane still set to one. A lane an
#: admin saved on one of these fails every call, and the screens say why instead of a bare HTTP 400.
RETIRED: dict[str, dict[str, str]] = {
    # https://api-docs.deepseek.com/updates — "deepseek-chat and deepseek-reasoner … discontinued …
    # (2026-07-24)"; V4 Flash retired on 2026-09-10, its names temporarily routed to V4.1 Flash.
    "deepseek": {
        "deepseek-chat": "DeepSeek retired this model name on 2026-07-24. Calls to it fail: choose deepseek-flash.",
        "deepseek-reasoner": "DeepSeek retired this model name on 2026-07-24. Calls to it fail: choose "
                             "deepseek-flash, whose thinking is set per feature on Models & Router.",
        "deepseek-v4-flash": "Retired on 2026-09-10. DeepSeek serves it with V4.1 Flash for now; choose "
                             "deepseek-flash before that stops.",
        "deepseek-v4-flash-vision-exp": "Retired on 2026-09-10. DeepSeek serves it with V4.1 Flash for now; "
                                        "choose deepseek-flash before that stops.",
    },
}

#: How hard a model is asked to think, per kind of work. "off" asks for no reasoning at all.
LEVELS = ("off", "low", "high", "max")
#: Chosen per job: planning and reviewing are worth the wait; writing whole files is long enough
#: without reasoning in front of it; a session thinks a little, so its answer shows its working.
THINKING_DEFAULTS: dict[str, str] = {"compile": "high", "review": "high", "agent": "off", "chat": "low",
                                     "compact": "off"}
#: The features whose thinking a person may set — every call site that asks the gateway for a model.
THINKING_FEATURES = ("compile", "agent", "review", "chat", "compact", "ask", "brainstorm", "extract",
                     "research", "eval")
#: What a model may write, in tokens, beyond the answer itself when it thinks: reasoning is paid for
#: and counted against `max_tokens`, so an answer asked to think hard with the old flat 3,000 spent
#: all of it thinking and came back empty. Gemini 2.5 Flash's output limit (65,536) bounds the top.
ANSWER_TOKENS = 3_000
THINKING_TOKENS = {"off": 0, "low": 8_192, "high": 24_576, "max": 49_152}


def thinking(store: Ledger, feature: str) -> str:
    """The level this feature asks for: what an admin saved, else the default for its kind of work."""
    saved = store.setting("ai.thinking", {}) or {}
    level = saved.get(feature) if isinstance(saved, dict) else None
    return level if level in LEVELS else THINKING_DEFAULTS.get(feature, "off")


def thinking_params(thinks: str, level: str) -> dict[str, Any]:
    """The request fields that set a lane's thinking — none at all for a lane that takes none.

    DeepSeek takes `thinking: {type}` and `reasoning_effort` low|high|max. The OpenAI-style lanes take
    `reasoning_effort` alone, where "none" is off and there is no "max" — high is the most they give."""
    if thinks == "deepseek":
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}
    if thinks == "effort":
        return {"reasoning_effort": {"off": "none", "low": "low"}.get(level, "high")}
    return {}


def max_tokens(thinks: str, level: str) -> int:
    """The answer's budget, and the reasoning's on top of it when the lane is asked to think."""
    return ANSWER_TOKENS + (THINKING_TOKENS.get(level, 0) if thinks else 0)


def retired(lane: Lane) -> str | None:
    """Why this lane's model no longer answers, or None when its provider still serves it."""
    return RETIRED.get(lane.id, {}).get(lane.model)


def _catalogue(lane_id: str) -> Lane | None:
    # Read from LANES at call time, not BY_ID: a test that swaps a lane's price must be seen.
    return next((x for x in LANES if x.id == lane_id), None)


def price_table(lane_id: str) -> dict[str, Price]:
    """model → its price on this lane, for a paid lane. Its catalogue model first, then the others."""
    lane = _catalogue(lane_id)
    if lane is None or lane.free:
        return {}
    table: dict[str, Price] = {}
    if lane.usd_per_m_in or lane.usd_per_m_out:
        cached = lane.usd_per_m_in if lane.usd_per_m_cached is None else lane.usd_per_m_cached
        table[lane.model] = Price(lane.model, lane.usd_per_m_in, lane.usd_per_m_out, cached, lane.window)
    for price in lane.prices:
        table.setdefault(price.model, price)
    return table


def off_peak(lane_id: str) -> OffPeak | None:
    lane = _catalogue(lane_id)
    return lane.off_peak if lane else None


def window_for(lane_id: str | None, model: str | None) -> int | None:
    """The context window of this model on this lane, in tokens, or None when no provider document
    this catalogue cites gives one — an admin's own model has no known window."""
    lane = _catalogue(lane_id or "")
    if lane is None or not model:
        return None
    if model == lane.model:
        return lane.window or None
    found = next((p for p in lane.prices if p.model == model), None)
    return found.window or None if found else None


def priced(lane_id: str) -> bool:
    """Whether this lane's cost is known. A free lane's is — it is zero. A paid lane that declares no price
    is not: its $0 is a missing number, and showing it as "free" would be the one thing a cost column must
    never do."""
    lane = _catalogue(lane_id)
    if lane is None:
        return lane_id == "rules"            # the offline rules cost nothing; an unknown lane is unknown
    return lane.free or bool(price_table(lane_id))


def price_of(lane_id: str) -> tuple[float, float]:
    """What a million tokens cost on this lane, in and out. An unknown lane costs nothing known."""
    lane = next((x for x in LANES if x.id == lane_id), None)
    return (lane.usd_per_m_in, lane.usd_per_m_out) if lane else (0.0, 0.0)


def priced_models(lane_id: str) -> tuple[str, ...] | None:
    """The models a lane's declared price is for, or None when it holds for any model.

    A catalogue price is the price of the catalogue's model (and of the embedding model the lane
    serves): an admin, or `NEUROCODE_<LANE>_MODEL`, can point a free lane at a paid model, and that
    call's cost is then unknown, not zero. The local lane and the offline rules cost nothing whatever
    they run, so any model is priced there."""
    lane = _catalogue(lane_id)
    if lane is None or lane.api == "ollama":
        return None
    if not lane.free:
        return tuple(price_table(lane_id))
    return tuple(m for m in (lane.model, lane.embed) if m)


def priced_call(lane_id: str, model: str) -> bool:
    """Whether one call's cost is known: its lane declares a price, and the price is for this model."""
    if not priced(lane_id):
        return False
    models = priced_models(lane_id)
    return models is None or model in models

def settled(store: Ledger, lane_id: str, environ: dict[str, str] | None = None) -> Lane | None:
    """The lane as this workspace has it: the catalogue, then what an admin saved, then the
    environment — which wins, so a script or CI can pin a model without touching the database."""
    base = BY_ID.get(lane_id)
    if base is None:
        return None
    env = os.environ if environ is None else environ
    fields: dict[str, Any] = {}
    if lane_id in LEGACY:
        key, mapping = LEGACY[lane_id]
        _apply(fields, store.setting(key, {}) or {}, mapping)
    _apply(fields, store.setting(f"ai.lane.{lane_id}", {}) or {},
           {"model": "model", "baseUrl": "base_url", "rpm": "rpm", "rpd": "rpd"})
    for name, field in ((f"NEUROCODE_{lane_id.upper()}_MODEL", "model"), (f"NEUROCODE_{lane_id.upper()}_URL", "base_url")):
        if (value := env.get(name)) and value.strip():
            fields[field] = value.strip().rstrip("/") if field == "base_url" else value.strip()
    return replace(base, **fields) if fields else base


def every(store: Ledger, environ: dict[str, str] | None = None) -> list[Lane]:
    return [lane for lane_id in IDS if (lane := settled(store, lane_id, environ)) is not None]


def enabled(store: Ledger, lane_id: str) -> bool:
    """A lane an admin switched off stays off even when its key is there."""
    return (store.setting(f"ai.lane.{lane_id}", {}) or {}).get("enabled", True) is not False


def key_of(lane: Lane, secrets: Secrets, environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    if not lane.needs_key:
        return None
    return secrets.get(lane.secret) or (env.get(lane.env) if lane.env else None) or None


def key_source(lane: Lane, secrets: Secrets, environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    if not lane.needs_key:
        return None
    if secrets.get(lane.secret):
        return "workspace"
    return "environment" if lane.env and env.get(lane.env) else None


def config(lane: Lane, secrets: Secrets, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """What a provider call needs. `baseUrl` and `url` are both given: the two APIs name it differently."""
    return {"id": lane.id, "model": lane.model, "baseUrl": lane.base_url, "url": lane.base_url,
            "key": key_of(lane, secrets, environ), "api": lane.api}


def describe(lane: Lane) -> dict[str, Any]:
    """A lane for the screens. `lane` is as this workspace has it, so `retired` and `window` are about
    the model it will really call; the prices are the catalogue's, for the models they name."""
    peak = off_peak(lane.id)
    return {"id": lane.id, "label": lane.label, "model": lane.model, "baseUrl": lane.base_url,
            "free": lane.free, "rpm": lane.rpm, "rpd": lane.rpd, "goodAt": list(lane.good_at),
            "needsKey": lane.needs_key, "signup": lane.signup, "note": lane.note, "api": lane.api,
            "retired": retired(lane), "window": window_for(lane.id, lane.model),
            "thinks": lane.thinks or None, "models": list(lane.models),
            "prices": [{"model": p.model, "usdPerMIn": p.per_m_in, "usdPerMCached": p.per_m_cached,
                        "usdPerMOut": p.per_m_out} for p in price_table(lane.id).values()],
            "offPeak": {"factor": peak.factor, "words": peak.words} if peak else None}
