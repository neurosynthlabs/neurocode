"""Every document the screens hold, announced the moment it really changes.

The stream promises four kinds of event and three of them were published by hand, at the call sites
that remembered. The fourth — `change`, the one that keeps a second open tab honest — was promised
and never sent at all, which is the failure mode this module exists to make impossible: it is not
wired to any call site. It listens to the unit of work itself.

Two rules make it trustworthy. It collects on **flush**, while the objects are still whole and can be
serialised, and it publishes on **commit**, so nothing is announced that a rollback then undoes. A
session with no bus — a test, a script, a migration — collects nothing and publishes nothing.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

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


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _context: Any) -> None:
    """What this unit of work touched, taken while the objects are still readable."""
    if session.info.get("bus") is None:
        return
    pending: list[tuple[str, Any]] = session.info.setdefault("changes", [])

    for obj in (*session.new, *session.dirty):
        collection = COLLECTIONS.get(type(obj).__name__)
        if collection is None or not session.is_modified(obj, include_collections=True) and obj not in session.new:
            continue
        shape = _json_for(type(obj).__name__)
        if shape is None:
            continue
        try:
            pending.append(("change", {"op": "put", "collection": collection, "doc": shape(obj)}))
        except Exception as e:                       # noqa: BLE001 — announcing must not fail a write
            log.debug("could not serialise a %s for the stream: %s", type(obj).__name__, e)

    for obj in session.deleted:
        collection = COLLECTIONS.get(type(obj).__name__)
        if collection is not None:
            pending.append(("change", {"op": "drop", "collection": collection, "id": obj.id}))


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
