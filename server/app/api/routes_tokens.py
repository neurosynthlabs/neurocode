"""Personal access tokens: how the `nc` terminal client and scripts sign in as a person, limited to the
scopes the token names and never more than the person holds.

Every route here is for a person's own tokens, and needs a signed-in session: a token cannot list,
make or revoke tokens (see `deps.signed_in_person`). The token itself is in the answer to the POST that
made it, and nowhere else, ever again. Making and revoking are written to the audit log.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.identity import Person
from ..services.tokens import MAX_DAYS, MAX_NAME, TokenService, token_json
from .deps import session, signed_in_person

router = APIRouter(prefix="/tokens", tags=["tokens"])

#: The most tokens one page lists, however the caller spells the number.
MAX_LIST = 100


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    #: Permission ids the token may use. Empty: everything you hold except `machine:access`.
    scopes: list[Annotated[str, Field(max_length=60)]] = Field(default_factory=list, max_length=40)
    #: Days until it stops working; left out, it works until it is revoked.
    expiresInDays: int | None = Field(default=None, ge=1, le=MAX_DAYS)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("")
async def tokens(limit: int = Query(default=50, ge=1, le=MAX_LIST), offset: int = Query(default=0, ge=0),
                 who: Person = Depends(signed_in_person),
                 open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Your tokens, newest first — live, expired and revoked, each with when it was last used."""
    page = await TokenService(open_session).mine(who, limit=limit, offset=offset)
    return {"items": [token_json(t) for t in page.items], "total": page.total, "limit": page.limit,
            "offset": page.offset, "nextOffset": page.next_offset}


@router.post("", status_code=201)
async def make_token(body: TokenIn, request: Request, who: Person = Depends(signed_in_person),
                     open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """A new token. `token` is its secret — shown in this answer once, and never again."""
    row, secret = await TokenService(open_session).create(who, name=body.name, scopes=body.scopes,
                                                          expires_days=body.expiresInDays, ip=_ip(request))
    return {**token_json(row), "token": secret}


@router.post("/{token_id}/revoke")
async def revoke_token(token_id: str, request: Request, who: Person = Depends(signed_in_person),
                       open_session: AsyncSession = Depends(session)) -> dict[str, Any]:
    """Stop a token working, at once and for good. It stays in the list, marked revoked."""
    return token_json(await TokenService(open_session).revoke(who, token_id, ip=_ip(request)))
