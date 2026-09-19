"""The Blueprint wizard: from a new idea to a designed, scaffolded system — the technology catalogue, the
template bank (shipped and a person's own), blueprints with their answers and architecture, and scaffolding
one into a project.

Reading needs a session. Designing — creating, editing, asking for a review, finalizing, importing and
saving templates — needs `plans:compile`, because a blueprint is the start of work the compiler will plan.
Scaffolding makes folders on this machine and onboards a project, so it also needs `projects:onboard` and
the machine door (`machine:access`, with machine access on).
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..models import Blueprint, BlueprintTemplate, Project
from ..schemas.blueprints import blueprint_json, blueprint_row, custom_template_json, template_json
from ..schemas.work import plan_json, project_json
from ..services.blueprints import (
    CATALOGUE,
    LAYERS,
    MAX_PAGE,
    QUESTIONS,
    BlueprintService,
    check_answers,
    fit,
    rank,
    template_summary,
)
from ..services.identity import Person
from ..services.onboarding import Spec, onboard, onboard_source
from .deps import current_person, database, gateway, hand_off, require, session
from .routes_machine import MAX_PATH, operator

router = APIRouter(prefix="/blueprints", tags=["blueprints"])

DESIGN = "plans:compile"


class Answers(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class BlueprintIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    templateId: str | None = Field(default=None, max_length=80)
    answers: dict[str, Any] = Field(default_factory=dict)


class BlueprintPatch(BaseModel):
    #: The revision the editor was showing. A save against an older one is refused, not merged.
    expectRevision: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=160)
    answers: dict[str, Any] | None = None
    spec: dict[str, Any] | None = None


class Revision(BaseModel):
    expectRevision: int | None = Field(default=None, ge=1)


class Decided(BaseModel):
    expectRevision: int = Field(ge=1)
    #: The accepted changes as the review listed them: `{id, path, from, to}`.
    changes: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    #: The ids of the changes rejected, so the review shows what was decided.
    rejected: list[str] = Field(default_factory=list, max_length=40)


class ImportIn(BaseModel):
    text: str = Field(min_length=1)
    #: What the file should become; left out, the file's own format decides.
    kind: Literal["blueprint", "template"] | None = None


class TemplateIn(BaseModel):
    blueprintId: str = Field(min_length=1, max_length=40)
    name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=2000)


class ScaffoldIn(BaseModel):
    #: An empty folder under the machine roots — the Workbench's folder picker chooses it.
    folder: str = Field(min_length=1, max_length=MAX_PATH)
    #: The scaffold repositories to make, by label; all of them when left out.
    repos: list[str] | None = Field(default=None, max_length=6)


async def _context(open_session: AsyncSession, bps: list[Blueprint],
                   service: BlueprintService) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Who made each blueprint, the name of the template it started from, and its project's name."""
    names = await service.names([b.created_by for b in bps])
    custom = [b.template for b in bps if b.template and b.template not in CATALOGUE.templates]
    templates = {tid: t["name"] for tid, t in CATALOGUE.templates.items()}
    if custom:
        rows = await open_session.execute(select(BlueprintTemplate.id, BlueprintTemplate.name)
                                          .where(BlueprintTemplate.id.in_(custom)))
        templates.update({tid: name for tid, name in rows.all()})
    pids = [b.project_id for b in bps if b.project_id]
    projects: dict[str, str] = {}
    if pids:
        rows = await open_session.execute(select(Project.id, Project.name).where(Project.id.in_(pids)))
        projects = {pid: name for pid, name in rows.all()}
    return names, templates, projects


async def _one(open_session: AsyncSession, service: BlueprintService, bp: Blueprint) -> dict[str, Any]:
    names, templates, projects = await _context(open_session, [bp], service)
    return blueprint_json(bp, names=names, template_name=templates.get(bp.template) if bp.template else None,
                          project_name=projects.get(bp.project_id or ""))


async def _mine(service: BlueprintService, answers: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
    rows, total = await service.mine(limit=MAX_PAGE)
    names = await service.names([r.created_by for r in rows])
    out = []
    for row in rows:
        tpl, _, _ = await service.template(row.id)
        item = custom_template_json(row, tpl, names=names)
        if answers is not None:
            item["fit"] = fit(tpl, answers)
        out.append(item)
    return out, total


# ── the catalogue ────────────────────────────────────────────────
@router.get("/catalogue", dependencies=[Depends(current_person)])
async def catalogue(open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Everything the wizard draws from: the technologies, the template bank (the catalogue's and your
    own), the questions, and the layers."""
    mine, total = await _mine(BlueprintService(open_session))
    return {"tech": list(CATALOGUE.tech),
            "templates": [template_summary(t) for t in CATALOGUE.templates.values()],
            "mine": mine, "mineTotal": total, "questions": QUESTIONS, "layers": LAYERS}


@router.post("/rank", dependencies=[Depends(current_person)])
async def ranked(body: Answers, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The templates ranked by how well they fit these answers, each with the conditions it met."""
    service = BlueprintService(open_session)
    kept = check_answers(body.answers)
    mine, _ = await _mine(service, kept)
    return {"catalogue": rank(kept), "mine": mine}


@router.get("/templates", dependencies=[Depends(current_person)])
async def templates(open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    mine, total = await _mine(BlueprintService(open_session))
    return {"catalogue": [template_summary(t) for t in CATALOGUE.templates.values()], "mine": mine,
            "mineTotal": total}


@router.get("/templates/{template_id}", dependencies=[Depends(current_person)])
async def template(template_id: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    service = BlueprintService(open_session)
    tpl, source, row = await service.template(template_id)
    names = await service.names([row.created_by] if row else [])
    return template_json(tpl, source, row, names=names)


@router.get("/templates/{template_id}/export", dependencies=[Depends(current_person)])
async def export_template(template_id: str, format: Literal["json", "yaml"] = "json",
                          open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    return await BlueprintService(open_session).export_template(template_id, format)


@router.post("/templates", status_code=201)
async def save_template(body: TemplateIn, who: Person = Depends(require(DESIGN)),
                        open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Save a blueprint's architecture as one of your templates."""
    service = BlueprintService(open_session)
    row = await service.save_template(body.blueprintId, body.name, body.description, who=who)
    tpl, source, _ = await service.template(row.id)
    return template_json(tpl, source, row, names={who.id: who.name})


@router.delete("/templates/{template_id}")
async def remove_template(template_id: str, who: Person = Depends(require(DESIGN)),
                          open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    await BlueprintService(open_session).remove_template(template_id, who=who)
    return {"ok": True, "id": template_id}


@router.post("/import", status_code=201)
async def import_(body: ImportIn, who: Person = Depends(require(DESIGN)),
                  open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """A blueprint or template file, JSON or YAML. Answers `{kind, blueprint | template}`."""
    service = BlueprintService(open_session)
    kind, made = await service.import_(body.text, who=who, as_kind=body.kind)
    if kind == "blueprint":
        return {"kind": kind, "blueprint": await _one(open_session, service, made)}
    tpl, source, row = await service.template(made.id)
    return {"kind": kind, "template": template_json(tpl, source, row, names={who.id: who.name})}


# ── blueprints ───────────────────────────────────────────────────
@router.get("", dependencies=[Depends(current_person)])
async def blueprints(limit: int | None = Query(default=None, ge=0, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                     open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    service = BlueprintService(open_session)
    rows, total = await service.page(limit=limit, offset=offset)
    names, templates_by_id, projects = await _context(open_session, rows, service)
    return {"items": [blueprint_row(b, names=names, template_name=templates_by_id.get(b.template) if b.template else None,
                                    project_name=projects.get(b.project_id or "")) for b in rows],
            "total": total, "offset": offset, "limit": min(limit if limit is not None else 100, MAX_PAGE)}


@router.post("", status_code=201)
async def create(body: BlueprintIn, who: Person = Depends(require(DESIGN)),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    service = BlueprintService(open_session)
    bp = await service.create(body.name, body.templateId, body.answers, who=who)
    return await _one(open_session, service, bp)


@router.get("/{blueprint_id}", dependencies=[Depends(current_person)])
async def blueprint(blueprint_id: str, open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    service = BlueprintService(open_session)
    return await _one(open_session, service, await service.get(blueprint_id))


@router.patch("/{blueprint_id}")
async def update(blueprint_id: str, body: BlueprintPatch, who: Person = Depends(require(DESIGN)),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    service = BlueprintService(open_session)
    bp = await service.update(blueprint_id, who=who, expect=body.expectRevision, name=body.name,
                              answers=body.answers, spec=body.spec)
    return await _one(open_session, service, bp)


@router.delete("/{blueprint_id}")
async def remove(blueprint_id: str, who: Person = Depends(require(DESIGN)),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    await BlueprintService(open_session).remove(blueprint_id, who=who)
    return {"ok": True, "id": blueprint_id}


@router.post("/{blueprint_id}/suggest")
async def suggest(blueprint_id: str, body: Revision | None = None, who: Person = Depends(require(DESIGN)),
                  open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A model's review of the architecture against the answers: proposed changes, none applied."""
    service = BlueprintService(open_session, gw)
    bp = await service.suggest(blueprint_id, who=who, expect=body.expectRevision if body else None)
    return await _one(open_session, service, bp)


@router.post("/{blueprint_id}/apply")
async def apply(blueprint_id: str, body: Decided, who: Person = Depends(require(DESIGN)),
                open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    service = BlueprintService(open_session)
    bp = await service.apply(blueprint_id, body.changes, body.rejected, who=who, expect=body.expectRevision)
    return await _one(open_session, service, bp)


@router.post("/{blueprint_id}/finalize")
async def finalize(blueprint_id: str, body: Revision | None = None, who: Person = Depends(require(DESIGN)),
                   open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """The document, the diagram, and the decisions written into memory."""
    service = BlueprintService(open_session)
    bp = await service.finalize(blueprint_id, who=who, expect=body.expectRevision if body else None)
    return await _one(open_session, service, bp)


@router.get("/{blueprint_id}/export", dependencies=[Depends(current_person)])
async def export(blueprint_id: str, format: Literal["json", "yaml"] = "json",
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """`{filename, mime, text}` — the screen turns it into a download."""
    return await BlueprintService(open_session).export(blueprint_id, format)


@router.post("/{blueprint_id}/scaffold", status_code=201, dependencies=[Depends(operator)])
async def scaffold(blueprint_id: str, body: ScaffoldIn, jobs: BackgroundTasks,
                   who: Person = Depends(require(DESIGN, "projects:onboard")),
                   open_session: AsyncSession = Depends(session), db: Database = Depends(database),
                   gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Make the repositories, onboard them as a project, and compile the scaffold plan. The project is read
    after the answer; the plan waits in Plans for a person to dispatch it."""
    service = BlueprintService(open_session, gw)
    made = await service.scaffold(blueprint_id, body.folder, body.repos, who=who)
    out = {"blueprint": await _one(open_session, service, await service.get(blueprint_id)),
           "project": project_json(made.project, sources=[]), "plan": plan_json(made.plan),
           "planRef": made.plan.ref, "taskRef": made.task.ref}
    await hand_off(open_session, jobs, onboard, db, gw, made.project.id,
                   Spec(source="local", repo=str(made.primary)))
    for source in made.sources:
        await hand_off(open_session, jobs, onboard_source, db, gw, made.project.id, source.id)
    return out
