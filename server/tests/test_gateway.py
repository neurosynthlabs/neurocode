"""The gateway against a provider that is a local HTTP server — never a real one.

What a lane really sends and really gets back: the thinking fields a feature asks for, the reasoning and
the cache and reasoning token counts a provider reports, an answer streamed in pieces, a stop halfway, an
answer that thought past its budget, and a field a lane refuses by name. The server speaks the two shapes
the lanes use — OpenAI-style chat completions (with server-sent events when streaming) and Ollama's
one-JSON-object-a-line — and records every request body it was sent.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.ai import gateway as gateway_module
from app.ai import lanes
from app.ai.gateway import (
    Gateway,
    LaneTooSlow,
    NoModel,
    OutOfBudget,
    ProviderError,
    Stopped,
    extract_json,
)
from app.ai.ledger import MemoryLedger
from app.secrets import Secrets


class Provider:
    """A scripted provider. Each POST takes the next reply: `(status, body)` for a whole answer, or
    `(200, [chunk, ...])` to stream the chunks one line at a time."""

    def __init__(self) -> None:
        self.replies: list[tuple[int, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.models = [lanes.BY_ID["ollama"].model]
        #: Seconds of silence before a whole answer, and between the pieces of a streamed one. A
        #: provider that goes soft does not stop sending — that is why a socket timeout never fires.
        self.stall = 0.0
        self.drip = 0.0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def do_GET(self) -> None:          # Ollama's /api/tags: which models are pulled
                body = json.dumps({"models": [{"name": m} for m in provider.models]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length") or 0)
                provider.sent.append(json.loads(self.rfile.read(size)))
                status, body = provider.replies.pop(0)
                if provider.stall:
                    time.sleep(provider.stall)
                if isinstance(body, list):
                    self.send_response(status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for chunk in body:
                        try:
                            self.wfile.write(chunk.encode() + b"\n")
                            self.wfile.flush()
                            if provider.drip:
                                time.sleep(provider.drip)
                        except OSError:                   # the reader hung up: it was stopped
                            return
                    return
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def sse(*chunks: dict[str, Any]) -> list[str]:
    """Chunks as server-sent events, with a keep-alive comment and the closing [DONE]."""
    return [": keep-alive", *(f"data: {json.dumps(c)}\n" for c in chunks), "data: [DONE]"]


def completion(content: str | None, *, reasoning: str = "", finish: str = "stop",
               usage: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": finish}], "usage": usage or {}}


@pytest.fixture
def provider() -> Iterator[Provider]:
    server = Provider()
    yield server
    server.close()


@pytest.fixture
def gateway(provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gateway:
    """The real gateway with the DeepSeek lane pointed at the local server and pinned."""
    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    monkeypatch.setenv("NEUROCODE_COMPILER", "deepseek")
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("deepseek_api_key", "test-key")
    return Gateway(MemoryLedger({"ai.lane.deepseek": {"baseUrl": provider.url}}), secrets)


ASK = [{"role": "user", "content": "Where is tax handled? Answer as JSON."}]


def test_the_thinking_a_feature_asks_for_is_what_the_lane_is_sent(gateway: Gateway, provider: Provider):
    """DeepSeek thinks by default at high effort, so every call says what it wants — and a feature that
    thinks gets room to, or its reasoning eats the answer's budget."""
    provider.replies += [(200, completion('{"ok": true}')), (200, completion('{"ok": true}'))]
    gateway.ask(ASK, extract_json, feature="agent")                 # agent: off by default
    gateway.ask(ASK, extract_json, feature="compile")               # compile: high by default
    off, high = provider.sent
    assert off["thinking"] == {"type": "disabled"} and "reasoning_effort" not in off
    assert off["max_tokens"] == lanes.WRITE_TOKENS and off["model"] == "deepseek-flash"   # writing code: room to
    assert high["thinking"] == {"type": "enabled"} and high["reasoning_effort"] == "high"
    assert high["max_tokens"] == lanes.ANSWER_TOKENS + lanes.THINKING_TOKENS["high"]

    gateway.store.save_setting("ai.thinking", {"agent": "max"})
    provider.replies.append((200, completion('{"ok": true}')))
    gateway.ask(ASK, extract_json, feature="agent")
    assert provider.sent[-1]["reasoning_effort"] == "max"


def test_reasoning_and_the_providers_token_counts_are_kept(gateway: Gateway, provider: Provider):
    usage = {"prompt_tokens": 1200, "completion_tokens": 300, "prompt_cache_hit_tokens": 1024,
             "completion_tokens_details": {"reasoning_tokens": 250}}
    provider.replies.append((200, completion('{"answer": "pkg/tax.py"}', reasoning="Look at tax first.",
                                             usage=usage)))
    result = gateway.ask(ASK, extract_json, feature="chat")
    assert result.data == {"answer": "pkg/tax.py"} and result.reasoning == "Look at tax first."
    assert result.usage == {"in": 1200, "out": 300, "cached": 1024, "reasoning": 250}
    assert result.streamed is False
    line = gateway.store.calls[-1]
    assert (line["tokens_in"], line["tokens_cached"], line["tokens_reasoning"]) == (1200, 1024, 250)

    # The OpenAI-style spelling of a cache hit is read the same way.
    provider.replies.append((200, completion('{"ok": 1}', usage={
        "prompt_tokens": 50, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 32}})))
    gateway.ask(ASK, extract_json, feature="ask")
    assert gateway.store.calls[-1]["tokens_cached"] == 32


def test_an_answer_that_thought_past_its_budget_is_named(gateway: Gateway, provider: Provider):
    """Empty content cut off at the budget used to fail as "the answer holds no JSON object"."""
    provider.replies.append((200, completion("", reasoning="…" * 50, finish="length",
                                             usage={"prompt_tokens": 10, "completion_tokens": 27_576})))
    with pytest.raises(ProviderError) as failed:
        gateway.ask(ASK, extract_json, feature="compile")
    assert "OutOfBudget" in failed.value.body and "thought past its budget" in failed.value.body
    line = gateway.store.calls[-1]
    assert line["ok"] is False and "thought past its budget" in line["error"]
    assert line["tokens_out"] == 27_576                      # what it spent is still counted
    assert issubclass(OutOfBudget, RuntimeError)


def test_a_field_the_lane_refuses_by_name_is_dropped_and_asked_again(gateway: Gateway, provider: Provider):
    provider.replies += [(400, {"error": {"message": "unknown field reasoning_effort"}}),
                         (200, completion('{"ok": true}'))]
    assert gateway.ask(ASK, extract_json, feature="compile").data == {"ok": True}
    first, second = provider.sent
    assert "reasoning_effort" in first and "reasoning_effort" not in second
    assert second["thinking"] == {"type": "enabled"}           # only the named field goes


def test_an_answer_streams_in_pieces_with_its_reasoning_first(gateway: Gateway, provider: Provider):
    provider.replies.append((200, sse(
        {"choices": [{"delta": {"reasoning_content": "Tax lives "}}]},
        {"choices": [{"delta": {"reasoning_content": "in pkg."}}]},
        {"choices": [{"delta": {"content": '{"answer": "pkg/'}}]},
        {"choices": [{"delta": {"content": 'tax.py"}'}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 40, "prompt_cache_hit_tokens": 640,
                                  "completion_tokens_details": {"reasoning_tokens": 12}}})))
    pieces: list[tuple[str, str]] = []
    result = gateway.ask(ASK, extract_json, feature="chat", on_delta=lambda k, t: pieces.append((k, t)))

    assert provider.sent[0]["stream"] is True and provider.sent[0]["stream_options"] == {"include_usage": True}
    assert pieces == [("reasoning", "Tax lives "), ("reasoning", "in pkg."),
                      ("answer", '{"answer": "pkg/'), ("answer", 'tax.py"}')]
    assert result.data == {"answer": "pkg/tax.py"} and result.reasoning == "Tax lives in pkg."
    assert result.streamed is True and result.thought_ms is not None
    assert result.usage == {"in": 900, "out": 40, "cached": 640, "reasoning": 12}
    assert gateway.store.calls[-1]["tokens_cached"] == 640


def test_a_stop_ends_the_stream_keeps_what_was_written_and_tries_no_other_lane(gateway: Gateway,
                                                                               provider: Provider):
    provider.replies.append((200, sse(
        {"choices": [{"delta": {"content": '{"answer": "Half'}}]},
        {"choices": [{"delta": {"content": ' an answer'}}]},
        {"choices": [{"delta": {"content": '"}'}}]})))
    pieces: list[str] = []

    def stop() -> bool:                       # a person presses Stop once the first words are on screen
        return bool(pieces)

    with pytest.raises(Stopped) as halted:
        gateway.ask(ASK, extract_json, feature="chat", on_delta=lambda _k, t: pieces.append(t), stop=stop)
    assert halted.value.reply.text == '{"answer": "Half' and pieces == ['{"answer": "Half']
    assert len(provider.sent) == 1                            # a stop is not a lane failing
    line = gateway.store.calls[-1]
    assert line["ok"] is False and line["error"] == "stopped by a person"


def test_a_stand_in_answers_whole_even_when_asked_to_stream(gateway: Gateway, monkeypatch: pytest.MonkeyPatch):
    """Only the real provider call streams. What stands in for it — here a test; in life a lane with no
    streaming shape — answers once, and the result says it was not streamed."""
    monkeypatch.setitem(gateway_module.CALLS, "deepseek", lambda messages, cfg: '{"answer": "whole"}')
    pieces: list[Any] = []
    result = gateway.ask(ASK, extract_json, feature="chat", on_delta=lambda *a: pieces.append(a))
    assert result.data == {"answer": "whole"} and result.streamed is False and pieces == []


def test_ollama_streams_one_object_a_line(provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NEUROCODE_OLLAMA_URL", raising=False)   # this test points the lane itself
    monkeypatch.setenv("NEUROCODE_COMPILER", "ollama")
    gw = Gateway(MemoryLedger({"ai.lane.ollama": {"baseUrl": provider.url}}), Secrets(tmp_path / "s.json"))
    provider.replies.append((200, [
        json.dumps({"message": {"role": "assistant", "content": "", "thinking": "Hmm."}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": '{"answer": '}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": '"local"}'}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop",
                    "prompt_eval_count": 77, "eval_count": 9})]))
    pieces: list[tuple[str, str]] = []
    result = gw.ask(ASK, extract_json, feature="chat", on_delta=lambda k, t: pieces.append((k, t)))
    assert result.data == {"answer": "local"} and result.reasoning == "Hmm." and result.streamed
    assert pieces[0] == ("reasoning", "Hmm.") and result.usage == {"in": 77, "out": 9}
    assert provider.sent[0]["stream"] is True


def test_with_no_lane_streaming_is_refused_like_any_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")
    gw = Gateway(MemoryLedger(), Secrets(tmp_path / "s.json"))
    with pytest.raises(NoModel):
        gw.ask(ASK, extract_json, feature="chat", on_delta=lambda *_: None)


# ── the lanes, as the catalogue declares them ────────────────────
def test_the_deepseek_lane_calls_a_model_its_provider_still_serves_and_prices_it():
    lane = lanes.BY_ID["deepseek"]
    assert lane.model == "deepseek-flash" and lanes.retired(lane) is None
    assert lanes.priced("deepseek") and lanes.priced_call("deepseek", "deepseek-v4-pro")
    assert not lanes.priced_call("deepseek", "deepseek-chat")           # retired: no known price
    table = lanes.price_table("deepseek")
    assert (table["deepseek-flash"].per_m_in, table["deepseek-flash"].per_m_cached,
            table["deepseek-flash"].per_m_out) == (0.30, 0.006, 1.20)
    assert lanes.window_for("deepseek", "deepseek-v4-pro") == 1_000_000
    assert lanes.window_for("groq", "openai/gpt-oss-120b") == 131_072
    assert lanes.window_for("groq", "a-model-an-admin-typed") is None   # unknown, not guessed

    saved = lanes.settled(MemoryLedger({"ai.lane.deepseek": {"model": "deepseek-chat"}}), "deepseek", {})
    assert saved is not None and "retired" in (lanes.retired(saved) or "")
    assert lanes.describe(saved)["retired"] and lanes.describe(saved)["window"] is None


def test_no_lane_calls_a_model_its_provider_has_withdrawn():
    """The whole table, checked against the whole withdrawn list. This is the test that would have
    caught Groq five weeks ago: the lane the router prefers first was calling a model shut down on
    2026-08-16, so every free-first call began with a wasted round trip to a 404."""
    for lane in lanes.LANES:
        assert lanes.retired(lane) is None, f"{lane.id} is set to a model its provider withdrew"
        if lane.embed:
            assert lanes.RETIRED.get(lane.id, {}).get(lane.embed) is None, \
                f"{lane.id} embeds with a model its provider withdrew"
    # And each withdrawal says what to choose instead, which is the difference between a message and
    # a mystery. The two Groq models below are the ones it shut down on 2026-08-16.
    for lane_id, withdrawn in lanes.RETIRED.items():
        for model, words in withdrawn.items():
            assert "choose" in words.lower(), f"{lane_id}/{model} does not say what to choose instead"
    assert "openai/gpt-oss-120b" in lanes.RETIRED["groq"]["llama-3.3-70b-versatile"]
    assert "openai/gpt-oss-20b" in lanes.RETIRED["groq"]["llama-3.1-8b-instant"]
    assert "gemini-embedding-001" in lanes.RETIRED["gemini"]["text-embedding-004"]
    assert "poolside" in lanes.RETIRED["openrouter"]["deepseek/deepseek-chat-v3.1:free"]
    assert "mistral-small-2603" in lanes.RETIRED["mistral"]["mistral-small-2506"]


def test_a_free_tier_behind_a_card_is_not_a_free_lane_and_is_not_priced_at_zero():
    """Cerebras answers its own FAQ "Is there a permanently free tier? No": $5 of trial credit that
    needs a verified card and expires. It sat here as `free=True`, so the ledger wrote $0 against a
    card somebody had added — the one thing a cost column must never do, a level up from the model."""
    cerebras = lanes.BY_ID["cerebras"]
    assert cerebras.free is False and cerebras.gate == "card" and cerebras.expires
    assert lanes.priced("cerebras") is False                     # unknown, and unknown is not zero
    assert lanes.priced_call("cerebras", cerebras.model) is False
    assert "card" in lanes.freedom(cerebras) and lanes.freedom(cerebras).startswith("Not free")
    assert "cerebras" not in lanes.FREE_IDS                      # so "free only" never routes to it

    # Every lane still says which kind of free it is, and every gate it names is one we have words for.
    for lane in lanes.LANES:
        assert lane.gate in lanes.GATES, f"{lane.id} names a gate nobody can read"
        assert lanes.freedom(lane) and lanes.describe(lane)["freedom"] == lanes.freedom(lane)
        if lane.free:
            assert lanes.priced(lane.id), f"{lane.id} is free, so its cost is known: it is zero"


def test_a_lane_whose_provider_ended_it_says_so_and_keeps_what_its_calls_cost():
    """GitHub retired GitHub Models whole on 2026-07-30. The lane is gone, but its lines are still in
    the ledger, and what they cost is still known — nothing — so a past day stays a fact."""
    assert "github" not in lanes.IDS and lanes.BY_ID.get("github") is None
    assert "2026-07-30" in (lanes.ended("github") or "")
    assert lanes.priced("github") and lanes.priced_call("github", "openai/gpt-4.1-mini")
    assert lanes.ended("groq") is None and lanes.priced("a-lane-that-never-was") is False


def test_a_lane_that_publishes_no_limits_says_the_caps_are_ours():
    """Google, Mistral and Z.ai all stopped publishing free-tier numbers in 2026. A number nobody
    publishes may still be a cap the router holds itself to — it may not be dressed as a promise."""
    for lane_id in ("gemini", "mistral", "zai", "cloudflare"):
        lane = lanes.BY_ID[lane_id]
        assert lane.caps == "ours" and lane.allowance
        assert lanes.describe(lane)["caps"] == "ours"
    assert lanes.BY_ID["groq"].caps == "published"               # Groq still publishes its free plan
    assert lanes.BY_ID["groq"].tpd == 200_000 and lanes.BY_ID["groq"].tpm == 8_000


def test_thinking_is_said_in_each_lanes_own_words():
    assert lanes.thinking_params("deepseek", "off") == {"thinking": {"type": "disabled"}}
    assert lanes.thinking_params("deepseek", "low") == {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    assert lanes.thinking_params("effort", "off") == {"reasoning_effort": "none"}
    assert lanes.thinking_params("effort", "max") == {"reasoning_effort": "high"}   # no "max" there
    assert lanes.thinking_params("", "high") == {}                                  # a lane that takes none
    # Groq's GPT-OSS models take low | medium | high and have no "none" at all, so off sends nothing
    # rather than a value the lane would refuse — a 400 and a second round trip for every call.
    assert lanes.thinking_params("effort-lmh", "off") == {}
    assert lanes.thinking_params("effort-lmh", "low") == {"reasoning_effort": "low"}
    assert lanes.thinking_params("effort-lmh", "max") == {"reasoning_effort": "high"}
    store = MemoryLedger({"ai.thinking": {"chat": "max", "review": "nonsense"}})
    assert lanes.thinking(store, "chat") == "max"
    assert lanes.thinking(store, "review") == "high"            # an unknown level falls back to the default
    assert lanes.thinking(store, "brainstorm") == "off"


# ── slow, not wrong: the wall clock and the breaker ──────────────

def _two_free_lanes(provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gateway:
    """Groq and Gemini, both pointed at the local server, so a chain really has somewhere to go.

    Both are free in the sense the "free" preference means — no money — which is why Cerebras cannot
    stand here any more: its trial credit needs a card, so the router no longer counts it as free."""
    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    monkeypatch.setenv("NEUROCODE_COMPILER", "free")
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("groq_api_key", "test-key")
    secrets.set("gemini_api_key", "test-key")
    return Gateway(MemoryLedger({"ai.lane.groq": {"baseUrl": provider.url},
                                 "ai.lane.gemini": {"baseUrl": provider.url}}), secrets)


def test_a_lane_that_dribbles_for_ever_is_cut_off_by_the_wall_clock(gateway: Gateway, provider: Provider,
                                                                    monkeypatch: pytest.MonkeyPatch):
    """The socket timeout was never the limit: every piece that arrives resets it.

    A provider that keeps sending, slowly, used to hold the call open for as long as it liked — and
    with it the request, the session at "thinking" and the connection that request was holding."""
    monkeypatch.setattr(gateway_module, "LANE_SECONDS", 0.4)
    provider.drip = 0.05
    provider.replies.append((200, sse(*({"choices": [{"delta": {"content": "."}}]} for _ in range(400)))))

    started = time.monotonic()
    with pytest.raises(ProviderError) as failed:
        gateway.ask(ASK, extract_json, feature="chat", on_delta=lambda *_: None)
    assert time.monotonic() - started < 5            # not 400 × 0.05 seconds, and not 120 either
    assert "LaneTooSlow" in str(failed.value) and "0.4 seconds" in str(failed.value)
    assert issubclass(LaneTooSlow, TimeoutError)


def test_a_lane_that_did_not_answer_rests_and_says_so_instead_of_being_chosen_again(
        gateway: Gateway, provider: Provider):
    """The breaker used to know one thing only: a key the provider refused. A lane that is simply not
    answering was recorded, forgotten, and asked first again on the very next call."""
    provider.replies.append((503, {"error": "upstream is down"}))
    with pytest.raises(ProviderError):
        gateway.ask(ASK, extract_json, feature="compile")

    blocked = gateway.why_not(gateway.lane("deepseek"))
    assert blocked is not None and "HTTP 503" in blocked and "trying it again in" in blocked
    assert [row for row in gateway.report() if row["id"] == "deepseek"][0]["ready"] is False
    assert gateway.chain() == []                     # so the router does not offer it
    assert gateway.status()["lanes"] == 0

    with pytest.raises(NoModel):                     # and nothing asks it again while it rests
        gateway.ask(ASK, extract_json, feature="compile")
    assert len(provider.sent) == 1


def test_a_lane_answering_too_many_calls_a_minute_rests_too(gateway: Gateway, provider: Provider):
    """A 429 is the provider saying "not now" — resting it is what it asked for."""
    provider.replies.append((429, {"error": "rate limit exceeded"}))
    with pytest.raises(ProviderError):
        gateway.ask(ASK, extract_json, feature="compile")
    assert "rate-limiting this key" in (gateway.why_not(gateway.lane("deepseek")) or "")


def test_a_refusal_about_the_request_leaves_the_lane_where_it_is(gateway: Gateway, provider: Provider):
    """A 400 is about what we sent. Resting the lane for it would punish the only lane that answers."""
    provider.replies += [(400, {"error": {"message": "this conversation is longer than the context"}}),
                         (200, completion('{"ok": true}'))]
    with pytest.raises(ProviderError):
        gateway.ask(ASK, extract_json, feature="agent")
    assert gateway.why_not(gateway.lane("deepseek")) is None
    assert gateway.ask(ASK, extract_json, feature="agent").data == {"ok": True}


def test_a_lane_that_answers_the_test_call_stops_resting(gateway: Gateway, provider: Provider):
    """Models → Keys presses Test. A lane that answers it is well, whatever it did a minute ago."""
    provider.replies += [(500, {"error": "boom"}), (200, completion('{"ok": true}'))]
    with pytest.raises(ProviderError):
        gateway.ask(ASK, extract_json, feature="compile")
    assert gateway.why_not(gateway.lane("deepseek")) is not None
    assert gateway.test("deepseek")["ok"] is True
    assert gateway.why_not(gateway.lane("deepseek")) is None


def test_the_chain_stops_when_its_budget_is_spent_rather_than_asking_every_lane(
        provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Three lanes each allowed their own minutes is not a fallback, it is a queue a person waits in."""
    monkeypatch.setattr(gateway_module, "LANE_SECONDS", 0.4)
    monkeypatch.setattr(gateway_module, "CHAIN_SECONDS", 0.5)
    monkeypatch.setattr(gateway_module, "LEAST_SECONDS", 0.2)
    gw = _two_free_lanes(provider, tmp_path, monkeypatch)
    assert len(gw.chain(limit=3)) >= 2                       # there really is a second lane to skip
    provider.stall = 1.5
    provider.replies += [(200, completion('{"ok": true}')), (200, completion('{"ok": true}'))]

    started = time.monotonic()
    with pytest.raises(ProviderError) as failed:
        gw.ask(ASK, extract_json, feature="compile")
    assert time.monotonic() - started < 3
    assert "no time left to ask another lane" in str(failed.value)
    assert len(provider.sent) == 1                           # the second lane was never opened


def test_with_no_model_the_rules_still_answer_when_the_chain_runs_out_of_time(
        provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`run` has an honest offline answer, so a spent budget reaches it rather than raising."""
    monkeypatch.setattr(gateway_module, "LANE_SECONDS", 0.3)
    monkeypatch.setattr(gateway_module, "CHAIN_SECONDS", 0.4)
    monkeypatch.setattr(gateway_module, "LEAST_SECONDS", 0.2)
    gw = _two_free_lanes(provider, tmp_path, monkeypatch)
    provider.stall = 1.5
    provider.replies += [(200, completion('{"ok": true}')), (200, completion('{"ok": true}'))]

    result = gw.run(ASK, extract_json, lambda: {"by": "rules"}, feature="compile")
    assert result.data == {"by": "rules"} and result.provider.id == "rules"
    assert result.fallback is not None and "no time left to ask another lane" in result.fallback


# ── what a free tier really binds on: tokens ─────────────────────

def _groq(provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **saved: Any) -> Gateway:
    """The Groq lane, pinned and pointed at the local server. Groq is the one lane in the catalogue
    whose provider publishes a token budget, so it is the one that can be tested against one."""
    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    monkeypatch.setenv("NEUROCODE_COMPILER", "groq")
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("groq_api_key", "test-key")
    return Gateway(MemoryLedger({"ai.lane.groq": {"baseUrl": provider.url, **saved}}), secrets)


def test_a_lane_asks_for_a_budget_its_own_minute_can_hold(provider: Provider, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch):
    """Groq's free minute holds 8,000 tokens. Compiling asks a model to think hard, which is 27,576.

    A provider does not discover that halfway: Cerebras documents adding `max_completion_tokens` to
    the prompt and refusing the request before it starts, and Groq says you hit whichever limit comes
    first. So the call would never have run, the router would have read the refusal as the lane being
    sick, and rested a lane that was perfectly well — on the two features that matter most."""
    gw = _groq(provider, tmp_path, monkeypatch)
    provider.replies.append((200, completion('{"ok": true}')))
    gw.ask(ASK, extract_json, feature="compile")                 # compile thinks "high" by default

    sent = provider.sent[0]
    assert sent["max_tokens"] == lanes.ANSWER_TOKENS <= lanes.BY_ID["groq"].max_request_tokens
    assert "reasoning_effort" not in sent          # the budget came down, and the thinking with it
    # The level itself is lowered rather than the budget quietly clipped: a model handed all of its
    # budget to reason with writes the reasoning and no answer, which is what OutOfBudget is for.
    assert lanes.budget(lanes.BY_ID["groq"], "high") == ("off", lanes.ANSWER_TOKENS)
    assert lanes.budget(lanes.BY_ID["deepseek"], "high") == (
        "high", lanes.ANSWER_TOKENS + lanes.THINKING_TOKENS["high"])
    # And the screens can say why this lane thinks less than the feature asked it to.
    assert lanes.describe(lanes.BY_ID["groq"])["maxRequestTokens"] == 7_000
    assert lanes.describe(lanes.BY_ID["deepseek"])["maxRequestTokens"] == 0


def test_a_lane_that_has_spent_its_tokens_for_today_is_busy_not_broken(provider: Provider, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch):
    """Groq allows 1,000 calls a day and 200,000 tokens. The tokens go first — about two dozen calls
    that write a file — and a router counting only calls would keep choosing a lane with nothing left,
    walking into a 429 every time and resting the lane for a minute each time it did."""
    gw = _groq(provider, tmp_path, monkeypatch)
    assert gw.why_not(gw.lane("groq")) is None
    for _ in range(4):                       # four calls, 50,000 tokens each: 200,000, and the day is done
        gw.store.record(feature="agent", lane="groq", model="openai/gpt-oss-120b", ok=True, ms=10,
                        tokens_in=40_000, tokens_out=10_000, user_id=None, project_id=None, agent="",
                        error="")
    assert gw.store.tokens_today("groq") == 200_000
    blocked = gw.why_not(gw.lane("groq"))
    assert blocked == "200,000 tokens today — its free allowance"
    assert gw.chain() == [] and gw.spent(gw.lane("groq"))["tokensToday"] == 200_000


def test_a_lane_that_has_spent_its_tokens_for_this_minute_waits_out_the_minute(
        provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The minute is counted from what the providers themselves reported, as the calls come back."""
    gw = _groq(provider, tmp_path, monkeypatch)
    provider.replies.append((200, completion('{"ok": true}', usage={"prompt_tokens": 7_000,
                                                                    "completion_tokens": 1_500})))
    gw.ask(ASK, extract_json, feature="agent")
    assert gw.spent(gw.lane("groq"))["tokensMinute"] == 8_500
    assert gw.why_not(gw.lane("groq")) == "8,000 tokens this minute — its free allowance"


def test_a_lane_with_no_address_of_its_own_says_so_instead_of_dialling_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Cloudflare's address carries the account id, so the catalogue cannot hold it. A lane with a key
    and no address used to look ready and fail a minute later on an empty URL."""
    monkeypatch.delenv("NEUROCODE_COMPILER", raising=False)
    monkeypatch.delenv("NEUROCODE_CLOUDFLARE_URL", raising=False)
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("cloudflare_api_token", "test-token")
    gw = Gateway(MemoryLedger(), secrets)
    assert lanes.BY_ID["cloudflare"].needs_base_url is True
    assert "no address yet" in (gw.why_not(gw.lane("cloudflare")) or "")
    assert "cloudflare" not in [x.id for x in gw.chain(limit=len(lanes.IDS))]
    assert lanes.describe(gw.lane("cloudflare"))["needsBaseUrl"] is True

    account = "https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"
    gw.store.save_setting("ai.lane.cloudflare", {"baseUrl": account})
    assert gw.lane("cloudflare").base_url == account and gw.why_not(gw.lane("cloudflare")) is None


def test_ollama_says_whether_it_is_installed_or_only_missing_the_model(provider: Provider, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch):
    """Two different problems with two different first moves. Calling both "no model pulled" sent a
    person to `ollama pull` on a machine with no Ollama on it — which is this one, today."""
    monkeypatch.delenv("NEUROCODE_OLLAMA_URL", raising=False)   # this test points the lane itself
    monkeypatch.setenv("NEUROCODE_COMPILER", "local")
    secrets = Secrets(tmp_path / "secrets.json")

    dead = Gateway(MemoryLedger({"ai.lane.ollama": {"baseUrl": "http://127.0.0.1:1"}}), secrets)
    assert "not running on this machine" in (dead.why_not(dead.lane("ollama")) or "")
    assert dead.ollama_ready() is False

    provider.models = [lanes.BY_ID["ollama"].model]
    wrong = Gateway(MemoryLedger({"ai.lane.ollama": {"baseUrl": provider.url, "model": "llama3:70b"}}), secrets)
    assert wrong.why_not(wrong.lane("ollama")) == \
        "Ollama is running, but llama3:70b is not pulled — `ollama pull llama3:70b`"

    right = Gateway(MemoryLedger({"ai.lane.ollama": {"baseUrl": provider.url}}), secrets)
    assert right.why_not(right.lane("ollama")) is None and right.ollama_ready() is True


# ── the cost of choosing a lane ──────────────────────────────────

class Counting(MemoryLedger):
    """A ledger that says how often it was really asked."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        super().__init__(settings)
        self.reads = 0

    def setting(self, key: str, default: Any = None) -> Any:
        self.reads += 1
        return super().setting(key, default)


def test_choosing_a_lane_reads_each_setting_once_and_not_once_per_question(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Settling eight lanes, asking each whether it is switched off and what it has spent was dozens
    of round trips for one question, and 350 for the Models screen — all through the
    gateway's own two-connection pool, which is what made that screen and four working agents wait on
    each other."""
    monkeypatch.delenv("NEUROCODE_COMPILER", raising=False)
    for lane in lanes.LANES:
        if lane.env:
            monkeypatch.delenv(lane.env, raising=False)
    counted = Counting({"ai.lane.deepseek": {"baseUrl": "http://nowhere"}})
    gw = Gateway(counted, Secrets(tmp_path / "s.json"))

    gw.chain(role=lanes.WRITE)
    once = counted.reads
    assert once <= len(lanes.IDS) + 4              # a read per lane, and the few keys beside them

    gw.chain(role=lanes.WRITE)
    gw.report()
    gw.status()
    assert counted.reads == once                   # the same rows, not asked again


def test_a_lane_an_admin_saves_is_the_lane_the_next_call_uses(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch):
    """Remembering is only safe if a write is seen at once: Models → Keys saves through the
    same object, so the row it replaced is dropped rather than believed for another two seconds."""
    monkeypatch.delenv("NEUROCODE_COMPILER", raising=False)
    gw = Gateway(MemoryLedger(), Secrets(tmp_path / "s.json"))
    assert gw.lane("deepseek").model == "deepseek-flash"
    gw.store.save_setting("ai.lane.deepseek", {"model": "deepseek-v4-pro"})
    assert gw.lane("deepseek").model == "deepseek-v4-pro"
    assert gw.preference() == "auto"
    gw.store.save_setting("ai.preference", "free")
    assert gw.preference() == "free"


def test_a_request_goes_whole_to_a_lane_that_can_hold_it_before_one_that_must_cut_it(
        provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Nothing is cut that need not be. Groq's free minute is 8,000 tokens for the whole request; a question
    carrying 40,000 characters of retrieved code goes to Gemini first, whole, and to Groq — cut down — only
    after it. A small question keeps the order the router chose."""
    gw = _two_free_lanes(provider, tmp_path, monkeypatch)
    tried: list[str] = []

    def attempt(p: Any, *_: Any, **__: Any) -> str:
        tried.append(p.id)
        return "not this time"

    monkeypatch.setattr(gw, "_try", attempt)
    big = [{"role": "user", "content": "code\n" * 8_000 + "What does this do?"}]
    with pytest.raises(gateway_module.ProviderError):
        gw.ask(big, json.loads, feature="chat")
    assert tried[:2] == ["gemini", "groq"]
    tried.clear()
    with pytest.raises(gateway_module.ProviderError):
        gw.ask(ASK, json.loads, feature="chat")
    assert tried[0] == gw.chain()[0].id


def test_a_lane_resting_only_for_its_full_minute_is_waited_for_when_nothing_else_can_answer(
        gateway: Gateway, provider: Provider, monkeypatch: pytest.MonkeyPatch):
    """With one free key, a full minute used to skip a run's step with "no model"; a lane that is down still
    is not asked again while it rests."""
    provider.replies += [(429, {"error": "rate limited"}), (200, completion('{"ok": true}'))]
    with pytest.raises(ProviderError):
        gateway.ask(ASK, extract_json, feature="compile")
    slept: list[float] = []

    def nap(seconds: float) -> None:
        slept.append(seconds)
        gateway._resting.clear()                       # the minute passes

    monkeypatch.setattr(gateway_module.time, "sleep", nap)
    assert gateway.ask(ASK, extract_json, feature="compile").data == {"ok": True}
    assert slept and 0 < slept[0] <= gateway_module.WAIT_FOR_REST + 1


def test_a_lane_whose_minute_is_used_up_is_waited_for_rather_than_skipped(
        provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Groq's free minute is 8,000 tokens; one step can use it. The next step used to find "no model" and be
    skipped — now it waits for the minute to turn."""
    gw = _groq(provider, tmp_path, monkeypatch)
    gw._burned["groq"] = [(gateway_module.time.monotonic() - 30, 8_000)]
    assert gw.chain() == []
    slept: list[float] = []

    def nap(seconds: float) -> None:
        slept.append(seconds)
        gw._burned["groq"] = []

    monkeypatch.setattr(gateway_module.time, "sleep", nap)
    provider.replies.append((200, completion('{"ok": true}')))
    assert gw.ask(ASK, extract_json, feature="compile").data == {"ok": True}
    assert slept and 25 < slept[0] < 35


def test_a_key_that_is_set_but_busy_is_not_called_missing(
        provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Seven agents on one free Groq key spent its minute at once, and every one heard "No model is configured"
    — which sent the owner to add a key they already had. Busy is said as busy, with the lane's own reason."""
    gw = _groq(provider, tmp_path, monkeypatch)
    gw._rest("groq", "its provider answered HTTP 503")                 # down, so not waited for
    with pytest.raises(gateway_module.NoModel) as busy:
        gw.ask(ASK, extract_json, feature="compile")
    assert str(busy.value).startswith("Every model is busy: ") and "HTTP 503" in str(busy.value)
    assert "configured" not in str(busy.value)
