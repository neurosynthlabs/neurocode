"""The one door to language models.

Every AI feature goes through here, so keys, routing, time-outs, failure handling and the fallback live
in one place. Under `auto` the order is DeepSeek (a key is set), then Ollama (the model is pulled),
then the offline rules, which always work and say plainly that they are rules. A key the provider
rejects is noted and not sent again until it changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from ..db import Store, now_iso
from ..secrets import Secrets

T = TypeVar("T")
PREFERENCES = ("auto", "deepseek", "ollama", "rules")


@dataclass
class Provider:
    id: str      # deepseek | ollama | rules
    model: str   # the name the UI shows


@dataclass
class Result(Generic[T]):
    data: T
    provider: Provider
    ms: int
    fallback: str | None = None

    def meta(self) -> dict[str, Any]:
        return {"provider": self.provider.id, "model": self.provider.model, "ms": self.ms}


class ProviderError(RuntimeError):
    """A provider answered with an HTTP error. 401 or 403 means the key itself is bad."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # the body says why: a bad key, no balance, an unknown model
        raise ProviderError(e.code, e.read()[:200].decode(errors="replace")) from e


Usage = dict[str, int]      # {"in": prompt tokens, "out": completion tokens}
Answer = tuple[str, Usage]


def call_deepseek(messages: list[dict[str, str]], cfg: dict[str, Any]) -> Answer:
    body = _post(f"{cfg['baseUrl']}/chat/completions",
                 {"model": cfg["model"], "messages": messages, "response_format": {"type": "json_object"},
                  "temperature": 0.2, "max_tokens": 3000},
                 {"Authorization": f"Bearer {cfg['key']}"}, 120)
    usage = body.get("usage") or {}
    return body["choices"][0]["message"]["content"], {"in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0)}


def call_ollama(messages: list[dict[str, str]], cfg: dict[str, Any]) -> Answer:
    body = _post(f"{cfg['url']}/api/chat", {"model": cfg["model"], "messages": messages, "format": "json",
                                            "stream": False, "options": {"temperature": 0.2}}, {}, 300)
    return body["message"]["content"], {"in": body.get("prompt_eval_count", 0), "out": body.get("eval_count", 0)}


# A provider returns its text and the tokens it counted. A stand-in (a test) may return just the text.
CALLS: dict[str, Callable[[list[dict[str, str]], dict[str, Any]], Answer | str]] = {
    "deepseek": call_deepseek, "ollama": call_ollama}


def _split(out: Answer | str) -> Answer:
    return (out[0], out[1] or {}) if isinstance(out, tuple) else (out, {})


def extract_json(raw: str) -> dict[str, Any]:
    """The first JSON object in a model's answer, even when it is wrapped in prose or a code fence."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the answer holds no JSON object")
    return clip(json.loads(raw[start:end + 1]))


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
    def __init__(self, store: Store, secrets: Secrets) -> None:
        self.store, self.secrets = store, secrets
        self._rejected: str | None = None                      # fingerprint of a DeepSeek key that was refused
        self._ollama_seen: tuple[float, str, bool] = (-1e9, "", False)

    # ── configuration ────────────────────────────────────────────
    def preference(self) -> str:
        """The environment (NEUROCODE_COMPILER) wins over the workspace setting; tests and CI rely on it."""
        pref = os.environ.get("NEUROCODE_COMPILER") or self.store.setting("ai.preference", "auto")
        return pref if pref in PREFERENCES else "auto"

    def deepseek(self) -> dict[str, Any]:
        cfg = self.store.setting("ai.deepseek", {}) or {}
        return {"key": self.secrets.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY") or None,
                "model": cfg.get("model") or os.environ.get("NEUROCODE_DEEPSEEK_MODEL") or "deepseek-chat",
                "baseUrl": (cfg.get("baseUrl") or os.environ.get("NEUROCODE_DEEPSEEK_URL") or "https://api.deepseek.com").rstrip("/")}

    def ollama(self) -> dict[str, Any]:
        cfg = self.store.setting("ai.ollama", {}) or {}
        return {"url": (cfg.get("url") or os.environ.get("NEUROCODE_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/"),
                "model": cfg.get("model") or os.environ.get("NEUROCODE_OLLAMA_MODEL") or "qwen2.5-coder:7b"}

    def config(self, provider_id: str) -> dict[str, Any]:
        return self.deepseek() if provider_id == "deepseek" else self.ollama()

    def key_source(self) -> str | None:
        if self.secrets.get("deepseek_api_key"):
            return "workspace"
        return "environment" if os.environ.get("DEEPSEEK_API_KEY") else None

    def rejected(self) -> bool:
        key = self.deepseek()["key"]
        return bool(key) and self._rejected == _fp(key)

    def forget_rejection(self) -> None:
        self._rejected = None

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

    def pick(self) -> Provider | None:
        """The model that would answer now, or None when only the offline rules are available."""
        want = self.preference()
        ds = self.deepseek()
        if want in ("auto", "deepseek") and ds["key"] and not self.rejected():
            return Provider("deepseek", ds["model"])
        if want in ("auto", "ollama") and self.ollama_ready():
            return Provider("ollama", self.ollama()["model"])
        return None

    def status(self) -> dict[str, Any]:
        p = self.pick()
        out: dict[str, Any] = {"provider": p.id, "model": p.model} if p else {"provider": "rules", "model": "offline planner"}
        if p is None and self.rejected() and self.preference() in ("auto", "deepseek"):
            out["note"] = "DeepSeek rejected the API key. Set a valid key in Admin → AI providers."
        return out

    # ── calls ────────────────────────────────────────────────────
    def run(self, messages: list[dict[str, str]], parse: Callable[[str], T], fallback: Callable[[], T], *,
            offline: str = "offline planner", feature: str = "compile", actor: str | None = None,
            project: str | None = None) -> Result[T]:
        """Ask the chosen model and validate its answer. No model, or an unusable answer: the rules stand in.
        Every attempt and every offline answer is written to the usage ledger."""
        provider, t0 = self.pick(), time.monotonic()
        reason = None
        if provider is not None:
            usage: Usage = {}
            try:
                raw, usage = _split(CALLS[provider.id](messages, self.config(provider.id)))
                data = parse(raw)
            except Exception as e:  # network, key, quota, malformed JSON, schema: the answer is unusable either way
                if isinstance(e, ProviderError) and e.status in (401, 403) and provider.id == "deepseek":
                    self._rejected = _fp(self.deepseek()["key"] or "")
                reason = f"{provider.model} failed ({type(e).__name__}: {str(e)[:160]})"
                self._record(feature, provider, False, _ms(t0), usage, actor, project, reason)
            else:
                result = Result(data, provider, _ms(t0))
                self._record(feature, provider, True, result.ms, usage, actor, project)
                return result
        t1 = time.monotonic()
        result = Result(fallback(), Provider("rules", offline), _ms(t0), reason)
        self._record(feature, result.provider, True, _ms(t1), {}, actor, project)
        return result

    def test(self, provider_id: str, actor: str | None = None) -> dict[str, Any]:
        """One tiny round trip, so the admin screen can say whether a provider really answers."""
        if provider_id == "rules":
            return {"ok": True, "ms": 0, "detail": "The offline rules need no model."}
        cfg = self.config(provider_id)
        if provider_id == "deepseek" and not cfg["key"]:
            return {"ok": False, "ms": 0, "detail": "No API key is set."}
        provider, t0, usage = Provider(provider_id, cfg["model"]), time.monotonic(), {}
        try:
            raw, usage = _split(CALLS[provider_id](
                [{"role": "system", "content": 'Reply with the JSON object {"ok": true} and nothing else.'},
                 {"role": "user", "content": "ping"}], cfg))
            extract_json(raw)
            if provider_id == "deepseek":
                self._rejected = None
            self._record("test", provider, True, _ms(t0), usage, actor, None)
            return {"ok": True, "ms": _ms(t0), "detail": f"{cfg['model']} answered."}
        except Exception as e:  # report whatever went wrong; this is a diagnostic
            if isinstance(e, ProviderError) and e.status in (401, 403) and provider_id == "deepseek":
                self._rejected = _fp(cfg["key"])
            detail = (str(e) or type(e).__name__)[:200]
            self._record("test", provider, False, _ms(t0), usage, actor, None, detail)
            return {"ok": False, "ms": _ms(t0), "detail": detail}

    def _record(self, feature: str, provider: Provider, ok: bool, ms: int, usage: Usage, actor: str | None,
                project: str | None, error: str = "") -> None:
        """One line in the usage ledger. The ledger must never break the feature it measures."""
        try:
            self.store.execute(
                "INSERT INTO ai_calls(at, feature, provider, model, ok, ms, tokens_in, tokens_out, user_id, project_id, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now_iso(), feature, provider.id, provider.model, int(ok), ms, int(usage.get("in") or 0),
                 int(usage.get("out") or 0), actor, project, error[:300]))
        except sqlite3.Error:
            pass
