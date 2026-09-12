"""The application, assembled.

Built next to the old one rather than on top of it: while the migration is underway both exist, and
nothing here can break what is currently serving. The old factory keeps the SQLite stack running; this
one is the Postgres stack, and the cutover is a one-line change of which is imported.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..db import Store
from ..secrets import Secrets
from ..settings import Settings, settings as get_settings
from ..events import Bus
from . import (
    routes_auth,
    routes_knowledge,
    routes_plans,
    routes_platform,
    routes_runs,
    routes_sessions,
    routes_work,
    stream,
)
from .deps import COOKIE
from .errors import install_error_handlers

ROUTERS = (routes_auth.router, routes_work.router, routes_plans.router, routes_knowledge.router,
           routes_platform.router, routes_sessions.router, routes_runs.router, stream.router)


def create_api(db: Database | None = None, *, config: Settings | None = None) -> FastAPI:
    cfg = config or get_settings()
    owned = db is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if owned:                                   # an engine this app made is an engine it closes
            await app.state.db.close()

    #: In-process fan-out to every open tab. One process, one workspace, so no broker is needed.
    feed = Bus()
    app = FastAPI(title="NeuroCode API", version="0.4.0", lifespan=lifespan)
    app.state.db = db or Database(config=cfg, bus=feed)
    app.state.settings = cfg
    # The AI gateway still keeps its settings and its usage ledger in the old store. That is the last
    # thing the cutover moves; until then it is built here so nothing else has to know about it.
    app.state.gateway = Gateway(Store(str(cfg.legacy_sqlite_path)), Secrets(cfg.secrets_path))
    app.state.bus = feed

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=cfg.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):  # noqa: ANN001, ANN202
        # A change that rides on the session cookie must also carry X-NC-Client. Another site can make
        # the browser send the cookie; it cannot add that header without a preflight it will not pass.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.cookies.get(COOKIE) \
                and not request.headers.get("x-nc-client"):
            return JSONResponse({"detail": "Missing the X-NC-Client header"}, status_code=403)
        return await call_next(request)

    install_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, object]:
        """Public: is the API up, and can it reach its database?"""
        return {"ok": await app.state.db.ping(), "database": "postgres"}

    return app
