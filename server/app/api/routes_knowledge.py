"""Memory over HTTP: facts, search, pins, archives, and the conflicts between facts.

Search is now Postgres full text over a column the database generates from the fact itself, ranked
with pinned facts first. The route did not change; what it asks did.

Taste lives here too, beside the facts: the signals a person's decisions left, the rules a model proposed
from them, and a person's word on each rule. Reading is for anyone signed in; learning and changing a rule
need memory:write, because an adopted rule is handed to every model that plans, writes or reviews.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import NotFound
from ..repositories.knowledge import HITS_CEILING, ConflictRepository, MemoryHitRepository, MemoryRepository
from ..schemas import conflict_json, fact_json
from ..schemas.knowledge import recall_json
from ..ai.gateway import Gateway
from ..schemas.work import when
from ..services.identity import Person
from ..services.knowledge import MemoryService, NewFact
from ..services.taste import CEILING as TASTE_CEILING
from ..services.taste import MAX_RULE, TasteService, rule_json, signal_json
from .deps import current_person, gateway, must_see, require, session, unseen_by

router = APIRouter()
#: The window the screen's "retired" figure covers.
RETIRED_DAYS = 30
Category = Literal["human", "project", "architecture", "business_rules", "legacy", "database", "bugs",
                   "decisions", "incidents", "preferences", "code"]


class PinIn(BaseModel):
    pinned: bool


class ResolveIn(BaseModel):
    keep: Literal["a", "b"]


class ConflictIn(BaseModel):
    a: str = Field(min_length=1, max_length=40)
    b: str = Field(min_length=1, max_length=40)
    topic: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=2000)
    severity: Literal["low", "medium", "high"] = "medium"


class FactIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    category: Category = "project"
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    reason: str = Field(default="", max_length=500)


class FactsIn(BaseModel):
    #: None — or "global", as the screens have always spelled it — files the facts under the workspace.
    projectId: str | None = Field(default=None, max_length=60)
    facts: list[FactIn] = Field(min_length=1, max_length=20)


@router.get("/memory")
async def memory(q: str = "", category: Category | None = None, project: str | None = None,
                 include_archived: bool = False, who: Person = Depends(current_person),
                 open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """What memory holds, of the projects this person may see. The workspace's own facts are
    everybody's; a fact filed under a restricted project is not searchable outside it."""
    found = await MemoryService(open_session).search(q, category=category, project=project,
                                                     include_archived=include_archived,
                                                     hidden=await unseen_by(who, open_session))
    return [fact_json(f) for f in found]


@router.get("/memory/stats")
async def memory_stats(project: str | None = Query(default=None, max_length=60),
                       who: Person = Depends(current_person),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The Memory screen's figures, counted by the database. They were counted from the list, which is
    a page of at most a few hundred facts, so a large memory read as a small one.

    Counted over the same facts the list beside them can show, or the header says a number the screen
    cannot reach — and the difference is the size of a project nobody told this person about."""
    found = await MemoryRepository(open_session).stats(project=project, retired_days=RETIRED_DAYS,
                                                       hidden=await unseen_by(who, open_session))
    return {"held": found.held, "pinned": found.pinned, "global": found.workspace,
            "recalled24h": found.recalled_24h, "retired": found.retired, "retiredDays": RETIRED_DAYS,
            "byCategory": {category: {"held": c.held, "pinned": c.pinned, "recalled24h": c.recalled_24h,
                                      "lastUsedAt": when(c.last_used_at)}
                           for category, c in found.by_category.items()}}


@router.post("/memory/facts", status_code=201)
async def add_facts(body: FactsIn, who: Person = Depends(require("memory:write")),
                    open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    added = await MemoryService(open_session).add(
        [NewFact(title=f.title, body=f.body, category=f.category, confidence=f.confidence, reason=f.reason)
         for f in body.facts],
        project_id=body.projectId, by=who.name, source=f"Added from text by {who.name}")
    return [fact_json(f) for f in added]


async def _fact(ref: str, who: Person, open_session: AsyncSession) -> None:
    """A fact reached by its reference: one filed under a project this person may not see is not
    there, so pinning or archiving it answers 404 rather than telling them it exists."""
    found = await MemoryRepository(open_session).by_ref(ref)
    if found is None:
        raise NotFound(f"fact {ref}")
    await must_see(who, open_session, found.project_id, f"fact {ref}")


@router.post("/memory/{ref}/pin")
async def pin(ref: str, body: PinIn, who: Person = Depends(require("memory:write")),
              open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    await _fact(ref, who, open_session)
    return fact_json(await MemoryService(open_session).pin(ref, body.pinned, who.name))


@router.post("/memory/{ref}/archive")
async def archive(ref: str, who: Person = Depends(require("memory:write")),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Archived, never deleted — a fact that turned out to be wrong is still evidence."""
    await _fact(ref, who, open_session)
    return fact_json(await MemoryService(open_session).archive(ref, who.name))


@router.get("/memory/hits")
async def hits(limit: int = Query(default=50, ge=1, le=HITS_CEILING), who: Person = Depends(current_person),
               open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The latest recalls, newest first: which fact, which feature used it, for what, and when — of
    the facts this person may read, since a recall quotes the fact's title."""
    return [recall_json(h) for h in await MemoryHitRepository(open_session).recent(
        limit, hidden=await unseen_by(who, open_session))]


@router.get("/memory/conflicts", dependencies=[Depends(current_person)])
async def conflicts(open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    page = await ConflictRepository(open_session).open()
    return [conflict_json(c) for c in page.items]


@router.post("/memory/conflicts", status_code=201)
async def file_conflict(body: ConflictIn, who: Person = Depends(require("memory:write")),
                        open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Nothing detects a contradiction on its own; a person who notices one files it here."""
    made = await MemoryService(open_session).conflict(body.a, body.b, topic=body.topic, detail=body.detail,
                                                      severity=body.severity.upper(), by=who.name)
    return conflict_json(made)


@router.post("/memory/conflicts/{cid}/resolve")
async def resolve(cid: str, body: ResolveIn, who: Person = Depends(require("memory:write")),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await MemoryService(open_session).resolve(cid, body.keep, who.name)


# ── taste ────────────────────────────────────────────────────────
TasteKind = Literal["accept", "refuse", "rework_note", "edit_delta", "plan_edit"]


def _scope(project: str | None) -> str | None:
    """None, or "workspace" / "global" as the screens spell it, is the workspace's own view."""
    return None if project in (None, "", "workspace", "global") else project


class LearnIn(BaseModel):
    #: The project whose signals are read, and whose rules they become; left out, the whole workspace's.
    projectId: str | None = Field(default=None, max_length=60)


class RuleChange(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_RULE * 2)
    #: active adopts (or switches on); retired rejects (or switches off).
    status: Literal["active", "retired"] | None = None


@router.get("/taste/rules")
async def taste_rules(project: str | None = Query(default=None, max_length=60),
                      status: Literal["proposed", "active", "retired"] | None = None,
                      limit: int = Query(default=100, ge=1, le=TASTE_CEILING), offset: int = Query(default=0, ge=0),
                      who: Person = Depends(current_person),
                      open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The rules in view — a project's own and the workspace's, or the workspace's alone — proposed first,
    then active, then retired, the best supported first; with the counts by status and the signals'
    counts, so the tab can say how much is waiting to be read."""
    taste = TasteService(open_session)
    scope = _scope(project)
    rules, counts = await taste.rules(scope, status=status, limit=limit, offset=offset)
    names = await taste.names([r.adopted_by for r in rules])
    return {"items": [rule_json(r, names) for r in rules], "counts": counts,
            "signals": await taste.signal_counts(scope, await unseen_by(who, open_session))}


@router.get("/taste/signals")
async def taste_signals(project: str | None = Query(default=None, max_length=60), kind: TasteKind | None = None,
                        unread: bool | None = None,
                        limit: int = Query(default=50, ge=1, le=TASTE_CEILING), offset: int = Query(default=0, ge=0),
                        who: Person = Depends(current_person),
                        open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """The signals, newest first: what each moment was, in a line, with what it was built from — of
    the projects this person may see. A signal quotes a rework note or a plan edit verbatim."""
    taste = TasteService(open_session)
    found = await taste.signals(_scope(project), kind=kind, unread=unread, limit=limit, offset=offset,
                                hidden=await unseen_by(who, open_session))
    names = await taste.names([s.by_user_id for s in found])
    return [signal_json(s, names) for s in found]


@router.get("/taste/rules/{rule_id}/evidence", dependencies=[Depends(current_person)])
async def taste_evidence(rule_id: int, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The signals a rule cites, each marked for or against it."""
    taste = TasteService(open_session)
    rule, cited = await taste.evidence(rule_id)
    names = await taste.names([rule.adopted_by, *(s.by_user_id for s, _ in cited)])
    return {"rule": rule_json(rule, names), "signals": [signal_json(s, names, stance=st) for s, st in cited]}


@router.post("/taste/learn")
async def taste_learn(body: LearnIn | None = None, who: Person = Depends(require("memory:write")),
                      open_session: AsyncSession = Depends(session),
                      gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """"Learn from my decisions": gather the moments not yet kept as signals, hand the unread signals to a
    model, and keep what it proposes as rules waiting for a person. 409 when there is nothing new or no
    model; 502 when every lane failed. Nothing proposed is used until it is adopted."""
    taste = TasteService(open_session, gw)
    done = await taste.learn(_scope(body.projectId if body else None), by=who.name, by_id=who.id)
    return {"read": done["read"], "harvested": done["harvested"], "model": done["model"],
            "proposed": [rule_json(r) for r in done["proposed"]],
            "weighed": [rule_json(r) for r in done["weighed"]]}


@router.patch("/taste/rules/{rule_id}")
async def taste_change(rule_id: int, body: RuleChange, who: Person = Depends(require("memory:write")),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Adopt, reject, switch on or off, or reword a rule. Written to the activity log."""
    taste = TasteService(open_session)
    rule = await taste.change(rule_id, text=body.text, status=body.status, by=who.name, by_id=who.id)
    return rule_json(rule, await taste.names([rule.adopted_by]))
