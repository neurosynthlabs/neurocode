"""Administration: people, roles, teams, the permission catalogue, the workspace and the audit log.

Same paths and same JSON as before. What moved is where the rules live — the last active Owner and
who may grant the Owner role are the identity service's, built-in roles defend themselves in the
admin service — so this file does three things and no more: it says who is asking, it writes down
what they did, and it hands back the document the screen reads.

Writing it down is not decoration. An admin screen with no record of what it changed is worth very
little afterwards, so every write here leaves a line in the audit log, with the actor, the target and
the fields that were actually sent.
"""
from __future__ import annotations

import logging

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.identity import Role
from ..repositories.identity import (
    AuditRepository,
    RoleRepository,
    TeamRepository,
    UserRepository,
    WorkspaceRepository,
)
from ..schemas.identity import audit_json, role_json, team_json, user_json, workspace_json
from ..services.admin import RoleService, TeamService, catalogue, sorted_permissions
from ..services.errors import Refused
from ..services.identity import IdentityService, Person
from .deps import current_person, identity_service, require, require_any, session

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


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _sent(body: BaseModel) -> dict[str, Any]:
    """What a PATCH actually asked to change. The log records the request, not the whole record —
    "name, roles" is what someone reading it later needs to know."""
    return {field: value for field, value in body.model_dump().items() if value is not None}


# ── people ───────────────────────────────────────────────────────
async def _people(open_session: AsyncSession, *, limit: int | None = None,
                  offset: int = 0) -> list[dict[str, Any]]:
    users = UserRepository(open_session)
    if limit is None and offset == 0:
        # The screen asks for the directory, not for a page of it — and Teams resolves every member
        # id against this list, so anyone missing from it renders as a raw id with no name.
        found, whole = await users.everything(users.directory)
        if not whole:
            log.warning("the people directory was cut at %d; the admin screens will be incomplete",
                        len(found))
    else:
        found = (await users.directory(limit=limit, offset=offset)).items
    listed = [u.id for u in found]
    roles, teams = await users.roles_by_user(listed), await TeamRepository(open_session).teams_by_user(listed)
    return [user_json(u, roles=roles.get(u.id, []), teams=teams.get(u.id, [])) for u in found]


async def _person(open_session: AsyncSession, user_id: str) -> dict[str, Any]:
    """One person in the shape the list gives — what every write about somebody answers with."""
    users = UserRepository(open_session)
    return user_json(await users.require(user_id), roles=await users.role_ids(user_id),
                     teams=await TeamRepository(open_session).teams_of(user_id))


@router.get("/users", dependencies=[Depends(require_any("users:manage", "teams:manage"))])
async def users(limit: int | None = None, offset: int = 0,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    return await _people(open_session, limit=limit, offset=offset)


@router.post("/users", status_code=201)
async def create_user(body: UserIn, request: Request, who: Person = Depends(require("users:manage")),
                      identity: IdentityService = Depends(identity_service),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """An admin can add people, but not admins above themselves: the Owner role is an Owner's to give."""
    if "owner" in body.roles and "owner" not in who.roles:
        raise Refused("Only an Owner can grant the Owner role.", status=403)
    person = await identity.create(body.email, body.name, body.password, body.roles)
    await AuditRepository(open_session).record(action="user.create", user_id=who.id,
                                               target=person.email, detail={"roles": list(person.roles)},
                                               ip=_ip(request))
    return await _person(open_session, person.id)


@router.patch("/users/{uid}")
async def update_user(uid: str, body: UserPatch, request: Request,
                      who: Person = Depends(require("users:manage")),
                      identity: IdentityService = Depends(identity_service),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    person = await identity.update(uid, actor=who, name=body.name, status=body.status, roles=body.roles)
    await AuditRepository(open_session).record(action="user.update", user_id=who.id, target=person.email,
                                               detail=_sent(body), ip=_ip(request))
    return await _person(open_session, uid)


@router.post("/users/{uid}/password")
async def reset_password(uid: str, body: PasswordReset, request: Request,
                         who: Person = Depends(require("users:manage")),
                         identity: IdentityService = Depends(identity_service),
                         open_session: AsyncSession = Depends(session)) -> dict[str, bool]:
    """Setting someone's password signs them out everywhere — the point is usually that they are
    locked out or that the old one is no longer theirs alone."""
    person = await identity.need(uid)
    await identity.set_password(uid, body.password)
    await AuditRepository(open_session).record(action="user.password_reset", user_id=who.id,
                                               target=person.email, ip=_ip(request))
    return {"ok": True}


# ── roles and the permissions they are built from ────────────────
async def _role(open_session: AsyncSession, role: Role) -> dict[str, Any]:
    return role_json(role, permissions=await sorted_permissions(p.permission for p in role.permissions),
                     members=await RoleRepository(open_session).worn_by(role.id))


@router.get("/permissions", dependencies=[Depends(require_any("roles:manage", "users:manage"))])
async def permissions() -> list[dict[str, Any]]:
    """The whole catalogue, each entry carrying the group the access screen files it under."""
    return await catalogue()


@router.get("/roles", dependencies=[Depends(require_any("roles:manage", "users:manage"))])
async def roles(limit: int | None = None, offset: int = 0,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    repo = RoleRepository(open_session)
    found = ((await repo.everything(repo.builtin_first))[0] if limit is None and offset == 0
             else (await repo.builtin_first(limit=limit, offset=offset)).items)
    worn = await repo.members_by_role()
    return [role_json(role, permissions=await sorted_permissions(p.permission for p in role.permissions),
                      members=worn.get(role.id, 0)) for role in found]


@router.post("/roles", status_code=201)
async def create_role(body: RoleIn, request: Request, who: Person = Depends(require("roles:manage")),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    made = await RoleService(open_session).create(body.name, body.description, body.permissions)
    doc = await _role(open_session, made)
    await AuditRepository(open_session).record(action="role.create", user_id=who.id, target=made.name,
                                               detail={"permissions": doc["permissions"]},
                                               ip=_ip(request))
    return doc


@router.patch("/roles/{rid}")
async def update_role(rid: str, body: RolePatch, request: Request,
                      who: Person = Depends(require("roles:manage")),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    changed = await RoleService(open_session).update(rid, name=body.name, description=body.description,
                                                     permissions=body.permissions)
    await AuditRepository(open_session).record(action="role.update", user_id=who.id, target=changed.name,
                                               detail=_sent(body), ip=_ip(request))
    return await _role(open_session, changed)


@router.delete("/roles/{rid}")
async def delete_role(rid: str, request: Request, who: Person = Depends(require("roles:manage")),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The screen replaces the deleted role with what came back, so the document is built while the
    role is still there to describe."""
    service = RoleService(open_session)
    role = await service.need(rid)
    doc, name = await _role(open_session, role), role.name
    await service.remove(role)
    await AuditRepository(open_session).record(action="role.delete", user_id=who.id, target=name,
                                               ip=_ip(request))
    return doc


# ── teams ────────────────────────────────────────────────────────
@router.get("/teams", dependencies=[Depends(require_any("teams:manage", "users:manage"))])
async def teams(limit: int | None = None, offset: int = 0,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    repo = TeamRepository(open_session)
    found = ((await repo.everything(repo.all_ordered))[0] if limit is None and offset == 0
             else (await repo.all_ordered(limit=limit, offset=offset)).items)
    return [team_json(team) for team in found]


@router.post("/teams", status_code=201)
async def create_team(body: TeamIn, request: Request, who: Person = Depends(require("teams:manage")),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    made = await TeamService(open_session).create(body.name, body.description, body.members)
    await AuditRepository(open_session).record(action="team.create", user_id=who.id, target=made.name,
                                               detail={"members": len(made.members)}, ip=_ip(request))
    return team_json(made)


@router.patch("/teams/{tid}")
async def update_team(tid: str, body: TeamPatch, request: Request,
                      who: Person = Depends(require("teams:manage")),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    changed = await TeamService(open_session).update(tid, name=body.name, description=body.description,
                                                     members=body.members)
    await AuditRepository(open_session).record(action="team.update", user_id=who.id, target=changed.name,
                                               detail=_sent(body), ip=_ip(request))
    return team_json(changed)


@router.delete("/teams/{tid}")
async def delete_team(tid: str, request: Request, who: Person = Depends(require("teams:manage")),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    service = TeamService(open_session)
    team = await service.need(tid)
    doc = team_json(team)
    await service.remove(team)
    await AuditRepository(open_session).record(action="team.delete", user_id=who.id, target=doc["name"],
                                               ip=_ip(request))
    return doc


# ── the workspace itself ─────────────────────────────────────────
async def _workspace(open_session: AsyncSession) -> dict[str, Any]:
    """The three totals are asked of the database every time. They used to be columns, and a column
    that holds a count is a column that is wrong the first time someone is removed."""
    return workspace_json(await WorkspaceRepository(open_session).current(),
                          people=await UserRepository(open_session).count(),
                          roles=await RoleRepository(open_session).count(),
                          teams=await TeamRepository(open_session).count())


@router.get("/workspace", dependencies=[Depends(current_person)])
async def workspace(open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await _workspace(open_session)


@router.patch("/workspace")
async def update_workspace(body: WorkspacePatch, request: Request,
                           who: Person = Depends(require("workspace:admin")),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    await WorkspaceRepository(open_session).name_it(body.name)
    await AuditRepository(open_session).record(action="workspace.update", user_id=who.id,
                                               target=body.name.strip(), ip=_ip(request))
    return await _workspace(open_session)


# ── the audit log ────────────────────────────────────────────────
@router.get("/audit", dependencies=[Depends(require("audit:read"))])
async def audit(limit: int = 100, before: int | None = None,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """Newest first. `before` is the last `seq` the screen has, so entries written while someone is
    reading push nothing off the page they are about to ask for."""
    entries = await AuditRepository(open_session).newest(before=before, limit=limit)
    return [audit_json(entry, user=name) for entry, name in entries]
