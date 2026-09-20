"""The gates a person decided and the rules the runtime applies.

The approvals themselves are served by the work routes; what lives here is what outlasts a single
approval — the answers the runtime keeps and consults the next time, per project — and the tool rules a
person writes ahead of time: allow, ask or deny, for a tool and a pattern, for the workspace or one
project. Reading either needs a session; writing a tool rule decides what runs without a person, so it
needs `rules:manage`, and every change to one is in the audit log.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.gates import RuleService
from ..services.identity import Person
from ..services.tool_rules import MANAGE, MAX_NOTE, MAX_PATTERN, MAX_SUBJECT, ToolRuleService
from .deps import current_person, require, session, unseen_by

router = APIRouter(prefix="/permissions")

#: The most tool rules one page holds, however the caller spells the number.
MAX_LIST = 200

Tool = Literal["edit", "command", "read", "web_fetch", "web_search", "mcp"]
Answer = Literal["allow", "ask", "deny"]


class ToolRuleIn(BaseModel):
    tool: Tool
    pattern: str = Field(min_length=1, max_length=MAX_PATTERN)
    action: Answer
    note: str = Field(default="", max_length=MAX_NOTE)
    #: Null for a rule that holds across the workspace.
    projectId: str | None = Field(default=None, max_length=80)


class ToolRulePatch(BaseModel):
    pattern: str | None = Field(default=None, min_length=1, max_length=MAX_PATTERN)
    action: Answer | None = None
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class ToolRuleTry(BaseModel):
    tool: Tool
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT)
    projectId: str | None = Field(default=None, max_length=80)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/rules")
async def rules(limit: int | None = None, offset: int = 0, who: Person = Depends(current_person),
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """Each project's standing answer to running its own tests, with who gave it and when — of the
    projects this person may see. Every row is a project, so the fence is the project's own."""
    return await RuleService(open_session).rules(limit=limit, offset=offset,
                                                 hidden=await unseen_by(who, open_session))


@router.get("/tool-rules")
async def tool_rules(project: str | None = Query(default=None, max_length=80), tool: Tool | None = None,
                     limit: int = Query(default=100, ge=1), offset: int = Query(default=0, ge=0),
                     who: Person = Depends(current_person),
                     open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The tool rules, workspace first. `project` is a project id, or `workspace` for only its own.
    A rule written for a project this person may not see is not among them."""
    return await ToolRuleService(open_session).listed(project=project, tool=tool, limit=min(limit, MAX_LIST),
                                                      offset=offset,
                                                      hidden=await unseen_by(who, open_session))


@router.post("/tool-rules", status_code=201)
async def add_tool_rule(body: ToolRuleIn, request: Request, who: Person = Depends(require(MANAGE)),
                        open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ToolRuleService(open_session).create(tool=body.tool, pattern=body.pattern, action=body.action,
                                                      note=body.note, project_id=body.projectId or None, who=who,
                                                      ip=_ip(request))


@router.post("/tool-rules/test", dependencies=[Depends(current_person)])
async def try_tool_rule(body: ToolRuleTry, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """What would happen if this tool were used on this subject here, and which rule says so. Nothing runs."""
    return await ToolRuleService(open_session).test(body.tool, body.subject, body.projectId or None)


@router.patch("/tool-rules/{rule_id}")
async def change_tool_rule(rule_id: int, body: ToolRulePatch, request: Request,
                           who: Person = Depends(require(MANAGE)),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ToolRuleService(open_session).update(rule_id, pattern=body.pattern, action=body.action,
                                                      note=body.note, who=who, ip=_ip(request))


@router.delete("/tool-rules/{rule_id}")
async def remove_tool_rule(rule_id: int, request: Request, who: Person = Depends(require(MANAGE)),
                           open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await ToolRuleService(open_session).delete(rule_id, who, ip=_ip(request))
