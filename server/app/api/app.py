"""The application, assembled.

Built next to the old one rather than on top of it: while the migration is underway both exist, and
nothing here can break what is currently serving. The old factory keeps the SQLite stack running; this
one is the Postgres stack, and the cutover is a one-line change of which is imported.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..ai.gateway import Gateway
from ..data.engine import Database
from ..ai.ledger import PostgresLedger
from ..secrets import Secrets
from ..settings import Settings, settings as get_settings
from ..events import Bus
from . import (
    routes_admin,
    routes_admin_system,
    routes_ai,
    routes_auth,
    routes_code,
    routes_knowledge,
    routes_plans,
    routes_platform,
    routes_runs,
    routes_sessions,
    routes_system,
    routes_work,
    stream,
)
from .deps import COOKIE
from .errors import install_error_handlers

log = logging.getLogger(__name__)

ROUTERS = (routes_auth.router, routes_work.router, routes_plans.router, routes_knowledge.router,
           routes_platform.router, routes_sessions.router, routes_runs.router, routes_code.router,
           routes_ai.router, routes_system.router, routes_admin.router, routes_admin_system.router,
           stream.router)


async def _on_start(app: FastAPI) -> None:
    """The two chores that have to happen before the first request, and could not happen anywhere else.

    Reconciling the built-in roles is what makes a new permission arrive on upgrade: the catalogue is
    the application's, not the database's, so an installation that has been running for months still
    gains whatever was added to it. A role someone made themselves is left exactly as it is.

    Neither is allowed to stop the API. A database that is not there yet is a thing to say plainly
    at the first request — `/health` answers that — not a process that refuses to boot.
    """
    from ..data.loader import sync_roles
    from ..repositories.identity import SessionRepository

    try:
        async with app.state.db.session() as open_session:
            await sync_roles(open_session)
            gone = await SessionRepository(open_session).purge_expired()
    except Exception as e:                       # noqa: BLE001 — start-up must survive a cold database
        log.warning("start-up chores skipped: %s", e)
        return
    if gone:
        log.info("removed %d expired session(s)", gone)

def create_api(db: Database | None = None, *, config: Settings | None = None) -> FastAPI:
    cfg = config or get_settings()
    owned = db is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _on_start(app)
        yield
        app.state.ledger.close()
        if owned:                                   # an engine this app made is an engine it closes
            await app.state.db.close()

    #: In-process fan-out to every open tab. One process, one workspace, so no broker is needed.
    feed = Bus()
    app = FastAPI(title="NeuroCode API", version="0.4.0", lifespan=lifespan)
    app.state.db = db or Database(config=cfg, bus=feed)
    app.state.settings = cfg
    # The gateway is blocking by nature — it waits on model providers from a worker thread — so it
    # gets its own small psycopg pool rather than the request's async session. Same database, though:
    # its settings and its usage ledger are Postgres rows like everything else.
    app.state.ledger = PostgresLedger(cfg.blocking_database_url, echo=cfg.echo_sql)
    app.state.gateway = Gateway(app.state.ledger, Secrets(cfg.secrets_path))
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
    async def health() -> dict[str, Any]:
        """Public: is the API up, can it reach its database, and what is answering for the models?

        The row counts come from the planner's own statistics rather than a COUNT(*) per table: this
        is polled, forty-odd tables would be forty-odd scans, and an estimate is all a status panel
        has ever shown. `ANALYZE` keeps them honest; a table nobody has analysed reports zero.
        """
        ok = await app.state.db.ping()
        counts: dict[str, int] = {}
        if ok:
            async with app.state.db.read() as open_session:
                rows = await open_session.execute(text(
                    "SELECT relname, GREATEST(n_live_tup, 0) FROM pg_stat_user_tables "
                    "WHERE n_live_tup > 0 ORDER BY relname"))
                counts = {name: int(n) for name, n in rows.all()}
        where = cfg.database_url.rsplit("@", 1)[-1].replace("+asyncpg", "")
        return {"ok": ok, "db": where, "counts": counts,
                # Asking the gateway means asking whether a key works, which is a blocking check.
                "compiler": await asyncio.to_thread(app.state.gateway.status)}

    return app
