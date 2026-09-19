"""Custom agents — the workspace's and a project's own, and those a repository declares in
`.neurocode/agents/*.md` or `.claude/agents/*.md` — and reviews of any diff on demand.

Reading needs a session. Writing an agent needs `agents:manage`, and nothing runs when one is written:
an agent is instructions a plan step or a session is later given. Trying one ("dry run") asks a model a
question, so it needs what asking a session does, `sessions:chat`.

A review on demand needs `runs:run` — it is the review a run's own step does, asked for directly — and
reads in the background: the request answers 202 with the review as running, and the finished review
arrives on the stream (`review`) and in the activity feed. Sending its findings to a session needs
`sessions:chat`; making a plan of them needs `plans:compile`, as compiling any requirement does.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..repositories import ProjectRepository
from ..schemas import chat_json, plan_json, task_json
from ..services import chat as chat_service
from ..services import reviews as review_service
from ..services.custom_agents import MAX_AGENT_STEPS, MAX_NAME, MAX_PROMPT, MAX_ROLE, MAX_TRY, TOOL_NAMES
from ..services.custom_agents import CustomAgentService, Draft
from ..services.identity import Person
from ..services.reviews import ReviewService
from .deps import current_person, database, gateway, hand_off, require, session

router = APIRouter(tags=["agents"])


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    role: str = Field(default="", max_length=MAX_ROLE)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    lane: str | None = Field(default=None, max_length=40)
    tools: list[str] = Field(default_factory=list, max_length=len(TOOL_NAMES))
    maxSteps: int = Field(default=8, ge=1, le=MAX_AGENT_STEPS)
    mode: Literal["primary", "subagent"] = "subagent"
    #: Null: the whole workspace's. Ignored on a change — an agent keeps the scope it was written in.
    projectId: str | None = Field(default=None, max_length=40)

    def draft(self) -> Draft:
        return Draft(name=self.name, role=self.role, prompt=self.prompt, lane=self.lane or None,
                     tools=list(self.tools), max_steps=self.maxSteps, mode=self.mode, project_id=self.projectId)


class TryIn(BaseModel):
    #: The agent's key, as GET /agents/custom lists it: `custom:<id>`, `file:<name>` or a roster agent's id.
    agent: str = Field(min_length=1, max_length=120)
    projectId: str | None = Field(default=None, max_length=40)
    question: str = Field(min_length=1, max_length=MAX_TRY)


class ReviewIn(BaseModel):
    target: Literal["branch", "working-tree", "commit-range"]
    #: What `head` is compared with. A branch review left without one compares with the checkout's branch.
    base: str = Field(default="", max_length=200)
    head: str = Field(default="", max_length=200)
    #: A further source's label; empty for the project's first.
    source: str = Field(default="", max_length=60)
    #: Fetch `head` from its remote first (`origin/feature`): a branch someone else pushed.
    fetch: bool = False


# ── custom agents ────────────────────────────────────────────────
@router.get("/agents/custom", dependencies=[Depends(current_person)])
async def custom_agents(project: str | None = Query(default=None, max_length=40),
                        open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Every agent beside the roster for one project (or the workspace alone): the stored ones, the files
    its checkout declares, with where each came from, what it may use, which lose their name to a nearer
    one, and what they have really done."""
    return await CustomAgentService(open_session).catalogue(project)


@router.post("/agents/custom", status_code=201)
async def create_agent(body: AgentIn, request: Request, who: Person = Depends(require("agents:manage")),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await CustomAgentService(open_session).create(body.draft(), who, ip=_ip(request))


@router.patch("/agents/custom/{agent_id}")
async def update_agent(agent_id: str, body: AgentIn, request: Request,
                       who: Person = Depends(require("agents:manage")),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await CustomAgentService(open_session).update(agent_id, body.draft(), who, ip=_ip(request))


@router.delete("/agents/custom/{agent_id}")
async def delete_agent(agent_id: str, request: Request, who: Person = Depends(require("agents:manage")),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await CustomAgentService(open_session).delete(agent_id, who, ip=_ip(request))


@router.post("/agents/try")
async def try_agent(body: TryIn, who: Person = Depends(require("sessions:chat")),
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A dry run: the agent answers one question with no tools, on the lane it prefers. Asked in the
    request, like Ask memory — the person is waiting on the answer. 409 with no model, 502 when every
    lane failed."""
    return await CustomAgentService(open_session, gw).dry_run(body.agent, body.projectId, body.question, who)


# ── reviews on demand ────────────────────────────────────────────
@router.get("/projects/{pid}/review/targets", dependencies=[Depends(current_person)])
async def review_targets(pid: str, source: str = Query(default="", max_length=60),
                         open_session: AsyncSession = Depends(session),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """What can be reviewed in one source: its branches, the one checked out, the working tree's changed
    files, its remotes and the REVIEW.md a reviewer would be handed."""
    return await ReviewService(open_session, gw).targets(pid, source)


@router.post("/projects/{pid}/review", status_code=202)
async def request_review(pid: str, body: ReviewIn, jobs: BackgroundTasks,
                         who: Person = Depends(require("runs:run")),
                         open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Ask for a review of a branch, the working tree or a range of commits. It is read in the background."""
    service = ReviewService(open_session, gw)
    review, new = await service.request(pid, target=body.target, base=body.base, head=body.head,
                                        source=body.source, fetch=body.fetch, who=who)
    out = await service.one(review.ref)
    if new:
        await hand_off(open_session, jobs, review_service.perform, db, gw, review.ref)
    return out


@router.get("/projects/{pid}/reviews", dependencies=[Depends(current_person)])
async def reviews(pid: str, limit: int | None = Query(default=None, ge=0), offset: int = Query(default=0, ge=0),
                  open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """The project's reviews on demand, newest first, paged."""
    return await ReviewService(open_session, gw).listed(pid, limit=limit, offset=offset)


@router.get("/reviews/{ref}", dependencies=[Depends(current_person)])
async def review(ref: str, open_session: AsyncSession = Depends(session),
                 gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await ReviewService(open_session, gw).one(ref)


async def _answer(db: Database, gw: Gateway, ref: str, who: Person) -> None:
    await chat_service.think(db, gw, ref, who.name, who)


@router.post("/reviews/{ref}/session", status_code=201)
async def review_to_session(ref: str, jobs: BackgroundTasks, who: Person = Depends(require("sessions:chat")),
                            open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                            gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """"Send to a session": a new session on the project, asked to go through the findings against the code.
    Its question is kept now; the answer arrives on the stream."""
    chat = await ReviewService(open_session, gw).to_session(ref, who)
    project = await ProjectRepository(open_session).get(chat.project_id)
    out = chat_json(chat, project_name=project.name if project else chat.project_id)
    await hand_off(open_session, jobs, _answer, db, gw, chat.ref, who)
    return out


@router.post("/reviews/{ref}/plan", status_code=201)
async def review_to_plan(ref: str, who: Person = Depends(require("plans:compile")),
                         open_session: AsyncSession = Depends(session),
                         gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """"Make a plan from these findings": compiled like any requirement — 409 with no model, 502 when every
    lane failed — and waiting in Plans for a person before anything runs."""
    plan, task = await ReviewService(open_session, gw).to_plan(ref, who)
    return {**plan_json(plan, task_ref=task.ref), "task": task_json(task)}
