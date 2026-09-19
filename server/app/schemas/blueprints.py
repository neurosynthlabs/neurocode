"""Blueprints and templates in the shape the Blueprints screen reads.

A blueprint's `spec` column holds its architecture and, beside it, what came of it — the last review,
the finalized document and diagram, the scaffold. They are handed out apart: `spec` is only the
architecture, so what the editor sends back is exactly what it was given.
"""
from __future__ import annotations

from typing import Any

from ..models import Blueprint, BlueprintTemplate
from ..services.blueprints import checks, diagram, split, template_summary
from .work import when


def blueprint_json(bp: Blueprint, *, names: dict[str, str], template_name: str | None = None,
                   project_name: str | None = None) -> dict[str, Any]:
    arch, extras = split(bp.spec)
    final = extras.get("final")
    return {
        "id": bp.id, "name": bp.name, "template": bp.template or None, "templateName": template_name,
        "status": bp.status, "revision": bp.revision, "answers": bp.answers or {}, "spec": arch,
        # Drawn from the architecture as it is now, so the editor's diagram never waits for a finalize.
        "diagram": diagram(arch), "checks": checks(arch),
        "review": extras.get("review"),
        # `stale` is true when the architecture changed after the document was written from it.
        "final": ({**final, "stale": final.get("revision") != bp.revision} if final else None),
        "scaffolded": extras.get("scaffolded"),
        "projectId": bp.project_id, "projectName": project_name,
        "createdBy": names.get(bp.created_by or "", None), "createdAt": when(bp.created_at),
        "updatedAt": when(bp.updated_at),
    }


def blueprint_row(bp: Blueprint, *, names: dict[str, str], template_name: str | None = None,
                  project_name: str | None = None) -> dict[str, Any]:
    """A blueprint as the list shows it: what it is and where it stands, not the whole architecture."""
    arch, extras = split(bp.spec)
    layers = arch.get("layers") or {}
    return {
        "id": bp.id, "name": bp.name, "template": bp.template or None, "templateName": template_name,
        "status": bp.status, "revision": bp.revision, "summary": arch.get("summary") or "",
        "idea": (bp.answers or {}).get("idea") or "",
        "layers": {k: v.get("choice") for k, v in layers.items() if v.get("choice")},
        "services": len(arch.get("services") or []),
        "reviewed": bool(extras.get("review")),
        "projectId": bp.project_id, "projectName": project_name,
        "createdBy": names.get(bp.created_by or "", None), "createdAt": when(bp.created_at),
        "updatedAt": when(bp.updated_at),
    }


def custom_template_json(row: BlueprintTemplate, tpl: dict[str, Any], *, names: dict[str, str]) -> dict[str, Any]:
    return template_summary(tpl, source="mine", extra={
        "description": row.description, "createdBy": names.get(row.created_by or "", None),
        "createdById": row.created_by, "createdAt": when(row.created_at)})


def template_json(tpl: dict[str, Any], source: str, row: BlueprintTemplate | None, *,
                  names: dict[str, str]) -> dict[str, Any]:
    """One template whole: its catalogue fields, its architecture as `spec`, and its diagram."""
    arch = {k: v for k, v in tpl.items() if k not in ("id", "name", "fitsWhen", "avoidWhen")}
    return {
        "id": tpl["id"], "name": tpl["name"], "summary": tpl.get("summary") or "", "source": source,
        "fitsWhen": tpl.get("fitsWhen") or [], "avoidWhen": tpl.get("avoidWhen") or [],
        "spec": arch, "diagram": diagram(arch),
        "description": row.description if row else None,
        "createdBy": names.get(row.created_by or "", None) if row else None,
        "createdById": row.created_by if row else None,
        "createdAt": when(row.created_at) if row else None,
    }
