"""The AI features beside the compiler: ask memory, brainstorm, and extract facts from text.

Each one builds its prompt, asks the gateway, and validates the answer. Two have an honest answer of
their own when no model can give one, and label it as such: asking memory quotes the facts it found,
and extracting picks the sentences that state a rule. Brainstorming has none — a template of generic
advice was once saved as though it were a brief about the idea — so with no model it is refused.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .gateway import Gateway, Result, extract_json


def _first_sentence(text: str, limit: int = 220) -> str:
    s = re.split(r"(?<=[.!?])\s", text.strip())[0]
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


# ── ask memory ───────────────────────────────────────────────────
class AskOut(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)


ASK_SYSTEM = """You are NeuroCode's memory. Answer the operator's question using only the memory facts in the
user message. Cite every fact you rely on by its ref in square brackets, like [MEM-142]. If the facts do not
answer the question, say so plainly and say what would need to be found out. At most five sentences.
Reply with one JSON object: {"answer": "...", "citations": ["MEM-142"]}"""


def ask(gw: Gateway, question: str, facts: list[dict[str, Any]], *, actor: str | None = None,
        project_id: str | None = None) -> Result[AskOut]:
    refs = {f["ref"] for f in facts}
    context = "\n".join(f"- {f['ref']} · {f['title']}: {f['body'][:500]}" for f in facts) or "(no fact matched)"
    msgs = [{"role": "system", "content": ASK_SYSTEM},
            {"role": "user", "content": f"Memory facts:\n{context}\n\nQuestion: {question}"}]

    def parse(raw: str) -> AskOut:
        out = AskOut.model_validate(extract_json(raw))
        out.citations = [c for c in dict.fromkeys(out.citations) if c in refs]  # never cite what it was not given
        return out

    def fallback() -> AskOut:
        if not facts:
            return AskOut(answer="Nothing in memory matches that yet. Try other words, or add what you know "
                                 "with Memory → Add from text.")
        top = facts[:4]
        lines = "\n".join(f"• [{f['ref']}] {f['title']}. {_first_sentence(f['body'])}" for f in top)
        return AskOut(answer=f"Here is what memory holds on that:\n{lines}", citations=[f["ref"] for f in top])

    return gw.run(msgs, parse, fallback, offline="memory search", feature="ask", actor=actor, project=project_id)


# ── brainstorm ───────────────────────────────────────────────────
class Phase(BaseModel):
    phase: str
    items: list[str] = Field(default_factory=list)


class BriefOut(BaseModel):
    title: str
    problem: str
    audience: str
    value: str
    mvp: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    roadmap: list[Phase] = Field(default_factory=list)


BRAIN_SYSTEM = """You are a sharp product partner inside NeuroCode. Turn the operator's idea (in any language)
into a brief for the project described. Be concrete and short; no filler. In `risks`, argue against the idea
the way a skeptical senior engineer would. Reply with one JSON object:
{"title": "...", "problem": "...", "audience": "...", "value": "...", "mvp": ["..."], "risks": ["..."],
 "metrics": ["..."], "questions": ["..."], "roadmap": [{"phase": "Week 1", "items": ["..."]}]}"""


def brainstorm(gw: Gateway, idea: str, project: dict[str, Any] | None, *, actor: str | None = None,
               project_id: str | None = None) -> Result[BriefOut]:
    """A brief written by a model. Raises `NoModel` when no lane can answer, and `ProviderError` when
    every lane that tried failed."""
    about = f"Project: {project['name']} · stack: {', '.join(project.get('stack', [])) or 'unknown'}" if project else "No project chosen."
    msgs = [{"role": "system", "content": BRAIN_SYSTEM}, {"role": "user", "content": f"{about}\n\nIdea:\n{idea}"}]
    return gw.ask(msgs, lambda raw: BriefOut.model_validate(extract_json(raw)), feature="brainstorm",
                  actor=actor, project=project_id)


# ── extract facts from text ─────────────────────────────────────
CATEGORIES = ("human", "project", "architecture", "business_rules", "legacy", "database", "bugs", "decisions",
              "incidents", "preferences", "code")


class Candidate(BaseModel):
    title: str
    body: str
    category: str = "project"
    confidence: str = "MEDIUM"
    reason: str = ""


class ExtractOut(BaseModel):
    facts: list[Candidate] = Field(default_factory=list)


EXTRACT_SYSTEM = f"""You extract durable facts for NeuroCode's memory from text the operator pastes: meeting notes,
requirements or chats, in any language. Keep only facts that will still matter next month: business rules,
decisions, constraints, preferences, known bugs, database facts. Skip small talk and one-off tasks. Each fact has a
short title, a one-sentence body, a category from {', '.join(CATEGORIES)}, a confidence (HIGH, MEDIUM or LOW) and the
reason it is true (who said it, or what it rests on). At most eight facts.
Reply with one JSON object: {{"facts": [{{"title": "...", "body": "...", "category": "...", "confidence": "...", "reason": "..."}}]}}"""

POLICY = re.compile(r"\b(must|never|always|should|shall|only|cannot|can't|not allowed|required|mandatory|rule|policy|"
                    r"decided|agreed|deadline)\b", re.I)
HINTS = [
    ("decisions", re.compile(r"\b(decided|agreed|decision|we will|finali[sz]ed)\b", re.I)),
    ("bugs", re.compile(r"\b(bug|error|fails?|broken|crash|issue)\b", re.I)),
    ("database", re.compile(r"\b(table|column|procedure|database|index|schema|migration)\b", re.I)),
    ("preferences", re.compile(r"\b(prefers?|likes?|hates?|wants?)\b", re.I)),
    ("business_rules", re.compile(r"\b(must|never|always|only|rule|policy)\b", re.I)),
]


def extract(gw: Gateway, text: str, project: dict[str, Any] | None, *, actor: str | None = None,
            project_id: str | None = None) -> Result[ExtractOut]:
    about = f"Project: {project['name']}" if project else "No project chosen."
    msgs = [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": f"{about}\n\nText:\n{text}"}]

    def parse(raw: str) -> ExtractOut:
        out = ExtractOut.model_validate(extract_json(raw))
        for c in out.facts:
            c.category = c.category if c.category in CATEGORIES else "project"
            c.confidence = c.confidence.upper() if c.confidence.upper() in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
        out.facts = [c for c in out.facts if c.title.strip() and c.body.strip()][:8]
        return out

    def fallback() -> ExtractOut:
        sentences = [s.strip(" -•*\t") for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 12]
        facts: list[Candidate] = []
        for s in sentences:
            if not POLICY.search(s):
                continue
            category = next((c for c, rx in HINTS if rx.search(s)), "project")
            facts.append(Candidate(title=s if len(s) <= 90 else s[:88].rstrip() + "…", body=s, category=category,
                                   reason="Picked by the offline rules: the sentence states a rule, a decision or a constraint."))
            if len(facts) == 8:
                break
        return ExtractOut(facts=facts)

    return gw.run(msgs, parse, fallback, offline="offline rules", feature="extract", actor=actor, project=project_id)
