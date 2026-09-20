"""The extension screens: skills, plugins, commands and hooks, discovered on disk and switched on or off.

Every route here reads. What is found comes off disk on each request (in a thread), so a skill written a
minute ago shows up without anything being imported; the switches themselves are prefs, written through
`PUT /prefs/{key}` like every other screen setting. A key the screen sends back is looked up among what
discovery found — it is never turned into a path.

Skills, commands and plugins are catalogues — a name and a description — and every role is meant to read
them. A hook is not: it is a command line configured on this machine, and the redaction that takes the
secret-looking parts out of one is a best effort, not a fence. So the hooks list alone asks for
`settings:write`, the right that already governs switching hooks on and off.

`projectId` may be left out. A new workspace has no project, and the Claude home on this machine is real
before one exists, so without it the screens read that home alone and count no session.

Not every route here reads. Three of them change this machine, and each says so by the right it asks
for: defining a custom tool (`mcp:manage`) says what the runtime may reach for; installing or removing a
plugin (`mcp:manage`) brings somebody else's files onto this machine or takes them away; running one
hook (`settings:write`) runs a command line — and that one runs nothing a tool rule has not already
allowed, so pressing the button on a hook nobody allowed refuses exactly as its event would.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..services import custom_tools
from ..services.extensions import MANAGE as PLUGINS, ExtensionService
from ..services.identity import Person
from .deps import current_person, require, session

router = APIRouter(prefix="/extensions", dependencies=[Depends(current_person)])

PROJECT = Query(default=None, alias="projectId", min_length=1, max_length=80)


@router.get("/skills")
async def skills(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Every skill this project's sessions can see, with the loads sessions really made."""
    return await ExtensionService(open_session).skills(project_id)


@router.get("/skills/detail")
async def skill(project_id: str | None = PROJECT, key: str = Query(min_length=1, max_length=400),
                open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ExtensionService(open_session).skill(project_id, key)


@router.get("/commands")
async def commands(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ExtensionService(open_session).commands(project_id)


@router.get("/hooks", dependencies=[Depends(require("settings:write"))])
async def hooks(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The hooks Claude Code would run here, with anything that looks like a secret taken out."""
    return await ExtensionService(open_session).hooks(project_id)


@router.get("/plugins")
async def plugins(project_id: str | None = PROJECT, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ExtensionService(open_session).plugins(project_id)


# ── the tools a person defined ───────────────────────────────────
class CustomToolIn(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    description: str = Field(default="", max_length=custom_tools.MAX_DESCRIPTION)
    kind: Literal["command", "http"]
    #: What it runs or calls, and the JSON Schema for its arguments. Checked by the service, once.
    spec: dict[str, Any] = Field(default_factory=dict)
    projectId: str | None = Field(default=None, max_length=80)


class CustomToolPatch(BaseModel):
    description: str | None = Field(default=None, max_length=custom_tools.MAX_DESCRIPTION)
    spec: dict[str, Any] | None = None
    enabled: bool | None = None


class PluginIn(BaseModel):
    #: An https git URL to clone, or a folder on this machine inside the machine's roots.
    source: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=40)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/tools")
async def tools(project_id: str | None = PROJECT,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """Every custom tool this project's sessions could be offered — the workspace's and its own,
    switched off ones included, so the screen can show what exists as well as what is offered."""
    return await custom_tools.CustomToolService(open_session).listed(project_id)


@router.post("/tools", status_code=201)
async def define_tool(body: CustomToolIn, request: Request,
                      who: Person = Depends(require(custom_tools.MANAGE)),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Define one. It is still refused at every call until a tool rule allows its name — defining a tool
    says what could happen; a rule says whether it may."""
    return await custom_tools.CustomToolService(open_session).create(
        name=body.name, description=body.description, kind=body.kind, spec=body.spec,
        project_id=body.projectId or None, who=who, ip=_ip(request))


@router.patch("/tools/{tool_id}")
async def change_tool(tool_id: str, body: CustomToolPatch, request: Request,
                      who: Person = Depends(require(custom_tools.MANAGE)),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await custom_tools.CustomToolService(open_session).update(
        tool_id, description=body.description, spec=body.spec, enabled=body.enabled, who=who, ip=_ip(request))


@router.delete("/tools/{tool_id}")
async def remove_tool(tool_id: str, request: Request,
                      who: Person = Depends(require(custom_tools.MANAGE)),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await custom_tools.CustomToolService(open_session).delete(tool_id, who, ip=_ip(request))


# ── running one hook, once, because a person asked ───────────────
@router.post("/hooks/{hook_id}/run")
async def run_hook(hook_id: str, project_id: str | None = PROJECT,
                   who: Person = Depends(require("settings:write")),
                   open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Fire one hook now. It runs only if a `hook` rule allows it — the same rule that lets it fire on
    its own event — and the firing is in the activity log either way."""
    return await ExtensionService(open_session).fire_hook(project_id, hook_id, who)


# ── the workspace's own plugin folder ────────────────────────────
@router.post("/plugins/install", status_code=201)
async def install_plugin(body: PluginIn, who: Person = Depends(require(PLUGINS)),
                         open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Clone or copy a plugin into this workspace's own plugin folder. Claude Code's own cache is never
    written to: what is installed here reaches NeuroCode's sessions and nothing else."""
    return await ExtensionService(open_session).install_plugin(body.source, body.name, who)


@router.delete("/plugins/{name}")
async def uninstall_plugin(name: str, who: Person = Depends(require(PLUGINS)),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Delete one plugin folder this workspace installed. A plugin Claude Code installed is not ours to
    remove, and is not found here."""
    return await ExtensionService(open_session).remove_plugin(name, who)
