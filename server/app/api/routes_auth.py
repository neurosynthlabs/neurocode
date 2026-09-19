"""Signing in: first run, login, logout, who am I, changing your own password — and the catalogue.

Same paths, same JSON, same cookie as before — the frontend cannot tell the difference. What changed
is underneath: every rule now lives in the identity service, and this file only carries the request
in and the answer out.
"""
from __future__ import annotations

import hmac

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import AuditRepository, RoleRepository, WorkspaceRepository
from ..repositories.usage import AgentRepository
from ..services.admin import catalogue as permission_catalogue
from ..services.admin import sorted_permissions
from ..services.errors import Refused
from ..services.identity import IdentityService, Person
from ..settings import settings
from .deps import COOKIE, current_person, identity_service, person_or_none, session, token_from

router = APIRouter(prefix="/auth")


class SetupIn(BaseModel):
    workspace: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)
    #: Only when the server was started with NEUROCODE_SETUP_TOKEN; ignored otherwise.
    setupToken: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


def _cookie(response: Response, token: str) -> None:
    """HttpOnly, so no script can read it; SameSite=Lax, so another site cannot spend it."""
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=settings().cookie_secure,
                        max_age=settings().session_days * 86_400, path="/")


async def _me(open_session: AsyncSession, who: Person) -> dict[str, Any]:
    name = await WorkspaceRepository(open_session).name()
    return {"user": who.public(), "workspace": {"name": name} if name else None}


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/status")
async def status(who: Person | None = Depends(person_or_none),
                 identity: IdentityService = Depends(identity_service),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Public: does this workspace need its first Owner (and a setup token to make one), and is the caller
    signed in?"""
    name = await WorkspaceRepository(open_session).name()
    needs = await identity.count() == 0
    return {"needsSetup": needs, "setupNeedsToken": needs and bool(settings().setup_token),
            "user": who.public() if who else None, "workspace": {"name": name} if name else None}


@router.post("/setup", status_code=201)
async def setup(body: SetupIn, request: Request, response: Response,
                identity: IdentityService = Depends(identity_service),
                open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """First run only: name the workspace and create its first Owner, signed straight in."""
    if await identity.count() > 0:
        raise Refused("This workspace is already set up. Sign in instead.")
    expected = settings().setup_token
    if expected and not hmac.compare_digest(body.setupToken.strip().encode(), expected.encode()):
        raise Refused("That setup token is not this server's. It is NEUROCODE_SETUP_TOKEN in the server's .env, "
                      "printed when the server was first deployed.", status=403)
    identity.check(body.email, body.name, body.password)
    await WorkspaceRepository(open_session).name_it(body.workspace)
    owner = await identity.create(body.email, body.name, body.password, ["owner"])
    _cookie(response, await identity.start_session(owner.id, request.headers.get("user-agent", "")))
    await AuditRepository(open_session).record(action="workspace.setup", user_id=owner.id,
                                               target=body.workspace.strip(), ip=_ip(request))
    return await _me(open_session, owner)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response,
                identity: IdentityService = Depends(identity_service),
                open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    audit = AuditRepository(open_session)
    try:
        who, token = await identity.login(body.email, body.password,
                                          user_agent=request.headers.get("user-agent", ""), ip=_ip(request))
    except Refused as refused:
        # A refused sign-in is worth recording — that is how a locked-out account is explained later.
        await audit.record(action="auth.login_failed", user_id=None, target=body.email.strip()[:200],
                           detail={"reason": str(refused)}, ip=_ip(request))
        raise
    _cookie(response, token)
    await audit.record(action="auth.login", user_id=who.id, target=who.email, ip=_ip(request))
    return await _me(open_session, who)


@router.post("/logout")
async def logout(request: Request, response: Response,
                 identity: IdentityService = Depends(identity_service)) -> dict[str, bool]:
    token = token_from(request)
    if token:
        await identity.logout(token)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(who: Person = Depends(current_person),
             open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await _me(open_session, who)


@router.get("/catalogue", dependencies=[Depends(current_person)])
async def catalogue(open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The names every screen needs to put words to an id: what each permission is called, what each
    role is called and grants, and who each agent is.

    Any signed-in person may read it, because anyone may be shown a role's name or be told which
    permission a button needs — which is why it is here and not behind the admin routes, where the
    same permissions are served with the roles screen. The roles are the workspace's, custom ones
    included; the agents are the roster, identity only: what they have done is on `GET /agents`.
    """
    roles = RoleRepository(open_session)
    found, _ = await roles.everything(roles.builtin_first)
    agents = await AgentRepository(open_session).all_ordered()
    return {
        "permissions": permission_catalogue(),
        "roles": [{"id": r.id, "name": r.name, "description": r.description, "builtin": r.builtin,
                   "permissions": sorted_permissions(p.permission for p in r.permissions)} for r in found],
        "agents": [{"id": a.id, "name": a.name, "role": a.role, "icon": a.icon} for a in agents],
    }


@router.post("/password")
async def change_password(body: PasswordIn, request: Request, who: Person = Depends(current_person),
                          identity: IdentityService = Depends(identity_service),
                          open_session: AsyncSession = Depends(session)) -> dict[str, bool]:
    """Changing it ends every other session that person has open; this one is kept."""
    if not await identity.verify(who.id, body.current):
        raise Refused("The current password is wrong.", status=403)
    await identity.set_password(who.id, body.new, keep=token_from(request))
    await AuditRepository(open_session).record(action="auth.password", user_id=who.id, target=who.email,
                                               ip=_ip(request))
    return {"ok": True}
