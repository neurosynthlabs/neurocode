"""Problems in a project's code, from its own checkers (tsc, eslint, ruff, mypy, pyright, go vet, cargo, …), and hover and
go to definition from the language servers found on this machine.

Everything here runs programs on the machine the API runs on, so every route goes through the machine's
own door (`routes_machine.operator`): machine access on for this server (off answers 404 to everyone),
a session, and `machine:access`. Every folder and file a request names is resolved by `machine.inside`,
so nothing outside the machine's roots is checked, read or handed to a language server.

- `GET  /diagnostics/checkers` — what a check of this folder would run, and what the project declares
  but this machine lacks. Starts nothing.
- `POST /diagnostics/checks` — start a check (answers at once, `status: "running"`); read it with
  `GET /diagnostics/checks/{id}` (problems paged, filterable), stop it with `…/cancel`.
- `GET  /diagnostics/checks` — the newest check of each folder, for a project or a folder.
- `GET  /diagnostics/file` — the problems the newest finished check found in one file: the editor's
  underlines.
- `GET  /lsp/status`, `POST /lsp/hover`, `POST /lsp/definition`, `POST /lsp/symbols` — the language
  server for the file shown; `GET /lsp/servers` and `DELETE /lsp/servers/{id}` — those running.

Checks and language servers live in this process (like terminals); closing the API stops them all.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import AuditRepository, NotFound, ProjectRepository
from ..services import code, diagnostics, lsp, machine
from ..services.errors import Refused
from ..services.identity import Person
from .deps import session
from .routes_machine import MAX_PATH, operator


def checks_of(app: Any) -> diagnostics.Checks:
    """The app's checks, made on first use — a test app that never ran its lifespan still has them."""
    if getattr(app.state, "checks", None) is None:
        app.state.checks = diagnostics.Checks()
    return app.state.checks


def servers_of(app: Any) -> lsp.LanguageServers:
    if getattr(app.state, "language_servers", None) is None:
        app.state.language_servers = lsp.LanguageServers()
    return app.state.language_servers


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Closing the API stops every check and language server it started, rather than leaving a tsc or a
    gopls running with nobody to read it."""
    yield
    if getattr(app.state, "checks", None) is not None:
        await app.state.checks.close_all()
    if getattr(app.state, "language_servers", None) is not None:
        await app.state.language_servers.close_all()


router = APIRouter(tags=["diagnostics"], lifespan=_lifespan)


def checks(request: Request) -> diagnostics.Checks:
    return checks_of(request.app)


def servers(request: Request) -> lsp.LanguageServers:
    return servers_of(request.app)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


# ── where a check runs ────────────────────────────────────────────
async def _target(open_session: AsyncSession, *, project_id: str | None, source: str | None,
                  folder: str | None) -> diagnostics.Target:
    """A project's source (its first when `source` is not named) or a folder — inside the roots, either way."""
    if bool(project_id) == bool(folder):
        raise Refused("Name a project (and optionally one of its sources) or a folder — one of the two.", status=422)
    if folder:
        real = await asyncio.to_thread(machine.inside, folder)
        if not real.is_dir():
            raise Refused(f"{folder} is not a folder.", status=404)
        return diagnostics.Target(kind="folder", folder=real, name=real.name or str(real))
    project = await ProjectRepository(open_session).get(project_id or "")
    if project is None:
        raise NotFound(f"project {project_id}")
    sources = await code.roots(open_session, project)
    if not sources:
        raise Refused(f"{project.name} has no folder on this machine to check.", status=409)
    chosen = next((s for s in sources if (s.primary and not source) or (source and s.label == source)), None)
    if chosen is None:
        raise NotFound(f"source {source} of {project.name}")
    if not chosen.ready:
        raise Refused(f"{chosen.label} is not on this machine yet.", status=409)
    real = await asyncio.to_thread(machine.inside, str(chosen.root))
    if not real.is_dir():
        raise Refused(f"{project.name}'s folder {chosen.root} is not on this machine.", status=409)
    return diagnostics.Target(kind="project", folder=real, name=chosen.label if not chosen.primary else project.name,
                              prefix=chosen.prefix, project_id=project.id, source=chosen.label)


def _detect(target: diagnostics.Target) -> tuple[list[diagnostics.Checker], list[diagnostics.Missing]]:
    real = target.folder
    roots = machine._real_roots()                                   # noqa: SLF001 — the same roots `inside` used
    bound = max((r for r in roots if real == r or real.is_relative_to(r)), key=lambda r: len(r.parts), default=None)
    return diagnostics.detect(real, bound)


class CheckIn(BaseModel):
    projectId: str | None = Field(default=None, max_length=120)
    #: A further source's label; the project's first source when left out.
    source: str | None = Field(default=None, max_length=60)
    folder: str | None = Field(default=None, max_length=MAX_PATH)
    #: Only these tools (by `tool`), when a person wants one; every checker found otherwise.
    tools: list[str] | None = Field(default=None, max_length=20)


@router.get("/diagnostics/checkers")
async def checkers(projectId: str | None = Query(default=None, max_length=120),
                   source: str | None = Query(default=None, max_length=60),
                   folder: str | None = Query(default=None, max_length=MAX_PATH),
                   _: Person = Depends(operator), open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """`{target, checkers: [{tool, label, command, why}], missing: [{tool, why}]}` — what a check would run."""
    target = await _target(open_session, project_id=projectId, source=source, folder=folder)
    found, missing = await asyncio.to_thread(_detect, target)
    return {"target": target.json(), "checkers": [c.json(target.folder) for c in found],
            "missing": [m.json() for m in missing]}


@router.post("/diagnostics/checks", status_code=202)
async def start_check(body: CheckIn, request: Request, who: Person = Depends(operator),
                      open_session: AsyncSession = Depends(session),
                      held: diagnostics.Checks = Depends(checks)) -> dict[str, Any]:
    """Run the folder's checkers, one after another, in the background. Answers the check at once; a
    folder with no checker is refused with what it lacks, so "no problems" always means a tool said so."""
    target = await _target(open_session, project_id=body.projectId, source=body.source, folder=body.folder)
    found, missing = await asyncio.to_thread(_detect, target)
    if body.tools is not None:
        wanted = set(body.tools)
        found = [c for c in found if c.tool in wanted]
    if not found:
        lacking = "; ".join(m.why for m in missing)
        raise Refused(f"No checker to run in {target.name}." + (f" {lacking}" if lacking else
                      " It declares none NeuroCode runs: a tsconfig, an ESLint config, oxlint, ruff, mypy or pyright "
                      "configured, a go.mod, a Cargo.toml, a Gradle or Maven build, or a .NET project."), status=409)
    check = held.start(owner=who.id, target=target, checkers=found, missing=missing)
    await AuditRepository(open_session).record(
        action="diagnostics.check", user_id=who.id, target=str(target.folder),
        detail={"check": check.id, "project": target.project_id, "source": target.source,
                "commands": [c.command(target.folder) for c in found]}, ip=_ip(request))
    return check.json(limit=0)


@router.get("/diagnostics/checks")
async def recent_checks(projectId: str | None = Query(default=None, max_length=120),
                        folder: str | None = Query(default=None, max_length=MAX_PATH),
                        who: Person = Depends(operator),
                        held: diagnostics.Checks = Depends(checks)) -> list[dict[str, Any]]:
    """The newest check of each folder — of a project's sources, of one folder, or all of yours — with
    their counts and tools but no problems (read those from the check). At most `KEEP_DONE`."""
    real = await asyncio.to_thread(machine.inside, folder) if folder else None
    return [c.json(limit=0) for c in held.latest(who.id, folder=real, project_id=projectId)]


@router.get("/diagnostics/checks/{check_id}")
async def read_check(check_id: str, offset: int = Query(default=0, ge=0),
                     limit: int = Query(default=diagnostics.PAGE_DEFAULT, ge=0),
                     severity: str | None = Query(default=None, pattern="^(error|warning|info)$"),
                     tool: str | None = Query(default=None, max_length=40),
                     who: Person = Depends(operator), held: diagnostics.Checks = Depends(checks)) -> dict[str, Any]:
    """One check, its problems errors first then by file and line, one page at a time (at most 1 000)."""
    return held.get(check_id, who.id).json(offset=offset, limit=min(limit, diagnostics.PAGE_MAX),
                                           severity=severity, tool=tool)


@router.post("/diagnostics/checks/{check_id}/cancel")
async def cancel_check(check_id: str, who: Person = Depends(operator),
                       held: diagnostics.Checks = Depends(checks)) -> dict[str, Any]:
    """Stop a check: the tool running is killed with everything it started; what finished is kept."""
    check = held.get(check_id, who.id)
    check.cancel()
    if check.task is not None:
        await asyncio.wait({check.task}, timeout=10)
    return check.json(limit=0)


@router.get("/diagnostics/file")
async def file_problems(path: str = Query(min_length=1, max_length=MAX_PATH), who: Person = Depends(operator),
                        held: diagnostics.Checks = Depends(checks)) -> dict[str, Any]:
    """`{path, checkId, checkedAt, problems}` — the newest finished check holding this file, and what it
    found there; `checkId` is null when no check has looked at it."""
    real = await asyncio.to_thread(machine.inside, path)
    check, found = held.of_file(who.id, str(real))
    return {"path": str(real), "checkId": check.id if check else None,
            "checkedAt": check.json(limit=0)["endedAt"] if check else None,
            "problems": [p.json() for p in found[:diagnostics.PAGE_MAX]]}


# ── language servers ──────────────────────────────────────────────
class At(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH)
    #: 1-based line and column (UTF-16 code units, the editor's own).
    line: int = Field(default=1, ge=1, le=10_000_000)
    col: int = Field(default=1, ge=1, le=10_000_000)
    #: The editor's text when it differs from the file (unsaved typing); the file on disk otherwise.
    text: str | None = None


@router.get("/lsp/status", dependencies=[Depends(operator)])
async def lsp_status(path: str = Query(min_length=1, max_length=MAX_PATH),
                     held: lsp.LanguageServers = Depends(servers)) -> dict[str, Any]:
    """`{language, state, server, root, message}` for the file shown: `none` (no language server covers
    it), `missing` (none installed — `message` says what installs one), `available`, `starting`, `ready`
    or `failed`. Starts nothing."""
    return await asyncio.to_thread(lsp.status, held, path)


@router.post("/lsp/hover", dependencies=[Depends(operator)])
async def lsp_hover(body: At, held: lsp.LanguageServers = Depends(servers)) -> dict[str, Any]:
    """`{server, markdown, range}` — what the language server says about the name at line:col."""
    return await lsp.hover(held, body.path, body.line, body.col, body.text)


@router.post("/lsp/definition", dependencies=[Depends(operator)])
async def lsp_definition(body: At, held: lsp.LanguageServers = Depends(servers)) -> dict[str, Any]:
    """`{server, locations: [{path, openable, line, col, endLine, endCol}]}` — where the name is defined.
    `openable` is false for a definition outside the machine's roots (a library's own file)."""
    return await lsp.definition(held, body.path, body.line, body.col, body.text)


@router.post("/lsp/symbols", dependencies=[Depends(operator)])
async def lsp_symbols(body: At, held: lsp.LanguageServers = Depends(servers)) -> dict[str, Any]:
    """`{server, symbols: [{name, detail, kind, depth, line, col}], capped}` — the file's outline."""
    return await lsp.symbols(held, body.path, body.text)


@router.get("/lsp/servers", dependencies=[Depends(operator)])
async def lsp_servers(held: lsp.LanguageServers = Depends(servers)) -> list[dict[str, Any]]:
    """The language servers running, most recently used first (at most `MAX_SERVERS`)."""
    return [s.json() for s in held.running()]


@router.delete("/lsp/servers/{server_id}", dependencies=[Depends(operator)])
async def stop_lsp_server(server_id: str, held: lsp.LanguageServers = Depends(servers)) -> dict[str, bool]:
    await held.remove(server_id)
    return {"ok": True}
