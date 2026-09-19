"""Routines and the inbox: work that starts on a cadence, from a webhook or on "Run now", and the one place
that says what needs a person, what is working and what finished since they last looked.

Reading a routine needs a session. Writing one reuses `workflows:write` — a routine is a workflow run
put on a clock, and no new permission would say anything that one does not. "Run now" also asks what the
Workflows screen's Run asks, `plans:compile` and `plans:decide`, because it acts as the person pressing
it. The webhook is the one route here with no session: its token is the permission, it is shown once,
and only its hash is kept.

Every fire is answered with 202 and carried on in the background (`services.schedules.fire_job`): a
compile can wait on a model for longer than a webhook sender waits for an answer. The fire's outcome
arrives on the stream as a `routine` event, and in the activity log.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.base import utcnow
from ..data.engine import Database
from ..services import schedules
from ..services.errors import Refused
from ..services.identity import Person
from ..services.inbox import InboxService
from ..services.schedules import (
    KEEP,
    MAX_CRON,
    MAX_NAME,
    MAX_PAYLOAD,
    MAX_REQUIREMENT,
    Cron,
    RoutineService,
    describe,
    excerpt,
    fire_json,
)
from ..schemas.work import when
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter(tags=["routines"])

WRITE = "workflows:write"
#: The most routines, or fires, one page holds.
MAX_LIST = 200


class RoutineIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    projectId: str = Field(min_length=1, max_length=40)
    #: A workflow to run; left out, `requirement` is compiled as a person's would be.
    workflowId: str | None = Field(default=None, max_length=40)
    #: The requirement each fire compiles — or, for a workflow, the input its `{input}` takes.
    requirement: str = Field(min_length=3, max_length=MAX_REQUIREMENT)
    #: Five-field cron in UTC; empty for a routine that fires only on "Run now" or its webhook.
    cadence: str = Field(default="", max_length=MAX_CRON)
    enabled: bool = True


class RoutinePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    projectId: str | None = Field(default=None, min_length=1, max_length=40)
    workflowId: str | None = Field(default=None, max_length=40)
    requirement: str | None = Field(default=None, min_length=3, max_length=MAX_REQUIREMENT)
    cadence: str | None = Field(default=None, max_length=MAX_CRON)
    enabled: bool | None = None


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _page(page: Any, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": items, "total": page.total, "limit": page.limit, "offset": page.offset,
            "nextOffset": page.next_offset}


# ── routines ─────────────────────────────────────────────────────
@router.get("/schedules", dependencies=[Depends(current_person)])
async def routines(project: str | None = Query(default=None, max_length=40),
                   limit: int = Query(default=100, ge=1), offset: int = Query(default=0, ge=0),
                   open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Every routine, by name, with its next minute, its last fire and why it would not fire now, if so."""
    service = RoutineService(open_session, gw)
    page = await service.listed(project=project, limit=min(limit, MAX_LIST), offset=offset)
    return _page(page, await service.documents(page.items))


@router.get("/schedules/cadence", dependencies=[Depends(current_person)])
async def cadence(cron: str = Query(default="", max_length=MAX_CRON)) -> dict[str, Any]:
    """What a cadence means before it is saved: in words, and its next three minutes in UTC. 422 with the
    reason when it does not read, or never comes round."""
    now = utcnow()
    if not cron.strip():
        return {"cron": "", "label": describe(""), "next": []}
    parsed = Cron.parse(cron)
    return {"cron": parsed.expression, "label": describe(parsed.expression),
            "next": [when(t) for t in parsed.upcoming(now)]}


@router.post("/schedules", status_code=201)
async def create(body: RoutineIn, who: Person = Depends(require(WRITE)), open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A routine. Nothing fires now: the first fire is at `nextAt`, or on "Run now"."""
    service = RoutineService(open_session, gw)
    made = await service.create(name=body.name, project_id=body.projectId, workflow_id=body.workflowId or None,
                                requirement=body.requirement, cadence=body.cadence, enabled=body.enabled, who=who)
    return await service.document(made)


@router.get("/schedules/{schedule_id}", dependencies=[Depends(current_person)])
async def routine(schedule_id: str, open_session: AsyncSession = Depends(session),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    service = RoutineService(open_session, gw)
    return await service.document(await service.get(schedule_id))


@router.get("/schedules/{schedule_id}/fires", dependencies=[Depends(current_person)])
async def fires(schedule_id: str, limit: int = Query(default=20, ge=1), offset: int = Query(default=0, ge=0),
                open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Its fires, newest first: what triggered each, what it started, and what became of it."""
    service = RoutineService(open_session, gw)
    page = await service.fires(schedule_id, limit=min(limit, MAX_LIST), offset=offset)
    states = await service.run_states([f.run_ref for f in page.items])
    return _page(page, [fire_json(f, states.get(f.run_ref or "")) for f in page.items])


@router.patch("/schedules/{schedule_id}")
async def change(schedule_id: str, body: RoutinePatch, who: Person = Depends(require(WRITE)),
                 open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Change what is sent. `enabled: false` pauses it (no next minute); true resumes it from now.
    `workflowId: null` turns a workflow routine into a requirement one."""
    service = RoutineService(open_session, gw)
    sent = body.model_fields_set
    changed = await service.update(
        schedule_id, who=who, name=body.name, project_id=body.projectId,
        workflow_id=(body.workflowId or None) if "workflowId" in sent else KEEP,
        requirement=body.requirement, cadence=body.cadence, enabled=body.enabled)
    return await service.document(changed)


@router.delete("/schedules/{schedule_id}")
async def remove(schedule_id: str, who: Person = Depends(require(WRITE)),
                 open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Deleted with its fires and its webhook. The plans and runs it started stay."""
    await RoutineService(open_session, gw).remove(schedule_id, who=who)
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run", status_code=202)
async def run_now(schedule_id: str, jobs: BackgroundTasks,
                  who: Person = Depends(require(WRITE, *schedules.FIRE_NEEDS)),
                  open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Fire it now, as you. 409 with the reason while its last fire's work is unfinished. The fire is
    compiled and dispatched after the answer; its outcome arrives as a `routine` event."""
    service = RoutineService(open_session, gw)
    schedule = await service.get(schedule_id)
    why = await service.blocked(schedule)
    if why:
        raise Refused(f"{schedule.name} did not fire: {why}")
    fire = await service.open_fire(schedule, "manual")
    await hand_off(open_session, jobs, schedules.fire_job, db, gw, fire.id, who.id)
    return fire_json(fire)


@router.post("/schedules/{schedule_id}/webhook/token", status_code=201)
async def issue_token(schedule_id: str, request: Request, who: Person = Depends(require(WRITE)),
                      open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A new webhook token — shown here once and never again. Any earlier token stops working now."""
    schedule, token = await RoutineService(open_session, gw).issue_token(schedule_id, who=who, ip=_ip(request))
    return {"scheduleId": schedule.id, "token": token, "path": f"/schedules/{schedule.id}/webhook"}


@router.delete("/schedules/{schedule_id}/webhook/token")
async def revoke_token(schedule_id: str, request: Request, who: Person = Depends(require(WRITE)),
                       open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    await RoutineService(open_session, gw).revoke_token(schedule_id, who=who, ip=_ip(request))
    return {"ok": True}


def _webhook_token(request: Request, token: str | None) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-neurocode-token", "").strip() or (token or "").strip()


@router.post("/schedules/{schedule_id}/webhook", status_code=202)
async def webhook(schedule_id: str, request: Request, jobs: BackgroundTasks,
                  token: str | None = Query(default=None, max_length=200),
                  open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Fire it from outside. The token is `Authorization: Bearer …`, `X-NeuroCode-Token`, or `?token=`.
    The body, whatever it is, is kept as a quoted excerpt and handed to the compiler as data. 401 for a
    token that does not open this routine; 409 while it is paused. While its last work is unfinished the
    call is still accepted, and the fire is recorded as skipped with the reason."""
    service = RoutineService(open_session, gw)
    schedule = await service.by_token(schedule_id, _webhook_token(request, token))
    if not schedule.enabled:
        raise Refused(f"{schedule.name} is paused, so its webhook does not fire it.")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) >= MAX_PAYLOAD:
            break
    payload = excerpt(raw)
    why = await service.blocked(schedule)
    if why:
        return fire_json(await service.skip(schedule, "webhook", why, payload))
    fire = await service.open_fire(schedule, "webhook", payload)
    await hand_off(open_session, jobs, schedules.fire_job, db, gw, fire.id, None)
    return fire_json(fire)


# ── the inbox ────────────────────────────────────────────────────
@router.get("/inbox")
async def inbox(who: Person = Depends(current_person), open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """What needs you, what is working, and what finished since you last marked the inbox seen."""
    return await InboxService(open_session).read(who.id)


@router.post("/inbox/seen")
async def inbox_seen(who: Person = Depends(current_person),
                     open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """"Done since" counts from now on. Nothing else changes: the inbox is read from the work itself."""
    return {"since": when(await InboxService(open_session).seen(who.id))}
