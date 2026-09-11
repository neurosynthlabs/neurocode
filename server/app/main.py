"""NeuroCode local API.

Serves the same domain the frontend ships as mock data, but persisted: approvals really get approved,
checklists stay ticked, memory is searchable with FTS5 and can be pinned or archived, and every change
is written to the activity log and pushed to open tabs over Server-Sent Events.

Run from the repo root (binds to localhost only — this never leaves the machine):
    uv run --project server uvicorn app.main:create_app --factory --app-dir server --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .db import Store

DEFAULT_DB = Path(__file__).resolve().parent.parent / "neurocode.db"
TaskStatus = Literal["backlog", "planning", "in_progress", "review", "blocked", "done"]


class StatusIn(BaseModel):
    status: TaskStatus


class DoneIn(BaseModel):
    done: bool


class PinIn(BaseModel):
    pinned: bool


class Bus:
    """In-process fan-out for the activity stream. One operator, one process — no broker needed."""

    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # a tab that stopped reading loses events, never blocks the API
                pass


def create_app(db_path: str | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("NEUROCODE_DB", str(DEFAULT_DB)))
    bus = Bus()
    app = FastAPI(title="NeuroCode API", version="0.1.0")
    app.state.store = store
    app.state.bus = bus
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def record(action: str, detail: str, *, project: str, level: str = "info", task_ref: str | None = None,
               actor: str = "You", kind: str = "human") -> dict[str, Any]:
        doc: dict[str, Any] = {"t": datetime.now().strftime("%H:%M:%S"), "actor": actor, "actorKind": kind,
                               "action": action, "detail": detail, "projectId": project, "level": level}
        if task_ref:
            doc["taskRef"] = task_ref
        doc = store.add_event(doc)
        bus.publish(doc)
        return doc

    def need(doc: dict[str, Any] | None, what: str) -> dict[str, Any]:
        if doc is None:
            raise HTTPException(404, f"{what} not found")
        return doc

    # ── meta ─────────────────────────────────────────────────────
    @app.get("/health")
    async def health() -> dict[str, Any]:
        tables = ("projects", "agents", "tasks", "approvals", "memory", "activity")
        return {"ok": True, "db": store.path, "counts": {t: store.count(t) for t in tables}}

    @app.post("/admin/reset")
    async def reset(x_confirm: str | None = Header(default=None)) -> dict[str, Any]:
        if x_confirm != "reset":
            raise HTTPException(400, "send header X-Confirm: reset — this restores the seed data and drops every change")
        store.seed()
        return await health()

    # ── reference data ───────────────────────────────────────────
    @app.get("/projects")
    async def projects() -> list[dict[str, Any]]:
        return store.docs("SELECT doc FROM projects")

    @app.get("/agents")
    async def agents() -> list[dict[str, Any]]:
        return store.docs("SELECT doc FROM agents")

    # ── tasks ────────────────────────────────────────────────────
    @app.get("/tasks")
    async def tasks(project: str | None = None) -> list[dict[str, Any]]:
        if project:
            return store.docs("SELECT doc FROM tasks WHERE project_id = ?", (project,))
        return store.docs("SELECT doc FROM tasks")

    @app.get("/tasks/{ref}")
    async def task(ref: str) -> dict[str, Any]:
        return need(store.one("tasks", ref), ref)

    @app.patch("/tasks/{ref}")
    async def move_task(ref: str, body: StatusIn) -> dict[str, Any]:
        t = need(store.one("tasks", ref), ref)
        before, t["status"], t["updatedAt"] = t["status"], body.status, "just now"
        store.save_task(t)
        record("Task moved", f"{ref} · {before.replace('_', ' ')} → {body.status.replace('_', ' ')}",
               project=t["projectId"], task_ref=ref)
        return t

    @app.post("/tasks/{ref}/checklist/{item_id}")
    async def check_item(ref: str, item_id: str, body: DoneIn) -> dict[str, Any]:
        t = need(store.one("tasks", ref), ref)
        item = need(next((c for c in t["checklist"] if c["id"] == item_id), None), f"checklist item {item_id}")
        item["done"], t["updatedAt"] = body.done, "just now"
        store.save_task(t)
        record("Checklist updated", f"{ref} · {'✓' if body.done else '○'} {item['label']}",
               project=t["projectId"], level="ok" if body.done else "info", task_ref=ref)
        return t

    # ── approvals — the human gate ───────────────────────────────
    @app.get("/approvals")
    async def approvals(status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return store.docs("SELECT doc FROM approvals WHERE status = ?", (status,))
        return store.docs("SELECT doc FROM approvals")

    @app.post("/approvals/{ref}/{decision}")
    async def decide(ref: str, decision: Literal["approve", "deny"]) -> dict[str, Any]:
        a = need(store.one("approvals", ref), ref)
        if a["status"] != "pending":
            raise HTTPException(409, f"{ref} was already {a['status']} — a decision is final")
        a["status"] = "approved" if decision == "approve" else "denied"
        a["decidedAt"] = datetime.now().isoformat(timespec="seconds")
        store.save_approval(a)
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
        store.save_memory(f)
        record("Memory pinned" if body.pinned else "Memory unpinned", f"{ref} · {f['title']}",
               project=f["projectId"] if f["projectId"] != "global" else "aios", actor="You")
        return f

    @app.post("/memory/{ref}/archive")
    async def archive(ref: str) -> dict[str, Any]:
        f = need(store.one("memory", ref), ref)
        store.save_memory(f, archived=True)
        record("Memory archived", f"{ref} · {f['title']} — recoverable, never deleted",
               project=f["projectId"] if f["projectId"] != "global" else "aios", level="warn")
        return f

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
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: activity\ndata: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
