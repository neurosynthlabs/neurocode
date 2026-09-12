"""Turning a refusal into a status code, in one place.

A service says no in words a person can read and carries the status it deserves; a repository says
"not there". Neither imports FastAPI. This is the only file that knows both sides, so a rule never
has to be written twice — once for the rule and once for its HTTP shape.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..repositories.base import NotFound
from ..services.errors import Refused


async def _refused(_: Request, error: Exception) -> JSONResponse:
    status = getattr(error, "status", 409)
    return JSONResponse({"detail": str(error)}, status_code=status)


async def _not_found(_: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=404)


def install_error_handlers(app: FastAPI) -> None:
    """`Refused` carries its own status — 400, 401, 403, 409, 429 — and `NotFound` is always a 404."""
    app.add_exception_handler(Refused, _refused)
    app.add_exception_handler(NotFound, _not_found)
