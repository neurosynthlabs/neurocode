"""Runs and sessions, in the shape the Live Runs and Sessions screens read.

A run's children arrive as an argument rather than through a relationship, on purpose: the caller has
already fetched them in one query for the whole list, and touching an unloaded relationship inside
async code raises rather than loading.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..ai import lanes
from ..models import Chat, ChatMessage, Run, RunLog, RunStep
from .work import when

#: The share of a lane's window at which a session folds its older turns. Written here, not imported
#: from services/chat, because the schemas sit below the services; services/chat reads it from here.
AUTO_COMPACT_AT = 0.8


def run_step_json(step: RunStep, *, grounding: dict[str, Any] | None = None,
                  taken_back: dict[str, Any] | None = None) -> dict[str, Any]:
    """One step. What an agent step was handed (`grounding`: the instruction files, retrieval's pieces and
    the files it read), the question it asked and the answer it got, the commit it left, and — for a
    step a revert took back — which revert, are there only when they are.

    `attempts` is there only when there was more than one try to speak of, either because the step was
    tried again or because its policy allows it to be. A step tried once, as most are, says nothing
    about tries at all: a screen showing "attempt 1 of 1" everywhere is noise, not information."""
    tried = step.attempts > 1 or step.max_attempts > 1
    return {"n": step.n, "kind": step.kind, "label": step.label, "agent": step.agent,
            "status": step.status, "detail": step.detail, "ms": step.ms,
            **({"attempts": step.attempts, "maxAttempts": step.max_attempts} if tried else {}),
            **({"child": step.child_run_id} if step.child_run_id else {}),
            **({"commitSha": step.commit_sha} if step.commit_sha else {}),
            **({"question": step.question, "answer": step.answer or None} if step.question else {}),
            **({"grounding": grounding} if grounding else {}),
            **({"takenBack": taken_back} if taken_back else {})}


def run_log_json(line: RunLog) -> dict[str, Any]:
    return {"id": line.id, "at": when(line.at), "step": line.step, "level": line.level,
            "line": line.line}


#: Kept on the run's review document, but shown beside it: the project's checks, a goal run's
#: completion check, and a re-read in flight, which the screen reads from the run's own step.
_BESIDE_REVIEW = ("checks", "goal", "reviewing", "grounding", "commits", "asks", "reverts", "resumes",
                  "references", "sources", "elsewhere", "awaiting")


def run_json(run: Run, *, project_name: str = "", children: Sequence[Run] = (),
             task_ref: str | None = None, plan_ref: str | None = None) -> dict[str, Any]:
    review = run.review or {"findings": [], "verdict": "", "by": ""}
    grounding = review.get("grounding") or {}
    reverts = review.get("reverts") or []
    # A step shows the revert that last took it back, while it still stands taken back.
    taken: dict[int, dict[str, Any]] = {}
    for revert in reverts:
        for n in revert.get("steps") or []:
            taken[n] = {"to": revert.get("to"), "by": revert.get("by"), "at": revert.get("at")}
    return {
        "id": run.id, "ref": run.ref, "projectId": run.project_id, "projectName": project_name,
        "taskRef": task_ref, "planRef": plan_ref, "requirement": run.requirement,
        "status": run.status, "branch": run.branch, "worktree": run.worktree, "repo": run.repo,
        "prefix": run.prefix, "base": run.base, "shortBase": run.base[:7],
        "startedAt": when(run.created_at), "finishedAt": when(run.finished_at),
        "requestedBy": run.requested_by, "targets": run.targets or [],
        "steps": [run_step_json(s, grounding=grounding.get(str(s.n)),
                                taken_back=taken.get(s.n) if s.status == "skipped" else None) for s in run.steps],
        # The totals are null when the runner's output was in no shape the parser knows.
        "tests": {"command": run.tests_command or None, "argv": None, "status": run.tests_status,
                  "summary": run.tests_summary, "passed": run.tests_passed, "failed": run.tests_failed,
                  "skipped": run.tests_skipped, "total": run.tests_total, "sha": run.tests_sha,
                  "runner": run.tests_runner},
        # `receipt`: the fingerprint of the patch the reviewer read; merge and push refuse another one.
        "review": {k: v for k, v in review.items() if k not in _BESIDE_REVIEW},
        "checks": review.get("checks") or [],
        "goal": review.get("goal"),
        "attempt": run.attempt, "goalBudget": run.goal_budget,
        "diff": {"files": run.diff_files, "insertions": run.diff_insertions,
                 "deletions": run.diff_deletions, "commits": run.diff_commits},
        "model": run.model, "lane": run.lane, "note": run.note, "removed": run.removed,
        "role": run.role, "agent": run.agent, "group": task_ref or plan_ref or run.ref,
        "parent": run.parent_id, "children": [c.ref for c in children],
        "conflicts": [{"branch": c.branch, "agent": c.agent, "files": c.files or []}
                      for c in run.conflicts],
        "merged": run.merged,
        # The branch as last pushed, and — once one has been opened from here — the pull or merge request
        # it has on the forge under `pullRequest`: its number, URL, state, whether it went up as a draft
        # and why. A project with several sources carries one per source under `pushed.sources` too.
        "pushed": run.pushed,
        # What a person allowed beyond the tool rules, the reverts made, and the sources only read.
        "grants": [{k: g.get(k) for k in ("tool", "subject", "scope", "step", "by", "at", "used")}
                   for g in run.grants or []],
        "reverts": [{k: r.get(k) for k in ("to", "by", "at", "steps", "redo")} for r in reverts],
        # Each time this run was carried on after being interrupted: where from, and what was kept.
        "resumes": [{k: r.get(k) for k in ("from", "by", "at", "adopted", "kept")}
                    for r in review.get("resumes") or []],
        "references": [x.get("label") for x in review.get("references") or []],
        "sources": [x.get("label") or "" for x in review.get("sources") or []],
        **({"waitingOn": run.waiting_on} if run.waiting_on else {}),
    }


def chat_message_json(message: ChatMessage) -> dict[str, Any]:
    """One turn. The fields a tool call carries are only there when it *is* a tool call."""
    out: dict[str, Any] = {"id": message.id, "at": when(message.at), "role": message.role,
                           "text": message.body}
    for key, value in (("by", message.by), ("model", message.model), ("lane", message.lane),
                       ("ms", message.ms), ("tool", message.tool), ("why", message.why),
                       ("detail", message.detail)):
        if value:
            out[key] = value
    if message.tool == "permission":
        # A permission card: what waits, what the rules said, what "for this session" would cover, and —
        # once a person answered — who, and how. `arguments` stays the call the model asked for.
        asked = message.arguments or {}
        out["arguments"] = asked.get("input") or {}
        out["permission"] = {k: asked.get(k) for k in ("tool", "subject", "why", "ruleId", "covers", "state",
                                                        "scope", "decidedBy", "decidedAt")}
        out["ok"] = message.ok
    elif message.tool:
        out["arguments"] = message.arguments or {}
        out["ok"] = message.ok
    if message.attachments:
        out["attachments"] = message.attachments
    # An edited or regenerated question says which turn it replaced, and a replaced turn which replaced it:
    # the screen builds the "1/2 · 2/2" switch from these two.
    if message.superseded_by:
        out["supersededBy"] = message.superseded_by
    if message.role == "you":
        asked = message.arguments or {}
        for key in ("edited", "regenerated", "lane"):
            if asked.get(key):
                out[key] = asked[key]
    # What the model reasoned before this turn, when the lane returned it, and for how long — shown
    # folded. A summary says which turns it folded; a folded turn says so, and is still here to read.
    if message.reasoning:
        out["reasoning"] = message.reasoning
    thought = (message.arguments or {}).get("thought") if message.role == "assistant" else None
    if thought:
        out["thought"] = {"ms": thought.get("ms"), "tokens": thought.get("tokens")}
    if message.role == "summary":
        folded = message.arguments or {}
        out["folded"] = {"turns": folded.get("folded", 0), "from": folded.get("from"), "to": folded.get("to")}
    if message.compacted:
        out["compacted"] = True
    return out


def chat_json(chat: Chat, *, project_name: str = "",
              instructions: Sequence[dict[str, Any]] | None = None,
              waiting: ChatMessage | None = None) -> dict[str, Any]:
    """`instructions` is the project's instruction files the session's model is handed, `[{path, bytes}]`
    (`Resolved.brief()`), read by the caller. Left out when the caller did not read them, so a list that
    was never looked at is never mistaken for a project that has none. `waiting` is the permission card
    the session waits on, when the caller looked and it waits on one."""
    asked = (waiting.arguments or {}) if waiting is not None else {}
    return {
        "id": chat.id, "ref": chat.ref, "projectId": chat.project_id, "projectName": project_name,
        "title": chat.title, "status": chat.status, "startedAt": when(chat.created_at),
        "lastAt": when(chat.last_at), "startedBy": chat.started_by, "turns": chat.turns,
        "toolCalls": chat.tool_calls, "model": chat.model, "lane": chat.lane, "note": chat.note,
        # The context meter: the prompt tokens the provider counted on the last call, against the window
        # of the model that answered it (null when its provider publishes none that this catalogue cites).
        "contextTokens": chat.context_tokens, "contextWindow": lanes.window_for(chat.lane, chat.model),
        "autoCompactAt": AUTO_COMPACT_AT,
        # Forked from another session: the parent's id and the turn it was forked at.
        "parentId": chat.parent_id, "forkedAt": chat.forked_at,
        # The agent it is asked through ("Ask <agent>"): `custom:<id>`, `file:<name>` or a roster agent's id.
        "agent": chat.agent,
        # What "Allow for this session" has allowed here, and who allowed it.
        "grants": [{"tool": g.get("tool"), "subject": g.get("subject"), "covers": g.get("covers"),
                    "by": g.get("by"), "at": g.get("at")} for g in (chat.grants or []) if isinstance(g, dict)],
        "waitingOn": ({"messageId": waiting.id, "tool": asked.get("tool"), "subject": asked.get("subject")}
                      if waiting is not None else None),
        **({"instructions": [{"path": f["path"], "bytes": f["bytes"]} for f in instructions]}
           if instructions is not None else {}),
    }
