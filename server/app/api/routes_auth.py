"""Signing in: first run, login, logout, who am I, changing your own password — and the catalogue.

Same paths, same JSON, same cookie as before — the frontend cannot tell the difference. What changed
is underneath: every rule now lives in the identity service, and this file only carries the request
in and the answer out.

Single sign-on adds two routes and no rules. `/auth/sso/start` asks the service where to send the
browser and sets the one-time cookie carrying the state and nonce; `/auth/sso/callback` is where the
provider sends it back, and it answers a *redirect* rather than JSON because what arrives there is a
browser somebody else sent, not a fetch with an answer to read. Both are public for exactly the
reason `/auth/login` is: they are how somebody who is nobody yet becomes somebody. Every decision
about whether to believe the token is the identity service’s.
"""
from __future__ import annotations

import hmac

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..repositories import AuditRepository, RoleRepository, WorkspaceRepository
from ..repositories.usage import AgentRepository
from ..services.admin import catalogue as permission_catalogue
from ..services.admin import sorted_permissions
from ..services.errors import Refused
from ..services.identity import SSO_COOKIE, SSO_MINUTES, IdentityService, Person, SsoService
from ..settings import settings
from .deps import COOKIE, current_person, gateway, identity_service, person_or_none, session, token_from

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
    # Whether this server opens the machine it runs on at all. A screen that offers a folder picker on a
    # hosted server, where every machine route answers 404, would be offering something that cannot happen.
    return {"user": who.public(), "workspace": {"name": name} if name else None,
            "machineAccess": settings().machine_access}


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/status")
async def status(who: Person | None = Depends(person_or_none),
                 identity: IdentityService = Depends(identity_service),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Public: does this workspace need its first Owner (and a setup token to make one), is the caller
    signed in, and is there a single sign-on button to draw?

    The SSO part is deliberately thin — whether there is a button and what it says. The issuer and the
    client id are an admin's business and are never handed to somebody who has not signed in.
    """
    name = await WorkspaceRepository(open_session).name()
    needs = await identity.count() == 0
    sso = await SsoService(open_session).settings()
    return {"needsSetup": needs, "setupNeedsToken": needs and bool(settings().setup_token),
            "user": who.public() if who else None, "workspace": {"name": name} if name else None,
            "machineAccess": settings().machine_access, "sso": sso.public()}


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


# ── single sign-on ───────────────────────────────────────────────
def _sso(open_session: AsyncSession, gw: Gateway) -> SsoService:
    """The service, holding the secrets file the client secret and the state key live in — the same
    file the model keys live in, for the same reason: it is on disk, mode 0600, and never in a row."""
    return SsoService(open_session, gw.secrets)


def _sso_cookie(response: Response, value: str | None) -> None:
    """The one-time cookie a sign-in is carried in. SameSite=Lax, because the provider sends the
    browser back with a top-level GET and a Strict cookie would not come with it; HttpOnly, because
    nothing in the page has any business reading a nonce."""
    if value is None:
        response.delete_cookie(SSO_COOKIE, path="/")
        return
    response.set_cookie(SSO_COOKIE, value, httponly=True, samesite="lax",
                        secure=settings().cookie_secure, max_age=SSO_MINUTES * 60, path="/")


@router.post("/sso/start")
async def sso_start(response: Response, open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, str]:
    """Where to send the browser. The state and the nonce go into a cookie of their own, so the
    answer that comes back can be checked against something this server wrote and nothing else."""
    where, cookie = await _sso(open_session, gw).start()
    _sso_cookie(response, cookie)
    return {"url": where}


@router.get("/sso/callback")
async def sso_callback(request: Request, code: str = "", state: str = "", error: str = "",
                       error_description: str = "", open_session: AsyncSession = Depends(session),
                       gw: Gateway = Depends(gateway)) -> RedirectResponse:
    """The provider sent the browser back here.

    It answers a redirect rather than JSON, because the thing that arrives here is a *browser* that
    was sent by somebody else — there is no fetch to read a body. A sign-in that did not happen goes
    back to the sign-in screen with the reason in the address, so a person reads a sentence rather
    than a blank page, and the reason is the service's own words.
    """
    service = _sso(open_session, gw)
    config = await service.settings()
    if error:
        return _sso_failed(config.home, f"{error}: {error_description}" if error_description else error)
    if not code:
        return _sso_failed(config.home, "The identity provider sent no authorization code.")
    try:
        who, token = await service.finish(code=code, state=state,
                                          cookie=request.cookies.get(SSO_COOKIE, ""),
                                          user_agent=request.headers.get("user-agent", ""),
                                          ip=_ip(request))
    except Refused as refused:
        await AuditRepository(open_session).record(action="auth.sso_failed", user_id=None,
                                                   target=config.issuer,
                                                   detail={"reason": str(refused)}, ip=_ip(request))
        return _sso_failed(config.home, str(refused))
    answer = RedirectResponse(config.home, status_code=303)
    _cookie(answer, token)
    _sso_cookie(answer, None)
    return answer


def _sso_failed(home: str, reason: str) -> RedirectResponse:
    from urllib.parse import quote

    answer = RedirectResponse(f"{home}?ssoError={quote(reason[:300])}", status_code=303)
    _sso_cookie(answer, None)
    return answer
