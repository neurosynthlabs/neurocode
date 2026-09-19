"""Sessions over HTTP: a conversation that reads this project's code, and never loses a turn.

The route's whole job is to keep the question before anything else happens, then hand the thinking to
a background task. That ordering is the promise: if the model never answers, your question is still
there when you come back.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..repositories import ChatRepository, NotFound, ProjectRepository
from ..schemas import chat_json, chat_message_json
from ..services import chat as chat_service
from ..services.chat import MAX_QUESTION, ChatService
from ..services.identity import Person
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter(prefix="/sessions")


class SessionIn(BaseModel):
    projectId: str = Field(max_length=80)
    title: str = Field(default="", max_length=80)


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_QUESTION)


async def _name_of(open_session: AsyncSession, project_id: str) -> str:
    project = await ProjectRepository(open_session).get(project_id)
    return project.name if project else project_id


@router.get("", dependencies=[Depends(current_person)])
async def sessions(project: str | None = None, limit: int | None = None, offset: int = 0,
                   open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    page = await ChatRepository(open_session).newest(project, limit=limit, offset=offset)
    names = {p.id: p.name for p in await ProjectRepository(open_session).all_ordered()}
    return [chat_json(c, project_name=names.get(c.project_id, c.project_id)) for c in page.items]


@router.post("", status_code=201)
async def create(body: SessionIn, who: Person = Depends(require("sessions:chat")),
                 open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    chat = await ChatService(open_session, gw).start(body.projectId, who.name, body.title)
    return chat_json(chat, project_name=await _name_of(open_session, chat.project_id))


@router.get("/{ref}", dependencies=[Depends(current_person)])
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
    return {**chat_json(chat, project_name=project.name if project else chat.project_id, instructions=read),
            "messages": [chat_message_json(m) for m in messages]}


@router.post("/{ref}/messages", status_code=201)
async def ask(ref: str, body: AskIn, jobs: BackgroundTasks,
              who: Person = Depends(require("sessions:chat")),
              open_session: AsyncSession = Depends(session), db: Database = Depends(database),
              gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Ask, and let it think in the background — the turns arrive on the stream as they are written."""
    out = await ChatService(open_session, gw).ask(ref, body.text, who.name)
    await hand_off(open_session, jobs, chat_service.think, db, gw, ref, who.name)
    return {"message": chat_message_json(out["message"]),
            "session": chat_json(out["chat"], project_name=await _name_of(open_session, out["chat"].project_id))}


@router.post("/{ref}/compact", status_code=201)
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


@router.post("/{ref}/cancel")
async def stop(ref: str, who: Person = Depends(require("sessions:chat")),
               open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    chat = await ChatRepository(open_session).by_ref(ref)
    if chat is None:
        raise NotFound(f"session {ref}")
    chat_service.stop(ref)
    return chat_json(chat, project_name=await _name_of(open_session, chat.project_id))
