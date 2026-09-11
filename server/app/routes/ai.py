"""AI features: ask memory, brainstorm, extract facts from text. All need `ai:use`, and all work with
no key: the answer says which model made it, or that the offline rules did."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..ai import features
from ..ai.gateway import Result
from ..auth import User, current_user, require
from ..context import Ctx, ctx

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


def _fell_back(c: Ctx, res: Result[Any], project: str | None) -> None:
    if res.fallback:
        c.record("AI fell back", f"{res.fallback}. The {res.provider.model} answered instead.", project=project or "aios",
                 level="warn", actor="NeuroCode", kind="system")


@router.post("/ask")
async def ask(body: AskIn, user: User = Depends(require("ai:use")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    question = body.question.strip()
    facts = c.store.memory(question, project=body.projectId, mode="any")[:6]
    res = await asyncio.to_thread(features.ask, c.gateway, question, facts)
    _fell_back(c, res, body.projectId)
    cited = [f for f in facts if f["ref"] in res.data.citations]
    c.act(user, "Asked memory", f"“{question[:120]}” · {len(cited)} facts cited · {res.provider.model}", project=body.projectId or "aios")
    return {"answer": res.data.answer, "citations": [{"ref": f["ref"], "title": f["title"]} for f in cited], **res.meta()}


@router.get("/brainstorms", dependencies=[Depends(current_user)])
async def brainstorms(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.all("brainstorms")


@router.post("/brainstorm", status_code=201)
async def brainstorm(body: IdeaIn, user: User = Depends(require("ai:use")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    idea = body.idea.strip()
    project = c.store.get("projects", body.projectId) if body.projectId else None
    res = await asyncio.to_thread(features.brainstorm, c.gateway, idea, project)
    _fell_back(c, res, body.projectId)
    n = c.store.count("brainstorms") + 1
    doc = {"id": f"b{n}-{int(datetime.now().timestamp())}", "ref": f"IDEA-{n}", "idea": idea, "projectId": body.projectId,
           "brief": res.data.model_dump(), "compiler": res.meta(), "by": user.name,
           "createdAt": datetime.now().strftime("%d %b %H:%M")}
    c.put("brainstorms", c.store.insert_brainstorm(doc))
    c.act(user, "Brainstormed", f"{doc['ref']} · {res.data.title} · {res.provider.model}", project=body.projectId or "aios", level="ok")
    return doc


@router.post("/extract")
async def extract(body: TextIn, user: User = Depends(require("ai:use")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    project = c.store.get("projects", body.projectId) if body.projectId else None
    res = await asyncio.to_thread(features.extract, c.gateway, body.text, project)
    _fell_back(c, res, body.projectId)
    return {"facts": [f.model_dump() for f in res.data.facts], **res.meta()}
