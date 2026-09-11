"""What every route shares, and the helpers that record what happened.

Two records are kept. The activity log is the product's story (who moved what, which agent did what)
and streams to every open tab. The audit log is for security: sign-ins and every change to access,
keys or settings, with the person and their address.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request

from .ai.gateway import Gateway
from .auth import Accounts, User
from .db import Store, now_iso
from .events import Bus
from .rbac import Rbac
from .secrets import Secrets


@dataclass
class Ctx:
    store: Store
    bus: Bus
    secrets: Secrets
    rbac: Rbac
    accounts: Accounts
    gateway: Gateway
    indexing: set[str] = field(default_factory=set)  # projects whose code is being indexed right now

    def put(self, collection: str, doc: dict[str, Any]) -> dict[str, Any]:
        """Tell every tab this document changed."""
        self.bus.publish("change", {"op": "put", "collection": collection, "doc": copy.deepcopy(doc)})
        return doc

    def drop(self, collection: str, id: str) -> None:
        self.bus.publish("change", {"op": "drop", "collection": collection, "id": id})

    def record(self, action: str, detail: str, *, project: str, level: str = "info", task_ref: str | None = None,
               actor: str = "System", kind: str = "system") -> dict[str, Any]:
        doc: dict[str, Any] = {"t": datetime.now().strftime("%H:%M:%S"), "actor": actor, "actorKind": kind,
                               "action": action, "detail": detail, "projectId": project, "level": level}
        if task_ref:
            doc["taskRef"] = task_ref
        doc = self.store.add_event(doc)
        self.bus.publish("activity", doc)
        return doc

    def act(self, user: User, action: str, detail: str, *, project: str, level: str = "info",
            task_ref: str | None = None) -> dict[str, Any]:
        """An activity line for something a person did."""
        return self.record(action, detail, project=project, level=level, task_ref=task_ref, actor=user.name, kind="human")

    def audit(self, action: str, *, user: User | None, target: str = "", detail: dict[str, Any] | None = None,
              request: Request | None = None) -> None:
        ip = request.client.host if request is not None and request.client else ""
        self.store.execute("INSERT INTO audit_log(at, user_id, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
                           (now_iso(), user.id if user else None, action, target, json.dumps(detail or {}, ensure_ascii=False), ip))


def ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def need(doc: dict[str, Any] | None, what: str) -> dict[str, Any]:
    if doc is None:
        raise HTTPException(404, f"{what} not found")
    return doc


def home(fact: dict[str, Any] | None) -> str:
    """Global facts belong to no project, so their events are filed under NeuroCode itself."""
    return "aios" if not fact or fact["projectId"] == "global" else fact["projectId"]


def in_flight(plan: dict[str, Any]) -> bool:
    return plan.get("status") == "dispatched" or any(s["state"] != "todo" for s in plan["steps"])
