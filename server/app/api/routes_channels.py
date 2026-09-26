"""Chat channels over HTTP: whether Telegram is set up, the admin's bot token, and each person linking
their own chat. What the bot sends and what its buttons decide lives in `services.telegram`."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..services.identity import Person
from ..services.telegram import ChannelService
from .deps import current_person, gateway, require, session, signed_in_person

router = APIRouter(prefix="/channels")


class TokenIn(BaseModel):
    #: The bot's token from @BotFather. Empty switches the channel off and unlinks everyone.
    token: str = Field(default="", max_length=100)


def _service(request: Request, open_session: AsyncSession, gw: Gateway) -> ChannelService:
    return ChannelService(open_session, gw.secrets, base=request.app.state.settings.telegram_url)


@router.get("")
async def channels(request: Request, who: Person = Depends(current_person),
                   open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _service(request, open_session, gw).status(who)


@router.put("/telegram")
async def configure(body: TokenIn, request: Request, who: Person = Depends(require("workspace:admin")),
                    open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """Check the token with Telegram, keep it in the keys file, and say which bot it is."""
    return await _service(request, open_session, gw).configure(
        body.token, who, ip=request.client.host if request.client else "")


@router.post("/telegram/link")
async def link(request: Request, who: Person = Depends(signed_in_person),
               open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    """A one-time code to send the bot, valid for ten minutes, and the t.me link that sends it. A person who
    signed in themselves only: a chat decides as the whole person, so an access token scoped to less must
    not be able to mint one."""
    return await _service(request, open_session, gw).link_code(who)


@router.delete("/telegram/link")
async def unlink(request: Request, who: Person = Depends(signed_in_person),
                 open_session: AsyncSession = Depends(session), gw: Gateway = Depends(gateway)) -> dict[str, Any]:
    return await _service(request, open_session, gw).unlink(who)
