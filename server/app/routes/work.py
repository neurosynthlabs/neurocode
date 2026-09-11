"""The work: tasks, approvals (the human gate), plans (the requirement compiler), final decisions and
screen settings. Reading needs a session; each change needs its permission."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field, field_validator

from ..ai.compiler import AGENTS, Context, PlanOut, compile_plan
from ..ai.gateway import Result
from ..auth import User, current_user, require
from ..context import Ctx, ctx, in_flight, need

router = APIRouter()
TaskStatus = Literal["backlog", "planning", "in_progress", "review", "blocked", "done"]
KEY = r"^[A-Za-z][\w.:-]{1,80}$"


class StatusIn(BaseModel):
    status: TaskStatus


class DoneIn(BaseModel):
    done: bool


class CompileIn(BaseModel):
    requirement: str = Field(min_length=3, max_length=4000)
    projectId: str


class AnswerIn(BaseModel):
    answer: str | None = Field(default=None, max_length=1000)
    defer: bool = False


class DecisionIn(BaseModel):
    value: str = Field(min_length=1, max_length=40)
    action: str = Field(min_length=1, max_length=80)
    detail: str = Field(default="", max_length=300)
    projectId: str = Field(default="aios", max_length=60)
    level: Literal["info", "ok", "warn", "err"] = "ok"


class PrefIn(BaseModel):
    value: Any
    detail: str = Field(default="", max_length=300)
    projectId: str = Field(default="aios", max_length=60)

    @field_validator("value")
    @classmethod
    def _small(cls, v: Any) -> Any:
        if len(json.dumps(v)) > 20_000:
            raise ValueError("a setting is limited to 20 KB")
        return v


# ── tasks ────────────────────────────────────────────────────────
@router.get("/tasks", dependencies=[Depends(current_user)])
async def tasks(project: str | None = None, c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    if project:
        return c.store.docs("SELECT doc FROM tasks WHERE project_id = ? ORDER BY rowid", (project,))
    return c.store.all("tasks")


@router.get("/tasks/{ref}", dependencies=[Depends(current_user)])
async def task(ref: str, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return need(c.store.one("tasks", ref), ref)


@router.patch("/tasks/{ref}")
async def move_task(ref: str, body: StatusIn, user: User = Depends(require("tasks:write")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    t = need(c.store.one("tasks", ref), ref)
    before, t["status"], t["updatedAt"] = t["status"], body.status, "just now"
    c.put("tasks", c.store.save_task(t))
    c.act(user, "Task moved", f"{ref} · {before.replace('_', ' ')} → {body.status.replace('_', ' ')}", project=t["projectId"], task_ref=ref)
    return t


@router.post("/tasks/{ref}/checklist/{item_id}")
async def check_item(ref: str, item_id: str, body: DoneIn, user: User = Depends(require("tasks:write")),
                     c: Ctx = Depends(ctx)) -> dict[str, Any]:
    t = need(c.store.one("tasks", ref), ref)
    item = need(next((x for x in t["checklist"] if x["id"] == item_id), None), f"checklist item {item_id}")
    item["done"], t["updatedAt"] = body.done, "just now"
    c.put("tasks", c.store.save_task(t))
    c.act(user, "Checklist updated", f"{ref} · {'✓' if body.done else '○'} {item['label']}", project=t["projectId"],
          level="ok" if body.done else "info", task_ref=ref)
    return t


# ── approvals: the human gate ────────────────────────────────────
@router.get("/approvals", dependencies=[Depends(current_user)])
async def approvals(status: str | None = None, c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    if status:
        return c.store.docs("SELECT doc FROM approvals WHERE status = ? ORDER BY rowid", (status,))
    return c.store.all("approvals")


@router.post("/approvals/{ref}/{decision}")
async def decide(ref: str, decision: Literal["approve", "deny"], user: User = Depends(require("approvals:decide")),
                 c: Ctx = Depends(ctx)) -> dict[str, Any]:
    a = need(c.store.one("approvals", ref), ref)
    if a["status"] != "pending":
        raise HTTPException(409, f"{ref} was already {a['status']} — a decision is final")
    a["status"] = "approved" if decision == "approve" else "denied"
    a["decidedAt"] = datetime.now().isoformat(timespec="seconds")
    a["decidedBy"] = user.name
    c.put("approvals", c.store.save_approval(a))
    c.act(user, "Approved" if decision == "approve" else "Denied", f"{ref} · {a['title']}", project=a["projectId"],
          level="ok" if decision == "approve" else "warn")
    return a


# ── plans: the requirement compiler ──────────────────────────────
def _compiled(res: Result[PlanOut], cited: list[str], n: int) -> dict[str, Any]:
    out = res.data
    return {
        "businessRequirement": out.businessRequirement, "technicalRequirement": out.technicalRequirement,
        "affectedModules": out.affectedModules, "affectedFiles": out.affectedFiles, "affectedDb": out.affectedDb,
        "architectureImpact": out.architectureImpact, "risk": out.risk, "confidence": out.confidence,
        "steps": [{"id": f"s{n}-{i}", "n": i, "label": s.label, "agent": s.agent, "state": "todo", "detail": s.detail}
                  for i, s in enumerate(out.steps, 1)],
        "testPlan": out.testPlan, "cited": cited, "compiler": res.meta(),
        "createdAt": datetime.now().strftime("today %H:%M"),
    }


def _task_shape(out: PlanOut, steps: list[dict[str, Any]], n: int) -> dict[str, Any]:
    return {"title": out.title, "priority": out.priority, "risk": out.risk, "layers": out.layers or ["Backend"],
            "agents": list(dict.fromkeys(AGENTS[s["agent"]] for s in steps if s["agent"] != "AI Commander")),
            "files": len(out.affectedFiles),
            "checklist": [{"id": f"c{n}-{s['n']}", "label": s["label"], "done": False} for s in steps]}


async def _run_compiler(c: Ctx, project: dict[str, Any], requirement: str,
                        answers: list[dict[str, str]]) -> tuple[Result[PlanOut], list[str]]:
    facts = c.store.memory(requirement, project=project["id"], mode="any")[:6]
    res, cited = await asyncio.to_thread(compile_plan, c.gateway, requirement, Context(project=project, facts=facts, answers=answers))
    if res.fallback:
        c.record("Compiler fell back", f"{res.fallback}. The offline planner stood in.", project=project["id"],
                 level="warn", actor="AI Commander", kind="agent")
    return res, cited


@router.get("/plans", dependencies=[Depends(current_user)])
async def plans(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.all("plans")


@router.post("/plans/compile", status_code=201)
async def compile_requirement(body: CompileIn, user: User = Depends(require("plans:compile")),
                              c: Ctx = Depends(ctx)) -> dict[str, Any]:
    project = need(c.store.get("projects", body.projectId), f"project {body.projectId}")
    requirement = body.requirement.strip()
    res, cited = await _run_compiler(c, project, requirement, [])
    n = c.store.next_number()
    fields = _compiled(res, cited, n)
    task = {"id": f"t{n}", "ref": f"TASK-{n}", "projectId": project["id"], "status": "planning", "tests": 0,
            "progress": 0, "createdAt": "just now", "updatedAt": "just now", "requirement": requirement,
            **_task_shape(res.data, fields["steps"], n)}
    plan = {"id": f"p{n}", "ref": f"PLAN-{n}", "taskRef": task["ref"], "projectId": project["id"],
            "rawRequirement": requirement, "status": "draft", "openQuestions": res.data.openQuestions,
            "requestedBy": user.name, **fields}
    c.put("tasks", c.store.insert_task(task))
    c.put("plans", c.store.insert_plan(plan))
    c.record("Requirement compiled",
             f"{plan['ref']} · {len(fields['steps'])} steps · {len(plan['openQuestions'])} open questions · "
             f"{res.provider.model}, {res.ms / 1000:.1f}s",
             project=project["id"], level="ok", task_ref=task["ref"], actor="AI Commander", kind="agent")
    return plan


@router.post("/plans/{ref}/recompile")
async def recompile(ref: str, user: User = Depends(require("plans:decide")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    plan = need(c.store.one("plans", ref), ref)
    if in_flight(plan):
        raise HTTPException(409, f"{ref} is already under way. Compile a new requirement instead.")
    project = need(c.store.get("projects", plan["projectId"]), f"project {plan['projectId']}")
    res, cited = await _run_compiler(c, project, plan["rawRequirement"], plan.get("answered", []))
    settled = {a["q"] for a in plan.get("answered", [])} | set(plan.get("deferred", []))
    n = int(re.search(r"\d+$", ref).group())  # type: ignore[union-attr]
    plan.update(_compiled(res, cited, n), openQuestions=[q for q in res.data.openQuestions if q not in settled])
    c.put("plans", c.store.save("plans", plan))
    task = c.store.one("tasks", plan["taskRef"])
    if task and task["status"] in ("backlog", "planning"):
        task.update(_task_shape(res.data, plan["steps"], n), updatedAt="just now")
        c.put("tasks", c.store.save_task(task))
    c.act(user, "Plan re-compiled", f"{ref} · {len(plan['steps'])} steps · {len(plan['openQuestions'])} open questions · "
          f"{res.provider.model}", project=plan["projectId"], level="ok", task_ref=plan["taskRef"])
    return plan


@router.post("/plans/{ref}/questions/{index}")
async def settle_question(ref: str, index: int, body: AnswerIn, user: User = Depends(require("plans:decide")),
                          c: Ctx = Depends(ctx)) -> dict[str, Any]:
    plan = need(c.store.one("plans", ref), ref)
    questions = plan["openQuestions"]
    if not 0 <= index < len(questions):
        raise HTTPException(404, f"{ref} has no open question #{index}")
    answer = (body.answer or "").strip()
    if not body.defer and not answer:
        raise HTTPException(422, "send an answer, or defer the question")
    q = questions.pop(index)
    if body.defer:
        plan.setdefault("deferred", []).append(q)
        c.act(user, "Question deferred", f"{ref} · {q}", project=plan["projectId"], level="warn")
    else:
        plan.setdefault("answered", []).append({"q": q, "a": answer})
        m = c.store.next_memory_number()
        fact = {"id": f"m{m}", "ref": f"MEM-{m}", "category": "business_rules", "title": q, "body": answer,
                "reason": f"{user.name} answered it while reviewing {ref}.", "source": f"{ref} · open question",
                "projectId": plan["projectId"], "confidence": "HIGH", "strength": 100, "hits": 0,
                "createdAt": "just now", "lastUsed": "just now", "evidence": [ref], "tags": ["answer", ref], "pinned": False}
        c.put("memory", c.store.insert_memory(fact))
        c.act(user, "Business rule recorded", f"{fact['ref']} · {q}", project=plan["projectId"], level="ok")
    c.put("plans", c.store.save("plans", plan))
    return plan


@router.post("/plans/{ref}/dispatch")
async def dispatch(ref: str, user: User = Depends(require("plans:decide")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    plan = need(c.store.one("plans", ref), ref)
    if in_flight(plan):
        raise HTTPException(409, f"{ref} is already under way")
    if n := len(plan["openQuestions"]):
        raise HTTPException(409, f"{ref} still has {n} open question{'s' if n > 1 else ''}. "
                                 f"Answer or defer {'them' if n > 1 else 'it'} first; the plan does not guess.")
    plan["status"] = "dispatched"
    plan["steps"][0]["state"] = "active"
    c.put("plans", c.store.save("plans", plan))
    task = c.store.one("tasks", plan["taskRef"])
    if task and task["status"] in ("backlog", "planning"):
        task.update(status="in_progress", updatedAt="just now")
        c.put("tasks", c.store.save_task(task))
    first = plan["steps"][0]
    c.act(user, "Plan dispatched", f"{ref} → {plan['taskRef']} · {first['agent']} starts: {first['label']}",
          project=plan["projectId"], level="ok", task_ref=plan["taskRef"])
    return plan


# ── final decisions and screen settings ─────────────────────────
@router.get("/decisions", dependencies=[Depends(current_user)])
async def decisions(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.all("decisions")


@router.post("/decisions/{key}", status_code=201)
async def record_decision(body: DecisionIn, key: str = PathParam(pattern=KEY), user: User = Depends(require("decisions:make")),
                          c: Ctx = Depends(ctx)) -> dict[str, Any]:
    if c.store.get("decisions", key):
        raise HTTPException(409, f"{key} was already decided — a decision is final")
    doc = {"id": key, "value": body.value, "decidedAt": datetime.now().isoformat(timespec="seconds"), "decidedBy": user.name}
    c.put("decisions", c.store.insert("decisions", doc))
    c.record(body.action, body.detail, project=body.projectId, level=body.level, actor=user.name, kind="human")
    return doc


@router.get("/prefs", dependencies=[Depends(current_user)])
async def prefs(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.all("prefs")


@router.put("/prefs/{key}")
async def set_pref(body: PrefIn, key: str = PathParam(pattern=KEY), user: User = Depends(require("settings:write")),
                   c: Ctx = Depends(ctx)) -> dict[str, Any]:
    doc = c.put("prefs", c.store.upsert("prefs", {"id": key, "value": body.value}))
    if body.detail:  # a switch or a choice is worth a log line; each keystroke in a text field is not
        c.act(user, "Setting changed", body.detail, project=body.projectId)
    return doc
