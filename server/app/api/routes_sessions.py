"""Sessions over HTTP: a conversation that reads this project's code, and never loses a turn.

The route's whole job is to keep the question before anything else happens, then hand the thinking to
a background task. That ordering is the promise: if the model never answers, your question is still
there when you come back.

Everything that shapes a session goes through here too: answering a tool call's permission card (which
resumes the answer), editing a question and regenerating an answer (new turns; the old ones stay),
forking, export and import, "Make this a plan", uploads, and the composer's `@` mentions — the one route
not under /sessions, since what it looks up belongs to a project.
"""
from __future__ import annotations

import asyncio
from typing import Any

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..repositories import ChatRepository, NotFound, ProjectRepository
from ..models import Chat
from ..schemas import chat_json, chat_message_json, plan_json, task_json
from ..services import chat as chat_service
from ..services.chat import MAX_ATTACHED, MAX_QUESTION, ChatService
from ..services.custom_agents import CustomAgentService
from ..services.identity import Person
from ..services.sessions import MAX_IMAGE_UPLOAD, SessionShapes, upload_json
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter()
sessions_router = APIRouter(prefix="/sessions")


class SessionIn(BaseModel):
    projectId: str = Field(max_length=80)
    title: str = Field(default="", max_length=80)
    #: "Ask <agent>": the agent every answer in this session is given by — its key from GET /agents/custom.
    agent: str | None = Field(default=None, max_length=120)


class Mention(BaseModel):
    """Something the person attached: a file, a symbol, a fact, a plan (from `@`) or an upload."""

    kind: Literal["file", "symbol", "fact", "plan", "upload"]
    ref: str = Field(min_length=1, max_length=500)
    name: str = Field(default="", max_length=300)


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_QUESTION)
    attachments: list[Mention] = Field(default_factory=list, max_length=MAX_ATTACHED)


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_QUESTION)
    #: Left out: the question keeps what was attached to it.
    attachments: list[Mention] | None = Field(default=None, max_length=MAX_ATTACHED)


class RegenerateIn(BaseModel):
    #: Another lane to answer on; left out, the router chooses as it always does.
    lane: str | None = Field(default=None, max_length=40)


class PermitIn(BaseModel):
    decision: Literal["once", "session", "refuse"]


class ForkIn(BaseModel):
    at: int = Field(ge=1)


class ImportIn(BaseModel):
    projectId: str = Field(max_length=80)
    document: dict[str, Any]


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    mime: str = Field(default="", max_length=120)
    #: The file's bytes, base64 — a picture of at most 5 MB is at most this long.
    data: str = Field(min_length=1, max_length=(MAX_IMAGE_UPLOAD * 4) // 3 + 8)


def _items(found: list[Mention] | None) -> list[dict[str, Any]] | None:
    return None if found is None else [m.model_dump() for m in found]


async def _json(open_session: AsyncSession, chat: Chat) -> dict[str, Any]:
    """A session as the screens read it, with the card it waits on, when it waits on one."""
    return chat_json(chat, project_name=await _name_of(open_session, chat.project_id),
                     waiting=await chat_service.pending_permission(open_session, chat.id))


async def _name_of(open_session: AsyncSession, project_id: str) -> str:
    project = await ProjectRepository(open_session).get(project_id)
    return project.name if project else project_id


@sessions_router.get("", dependencies=[Depends(current_person)])
async def sessions(project: str | None = None, limit: int | None = None, offset: int = 0,
                   open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    page = await ChatRepository(open_session).newest(project, limit=limit, offset=offset)
    names = {p.id: p.name for p in await ProjectRepository(open_session).all_ordered()}
    waiting = await chat_service.waiting_on(open_session, [c.id for c in page.items])
    return [chat_json(c, project_name=names.get(c.project_id, c.project_id), waiting=waiting.get(c.id))
            for c in page.items]


@sessions_router.post("", status_code=201)
async def create(body: SessionIn, who: Person = Depends(require("sessions:chat")),
                 open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    chat = await ChatService(open_session, gw).start(body.projectId, who.name, body.title, agent=body.agent)
    return chat_json(chat, project_name=await _name_of(open_session, chat.project_id))


@sessions_router.get("/{ref}", dependencies=[Depends(current_person)])
async def session_detail(ref: str, after: int = 0,
                         open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The session and its turns. `after` is the last message id you hold, for catching up."""
    chats = ChatRepository(open_session)
    chat = await chats.by_ref(ref)
    if chat is None:
        raise NotFound(f"session {ref}")
    messages = await chats.messages(chat.id, after)
    project = await ProjectRepository(open_session).get(chat.project_id)
    # The instruction files its model is handed, read the way the answering loop reads them.
    read = await asyncio.to_thread(chat_service.instruction_files, project)
    waiting = await chat_service.pending_permission(open_session, chat.id)
    parent = await open_session.get(Chat, chat.parent_id) if chat.parent_id else None
    return {**chat_json(chat, project_name=project.name if project else chat.project_id, instructions=read,
                        waiting=waiting),
            "parentRef": parent.ref if parent else None,
            "messages": [chat_message_json(m) for m in messages]}


@sessions_router.post("/{ref}/messages", status_code=201)
async def ask(ref: str, body: AskIn, jobs: BackgroundTasks,
              who: Person = Depends(require("sessions:chat")),
              open_session: AsyncSession = Depends(session), db: Database = Depends(database),
              gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Ask, and let it think in the background — the turns arrive on the stream as they are written."""
    out = await ChatService(open_session, gw).ask(ref, body.text, who.name, _items(body.attachments) or [])
    await hand_off(open_session, jobs, _answer, db, gw, ref, who)
    return {"message": chat_message_json(out["message"]),
            "session": chat_json(out["chat"], project_name=await _name_of(open_session, out["chat"].project_id))}


@sessions_router.post("/{ref}/compact", status_code=201)
async def compact(ref: str, who: Person = Depends(require("sessions:chat")),
                  open_session: AsyncSession = Depends(session),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Fold the session's older turns into one summary a model writes. The turns stay, marked, for the
    person to read; the model is sent the summary instead. Asked in the request, like Ask memory: the
    person is waiting on it, and a background job would only make them wait for the stream instead."""
    summary = await ChatService(open_session, gw).compact(ref, who.name)
    chat = await ChatRepository(open_session).by_ref(ref)
    if chat is None:
        raise NotFound(f"session {ref}")
    return {"summary": chat_message_json(summary),
            "session": chat_json(chat, project_name=await _name_of(open_session, chat.project_id))}


@sessions_router.post("/{ref}/cancel")
async def stop(ref: str, who: Person = Depends(require("sessions:chat")),
               open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    chat = await ChatRepository(open_session).by_ref(ref)
    if chat is None:
        raise NotFound(f"session {ref}")
    chat_service.stop(ref)
    return chat_json(chat, project_name=await _name_of(open_session, chat.project_id))


async def _answer(db: Database, gw: Gateway, ref: str, who: Person) -> None:
    """The background answer, for the person who asked — the acting tools it may reach are theirs."""
    await chat_service.think(db, gw, ref, who.name, who)


@sessions_router.post("/{ref}/permissions/{message_id}")
async def permit(ref: str, message_id: int, body: PermitIn, jobs: BackgroundTasks,
                 who: Person = Depends(require("sessions:chat")),
                 open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Answer a permission card: allow once, allow for this session, or refuse. The answer resumes where
    it paused — an allowed call is made first; a refused one is told to the model as refused."""
    chat = await ChatService(open_session, gw).permit(ref, message_id, body.decision, who)
    await hand_off(open_session, jobs, _answer, db, gw, ref, who)
    return await _json(open_session, chat)


@sessions_router.post("/{ref}/messages/{message_id}/edit", status_code=201)
async def edit(ref: str, message_id: int, body: EditIn, jobs: BackgroundTasks,
               who: Person = Depends(require("sessions:chat")),
               open_session: AsyncSession = Depends(session), db: Database = Depends(database),
               gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Ask an edited question in place of an earlier one. The old question and what followed it stay,
    marked as replaced, and the model is sent only the new line."""
    out = await ChatService(open_session, gw).edit(ref, message_id, body.text, who.name, _items(body.attachments))
    await hand_off(open_session, jobs, _answer, db, gw, ref, who)
    return {"message": chat_message_json(out["message"]), "session": await _json(open_session, out["chat"])}


@sessions_router.post("/{ref}/messages/{message_id}/regenerate", status_code=201)
async def regenerate(ref: str, message_id: int, jobs: BackgroundTasks, body: RegenerateIn | None = None,
                     who: Person = Depends(require("sessions:chat")),
                     open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Answer the question behind this answer again, on another lane when one is named."""
    out = await ChatService(open_session, gw).regenerate(ref, message_id, who.name, body.lane if body else None)
    await hand_off(open_session, jobs, _answer, db, gw, ref, who)
    return {"message": chat_message_json(out["message"]), "session": await _json(open_session, out["chat"])}


@sessions_router.post("/{ref}/fork", status_code=201)
async def fork(ref: str, body: ForkIn, who: Person = Depends(require("sessions:chat")),
               open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A new session with this one's turns up to `at`, which remembers where it came from — and the agent
    it was asked through, while that agent is still there."""
    made = await SessionShapes(open_session, gw).fork(ref, body.at, who)
    parent = await ChatRepository(open_session).by_ref(ref)
    if parent is not None and parent.agent:
        project = await ProjectRepository(open_session).get(made.project_id)
        if await CustomAgentService(open_session).resolve(project, parent.agent) is not None:
            made.agent = parent.agent
            await open_session.flush()
    return await _json(open_session, made)


@sessions_router.get("/{ref}/export", dependencies=[Depends(current_person)])
async def export(ref: str, format: Literal["md", "json"] = "md", open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """`{filename, mime, text}`: the screen makes the download from it. JSON holds every turn, replaced
    ones included; Markdown the current line."""
    return await SessionShapes(open_session, gw).export(ref, format)


@sessions_router.post("/import", status_code=201)
async def import_session(body: ImportIn, who: Person = Depends(require("sessions:chat")),
                         open_session: AsyncSession = Depends(session),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A session export read back into a new session on the named project."""
    made = await SessionShapes(open_session, gw).import_(body.projectId, body.document, who)
    return await _json(open_session, made)


@sessions_router.post("/{ref}/to-plan", status_code=201)
async def to_plan(ref: str, who: Person = Depends(require("plans:compile")),
                  open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """"Make this a plan": the last question and the refs its answer rests on, compiled like any
    requirement. Needs a model — 409 with none configured, 502 when every lane failed."""
    plan, task = await SessionShapes(open_session, gw).to_plan(ref, who)
    return {**plan_json(plan, task_ref=task.ref), "task": task_json(task)}


@sessions_router.post("/{ref}/files", status_code=201)
async def upload(ref: str, body: UploadIn, who: Person = Depends(require("sessions:chat")),
                 open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A file dropped into the composer. It is attached to a question by its id (`kind: upload`)."""
    return upload_json(await SessionShapes(open_session, gw).upload(ref, body.name, body.mime, body.data, who))


@sessions_router.get("/{ref}/files/{file_id}", dependencies=[Depends(current_person)])
async def uploaded(ref: str, file_id: int, open_session: AsyncSession = Depends(session),
                   gw: Gateway = Depends(gateway)) -> Response:
    """An upload's bytes, for its chip. Only the types an upload may be are ever served, never sniffed,
    and never as a page: a text file comes back as plain text whatever it claims to be."""
    found = await SessionShapes(open_session, gw).file(ref, file_id)
    mime = found.mime if found.mime in chat_service.IMAGE_TYPES else "text/plain; charset=utf-8"
    return Response(found.data, media_type=mime, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.get("/projects/{pid}/mentions", dependencies=[Depends(current_person)])
async def mentions(pid: str, q: str = Query(default="", max_length=120),
                   open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """What the composer's `@` offers for this project: files, symbols, facts and plans."""
    return {"items": await SessionShapes(open_session, gw).mentions(pid, q)}


# Last, once every route above is on it: included routes are copied at the moment of inclusion.
router.include_router(sessions_router)
