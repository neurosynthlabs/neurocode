"""The system screens: the agent roster, the model router, and what the AI gateway has been doing.

The roster is where the interesting change is. An agent's card used to read everything off the agent's
own row — its status, its model, tasks done, success rate — and every one of those was sample text
written once. The row now holds only what the catalogue declares, and it goes out under
`declared`, because nothing in the runtime obeys it. Everything else is derived: the status from the
runs, the lanes from the router, the record from the steps, the spend from the ledger.

The router screen is the same idea. What each feature asks the gateway for is fixed in the code that
calls it, so it is written down here once, next to the words that explain it, and checked against the
call sites by the tests rather than trusted.

The usage report keeps the shape the screen was written against, including `provider`, which the new
ledger records as a lane. Renaming it would have been tidier and would have broken a chart.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..ai import lanes
from ..ai.lanes import CHAT, REVIEW, WRITE, Lane
from ..data import roster
from ..models import Agent
from ..repositories.usage import (
    OFFLINE,
    AgentSpend,
    Answered,
    CallLine,
    CostlyCall,
    CurrentRun,
    DayLine,
    FeatureLine,
    InFlight,
    LaneLine,
    PersonLine,
    ProjectSpend,
    StepRecord,
    Usage,
)
from ..services.runs import EDIT_SYSTEM, REVIEW_SYSTEM
from .work import when

# ── the roster ───────────────────────────────────────────────────

#: What the runtime makes sure of for every agent, whatever its row declares. Each line is a rule the
#: code enforces, not a promise: the worktree and branch per run and the untouched checkout
#: (services/runs.py), the path check on every file a model returns (agent/git.safe_path), the only
#: command that runs and its approval (services/runs._test), the signature (_handoff), the merge
#: permission (api/routes_runs.py), and the second lane for a review (_review).
ENFORCED = (
    "Works in a git worktree and branch of its own. Your checked-out tree is never touched.",
    "A file path a model returns that is absolute, climbs out with '..', or points into .git is refused.",
    "Nothing a model says is ever executed. The only command that runs is the project's own test "
    "command, and the first time in a project only after you approve it.",
    "A run that changed a file stops at your signature. Refuse it and the branch and worktree are removed.",
    "Merging an accepted branch needs the runs:merge permission.",
    "The diff is reviewed through a different lane from the one that wrote it whenever another is open.",
)

#: Agents the runtime never makes a model call as, and why. The commander is skipped when a plan is
#: split into runs; the QA Engineer's step runs a command; the Orchestrator's merges are git's.
NO_CALL = {
    roster.COMMANDER: "Hands the work out. The runtime makes no model call as it.",
    roster.APPROVER: "That is you: every run stops at your signature.",
    roster.TESTER: "Runs the project's own test command. No model is asked.",
    roster.ORCHESTRATOR: "Merges the agents' branches with git. No model is asked.",
}

#: What each kind of step's verdict really measures, in the words the card uses.
OUTCOME_LABEL = {"edit": "edits written", "test": "project tests passed", "review": "model-read reviews"}

#: The part of the user message the runtime writes before the files or the diff, with the per-run
#: pieces named in angle brackets. It is the same text services/runs.py sends.
EDIT_PREAMBLE = ("You are the {agent}.\nProject: <project id>\nRequirement: <the requirement>\n"
                 "Step <n>: <the step's label>\n<the step's detail>\n\nFiles you may change:\n"
                 "<each file's path and content>")
REVIEW_PREAMBLE = "Requirement: <the requirement>\n\nDiff:\n<the run's diff>"


def calls_as(agent: Agent, records: Sequence[StepRecord]) -> str | None:
    """The role the runtime asks a lane for when it works as this agent, or None when it never asks.

    What an agent has really done decides first: owning a review step means the review role, owning an
    edit step the write role. An agent with no history is placed by the names the runtime itself gives
    out — the reviewer, the tester, the merger, the commander — and anyone else would be handed edit
    steps by a plan that named them.
    """
    kinds = {r.kind for r in records}
    if "review" in kinds or agent.name == roster.REVIEWER:
        return REVIEW
    if "edit" in kinds:
        return WRITE
    return None if agent.name in NO_CALL else WRITE


def _status(agent: Agent, flights: Sequence[InFlight]) -> str:
    """Running when the agent owns a step being worked, or a whole run between steps; waiting when a
    step of its is parked on a person. `disabled` is the one status a person sets, so it stands."""
    if agent.status == "disabled":
        return "disabled"
    if any((f.step_status or f.run_status) == "running" for f in flights):
        return "running"
    return "waiting" if flights else "idle"


def _lane(lane: Lane | None) -> dict[str, str] | None:
    return {"lane": lane.id, "model": lane.model} if lane else None


def _current(run: CurrentRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {"runRef": run.run_ref, "taskRef": run.task_ref, "status": run.status, "branch": run.branch,
            "worktree": run.worktree, "startedAt": when(run.started_at), "progress": run.progress,
            "step": run.step, "stepKind": run.step_kind, "stepStatus": run.step_status,
            "filesChanged": run.files_changed, "tokensIn": run.tokens_in, "tokensOut": run.tokens_out}


def pick_run(flights: Sequence[InFlight]) -> str | None:
    """The run an agent's card shows when it is in more than one: a running one before a waiting one,
    then the newest."""
    if not flights:
        return None
    best = max(flights, key=lambda f: ((f.step_status or f.run_status) == "running", f.created_at))
    return best.run_id


def agent_json(agent: Agent, *, tasks_done: int, records: Sequence[StepRecord], flights: Sequence[InFlight],
               current: CurrentRun | None, chains: dict[str, list[Lane]], answered: Answered | None,
               tokens_24h: int, cost_24h: float) -> dict[str, Any]:
    role = calls_as(agent, records)
    chain = chains.get(role, []) if role else []
    prompt = None
    if role == WRITE:
        prompt = {"system": EDIT_SYSTEM, "user": EDIT_PREAMBLE.format(agent=agent.name)}
    elif role == REVIEW:
        prompt = {"system": REVIEW_SYSTEM, "user": REVIEW_PREAMBLE}
    return {
        "id": agent.id, "name": agent.name, "role": agent.role, "icon": agent.icon,
        "status": _status(agent, flights),
        "tasksDone": tasks_done,
        "outcomes": [{"kind": r.kind, "label": OUTCOME_LABEL[r.kind], "decided": r.decided, "good": r.good,
                      "rate": round(100 * r.good / r.decided) if r.decided else None,
                      "avgMinutes": r.minutes}
                     for r in sorted(records, key=lambda r: list(OUTCOME_LABEL).index(r.kind))],
        # Summed from the usage ledger, which records the agent that asked. Free lanes are priced at
        # zero because they are free, so a cost of 0 here is a fact rather than a missing number.
        "tokens24h": tokens_24h, "cost24h": cost_24h,
        "callsAs": role, "noCall": None if role else NO_CALL.get(agent.name),
        "lanes": {"primary": _lane(chain[0] if chain else None),
                  "fallback": _lane(chain[1] if len(chain) > 1 else None),
                  "lastAnswered": {"lane": answered.lane, "model": answered.model, "at": when(answered.at)}
                  if answered else None},
        "prompt": prompt,
        "current": _current(current),
        "declared": {"autonomy": agent.autonomy, "tools": agent.tools or [], "skills": agent.skills or [],
                     "guardrails": agent.guardrails or [], "systemPrompt": agent.system_prompt},
    }


# ── the router ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Route:
    """What one feature asks the gateway for, and what happens when no lane answers."""

    feature: str
    role: str | None
    offline: bool
    avoids_writer: bool
    how: str


#: Read off the call sites, not designed: compiler.py, ai/features.py, services/runs._edit and
#: _review, services/chat.py, services/retrieval.py and the admin test. `offline` is whether the
#: feature has an answer of its own when no lane does.
ROUTES = (
    Route("compile", None, False, False,
          "Asks for no particular role, so every open lane is equal. With none, compiling is refused: "
          "no plan is invented."),
    Route("agent", WRITE, False, False,
          "Starts with the lane the run was given when it was dispatched, then lanes good at writing. "
          "With no lane the step is skipped: no code is invented."),
    Route("review", REVIEW, True, True,
          "Lanes good at reviewing, with the lane that wrote the code moved last. With no lane, or a "
          "failed answer, rules read the diff and the review says so."),
    Route("chat", CHAT, False, False,
          "Lanes good at chat. With no lane the session says so and stops."),
    Route("ask", None, True, False,
          "No particular role. With no lane, memory search answers from the facts alone."),
    Route("brainstorm", None, False, False,
          "No particular role. With no lane, brainstorming is refused: no brief is invented."),
    Route("extract", None, True, False,
          "No particular role. With no lane, rules pull the facts out and say so."),
    Route("embed", None, False, False,
          "The first open lane that serves an embedding model. With none, retrieval stays lexical."),
    Route("test", None, False, False,
          "An admin's test: one tiny call down the one lane it names."),
)

#: How the gateway orders the lanes for a call, in the order `Gateway.chain` sorts them.
ORDERING = ("Open lanes are taken in turn, rotated so agents working at the same time start on different "
            "lanes. The lane a run was given goes first, then lanes good at the role, and a lane the call "
            "should avoid goes last rather than away. A lane that fails hands the call to the next one.")

#: What each preference lets the router use — `Gateway._allowed`, in words.
PREFERENCE_TEXT = {
    "auto": "Every lane that is switched on, has its key and has allowance left.",
    "free": "Only free lanes. The paid lane is never used.",
    "local": "Only Ollama, on this machine. Nothing leaves it.",
    "rules": "No model at all. Features with an offline answer give it; the rest say no model is set up.",
}

#: Everything about a lane's key stays on the admin screen. The rest is what every signed-in person
#: already sees on Runs and Sessions: which lane answered, and with which model.
PRIVATE = ("keyMask", "keySource", "baseUrl", "signup")


def fleet_json(report: list[dict[str, Any]], catalogue: Sequence[Lane],
               lines: Sequence[LaneLine]) -> list[dict[str, Any]]:
    """Every lane with its last day. A lane may have answered under two models when an admin changed
    it, so its ledger lines are summed rather than matched on the model it has now."""
    embeds = {lane.id: lane.embed or None for lane in catalogue}
    out = []
    for row in report:
        mine = [line for line in lines if line.lane == row["id"]]
        calls = sum(line.calls for line in mine)
        tin, tout = sum(line.tokens_in for line in mine), sum(line.tokens_out for line in mine)
        per_in, per_out = lanes.price_of(row["id"])
        known = lanes.priced(row["id"])
        # The lane's price holds for the model it names; a day that ran another model through it has no
        # known cost, even on a free lane.
        all_priced = known and all(lanes.priced_call(line.lane, line.model)
                                   for line in mine if line.tokens_in or line.tokens_out)
        out.append({
            **{k: v for k, v in row.items() if k not in PRIVATE},
            "hosting": "local" if row["api"] == "ollama" else "remote", "embed": embeds.get(row["id"]),
            "priced": known, "usdPerMIn": per_in, "usdPerMOut": per_out,
            "calls24h": calls, "failures24h": sum(line.failures for line in mine),
            "avgMs24h": round(sum(line.avg_ms * line.calls for line in mine) / calls) if calls else 0,
            "tokensIn24h": tin, "tokensOut24h": tout,
            "cost24h": round(tin / 1e6 * per_in + tout / 1e6 * per_out, 4) if all_priced else None,
        })
    return out


def routes_json(chains: dict[str | None, list[Lane]], embed: Lane | None, features: Sequence[FeatureLine],
                offline_reviews: int) -> list[dict[str, Any]]:
    by_feature = {line.feature: line for line in features}
    out = []
    for route in ROUTES:
        line = by_feature.get(route.feature)
        chain = ([embed] if embed else []) if route.feature == "embed" else \
            [] if route.feature == "test" else chains.get(route.role, [])
        # A review the rules wrote is not in the ledger, so it is counted from the runs instead.
        offline = offline_reviews if route.feature == "review" else (line.offline if line else 0)
        out.append({"feature": route.feature, "role": route.role, "offline": route.offline,
                    "avoidsWriter": route.avoids_writer, "how": route.how,
                    "chain": [{"lane": x.id, "model": x.embed if route.feature == "embed" else x.model}
                              for x in chain],
                    "calls24h": line.calls if line else 0, "failures24h": line.failures if line else 0,
                    "offline24h": offline})
    return out


def totals_json(fleet: Sequence[dict[str, Any]], lines: Sequence[LaneLine]) -> dict[str, Any]:
    """The last day across every lane. Local is Ollama, offline is the rules, and remote is the rest —
    including a lane the ledger remembers that the catalogue no longer has."""
    calls = sum(line.calls for line in lines)
    local = sum(line.calls for line in lines if line.lane == "ollama")
    offline = sum(line.calls for line in lines if line.lane == OFFLINE)
    return {"calls": calls, "local": local, "offline": offline, "remote": calls - local - offline,
            "failures": sum(line.failures for line in lines),
            "tokensIn": sum(line.tokens_in for line in lines), "tokensOut": sum(line.tokens_out for line in lines),
            "costUsd": round(sum(lane["cost24h"] or 0 for lane in fleet), 4),
            # False when a lane with no declared price answered: the total is then a floor, and says so.
            "costComplete": not any(lane["cost24h"] is None and lane["calls24h"] for lane in fleet)}


def day_json(line: DayLine) -> dict[str, Any]:
    return {"day": line.day, "calls": line.calls, "model": line.model, "offline": line.offline,
            "tokens": line.tokens, "costUsd": line.cost_usd, "costComplete": not line.unpriced}


def agent_spend_json(line: AgentSpend) -> dict[str, Any]:
    """`agent` is the roster id when the roster knows the agent, and the name the ledger wrote when it
    does not — a renamed or removed agent's spend is still history, under the name it had."""
    known = roster.BY_ID.get(line.agent)
    return {"agent": line.agent, "name": known.name if known else line.agent, "calls": line.calls,
            "tokensIn": line.tokens_in, "tokensOut": line.tokens_out, "costUsd": line.cost_usd,
            "costComplete": not line.unpriced}


def project_spend_json(line: ProjectSpend) -> dict[str, Any]:
    return {"projectId": line.project_id, "projectName": line.project_name, "calls": line.calls,
            "tokensIn": line.tokens_in, "tokensOut": line.tokens_out, "costUsd": line.cost_usd,
            "costComplete": not line.unpriced}


def costly_json(line: CostlyCall) -> dict[str, Any]:
    return {"at": when(line.at), "lane": line.lane, "model": line.model, "feature": line.feature,
            "agent": line.agent, "tokensIn": line.tokens_in, "tokensOut": line.tokens_out,
            "costUsd": line.cost_usd, "runRef": line.run_ref, "taskRef": line.task_ref}


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
                   "avgMs": totals.avg_ms,
                   # A floor whenever `costComplete` is false; None when no call in the window was priced.
                   "costUsd": totals.cost_usd, "costComplete": not totals.unpriced},
        "byDay": [day_json(d) for d in report.by_day],
        "byAgent": [agent_spend_json(a) for a in report.by_agent],
        "byProject": [project_spend_json(p) for p in report.by_project],
        "costliest": [costly_json(c) for c in report.costliest],
        "byFeature": [feature_json(f) for f in report.by_feature],
        "byProvider": [lane_json(line) for line in report.by_lane],
        "recent": [call_json(c, admin=admin) for c in report.recent],
    }
    if report.by_person is not None:   # who uses what is for admins; everyone else sees the totals
        out["byPerson"] = [person_json(p) for p in report.by_person]
    return out
