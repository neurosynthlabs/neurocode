"""Administration of the workspace itself: the database under it, and the models above it.

Everything here needs `workspace:admin`, and everything that changes something is written to the
audit log with who did it and from where — a key that was set, a lane that was switched off, a reset
that threw the workspace's work away.

Two things are not the request's to do. The database chores are not SQL a transaction can hold — a
backup is another program, a vacuum cannot live inside a transaction — so they belong to
`services.maintenance`. The model lanes are blocking by nature: the gateway waits on providers and
keeps its settings in a pool of its own, so every call into it goes to a worker thread. A key never
comes back out of any of it; a route says whether one is set and shows its last four characters.

People, roles and teams are administration too, and they are next door in `routes_admin`.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import lanes
from ..ai.gateway import PREFERENCES, Gateway
from ..data.engine import Database
from ..repositories.identity import AuditRepository
from ..secrets import Secrets
from ..services.errors import Refused
from ..services.identity import Person
from ..services.maintenance import MaintenanceService, NoBackupTool
from ..settings import Settings
from .deps import database, gateway, require, session

router = APIRouter(prefix="/admin")


class AiPatch(BaseModel):
    preference: str | None = None
    deepseekKey: str | None = Field(default=None, max_length=300)   # an empty string removes the key
    deepseekModel: str | None = Field(default=None, max_length=80)
    deepseekUrl: str | None = Field(default=None, max_length=300)
    ollamaUrl: str | None = Field(default=None, max_length=300)
    ollamaModel: str | None = Field(default=None, max_length=80)
    # Any lane, by id: what the rest of these fields are about.
    lane: str | None = Field(default=None, max_length=40)
    key: str | None = Field(default=None, max_length=300)           # an empty string removes it
    model: str | None = Field(default=None, max_length=120)
    baseUrl: str | None = Field(default=None, max_length=300)
    rpm: int | None = Field(default=None, ge=0, le=10_000)
    rpd: int | None = Field(default=None, ge=0, le=1_000_000)
    enabled: bool | None = None
    #: feature → off | low | high | max: how hard that feature asks a model to think. Only the features
    #: named change; "default" puts one back to the default for its kind of work.
    thinking: dict[str, str] | None = Field(default=None, max_length=20)


class AiTest(BaseModel):
    provider: str = Field(max_length=40)     # a lane id, or "rules"


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def config(request: Request) -> Settings:
    """Which database these chores are of, and where its copies go. Held by the app, so a test points
    it at a database of its own without any of this knowing."""
    return request.app.state.settings


def maintenance(open_session: AsyncSession = Depends(session),
                cfg: Settings = Depends(config)) -> MaintenanceService:
    return MaintenanceService(open_session, cfg)


# ── the database ─────────────────────────────────────────────────
@router.get("/database", dependencies=[Depends(require("workspace:admin"))])
async def database_report(chores: MaintenanceService = Depends(maintenance)) -> dict[str, Any]:
    """How big it is, what is in it, how its indexes are, and when it was last copied."""
    return await chores.stats()


@router.post("/database/backup", status_code=201)
async def backup_database(request: Request, who: Person = Depends(require("workspace:admin")),
                          chores: MaintenanceService = Depends(maintenance),
                          open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    made = await chores.backup("manual")
    await AuditRepository(open_session).record(action="database.backup", user_id=who.id,
                                               target=made["name"], detail={"bytes": made["bytes"]},
                                               ip=_ip(request))
    return made


@router.post("/database/check", dependencies=[Depends(require("workspace:admin"))])
async def check_database(chores: MaintenanceService = Depends(maintenance)) -> dict[str, Any]:
    return await chores.check()


@router.post("/database/optimize")
async def optimize_database(request: Request, who: Person = Depends(require("workspace:admin")),
                            chores: MaintenanceService = Depends(maintenance),
                            db: Database = Depends(database),
                            open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Give back what dead rows hold, refresh the statistics, rebuild the indexes.

    The request's own transaction is closed first, and deliberately: REINDEX waits for every open
    transaction in the database to finish, and this request — which has done nothing but read who is
    asking — was one of them. Left open it blocked the chore on itself, every time, and the screen
    still said it had worked. The audit line is written afterwards, in a transaction of its own.
    """
    await open_session.rollback()
    done = await chores.optimize(db.engine)
    async with db.session() as writing:
        await AuditRepository(writing).record(
            action="database.optimize", user_id=who.id, target="database",
            detail={"beforeBytes": done["beforeBytes"], "afterBytes": done["afterBytes"]},
            ip=_ip(request))
    return done


# ── how long history is kept ─────────────────────────────────────
@router.get("/database/retention", dependencies=[Depends(require("workspace:admin"))])
async def retention(chores: MaintenanceService = Depends(maintenance)) -> dict[str, Any]:
    """Each history table, how long it is kept, and exactly how many rows are past that.

    Read before the button is pressed, so what a person agrees to is a number somebody counted rather
    than "old rows". Counting is a statement per table; the screen asks for it when it is opened.
    """
    tables = await chores.retention()
    return {"tables": tables, "rows": sum(int(t["rows"]) for t in tables)}


@router.post("/database/prune")
async def prune_database(request: Request, who: Person = Depends(require("workspace:admin")),
                         chores: MaintenanceService = Depends(maintenance),
                         db: Database = Depends(database),
                         open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Remove the history that is past its keeping, a few thousand rows to a statement.

    The request's own transaction is let go first, as the vacuum's is: the deleting happens on a
    connection of its own, committing as it goes, so nothing about it is held open by a browser that
    walked away. The audit line follows in a transaction of its own, whatever went.
    """
    await open_session.rollback()
    done = await chores.prune(db.engine)
    async with db.session() as writing:
        await AuditRepository(writing).record(
            action="database.prune", user_id=who.id, target="history",
            detail={"removed": done["removed"],
                    "tables": {t["table"]: t["removed"] for t in done["tables"] if t["removed"]}},
            ip=_ip(request))
    return done


# ── emptying the workspace ───────────────────────────────────────
@router.post("/reset")
async def reset(request: Request, x_confirm: str | None = Header(default=None),
                who: Person = Depends(require("workspace:admin")),
                chores: MaintenanceService = Depends(maintenance),
                open_session: AsyncSession = Depends(session),
                gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Empties the workspace: every project and everything done in it, memory, activity, gates,
    decisions, screen preferences, brainstorms, MCP servers, workflows and eval suites, and each
    project's own settings (its standing answer to running its tests). People, roles, teams, the agent
    roster, keys, the workspace's settings, the usage ledger and the audit log are kept. Every open tab
    hears one `reset` event once it has committed."""
    if x_confirm != "reset":
        raise Refused("send header X-Confirm: reset — this empties the workspace (projects, work, memory, "
                      "activity); people, roles, teams, keys and the audit log are kept", status=400)
    # Emptying is undoable only where a copy can be taken. Where pg_dump is missing there is none, and
    # the audit line says so — rather than the reset quietly implying an undo that does not exist.
    saved, skipped = None, ""
    try:
        saved = await chores.backup("before reset")
    except NoBackupTool as no_tool:
        # Only "there is no pg_dump here" is a reason to go ahead without a copy. A backup that was
        # attempted and failed — bad credentials, a full disk, a client older than the server — is
        # something to fix, and destroying the workspace anyway is the worst possible response to it.
        skipped = str(no_tool)
    counts = await chores.empty()
    await AuditRepository(open_session).record(
        action="data.reset", user_id=who.id, target="workspace emptied",
        detail={"backup": saved["name"] if saved else None,
                **({"backupSkipped": skipped} if skipped else {})}, ip=_ip(request))
    return {"ok": True, "emptied": True, "backup": saved["name"] if saved else None, "counts": counts,
            "compiler": await asyncio.to_thread(gw.status)}


# ── the model lanes ──────────────────────────────────────────────
def _report(gw: Gateway) -> dict[str, Any]:
    """The whole AI providers screen in one answer. Blocking: it reads the ledger and asks a local
    Ollama whether it is up, so it is only ever called from a worker thread."""
    ds, ol = gw.deepseek(), gw.ollama()
    return {
        "preference": gw.preference(),
        # What the gateway itself obeys: an environment that pins the compiler wins over the setting.
        "preferenceLocked": bool(os.environ.get("NEUROCODE_COMPILER")),
        "active": gw.status(),
        "deepseek": {"hasKey": bool(ds["key"]), "keyMask": Secrets.mask(ds["key"]),
                     "keySource": gw.key_source(), "model": ds["model"], "baseUrl": ds["baseUrl"],
                     "rejected": gw.rejected(),
                     "retired": lanes.RETIRED.get("deepseek", {}).get(ds["model"])},
        "ollama": {"url": ol["url"], "model": ol["model"], "ready": gw.ollama_ready()},
        "lanes": gw.report(),
    }


def _lane(gw: Gateway, body: AiPatch) -> dict[str, Any]:
    """Change one lane: its model, its base URL, the limits it holds itself to, its key, or whether it
    is used at all. The key goes to the secrets file and is never returned, only reported as set."""
    lane = lanes.BY_ID.get(body.lane or "")
    if lane is None:
        raise Refused(f"there is no lane called {body.lane}", status=404)
    changed: dict[str, Any] = {}
    saved = dict(gw.store.setting(f"ai.lane.{lane.id}", {}) or {})
    for field, written in (("model", body.model), ("baseUrl", body.baseUrl)):
        if written is not None:
            saved[field] = written.strip()
            changed[f"{lane.id}.{field}"] = written.strip()
    for field, number in (("rpm", body.rpm), ("rpd", body.rpd)):
        if number is not None:
            saved[field] = number
            changed[f"{lane.id}.{field}"] = number
    if body.enabled is not None:
        saved["enabled"] = body.enabled
        changed[f"{lane.id}.enabled"] = body.enabled
    if saved:
        gw.store.save_setting(f"ai.lane.{lane.id}", saved)
    if body.key is not None:
        if not lane.needs_key:
            raise Refused(f"{lane.label} needs no key.", status=400)
        gw.secrets.set(lane.secret, body.key.strip() or None)
        gw.forget_rejection(lane.id)
        changed[f"{lane.id}.key"] = "set" if body.key.strip() else "removed"   # never the key itself
    return changed


def _thinking(gw: Gateway, levels: dict[str, str]) -> dict[str, Any]:
    """Save how hard each named feature thinks. Refused whole when any name or level is not one the
    gateway knows, so a typo never half-applies."""
    for feature, level in levels.items():
        if feature not in lanes.THINKING_FEATURES:
            raise Refused(f"there is no feature called {feature}; the features are: "
                          f"{', '.join(lanes.THINKING_FEATURES)}", status=400)
        if level not in (*lanes.LEVELS, "default"):
            raise Refused(f"thinking must be one of: {', '.join(lanes.LEVELS)}, or default", status=400)
    saved = dict(gw.store.setting("ai.thinking", {}) or {})
    for feature, level in levels.items():
        if level == "default":
            saved.pop(feature, None)
        else:
            saved[feature] = level
    gw.store.save_setting("ai.thinking", saved)
    return {f"thinking.{feature}": level for feature, level in levels.items()}


def _apply(gw: Gateway, body: AiPatch) -> dict[str, Any]:
    """Every change in one hop to the worker thread: the ledger and the secrets file both block.
    Returns what changed, in words fit for the audit log — and with no key among them."""
    changed: dict[str, Any] = {}
    if body.preference is not None:
        if body.preference not in PREFERENCES:
            raise Refused(f"routing must be one of: {', '.join(PREFERENCES)}", status=400)
        gw.store.save_setting("ai.preference", body.preference)
        changed["preference"] = body.preference
    if body.lane is not None:
        changed.update(_lane(gw, body))
    if body.thinking is not None:
        changed.update(_thinking(gw, body.thinking))
    # DeepSeek and Ollama were configured before lanes existed, and the screen still sends them under
    # their own names. They are written back to the two keys the gateway still reads them from.
    for key, fields in (("ai.deepseek", (("deepseekModel", "model"), ("deepseekUrl", "baseUrl"))),
                        ("ai.ollama", (("ollamaModel", "model"), ("ollamaUrl", "url")))):
        saved = dict(gw.store.setting(key, {}) or {})
        touched = False
        for field, name in fields:
            if (value := getattr(body, field)) is not None:
                saved[name] = value.strip()
                changed[field] = value.strip()
                touched = True
        if touched:
            gw.store.save_setting(key, saved)
    if body.deepseekKey is not None:
        gw.secrets.set("deepseek_api_key", body.deepseekKey.strip() or None)
        gw.forget_rejection()
        changed["deepseekKey"] = "set" if body.deepseekKey.strip() else "removed"  # never the key itself
    return changed


@router.get("/ai", dependencies=[Depends(require("workspace:admin"))])
async def ai(gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await asyncio.to_thread(_report, gw)


@router.put("/ai")
async def update_ai(body: AiPatch, request: Request, who: Person = Depends(require("workspace:admin")),
                    open_session: AsyncSession = Depends(session),
                    gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    changed = await asyncio.to_thread(_apply, gw, body)
    await AuditRepository(open_session).record(action="ai.update", user_id=who.id, target="AI providers",
                                               detail=changed, ip=_ip(request))
    return await asyncio.to_thread(_report, gw)


@router.post("/ai/test")
async def test_ai(body: AiTest, who: Person = Depends(require("workspace:admin")),
                  gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """One real round trip down one lane, so the screen can say whether it answers — not whether it
    is configured. Every probe is ledgered like any other call."""
    return await asyncio.to_thread(gw.test, body.provider, who.id)
