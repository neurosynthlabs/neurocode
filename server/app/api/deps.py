"""What every route is given: a unit of work, and who is asking.

The session dependency is the transaction. Everything a request does happens inside it and commits
together at the end; a request that raises rolls back whole, so a failure cannot leave half a change
behind. That used to be impossible — the old store committed each statement on its own.

"At the end" means before the response leaves, not after. FastAPI closes an ordinary yield dependency
only once the response has been sent, so a screen that wrote and then read straight back — empty the
workspace and reload it, open a session and ask it something — could read the state from before its
own write, and a commit that failed reached the screen as a 2xx for data that was never stored. The
transaction is therefore held in the *function* scope, which FastAPI closes as soon as the route has
returned and its answer is serialised: the commit is done, or has failed as a 500, before a byte of
the answer is sent. It is also why the live stream holds no connection while it streams.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..services.errors import Denied, Refused
from ..services.identity import IdentityService, Person
from ..services.tokens import PREFIX as TOKEN_PREFIX, TokenPerson, TokenService

#: The session cookie. A script may send `Authorization: Bearer <token>` instead — a session token, or a
#: personal access token (`nc_pat_…`) made in Settings or by `nc login`.
COOKIE = "nc_session"


def database(request: Request) -> Database:
    return request.app.state.db


def gateway(request: Request) -> Gateway:
    """The one door to the models. Held by the app, so a background task can be handed the same one."""
    return request.app.state.gateway


async def _unit_of_work(db: Database = Depends(database)) -> AsyncIterator[AsyncSession]:
    async with db.session() as open_session:
        yield open_session


async def session(open_session: AsyncSession = Depends(_unit_of_work, scope="function")) -> AsyncSession:
    """One transaction per request: it commits when the route returns, and rolls back when it raises.

    The scope is set once, here, rather than at the hundred-odd places a route asks for a session: a
    scope is part of FastAPI's cache key, so a route that said it at one place and not another would
    be handed two sessions in two transactions. Everything depends on this plain function, which
    returns the one session the function-scoped unit of work opened.
    """
    return open_session


async def identity_service(open_session: AsyncSession = Depends(session)) -> IdentityService:
    return IdentityService(open_session)


def token_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


async def person_or_none(request: Request,
                         identity: IdentityService = Depends(identity_service)) -> Person | None:
    """Who is asking, when anyone is. Used by the public routes that answer differently once signed in.

    A personal access token is its own kind of answer: the person, cut to the token's scopes. One that
    is expired, revoked or unknown is a 401 at once, even on a public route — a script holding a dead
    token should hear so, not quietly be treated as nobody."""
    token = token_from(request)
    if not token:
        return None
    if token.startswith(TOKEN_PREFIX):
        return await TokenService(identity.session).person(token)
    return await identity.whoami(token)


async def current_person(who: Person | None = Depends(person_or_none)) -> Person:
    if who is None:
        raise Refused("Sign in to continue.", status=401)
    return who


async def signed_in_person(who: Person = Depends(current_person)) -> Person:
    """A person who signed in themselves — a browser session or a session token — and not a personal
    access token. Managing tokens needs this: a token that could make tokens could make itself one
    with more than it was given."""
    if isinstance(who, TokenPerson):
        raise Refused("Access tokens are managed from a signed-in session, not with a token.", status=403)
    return who


async def hand_off(open_session: AsyncSession, jobs: BackgroundTasks, job: Callable[..., Any],
                   *args: Any) -> None:
    """Commit what this request wrote, then queue the job that will read it.

    The one place a route commits, and why: a background job runs in a transaction of its own, and
    once FastAPI started it *before* this request's session was closed and committed. So a job that
    went to fetch the project, the plan or the question the request had just written found nothing —
    and returned quietly. Onboarding stayed "onboarding" forever, a session's question was never
    answered, a dispatched plan never ran. The session now commits before the response (see `session`),
    which is before any job starts; this commit stays so the guarantee does not rest on the order
    FastAPI happens to close things in. Every route that hands work to a job does it through here.
    """
    await open_session.commit()
    jobs.add_task(job, *args)


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
