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
from sqlalchemy.ext.asyncio import AsyncSession
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
    routes_agents,
    routes_ai,
    routes_auth,
    routes_blueprints,
    routes_code,
    routes_data,
    routes_diagnostics,
    routes_evals,
    routes_extensions,
    routes_git,
    routes_knowledge,
    routes_machine,
    routes_notebooks,
    routes_ops,
    routes_permissions,
    routes_plans,
    routes_platform,
    routes_research,
    routes_routines,
    routes_runs,
    routes_sessions,
    routes_system,
    routes_terminal,
    routes_testing,
    routes_tokens,
    routes_work,
    routes_workflows,
    stream,
)
from .deps import COOKIE
from .errors import install_error_handlers

log = logging.getLogger(__name__)

ROUTERS = (routes_auth.router, routes_work.router, routes_plans.router, routes_knowledge.router,
           routes_platform.router, routes_sessions.router, routes_runs.router, routes_code.router,
           routes_ai.router, routes_system.router, routes_admin.router, routes_admin_system.router,
           routes_testing.router, routes_git.router, routes_extensions.router, routes_workflows.router,
           routes_evals.router, routes_research.router, routes_ops.router, routes_permissions.router,
           routes_machine.router, routes_terminal.router, routes_blueprints.router, routes_routines.router,
           routes_tokens.router, routes_agents.router, routes_diagnostics.router,
           routes_notebooks.router, routes_data.router, stream.router)

#: What a row that was in flight when the process died says about itself afterwards.
INTERRUPTED = "interrupted: the server restarted"
#: The same thing said to the person reading the session, where a status word is not enough: the
#: question is still there and asking it again works, which is the only thing they need to know.
INTERRUPTED_ANSWER = ("The server restarted while this answer was being written, so it never "
                      "finished. Nothing was lost — ask again to carry on.")


async def reconcile_interrupted(open_session: AsyncSession) -> dict[str, int]:
    """Mark the work that claims to be in flight but cannot be, because the process running it is gone.

    Runs, evals and research are executed by tasks inside this process. When it stops, their rows keep
    saying `running` — or `queued`, for jobs that were handed off and never began — forever, and every
    screen that reads them shows work nobody is doing. At start-up nothing can be running yet, so any
    row that says so is a leftover, and failing it is the only true thing to say.

    A `waiting` run is the exception, and is left alone: it is parked on a person's approval, not on a
    process, and deciding that approval resumes it after a restart exactly as before.

    Sessions belong in the same list and were missing from it. `chat.think` sets a session to
    `thinking` and only clears it in its own `finally`, which does not run when the process is killed
    mid-answer — the common case, since answering is a background job that can take minutes. The
    Command Center's "what is working" and the pulsing dot on the Sessions list read that status, so
    one restart left a session working forever with nothing in the UI able to clear it. It is set back
    to idle here, and each one gets a turn of its own saying what happened, the same honesty the runs
    path already has.
    """
    params = {"why": INTERRUPTED}
    runs = (await open_session.execute(text(
        "WITH gone AS ("
        "  UPDATE runs SET status = 'failed', note = :why, finished_at = now() "
        "  WHERE status = 'running' RETURNING id), "
        "steps AS ("
        "  UPDATE run_steps SET status = 'failed', detail = :why "
        "  WHERE status = 'running' AND run_id IN (SELECT id FROM gone)) "
        "INSERT INTO run_logs(run_id, level, line) SELECT id, 'warn', :why FROM gone RETURNING run_id"),
        params)).all()
    evals = (await open_session.execute(text(
        "UPDATE eval_runs SET status = 'failed', note = :why, finished_at = now() "
        "WHERE status IN ('queued', 'running') RETURNING id"), params)).all()
    research = (await open_session.execute(text(
        "WITH gone AS ("
        "  UPDATE research_reports SET status = 'failed', note = :why, finished_at = now() "
        "  WHERE status IN ('queued', 'running') RETURNING id), "
        "angles AS ("
        "  UPDATE research_angles SET status = 'failed', error = :why "
        "  WHERE status = 'running' AND report_id IN (SELECT id FROM gone)) "
        "SELECT id FROM gone"), params)).all()
    sessions = (await open_session.execute(text(
        "WITH gone AS ("
        "  UPDATE chats SET status = 'idle', last_at = now() "
        "  WHERE status = 'thinking' RETURNING id) "
        "INSERT INTO chat_messages(chat_id, role, body, detail) "
        "SELECT id, 'note', :said, 'interrupted' FROM gone RETURNING chat_id"),
        {**params, "said": INTERRUPTED_ANSWER})).all()
    return {"runs": len(runs), "evals": len(evals), "research": len(research),
            "sessions": len(sessions)}


async def start_up_chores(open_session: AsyncSession) -> tuple[int, dict[str, int]]:
    """What has to happen before the first request, and could not happen anywhere else.

    Reconciling the catalogue is what makes a new permission, or a new agent, arrive on upgrade: the
    built-in roles and the roster are the application's, not the database's, so an installation that
    has been running for months still gains whatever was added to them. A role someone made
    themselves is left exactly as it is, and so is an agent a person switched off.

    That is all a new workspace is given. Everything else in it, somebody makes.

    Reconciling interrupted work belongs here for the same reason: only before the first request is it
    certain that nothing in this process is running yet. Returns the expired sessions removed and the
    interrupted work marked failed, so the caller can say so.
    """
    from ..data.loader import sync_agents, sync_roles
    from ..repositories.identity import SessionRepository

    await sync_roles(open_session)
    await sync_agents(open_session)
    gone = await SessionRepository(open_session).purge_expired()
    return gone, await reconcile_interrupted(open_session)


#: What `/health` says about the models when it could not ask. Every answer the gateway could give is
#: read from the settings table, so with the database down there is nothing true to say about a lane.
NO_COMPILER = {"provider": "unknown", "model": "",
               "note": "The router could not be read: its settings live in the database, which is not "
                       "answering."}


async def _compiler(app: FastAPI, ok: bool) -> dict[str, Any]:
    """What is answering for the models — asked only when there is a database to ask.

    The gateway's status walks its lanes, and every lane is settled from the settings table through
    the gateway's own blocking pool. With Postgres unreachable that raised, and `/health` — the one
    route whose whole job is to say the database is down — came back as a plain-text 500 instead. The
    web app reads `{ok: false}` to say "the API is up, its database is not"; a 500 is the one answer
    it cannot use.
    """
    if not ok:
        return dict(NO_COMPILER)
    try:
        # Asking the gateway means asking whether a key works, which is a blocking check.
        return await asyncio.to_thread(app.state.gateway.status)
    except Exception as e:                       # noqa: BLE001 — liveness answers, whatever else is wrong
        log.warning("/health could not read the router: %s", e)
        return dict(NO_COMPILER)


async def _on_start(app: FastAPI) -> None:
    """The start-up chores, in a transaction of their own.

    None of them is allowed to stop the API. A database that is not there yet is a thing to say
    plainly at the first request — `/health` answers that — not a process that refuses to boot.
    """
    try:
        async with app.state.db.session() as open_session:
            gone, interrupted = await start_up_chores(open_session)
    except Exception as e:                       # noqa: BLE001 — start-up must survive a cold database
        log.warning("start-up chores skipped: %s", e)
        return
    if gone:
        log.info("removed %d expired session(s)", gone)
    for what, n in interrupted.items():
        if n:
            log.info("reconciled %d interrupted %s", n, what)


#: How often the housekeeping loop wakes. The chore under it happens once a day — this is only how
#: soon after a start the day's prune is taken, and how long a stopped server's turn waits. Five
#: minutes costs one statement against one row; a tighter loop would buy nothing a person can see.
HOUSEKEEPING_SECONDS = 300.0


async def _housekeep(db: Database, cfg: Settings, every: float = HOUSEKEEPING_SECONDS) -> None:
    """The daily prune, run from this process — the one thing `Housekeeping` was missing.

    It has no loop of its own by design, so this is it: a wake, a claim, and almost always nothing.
    Only whoever wins the claim prunes, so three API processes — or one restarted three times in a
    morning — still prune once between them. A pass that fails is said once and tried again on the
    next wake: history that outlives its keeping by an hour is not worth ending a loop over.
    """
    from ..services.maintenance import Housekeeping
    keeper = Housekeeping(db, config=cfg)
    down = False
    while True:
        try:
            await keeper.tick()
            down = False
        except Exception as e:                   # noqa: BLE001 — a pass must never end the loop
            if not down:
                log.warning("housekeeping: skipped a pass: %s", e)
            down = True
        await asyncio.sleep(every)


def create_api(db: Database | None = None, *, config: Settings | None = None) -> FastAPI:
    cfg = config or get_settings()
    owned = db is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _on_start(app)
        # Routines fire from here, in this process, after the start-up chores have run. The loop survives
        # a database that is not there yet; stopping the app stops it and any fire it has under way.
        clock: asyncio.Task[None] | None = None
        scheduler = None
        if cfg.scheduler:
            from ..services.schedules import Scheduler
            scheduler = Scheduler(app.state.db, app.state.gateway)
            clock = asyncio.create_task(scheduler.run())
        # And beside it, the housekeeping the retention settings promise. Without this the daily prune
        # was written, tested and never once run: the screen's "Remove 12,480 rows" button was the only
        # way history ever shrank. NEUROCODE_PRUNE_DAILY=false is the off switch, and then there is no
        # loop at all rather than one that wakes for ever to decide it has nothing to do.
        chores: asyncio.Task[None] | None = None
        if cfg.prune_daily:
            chores = asyncio.create_task(_housekeep(app.state.db, cfg))
        yield
        if chores is not None:
            chores.cancel()
            await asyncio.gather(chores, return_exceptions=True)
        if clock is not None and scheduler is not None:
            clock.cancel()
            await asyncio.gather(clock, return_exceptions=True)
            await scheduler.stop()
        app.state.ledger.close()
        if owned:                                   # an engine this app made is an engine it closes
            await app.state.db.close()

    #: In-process fan-out to every open tab. One process, one workspace, so no broker is needed. A
    #: database handed in without a bus of its own is given this one: the stream subscribes to
    #: `app.state.bus`, and a database announcing its changes to a different bus — or to none — left
    #: every open tab hearing nothing at all.
    feed = db.bus if db is not None and db.bus is not None else Bus()
    if db is not None:
        db.bus = feed
    app = FastAPI(title="NeuroCode API", version="0.4.0", lifespan=lifespan)
    app.state.db = db or Database(config=cfg, bus=feed)
    app.state.settings = cfg
    # The gateway is blocking by nature — it waits on model providers from a worker thread — so it
    # gets its own small psycopg pool rather than the request's async session. Same database, though:
    # its settings and its usage ledger are Postgres rows like everything else.
    # The same database the app was given — not the one settings name. Built from settings, a gateway
    # inside an app handed a different database wrote its ledger somewhere else entirely, where the
    # user who made the call did not exist, and the foreign key quietly refused every line.
    app.state.ledger = PostgresLedger(app.state.db.url.replace("+asyncpg", "+psycopg"), echo=cfg.echo_sql)
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
        return {"ok": ok, "db": where, "counts": counts, "compiler": await _compiler(app, ok)}

    return app
