"""The requirement compiler: a requirement in English or Hinglish in, an implementation plan out.

The compiler is given the memory facts that match the requirement and records which ones, so a reader
can check what the plan was based on. The gateway picks the model; when there is none, or its answer
fails validation, the keyword planner below stands in.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from .gateway import Gateway, Result, extract_json

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
class Context:
    project: dict[str, Any]
    facts: list[dict[str, Any]]
    answers: list[dict[str, str]] = field(default_factory=list)


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
    import json

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
    data = extract_json(raw)
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


def compile_plan(gw: Gateway, requirement: str, ctx: Context) -> tuple[Result[PlanOut], list[str]]:
    """The plan, how it was made, and the memory refs the compiler was given."""
    result = gw.run(messages(requirement, ctx), parse, lambda: rules(requirement, ctx), offline="offline planner")
    return result, [f["ref"] for f in ctx.facts]
