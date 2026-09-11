"""Signing in: first-run setup, login, logout, who am I, and changing your own password.

These run as plain functions, so FastAPI puts them on a worker thread: scrypt is deliberately slow and
must not stall the event loop.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import COOKIE, SESSION_TTL, User, current_user, token_from
from ..context import Ctx, ctx
from ..db import now_iso

router = APIRouter(prefix="/auth")


class SetupIn(BaseModel):
    workspace: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


def _cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=False,
                        max_age=int(SESSION_TTL.total_seconds()), path="/")


def _me(c: Ctx, user: User) -> dict[str, Any]:
    ws = c.store.row("SELECT name FROM workspace WHERE id = 1")
    return {"user": user.public(), "workspace": {"name": ws[0]} if ws else None}


@router.get("/status")
def status(request: Request, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """Public: does this workspace need its first Owner, and is the caller signed in?"""
    token = token_from(request)
    user = c.accounts.session(token) if token else None
    ws = c.store.row("SELECT name FROM workspace WHERE id = 1")
    return {"needsSetup": c.accounts.count() == 0, "user": user.public() if user else None,
            "workspace": {"name": ws[0]} if ws else None}


@router.post("/setup", status_code=201)
def setup(body: SetupIn, request: Request, response: Response, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    """First run only: name the workspace and create its first Owner, signed straight in."""
    if c.accounts.count() > 0:
        raise HTTPException(409, "This workspace is already set up. Sign in instead.")
    c.accounts.check(body.email, body.name, body.password)
    c.store.execute("INSERT OR REPLACE INTO workspace(id, name, created_at) VALUES (1, ?, ?)", (body.workspace.strip(), now_iso()))
    user = c.accounts.create(body.email, body.name, body.password, ["owner"])
    _cookie(response, c.accounts.start_session(user.id, request.headers.get("user-agent", "")))
    c.audit("workspace.setup", user=user, target=body.workspace.strip(), request=request)
    return _me(c, user)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, c: Ctx = Depends(ctx)) -> dict[str, Any]:
    try:
        user, token = c.accounts.login(body.email, body.password, request.headers.get("user-agent", ""))
    except HTTPException as e:
        c.audit("auth.login_failed", user=None, target=body.email.strip()[:200], detail={"reason": e.detail}, request=request)
        raise
    _cookie(response, token)
    c.audit("auth.login", user=user, target=user.email, request=request)
    return _me(c, user)


@router.post("/logout")
def logout(request: Request, response: Response, c: Ctx = Depends(ctx)) -> dict[str, bool]:
    token = token_from(request)
    if token:
        c.accounts.logout(token)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), c: Ctx = Depends(ctx)) -> dict[str, Any]:
    return _me(c, user)


@router.post("/password")
def change_password(body: PasswordIn, request: Request, user: User = Depends(current_user),
                    c: Ctx = Depends(ctx)) -> dict[str, bool]:
    if not c.accounts.verify(user.id, body.current):
        raise HTTPException(403, "The current password is wrong")
    c.accounts.set_password(user.id, body.new, keep=token_from(request))
    c.audit("auth.password", user=user, target=user.email, request=request)
    return {"ok": True}
