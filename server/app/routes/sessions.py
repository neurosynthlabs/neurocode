"""Sessions over HTTP: a conversation that reads this project's code, and never loses a turn."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import chat
from ..auth import User, current_user, require
from ..context import Ctx, ctx, need

router = APIRouter(prefix="/sessions")


class SessionIn(BaseModel):
    projectId: str = Field(max_length=80)
    title: str = Field(default="", max_length=80)


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=chat.MAX_QUESTION)


@router.get("", dependencies=[Depends(current_user)])
async def sessions(project: str | None = None, c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.docs("SELECT doc FROM chats WHERE (? IS NULL OR project_id = ?) ORDER BY started DESC",
                        (project, project))


@router.post("", status_code=201)
async def create(body: SessionIn, user: User = Depends(require("sessions:chat")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    project = need(c.store.get("projects", body.projectId), f"project {body.projectId}")
    doc = chat.start(c, project, user.name, body.title)
    c.act(user, "Session started", f"{doc['ref']} · {project['name']}", project=project["id"])
    return doc


@router.get("/{ref}", dependencies=[Depends(current_user)])
async def session(ref: str, after: int = 0, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """The session and its turns. `after` is the last message id you hold, for catching up."""
    doc = need(c.store.one("chats", ref), ref)
    return {**doc, "messages": c.store.messages(doc["id"], after)}


@router.post("/{ref}/messages", status_code=201)
async def ask(ref: str, body: AskIn, jobs: BackgroundTasks, user: User = Depends(require("sessions:chat")),
              c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """Ask, and let it think in the background. Your question is stored before the model is ever called."""
    doc = need(c.store.one("chats", ref), ref)
    if doc["status"] == "thinking":
        raise HTTPException(409, f"{ref} is still answering. Wait for it, or stop it first.")
    try:
        message = chat.ask(c, doc, body.text, user.name)
    except chat.Refused as e:
        raise HTTPException(400, str(e)) from e
    jobs.add_task(chat.think, c, ref, user.name)
    return {"message": message, "session": doc}


@router.post("/{ref}/cancel")
async def stop(ref: str, user: User = Depends(require("sessions:chat")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    doc = need(c.store.one("chats", ref), ref)
    chat.cancel(c, ref)
    return doc
