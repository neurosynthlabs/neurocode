"""Administration: people, roles, teams, the audit log, the workspace, AI providers and resetting data.

Every change here is written to the audit log with who made it and from where. Model API keys are
stored in the secrets file and only ever reported masked.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets as pysecrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..ai import lanes
from ..ai.gateway import PREFERENCES
from ..auth import User, current_user, require, require_any
from ..context import Ctx, ctx
from ..db import TABLES, now_iso
from ..secrets import Secrets

router = APIRouter(prefix="/admin")


class UserIn(BaseModel):
    email: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(max_length=200)
    roles: list[str] = Field(default_factory=lambda: ["viewer"], max_length=10)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    status: Literal["active", "disabled"] | None = None
    roles: list[str] | None = Field(default=None, max_length=10)


class PasswordReset(BaseModel):
    password: str = Field(max_length=200)


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    permissions: list[str] = Field(default_factory=list, max_length=50)


class RolePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    permissions: list[str] | None = Field(default=None, max_length=50)


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    members: list[str] = Field(default_factory=list, max_length=200)


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    members: list[str] | None = Field(default=None, max_length=200)


class WorkspacePatch(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class AiPatch(BaseModel):
    preference: str | None = None
    deepseekKey: str | None = Field(default=None, max_length=300)   # an empty string removes the key
    deepseekModel: str | None = Field(default=None, max_length=80)
    deepseekUrl: str | None = Field(default=None, max_length=300)
    ollamaUrl: str | None = Field(default=None, max_length=300)
    ollamaModel: str | None = Field(default=None, max_length=80)
    # Any lane, by id: what the rest of these fields are about.
    lane: str | None = Field(default=None, max_length=40)
    key: str | None = Field(default=None, max_length=300)           # an empty string removes it
    model: str | None = Field(default=None, max_length=120)
    baseUrl: str | None = Field(default=None, max_length=300)
    rpm: int | None = Field(default=None, ge=0, le=10_000)
    rpd: int | None = Field(default=None, ge=0, le=1_000_000)
    enabled: bool | None = None


class AiTest(BaseModel):
    provider: str = Field(max_length=40)     # a lane id, or "rules"


# ── people ───────────────────────────────────────────────────────
@router.get("/users", dependencies=[Depends(require_any("users:manage", "teams:manage"))])
def users(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.accounts.list()


@router.post("/users", status_code=201)
def create_user(body: UserIn, request: Request, actor: User = Depends(require("users:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    if "owner" in body.roles and "owner" not in actor.roles:
        raise HTTPException(403, "Only an Owner can grant the Owner role")
    user = c.accounts.create(body.email, body.name, body.password, body.roles)
    c.audit("user.create", user=actor, target=user.email, detail={"roles": list(user.roles)}, request=request)
    return next(u for u in c.accounts.list() if u["id"] == user.id)


@router.patch("/users/{uid}")
def update_user(uid: str, body: UserPatch, request: Request, actor: User = Depends(require("users:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    user = c.accounts.update(uid, actor=actor, name=body.name, status=body.status, roles=body.roles)
    changed = {k: v for k, v in body.model_dump().items() if v is not None}
    c.audit("user.update", user=actor, target=user.email, detail=changed, request=request)
    return next(u for u in c.accounts.list() if u["id"] == uid)


@router.post("/users/{uid}/password")
def reset_password(uid: str, body: PasswordReset, request: Request, actor: User = Depends(require("users:manage")),
                   c: Ctx = Depends(ctx)) -> dict[str, bool]:
    user = c.accounts.need(uid)
    c.accounts.set_password(uid, body.password)
    c.audit("user.password_reset", user=actor, target=user.email, request=request)
    return {"ok": True}


# ── roles ────────────────────────────────────────────────────────
@router.get("/permissions", dependencies=[Depends(require_any("roles:manage", "users:manage"))])
def permissions(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.rbac.catalogue


@router.get("/roles", dependencies=[Depends(require_any("roles:manage", "users:manage"))])
def roles(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.rbac.roles()


@router.post("/roles", status_code=201)
def create_role(body: RoleIn, request: Request, actor: User = Depends(require("roles:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    role = c.rbac.create(body.name, body.description, body.permissions)
    c.audit("role.create", user=actor, target=role["name"], detail={"permissions": role["permissions"]}, request=request)
    return role


@router.patch("/roles/{rid}")
def update_role(rid: str, body: RolePatch, request: Request, actor: User = Depends(require("roles:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    role = c.rbac.update(rid, name=body.name, description=body.description, permissions=body.permissions)
    c.audit("role.update", user=actor, target=role["name"], detail={k: v for k, v in body.model_dump().items() if v is not None}, request=request)
    return role


@router.delete("/roles/{rid}")
def delete_role(rid: str, request: Request, actor: User = Depends(require("roles:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    role = c.rbac.delete(rid)
    c.audit("role.delete", user=actor, target=role["name"], request=request)
    return role


# ── teams ────────────────────────────────────────────────────────
def _teams(c: Ctx) -> list[dict[str, Any]]:
    members: dict[str, list[str]] = {}
    for r in c.store.rows("SELECT team_id, user_id FROM team_members"):
        members.setdefault(r["team_id"], []).append(r["user_id"])
    return [{"id": r["id"], "name": r["name"], "description": r["description"], "members": members.get(r["id"], []),
             "createdAt": r["created_at"]} for r in c.store.rows("SELECT * FROM teams ORDER BY name")]


def _team(c: Ctx, tid: str) -> dict[str, Any]:
    team = next((t for t in _teams(c) if t["id"] == tid), None)
    if team is None:
        raise HTTPException(404, "team not found")
    return team


def _check_members(c: Ctx, members: list[str]) -> list[str]:
    known = {r[0] for r in c.store.rows("SELECT id FROM users")}
    unknown = [m for m in members if m not in known]
    if unknown:
        raise HTTPException(422, f"Unknown people: {', '.join(unknown)}")
    return list(dict.fromkeys(members))


@router.get("/teams", dependencies=[Depends(require_any("teams:manage", "users:manage"))])
def teams(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return _teams(c)


@router.post("/teams", status_code=201)
def create_team(body: TeamIn, request: Request, actor: User = Depends(require("teams:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    if c.store.row("SELECT 1 FROM teams WHERE name = ?", (body.name.strip(),)):
        raise HTTPException(409, "A team with that name already exists")
    members = _check_members(c, body.members)
    tid = "t_" + pysecrets.token_hex(5)
    with c.store.tx() as t:
        t.execute("INSERT INTO teams VALUES (?, ?, ?, ?)", (tid, body.name.strip(), body.description.strip(), now_iso()))
        t.executemany("INSERT INTO team_members VALUES (?, ?)", [(tid, m) for m in members])
    c.audit("team.create", user=actor, target=body.name.strip(), detail={"members": len(members)}, request=request)
    return _team(c, tid)


@router.patch("/teams/{tid}")
def update_team(tid: str, body: TeamPatch, request: Request, actor: User = Depends(require("teams:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    team = _team(c, tid)
    members = _check_members(c, body.members) if body.members is not None else None
    with c.store.tx() as t:
        t.execute("UPDATE teams SET name = ?, description = ? WHERE id = ?",
                  ((body.name or team["name"]).strip(), (body.description if body.description is not None else team["description"]).strip(), tid))
        if members is not None:
            t.execute("DELETE FROM team_members WHERE team_id = ?", (tid,))
            t.executemany("INSERT INTO team_members VALUES (?, ?)", [(tid, m) for m in members])
    c.audit("team.update", user=actor, target=team["name"], detail={k: v for k, v in body.model_dump().items() if v is not None}, request=request)
    return _team(c, tid)


@router.delete("/teams/{tid}")
def delete_team(tid: str, request: Request, actor: User = Depends(require("teams:manage")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    team = _team(c, tid)
    c.store.execute("DELETE FROM teams WHERE id = ?", (tid,))
    c.audit("team.delete", user=actor, target=team["name"], request=request)
    return team


# ── audit ────────────────────────────────────────────────────────
@router.get("/audit", dependencies=[Depends(require("audit:read"))])
def audit(limit: int = 100, before: int | None = None, c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    rows = c.store.rows("SELECT a.*, u.name AS user_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
                        "WHERE (? IS NULL OR a.seq < ?) ORDER BY a.seq DESC LIMIT ?", (before, before, max(1, min(limit, 500))))
    return [{"seq": r["seq"], "at": r["at"], "user": r["user_name"], "userId": r["user_id"], "action": r["action"],
             "target": r["target"], "detail": json.loads(r["detail"] or "{}"), "ip": r["ip"]} for r in rows]


# ── workspace ────────────────────────────────────────────────────
def _workspace(c: Ctx) -> dict[str, Any]:
    ws = c.store.row("SELECT name, created_at FROM workspace WHERE id = 1")
    return {"name": ws["name"] if ws else "NeuroCode", "createdAt": ws["created_at"] if ws else None,
            "people": c.accounts.count(), "roles": len(c.rbac.roles()), "teams": c.store.row("SELECT COUNT(*) FROM teams")[0]}


@router.get("/workspace", dependencies=[Depends(current_user)])
def workspace(c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return _workspace(c)


@router.patch("/workspace")
def update_workspace(body: WorkspacePatch, request: Request, actor: User = Depends(require("workspace:admin")),
                     c: Ctx = Depends(ctx)) -> dict[str, Any]:
    c.store.execute("UPDATE workspace SET name = ? WHERE id = 1", (body.name.strip(),))
    c.audit("workspace.update", user=actor, target=body.name.strip(), request=request)
    return _workspace(c)


# ── AI providers ─────────────────────────────────────────────────
def _lane_patch(c: Ctx, body: AiPatch) -> dict[str, Any]:
    """Change one lane: its model, its base URL, the limits it holds itself to, its key, or whether it
    is used at all. The key goes to the secrets file and is never returned, only reported as set."""
    lane = lanes.BY_ID.get(body.lane or "")
    if lane is None:
        raise HTTPException(404, f"there is no lane called {body.lane}")
    changed: dict[str, Any] = {}
    saved = dict(c.store.setting(f"ai.lane.{lane.id}", {}) or {})
    for field, text in (("model", body.model), ("baseUrl", body.baseUrl)):
        if text is not None:
            saved[field] = text.strip()
            changed[f"{lane.id}.{field}"] = text.strip()
    for field, number in (("rpm", body.rpm), ("rpd", body.rpd)):
        if number is not None:
            saved[field] = number
            changed[f"{lane.id}.{field}"] = number
    if body.enabled is not None:
        saved["enabled"] = body.enabled
        changed[f"{lane.id}.enabled"] = body.enabled
    if saved:
        c.store.set_setting(f"ai.lane.{lane.id}", saved)
    if body.key is not None:
        if not lane.needs_key:
            raise HTTPException(400, f"{lane.label} needs no key.")
        c.secrets.set(lane.secret, body.key.strip() or None)
        c.gateway.forget_rejection(lane.id)
        changed[f"{lane.id}.key"] = "set" if body.key.strip() else "removed"   # never the key itself
    return changed


def _ai(c: Ctx) -> dict[str, Any]:
    g = c.gateway
    ds, ol = g.deepseek(), g.ollama()
    return {
        "preference": g.preference(),
        "preferenceLocked": bool(os.environ.get("NEUROCODE_COMPILER")),
        "active": g.status(),
        "deepseek": {"hasKey": bool(ds["key"]), "keyMask": Secrets.mask(ds["key"]), "keySource": g.key_source(),
                     "model": ds["model"], "baseUrl": ds["baseUrl"], "rejected": g.rejected()},
        "ollama": {"url": ol["url"], "model": ol["model"], "ready": g.ollama_ready()},
        "lanes": g.report(),
    }


@router.get("/ai", dependencies=[Depends(require("workspace:admin"))])
async def ai(c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return await asyncio.to_thread(_ai, c)


@router.put("/ai")
async def update_ai(body: AiPatch, request: Request, actor: User = Depends(require("workspace:admin")),
                    c: Ctx = Depends(ctx)) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    if body.preference is not None:
        if body.preference not in PREFERENCES:
            raise HTTPException(400, f"routing must be one of: {', '.join(PREFERENCES)}")
        c.store.set_setting("ai.preference", body.preference)
        changed["preference"] = body.preference
    if body.lane is not None:
        changed.update(_lane_patch(c, body))
    ds = dict(c.store.setting("ai.deepseek", {}) or {})
    for field, key in (("deepseekModel", "model"), ("deepseekUrl", "baseUrl")):
        if (value := getattr(body, field)) is not None:
            ds[key] = value.strip()
            changed[field] = value.strip()
    c.store.set_setting("ai.deepseek", ds)
    ol = dict(c.store.setting("ai.ollama", {}) or {})
    for field, key in (("ollamaModel", "model"), ("ollamaUrl", "url")):
        if (value := getattr(body, field)) is not None:
            ol[key] = value.strip()
            changed[field] = value.strip()
    c.store.set_setting("ai.ollama", ol)
    if body.deepseekKey is not None:
        c.secrets.set("deepseek_api_key", body.deepseekKey.strip() or None)
        c.gateway.forget_rejection()
        changed["deepseekKey"] = "set" if body.deepseekKey.strip() else "removed"  # never the key itself
    c.audit("ai.update", user=actor, target="AI providers", detail=changed, request=request)
    return await asyncio.to_thread(_ai, c)


@router.post("/ai/test")
async def test_ai(body: AiTest, actor: User = Depends(require("workspace:admin")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return await asyncio.to_thread(c.gateway.test, body.provider, actor.id)


# ── resetting data ───────────────────────────────────────────────
@router.post("/reset")
async def reset(request: Request, x_confirm: str | None = Header(default=None), actor: User = Depends(require("workspace:admin")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """Puts the sample work back. People, roles, teams, keys and the audit log are kept."""
    if x_confirm != "reset":
        raise HTTPException(400, "send header X-Confirm: reset — this restores the seed data and drops every change")
    # a reset is undoable: the database is copied aside first
    saved = await asyncio.to_thread(c.store.backup, "before reset") if c.store.backup_dir else None
    c.store.seed()
    c.audit("data.reset", user=actor, target="workspace data", detail={"backup": saved["name"] if saved else None}, request=request)
    return {"ok": True, "backup": saved["name"] if saved else None, "counts": {t: c.store.count(t) for t in TABLES},
            "compiler": await asyncio.to_thread(c.gateway.status)}


# ── the database ─────────────────────────────────────────────────
@router.get("/database", dependencies=[Depends(require("workspace:admin"))])
async def database(c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return await asyncio.to_thread(c.store.stats)


@router.post("/database/backup", status_code=201)
async def backup_database(request: Request, actor: User = Depends(require("workspace:admin")),
                          c: Ctx = Depends(ctx)) -> dict[str, Any]:
    try:
        made = await asyncio.to_thread(c.store.backup, "manual")
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    c.audit("database.backup", user=actor, target=made["name"], detail={"bytes": made["bytes"]}, request=request)
    return made


@router.post("/database/check", dependencies=[Depends(require("workspace:admin"))])
async def check_database(c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return await asyncio.to_thread(c.store.check)


@router.post("/database/optimize")
async def optimize_database(request: Request, actor: User = Depends(require("workspace:admin")),
                            c: Ctx = Depends(ctx)) -> dict[str, Any]:
    done = await asyncio.to_thread(c.store.optimize)
    c.audit("database.optimize", user=actor, target="database",
            detail={"beforeBytes": done["beforeBytes"], "afterBytes": done["afterBytes"]}, request=request)
    return done
