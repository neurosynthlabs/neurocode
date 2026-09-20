"""The Git screen: worktrees, branches and what would merge cleanly, read from the repositories on disk.

A project with several sources has a repository per source: `?source=<label>` reads that one, and the
first source's is read when none is named. Reading needs only a session — the same exposure `/runs`
already has. Merging is still `POST /runs/{ref}/merge`, behind `runs:merge`, and approving a gate is
`/approvals`, behind its own.

The last three routes are the only ones that write, and they exist because the Workbench edits the
checkout itself: a file diff against the last commit, a commit of the files a person picked, and
putting files back the way that commit had them. Without them a typo fixed in the editor blocked
every merge until the person left for a terminal. They are the machine's own door — machine access
on, `machine:access`, inside the machine's roots — asked for inside the project through `scoped`,
and each one writes an audit line.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.git import MAX_CHANGE_PATHS, MAX_MESSAGE
from ..services import machine
from ..services.git_view import MAX_COMMITS, GitChangeService, GitViewService
from ..services.identity import Person
from .deps import current_person, scoped, session

router = APIRouter(prefix="/projects/{pid}/git")

#: The permission the three writing routes need, on top of machine access being on at all.
ACCESS = "machine:access"
#: A path as the repository names it — relative to its top, which is how git's own status reports one.
RelPath = Annotated[str, Field(min_length=1, max_length=4096)]


async def machine_on() -> None:
    """A server with machine access off answers as if these routes did not exist, signed in or not.

    It is a route dependency rather than a line in each body, because FastAPI solves those first: the
    setting is weighed before the session, exactly as `/machine` weighs it.
    """
    machine.enabled()


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class Commit(BaseModel):
    paths: list[RelPath] = Field(min_length=1, max_length=MAX_CHANGE_PATHS)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    source: str | None = None


class Discard(BaseModel):
    paths: list[RelPath] = Field(min_length=1, max_length=MAX_CHANGE_PATHS)
    source: str | None = None


@router.get("", dependencies=[Depends(current_person)])
async def overview(pid: str, source: str | None = None,
                   open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The checkout, every worktree beside it, and what merging each would do. A project with no code on
    this machine answers `available: false` with the reason, not an error. `sources` lists the labels
    that may be passed as `source`."""
    return await GitViewService(open_session).overview(pid, source)


@router.get("/diff", dependencies=[Depends(current_person)])
async def diff(pid: str, branch: str, against: Literal["base", "head"] = "base", source: str | None = None,
               open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """A branch's files. `base`: what it changed since it started. `head`: what merging it now would bring."""
    return await GitViewService(open_session).diff(pid, branch, against, source)


@router.get("/conflicts", dependencies=[Depends(current_person)])
async def conflicts(pid: str, source: str | None = None,
                    open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    return await GitViewService(open_session).conflicts(pid, source)


@router.get("/commits", dependencies=[Depends(current_person)])
async def commits(pid: str, limit: int = 200, source: str | None = None,
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await GitViewService(open_session).commits(pid, min(MAX_COMMITS, max(1, limit)), source)


@router.get("/file-diff", dependencies=[Depends(machine_on), Depends(scoped(ACCESS))])
async def file_diff(pid: str, path: str, source: str | None = None,
                    open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """What one file in the checkout has that the last commit does not, as git prints it.

    A file git has never seen answers with the whole file as additions and `change: "?"`, which is
    what the file tree already colours it as."""
    return await GitChangeService(open_session).file_diff(pid, path, source)


@router.post("/commit", dependencies=[Depends(machine_on)])
async def commit(pid: str, body: Commit, request: Request, who: Person = Depends(scoped(ACCESS)),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Commit exactly the files named, and nothing else the checkout happens to have changed.

    The commit is made with the git identity this machine is configured with, never NeuroCode's own
    address: that address is how the Git screen tells an agent's commits from a person's."""
    return await GitChangeService(open_session).commit(pid, body.paths, body.message, who,
                                                       ip=_ip(request), source=body.source)


@router.post("/discard", dependencies=[Depends(machine_on)])
async def discard(pid: str, body: Discard, request: Request, who: Person = Depends(scoped(ACCESS)),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Put these files back the way the last commit had them. A file that commit does not hold is
    refused by name rather than deleted."""
    return await GitChangeService(open_session).discard(pid, body.paths, who,
                                                        ip=_ip(request), source=body.source)
