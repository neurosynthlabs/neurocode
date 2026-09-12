"""What every route is given: a unit of work, and who is asking.

The session dependency is the transaction. Everything a request does happens inside it and commits
together at the end; a request that raises rolls back whole, so a failure cannot leave half a change
behind. That used to be impossible — the old store committed each statement on its own.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..services.errors import Denied, Refused
from ..services.identity import IdentityService, Person

#: The session cookie. A script may send `Authorization: Bearer <token>` instead.
COOKIE = "nc_session"


def database(request: Request) -> Database:
    return request.app.state.db


def gateway(request: Request) -> Gateway:
    """The one door to the models. Held by the app, so a background task can be handed the same one."""
    return request.app.state.gateway


async def session(db: Database = Depends(database)) -> AsyncIterator[AsyncSession]:
    """One transaction per request: it commits when the route returns, and rolls back when it raises."""
    async with db.session() as open_session:
        yield open_session


async def identity_service(open_session: AsyncSession = Depends(session)) -> IdentityService:
    return IdentityService(open_session)


def token_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


async def person_or_none(request: Request,
                         identity: IdentityService = Depends(identity_service)) -> Person | None:
    """Who is asking, when anyone is. Used by the public routes that answer differently once signed in."""
    token = token_from(request)
    return await identity.whoami(token) if token else None


async def current_person(who: Person | None = Depends(person_or_none)) -> Person:
    if who is None:
        raise Refused("Sign in to continue.", status=401)
    return who


def require(*permissions: str):
    """Lets the request through only when the person holds every one of these."""
    async def dependency(who: Person = Depends(current_person)) -> Person:
        missing = [p for p in permissions if p not in who.permissions]
        if missing:
            raise Denied(", ".join(missing))
        return who
    return dependency


def require_any(*permissions: str):
    """Lets the request through when the person holds at least one of these."""
    async def dependency(who: Person = Depends(current_person)) -> Person:
        if not any(p in who.permissions for p in permissions):
            raise Denied(" or ".join(permissions))
        return who
    return dependency
