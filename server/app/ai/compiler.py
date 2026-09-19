"""The requirement compiler: a requirement in English or Hinglish in, an implementation plan out.

The compiler is given the memory facts that match the requirement and records which ones, so a reader
can check what the plan was based on. The gateway picks the model. There is no plan without one: a
planner made of keywords used to stand in, and its plans read like a model's — steps, risk, a
confidence it had invented — while understanding nothing of the requirement. With no lane able to
answer, compiling now says so and writes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..data import roster
from .gateway import Gateway, Result, extract_json

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
    #: Only ever what the model said. A model that leaves it out gets no number put in its mouth.
    confidence: int | None = Field(default=None, ge=0, le=100)
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
- End with a test step owned by {tester} and a review step owned by {reviewer}. When risk is HIGH
  or CRITICAL, add a final step "Your approval" owned by {commander}.
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
    system = SYSTEM.format(agents=", ".join(roster.NAMES), tester=roster.TESTER, reviewer=roster.REVIEWER,
                           commander=roster.COMMANDER, schema=json.dumps(SCHEMA_HINT, indent=1))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


# ── making a model's answer safe to store ───────────────────────
#: Words a model uses for an owner that is not quite a roster name, and the agent each one means.
HINTS = (("front", "frontend"), ("back", "backend"), ("api", "backend"), ("data", "database"),
         ("sql", "database"), ("db", "database"), ("qa", "qa"), ("test", "qa"), ("review", "reviewer"),
         ("arch", "architect"), ("secur", "security"), ("devops", "devops"), ("deploy", "devops"),
         ("doc", "docs"), ("research", "researcher"), ("vision", "vision"))


def agent_name(raw: str) -> str:
    s = raw.strip().lower()
    for name, agent_id in roster.IDS_BY_NAME.items():
        if s in (name.lower(), agent_id):
            return name
    for hint, agent_id in HINTS:
        if hint in s and agent_id in roster.BY_ID:
            return roster.BY_ID[agent_id].name
    return roster.COMMANDER


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


def compile_plan(gw: Gateway, requirement: str, ctx: Context, *, actor: str | None = None,
                 project: str | None = None) -> tuple[Result[PlanOut], list[str]]:
    """The plan, how it was made, and the memory refs the compiler was given.

    Raises `NoModel` when no lane can answer, and `ProviderError` when every lane that tried failed —
    carrying the last one's reason — so the caller can say which of the two happened.
    """
    result = gw.ask(messages(requirement, ctx), parse, feature="compile", actor=actor, project=project)
    return result, [f["ref"] for f in ctx.facts]
