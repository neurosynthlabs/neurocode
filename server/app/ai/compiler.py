"""The requirement compiler: a requirement in a person's own words in, an implementation plan out.

The compiler is given the project's own instructions (its AGENTS.md and CLAUDE.md), the taste rules a
person adopted, the memory facts that match the requirement and the pieces of code and documents retrieval
finds for it — and the caller records which, so a reader can check what the plan was based on. The gateway picks the model. There is no plan without one: a
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

#: What one retrieval piece may put in the prompt. Six of them stay under a few thousand tokens.
PIECE_CHARS = 900

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
    #: What "done" means, one checkable sentence each. A person may edit them before dispatch.
    acceptanceCriteria: list[str] = Field(default_factory=list)


@dataclass
class Context:
    project: dict[str, Any]
    facts: list[dict[str, Any]]
    answers: list[dict[str, str]] = field(default_factory=list)
    #: The project's instruction files, already read and capped (`services/instructions.py`).
    instructions: str = ""
    #: Retrieval's pieces for the requirement: `{kind, ref, path, text}`, code and documents only.
    pieces: list[dict[str, Any]] = field(default_factory=list)
    #: The taste rules a person adopted, one `- [TASTE-n] sentence` per line (`services/taste.py`).
    taste: str = ""
    #: Where the plan must not change anything: a source kept as reference, a project this one reads from.
    #: One line each, `label/ — why`.
    readonly: list[str] = field(default_factory=list)


SYSTEM = """You are the requirement compiler inside NeuroCode, an AI engineering OS. The operator writes
requirements in any language, often informally. Turn one requirement into an implementation plan
for the project described in the user message.

Rules:
- Use only what the context supports. Name an existing file, table or module only when a memory fact
  or a code piece shows it, or the stack makes it certain; otherwise describe it in words.
- A file the change must create may be named in affectedFiles too; it will be checked against the code
  index and shown as a new file.
- Follow the project's instructions where they bear on the plan: its conventions, its commands, what it
  says must not be touched.
- When a business decision is missing, put it in openQuestions instead of guessing.
- Steps are small and ordered. Each step has exactly one owner from: {agents}.
- End with a test step owned by {tester} and a review step owned by {reviewer}. When risk is HIGH
  or CRITICAL, add a final step "Your approval" owned by {commander}.
- risk is CRITICAL for production data or money movement, HIGH for schema changes or financial logic.
- Write businessRequirement and technicalRequirement in plain English whatever language the input is in.
- acceptanceCriteria are what "done" means: 2 to 6 sentences, each one a person or a test can check
  against the finished change ("An interstate invoice shows IGST and no CGST line"), never a step.

Reply with one JSON object and nothing else, shaped like this:
{schema}"""

SCHEMA_HINT = {
    "title": "imperative title, under 80 characters",
    "businessRequirement": "what this means for the business",
    "technicalRequirement": "what has to change in the code",
    "affectedModules": ["module or service names"],
    "affectedFiles": ["paths, only when supported by a fact or a code piece, or a file the change creates"],
    "affectedDb": ["tables or procedures, only when supported by a fact"],
    "architectureImpact": "what changes structurally, or 'none'",
    "risk": "LOW | MEDIUM | HIGH | CRITICAL",
    "confidence": "integer 0-100",
    "priority": "LOW | NORMAL | HIGH | URGENT",
    "layers": ["Frontend | Backend | Database | Security | DevOps | Documentation"],
    "steps": [{"label": "short step", "agent": "one owner", "detail": "one sentence"}],
    "testPlan": ["one test per item"],
    "openQuestions": ["business decisions the plan must not guess"],
    "acceptanceCriteria": ["one checkable sentence per item"],
}


def messages(requirement: str, ctx: Context) -> list[dict[str, str]]:
    """The prompt, stable parts first — the system text, the project, its instructions, then what this
    requirement found — so a provider that caches a prompt's opening can reuse it across compiles."""
    import json

    p = ctx.project
    lines = [f"Project: {p['name']} · stack: {', '.join(p.get('stack', [])) or 'unknown'}", p.get("description", "")]
    if ctx.instructions:
        lines.append("\nThe project's instructions, from files in its repository (follow them where they "
                     f"bear on the plan):\n{ctx.instructions}")
    if ctx.taste:
        lines.append("\nHow this team likes the work done — rules a person adopted from their own decisions "
                     f"(follow them unless the requirement says otherwise):\n{ctx.taste}")
    if ctx.readonly:
        lines.append("\nRead only — read these for context, and never plan a change inside them:")
        lines += [f"- {x}" for x in ctx.readonly]
    if ctx.facts:
        lines.append("\nMemory facts (cite the ref when you rely on one):")
        lines += [f"- {f['ref']} · {f['title']}: {f['body'][:400]} (evidence: {', '.join(f.get('evidence', [])[:4])})"
                  for f in ctx.facts]
    if ctx.pieces:
        lines.append("\nCode and documents from this repository that bear on the requirement (the ref is "
                     "path#symbol:line):")
        lines += [f"[{x['kind']} · {x['ref']}]\n{x['text'][:PIECE_CHARS]}" for x in ctx.pieces]
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


#: Acceptance criteria a plan keeps, and how long one may be — the same rule for the model and a person.
MAX_CRITERIA = 12
MAX_CRITERION = 300


def criteria(raw: Any) -> list[str]:
    """Checkable sentences, trimmed, deduplicated and capped. Anything that is not text is dropped."""
    items = raw if isinstance(raw, list) else []
    clean = (" ".join(str(x).split())[:MAX_CRITERION] for x in items if isinstance(x, str))
    return list(dict.fromkeys(c for c in clean if c))[:MAX_CRITERIA]


def parse(raw: str) -> PlanOut:
    data = extract_json(raw)
    data["steps"] = [{**s, "agent": agent_name(str(s.get("agent", "")))}
                     for s in data.get("steps", []) if isinstance(s, dict) and s.get("label")]
    data["layers"] = list(dict.fromkeys(x for x in map(layer_name, data.get("layers", [])) if x))
    data["acceptanceCriteria"] = criteria(data.get("acceptanceCriteria"))
    for key in ("risk", "priority"):
        if isinstance(data.get(key), str):
            data[key] = data[key].strip().upper()
    if isinstance(data.get("confidence"), (str, float)):
        data["confidence"] = int(float(data["confidence"]))
    return PlanOut.model_validate(data)


# ── revising a plan from a person's comments ──────────────────────
class Reply(BaseModel):
    comment: int
    reply: str


class RevisionOut(BaseModel):
    """What a revision may change: the steps, the questions, the files, what done means — and it says what
    it changed and answers each comment. The requirement and its reading stay the person's."""

    steps: list[Step] = Field(min_length=1)
    openQuestions: list[str] = Field(default_factory=list)
    affectedFiles: list[str] = Field(default_factory=list)
    acceptanceCriteria: list[str] = Field(default_factory=list)
    summary: str = ""
    replies: list[Reply] = Field(default_factory=list)


REVISE_SYSTEM = """You are the requirement compiler inside NeuroCode, revising a plan you wrote before a person
dispatches it. The person left comments on the plan and on its steps. Rewrite the plan so every comment is
dealt with, and change nothing a comment does not ask for.

What each comment kind asks:
- comment: take it into account.
- split: the step it is on is too big — split it into smaller steps.
- remove: the step it is on should not be done — drop it, unless the plan cannot work without it; then keep
  it and say why in the reply.
- why: the person asks why — answer in the reply, and change the step only if the answer shows it is wrong.
- risky: the person thinks the step is risky — make it safer (smaller, reversible, tested first) or say in
  the reply why it is not.

Rules:
- Steps are small and ordered. Each step has exactly one owner from: {agents}.
- Keep a test step owned by {tester} and a review step owned by {reviewer} at the end.
- Follow the project's instructions and the team's taste rules where they bear on the plan.
- affectedFiles: the full list after the revision; leave it empty to keep the plan's files as they are.
- acceptanceCriteria: the full list after the revision; leave it empty to keep them as they are.
- summary: two or three sentences on what changed and why.
- replies: one per comment, by its id, in a sentence or two.

Reply with one JSON object and nothing else, shaped like this:
{schema}"""

REVISE_HINT = {
    "steps": [{"label": "short step", "agent": "one owner", "detail": "one sentence"}],
    "openQuestions": ["business decisions the plan must not guess"],
    "affectedFiles": ["paths, or empty to keep the plan's"],
    "acceptanceCriteria": ["one checkable sentence per item, or empty to keep the plan's"],
    "summary": "what changed and why",
    "replies": [{"comment": 12, "reply": "one or two sentences"}],
}


def revise_messages(requirement: str, ctx: Context, plan: dict[str, Any],
                    comments: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The revise prompt: the same stable opening as a compile — project, instructions, taste, facts, pieces
    — then the plan as it stands and the comments on it."""
    import json

    opening = messages(requirement, ctx)[1]["content"]
    lines = [opening, "\nThe plan as it stands:"]
    lines += [f"{s['n']}. {s['label']} ({s['agent']}) — {s['detail']}" for s in plan["steps"]]
    if plan.get("openQuestions"):
        lines.append("Open questions: " + " · ".join(plan["openQuestions"]))
    if plan.get("affectedFiles"):
        lines.append("Files: " + ", ".join(plan["affectedFiles"]))
    if plan.get("acceptanceCriteria"):
        lines.append("Acceptance criteria: " + " · ".join(plan["acceptanceCriteria"]))
    lines.append("\nThe person's comments (id · kind · where · text):")
    for c in comments:
        where = f"on step {c['step']['n']} \"{c['step']['label']}\"" if c.get("step") else "on the whole plan"
        lines.append(f"[{c['id']}] {c['kind']} · {where} · {c['body']}")
    system = REVISE_SYSTEM.format(agents=", ".join(roster.NAMES), tester=roster.TESTER, reviewer=roster.REVIEWER,
                                  schema=json.dumps(REVISE_HINT, indent=1))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_revision(ids: set[int]):
    """A revision safe to store: owners from the roster, criteria trimmed, and replies only to comments it
    was shown."""
    def parse(raw: str) -> RevisionOut:
        data = extract_json(raw)
        data["steps"] = [{**s, "agent": agent_name(str(s.get("agent", "")))}
                         for s in data.get("steps", []) if isinstance(s, dict) and s.get("label")]
        data["acceptanceCriteria"] = criteria(data.get("acceptanceCriteria"))
        data["openQuestions"] = [q for q in data.get("openQuestions") or [] if isinstance(q, str) and q.strip()]
        data["affectedFiles"] = [f for f in data.get("affectedFiles") or [] if isinstance(f, str) and f.strip()]
        data["replies"] = [r for r in data.get("replies") or []
                           if isinstance(r, dict) and isinstance(r.get("comment"), int) and r["comment"] in ids
                           and isinstance(r.get("reply"), str)]
        out = RevisionOut.model_validate(data)
        out.summary = " ".join(out.summary.split())[:1_000]
        return out
    return parse


def revise_plan(gw: Gateway, requirement: str, ctx: Context, plan: dict[str, Any], comments: list[dict[str, Any]], *,
                actor: str | None = None, project: str | None = None) -> Result[RevisionOut]:
    """The plan rewritten to deal with the comments. A revise is a compile, so it is ledgered as one and
    routed like one. Raises `NoModel` / `ProviderError` as compiling does."""
    ids = {int(c["id"]) for c in comments}
    return gw.ask(revise_messages(requirement, ctx, plan, comments), parse_revision(ids), feature="compile",
                  actor=actor, project=project)


def compile_plan(gw: Gateway, requirement: str, ctx: Context, *, actor: str | None = None,
                 project: str | None = None) -> tuple[Result[PlanOut], list[str]]:
    """The plan, how it was made, and the memory refs the compiler was given.

    Raises `NoModel` when no lane can answer, and `ProviderError` when every lane that tried failed —
    carrying the last one's reason — so the caller can say which of the two happened.
    """
    result = gw.ask(messages(requirement, ctx), parse, feature="compile", actor=actor, project=project)
    return result, [f["ref"] for f in ctx.facts]
