"""Projects and repository onboarding, and the MCP registry."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import onboarding
from ..auth import User, current_user, require
from ..context import Ctx, ctx
from .code import run_index

router = APIRouter()


class RuleIn(BaseModel):
    id: str = Field(max_length=40)
    label: str = Field(max_length=120)
    note: str = Field(default="", max_length=300)


class ProjectIn(BaseModel):
    source: Literal["git", "local"]
    repo: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", max_length=100)
    excluded: list[str] = Field(default_factory=list, max_length=50)
    connectDb: bool = True
    mineGit: bool = True
    ingestDocs: bool = True
    rules: list[RuleIn] = Field(default_factory=list, max_length=20)


class McpIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    transport: Literal["stdio", "http", "sse"]
    command: str = Field(min_length=1, max_length=1000)
    scope: Literal["project", "global", "local"]
    defaultEffect: Literal["ask", "allow-read", "deny"]
    config: str = Field(max_length=4000)

    @field_validator("config")
    @classmethod
    def _is_json(cls, v: str) -> str:
        json.loads(v)  # a ValueError here becomes a 422
        return v


# ── projects and onboarding ─────────────────────────────────────
@router.get("/projects", dependencies=[Depends(current_user)])
async def projects(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.all("projects")


def _onboard(c: Ctx, project_id: str, spec: ProjectIn) -> None:
    """Runs after the response, on a worker thread. Every stage lands in the activity log."""
    doc = c.store.get("projects", project_id)
    if doc is None:
        return
    name, where = doc["name"], onboarding.redact(spec.repo)

    def say(action: str, detail: str, level: str = "ok") -> None:
        c.record(action, f"{name} · {detail}", project=project_id, level=level, actor="Architect", kind="agent")

    try:
        if spec.source == "git":
            root = onboarding.REPOS_DIR / project_id
            say("Cloning", f"{where} @ {spec.branch}", "info")
            onboarding.clone(spec.repo, spec.branch, root)
            say("Repository cloned", f"shallow clone of {spec.branch}")
        else:
            root = Path(os.path.expanduser(spec.repo.strip()))
        found = onboarding.scan(root, spec.excluded)
    except Exception as e:  # a refused clone, a vanished folder, a full disk: the operator needs the reason
        reason = onboarding.redact(str(e)) or type(e).__name__
        doc.update(status="paused", lastActive="just now", description=f"Onboarding stopped: {reason}")
        c.put("projects", c.store.save("projects", doc))
        say("Onboarding failed", reason, "err")
        return
    steps = onboarding.step_count(spec.connectDb, spec.mineGit, spec.ingestDocs)
    doc.update(onboarding.measured(found, steps))
    c.put("projects", c.store.save("projects", doc))
    langs = " · ".join(f"{lang['name']} {lang['pct']}%" for lang in found["languages"][:4]) or "no source files recognised"
    say("Stack detected", langs)
    say("Tree mapped", f"{found['files']:,} files · {onboarding.fmt_lines(found['lines'])} lines · {found['modules']} modules")
    if found["dbTables"] or found["storedProcs"]:
        say("SQL objects found", f"{found['dbTables']} tables · {found['storedProcs']} procedures", "info")
    try:
        idx = run_index(c, project_id, root, spec.excluded, found=found, steps=steps)
    except Exception as e:  # a parser must never undo onboarding: the measured project stays
        say("Indexing failed", onboarding.redact(str(e))[:200] or type(e).__name__, "err")
        return
    say("Code indexed", idx.describe())
    say("Onboarding paused", f"step {onboarding.INDEXED_STEPS + 1} of {steps}: business rules and test mapping "
                             "are not connected yet", "warn")


@router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, jobs: BackgroundTasks, user: User = Depends(require("projects:onboard")),
                         c: Ctx = Depends(ctx)) -> dict[str, Any]:
    if problem := onboarding.problem(body.source, body.repo, body.branch):
        raise HTTPException(422, problem)
    pid = c.store.unique_id("projects", onboarding.slug(body.repo))
    where = onboarding.redact(body.repo.strip())
    doc = {
        "id": pid, "name": onboarding.title(pid), "codename": pid.upper(), "stack": [], "kind": "greenfield",
        "status": "onboarding", "memoryPct": 0, "understoodPct": 0, "lines": "—", "modules": 0, "dbTables": 0,
        "storedProcs": 0, "repo": where, "lastActive": "just now", "coverage": [],
        "work": {"tasks": 0, "running": 0, "review": 0, "blocked": 0},
        "description": f"Onboarding from {where}.",
        "source": {"kind": body.source, "repo": where, **({"branch": body.branch.strip()} if body.source == "git" else {})},
        "rules": [r.model_dump() for r in body.rules], "excluded": body.excluded, "onboardedBy": user.name,
    }
    c.put("projects", c.store.insert("projects", doc))
    c.act(user, "Onboarding started", f"{doc['name']} · {where}", project=pid)
    jobs.add_task(_onboard, c, pid, body)
    return doc


# ── MCP servers ──────────────────────────────────────────────────
@router.get("/mcp/servers", dependencies=[Depends(current_user)])
async def mcp_servers(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.all("mcp")


@router.post("/mcp/servers", status_code=201)
async def register_mcp(body: McpIn, user: User = Depends(require("mcp:manage")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    sid = c.store.unique_id("mcp", body.name)
    doc = {"id": sid, "name": sid, "transport": body.transport, "status": "disconnected", "scope": body.scope,
           "command": body.command.strip(), "tools": [], "resources": 0, "prompts": 0, "latencyMs": 0,
           "calls24h": 0, "errorRate": 0, "untrusted": True, "defaultEffect": body.defaultEffect,
           "config": body.config, "registeredBy": user.name}
    c.put("mcp", c.store.insert("mcp", doc))
    c.act(user, "MCP server registered", f"{sid} · {body.transport} · {body.scope} scope · tools default to {body.defaultEffect}",
          project="aios")
    return doc
