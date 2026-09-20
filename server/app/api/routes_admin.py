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

from ..ai.gateway import Gateway
from ..models.identity import Role
from ..repositories.identity import (
    AuditRepository,
    RoleRepository,
    TeamRepository,
    UserRepository,
    WorkspaceRepository,
)
from ..schemas.identity import audit_json, role_json, team_json, user_json, workspace_json
from ..secrets import Secrets
from ..services import sandbox
from ..services.admin import RoleService, TeamService, catalogue, sorted_permissions
from ..services.errors import Refused
from ..services.identity import SSO_SECRET, MIN_PASSWORD, IdentityService, Person, SsoService
from ..settings import settings
from .deps import current_person, gateway, identity_service, require, require_any, session

log = logging.getLogger(__name__)
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


class SandboxPatch(BaseModel):
    """What the workspace says about the fence around the runtime's commands. `enabled` can only ever
    narrow what the server was started with — see `sandbox.read_policy`."""

    enabled: bool = True
    network: bool = False


class SsoPatch(BaseModel):
    """Everything on the single sign-on panel. A field left out is left alone, which is what lets the
    secret be set without re-sending the issuer, and the issuer changed without clearing the secret."""

    enabled: bool | None = None
    issuer: str | None = Field(default=None, max_length=300)
    clientId: str | None = Field(default=None, max_length=300)
    #: Sent only when it is being changed. An empty string clears it.
    clientSecret: str | None = Field(default=None, max_length=500)
    label: str | None = Field(default=None, max_length=60)
    roleClaim: str | None = Field(default=None, max_length=80)
    roleMap: dict[str, str] | None = Field(default=None, max_length=50)
    defaultRoles: list[str] | None = Field(default=None, max_length=10)
    createUsers: bool | None = None
    requireSso: bool | None = None
    redirectUri: str | None = Field(default=None, max_length=500)


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


@router.get("/users", dependencies=[Depends(require_any("people:read", "users:manage", "teams:manage"))])
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
    return role_json(role, permissions=sorted_permissions(p.permission for p in role.permissions),
                     members=await RoleRepository(open_session).worn_by(role.id))


@router.get("/permissions", dependencies=[Depends(require_any("roles:manage", "users:manage"))])
async def permissions() -> list[dict[str, Any]]:
    """The whole catalogue, each entry carrying the module, sub-module and verb the Roles matrix
    files it under. Reading which rights a role carries is a different question from reading who is
    here, so this one stays on the pair that can change them."""
    return catalogue()


@router.get("/roles", dependencies=[Depends(require_any("roles:manage", "users:manage"))])
async def roles(limit: int | None = None, offset: int = 0,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    repo = RoleRepository(open_session)
    found = ((await repo.everything(repo.builtin_first))[0] if limit is None and offset == 0
             else (await repo.builtin_first(limit=limit, offset=offset)).items)
    worn = await repo.members_by_role()
    return [role_json(role, permissions=sorted_permissions(p.permission for p in role.permissions),
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
@router.get("/teams", dependencies=[Depends(require_any("people:read", "teams:manage", "users:manage"))])
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
async def _workspace(open_session: AsyncSession, identity: IdentityService) -> dict[str, Any]:
    """The totals are asked of the database every time. They used to be columns, and a column that
    holds a count is a column that is wrong the first time someone is removed.

    The security rules are read from the identity service that enforces them — its settings and its
    password floor — rather than restated, so a NEUROCODE_SESSION_DAYS set where the API runs shows
    here exactly as it applies at sign-in."""
    roles = RoleRepository(open_session)
    rules = identity.config
    return workspace_json(await WorkspaceRepository(open_session).current(),
                          people=await UserRepository(open_session).count(),
                          roles=await roles.count(), builtin_roles=await roles.count(Role.builtin.is_(True)),
                          teams=await TeamRepository(open_session).count(),
                          security={"sessionDays": rules.session_days, "minPassword": MIN_PASSWORD,
                                    "loginAttempts": rules.login_attempts,
                                    "lockoutSeconds": rules.lockout_seconds})


@router.get("/workspace", dependencies=[Depends(current_person)])
async def workspace(open_session: AsyncSession = Depends(session),
                    identity: IdentityService = Depends(identity_service)) -> dict[str, Any]:
    return await _workspace(open_session, identity)


@router.patch("/workspace")
async def update_workspace(body: WorkspacePatch, request: Request,
                           who: Person = Depends(require("workspace:admin")),
                           identity: IdentityService = Depends(identity_service),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    await WorkspaceRepository(open_session).name_it(body.name)
    await AuditRepository(open_session).record(action="workspace.update", user_id=who.id,
                                               target=body.name.strip(), ip=_ip(request))
    return await _workspace(open_session, identity)


# ── the audit log ────────────────────────────────────────────────
@router.get("/audit", dependencies=[Depends(require("audit:read"))])
async def audit(limit: int = 100, before: int | None = None,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """Newest first. `before` is the last `seq` the screen has, so entries written while someone is
    reading push nothing off the page they are about to ask for."""
    entries = await AuditRepository(open_session).newest(before=before, limit=limit)
    return [audit_json(entry, user=name) for entry, name in entries]


# ── the sandbox around the runtime's commands ────────────────────
async def _sandbox_doc(open_session: AsyncSession) -> dict[str, Any]:
    """What is actually in force, measured on this machine rather than described from the settings.

    `detected` is what the machine has whatever anyone asked for — so a workspace that has turned
    sandboxing off still reads what it is turning off, and one on a machine with nothing reads why.
    """
    policy = await sandbox.read_policy(open_session)
    kind, why = sandbox.detect()
    return {"enabled": policy.enabled, "network": policy.network,
            "serverAllows": settings().sandbox,
            "detected": {"kind": kind, "name": sandbox.NAMES.get(kind, kind), "why": why},
            "inForce": sandbox.preview(policy).json()}


@router.get("/sandbox", dependencies=[Depends(require("workspace:admin"))])
async def sandbox_settings(open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await _sandbox_doc(open_session)


@router.put("/sandbox")
async def update_sandbox(body: SandboxPatch, request: Request,
                         who: Person = Depends(require("workspace:admin")),
                         open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Turning the network on is the one that matters, and it is why this screen exists: a test suite
    that installs its packages needs it, and nobody should discover that it was on by reading code."""
    await sandbox.write_policy(open_session, enabled=body.enabled, network=body.network)
    await AuditRepository(open_session).record(action="sandbox.update", user_id=who.id,
                                               target="runtime", detail=_sent(body), ip=_ip(request))
    return await _sandbox_doc(open_session)


# ── single sign-on ───────────────────────────────────────────────
async def _sso_doc(open_session: AsyncSession, gw: Gateway) -> dict[str, Any]:
    """The panel's document. The client secret is reported as set-or-not and its last four characters,
    exactly as a model key is — a screen that could read it back is a screen that leaks it."""
    config = await SsoService(open_session, gw.secrets).settings()
    held = gw.secrets.get(SSO_SECRET)
    return {**config.stored(), "hasSecret": bool(held), "secretMask": Secrets.mask(held),
            "ready": config.ready,
            # The address to register at the provider, for an admin who has not chosen one yet.
            "callbackPath": "/api/auth/sso/callback"}


@router.get("/sso", dependencies=[Depends(require("workspace:admin"))])
async def sso_settings(open_session: AsyncSession = Depends(session),
                       gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _sso_doc(open_session, gw)


@router.put("/sso")
async def update_sso(body: SsoPatch, request: Request, who: Person = Depends(require("workspace:admin")),
                     open_session: AsyncSession = Depends(session),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """The settings are checked and stored first, the secret after — and that order is the whole of
    the care taken here. The secrets file is the one thing outside this request's transaction, so a
    save refused for naming an unknown role rolls the settings back but could not roll a file back;
    writing the secret last means a refusal leaves the file exactly as it was."""
    service = SsoService(open_session, gw.secrets)
    patch = {k: v for k, v in body.model_dump().items() if k != "clientSecret" and v is not None}
    if patch:
        await service.save(patch, actor=who, ip=_ip(request))
    if body.clientSecret is not None:
        await service.set_secret(body.clientSecret, actor=who, ip=_ip(request))
    return await _sso_doc(open_session, gw)
