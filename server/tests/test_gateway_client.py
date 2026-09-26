"""What the gateway sends a provider, and what it makes of a refusal.

Found by the first run against a real Groq key: Python's default User-Agent is refused by the Cloudflare
firewall in front of Groq with a 403 ("error code: 1010"), and the gateway read that 403 as a refused key,
switched the lane off and never reached the model. Every test before it ran against a stub.
"""
from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from app.ai import gateway


class _Reply(io.BytesIO):
    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def test_every_call_to_a_provider_says_who_is_calling(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def urlopen(req: Any, timeout: float) -> _Reply:
        seen["agent"] = req.get_header("User-agent")
        return _Reply(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(gateway.urllib.request, "urlopen", urlopen)
    answer = gateway._post("https://api.groq.test/v1/chat", {}, {"Authorization": "Bearer k"}, gateway._Budget(5))
    assert answer == {"ok": True}
    assert seen["agent"] == gateway.USER_AGENT and not seen["agent"].startswith("Python-urllib")
    gateway._lines("https://api.groq.test/v1/chat", {}, {}, gateway._Budget(5))
    assert seen["agent"] == gateway.USER_AGENT


def test_a_firewall_refusal_is_not_a_refused_key():
    assert gateway.ProviderError(401, "Invalid API Key").key_refused
    assert gateway.ProviderError(403, '{"error": {"message": "forbidden"}}').key_refused
    assert not gateway.ProviderError(403, "error code: 1010").key_refused
    assert not gateway.ProviderError(403, "error code: 1020").key_refused
    assert not gateway.ProviderError(429, "rate limited").key_refused


def test_the_body_of_a_refusal_is_kept_to_say_why(monkeypatch: pytest.MonkeyPatch):
    def urlopen(req: Any, timeout: float) -> Any:
        body = io.BytesIO(b"error code: 1010")
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr(gateway.urllib.request, "urlopen", urlopen)
    with pytest.raises(gateway.ProviderError) as refused:
        gateway._post("https://api.groq.test/v1/chat", {}, {}, gateway._Budget(5))
    assert refused.value.status == 403 and not refused.value.key_refused


# ── tools, declared natively ────────────────────────────────────────
PATH = {"type": "object", "properties": {"path": {"type": "string"}}}
READ = [{"type": "function", "function": {"name": "read_file", "description": "read a file", "parameters": PATH}}]
CFG = {"model": "openai/gpt-oss-120b", "baseUrl": "https://api.groq.test/v1", "key": "k",
       "thinks": "effort-lmh", "thinking": "low"}


def test_tools_are_declared_and_json_mode_is_left_out():
    body = gateway._body([{"role": "user", "content": "hi"}], {**CFG, "tools": READ}, stream=False)
    assert body["tools"] == READ and body["tool_choice"] == "auto" and "response_format" not in body
    plain = gateway._body([{"role": "user", "content": "hi"}], CFG, stream=False)
    assert plain["response_format"] == {"type": "json_object"} and "tools" not in plain


def test_a_native_tool_call_arrives_as_the_json_a_session_reads(monkeypatch: pytest.MonkeyPatch):
    reply = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    monkeypatch.setattr(gateway, "_post", lambda *a, **k: reply)
    got = gateway.call_openai([{"role": "user", "content": "hi"}], {**CFG, "tools": READ})
    assert json.loads(got.text) == {"tool": "read_file", "arguments": {"path": "a.py"}, "why": ""}


def test_a_streamed_tool_call_is_put_together_from_its_pieces(monkeypatch: pytest.MonkeyPatch):
    chunks = [
        {"choices": [{"delta": {"reasoning": "I should read it."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "read_", "arguments": '{"pa'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "file", "arguments": 'th": "a.py"}'}}]},
                      "finish_reason": "tool_calls"}]},
    ]
    lines = [f"data: {json.dumps(c)}\n".encode() for c in chunks] + [b"data: [DONE]\n"]
    monkeypatch.setattr(gateway, "_lines", lambda *a, **k: _Reply(b"".join(lines)))
    got = gateway.stream_openai([{"role": "user", "content": "hi"}], {**CFG, "tools": READ}, lambda k, p: None)
    assert json.loads(got.text) == {"tool": "read_file", "arguments": {"path": "a.py"}, "why": ""}
    assert got.reasoning == "I should read it."


# ── a full minute is waited out, not failed ─────────────────────────
GROQ_429 = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
            "tokens per minute (TPM): Limit 8000, Used 7200, Requested 1500. Please try again in 5.4s. Need more?")


def test_a_rate_limited_minute_is_waited_out_and_asked_again(monkeypatch: pytest.MonkeyPatch):
    slept: list[float] = []
    answers = iter([gateway.ProviderError(429, GROQ_429), {"choices": [{"message": {"content": '{"ok": 1}'}}]}])

    def post(*_: Any) -> Any:
        got = next(answers)
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(gateway, "_post", post)
    monkeypatch.setattr(gateway.time, "sleep", slept.append)
    reply = gateway.call_openai([{"role": "user", "content": "hi"}], {**CFG, "seconds": 90})
    assert reply.text == '{"ok": 1}' and slept == [pytest.approx(5.65)]


def test_a_long_wait_or_a_retry_after_header_is_honoured_or_handed_on():
    assert gateway._wait_for(gateway.ProviderError(429, "", retry_after=3.0), left=60) == pytest.approx(3.25)
    assert gateway._wait_for(gateway.ProviderError(429, "Please try again in 1m2s"), left=120) is None
    assert gateway._wait_for(gateway.ProviderError(429, "Please try again in 850ms"), left=60) == pytest.approx(1.1)
    assert gateway._wait_for(gateway.ProviderError(429, GROQ_429), left=6) is None      # no time left to wait
    assert gateway._wait_for(gateway.ProviderError(503, GROQ_429), left=60) is None


def test_openrouter_is_handed_its_other_free_models_to_fall_back_on():
    from pathlib import Path

    from app.ai import lanes
    from app.secrets import Secrets
    lane = next(x for x in lanes.LANES if x.id == "openrouter")
    cfg = lanes.config(lane, Secrets(Path("/nonexistent/secrets.json")))
    body = gateway._body([{"role": "user", "content": "hi"}], cfg, stream=False)
    assert body["models"][0] == lane.model and len(body["models"]) == 3
    assert all(m.endswith(":free") for m in body["models"])
    groq = next(x for x in lanes.LANES if x.id == "groq")
    assert "models" not in gateway._body([], lanes.config(groq, Secrets(Path("/nonexistent/s.json"))), stream=False)


# ── a request is made to fit the lane ───────────────────────────────
def test_a_request_too_big_for_the_lane_is_cut_where_it_matters_least():
    system = {"role": "system", "content": "You answer about the repository. " * 40}
    grounding = {"role": "user", "content": "code piece\n" * 3000}           # ~33k characters of retrieved code
    history = {"role": "assistant", "content": "an earlier answer " * 400}
    question = {"role": "user", "content": "Summarise the frontend and backend trees in four lines."}
    msgs = [system, grounding, history, question]
    room = 4600                                                               # Groq: 8,000 - 3,000 answer - margin
    out = gateway.fit(msgs, room)
    assert gateway._size(out) <= room
    assert out[-1] == question and out[0] == system                           # the question and the rules whole
    assert gateway.CUT in out[1]["content"] and out[1]["content"].startswith("code piece")
    assert msgs[1]["content"] == grounding["content"]                          # the caller's list is not changed


def test_a_request_that_fits_is_sent_as_it_is():
    msgs = [{"role": "system", "content": "short"}, {"role": "user", "content": "hi"}]
    assert gateway.fit(msgs, 4000) is msgs and gateway.fit(msgs, 0) is msgs
