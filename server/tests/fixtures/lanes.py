"""A model lane for tests that need a model, with no network behind it.

The gateway is the real one — routing, parsing, the ledger line — and only the provider call is
replaced. The router is pinned to Groq, Groq is given a key, and every call Groq receives is answered
from a script, in order: a string is the model's reply, an exception is the provider failing.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai import gateway as gateway_module

LANE_MODEL = "llama-3.3-70b-versatile"

#: A plan a model might write for an invoice-tax requirement: steps with owners, two questions it
#: would not guess, and a confidence of its own.
PLAN = {
    "title": "Fix the interstate tax split on invoices",
    "businessRequirement": "Interstate invoices must charge IGST, not CGST and SGST.",
    "technicalRequirement": "Correct the place-of-supply check in the tax calculation.",
    "affectedModules": ["tax"], "affectedFiles": [], "affectedDb": ["TRANS_INVOICE"],
    "architectureImpact": "none", "risk": "HIGH", "confidence": 72, "priority": "HIGH",
    "layers": ["Backend", "Database"],
    "steps": [{"label": "Map the change", "agent": "Architect", "detail": "Find every caller."},
              {"label": "Fix the calculation", "agent": "backend engineer", "detail": "Place of supply."},
              {"label": "Tests", "agent": "QA Engineer", "detail": "Interstate and intrastate cases."},
              {"label": "Review", "agent": "Code Reviewer", "detail": "Against the tax rules."}],
    "testPlan": ["An interstate invoice charges IGST"],
    "openQuestions": ["Re-issue the invoices already sent, or leave them?",
                      "Does this ship before the month-end close?"],
}

#: A brief a model might write for an idea.
BRIEF = {
    "title": "Honest OPD wait times", "problem": "Patients wait without knowing how long.",
    "audience": "OPD patients and front-desk staff", "value": "Fewer people crowding the desk.",
    "mvp": ["An SMS with the expected wait"], "risks": ["The estimate is wrong and trust drops"],
    "metrics": ["Desk queries per hour"], "questions": ["Which clinics first?"],
    "roadmap": [{"phase": "Week 1", "items": ["Measure today's waits"]}],
}


def answering(monkeypatch: pytest.MonkeyPatch, *script: str | dict[str, Any] | Exception) -> list[Any]:
    """Pin the router to one lane and answer its calls from `script`. Returns the messages it was sent."""
    sent: list[Any] = []
    replies = list(script)

    def reply(messages: list[dict[str, str]], cfg: dict[str, Any]) -> str:
        sent.append(messages)
        answer = replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)

    monkeypatch.setenv("NEUROCODE_COMPILER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setitem(gateway_module.CALLS, "groq", reply)
    return sent


def no_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a laptop with no key looks like to the router: no lane may answer, and nothing is called."""
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")
