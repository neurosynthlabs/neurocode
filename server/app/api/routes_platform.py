"""Projects, the MCP registry, and the web.

A project's card carries numbers that used to be stored on it and drift: how many tasks it is
carrying, how many are running, how big the code is. They are all computed here, in two queries for
the whole list rather than one per project.

Onboarding a repository — cloning, scanning, indexing — still runs on the old stack; it moves with the
runtime in its own phase.

A project's sources are here too: the folders and repositories it holds beside its first one. Adding
one onboards it through the same pipeline as a project; the list says where each one is on this
machine only to someone who may browse the machine.

The web is here for the same reason MCP is: both are tools this server reaches out with on someone's
behalf, behind the same address guard and the same tool rules. `/web` says whether search is set up and
lets an admin set its key; `/web/search` and `/web/fetch` are a person trying it, and need `ai:use`,
the permission research — the first thing that searches the web — already asks for.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..models import McpServer
from ..repositories import ActivityRepository, NotFound, ProjectRepository
from ..repositories.code import CodeIndexRepository
from ..repositories.platform import McpRepository
from ..repositories.references import ProjectReferenceRepository
from ..repositories.sources import MAX_SOURCES, ProjectSourceRepository
from ..schemas import project_json
from ..schemas.platform import mcp_json
from ..schemas.work import source_json
from ..services.code import checkout, source_root
from ..services.errors import Refused
from ..services import instructions
from ..services.identity import Person
from ..services.mcp import MAX_ARGUMENTS, McpService
from ..services.web import MAX_QUERY, MAX_URL, WebService
from ..services.references import MAX_NOTE, ReferenceService, reference_json
from ..services.onboarding import OnboardingService, SourceService, SourceSpec, Spec, onboard, onboard_source, reread
from ..settings import settings
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter()


class RuleIn(BaseModel):
    id: str = Field(max_length=40)
    label: str = Field(max_length=120)
    note: str = Field(default="", max_length=300)


class ProjectIn(BaseModel):
    """What the onboarding wizard sends. Checked here; nothing touches git until it passes."""

    source: Literal["git", "local"]
    repo: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", max_length=100)
    excluded: list[str] = Field(default_factory=list, max_length=50)
    rules: list[RuleIn] = Field(default_factory=list, max_length=20)


class SourceIn(BaseModel):
    """A further folder or repository for a project. Checked here and by the service; nothing touches git
    until it passes."""

    label: str = Field(min_length=1, max_length=60)
    kind: Literal["git", "local"]
    repo: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", max_length=100)
    #: `reference`: indexed and read for grounding, never written to — documents, a design system,
    #: another team's repository.
    role: Literal["code", "reference"] = "code"


class SourceEdit(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=60)
    #: Its place among the further sources, from 0. The first source always stays first.
    position: int | None = Field(default=None, ge=0, le=MAX_SOURCES)
    role: Literal["code", "reference"] | None = None


class ReferenceIn(BaseModel):
    referencedId: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=MAX_NOTE)


class ReferenceEdit(BaseModel):
    note: str = Field(max_length=MAX_NOTE)


class McpIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    transport: Literal["stdio", "http", "sse"]
    command: str = Field(min_length=1, max_length=1000)
    scope: Literal["project", "global", "local"]
    defaultEffect: Literal["ask", "allow-read", "deny"]
    config: str = Field(max_length=4000)

    @field_validator("config")
    @classmethod
    def _is_json(cls, v: str) -> str:
        json.loads(v)          # a ValueError here becomes a 422
        return v


class TrustIn(BaseModel):
    trusted: bool


class ToolCallIn(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: The project the call is made for, so that project's tool rules apply; null is the workspace's.
    projectId: str | None = Field(default=None, max_length=80)


class WebKeyIn(BaseModel):
    key: str = Field(max_length=300)          # an empty string removes it


class WebSearchIn(BaseModel):
    q: str = Field(min_length=1, max_length=MAX_QUERY)
    projectId: str | None = Field(default=None, max_length=80)


class WebFetchIn(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL)
    projectId: str | None = Field(default=None, max_length=80)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/projects", dependencies=[Depends(current_person)])
async def projects(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    repo = ProjectRepository(open_session)
    found = await repo.all_ordered()
    counts = await repo.task_counts()
    ids = [p.id for p in found]
    indexes = await CodeIndexRepository(open_session).for_projects(ids)
    sources = await ProjectSourceRepository(open_session).for_projects(ids)
    references = await ProjectReferenceRepository(open_session).for_projects(ids)
    return [project_json(p, tasks=counts.get(p.id), index=indexes.get(p.id), sources=sources.get(p.id, []),
                         references=references.get(p.id, []))
            for p in found]


@router.get("/projects/{pid}", dependencies=[Depends(current_person)])
async def project(pid: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    repo = ProjectRepository(open_session)
    found = await repo.get(pid)
    if found is None:
        raise NotFound(f"project {pid}")
    counts = await repo.task_counts()
    return project_json(found, tasks=counts.get(pid),
                        index=await CodeIndexRepository(open_session).summary(pid),
                        sources=await ProjectSourceRepository(open_session).of(pid),
                        references=(await ProjectReferenceRepository(open_session).for_projects([pid])).get(pid, []))


def _may_see_the_machine(who: Person) -> bool:
    """Where a checkout is on disk is the machine's business: said to someone who may browse it, on a
    server that lets anyone browse it at all."""
    return settings().machine_access and who.can("machine:access")


@router.get("/projects/{pid}/sources")
async def project_sources(pid: str, who: Person = Depends(current_person),
                          open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """Every source of the project, the first one first (`id` null), then the rest in their order."""
    found = await ProjectRepository(open_session).get(pid)
    if found is None:
        raise NotFound(f"project {pid}")
    show = _may_see_the_machine(who)
    first = checkout(found)
    out = [source_json(found, None, root=str(first) if first else None, show_root=show)] if found.source_kind else []
    for row in await ProjectSourceRepository(open_session).of(pid):
        where = source_root(row)
        out.append(source_json(found, row, root=str(where) if where else None, show_root=show))
    return out


@router.post("/projects/{pid}/sources", status_code=201)
async def add_source(pid: str, body: SourceIn, jobs: BackgroundTasks,
                     who: Person = Depends(require("projects:onboard")),
                     open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Add a folder or a repository to the project. The row is written now; cloning, measuring and
    indexing the whole project with it run after, and land in Activity as they finish."""
    service = SourceService(open_session)
    made = await service.add(pid, SourceSpec(label=body.label, kind=body.kind, repo=body.repo,
                                             branch=body.branch, role=body.role), who.name)
    project = await service.project(pid)
    where = source_root(made)
    doc = source_json(project, made, root=str(where) if where else None, show_root=_may_see_the_machine(who))
    await hand_off(open_session, jobs, onboard_source, db, gw, pid, made.id)
    return doc


@router.patch("/projects/{pid}/sources/{source_id}")
async def edit_source(pid: str, source_id: int, body: SourceEdit, jobs: BackgroundTasks,
                      who: Person = Depends(require("projects:onboard")),
                      open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                      gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Rename, move or change the role of a source. A rename indexes the project again, because every
    file of the source is named under its label; a role change reads nothing again."""
    if body.label is None and body.position is None and body.role is None:
        raise Refused("Say what to change: a label, a position or a role.", status=422)
    service = SourceService(open_session)
    changed, renamed = await service.update(pid, source_id, who.name, label=body.label, position=body.position,
                                            role=body.role)
    project = await service.project(pid)
    where = source_root(changed)
    doc = source_json(project, changed, root=str(where) if where else None, show_root=_may_see_the_machine(who))
    if renamed:
        await hand_off(open_session, jobs, reread, db, gw, pid)
    return doc


@router.delete("/projects/{pid}/sources/{source_id}")
async def remove_source(pid: str, source_id: int, jobs: BackgroundTasks,
                        who: Person = Depends(require("projects:onboard")),
                        open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                        gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Take a source out of the project: its rows in the index go at once, the project's figures are
    measured again after. Its folder, and a clone of it, are never deleted."""
    gone = await SourceService(open_session).remove(pid, source_id, who.name)
    await hand_off(open_session, jobs, reread, db, gw, pid)
    return {"ok": True, "id": source_id, "label": gone.label}


@router.post("/projects/{pid}/sources/{source_id}/reindex", status_code=202)
async def reindex_source(pid: str, source_id: int, jobs: BackgroundTasks,
                         who: Person = Depends(require("projects:onboard")),
                         open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Read a source again — and, when it failed, onboard it again from the start. Answered at once."""
    source, again = await SourceService(open_session).reindex(pid, source_id, who.name)
    await hand_off(open_session, jobs, *((onboard_source, db, gw, pid, source.id) if again else (reread, db, gw, pid)))
    return {"ok": True, "onboarding": again}


# ── projects a project reads from ────────────────────────────────
@router.get("/projects/{pid}/references", dependencies=[Depends(current_person)])
async def project_references(pid: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """`{references, referencedBy, readAtMost, max}`: the projects this one reads from, and those that
    read from it — each `{id, project: {id, name, status, understoodPct}, note, createdAt}`. Retrieval
    and grounding search the first `readAtMost` of `references` beside the project's own pieces."""
    return await ReferenceService(open_session).listing(pid)


@router.post("/projects/{pid}/references", status_code=201)
async def add_reference(pid: str, body: ReferenceIn, request: Request,
                        who: Person = Depends(require("projects:onboard")),
                        open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Read another project from this one: refused for the project itself (422), one it already
    references (409) and a project that does not exist (404). Audited."""
    made, other = await ReferenceService(open_session).add(pid, body.referencedId, body.note, who, ip=_ip(request))
    return reference_json(made, other)


@router.patch("/projects/{pid}/references/{reference_id}")
async def edit_reference(pid: str, reference_id: int, body: ReferenceEdit, request: Request,
                         who: Person = Depends(require("projects:onboard")),
                         open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Change the note — why it is referenced, which a model reads beside what it reads there."""
    changed, other = await ReferenceService(open_session).update(pid, reference_id, body.note, who, ip=_ip(request))
    return reference_json(changed, other)


@router.delete("/projects/{pid}/references/{reference_id}")
async def remove_reference(pid: str, reference_id: int, request: Request,
                           who: Person = Depends(require("projects:onboard")),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    other = await ReferenceService(open_session).remove(pid, reference_id, who, ip=_ip(request))
    return {"ok": True, "id": reference_id, "referencedId": other.id}


@router.get("/projects/{pid}/instructions", dependencies=[Depends(current_person)])
async def project_instructions(pid: str, target: list[str] = Query(default_factory=list, max_length=50),
                               open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The instruction files at the project's checkout root, read now: which were read, their size and
    sha1, which rules applied, what an `@import` was refused and why, and whether the cap cut the text.
    With `target` (repeatable), a rule scoped by `paths:` says whether it would apply to those files.
    A project with no code on this machine answers with no files, not an error."""
    found = await ProjectRepository(open_session).get(pid)
    if found is None:
        raise NotFound(f"project {pid}")
    return instructions.as_json(await instructions.for_project(found, target, session=open_session))


@router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, jobs: BackgroundTasks,
                         who: Person = Depends(require("projects:onboard")),
                         open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Onboard a repository. The row is written now; cloning, measuring and indexing run after."""
    spec = Spec(source=body.source, repo=body.repo, branch=body.branch, excluded=body.excluded,
                rules=[r.model_dump() for r in body.rules])
    project = await OnboardingService(open_session).create(spec, who.name)
    await hand_off(open_session, jobs, onboard, db, gw, project.id, spec)
    return project_json(project, sources=[])


@router.get("/mcp/servers", dependencies=[Depends(current_person)])
async def mcp_servers(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    return [mcp_json(s) for s in await McpRepository(open_session).all_ordered()]


@router.post("/mcp/servers", status_code=201)
async def register_mcp(body: McpIn, who: Person = Depends(require("mcp:manage")),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Registered from here, a server's output stays **untrusted** until a person promotes it."""
    repo = McpRepository(open_session)
    if await repo.get(body.name) is not None:
        raise Refused(f"A server called {body.name} is already registered.")
    server = await repo.add(McpServer(
        id=body.name, name=body.name, transport=body.transport, status="disconnected",
        scope=body.scope, command=body.command.strip(), untrusted=True,
        default_effect=body.defaultEffect, config=body.config,
        # Given up front so the collection exists: reading it on a brand-new object would otherwise
        # be a lazy load, and a lazy load inside async code raises instead of returning nothing.
        # Nothing about it is measured yet; a check fills that in.
        tools=[]))
    await ActivityRepository(open_session).record(
        actor=who.name, actor_kind="human", action="MCP server registered",
        detail=f"{server.id} · {body.transport} · {body.scope} scope · tools default to {body.defaultEffect}",
        project_id=None)
    return mcp_json(server)


@router.post("/mcp/servers/{server_id}/trust")
async def trust_mcp(server_id: str, body: TrustIn, who: Person = Depends(require("mcp:manage")),
                    open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Trusting a stdio server is what allows its command to be launched; the service asks for the
    permission that takes, on top of managing the registry."""
    return mcp_json(await McpService(open_session).trust(server_id, body.trusted, who))


@router.post("/mcp/servers/{server_id}/check")
async def check_mcp(server_id: str, who: Person = Depends(require("mcp:manage")),
                    open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Connect once and record what happened. A failed check is still a 200: the server's status and
    reason are the answer. Refused only when the check may not run at all."""
    return mcp_json(await McpService(open_session).check(server_id, who))


@router.post("/mcp/servers/{server_id}/tools/{name}/call")
async def call_mcp_tool(server_id: str, name: str, body: ToolCallIn, request: Request,
                        who: Person = Depends(require("mcp:manage")),
                        open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Try one tool: refused in words before anything is sent when the server is untrusted, was not
    connected at its last check, never listed the tool, or a rule denies it. Once sent, what came back
    is the answer — `ok: false` with the reason when the call itself failed."""
    if len(json.dumps(body.arguments)) > MAX_ARGUMENTS:
        raise Refused(f"The arguments are larger than {MAX_ARGUMENTS // 1000} KB.", status=422)
    await _known_project(open_session, body.projectId)
    return await McpService(open_session).call_tool(server_id, name, body.arguments, who,
                                                    project_id=body.projectId or None, ip=_ip(request))


# ── the web ──────────────────────────────────────────────────────
@router.get("/web")
async def web_status(who: Person = Depends(current_person), open_session: AsyncSession = Depends(session),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Whether web search can answer, and the caps on fetching. The masked key only for an admin."""
    return WebService(open_session, gw.secrets).status(masked=who.can("workspace:admin"))


@router.put("/web")
async def set_web_key(body: WebKeyIn, request: Request, who: Person = Depends(require("workspace:admin")),
                      open_session: AsyncSession = Depends(session),
                      gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await WebService(open_session, gw.secrets).set_key(body.key, who, ip=_ip(request))


@router.post("/web/search")
async def web_search(body: WebSearchIn, who: Person = Depends(require("ai:use")),
                     open_session: AsyncSession = Depends(session),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    await _known_project(open_session, body.projectId)
    return await WebService(open_session, gw.secrets).search(body.q, actor=who.name,
                                                             project_id=body.projectId or None)


@router.post("/web/fetch")
async def web_fetch(body: WebFetchIn, who: Person = Depends(require("ai:use")),
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    await _known_project(open_session, body.projectId)
    return await WebService(open_session, gw.secrets).fetch(body.url, actor=who.name,
                                                            project_id=body.projectId or None)


async def _known_project(open_session: AsyncSession, project_id: str | None) -> None:
    if project_id and await ProjectRepository(open_session).get(project_id) is None:
        raise NotFound(f"project {project_id}")
