"""The gates a person decided and the rules the runtime applies.

The approvals themselves are served by the work routes; what lives here is what outlasts a single
approval — the answers the runtime keeps and consults the next time, per project.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.gates import RuleService
from .deps import current_person, session

router = APIRouter(prefix="/permissions")


@router.get("/rules", dependencies=[Depends(current_person)])
async def rules(limit: int | None = None, offset: int = 0,
                open_session: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
    """Each project's standing answer to running its own tests, with who gave it and when."""
    return await RuleService(open_session).rules(limit=limit, offset=offset)
