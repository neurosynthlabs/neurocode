"""The requirement compiler: a requirement in English or Hinglish in, an implementation plan out.

Providers, in the order `auto` tries them (NEUROCODE_COMPILER pins one):
  deepseek  DEEPSEEK_API_KEY is set              DeepSeek's chat API in JSON mode
  ollama    an Ollama server has the model       a local model; nothing leaves the machine
  rules     always                               a keyword planner that says plainly it is one

The compiler is given the memory facts that match the requirement and records which ones, so a
reader can check what the plan was based on. A model answer that fails to parse or validate is
never shown: the rules planner stands in, and the API logs that it did.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

AGENTS = {
    "AI Commander": "commander", "Architect": "architect", "Researcher": "researcher",
    "Frontend Engineer": "frontend", "Backend Engineer": "backend", "Database Engineer": "database",
    "DevOps Engineer": "devops", "QA Engineer": "qa", "Security Engineer": "security",
    "Code Reviewer": "reviewer", "Documentation Agent": "docs", "Vision Agent": "vision",
}
LAYERS = ("Frontend", "Backend", "Database", "Security", "DevOps", "Documentation")


class Step(BaseModel):
    label: str
    agent: str
    detail: str = ""


class PlanOut(BaseModel):
    title: str
    businessRequirement: str
    technicalRequirement: str
    affectedModules: list[str] = Field(default_factory=list)
    affectedFiles: list[str] = Field(default_factory=list)
    affectedDb: list[str] = Field(default_factory=list)
    architectureImpact: str = ""
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    confidence: int = Field(default=60, ge=0, le=100)
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"
    layers: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    testPlan: list[str] = Field(default_factory=list)
    openQuestions: list[str] = Field(default_factory=list)


@dataclass
class Provider:
    id: str      # deepseek | ollama | rules
    model: str   # the name the UI shows


@dataclass
class Context:
    project: dict[str, Any]
    facts: list[dict[str, Any]]
    answers: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Result:
    plan: PlanOut
    provider: Provider
    ms: int
    cited: list[str]
    fallback: str | None = None


RULES = Provider("rules", "offline planner")


# ── which provider ──────────────────────────────────────────────
def _ollama() -> tuple[str, str]:
    return (os.environ.get("NEUROCODE_OLLAMA_URL", "http://127.0.0.1:11434"),
            os.environ.get("NEUROCODE_OLLAMA_MODEL", "qwen2.5-coder:7b"))


_ollama_seen: dict[str, tuple[float, bool]] = {}


def ollama_ready() -> bool:
    """Is an Ollama server up with the configured model pulled? Remembered for 30 s."""
    url, model = _ollama()
    at, ok = _ollama_seen.get(url + model, (-1e9, False))
    if time.monotonic() - at < 30:
        return ok
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=0.4) as r:
            names = {m.get("name", "") for m in json.loads(r.read()).get("models", [])}
        ok = model in names or f"{model}:latest" in names
    except (OSError, ValueError):
        ok = False
    _ollama_seen[url + model] = (time.monotonic(), ok)
    return ok


def pick() -> Provider:
    want = os.environ.get("NEUROCODE_COMPILER", "auto")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if want in ("auto", "deepseek") and key and _rejected.get("deepseek") != _fp(key):
        return Provider("deepseek", os.environ.get("NEUROCODE_DEEPSEEK_MODEL", "deepseek-chat"))
    if want in ("auto", "ollama") and ollama_ready():
        return Provider("ollama", _ollama()[1])
    return RULES


def status() -> dict[str, str]:
    """What /health reports: the provider that would compile now, and why a configured one is skipped."""
    p = pick()
    out = {"provider": p.id, "model": p.model}
    key = os.environ.get("DEEPSEEK_API_KEY")
    if p.id != "deepseek" and key and _rejected.get("deepseek") == _fp(key):
        out["note"] = "DeepSeek rejected DEEPSEEK_API_KEY. Set a valid key and restart the API."
    return out


# ── model calls ─────────────────────────────────────────────────
class ProviderError(RuntimeError):
    """A provider answered with an HTTP error. 401 or 403 means the key itself is bad."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status


# Keys a provider has rejected, by fingerprint. Not sent again until the key changes, which takes a restart.
_rejected: dict[str, str] = {}


def _fp(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # the body says why: a bad key, no balance, an unknown model
        raise ProviderError(e.code, e.read()[:200].decode(errors="replace")) from e


def call_deepseek(messages: list[dict[str, str]]) -> str:
    base = os.environ.get("NEUROCODE_DEEPSEEK_URL", "https://api.deepseek.com")
    body = _post(f"{base}/chat/completions",
                 {"model": os.environ.get("NEUROCODE_DEEPSEEK_MODEL", "deepseek-chat"), "messages": messages,
                  "response_format": {"type": "json_object"}, "temperature": 0.2, "max_tokens": 3000},
                 {"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"}, 120)
    return body["choices"][0]["message"]["content"]


def call_ollama(messages: list[dict[str, str]]) -> str:
    url, model = _ollama()
    body = _post(f"{url}/api/chat", {"model": model, "messages": messages, "format": "json", "stream": False,
                                     "options": {"temperature": 0.2}}, {}, 300)
    return body["message"]["content"]


CALLS: dict[str, Callable[[list[dict[str, str]]], str]] = {"deepseek": call_deepseek, "ollama": call_ollama}

SYSTEM = """You are the requirement compiler inside NeuroCode, an AI engineering OS. The operator writes
requirements in English or Hinglish, often informally. Turn one requirement into an implementation plan
for the project described in the user message.

Rules:
- Use only what the context supports. Name a file, table or module only when a memory fact mentions it
  or the stack makes it certain; otherwise describe it in words.
- When a business decision is missing, put it in openQuestions instead of guessing.
- Steps are small and ordered. Each step has exactly one owner from: {agents}.
- End with a test step owned by QA Engineer and a review step owned by Code Reviewer. When risk is HIGH
  or CRITICAL, add a final step "Your approval" owned by AI Commander.
- risk is CRITICAL for production data or money movement, HIGH for schema changes or financial logic.
- Write businessRequirement and technicalRequirement in plain English even when the input is Hinglish.

Reply with one JSON object and nothing else, shaped like this:
{schema}"""

SCHEMA_HINT = {
    "title": "imperative title, under 80 characters",
    "businessRequirement": "what this means for the business",
    "technicalRequirement": "what has to change in the code",
    "affectedModules": ["module or service names"],
    "affectedFiles": ["paths, only when supported by a fact"],
    "affectedDb": ["tables or procedures, only when supported by a fact"],
    "architectureImpact": "what changes structurally, or 'none'",
    "risk": "LOW | MEDIUM | HIGH | CRITICAL",
    "confidence": "integer 0-100",
    "priority": "LOW | NORMAL | HIGH | URGENT",
    "layers": ["Frontend | Backend | Database | Security | DevOps | Documentation"],
    "steps": [{"label": "short step", "agent": "one owner", "detail": "one sentence"}],
    "testPlan": ["one test per item"],
    "openQuestions": ["business decisions the plan must not guess"],
}


def messages(requirement: str, ctx: Context) -> list[dict[str, str]]:
    p = ctx.project
    lines = [f"Project: {p['name']} · stack: {', '.join(p.get('stack', [])) or 'unknown'}", p.get("description", "")]
    if ctx.facts:
        lines.append("\nMemory facts (cite the ref when you rely on one):")
        lines += [f"- {f['ref']} · {f['title']}: {f['body'][:400]} (evidence: {', '.join(f.get('evidence', [])[:4])})"
                  for f in ctx.facts]
    if ctx.answers:
        lines.append("\nQuestions the operator has already answered:")
        lines += [f"- {a['q']} → {a['a']}" for a in ctx.answers]
    lines.append(f"\nRequirement:\n{requirement}")
    system = SYSTEM.format(agents=", ".join(AGENTS), schema=json.dumps(SCHEMA_HINT, indent=1))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


# ── making a model's answer safe to store ───────────────────────
def _clip(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip()[:1500]
    if isinstance(v, list):
        return [_clip(x) for x in v[:16]]
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    return v


def agent_name(raw: str) -> str:
    s = raw.strip().lower()
    for name in AGENTS:
        if s in (name.lower(), AGENTS[name]):
            return name
    for hint, name in (("front", "Frontend Engineer"), ("back", "Backend Engineer"), ("api", "Backend Engineer"),
                       ("data", "Database Engineer"), ("sql", "Database Engineer"), ("db", "Database Engineer"),
                       ("qa", "QA Engineer"), ("test", "QA Engineer"), ("review", "Code Reviewer"),
                       ("arch", "Architect"), ("secur", "Security Engineer"), ("devops", "DevOps Engineer"),
                       ("deploy", "DevOps Engineer"), ("doc", "Documentation Agent"), ("research", "Researcher"),
                       ("vision", "Vision Agent")):
        if hint in s:
            return name
    return "AI Commander"


def layer_name(raw: Any) -> str | None:
    s = str(raw).strip().lower()
    for layer in LAYERS:
        if s.startswith(layer.lower()[:4]):
            return layer
    return {"db": "Database", "sql": "Database", "ui": "Frontend", "api": "Backend", "ops": "DevOps"}.get(s)


def parse(raw: str) -> PlanOut:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the answer holds no JSON object")
    data = _clip(json.loads(raw[start:end + 1]))
    data["steps"] = [{**s, "agent": agent_name(str(s.get("agent", "")))}
                     for s in data.get("steps", []) if isinstance(s, dict) and s.get("label")]
    data["layers"] = list(dict.fromkeys(x for x in map(layer_name, data.get("layers", [])) if x))
    for key in ("risk", "priority"):
        if isinstance(data.get(key), str):
            data[key] = data[key].strip().upper()
    if isinstance(data.get("confidence"), (str, float)):
        data["confidence"] = int(float(data["confidence"]))
    return PlanOut.model_validate(data)


# ── the planner that needs no model ─────────────────────────────
KEYWORDS = {
    "Database": ("table", "column", "sql", "sp_", "stored proc", "procedure", "migration", "index", "query",
                 "database", " db ", "trans_", "mst_", "schema", "backfill"),
    "Backend": ("api", "endpoint", "service", "controller", "server", "validat", "calculat", "tax", "invoice",
                "report", "export", "import", " job", "queue", "webhook", "logic", "gst", "rounding", "bulk"),
    "Frontend": (" ui", "screen", "page", "button", "form", "grid", "modal", "dashboard", "css", "layout",
                 "component", "display", "dikh", " show", "upload", "click"),
    "Security": ("auth", "login", "password", "token", "permission", "role", "xss", "injection", "security",
                 "secret", "otp"),
    "DevOps": ("deploy", "pipeline", "docker", " iis", "redis", "load balancer", "kubernetes", "nginx", "ci/cd"),
}
OWNER = {"Database": "Database Engineer", "Backend": "Backend Engineer", "Frontend": "Frontend Engineer",
         "Security": "Security Engineer", "DevOps": "DevOps Engineer"}
WORK = {
    "Database": "Schema or procedure change, with a rollback tested on a snapshot",
    "Backend": "Change the logic behind its interface; no SQL inside a service",
    "Frontend": "Update the screen, its validation and its empty and error states",
    "Security": "Threat-model the change and add the guard before it ships",
    "DevOps": "Change the configuration or pipeline behind a flag",
}
MONEY = ("tax", "gst", "invoice", "price", "amount", "billing", "payment", "salary", "refund", "ledger")
CRITICAL = ("production", " prod ", "payment", "refund", "delete data", "drop table", "live data")
URGENT = ("urgent", "asap", "jaldi", "abhi", "immediately", " p0", "blocker")
DB_IDENT = re.compile(r"\b(?:TRANS|MST|STG|SP|TBL|VW)_[A-Za-z0-9_]+\b")
# Evidence paths that are code, not notes or reports. A `:123` line suffix is kept.
FILE_PATH = re.compile(r"^[\w./-]+\.(?:cs|vb|sql|ts|tsx|js|jsx|mjs|py|java|kt|go|rs|rb|php|cshtml|razor|aspx)(?::\d+)?$", re.I)
SQL_FILE = re.compile(r"\.sql(?::\d+)?$", re.I)


def rules(requirement: str, ctx: Context) -> PlanOut:
    text = f" {requirement.lower()} "
    cited = [f["ref"] for f in ctx.facts]
    top = ctx.facts[:3]  # FTS ranks best first; the tail is weaker evidence, cited but not mined

    def base(path: str) -> str:
        return re.sub(r"\.\w+(?::\d+)?$", "", os.path.basename(path))

    files = list(dict.fromkeys(e for f in top for e in f.get("evidence", []) if FILE_PATH.match(e)))[:8]
    modules = list(dict.fromkeys(base(f) for f in files if not SQL_FILE.search(f) and "test" not in f.lower()))[:6]
    db = list(dict.fromkeys(DB_IDENT.findall(requirement) + [x for f in top for x in DB_IDENT.findall(f.get("body", ""))]
                            + [base(f) for f in files if SQL_FILE.search(f)]))[:6]
    layers = [layer for layer, words in KEYWORDS.items() if any(w in text for w in words)] or ["Backend"]
    if db and "Database" not in layers:
        layers.append("Database")  # the memory behind this requirement reaches into the database
    if any(w in text for w in CRITICAL):
        risk = "CRITICAL"
    elif "Database" in layers or any(w in text for w in MONEY):
        risk = "HIGH"
    elif "Backend" in layers or "Security" in layers:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    start = f", starting from {', '.join(cited[:3])}" if cited else ""
    steps = [Step(label="Map the change", agent="Architect", detail=f"Find every caller, table and rule the change touches{start}.")]
    steps += [Step(label=f"{layer} change", agent=OWNER[layer], detail=WORK[layer]) for layer in layers if layer in OWNER]
    steps += [Step(label="Tests", agent="QA Engineer", detail="A failing test for the reported case first, then unit and regression runs."),
              Step(label="Review", agent="Code Reviewer", detail="Check the diff against the project rules and the memory it cites.")]
    if risk in ("HIGH", "CRITICAL"):
        steps.append(Step(label="Your approval", agent="AI Commander", detail="Nothing ships until you sign it."))

    questions = []
    if "Database" in layers:
        questions.append("What happens to records created under the old behaviour: backfill them, or leave them as they are?")
    if "Frontend" in layers:
        questions.append("Who should see this change: every user, or one role?")
    if risk in ("HIGH", "CRITICAL"):
        questions.append("Does this have to ship by a date, or can it wait for the next release window?")
    if not cited:
        questions.append("Which module owns this today? No memory fact matched the requirement.")

    tests = ["Reproduce the reported case as a failing test", "Unit tests for every rule the change touches",
             "Regression run of the affected module's suite"]
    if "Database" in layers:
        tests.append("The migration applies and rolls back on a snapshot")

    first = re.split(r"(?<=[.!?])\s", requirement.strip())[0][:80].rstrip(" .,;:")
    return PlanOut(
        title=first[:1].upper() + first[1:],
        businessRequirement=f"In your words: “{requirement.strip()}”. The offline planner keeps your wording rather "
                            "than guess at intent. Connect a model for a real restatement.",
        technicalRequirement=f"Touches {', '.join(layers)}. " + (
            f"Memory that applies: {', '.join(cited)}." if cited else "No memory fact matched, so the Architect starts from the call graph."),
        affectedModules=modules, affectedFiles=files, affectedDb=db,
        architectureImpact="Not assessed. The offline planner does not read code; the Architect's first step maps it.",
        risk=risk, confidence=min(60, 35 + 8 * len(cited)),
        priority="URGENT" if any(w in text for w in URGENT) else ("HIGH" if risk in ("HIGH", "CRITICAL") else "NORMAL"),
        layers=layers, steps=steps, testPlan=tests, openQuestions=questions,
    )


def compile_plan(requirement: str, ctx: Context) -> Result:
    provider, t0 = pick(), time.monotonic()
    cited = [f["ref"] for f in ctx.facts]
    fallback = None
    if provider.id != "rules":
        try:
            plan = parse(CALLS[provider.id](messages(requirement, ctx)))
            return Result(plan, provider, round((time.monotonic() - t0) * 1000), cited)
        except Exception as e:  # network, key, quota, malformed JSON, schema: the answer is unusable either way
            if isinstance(e, ProviderError) and e.status in (401, 403) and provider.id == "deepseek":
                _rejected["deepseek"] = _fp(os.environ.get("DEEPSEEK_API_KEY", ""))
            fallback = f"{provider.model} failed ({type(e).__name__}: {str(e)[:160]})"
    return Result(rules(requirement, ctx), RULES, round((time.monotonic() - t0) * 1000), cited, fallback)
