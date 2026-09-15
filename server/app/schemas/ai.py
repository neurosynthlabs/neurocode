"""A brainstorm row, back in the shape the Brainstorm page reads.

The screen reads one `brief` document: a headline, the problem, who feels it, what it is worth, the
smallest thing worth building, the case against it, how you would know, what to answer first, and a
roadmap. The row keeps that as the stages of an idea plus three lists of its own, because stages and
lists are what can be queried a year later — a blob of JSON is not.

This file is the seam between the two, so it is also where the stages are named. Both halves spell
them here and nowhere else.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import Brainstorm

#: The stages a brief's prose is kept as, in the order an idea is walked through. The first one also
#: carries the brief's headline — the row has no title of its own, and a stage's title is exactly
#: that: what this part of the idea is called.
PROBLEM, AUDIENCE, VALUE, METRICS, QUESTIONS = "Problem", "Audience", "Value", "Metrics", "Open questions"


def stamp(at: datetime | None) -> str:
    """"15 Sep 21:44" — the short local time the cards have always shown, not an ISO timestamp."""
    return at.astimezone().strftime("%d %b %H:%M") if at else ""


def brainstorm_nodes(bid: str, *, title: str, problem: str, audience: str, value: str,
                     metrics: list[str], questions: list[str]) -> list[dict[str, Any]]:
    """A brief's prose as the stage nodes the row stores."""
    stages = ((PROBLEM, title, [problem]), (AUDIENCE, AUDIENCE, [audience]), (VALUE, VALUE, [value]),
              (METRICS, METRICS, metrics), (QUESTIONS, QUESTIONS, questions))
    return [{"id": f"{bid}-{n}", "stage": stage, "title": heading,
             "items": [str(item) for item in items if item]}
            for n, (stage, heading, items) in enumerate(stages)]


def _node(nodes: list[Any], stage: str) -> dict[str, Any]:
    found = next((n for n in nodes if isinstance(n, dict) and n.get("stage") == stage), None)
    return found or {}


def _items(nodes: list[Any], stage: str) -> list[str]:
    return [str(item) for item in _node(nodes, stage).get("items") or []]


def _line(nodes: list[Any], stage: str) -> str:
    """A stage that holds one paragraph rather than a list — the problem, the audience, the value."""
    items = _items(nodes, stage)
    return items[0] if items else ""


def brainstorm_json(brainstorm: Brainstorm) -> dict[str, Any]:
    """The row, reassembled into the document the Brainstorm page was written against."""
    nodes = brainstorm.nodes if isinstance(brainstorm.nodes, list) else []
    roadmap = brainstorm.roadmap if isinstance(brainstorm.roadmap, list) else []
    return {
        "id": brainstorm.id, "ref": brainstorm.ref, "idea": brainstorm.idea,
        "projectId": brainstorm.project_id,
        "brief": {
            "title": _node(nodes, PROBLEM).get("title") or brainstorm.idea[:80],
            "problem": _line(nodes, PROBLEM), "audience": _line(nodes, AUDIENCE),
            "value": _line(nodes, VALUE), "mvp": list(brainstorm.mvp or []),
            "risks": list(brainstorm.devils_advocate or []), "metrics": _items(nodes, METRICS),
            "questions": _items(nodes, QUESTIONS),
            "roadmap": [{"phase": phase.get("phase", ""), "items": list(phase.get("items") or [])}
                        for phase in roadmap if isinstance(phase, dict)],
        },
        "compiler": brainstorm.compiler or {}, "by": brainstorm.requested_by,
        "createdAt": stamp(brainstorm.created_at),
    }
