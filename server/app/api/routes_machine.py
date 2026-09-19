"""The machine the API runs on, as the Workbench sees it: its folders and files.

Everything here needs `machine:access` (an Owner's permission) and `settings.machine_access`, and stays
inside `settings.machine_roots`. The setting is checked before anything else, so a server that turned it
off answers 404 to everyone — signed in or not — and does not even advertise that the routes exist.

The filesystem and git calls block, so each runs in a worker thread; the database is touched only to
write the audit line for a change.

A project can also start here: from an archive on this machine (`/machine/import`), from one the browser
uploads (`/machine/import/upload`, for a server the person is not sitting at), or as an empty folder
with a repository (`/machine/empty-project`). Each makes a new folder — never merging into one that
exists — and then onboards it exactly as a folder picked in the wizard would be. They also need
`projects:onboard`, because what they end in is a new project.

On a Mac, a folder the system guards per app (Desktop, Documents, Downloads, iCloud Drive, external
volumes) answers 403 with `code: "needs_os_permission"`, the folder and the app to allow — the fix is a
switch in System Settings, which the screen names, never something this server can do.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..schemas import project_json
from ..schemas.machine import entry_json, files_json, git_json, listing_json, opened_json, root_json, saved_json
from ..services import archive as archives
from ..services import machine
from ..services.errors import Denied, Refused
from ..services.identity import Person
from ..services.onboarding import OnboardingService, Spec, onboard
from .deps import database, gateway, hand_off, person_or_none, require, session
from .routes_platform import RuleIn

T = TypeVar("T")

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


class ProjectOptions(BaseModel):
    """What the onboarding wizard adds to any new project: paths never read, and rules recorded."""

    excluded: list[str] = Field(default_factory=list, max_length=50)
    rules: list[RuleIn] = Field(default_factory=list, max_length=20)


class ImportIn(ProjectOptions):
    #: The archive, on this machine, inside the roots.
    archive: str = Field(min_length=1, max_length=MAX_PATH)
    #: The folder the project's own folder is made in, and that folder's name.
    into: str = Field(min_length=1, max_length=MAX_PATH)
    name: str = Field(min_length=1, max_length=120)


class EmptyIn(ProjectOptions):
    into: str = Field(min_length=1, max_length=MAX_PATH)
    name: str = Field(min_length=1, max_length=120)


#: The most an upload may carry: the most an import may unpack, since an archive is never larger.
MAX_UPLOAD = archives.MAX_TOTAL
#: The small fields beside an upload's file.
MAX_FIELD = 64 * 1024
#: Where an upload is held until it is unpacked: the system's temporary folder, unless set.
UPLOAD_DIR: str | None = None


async def _answer(call: Callable[..., T], *args: Any, **kwargs: Any) -> T | JSONResponse:
    """A blocking call on a worker thread, with macOS's privacy refusal answered in full: the words, and
    the `code`, folder and app the screen builds its "turn it on in System Settings" card from."""
    try:
        return await asyncio.to_thread(call, *args, **kwargs)
    except machine.NeedsOsPermission as blocked:
        return JSONResponse(blocked.as_json(), status_code=blocked.status)


async def _guarded(work: Awaitable[T]) -> T | JSONResponse:
    try:
        return await work
    except machine.NeedsOsPermission as blocked:
        return JSONResponse(blocked.as_json(), status_code=blocked.status)


def _spec(target: Path, options: ProjectOptions) -> Spec:
    return Spec(source="local", repo=str(target), branch="main", excluded=options.excluded,
                rules=[r.model_dump() for r in options.rules])


@router.get("/roots", dependencies=[Depends(operator)])
async def roots() -> list[dict[str, Any]]:
    """The folders the browser may open. A configured root that does not exist is left out."""
    return [root_json(r) for r in await asyncio.to_thread(machine.roots)]


@router.get("/places", dependencies=[Depends(operator)])
async def places() -> list[dict[str, Any]]:
    """Desktop, Downloads and Documents — the quick places a file picker offers — when each is here and
    inside the roots."""
    return [root_json(r) for r in await asyncio.to_thread(machine.places)]


@router.get("/list", dependencies=[Depends(operator)])
async def listing(path: str, hidden: bool = False) -> Any:
    """A folder's entries, folders first, at most 2 000 (`capped` says when there were more). A folder
    macOS guards from the app that started the API answers 403 `needs_os_permission`."""
    found = await _answer(machine.listing, path, hidden=hidden)
    return found if isinstance(found, JSONResponse) else listing_json(found)


@router.get("/file", dependencies=[Depends(operator)])
async def read(path: str) -> Any:
    """A file's text (UTF-8, up to 2 MB) with the SHA-1 a save must send back; a binary file comes
    without text."""
    found = await _answer(machine.read, path)
    return found if isinstance(found, JSONResponse) else opened_json(found)


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


# ── new projects: from an archive, or empty ──────────────────────
@router.get("/archive", dependencies=[Depends(operator)])
async def archive_survey(path: str) -> Any:
    """What an archive would become, read from its headers and every entry checked — nothing written:
    `{path, name, suggestedName, kind, bytes, entries, files, folders, total, top, sample}`. `top` is
    the single folder that will be dropped, when there is one. Refusals say which guard stopped it."""
    found = await _answer(machine.survey_archive, path)
    if isinstance(found, JSONResponse):
        return found
    file, surveyed = found
    return {"path": str(file), "name": file.name, "suggestedName": surveyed.top or archives.stem_of(file.name),
            **surveyed.brief()}


async def _start_import(open_session: AsyncSession, jobs: BackgroundTasks, db: Database, gw: Gateway,
                        who: Person, request: Request, *, archive: Path, shown: str, kind: str,
                        into: str, name: str, options: ProjectOptions, uploaded: bool) -> dict[str, Any]:
    """Survey, make the folder, write the project, and hand the unpacking to a job. The folder is made
    before the project row, because onboarding checks the folder exists — and it is empty, so the job
    that fills it is the only writer there."""
    target = await asyncio.to_thread(machine.new_folder, into, name)
    try:
        surveyed = await asyncio.to_thread(archives.survey, archive, kind)
    except PermissionError as denied:
        raise machine.unreadable(archive.parent, denied) from denied
    await asyncio.to_thread(machine.make_project_folder, target)
    spec = _spec(target, options)
    try:
        project = await OnboardingService(open_session).create(spec, who.name)
    except BaseException:
        await asyncio.to_thread(os.rmdir, target)          # still empty: nothing was written in it
        raise
    await machine.MachineService(open_session).imported(who, shown, target, project.id, len(surveyed.entries),
                                                        surveyed.total, uploaded=uploaded, ip=_ip(request))
    await hand_off(open_session, jobs, archives.import_archive, db, gw, project.id, archive, target, surveyed, spec,
                   uploaded)
    return {"project": project_json(project, sources=[]), "target": str(target), **surveyed.brief()}


@router.post("/import", status_code=201, dependencies=[Depends(require("projects:onboard"))])
async def import_archive(body: ImportIn, request: Request, jobs: BackgroundTasks, who: Person = Depends(operator),
                         open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                         gw: Gateway = Depends(gateway)) -> Any:
    """Make a new project from an archive on this machine: `{archive, into, name, excluded?, rules?}`.
    Checked now — every entry, the new folder (refused if it exists) — then unpacked and onboarded in the
    background, each stage in Activity. Answers `{project, target, kind, entries, files, total, top, …}`."""
    async def work() -> dict[str, Any]:
        file, kind = await asyncio.to_thread(machine.archive_at, body.archive)
        return await _start_import(open_session, jobs, db, gw, who, request, archive=file, shown=str(file), kind=kind,
                                   into=body.into, name=body.name, options=body, uploaded=False)
    return await _guarded(work())


async def _receive(request: Request) -> tuple[Path, str, dict[str, str]]:
    """The browser's upload, streamed to a temporary file with the ceiling enforced as it arrives — a
    body past `MAX_UPLOAD` is cut off at that byte, never read to its end first. Returns the file, the
    name the browser gave it, and the small fields beside it."""
    kind, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise Refused("Send the archive as multipart/form-data, in a part called archive.", status=415)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD + MAX_FIELD:
        raise Refused(f"The upload is larger than {MAX_UPLOAD // 1024 ** 3} GB, the most one import may carry.",
                      status=413)
    fd, temp = tempfile.mkstemp(prefix="neurocode-upload-", suffix=".part", dir=UPLOAD_DIR)
    out = os.fdopen(fd, "wb")
    state: dict[str, Any] = {"header": b"", "value": b"", "headers": {}, "part": None, "filename": "",
                             "fields": {}, "file": 0, "files": 0, "too_big": False}

    def on_part_begin() -> None:
        state["headers"], state["part"] = {}, None

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["header"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        state["headers"][state["header"].decode("latin-1").lower()] = state["value"]
        state["header"] = state["value"] = b""

    def on_headers_finished() -> None:
        _, disposition = parse_options_header(state["headers"].get("content-disposition", b""))
        name = disposition.get(b"name", b"").decode("utf-8", "replace")
        if b"filename" in disposition:
            state["files"] += 1
            state["part"] = "file" if name == "archive" and state["files"] == 1 else "skip"
            if state["part"] == "file":
                state["filename"] = disposition[b"filename"].decode("utf-8", "replace")
        else:
            state["part"] = ("field", name)
            state["fields"][name] = b""

    def on_part_data(data: bytes, start: int, end: int) -> None:
        part = state["part"]
        if part == "file":
            state["file"] += end - start
            if state["file"] > MAX_UPLOAD:
                state["too_big"] = True
                return
            out.write(data[start:end])
        elif isinstance(part, tuple):
            held = state["fields"][part[1]]
            if len(held) + (end - start) <= MAX_FIELD:
                state["fields"][part[1]] = held + data[start:end]

    parser = MultipartParser(boundary, {"on_part_begin": on_part_begin, "on_header_field": on_header_field,
                                        "on_header_value": on_header_value, "on_header_end": on_header_end,
                                        "on_headers_finished": on_headers_finished, "on_part_data": on_part_data})
    try:
        async for chunk in request.stream():
            parser.write(chunk)
            if state["too_big"]:
                raise Refused(f"The upload is larger than {MAX_UPLOAD // 1024 ** 3} GB, the most one import may "
                              "carry. Nothing was kept.", status=413)
        parser.finalize()
        out.close()
        if not state["filename"]:
            raise Refused("No archive arrived: send it in a part called archive, with its file name.", status=422)
    except BaseException:
        out.close()
        os.unlink(temp)
        raise
    fields = {k: v.decode("utf-8", "replace") for k, v in state["fields"].items()}
    return Path(temp), os.path.basename(state["filename"].replace("\\", "/")), fields


@router.post("/import/upload", status_code=201, dependencies=[Depends(require("projects:onboard"))])
async def import_upload(request: Request, jobs: BackgroundTasks,
                        into: str = Query(min_length=1, max_length=MAX_PATH),
                        name: str = Query(min_length=1, max_length=120),
                        who: Person = Depends(operator), open_session: AsyncSession = Depends(session),
                        db: Database = Depends(database), gw: Gateway = Depends(gateway)) -> Any:
    """The browser uploads the archive (multipart: `archive`, and optionally `options` =
    `{"excluded": [...], "rules": [...]}` as JSON); `into` and `name` are in the query, so the new
    folder is checked before a byte is received. The same checks as an archive on this machine, then
    the same job; the uploaded copy is deleted once it is unpacked, or refused."""
    async def work() -> dict[str, Any]:
        await asyncio.to_thread(machine.new_folder, into, name)          # refuse before receiving anything
        temp, filename, fields = await _receive(request)
        try:
            kind = archives.kind_of(filename)
            if kind is None:
                raise Refused(f"{filename} is not an archive this server unpacks ({', '.join(archives.SUFFIXES)}).",
                              status=422)
            try:
                options = ProjectOptions.model_validate(json.loads(fields["options"])) if fields.get("options") \
                    else ProjectOptions()
            except (ValueError, ValidationError) as bad:
                raise Refused("options must be JSON: {\"excluded\": [...], \"rules\": [...]}.", status=422) from bad
            return await _start_import(open_session, jobs, db, gw, who, request, archive=temp, shown=filename,
                                       kind=kind, into=into, name=name, options=options, uploaded=True)
        except BaseException:
            await asyncio.to_thread(archives._forget, temp)      # noqa: SLF001 — the job owns it only once handed off
            raise
    return await _guarded(work())


@router.post("/empty-project", status_code=201, dependencies=[Depends(require("projects:onboard"))])
async def empty_project(body: EmptyIn, request: Request, jobs: BackgroundTasks, who: Person = Depends(operator),
                        open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                        gw: Gateway = Depends(gateway)) -> Any:
    """Start a project from nothing: a new folder `name` inside `into`, `git init` on main, a README
    committed once (so agents have a branch to work from), then onboarded. Answers `{project, target}`."""
    async def work() -> dict[str, Any]:
        target = await asyncio.to_thread(machine.new_folder, body.into, body.name)
        await asyncio.to_thread(machine.empty_project, target, body.name.strip(), who.name, who.email)
        spec = _spec(target, body)
        project = await OnboardingService(open_session).create(spec, who.name)
        await machine.MachineService(open_session).started(who, target, project.id, ip=_ip(request))
        await hand_off(open_session, jobs, onboard, db, gw, project.id, spec)
        return {"project": project_json(project, sources=[]), "target": str(target)}
    return await _guarded(work())
