"""NeuroCode local API.

Serves the domain the frontend ships as mock data, but persisted: approvals really get approved, plans
really get compiled, repositories really get measured, and memory is searchable with FTS5. Every
change is pushed to open tabs over Server-Sent Events as two kinds of event: `activity` (the log line)
and `change` (the document that changed, so every tab stays in step).

Run from the repo root (it binds to localhost only; nothing here leaves the machine):
    uv run --project server uvicorn app.main:create_app --factory --app-dir server --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from . import compiler, onboarding
from .db import TABLES, Store

SERVER_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = SERVER_DIR / "neurocode.db"
ENV_FILE = SERVER_DIR / ".env"
TaskStatus = Literal["backlog", "planning", "in_progress", "review", "blocked", "done"]


class StatusIn(BaseModel):
    status: TaskStatus


class DoneIn(BaseModel):
    done: bool


class PinIn(BaseModel):
    pinned: bool


class RuleIn(BaseModel):
    id: str = Field(max_length=40)
    label: str = Field(max_length=120)
    note: str = Field(default="", max_length=300)


class ProjectIn(BaseModel):
    source: Literal["git", "local"]
    repo: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", max_length=100)
    excluded: list[str] = Field(default_factory=list, max_length=50)
    connectDb: bool = True
    mineGit: bool = True
    ingestDocs: bool = True
    rules: list[RuleIn] = Field(default_factory=list, max_length=20)


class McpIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    transport: Literal["stdio", "http", "sse"]
    command: str = Field(min_length=1, max_length=1000)
    scope: Literal["project", "global", "local"]
    defaultEffect: Literal["ask", "allow-read", "deny"]
    config: str = Field(max_length=4000)

    @field_validator("config")
    @classmethod
    def _is_json(cls, v: str) -> str:
        json.loads(v)  # a ValueError here becomes a 422
        return v


class ResolveIn(BaseModel):
    keep: Literal["a", "b", "adr"]


class CompileIn(BaseModel):
    requirement: str = Field(min_length=3, max_length=4000)
    projectId: str


class AnswerIn(BaseModel):
    answer: str | None = Field(default=None, max_length=1000)
    defer: bool = False


class Bus:
    """In-process fan-out of everything the API records. One operator, one process: no broker needed.
    Safe to publish from worker threads (onboarding runs in one): each queue is fed on its own loop."""

    def __init__(self) -> None:
        self.queues: dict[asyncio.Queue[tuple[str, Any]], asyncio.AbstractEventLoop] = {}
        self.listeners: list[Callable[[str, Any], None]] = []

    def subscribe(self) -> asyncio.Queue[tuple[str, Any]]:
        q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=500)
        self.queues[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue[tuple[str, Any]]) -> None:
        self.queues.pop(q, None)

    def listen(self, fn: Callable[[str, Any], None]) -> None:
        self.listeners.append(fn)

    def publish(self, kind: str, data: Any) -> None:
        for fn in list(self.listeners):
            fn(kind, data)
        for q, loop in list(self.queues.items()):
            try:
                loop.call_soon_threadsafe(self._offer, q, (kind, data))
            except RuntimeError:  # the loop behind that tab is gone
                self.queues.pop(q, None)

    @staticmethod
    def _offer(q: asyncio.Queue[tuple[str, Any]], item: tuple[str, Any]) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:  # a tab that stopped reading loses events; it never blocks the API
            pass


def load_env(path: Path) -> None:
    """KEY=VALUE lines from server/.env. The real environment always wins. No dependency needed."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def in_flight(plan: dict[str, Any]) -> bool:
    return plan.get("status") == "dispatched" or any(s["state"] != "todo" for s in plan["steps"])


def home(fact: dict[str, Any] | None) -> str:
    """Global facts belong to no project, so their events are filed under NeuroCode itself."""
    return "aios" if not fact or fact["projectId"] == "global" else fact["projectId"]


def create_app(db_path: str | None = None, env_file: Path | None = ENV_FILE) -> FastAPI:
    if env_file is not None:
        load_env(env_file)
    store = Store(db_path or os.environ.get("NEUROCODE_DB", str(DEFAULT_DB)))
    bus = Bus()
    app = FastAPI(title="NeuroCode API", version="0.2.0")
    app.state.store = store
    app.state.bus = bus
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def put(collection: str, doc: dict[str, Any]) -> dict[str, Any]:
        bus.publish("change", {"op": "put", "collection": collection, "doc": copy.deepcopy(doc)})
        return doc

    def drop(collection: str, id: str) -> None:
        bus.publish("change", {"op": "drop", "collection": collection, "id": id})

    def record(action: str, detail: str, *, project: str, level: str = "info", task_ref: str | None = None,
               actor: str = "You", kind: str = "human") -> dict[str, Any]:
        doc: dict[str, Any] = {"t": datetime.now().strftime("%H:%M:%S"), "actor": actor, "actorKind": kind,
                               "action": action, "detail": detail, "projectId": project, "level": level}
        if task_ref:
            doc["taskRef"] = task_ref
        doc = store.add_event(doc)
        bus.publish("activity", doc)
        return doc

    def need(doc: dict[str, Any] | None, what: str) -> dict[str, Any]:
        if doc is None:
            raise HTTPException(404, f"{what} not found")
        return doc

    # ── meta ─────────────────────────────────────────────────────
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "db": store.path, "counts": {t: store.count(t) for t in TABLES},
                "compiler": await asyncio.to_thread(compiler.status)}

    @app.post("/admin/reset")
    async def reset(x_confirm: str | None = Header(default=None)) -> dict[str, Any]:
        if x_confirm != "reset":
            raise HTTPException(400, "send header X-Confirm: reset — this restores the seed data and drops every change")
        store.seed()
        return await health()

    @app.get("/agents")
    async def agents() -> list[dict[str, Any]]:
        return store.all("agents")

    # ── projects and onboarding ──────────────────────────────────
    @app.get("/projects")
    async def projects() -> list[dict[str, Any]]:
        return store.all("projects")

    def run_onboarding(project_id: str, spec: ProjectIn) -> None:
        """Runs after the response, on a worker thread. Every stage lands in the activity log."""
        doc = store.get("projects", project_id)
        if doc is None:
            return
        name, where = doc["name"], onboarding.redact(spec.repo)
        say = lambda action, detail, level="ok": record(  # noqa: E731
            action, f"{name} · {detail}", project=project_id, level=level, actor="Architect", kind="agent")
        try:
            if spec.source == "git":
                root = onboarding.REPOS_DIR / project_id
                say("Cloning", f"{where} @ {spec.branch}", "info")
                onboarding.clone(spec.repo, spec.branch, root)
                say("Repository cloned", f"shallow clone of {spec.branch}")
            else:
                root = Path(os.path.expanduser(spec.repo.strip()))
            found = onboarding.scan(root, spec.excluded)
        except Exception as e:  # a refused clone, a vanished folder, a full disk: the operator needs the reason
            reason = onboarding.redact(str(e)) or type(e).__name__
            doc.update(status="paused", lastActive="just now", description=f"Onboarding stopped: {reason}")
            put("projects", store.save("projects", doc))
            say("Onboarding failed", reason, "err")
            return
        steps = onboarding.step_count(spec.connectDb, spec.mineGit, spec.ingestDocs)
        doc.update(onboarding.measured(found, steps))
        put("projects", store.save("projects", doc))
        langs = " · ".join(f"{lang['name']} {lang['pct']}%" for lang in found["languages"][:4]) or "no source files recognised"
        say("Stack detected", langs)
        say("Tree mapped", f"{found['files']:,} files · {onboarding.fmt_lines(found['lines'])} lines · {found['modules']} modules")
        if found["dbTables"] or found["storedProcs"]:
            say("SQL objects found", f"{found['dbTables']} tables · {found['storedProcs']} procedures", "info")
        say("Onboarding paused", f"step 3 of {steps}: the syntax-tree parser is not connected yet", "warn")

    @app.post("/projects", status_code=201)
    async def create_project(body: ProjectIn, jobs: BackgroundTasks) -> dict[str, Any]:
        if problem := onboarding.problem(body.source, body.repo, body.branch):
            raise HTTPException(422, problem)
        pid = store.unique_id("projects", onboarding.slug(body.repo))
        where = onboarding.redact(body.repo.strip())
        doc = {
            "id": pid, "name": onboarding.title(pid), "codename": pid.upper(), "stack": [], "kind": "greenfield",
            "status": "onboarding", "memoryPct": 0, "understoodPct": 0, "lines": "—", "modules": 0, "dbTables": 0,
            "storedProcs": 0, "repo": where, "lastActive": "just now", "coverage": [],
            "work": {"tasks": 0, "running": 0, "review": 0, "blocked": 0},
            "description": f"Onboarding from {where}.",
            "source": {"kind": body.source, "repo": where, **({"branch": body.branch.strip()} if body.source == "git" else {})},
            "rules": [r.model_dump() for r in body.rules],
        }
        put("projects", store.insert("projects", doc))
        record("Onboarding started", f"{doc['name']} · {where}", project=pid)
        jobs.add_task(run_onboarding, pid, body)
        return doc

    # ── tasks ────────────────────────────────────────────────────
    @app.get("/tasks")
    async def tasks(project: str | None = None) -> list[dict[str, Any]]:
        if project:
            return store.docs("SELECT doc FROM tasks WHERE project_id = ? ORDER BY rowid", (project,))
        return store.all("tasks")

    @app.get("/tasks/{ref}")
    async def task(ref: str) -> dict[str, Any]:
        return need(store.one("tasks", ref), ref)

    @app.patch("/tasks/{ref}")
    async def move_task(ref: str, body: StatusIn) -> dict[str, Any]:
        t = need(store.one("tasks", ref), ref)
        before, t["status"], t["updatedAt"] = t["status"], body.status, "just now"
        put("tasks", store.save_task(t))
        record("Task moved", f"{ref} · {before.replace('_', ' ')} → {body.status.replace('_', ' ')}",
               project=t["projectId"], task_ref=ref)
        return t

    @app.post("/tasks/{ref}/checklist/{item_id}")
    async def check_item(ref: str, item_id: str, body: DoneIn) -> dict[str, Any]:
        t = need(store.one("tasks", ref), ref)
        item = need(next((c for c in t["checklist"] if c["id"] == item_id), None), f"checklist item {item_id}")
        item["done"], t["updatedAt"] = body.done, "just now"
        put("tasks", store.save_task(t))
        record("Checklist updated", f"{ref} · {'✓' if body.done else '○'} {item['label']}",
               project=t["projectId"], level="ok" if body.done else "info", task_ref=ref)
        return t

    # ── approvals: the human gate ────────────────────────────────
    @app.get("/approvals")
    async def approvals(status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return store.docs("SELECT doc FROM approvals WHERE status = ? ORDER BY rowid", (status,))
        return store.all("approvals")

    @app.post("/approvals/{ref}/{decision}")
    async def decide(ref: str, decision: Literal["approve", "deny"]) -> dict[str, Any]:
        a = need(store.one("approvals", ref), ref)
        if a["status"] != "pending":
            raise HTTPException(409, f"{ref} was already {a['status']} — a decision is final")
        a["status"] = "approved" if decision == "approve" else "denied"
        a["decidedAt"] = datetime.now().isoformat(timespec="seconds")
        put("approvals", store.save_approval(a))
        record("Approved" if decision == "approve" else "Denied", f"{ref} · {a['title']}",
               project=a["projectId"], level="ok" if decision == "approve" else "warn")
        return a

    # ── memory ───────────────────────────────────────────────────
    @app.get("/memory")
    async def memory(q: str = "", category: str | None = None, project: str | None = None,
                     include_archived: bool = False) -> list[dict[str, Any]]:
        return store.memory(q, category, project, include_archived)

    @app.post("/memory/{ref}/pin")
    async def pin(ref: str, body: PinIn) -> dict[str, Any]:
        f = need(store.one("memory", ref), ref)
        f["pinned"] = body.pinned
        put("memory", store.save_memory(f))
        record("Memory pinned" if body.pinned else "Memory unpinned", f"{ref} · {f['title']}", project=home(f))
        return f

    @app.post("/memory/{ref}/archive")
    async def archive(ref: str) -> dict[str, Any]:
        f = need(store.one("memory", ref), ref)
        store.save_memory(f, archived=True)
        drop("memory", f["id"])
        record("Memory archived", f"{ref} · {f['title']} — recoverable, never deleted", project=home(f), level="warn")
        return f

    @app.get("/memory/conflicts")
    async def conflicts() -> list[dict[str, Any]]:
        return store.docs("SELECT doc FROM conflicts WHERE status = 'open' ORDER BY rowid")

    @app.post("/memory/conflicts/{cid}/resolve")
    async def resolve(cid: str, body: ResolveIn) -> dict[str, Any]:
        c = need(store.get("conflicts", cid), f"conflict {cid}")
        if c.get("status", "open") != "open":
            raise HTTPException(409, f"'{c['topic']}' was already resolved")
        if body.keep == "adr":
            record("Conflict escalated", f"{c['topic']} · written up as an ADR. Both facts stay until it is decided.",
                   project=home(store.get("memory", c["a"])), level="warn")
        else:
            keep_id, lose_id = (c["a"], c["b"]) if body.keep == "a" else (c["b"], c["a"])
            winner, loser = store.get("memory", keep_id), store.get("memory", lose_id)
            if loser:
                store.save_memory(loser, archived=True)
                drop("memory", loser["id"])
            record("Conflict resolved", f"{c['topic']} · kept {winner['ref'] if winner else keep_id}, "
                   f"archived {loser['ref'] if loser else lose_id} as superseded", project=home(winner), level="ok")
        c.update(status="resolved", resolution=body.keep)
        store.save("conflicts", c, status="resolved")
        drop("conflicts", cid)
        return c

    # ── plans: the requirement compiler ──────────────────────────
    @app.get("/plans")
    async def plans() -> list[dict[str, Any]]:
        return store.all("plans")

    async def run_compiler(project: dict[str, Any], requirement: str, answers: list[dict[str, str]]) -> compiler.Result:
        facts = store.memory(requirement, project=project["id"], mode="any")[:6]
        ctx = compiler.Context(project=project, facts=facts, answers=answers)
        return await asyncio.to_thread(compiler.compile_plan, requirement, ctx)

    def compiled(res: compiler.Result, n: int) -> dict[str, Any]:
        out = res.plan
        return {
            "businessRequirement": out.businessRequirement, "technicalRequirement": out.technicalRequirement,
            "affectedModules": out.affectedModules, "affectedFiles": out.affectedFiles, "affectedDb": out.affectedDb,
            "architectureImpact": out.architectureImpact, "risk": out.risk, "confidence": out.confidence,
            "steps": [{"id": f"s{n}-{i}", "n": i, "label": s.label, "agent": s.agent, "state": "todo", "detail": s.detail}
                      for i, s in enumerate(out.steps, 1)],
            "testPlan": out.testPlan, "cited": res.cited,
            "compiler": {"provider": res.provider.id, "model": res.provider.model, "ms": res.ms},
            "createdAt": datetime.now().strftime("today %H:%M"),
        }

    def task_shape(out: compiler.PlanOut, steps: list[dict[str, Any]], n: int) -> dict[str, Any]:
        return {"title": out.title, "priority": out.priority, "risk": out.risk, "layers": out.layers or ["Backend"],
                "agents": list(dict.fromkeys(compiler.AGENTS[s["agent"]] for s in steps if s["agent"] != "AI Commander")),
                "files": len(out.affectedFiles),
                "checklist": [{"id": f"c{n}-{s['n']}", "label": s["label"], "done": False} for s in steps]}

    @app.post("/plans/compile", status_code=201)
    async def compile_requirement(body: CompileIn) -> dict[str, Any]:
        project = need(store.get("projects", body.projectId), f"project {body.projectId}")
        requirement = body.requirement.strip()
        res = await run_compiler(project, requirement, [])
        n = store.next_number()
        fields = compiled(res, n)
        task = {"id": f"t{n}", "ref": f"TASK-{n}", "projectId": project["id"], "status": "planning", "tests": 0,
                "progress": 0, "createdAt": "just now", "updatedAt": "just now", "requirement": requirement,
                **task_shape(res.plan, fields["steps"], n)}
        plan = {"id": f"p{n}", "ref": f"PLAN-{n}", "taskRef": task["ref"], "projectId": project["id"],
                "rawRequirement": requirement, "status": "draft", "openQuestions": res.plan.openQuestions, **fields}
        put("tasks", store.insert_task(task))
        put("plans", store.insert_plan(plan))
        if res.fallback:
            record("Compiler fell back", f"{res.fallback}. The offline planner stood in.", project=project["id"],
                   level="warn", actor="AI Commander", kind="agent")
        record("Requirement compiled",
               f"{plan['ref']} · {len(fields['steps'])} steps · {len(plan['openQuestions'])} open questions · "
               f"{res.provider.model}, {res.ms / 1000:.1f}s",
               project=project["id"], level="ok", task_ref=task["ref"], actor="AI Commander", kind="agent")
        return plan

    @app.post("/plans/{ref}/recompile")
    async def recompile(ref: str) -> dict[str, Any]:
        plan = need(store.one("plans", ref), ref)
        if in_flight(plan):
            raise HTTPException(409, f"{ref} is already under way. Compile a new requirement instead.")
        project = need(store.get("projects", plan["projectId"]), f"project {plan['projectId']}")
        res = await run_compiler(project, plan["rawRequirement"], plan.get("answered", []))
        settled = {a["q"] for a in plan.get("answered", [])} | set(plan.get("deferred", []))
        n = int(re.search(r"\d+$", ref).group())  # type: ignore[union-attr]
        plan.update(compiled(res, n), openQuestions=[q for q in res.plan.openQuestions if q not in settled])
        put("plans", store.save("plans", plan))
        task = store.one("tasks", plan["taskRef"])
        if task and task["status"] in ("backlog", "planning"):
            task.update(task_shape(res.plan, plan["steps"], n), updatedAt="just now")
            put("tasks", store.save_task(task))
        record("Plan re-compiled", f"{ref} · {len(plan['steps'])} steps · {len(plan['openQuestions'])} open questions · "
               f"{res.provider.model}", project=plan["projectId"], level="ok", task_ref=plan["taskRef"],
               actor="AI Commander", kind="agent")
        return plan

    @app.post("/plans/{ref}/questions/{index}")
    async def settle_question(ref: str, index: int, body: AnswerIn) -> dict[str, Any]:
        plan = need(store.one("plans", ref), ref)
        questions = plan["openQuestions"]
        if not 0 <= index < len(questions):
            raise HTTPException(404, f"{ref} has no open question #{index}")
        answer = (body.answer or "").strip()
        if not body.defer and not answer:
            raise HTTPException(422, "send an answer, or defer the question")
        q = questions.pop(index)
        if body.defer:
            plan.setdefault("deferred", []).append(q)
            record("Question deferred", f"{ref} · {q}", project=plan["projectId"], level="warn")
        else:
            plan.setdefault("answered", []).append({"q": q, "a": answer})
            m = store.next_memory_number()
            fact = {"id": f"m{m}", "ref": f"MEM-{m}", "category": "business_rules", "title": q, "body": answer,
                    "reason": f"You answered it while reviewing {ref}.", "source": f"{ref} · open question",
                    "projectId": plan["projectId"], "confidence": "HIGH", "strength": 100, "hits": 0,
                    "createdAt": "just now", "lastUsed": "just now", "evidence": [ref], "tags": ["answer", ref],
                    "pinned": False}
            put("memory", store.insert_memory(fact))
            record("Business rule recorded", f"{fact['ref']} · {q}", project=plan["projectId"], level="ok")
        put("plans", store.save("plans", plan))
        return plan

    @app.post("/plans/{ref}/dispatch")
    async def dispatch(ref: str) -> dict[str, Any]:
        plan = need(store.one("plans", ref), ref)
        if in_flight(plan):
            raise HTTPException(409, f"{ref} is already under way")
        if n := len(plan["openQuestions"]):
            raise HTTPException(409, f"{ref} still has {n} open question{'s' if n > 1 else ''}. "
                                     f"Answer or defer {'them' if n > 1 else 'it'} first; the plan does not guess.")
        plan["status"] = "dispatched"
        plan["steps"][0]["state"] = "active"
        put("plans", store.save("plans", plan))
        task = store.one("tasks", plan["taskRef"])
        if task and task["status"] in ("backlog", "planning"):
            task.update(status="in_progress", updatedAt="just now")
            put("tasks", store.save_task(task))
        first = plan["steps"][0]
        record("Plan dispatched", f"{ref} → {plan['taskRef']} · {first['agent']} starts: {first['label']}",
               project=plan["projectId"], level="ok", task_ref=plan["taskRef"])
        return plan

    # ── MCP servers ──────────────────────────────────────────────
    @app.get("/mcp/servers")
    async def mcp_servers() -> list[dict[str, Any]]:
        return store.all("mcp")

    @app.post("/mcp/servers", status_code=201)
    async def register_mcp(body: McpIn) -> dict[str, Any]:
        sid = store.unique_id("mcp", body.name)
        doc = {"id": sid, "name": sid, "transport": body.transport, "status": "disconnected", "scope": body.scope,
               "command": body.command.strip(), "tools": [], "resources": 0, "prompts": 0, "latencyMs": 0,
               "calls24h": 0, "errorRate": 0, "untrusted": True, "defaultEffect": body.defaultEffect,
               "config": body.config}
        put("mcp", store.insert("mcp", doc))
        record("MCP server registered", f"{sid} · {body.transport} · {body.scope} scope · tools default to {body.defaultEffect}",
               project="aios")
        return doc

    # ── activity ─────────────────────────────────────────────────
    @app.get("/activity")
    async def activity(limit: int = 200) -> list[dict[str, Any]]:
        return store.activity(max(1, min(limit, 1000)))

    @app.get("/activity/stream")
    async def stream() -> StreamingResponse:
        q = bus.subscribe()

        async def events():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
