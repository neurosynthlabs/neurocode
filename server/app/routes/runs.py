"""Agent runs over HTTP: what they did, their output, the real diff, and stopping or discarding one."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import runtime
from ..auth import User, current_user, require
from ..context import Ctx, ctx, need

router = APIRouter(prefix="/runs")


@router.get("", dependencies=[Depends(current_user)])
async def runs(project: str | None = None, c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
    return c.store.docs("SELECT doc FROM runs WHERE (? IS NULL OR project_id = ?) ORDER BY started DESC", (project, project))


@router.get("/{ref}", dependencies=[Depends(current_user)])
async def run(ref: str, after: int = 0, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """The run and its output. `after` is the last log id you hold, for catching up after a reconnect."""
    doc = need(c.store.one("runs", ref), ref)
    return {**doc, "logs": c.store.run_logs(doc["id"], after)}


@router.get("/{ref}/diff", dependencies=[Depends(current_user)])
async def diff(ref: str, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    doc = need(c.store.one("runs", ref), ref)
    return await asyncio.to_thread(runtime.diff_of, doc)


@router.post("/{ref}/cancel")
async def cancel(ref: str, user: User = Depends(require("runs:run")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    doc = need(c.store.one("runs", ref), ref)
    if doc["status"] in ("done", "failed", "cancelled"):
        raise HTTPException(409, f"{ref} already {doc['status']}")
    c.act(user, "Run stopped", f"{ref} · {doc['branch']} — the worktree stays for you to look at",
          project=doc["projectId"], level="warn", task_ref=doc.get("taskRef"))
    return await asyncio.to_thread(runtime.cancel, c, doc)


@router.post("/{ref}/merge")
async def merge(ref: str, request: Request, user: User = Depends(require("runs:merge")),
                c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """Merge an accepted run into the branch your repository has checked out."""
    doc = need(c.store.one("runs", ref), ref)
    try:
        result = await asyncio.to_thread(runtime.merge, c, doc, user.name)
    except runtime.Refused as e:
        raise HTTPException(409, str(e)) from e
    if result["merged"]:
        c.act(user, "Merged", f"{ref} · {doc['branch']} → {result['into']} as {result['commit']} · undo: {result['undo']}",
              project=doc["projectId"], level="ok", task_ref=doc.get("taskRef"))
        c.audit("run.merge", user=user, target=f"{doc['branch']} → {result['into']}",
                detail={"commit": result["commit"], "run": ref}, request=request)
    else:
        c.act(user, "Merge collided", f"{ref} · {len(result['conflicts'])} files collide with {result['into']}; "
              "nothing was merged", project=doc["projectId"], level="warn", task_ref=doc.get("taskRef"))
    return {**result, "run": c.store.one("runs", ref)}


@router.post("/{ref}/discard")
async def discard(ref: str, user: User = Depends(require("runs:run")), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """Remove the worktree and the branch. Only once the run has stopped."""
    doc = need(c.store.one("runs", ref), ref)
    if doc["status"] in ("queued", "running"):
        raise HTTPException(409, f"{ref} is still working. Stop it first.")
    if doc["status"] == "waiting":
        raise HTTPException(409, f"{ref} is waiting for your decision. Answer it, or stop the run first.")
    if doc.get("removed"):
        return doc
    await asyncio.to_thread(runtime.cleanup, c, doc)
    c.act(user, "Worktree discarded", f"{ref} · {doc['branch']} removed", project=doc["projectId"], level="warn",
          task_ref=doc.get("taskRef"))
    c.put("runs", c.store.save_run(doc))
    return doc
