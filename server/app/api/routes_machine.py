"""The machine the API runs on, as the Workbench sees it: its folders and files.

Everything here needs `machine:access` (an Owner's permission) and `settings.machine_access`, and stays
inside `settings.machine_roots`. The setting is checked before anything else, so a server that turned it
off answers 404 to everyone — signed in or not — and does not even advertise that the routes exist.

The filesystem and git calls block, so each runs in a worker thread; the database is touched only to
write the audit line for a change.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas.machine import entry_json, files_json, git_json, listing_json, opened_json, root_json, saved_json
from ..services import machine
from ..services.errors import Denied, Refused
from ..services.identity import Person
from .deps import person_or_none, session

router = APIRouter(prefix="/machine", tags=["machine"])

#: The permission every machine route needs. Only the Owner role holds it out of the box.
ACCESS = "machine:access"
#: A path is a path; this is only a ceiling on what a request may send as one.
MAX_PATH = 4096


async def operator(who: Person | None = Depends(person_or_none)) -> Person:
    """The person at the machine: the setting first (off means 404 for everyone), then a session, then
    `machine:access`. The terminal routes use the same door."""
    machine.enabled()
    if who is None:
        raise Refused("Sign in to continue.", status=401)
    if ACCESS not in who.permissions:
        raise Denied(ACCESS, "use this machine")
    return who


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class FileSave(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH)
    #: The whole text. The size ceiling is the service's (2 MB encoded), said in words when crossed.
    text: str
    #: The SHA-1 of the file as the editor opened it; the save is refused when the file no longer has it.
    expectSha1: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-fA-F]{40}$")


class NewPath(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH)


@router.get("/roots", dependencies=[Depends(operator)])
async def roots() -> list[dict[str, Any]]:
    """The folders the browser may open. A configured root that does not exist is left out."""
    return [root_json(r) for r in await asyncio.to_thread(machine.roots)]


@router.get("/list", dependencies=[Depends(operator)])
async def listing(path: str, hidden: bool = False) -> dict[str, Any]:
    """A folder's entries, folders first, at most 2 000 (`capped` says when there were more)."""
    return listing_json(await asyncio.to_thread(machine.listing, path, hidden=hidden))


@router.get("/file", dependencies=[Depends(operator)])
async def read(path: str) -> dict[str, Any]:
    """A file's text (UTF-8, up to 2 MB) with the SHA-1 a save must send back; a binary file comes
    without text."""
    return opened_json(await asyncio.to_thread(machine.read, path))


@router.put("/file")
async def save(body: FileSave, request: Request, who: Person = Depends(operator),
               open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Save over the file only if it still has `expectSha1`; otherwise 409, and nothing is written."""
    saved, before = await asyncio.to_thread(machine.write, body.path, body.text, body.expectSha1)
    await machine.MachineService(open_session).saved(who, saved, before, ip=_ip(request))
    return saved_json(saved)


@router.post("/mkdir", status_code=201)
async def mkdir(body: NewPath, request: Request, who: Person = Depends(operator),
                open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    made = await asyncio.to_thread(machine.make_folder, body.path)
    await machine.MachineService(open_session).made(who, made, ip=_ip(request))
    return entry_json(made)


@router.post("/new-file", status_code=201)
async def new_file(body: NewPath, request: Request, who: Person = Depends(operator),
                   open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    made = await asyncio.to_thread(machine.make_file, body.path)
    await machine.MachineService(open_session).made(who, made, ip=_ip(request))
    return entry_json(made)


@router.get("/git", dependencies=[Depends(operator)])
async def git_status(path: str) -> dict[str, Any] | None:
    """Branch, ahead/behind its upstream, and the changed files of the repository holding `path`
    (paths relative to `root`), or null when the path is in no repository inside the roots."""
    return git_json(await asyncio.to_thread(machine.status, path))


@router.get("/files", dependencies=[Depends(operator)])
async def files(path: str) -> dict[str, Any]:
    """Every file under a folder, relative to it, for quick-open: git's list inside a repository
    (so .gitignore holds), a walk that skips hidden folders elsewhere; at most 20 000."""
    return files_json(await asyncio.to_thread(machine.files, path))
