"""Many small free models instead of one big paid one.

A **lane** is a provider and a model together, with the free tier's limits written down next to it.
The gateway routes each call into a lane, and the runtime spreads agents that work at the same time
across *different* lanes — because the real ceiling on parallel agents is not intelligence, it is one
provider's allowance. Four free lanes are four times the road.

That allowance used to be counted in requests a minute. In 2026 it is counted in **tokens**: Groq's
free plan allows 1,000 calls a day and 200,000 tokens a day, so the day ends at about two dozen calls
that write a file, not at nine hundred. A lane therefore carries `tpm` and `tpd` beside `rpm` and
`rpd`, and the router stops on whichever runs out first.

Three different things are called free, and this file keeps them apart. `free` is about money — a
call on a free lane costs nothing, which is what lets the ledger price it at $0 and mean it. `gate`
is about what a provider asks for instead: nothing, a payment card, a phone number, identity. A tier
that needs a card is not free money with an extra step, it is a bill that has not arrived yet, and
Cerebras — "Is there a permanently free tier? No" — is priced here as what it is.

Every number is sourced in a comment beside it, with the page and the day it was read, and a number
nobody publishes is marked as the router's own (`caps="ours"`) rather than dressed up as a provider's
promise. Free tiers change, so every number is editable in Admin → AI providers, and a lane that has
spent its allowance is treated as busy, not broken. Nothing here holds a key; keys live in
`secrets.json` (or an environment variable), and a lane with no key is invisible to the router.
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


#: What a lane's free tier asks for besides an account, in its provider's own words — the difference
#: between one kind of free and another. "" is what a provider's own documents say when they name
#: nothing further; it is their word, not a promise, and the screens say so in those terms.
GATES: dict[str, str] = {
    "": "nothing else its own documents ask for",
    "card": "a payment card before anything answers",
    "phone": "a phone number",
    "identity": "identity verification",
}


@dataclass(frozen=True)
class Lane:
    id: str
    label: str
    api: str                    # "openai" — chat completions — or "ollama"
    model: str
    base_url: str               # "" = the provider's address is not ours to know (an account is in it)
    #: Whether a call here costs no money. Only that: what the provider asks for *instead* — a card,
    #: a phone, an identity — is `gate`, because a tier behind a card is not free, and the ledger
    #: must not write $0 for one. A lane that is not free and declares no price is unpriced, not zero.
    free: bool
    rpm: int                    # calls a minute this lane allows itself (0 = not capped by us)
    rpd: int                    # calls a day (0 = not capped by us)
    good_at: tuple[str, ...]
    secret: str = ""            # its name in secrets.json ("" = the lane needs no key)
    env: str = ""               # the environment variable that stands in for it
    signup: str = ""            # where a free key comes from, shown in the UI
    note: str = ""
    embed: str = ""             # the embedding model it serves, if it serves one
    #: US dollars per million tokens, in and out. Zero is the honest figure for a free lane, and it is
    #: what makes a cost of $0 on a usage screen a fact rather than a placeholder. A paid lane fills
    #: these in with its own price — and a paid lane that cannot (Cerebras publishes none for its
    #: trial) leaves them at zero, where `priced()` reads the whole lane as unpriced rather than free.
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
    #: The models on this lane that read images, each from its provider's documentation. Per model, not
    #: per lane: an admin can point a lane at another model, and that one reads nothing we can vouch for.
    #: A session sends a picture only to one of these; every other model is refused it in words.
    vision: tuple[str, ...] = ()
    #: What this lane's free tier asks for besides an account — one of `GATES`. A free tier behind a
    #: card and one behind nothing are different offers, and a person choosing a lane may not be shown
    #: the same word for both.
    gate: str = ""
    #: When what is free here runs out for good, in words. Trial credit expires; an allowance does not.
    expires: str = ""
    #: Where `rpm` and `rpd` came from. "published": the provider publishes limits and these are ours,
    #: taken from them. "ours": the provider publishes none at all, and these are the router's own
    #: guess — shown as a guess, because a number that pretends is worse than no number.
    caps: str = "published"
    #: The provider's own allowance in its own units, in words — what it actually meters, which is
    #: often not calls. Shown beside the caps so nobody reads the router's ceiling as the provider's.
    allowance: str = ""
    #: Tokens a minute and a day, as the provider publishes them (0 = it publishes none). This is what
    #: a 2026 free tier really binds on, and the router counts both against its own ledger.
    tpm: int = 0
    tpd: int = 0
    #: The most one request may ask for, in tokens, where the lane's own minute bucket is smaller than
    #: a thinking call. Groq's free plan allows 8,000 tokens a minute; a review asking to think hard
    #: wants 27,576, and providers estimate input + `max_tokens` *before* they start (Cerebras says so
    #: outright), so such a call is refused rather than run. 0 = no ceiling of the lane's own.
    max_request_tokens: int = 0

    @property
    def needs_key(self) -> bool:
        return bool(self.secret)

    @property
    def needs_base_url(self) -> bool:
        """Whether this lane cannot be called until somebody gives it an address of its own."""
        return not self.base_url


# The order is the order the router prefers when nothing else decides: free lanes first, the paid ones
# as a backstop, and the local model last — it never runs out, it is only slower.
LANES: tuple[Lane, ...] = (
    # https://console.groq.com/docs/models (read 2026-09-20): openai/gpt-oss-120b, context 131,072.
    # https://console.groq.com/docs/deprecations — "llama-3.3-70b-versatile | 08/16/26 |
    # openai/gpt-oss-120b or qwen/qwen3.6-27b": the model this lane called for a year was shut down
    # five weeks ago, and Groq names this one in its place.
    # https://console.groq.com/docs/rate-limits — the Free Plan row for openai/gpt-oss-120b is
    # "30 RPM, 1K RPD, 8K TPM, 200K TPD", and "You can hit any limit type depending on which threshold
    # you reach first". 200,000 tokens is the day's real end, long before nine hundred calls are.
    # https://console.groq.com/docs/reasoning — `reasoning_effort` on GPT-OSS takes low | medium | high
    # and has no "none", which is why this lane's dialect is "effort-lmh" and not "effort".
    # Vision: the models page marks modalities with icons and no words, so no model here is vouched
    # for as reading images, and a session refuses to send one rather than guess.
    Lane("groq", "Groq", "openai", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1",
         True, 28, 900, (WRITE, PLAN, CHAT), "groq_api_key", "GROQ_API_KEY", "https://console.groq.com/keys",
         "Free and the fastest lane here. Its day ends on tokens, not calls: 200,000 of them, which is "
         "a couple of dozen calls that write a file.", window=131_072, thinks="effort-lmh",
         models=("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"),
         allowance="Groq publishes 30 calls a minute, 1,000 a day, 8,000 tokens a minute and 200,000 a day.",
         tpm=8_000, tpd=200_000,
         # Ours, not Groq's: a little under the 8,000 a minute it publishes, so a prompt of a few
         # hundred tokens still fits inside the minute's bucket beside the answer we ask for.
         max_request_tokens=7_000),
    # https://developers.cloudflare.com/workers-ai/platform/pricing/ (read 2026-09-20, the page says
    # "Last updated Sep 17, 2026"): "Our free allocation allows anyone to use a total of 10,000 Neurons
    # per day at no charge", and "All limits reset daily at 00:00 UTC". Per million tokens:
    # glm-4.7-flash 5,500 neurons in / 36,400 out; bge-m3 1,075 in. On those published rates a call of
    # 4K in and 1K out is 58 neurons — about 170 a day — and a day of embeddings is millions of tokens.
    # The models that need a paid plan are named on that page (kimi-k2.6/k2.7-code, glm-5.2/5.3/5.3-flash,
    # deepseek-v4-flash-0731, deepseek-v4-pro-0813); none of this row's models is among them.
    # https://developers.cloudflare.com/workers-ai/platform/limits/ — text generation is 300 requests a
    # minute; ours is well under it. https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
    # — /v1/chat/completions and /v1/embeddings both speak the OpenAI shape at
    # https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1, which is why this row carries no
    # address: the account id is the one part of it nobody but its owner knows.
    # https://developers.cloudflare.com/workers-ai/models/glm-4.7-flash — "Context Window | 131,072 tokens".
    Lane("cloudflare", "Cloudflare Workers AI", "openai", "@cf/zai-org/glm-4.7-flash", "",
         True, 60, 0, (WRITE, REVIEW, CHAT), "cloudflare_api_token", "CLOUDFLARE_API_TOKEN",
         "dash.cloudflare.com → AI → Workers AI → REST API · the token needs Workers AI Read and Edit, "
         "and the lane needs your account id in its address",
         "Free on the Workers Free plan: 10,000 Neurons a day, reset at 00:00 UTC. On Cloudflare's "
         "published rates that is about 170 calls of 4K in and 1K out — or millions of tokens of "
         "embeddings, which is what makes this the lane retrieval wants.",
         embed="@cf/baai/bge-m3", window=131_072, caps="ours",
         allowance="Cloudflare meters neurons, not calls: 10,000 a day free, and 300 text-generation "
                   "calls a minute. The day cap here is left open because a neuron is not a call.",
         models=("@cf/zai-org/glm-4.7-flash", "@cf/openai/gpt-oss-120b", "@cf/openai/gpt-oss-20b")),
    # https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash (read 2026-09-20) — input token
    # limit 1,048,576, inputs "Text, images, video, audio"; the stable id is not deprecated.
    # https://ai.google.dev/gemini-api/docs/rate-limits — there is no free-tier table any more, only
    # "Rate limits depend on a variety of factors (such as your usage tier) and can be viewed in Google
    # AI Studio" and "Specified rate limits are not guaranteed and actual capacity may vary". So the
    # two numbers below are the router's own (caps="ours"); a 429 rests the lane in any case.
    # https://ai.google.dev/gemini-api/docs/pricing — the free tier's row reads "Content used to improve
    # our products" where the paid one reads "Content not used to improve our products". For a product
    # about private code that belongs on the screen, not in a footnote.
    # https://ai.google.dev/gemini-api/docs/embeddings — text-embedding-004 is gone from the docs
    # (see RETIRED below); "For text-only use cases, gemini-embedding-001 remains available", it is
    # trained with Matryoshka Representation Learning, and "you can truncate it to a smaller size
    # without losing quality". Its 3,072 numbers are cut to the 1,536 the index stores by
    # `retrieval._pad`, which is exactly that truncation; Google asks for a re-normalisation after one,
    # and cosine distance — what the index uses — does not care about a vector's length.
    # https://ai.google.dev/gemini-api/docs/openai — `reasoning_effort`, where "none" turns thinking off
    # on 2.5 models; "Reasoning cannot be turned off for Gemini 2.5 Pro or 3 models", so an admin who
    # moves this lane to a 3.x model will have that field dropped and asked again without it.
    Lane("gemini", "Google Gemini", "openai", "gemini-2.5-flash",
         "https://generativelanguage.googleapis.com/v1beta/openai",
         True, 10, 200, (PLAN, REVIEW, CHAT), "gemini_api_key", "GEMINI_API_KEY", "https://aistudio.google.com/apikey",
         "Free, no card, and a million tokens of context — the lane for planning and for reading a long "
         "diff. Google's free tier is trained on: its pricing page says content on it is used to improve "
         "Google's products, which the paid tier says it is not.",
         embed="gemini-embedding-001", window=1_048_576, thinks="effort", caps="ours",
         allowance="Google stopped publishing the free tier's limits: \"Specified rate limits are not "
                   "guaranteed\". Yours are in AI Studio, at aistudio.google.com/rate-limit.",
         models=("gemini-2.5-flash", "gemini-3.5-flash-lite"),
         # gemini-3.5-flash-lite's card (read 2026-09-20) gives the same 1,048,576 input limit and
         # inputs "Text, Image, Video, Audio, and PDF".
         vision=("gemini-2.5-flash", "gemini-3.5-flash-lite")),
    # https://docs.z.ai/guides/overview/pricing (read 2026-09-20): GLM-4.7-Flash, GLM-4.5-Flash and
    # GLM-4.6V-Flash are "Free | Free | Free | Free" — input, cached input, storage and output.
    # https://docs.z.ai/guides/overview/overview.md — GLM-4.7-Flash 200K context, GLM-4.6V-Flash 128K
    # and in the Vision Models table, which is what puts a free model that reads pictures in this
    # catalogue for the first time. https://docs.z.ai/guides/overview/quick-start.md — the base URL.
    # Z.ai's own rate-limit page is behind a login, so it publishes no number a person can read: the
    # caps below are ours. A default concurrency of 1 is reported but not documented, which is why
    # this lane is kept out of the roles agents fan out on.
    Lane("zai", "Z.ai GLM", "openai", "glm-4.7-flash", "https://api.z.ai/api/paas/v4",
         True, 10, 500, (WRITE, CHAT), "zai_api_key", "ZAI_API_KEY", "https://z.ai/manage-apikey/apikey-list",
         "Free, and the only lane here whose vision model is free too. Z.ai publishes no limit for its "
         "Flash models, so these caps are ours, not theirs — and its concurrency is reported as one, so "
         "this is a poor lane to fan four agents out across.",
         window=200_000, caps="ours",
         allowance="Z.ai publishes no rate limit for the free Flash models — its limits page needs a login.",
         models=("glm-4.7-flash", "glm-4.5-flash", "glm-4.6v-flash"), vision=("glm-4.6v-flash",)),
    # https://docs.mistral.ai/getting-started/models/models_overview (read 2026-09-20): Mistral Small
    # 3.2 (mistral-small-2506) is in the deprecated-and-retired table, retired 2026-07-31, replaced by
    # Mistral Small 4 — so the model is pinned here rather than left on the `-latest` alias, whose
    # target Mistral does not document. Its card gives 256k of context.
    # https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key —
    # "Free mode: API access is enabled by default with no credit card required."
    # https://docs.mistral.ai/admin/billing-usage/usage-limits — the numbers themselves are no longer
    # published, only their names; yours are in the console under Admin → API → Limits. The old 400 a
    # day came from a table that no longer exists, so it is gone rather than kept as decoration.
    Lane("mistral", "Mistral", "openai", "mistral-small-2603", "https://api.mistral.ai/v1",
         True, 10, 0, (WRITE, REVIEW), "mistral_api_key", "MISTRAL_API_KEY", "https://console.mistral.ai/api-keys",
         "Free mode, and Mistral says in its own words that it needs no credit card. It no longer "
         "publishes the free limits, so the cap here is ours; yours are in its console.",
         embed="mistral-embed", window=256_000, caps="ours",
         allowance="Mistral publishes no Free-mode numbers any more — see Admin → API → Limits in its console.",
         models=("mistral-small-2603",)),
    # https://openrouter.ai/api/v1/models (read 2026-09-20, no key needed): 446 models, 21 of them
    # `:free`, and deepseek/deepseek-chat-v3.1:free — what this lane called — is not among them.
    # poolside/laguna-s-2.1:free is priced "0" in and out with 262,144 of context;
    # qwen/qwen3.8-27b:free declares image input; nvidia/nemotron-3-ultra-550b-a55b:free has 1,000,000.
    # https://openrouter.ai/docs/api-reference/limits — free models are 20 calls a minute, and 50 a day
    # until ten credits have been bought all-time, then 1,000. Ours are under the first pair.
    Lane("openrouter", "OpenRouter", "openai", "poolside/laguna-s-2.1:free", "https://openrouter.ai/api/v1",
         True, 18, 45, (WRITE, PLAN), "openrouter_api_key", "OPENROUTER_API_KEY", "https://openrouter.ai/keys",
         "One key, 21 `:free` models. Fifty calls a day on a zero balance — a good overflow lane, and "
         "the cheapest capacity on this list: buying $10 of credit once raises it to 1,000 a day.",
         window=262_144,
         allowance="OpenRouter publishes 20 calls a minute and 50 a day for `:free` models, or 1,000 a "
                   "day once ten credits have been bought. `GET /api/v1/key` reports what is left.",
         models=("poolside/laguna-s-2.1:free", "nvidia/nemotron-3-ultra-550b-a55b:free",
                 "qwen/qwen3.8-27b:free", "cohere/north-mini-code:free"),
         vision=("qwen/qwen3.8-27b:free",)),
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
         window=1_000_000, thinks="deepseek", models=("deepseek-flash", "deepseek-v4-pro"),
         # https://api-docs.deepseek.com/guides/vision (read 2026-09-19): deepseek-flash takes `image_url`
         # parts (JPEG, PNG, GIF, WebP); the pricing page lists vision as "Not supported" on v4-pro.
         vision=("deepseek-flash",),
         allowance="DeepSeek meters nothing on a paid key; what it costs is above, per million tokens."),
    # https://inference-docs.cerebras.ai/support/rate-limits (read 2026-09-20). Its own FAQ, asked "Is
    # there a permanently free tier?", answers: "No. The Free Trial is time- and credit-bounded: $5 in
    # credits that expire 30 days after they're granted." The credits arrive "after adding a verified
    # payment method", and without one "Playground and API access remain inactive". So this lane is not
    # free — it was `free=True` here, which had the ledger writing $0 against a card somebody added.
    # The Free Trial table holds gpt-oss-120b and qwen-3.8-27b at 5 RPM, 30K uncached TPM, 90K total
    # TPM, 1M an hour and 1M a day. qwen-3-coder-480b, which this lane called, is not in the catalogue
    # at all (see RETIRED). Cerebras publishes no price for the trial, so this row declares none: an
    # unpriced lane is honest, a lane priced at zero is not. It also documents estimating a request
    # before running it — input plus `max_completion_tokens` — which is why 27,576 fits here but not
    # inside Groq's 8,000-token minute.
    Lane("cerebras", "Cerebras", "openai", "gpt-oss-120b", "https://api.cerebras.ai/v1",
         False, 4, 0, (WRITE, REVIEW), "cerebras_api_key", "CEREBRAS_API_KEY", "https://cloud.cerebras.ai",
         "Not free: $5 of trial credit that needs a verified payment card and expires. The fastest "
         "output here while it lasts, and its cost is unknown to this ledger, not zero.",
         gate="card", expires="$5 of credit, 30 days after it is granted",
         tpm=90_000, tpd=1_000_000,
         allowance="Cerebras publishes 5 calls a minute, 90,000 tokens a minute (30,000 of them "
                   "uncached), 1M an hour and 1M a day on the Free Trial.",
         models=("gpt-oss-120b", "qwen-3.8-27b")),
    # Verified on this machine on 2026-09-20: `which ollama` finds nothing and 127.0.0.1:11434 refuses
    # the connection, so this lane is not "ready", it is not installed — which `Gateway.ollama_ready`
    # now says in those words. hw.memsize is 16 GiB here, which is the real ceiling on the model: a
    # 7-8B at Q4 is about 5 GB resident; qwen3-coder:30b is a 19 GB download
    # (https://ollama.com/library/qwen3-coder/tags) and does not fit.
    Lane("ollama", "Ollama · this Mac", "ollama", "qwen2.5-coder:7b", "http://127.0.0.1:11434",
         True, 0, 0, (WRITE, REVIEW, PLAN, CHAT), "", "", "https://ollama.com/download",
         "Local, free and unmetered — the only place where free really is unlimited, and the price is "
         "the model. On 16 GB this Mac runs a 7-8B at Q4 (about 5 GB resident); a 30B, even at Q4, is a "
         "19 GB download and does not fit.", embed="nomic-embed-text", caps="",
         allowance="Nothing meters this lane but the machine it runs on."),
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


#: Lanes that no longer exist anywhere, with the day their provider ended them. A row is kept after
#: the lane is gone for two reasons: the ledger still holds its calls, and what they cost is still
#: known (nothing), so a past day's total stays complete rather than turning into "unpriced"; and a
#: workspace that still has the lane saved somewhere gets a sentence instead of "there is no lane
#: called github".
@dataclass(frozen=True)
class Ended:
    label: str
    free: bool
    words: str


ENDED: dict[str, Ended] = {
    # https://docs.github.com/en/github-models (read 2026-09-20): "GitHub Models has been retired." /
    # "As of July 30, 2026, GitHub Models has been fully retired." / "The playground, model catalog,
    # inference API, and bring your own key (BYOK) are no longer available to any customer."
    "github": Ended("GitHub Models", True,
                    "GitHub retired GitHub Models on 2026-07-30 — the playground, the catalogue and the "
                    "inference API together. Nothing can run on it now. For embeddings, which it used to "
                    "serve, the Cloudflare lane serves @cf/baai/bge-m3."),
}

#: Providers looked at for a lane on 2026-09-20 and left out, so nobody hunts them again. NVIDIA NIM:
#: its public catalogue (https://integrate.api.nvidia.com/v1/models, read without a key) does not hold
#: openai/gpt-oss-120b, it publishes no rate limit anywhere readable, and its own documents do not say
#: what signing up asks for — three unknowns, and the point of this table is that there are none.
#: ModelScope: the largest card-free allowance found (reported 2,000 calls a day), but its own limits
#: page renders nothing without JavaScript, no model id could be verified, and it needs an Alibaba
#: Cloud account with real-name verification. Together, Fireworks, SambaNova, Hugging Face, Chutes and
#: Moonshot are all trial credit rather than an allowance that comes back, and a lane that vanishes
#: without warning is worse than no lane.

#: Model names a provider has withdrawn, with what that means for a lane still set to one. A lane an
#: admin saved on one of these fails every call, and the screens say why instead of a bare HTTP 400.
RETIRED: dict[str, dict[str, str]] = {
    # https://console.groq.com/docs/deprecations (read 2026-09-20): both Llamas shut down on 08/16/26,
    # with the replacements Groq itself names; qwen/qwen3.6-27b — one of those replacements — shut down
    # on 09/14/26 in its turn, replaced by "the direct successor" qwen/qwen3.8-27b.
    "groq": {
        "llama-3.3-70b-versatile": "Groq shut this model down on 2026-08-16. Calls to it fail: choose "
                                   "openai/gpt-oss-120b, which Groq names in its place.",
        "llama-3.1-8b-instant": "Groq shut this model down on 2026-08-16. Calls to it fail: choose "
                                "openai/gpt-oss-20b, which Groq names in its place.",
        "qwen/qwen3.6-27b": "Groq shut this model down on 2026-09-14. Choose qwen/qwen3.8-27b, its "
                            "direct successor, with the same 131K context.",
    },
    # https://inference-docs.cerebras.ai/support/rate-limits (read 2026-09-20): the only models on the
    # page are gpt-oss-120b and qwen-3.8-27b, on every tier.
    "cerebras": {
        "qwen-3-coder-480b": "Cerebras no longer lists this model on any tier. Choose gpt-oss-120b or "
                             "qwen-3.8-27b.",
    },
    # https://openrouter.ai/api/v1/models (read 2026-09-20): 446 models live, and this is not one.
    "openrouter": {
        "deepseek/deepseek-chat-v3.1:free": "OpenRouter's catalogue no longer holds this model, so every "
                                            "call returns an error. Choose poolside/laguna-s-2.1:free, or "
                                            "any other `:free` id from openrouter.ai/models.",
    },
    # https://ai.google.dev/gemini-api/docs/embeddings (read 2026-09-20) no longer mentions
    # text-embedding-004 at all; Google's developer forum gives 2026-01-14 as the day it was shut down
    # (https://discuss.ai.google.dev/t/what-is-the-retirement-date-for-text-embedding-004-model/107445 —
    # a forum answer, not a doc page, so the date is reported rather than confirmed here).
    "gemini": {
        "text-embedding-004": "Google withdrew this embedding model; the docs no longer list it, and it "
                              "answers 404. Choose gemini-embedding-001.",
    },
    # https://docs.mistral.ai/getting-started/models/models_overview (read 2026-09-20): Mistral Small
    # 3.2 is in the deprecated-and-retired table — deprecated 2026-04-30, retired 2026-07-31.
    "mistral": {
        "mistral-small-2506": "Mistral retired Mistral Small 3.2 on 2026-07-31. Choose mistral-small-2603, "
                              "the Small 4 it names in its place.",
    },
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
                     "research", "eval", "blueprint")
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
    `reasoning_effort` alone, where "none" is off and there is no "max" — high is the most they give.
    Groq's GPT-OSS models take the same field with no "none" at all
    (https://console.groq.com/docs/reasoning, read 2026-09-20: low, medium and high, and only Qwen
    takes "none"), so "effort-lmh" leaves the field out rather than sending a value that 400s and
    costs the call a round trip to find out."""
    if thinks == "deepseek":
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": level}
    if thinks == "effort":
        return {"reasoning_effort": {"off": "none", "low": "low"}.get(level, "high")}
    if thinks == "effort-lmh":
        return {} if level == "off" else {"reasoning_effort": "low" if level == "low" else "high"}
    return {}


def max_tokens(thinks: str, level: str) -> int:
    """The answer's budget, and the reasoning's on top of it when the lane is asked to think."""
    return ANSWER_TOKENS + (THINKING_TOKENS.get(level, 0) if thinks else 0)


def budget(lane: Lane | None, level: str) -> tuple[str, int]:
    """How hard this lane may really think, and what to ask for — the level asked for, unless the
    lane's own minute is too small to hold it.

    A free tier binds on tokens a minute, and a provider does not wait to find out: Cerebras documents
    estimating input plus `max_completion_tokens` before the request runs and refusing it if the sum is
    over quota, and Groq says you hit whichever limit comes first. So a review asking to think hard —
    27,576 tokens — would never *start* on Groq's free 8,000 a minute; the router would read the
    refusal as the lane being sick and rest a lane that was perfectly well.

    The answer is to ask for less rather than to ask and be refused, and to lower the thinking with the
    budget instead of quietly clipping it: a model given all its budget to reason with writes the
    reasoning and no answer, which is the failure `ANSWER_TOKENS` was written for.
    """
    thinks = lane.thinks if lane else ""
    cap = lane.max_request_tokens if lane else 0
    if not cap:
        return level, max_tokens(thinks, level)
    for candidate in (level, "high", "low", "off"):
        if LEVELS.index(candidate) <= LEVELS.index(level) and max_tokens(thinks, candidate) <= cap:
            return candidate, max_tokens(thinks, candidate)
    return "off", min(ANSWER_TOKENS, cap)


def retired(lane: Lane) -> str | None:
    """Why this lane's model no longer answers, or None when its provider still serves it."""
    return RETIRED.get(lane.id, {}).get(lane.model)


def ended(lane_id: str) -> str | None:
    """Why a lane that used to be here is not, or None when it never was one of ours."""
    gone = ENDED.get(lane_id)
    return gone.words if gone else None


def freedom(lane: Lane) -> str:
    """What "free" means on this lane, in one line — the sentence a person reads before choosing it.

    Three offers were all called free before this: free with nothing asked, free once a card is on
    file, free once you have proved who you are. They are not the same offer, and the one with the
    card is not free at all — it is a bill that has not arrived. So a lane that is not free says so
    first, whether it is priced (DeepSeek) or whether nobody publishes what it costs (Cerebras)."""
    asks = GATES.get(lane.gate, lane.gate)
    if not lane.free:
        words = "Paid" if price_table(lane.id) else "Not free, and unpriced here"
        return f"{words} · needs {asks}" if lane.gate else words
    if lane.api == "ollama":
        return "Free and unmetered · this machine's own model"
    return f"Free · {asks}" if not lane.gate else f"Free, after {asks}"


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


def reads_images(lane_id: str | None, model: str | None) -> bool:
    """Whether this model, on this lane, is documented to read images. Mistral is left out on purpose:
    its docs name `mistral-small-2506` as the Small model that sees, not the `-latest` alias it serves."""
    lane = _catalogue(lane_id or "")
    return bool(lane and model and model in lane.vision)


def priced(lane_id: str) -> bool:
    """Whether this lane's cost is known. A free lane's is — it is zero. A paid lane that declares no price
    is not: its $0 is a missing number, and showing it as "free" would be the one thing a cost column must
    never do. Cerebras is exactly that lane now, and it used to be priced at zero against a card."""
    lane = _catalogue(lane_id)
    if lane is None:
        # The offline rules cost nothing, and so did a free lane that has since been ended — its calls
        # are in the ledger for good, and what they cost is still known. An unknown lane is unknown.
        gone = ENDED.get(lane_id)
        return lane_id == "rules" or (gone is not None and gone.free)
    return lane.free or bool(price_table(lane_id))


def price_of(lane_id: str) -> tuple[float, float]:
    """What a million tokens cost on this lane, in and out. An unknown lane costs nothing known."""
    lane = next((x for x in LANES if x.id == lane_id), None)
    return (lane.usd_per_m_in, lane.usd_per_m_out) if lane else (0.0, 0.0)


def priced_models(lane_id: str) -> tuple[str, ...] | None:
    """The models a lane's declared price is for, or None when it holds for any model.

    A catalogue price is the price of the catalogue's model (and of the embedding model the lane
    serves): an admin, or `NEUROCODE_<LANE>_MODEL`, can point a free lane at a paid model, and that
    call's cost is then unknown, not zero. The local lane, the offline rules and a free lane that has
    since been ended cost nothing whatever they ran, so any model is priced there."""
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
    the model it will really call; the prices are the catalogue's, for the models they name.

    `free` alone is not enough to show a person and never was: `gate`, `freedom` and `expires` are
    what tell one kind of free from another, and `caps` says whether the two numbers beside them are
    the provider's or ours."""
    peak = off_peak(lane.id)
    return {"id": lane.id, "label": lane.label, "model": lane.model, "baseUrl": lane.base_url,
            "free": lane.free, "rpm": lane.rpm, "rpd": lane.rpd, "goodAt": list(lane.good_at),
            "needsKey": lane.needs_key, "signup": lane.signup, "note": lane.note, "api": lane.api,
            "retired": retired(lane), "window": window_for(lane.id, lane.model),
            "thinks": lane.thinks or None, "models": list(lane.models),
            "vision": reads_images(lane.id, lane.model),
            "gate": lane.gate, "freedom": freedom(lane), "expires": lane.expires or None,
            "caps": lane.caps, "allowance": lane.allowance, "tpm": lane.tpm, "tpd": lane.tpd,
            "needsBaseUrl": lane.needs_base_url, "maxRequestTokens": lane.max_request_tokens,
            "prices": [{"model": p.model, "usdPerMIn": p.per_m_in, "usdPerMCached": p.per_m_cached,
                        "usdPerMOut": p.per_m_out} for p in price_table(lane.id).values()],
            "offPeak": {"factor": peak.factor, "words": peak.words} if peak else None}
