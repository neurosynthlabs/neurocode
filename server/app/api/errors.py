"""Turning a refusal into a status code, in one place.

A service says no in words a person can read and carries the status it deserves; a repository says
"not there". Neither imports FastAPI. This is the only file that knows both sides, so a rule never
has to be written twice — once for the rule and once for its HTTP shape.

The last three handlers are for what nobody meant to say. Starlette's own answer to an unhandled
exception is the plain-text body `Internal Server Error` with no JSON at all, and the web app — which
reads `detail` out of the body — then shows a toast that says "HTTP 500" and nothing else. That is
the product's one rule broken exactly when it matters most: these fire when Postgres has gone away
under a request, or when the pool has no connection to give, which is when a person most needs to be
told what happened and what to do about it. So every one of them answers in the same shape a refusal
does, and the catch-all logs the traceback so the words a person sees stay short.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from ..repositories.base import NotFound
from ..services.errors import Refused

log = logging.getLogger(__name__)

#: What the API says when its database is unreachable and when its pool is full. Both are temporary
#: and both have something the person can do, so both say what it is.
DATABASE_GONE = ("The database is not answering. Check that Postgres is running, then try again.")
POOL_BUSY = ("The API is busy and could not get a database connection in time. Something long-running "
             "is holding them — try again in a moment.")


async def _refused(_: Request, error: Exception) -> JSONResponse:
    status = getattr(error, "status", 409)
    return JSONResponse({"detail": str(error)}, status_code=status)


async def _not_found(_: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=404)


async def _pool_busy(request: Request, error: Exception) -> JSONResponse:
    log.warning("no database connection for %s %s: %s", request.method, request.url.path, error)
    return JSONResponse({"detail": POOL_BUSY}, status_code=503)


async def _database_gone(request: Request, error: Exception) -> JSONResponse:
    """Postgres went away mid-request, or was never there. 503, because it is the database that is
    down and not the request that was wrong — and because a client may sensibly try again."""
    log.warning("the database failed during %s %s: %s", request.method, request.url.path, error)
    return JSONResponse({"detail": DATABASE_GONE}, status_code=503)


async def _unexpected(request: Request, error: Exception) -> JSONResponse:
    """Anything nobody wrote a rule for. The person gets words and the path they were on; the log
    gets the traceback, because the words on a screen are not the place for one."""
    log.exception("unhandled error during %s %s", request.method, request.url.path)
    return JSONResponse(
        {"detail": f"Something went wrong inside the API while answering "
                   f"{request.method} {request.url.path}. It is written in the server's log with "
                   f"everything needed to find it ({type(error).__name__})."},
        status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    """`Refused` carries its own status — 400, 401, 403, 409, 429 — and `NotFound` is always a 404.

    Only the two database failures that are really about the database are given 503s:
    `OperationalError` (the server went away, refused the connection, or cancelled the statement) and
    `InterfaceError` (the connection itself broke). An `IntegrityError` or a `ProgrammingError` is a
    defect in this code, not an outage, and belongs with everything else in the catch-all.

    Order does not matter: Starlette walks the exception's own class hierarchy and takes the most
    specific handler registered, so `OperationalError` finds its own rather than the catch-all.
    """
    app.add_exception_handler(Refused, _refused)
    app.add_exception_handler(NotFound, _not_found)
    app.add_exception_handler(PoolTimeout, _pool_busy)
    app.add_exception_handler(OperationalError, _database_gone)
    app.add_exception_handler(InterfaceError, _database_gone)
    app.add_exception_handler(Exception, _unexpected)
