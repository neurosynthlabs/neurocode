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

"Who is asking" has two answers where a project is involved: what a person holds across the workspace
(`require`) and what they hold inside one project (`scoped`). The second is the whole of per-project
rights — a grant that narrows nothing anybody actually asks for narrows nothing at all.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import replace
from typing import Any

from fastapi import BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..models import Project
from ..repositories import NotFound, ProjectRepository
from ..services.errors import Denied, Refused
from ..services.identity import NEVER_NARROWED, IdentityService, Person
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


def _all_of(held: frozenset[str], wanted: Iterable[str]) -> None:
    """Every one of these, or a refusal naming the ones that are missing."""
    if missing := [p for p in wanted if p not in held]:
        raise Denied(", ".join(missing))


def require(*permissions: str):
    """Lets the request through only when the person holds every one of these."""
    async def dependency(who: Person = Depends(current_person)) -> Person:
        _all_of(who.permissions, permissions)
        return who
    return dependency


def require_any(*permissions: str):
    """Lets the request through when the person holds at least one of these."""
    async def dependency(who: Person = Depends(current_person)) -> Person:
        if not any(p in who.permissions for p in permissions):
            raise Denied(" or ".join(permissions))
        return who
    return dependency


def scoped(*permissions: str, path: str = "pid"):
    """`require`, asked inside the project this request names.

    The permission is tested against what the person holds *there* — their workspace rights, narrowed
    by the grant the project gives them (`Person.in_project`) — and the person handed to the route is
    narrowed the same way, so a check the route makes for itself later cannot quietly answer with
    more than the project allows. A project nobody has restricted resolves to the workspace set
    unchanged, which is every project until somebody restricts one, so this costs one indexed read
    and changes nothing else.

    A restricted project the person holds no grant in answers 404 before the permission is even
    weighed: whether there is a project here is itself something they were not told.

    Two cases are deliberately *not* guessed at. A request that names no project in its path is
    exactly `require(*permissions)` — there is nothing to narrow by, and refusing on a guess would be
    inventing a rule. A project id that is not a project at all is left to the route to answer in its
    own words, because this dependency knows what was asked for and not what it was for.
    """
    async def dependency(request: Request, who: Person = Depends(current_person),
                         open_session: AsyncSession = Depends(session)) -> Person:
        pid = request.path_params.get(path)
        if not pid or not isinstance(pid, str):
            _all_of(who.permissions, permissions)
            return who
        return await holds_in(who, open_session, pid, f"project {pid}", *permissions)
    return dependency


async def holds_in(who: Person, open_session: AsyncSession, project_id: str | None, what: str,
                   *permissions: str) -> Person:
    """`scoped`, for a row reached by its own reference — a run, a plan — once the row has told us its
    project: 404 naming `what` when the project is one this person may not see, then every permission
    asked of what they hold *there*. Returns the person narrowed to that, so what the route asks of them
    afterwards (may they also decide the gate?) is answered inside the project too.

    A row of no project, or of a project id that is not a project, is weighed against the workspace set,
    exactly as `scoped` weighs a path that names none."""
    found = await ProjectRepository(open_session).get(project_id) if project_id else None
    if found is None:
        _all_of(who.permissions, permissions)
        return who
    if not who.may_see(found.id, found.restricted):
        raise NotFound(what)
    held = who.in_project(found.id, found.restricted)
    _all_of(held, permissions)
    return who if held == who.permissions else replace(who, permissions=held)


async def unseen_by(who: Person, open_session: AsyncSession) -> frozenset[str]:
    """The projects this person may not see: the restricted ones they hold no grant in.

    Said as what to hide rather than what to show, because the answer outlives the question. A list
    of what may be seen goes stale the moment a project is made — and a project is open to the whole
    workspace until somebody restricts it, so a new one would be filtered out of a feed for no
    reason. What must be hidden only grows by an admin's deliberate act.

    One query in a workspace where nothing is restricted, which is every workspace until something
    is; two where something is.
    """
    if who.permissions & NEVER_NARROWED:     # an Owner or Admin is never locked out of a project
        return frozenset()
    restricted = set((await open_session.execute(
        select(Project.id).where(Project.restricted.is_(True)))).scalars())
    if not restricted:
        return frozenset()
    return frozenset(restricted - set(await IdentityService(open_session).project_rights(who.id)))


async def must_see(who: Person, open_session: AsyncSession, project_id: str | None, what: str) -> None:
    """The same fence for one row, reached by its own reference rather than through a list.

    `scoped` cannot help here: the path names a task, a plan, a run or a review, not the project it
    belongs to, so the project is only known once the row has been read. What the row knows is its
    `project_id`, and that is enough — a row of a project `unseen_by` hides is not there.

    404 and never 403, for the same reason `scoped` answers 404: "there is a plan called PLAN-12,
    and you may not read it" has already told them the thing the restriction exists to withhold. A
    row belonging to no project is the workspace's own and is never hidden.
    """
    if project_id and project_id in await unseen_by(who, open_session):
        raise NotFound(what)
