"""The Blueprint wizard: from a new idea to a designed, scaffolded system — the technology catalogue, the
template bank (shipped and a person's own), blueprints with their answers and architecture, and scaffolding
one into a project. The routes are added by the Blueprint build.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/blueprints", tags=["blueprints"])
