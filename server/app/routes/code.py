"""The code index over HTTP: a summary, a lazy file tree, search, one file's relations, impact analysis
and the module graph. Reading needs a session; re-indexing needs `projects:onboard`."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from .. import codeindex, onboarding
from ..auth import User, current_user, require
from ..context import Ctx, ctx, need

router = APIRouter(prefix="/projects/{pid}/code")


def source_root(doc: dict[str, Any]) -> Path | None:
    """Where an onboarded project's code lives on this machine. Sample projects have none."""
    src = doc.get("source")
    if not src:
        return None
    return onboarding.REPOS_DIR / doc["id"] if src["kind"] == "git" else Path(os.path.expanduser(src["repo"]))


def run_index(c: Ctx, pid: str, root: Path, excluded: list[str], *, found: dict[str, Any] | None = None,
              steps: int | None = None) -> codeindex.Index:
    """Index a project's code and record the result on it. Blocking: call it from a worker thread."""
    c.indexing.add(pid)
    try:
        idx = codeindex.build(root, excluded)
        codeindex.save(c.store, pid, str(root), idx)
    finally:
        c.indexing.discard(pid)
    doc = c.store.get("projects", pid)
    if doc is not None:
        doc.update(codeindex.project_fields(idx, doc, found=found, steps=steps))
        c.put("projects", c.store.save("projects", doc))
    return idx


def _reindex(c: Ctx, pid: str, root: Path, excluded: list[str]) -> None:
    name = (c.store.get("projects", pid) or {}).get("name", pid)
    try:
        idx = run_index(c, pid, root, excluded)
    except Exception as e:  # the operator needs the reason; the previous index stays in place
        reason = onboarding.redact(str(e))[:200] or type(e).__name__
        c.record("Indexing failed", f"{name} · {reason}", project=pid, level="err", actor="Architect", kind="agent")
        return
    c.record("Code indexed", f"{name} · {idx.describe()}", project=pid, level="ok", actor="Architect", kind="agent")


def _project(c: Ctx, pid: str) -> dict[str, Any]:
    return need(c.store.get("projects", pid), f"project {pid}")


@router.get("", dependencies=[Depends(current_user)])
async def summary(pid: str, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    doc = _project(c, pid)
    found = await asyncio.to_thread(codeindex.summary, c.store, pid)
    state = {"indexing": pid in c.indexing, "canIndex": source_root(doc) is not None}
    return {**found, **state} if found else {"indexed": False, **state}


@router.get("/files", dependencies=[Depends(current_user)])
async def files(pid: str, dir: str = "", c: Ctx = Depends(ctx)) -> dict[str, Any]:
    _project(c, pid)
    return await asyncio.to_thread(codeindex.children, c.store, pid, dir)


@router.get("/search", dependencies=[Depends(current_user)])
async def search(pid: str, q: str = "", c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    _project(c, pid)
    return await asyncio.to_thread(codeindex.search, c.store, pid, q)


@router.get("/file", dependencies=[Depends(current_user)])
async def file(pid: str, path: str, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    _project(c, pid)
    return need(await asyncio.to_thread(codeindex.file_detail, c.store, pid, path), path)


@router.get("/impact", dependencies=[Depends(current_user)])
async def impact(pid: str, path: str | None = None, module: str | None = None,
                 obj: str | None = Query(default=None, alias="object"), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    _project(c, pid)
    if not (path or module or obj):
        raise HTTPException(422, "Name a path, a module or a database object")
    found = await asyncio.to_thread(lambda: codeindex.impact(c.store, pid, path=path, module=module, obj=obj))
    return need(found, path or module or obj or "")


@router.get("/graph", dependencies=[Depends(current_user)])
async def graph(pid: str, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    _project(c, pid)
    return await asyncio.to_thread(codeindex.graph, c.store, pid)


@router.post("/reindex", status_code=202)
async def reindex(pid: str, jobs: BackgroundTasks, user: User = Depends(require("projects:onboard")),
                  c: Ctx = Depends(ctx)) -> dict[str, bool]:
    doc = _project(c, pid)
    root = source_root(doc)
    if root is None:
        raise HTTPException(409, f"{doc['name']} is a sample project with no code on this machine. Onboard a repository to index one.")
    if not root.is_dir():
        raise HTTPException(409, f"The code for {doc['name']} is no longer at {onboarding.redact(str(root))}")
    if pid in c.indexing:
        raise HTTPException(409, f"{doc['name']} is being indexed already")
    c.act(user, "Re-indexing", f"{doc['name']} · reading the code again", project=pid)
    jobs.add_task(_reindex, c, pid, root, doc.get("excluded", []))
    return {"ok": True}
