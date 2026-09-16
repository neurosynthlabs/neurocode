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
from dataclasses import dataclass, replace
from typing import Any

from ..secrets import Secrets
from .ledger import Ledger

# What a lane is asked for. A lane offers what it is good at; the router asks for what it needs.
WRITE, REVIEW, PLAN, CHAT = "write", "review", "plan", "chat"
ROLES = (WRITE, REVIEW, PLAN, CHAT)


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

    @property
    def needs_key(self) -> bool:
        return bool(self.secret)


# The order is the order the router prefers when nothing else decides: free lanes first, the paid one
# as a backstop, and the local model last — it never runs out, it is only slower.
LANES: tuple[Lane, ...] = (
    Lane("groq", "Groq", "openai", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1",
         True, 28, 900, (WRITE, PLAN, CHAT), "groq_api_key", "GROQ_API_KEY", "https://console.groq.com/keys",
         "Free and very fast. A good first lane for writing code."),
    Lane("cerebras", "Cerebras", "openai", "qwen-3-coder-480b", "https://api.cerebras.ai/v1",
         True, 20, 500, (WRITE, REVIEW), "cerebras_api_key", "CEREBRAS_API_KEY", "https://cloud.cerebras.ai",
         "Free tier, a coder model, output faster than anything else here."),
    Lane("gemini", "Google Gemini", "openai", "gemini-2.5-flash",
         "https://generativelanguage.googleapis.com/v1beta/openai",
         True, 10, 200, (PLAN, REVIEW, CHAT), "gemini_api_key", "GEMINI_API_KEY", "https://aistudio.google.com/apikey",
         "Free tier with a large context window. Good at planning and at reading a long diff.",
         embed="text-embedding-004"),
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
    Lane("deepseek", "DeepSeek", "openai", "deepseek-chat", "https://api.deepseek.com",
         False, 0, 0, (WRITE, REVIEW, PLAN, CHAT), "deepseek_api_key", "DEEPSEEK_API_KEY",
         "https://platform.deepseek.com", "Paid, and the strongest lane here for writing code."),
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


def priced(lane_id: str) -> bool:
    """Whether this lane's cost is known. A free lane's is — it is zero. A paid lane that declares no price
    is not: its $0 is a missing number, and showing it as "free" would be the one thing a cost column must
    never do."""
    lane = next((x for x in LANES if x.id == lane_id), None)
    if lane is None:
        return lane_id == "rules"            # the offline rules cost nothing; an unknown lane is unknown
    return lane.free or bool(lane.usd_per_m_in or lane.usd_per_m_out)


def price_of(lane_id: str) -> tuple[float, float]:
    """What a million tokens cost on this lane, in and out. An unknown lane costs nothing known."""
    lane = next((x for x in LANES if x.id == lane_id), None)
    return (lane.usd_per_m_in, lane.usd_per_m_out) if lane else (0.0, 0.0)

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
    return {"id": lane.id, "label": lane.label, "model": lane.model, "baseUrl": lane.base_url,
            "free": lane.free, "rpm": lane.rpm, "rpd": lane.rpd, "goodAt": list(lane.good_at),
            "needsKey": lane.needs_key, "signup": lane.signup, "note": lane.note, "api": lane.api}
