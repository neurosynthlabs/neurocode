"""Facts and their conflicts, in the shape the memory screens read."""
from __future__ import annotations

from typing import Any

from ..models import MemoryConflict, MemoryFact
from .work import when

#: A fact that belongs to no project belongs to the workspace. The screens spell that "global".
GLOBAL = "global"


def fact_json(fact: MemoryFact) -> dict[str, Any]:
    return {
        "id": fact.id, "ref": fact.ref, "category": fact.category, "title": fact.title,
        "body": fact.body, "reason": fact.reason, "source": fact.source,
        "projectId": fact.project_id or GLOBAL, "confidence": fact.confidence,
        "strength": fact.strength, "hits": fact.hits, "createdAt": when(fact.created_at),
        "lastUsed": when(fact.last_used_at), "evidence": fact.evidence or [],
        "tags": sorted(t.tag for t in fact.tags), "pinned": fact.pinned,
    }


def conflict_json(conflict: MemoryConflict) -> dict[str, Any]:
    return {
        "id": conflict.id, "topic": conflict.topic, "a": conflict.a, "b": conflict.b,
        "detected": when(conflict.detected_at), "detail": conflict.detail,
        "suggestion": conflict.suggestion, "severity": conflict.severity, "status": conflict.status,
        **({"resolution": conflict.resolution} if conflict.resolution else {}),
    }
