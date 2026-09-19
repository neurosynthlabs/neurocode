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
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.ai import gateway as gateway_module
from app.ai import lanes
from app.ai.gateway import Gateway, NoModel, OutOfBudget, ProviderError, Stopped, extract_json
from app.ai.ledger import MemoryLedger
from app.secrets import Secrets


class Provider:
    """A scripted provider. Each POST takes the next reply: `(status, body)` for a whole answer, or
    `(200, [chunk, ...])` to stream the chunks one line at a time."""

    def __init__(self) -> None:
        self.replies: list[tuple[int, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.models = ["qwen2.5-coder:7b"]
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
                if isinstance(body, list):
                    self.send_response(status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for chunk in body:
                        try:
                            self.wfile.write(chunk.encode() + b"\n")
                            self.wfile.flush()
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
    assert off["max_tokens"] == lanes.ANSWER_TOKENS and off["model"] == "deepseek-flash"
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
    assert lanes.window_for("groq", "llama-3.3-70b-versatile") == 131_072
    assert lanes.window_for("groq", "a-model-an-admin-typed") is None   # unknown, not guessed

    saved = lanes.settled(MemoryLedger({"ai.lane.deepseek": {"model": "deepseek-chat"}}), "deepseek", {})
    assert saved is not None and "retired" in (lanes.retired(saved) or "")
    assert lanes.describe(saved)["retired"] and lanes.describe(saved)["window"] is None


def test_thinking_is_said_in_each_lanes_own_words():
    assert lanes.thinking_params("deepseek", "off") == {"thinking": {"type": "disabled"}}
    assert lanes.thinking_params("deepseek", "low") == {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    assert lanes.thinking_params("effort", "off") == {"reasoning_effort": "none"}
    assert lanes.thinking_params("effort", "max") == {"reasoning_effort": "high"}   # no "max" there
    assert lanes.thinking_params("", "high") == {}                                  # a lane that takes none
    store = MemoryLedger({"ai.thinking": {"chat": "max", "review": "nonsense"}})
    assert lanes.thinking(store, "chat") == "max"
    assert lanes.thinking(store, "review") == "high"            # an unknown level falls back to the default
    assert lanes.thinking(store, "brainstorm") == "off"
