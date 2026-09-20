"""The AI features over HTTP: ask memory, brainstorm an idea, pull facts out of pasted text.

Every answer names the model that wrote it. Asking and extracting answer with no API key configured:
the offline rules write the answer and say so — `provider` is `rules` and the model is named. A
brainstorm has no honest offline version, so with no lane it is refused: 409 with the words that say
how to add a key, or 502 with the provider's own reason when the lanes that tried all failed.

What the routes no longer do is think. Which facts a question is answered from, whether a project
exists, and how a brief becomes a row all live in the service, where they can be read without a web
request in the way.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..repositories.base import MAX_LIMIT
from ..repositories.platform import BrainstormRepository
from ..schemas.ai import brainstorm_json
from ..services.ai_features import AiFeatureService
from ..services.identity import Person
from .deps import current_person, gateway, require, session, unseen_by

router = APIRouter(prefix="/ai")


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    projectId: str | None = Field(default=None, max_length=60)


class IdeaIn(BaseModel):
    idea: str = Field(min_length=3, max_length=2000)
    projectId: str | None = Field(default=None, max_length=60)


class TextIn(BaseModel):
    text: str = Field(min_length=10, max_length=20_000)
    projectId: str | None = Field(default=None, max_length=60)


@router.post("/ask")
async def ask(body: AskIn, who: Person = Depends(require("ai:use")),
              open_session: AsyncSession = Depends(session),
              gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """What memory holds on a question, in a few sentences, citing the facts it leaned on."""
    return await AiFeatureService(open_session, gw).ask(body.question.strip(), body.projectId,
                                                        by=who.name, by_id=who.id)


@router.get("/brainstorms")
async def brainstorms(project: str | None = None, limit: int | None = None, offset: int = 0,
                      who: Person = Depends(current_person),
                      open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The Brainstorm screen holds all of these at once, so the default is the ceiling rather than the
    usual hundred — a hundred would have been a silent truncation the caller could not even see.

    An idea argued out about a project this person may not see is not among them; one filed against
    no project is the workspace's and is."""
    page = await BrainstormRepository(open_session).newest(project, limit=limit or MAX_LIMIT,
                                                           offset=offset,
                                                           hidden=await unseen_by(who, open_session))
    return [brainstorm_json(b) for b in page.items]


@router.post("/brainstorm", status_code=201)
async def brainstorm(body: IdeaIn, who: Person = Depends(require("ai:use")),
                     open_session: AsyncSession = Depends(session),
                     gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """An idea comes back as a brief that argues against itself, and is kept. Needs a model."""
    made = await AiFeatureService(open_session, gw).brainstorm(body.idea.strip(), body.projectId,
                                                               by=who.name, by_id=who.id)
    return brainstorm_json(made)


@router.post("/extract")
async def extract(body: TextIn, who: Person = Depends(require("ai:use")),
                  open_session: AsyncSession = Depends(session),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Candidate facts out of notes, requirements or a chat. Memory itself is unchanged until someone
    keeps them — this route proposes, `POST /memory/facts` remembers."""
    return await AiFeatureService(open_session, gw).extract(body.text, body.projectId, by_id=who.id)
