"""Every document the screens hold, announced the moment it really changes.

The stream promises four kinds of event and three of them were published by hand, at the call sites
that remembered. The fourth — `change`, the one that keeps a second open tab honest — was promised
and never sent at all, which is the failure mode this module exists to make impossible: it is not
wired to any call site. It listens to the unit of work itself.

Two rules make it trustworthy. It collects on **flush** and publishes on **commit**, so nothing is
announced that a rollback then undoes. A session with no bus — a test, a script, a migration —
collects nothing and publishes nothing.

A third rule: a document announced here is the document the list route returns, field for field. The
screens replace what they hold with it, so a run announced without its project's name, its agents or
its task used to blank all three on screen at every step, and a project announced without its task
counts and its code index showed zeros and switched code search off. Runs, projects and decisions are
shaped by the same builders their routes use — the same queries, so one definition — and those
queries need the database. So objects are *noted* on flush (while `session.new` and `session.dirty`
still say what changed) and *shaped* just after it (`after_flush_postexec`, when the rows are written
and the session may be queried), still inside the transaction and before the commit that announces
them.

A change that is not one document — emptying the whole workspace — is announced with `announce`,
under the same rule: only once it has committed.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_session
from sqlalchemy.orm import Session
from sqlalchemy.util import await_only
from sqlalchemy.util.concurrency import in_greenlet

log = logging.getLogger(__name__)

#: model name → the collection the frontend keeps it in. Only what a screen actually holds: a run's
#: log lines and a chat's messages have their own events, and nothing else is a document over there.
COLLECTIONS: dict[str, str] = {
    "Task": "tasks",
    "Approval": "approvals",
    "MemoryFact": "memory",
    "MemoryConflict": "conflicts",
    "Project": "projects",
    "Plan": "plans",
    "McpServer": "mcp",
    "Pref": "prefs",
    "Decision": "decisions",
    "Brainstorm": "brainstorms",
    "Run": "runs",
}


def _serialisers() -> dict[str, Callable[[Any], dict[str, Any]]]:
    """Imported on first use: the schemas import the models, and the models import this."""
    from ..schemas.knowledge import conflict_json, fact_json
    from ..schemas.platform import mcp_json
    from ..schemas.runtime import run_json
    from ..schemas.work import approval_json, decision_json, plan_json, pref_json, project_json, task_json

    out: dict[str, Callable[[Any], dict[str, Any]]] = {
        "Task": task_json, "Approval": approval_json, "MemoryFact": fact_json,
        "MemoryConflict": conflict_json, "Project": project_json, "Plan": plan_json,
        "McpServer": mcp_json, "Pref": pref_json, "Decision": decision_json, "Run": run_json,
    }
    try:                                             # only once the /ai surface is in place
        from ..schemas.ai import brainstorm_json
        out["Brainstorm"] = brainstorm_json
    except ImportError:
        pass
    return out


_CACHE: dict[str, Callable[[Any], dict[str, Any]]] = {}


def _json_for(name: str) -> Callable[[Any], dict[str, Any]] | None:
    if not _CACHE:
        _CACHE.update(_serialisers())
    return _CACHE.get(name)


async def _runs(open_session: AsyncSession, runs: list[Any]) -> list[dict[str, Any]]:
    # The routes' own builder: a run's project name, its agents, its task and its plan.
    from ..api.routes_runs import _context, _one
    context = await _context(open_session, runs)
    return [_one(run, context) for run in runs]


async def _projects(open_session: AsyncSession, projects: list[Any]) -> list[dict[str, Any]]:
    # What `GET /projects` adds: the open-work counts, the code index and the project's sources.
    from ..repositories import CodeIndexRepository, ProjectRepository
    from ..repositories.references import ProjectReferenceRepository
    from ..repositories.sources import ProjectSourceRepository
    from ..schemas.work import project_json
    ids = [p.id for p in projects]
    counts = await ProjectRepository(open_session).task_counts()
    indexes = await CodeIndexRepository(open_session).for_projects(ids)
    sources = await ProjectSourceRepository(open_session).for_projects(ids)
    references = await ProjectReferenceRepository(open_session).for_projects(ids)
    return [project_json(p, tasks=counts.get(p.id), index=indexes.get(p.id), sources=sources.get(p.id, []),
                         references=references.get(p.id, []))
            for p in projects]


async def _decisions(open_session: AsyncSession, decisions: list[Any]) -> list[dict[str, Any]]:
    from ..repositories import DecisionRepository
    from ..schemas.work import decision_json
    names = await DecisionRepository(open_session).names(decisions)
    return [decision_json(d, by=names.get(d.by_user_id or "")) for d in decisions]


#: The documents whose list route adds what the row alone does not hold.
_IN_CONTEXT: dict[str, Callable[[AsyncSession, list[Any]], Any]] = {
    "Run": _runs, "Project": _projects, "Decision": _decisions,
}


def _docs(session: Session, name: str, objs: list[Any]) -> list[dict[str, Any]]:
    """The documents for these objects, all of one model, in the shape their list route returns."""
    builder = _IN_CONTEXT.get(name)
    proxy = async_session(session) if builder is not None else None
    # Every session that carries a bus is an AsyncSession, flushed from inside SQLAlchemy's greenlet —
    # which is what lets this synchronous hook wait on the async builders with `await_only`.
    if builder is not None and proxy is not None and in_greenlet():
        return list(await_only(builder(proxy, objs)))
    shape = _json_for(name)
    return [shape(obj) for obj in objs] if shape is not None else []


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _context: Any) -> None:
    """What this unit of work touched, noted while `new` and `dirty` still say so."""
    if session.info.get("bus") is None:
        return
    pending: list[tuple[str, Any]] = session.info.setdefault("changes", [])
    touched: list[Any] = session.info.setdefault("touched", [])

    for obj in (*session.new, *session.dirty):
        collection = COLLECTIONS.get(type(obj).__name__)
        if collection is None or not session.is_modified(obj, include_collections=True) and obj not in session.new:
            continue
        touched.append(obj)

    for obj in session.deleted:
        collection = COLLECTIONS.get(type(obj).__name__)
        if collection is not None:
            pending.append(("change", {"op": "drop", "collection": collection, "id": obj.id}))


@event.listens_for(Session, "after_flush_postexec")
def _shape(session: Session, _context: Any) -> None:
    """The noted objects as documents, now that their rows are written and can be read around."""
    touched: list[Any] | None = session.info.pop("touched", None)
    if not touched:
        return
    pending: list[tuple[str, Any]] = session.info.setdefault("changes", [])
    by_model: dict[str, list[Any]] = {}
    for obj in touched:
        same = by_model.setdefault(type(obj).__name__, [])
        if not any(o is obj for o in same):
            same.append(obj)
    for name, objs in by_model.items():
        try:
            docs = _docs(session, name, objs)
        except Exception as e:                       # noqa: BLE001 — announcing must not fail a write
            log.debug("could not serialise %d %s for the stream: %s", len(objs), name, e)
            continue
        pending.extend(("change", {"op": "put", "collection": COLLECTIONS[name], "doc": doc}) for doc in docs)


def announce(session: Session | AsyncSession, kind: str, data: Any) -> None:
    """Publish one event when this transaction commits, and never if it rolls back."""
    target = session.sync_session if isinstance(session, AsyncSession) else session
    if target.info.get("bus") is not None:
        target.info.setdefault("changes", []).append((kind, data))


@event.listens_for(Session, "after_commit")
def _announce(session: Session) -> None:
    """Only now, because only now is it true."""
    pending = session.info.pop("changes", None)
    feed = session.info.get("bus")
    if not pending or feed is None:
        return
    for kind, data in pending:
        feed.publish(kind, data)


@event.listens_for(Session, "after_rollback")
@event.listens_for(Session, "after_soft_rollback")
def _forget(session: Session, *_: Any) -> None:
    """A change that was undone was never a change."""
    session.info.pop("changes", None)
    session.info.pop("touched", None)
