"""Memory: facts with FTS5 search, pins and archives, conflicts between facts, and facts added from text."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import User, current_user, require
from ..context import Ctx, ctx, home, need

router = APIRouter()
Category = Literal["human", "project", "architecture", "business_rules", "legacy", "database", "bugs", "decisions",
                   "incidents", "preferences", "code"]


class PinIn(BaseModel):
    pinned: bool


class ResolveIn(BaseModel):
    keep: Literal["a", "b", "adr"]


class FactIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    category: Category = "project"
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    reason: str = Field(default="", max_length=500)


class FactsIn(BaseModel):
    projectId: str = Field(default="global", max_length=60)
    facts: list[FactIn] = Field(min_length=1, max_length=20)


@router.get("/memory", dependencies=[Depends(current_user)])
async def memory(q: str = "", category: str | None = None, project: str | None = None,
                 include_archived: bool = False, c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.memory(q, category, project, include_archived)


@router.post("/memory/facts", status_code=201)
async def add_facts(body: FactsIn, user: User = Depends(require("memory:write")), c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    if body.projectId != "global" and not c.store.get("projects", body.projectId):
        raise HTTPException(404, f"project {body.projectId} not found")
    added = []
    for f in body.facts:
        m = c.store.next_memory_number()
        fact = {"id": f"m{m}", "ref": f"MEM-{m}", "category": f.category, "title": f.title.strip(), "body": f.body.strip(),
                "reason": f.reason.strip() or f"Added from text by {user.name}.", "source": f"Added from text by {user.name}",
                "projectId": body.projectId, "confidence": f.confidence, "strength": 80, "hits": 0,
                "createdAt": "just now", "lastUsed": "never", "evidence": [], "tags": [f.category.replace("_", "-")], "pinned": False}
        added.append(c.put("memory", c.store.insert_memory(fact)))
    first = added[0]["title"]
    c.act(user, "Memory added", f"{len(added)} fact{'s' if len(added) > 1 else ''} · {first[:80]}",
          project=home(added[0]), level="ok")
    return added


@router.post("/memory/{ref}/pin")
async def pin(ref: str, body: PinIn, user: User = Depends(require("memory:write")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    f = need(c.store.one("memory", ref), ref)
    f["pinned"] = body.pinned
    c.put("memory", c.store.save_memory(f))
    c.act(user, "Memory pinned" if body.pinned else "Memory unpinned", f"{ref} · {f['title']}", project=home(f))
    return f


@router.post("/memory/{ref}/archive")
async def archive(ref: str, user: User = Depends(require("memory:write")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    f = need(c.store.one("memory", ref), ref)
    c.store.save_memory(f, archived=True)
    c.drop("memory", f["id"])
    c.act(user, "Memory archived", f"{ref} · {f['title']} — recoverable, never deleted", project=home(f), level="warn")
    return f


@router.get("/memory/conflicts", dependencies=[Depends(current_user)])
async def conflicts(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.docs("SELECT doc FROM conflicts WHERE status = 'open' ORDER BY rowid")


@router.post("/memory/conflicts/{cid}/resolve")
async def resolve(cid: str, body: ResolveIn, user: User = Depends(require("memory:write")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    conflict = need(c.store.get("conflicts", cid), f"conflict {cid}")
    if conflict.get("status", "open") != "open":
        raise HTTPException(409, f"'{conflict['topic']}' was already resolved")
    if body.keep == "adr":
        c.act(user, "Conflict escalated", f"{conflict['topic']} · written up as an ADR. Both facts stay until it is decided.",
              project=home(c.store.get("memory", conflict["a"])), level="warn")
    else:
        keep_id, lose_id = (conflict["a"], conflict["b"]) if body.keep == "a" else (conflict["b"], conflict["a"])
        winner, loser = c.store.get("memory", keep_id), c.store.get("memory", lose_id)
        if loser:
            c.store.save_memory(loser, archived=True)
            c.drop("memory", loser["id"])
        c.act(user, "Conflict resolved", f"{conflict['topic']} · kept {winner['ref'] if winner else keep_id}, "
              f"archived {loser['ref'] if loser else lose_id} as superseded", project=home(winner), level="ok")
    conflict.update(status="resolved", resolution=body.keep, resolvedBy=user.name)
    c.store.save("conflicts", conflict, status="resolved")
    c.drop("conflicts", cid)
    return conflict
