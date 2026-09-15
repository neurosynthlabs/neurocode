"""The system screens: the agent roster, and what the AI gateway has been doing.

The roster is where the interesting change is. An agent's card used to read three numbers off the
agent's own row — tasks done, success rate, average duration — and those numbers were written once and
never again. They arrive here as arguments now, counted from the tasks and the runs themselves, and
the field names are exactly the ones the card already reads.

The usage report keeps the shape the screen was written against, including `provider`, which the new
ledger records as a lane. Renaming it would have been tidier and would have broken a chart.
"""
from __future__ import annotations

from typing import Any

from ..models import Agent
from ..repositories.usage import CallLine, DayLine, FeatureLine, LaneLine, PersonLine, Usage
from .work import when


def agent_json(agent: Agent, *, tasks_done: int = 0, success_rate: int = 0, avg_minutes: int = 0,
               tokens_24h: int = 0, cost_24h: float = 0.0) -> dict[str, Any]:
    return {
        "id": agent.id, "name": agent.name, "role": agent.role, "icon": agent.icon,
        "model": agent.model, "fallbackModel": agent.fallback_model, "status": agent.status,
        "tools": agent.tools or [], "skills": agent.skills or [], "autonomy": agent.autonomy,
        "tasksDone": tasks_done, "successRate": success_rate, "avgMinutes": avg_minutes,
        # Summed from the usage ledger, which records the agent that asked. Free lanes are priced at
        # zero because they are free, so a cost of 0 here is a fact rather than a missing number.
        "tokens24h": tokens_24h, "cost24h": cost_24h,
        "systemPrompt": agent.system_prompt, "guardrails": agent.guardrails or [],
    }


def day_json(line: DayLine) -> dict[str, Any]:
    return {"day": line.day, "calls": line.calls, "model": line.model, "offline": line.offline,
            "tokens": line.tokens}


def feature_json(line: FeatureLine) -> dict[str, Any]:
    return {"feature": line.feature, "calls": line.calls, "model": line.model,
            "offline": line.offline, "failures": line.failures, "tokensIn": line.tokens_in,
            "tokensOut": line.tokens_out, "avgMs": line.avg_ms}


def lane_json(line: LaneLine) -> dict[str, Any]:
    """`provider` is the lane. The old ledger stored both and they never disagreed."""
    return {"provider": line.lane, "model": line.model, "calls": line.calls,
            "failures": line.failures, "tokensIn": line.tokens_in, "tokensOut": line.tokens_out,
            "avgMs": line.avg_ms}


def call_json(line: CallLine, *, admin: bool) -> dict[str, Any]:
    """One line of the ledger. Who made the call is left out for everyone but an admin."""
    return {"at": when(line.at), "feature": line.feature, "provider": line.lane, "model": line.model,
            "ok": line.ok, "ms": line.ms, "tokensIn": line.tokens_in, "tokensOut": line.tokens_out,
            "error": line.error, "by": line.by if admin else None}


def person_json(line: PersonLine) -> dict[str, Any]:
    return {"name": line.name, "calls": line.calls, "tokens": line.tokens}


def usage_json(report: Usage, *, admin: bool) -> dict[str, Any]:
    totals = report.totals
    out: dict[str, Any] = {
        "days": report.days,
        "totals": {"calls": totals.calls, "modelCalls": totals.model_calls,
                   "offline": totals.offline, "failures": totals.failures,
                   "tokensIn": totals.tokens_in, "tokensOut": totals.tokens_out,
                   "avgMs": totals.avg_ms},
        "byDay": [day_json(d) for d in report.by_day],
        "byFeature": [feature_json(f) for f in report.by_feature],
        "byProvider": [lane_json(line) for line in report.by_lane],
        "recent": [call_json(c, admin=admin) for c in report.recent],
    }
    if report.by_person is not None:   # who uses what is for admins; everyone else sees the totals
        out["byPerson"] = [person_json(p) for p in report.by_person]
    return out
