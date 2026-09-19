"""Facts and their conflicts, in the shape the memory screens read."""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect

from ..models import MemoryConflict, MemoryFact
from ..repositories.knowledge import Recall
from .work import when


def fact_json(fact: MemoryFact) -> dict[str, Any]:
    return {
        "id": fact.id, "ref": fact.ref, "category": fact.category, "title": fact.title,
        "body": fact.body, "reason": fact.reason, "source": fact.source,
        # Null for a fact that belongs to no project: it belongs to the workspace, and to every project.
        "projectId": fact.project_id, "confidence": fact.confidence,
        # Counted from `memory_hits` in the statement that loaded the fact. Read from what was loaded
        # rather than through the attribute, which would be a lazy load inside async code; a fact
        # written in this unit of work has not been recalled by anything yet.
        "hits24h": inspect(fact).dict.get("hits_24h") or 0, "lastUsedAt": when(fact.last_used_at),
        "createdAt": when(fact.created_at), "evidence": fact.evidence or [],
        "tags": sorted(t.tag for t in fact.tags), "pinned": fact.pinned,
        "archived": fact.archived, "archivedAt": when(fact.archived_at),
    }


def recall_json(hit: Recall) -> dict[str, Any]:
    return {"ref": hit.ref, "title": hit.title, "feature": hit.feature, "context": hit.context,
            "at": when(hit.at)}


def conflict_json(conflict: MemoryConflict) -> dict[str, Any]:
    return {
        "id": conflict.id, "topic": conflict.topic, "a": conflict.a, "b": conflict.b,
        "detected": when(conflict.detected_at), "detail": conflict.detail,
        "suggestion": conflict.suggestion, "severity": conflict.severity, "status": conflict.status,
        **({"resolution": conflict.resolution} if conflict.resolution else {}),
    }
