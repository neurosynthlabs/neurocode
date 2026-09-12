"""NeuroCode local API. docs/ARCHITECTURE.md describes the whole shape.

The app factory wires the store (SQLite with numbered migrations), the secrets file, accounts and
roles, the AI gateway and the event bus into one context, puts a CSRF guard in front, and mounts one
router per area. Every change is pushed to open tabs over Server-Sent Events.

Run from the repo root (it binds to localhost only):
    uv run --project server uvicorn app.main:create_app --factory --app-dir server --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .ai.gateway import Gateway
from .auth import COOKIE, Accounts
from .context import Ctx
from .db import Store
from .events import Bus
from .rbac import Rbac
from .routes import admin, code, knowledge, platform, runs, sessions, system, work
from .routes import ai as ai_routes
from .routes import auth as auth_routes
from .secrets import Secrets

SERVER_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = SERVER_DIR / "neurocode.db"
ENV_FILE = SERVER_DIR / ".env"


def load_env(path: Path) -> None:
    """KEY=VALUE lines from server/.env. The real environment always wins. No dependency needed."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def create_app(db_path: str | None = None, env_file: Path | None = ENV_FILE) -> FastAPI:
    # NEUROCODE_ENV_FILE points at another .env; set it empty to read none (the e2e run does, so a key
    # on this machine never reaches a test).
    override = os.environ.get("NEUROCODE_ENV_FILE")
    if override is not None:
        env_file = Path(override) if override else None
    if env_file is not None:
        load_env(env_file)
    path = db_path or os.environ.get("NEUROCODE_DB", str(DEFAULT_DB))
    store = Store(path)
    secrets = Secrets(Path(os.environ.get("NEUROCODE_SECRETS") or Path(path).with_name("secrets.json")))
    rbac = Rbac(store)
    c = Ctx(store=store, bus=Bus(), secrets=secrets, rbac=rbac, accounts=Accounts(store, rbac), gateway=Gateway(store, secrets))

    app = FastAPI(title="NeuroCode API", version="0.3.0")
    app.state.ctx = c
    app.state.store = store
    app.state.bus = c.bus
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # A change that rides on the session cookie must also carry X-NC-Client. A page on another site
        # can make the browser send the cookie, but it cannot add that header without a CORS preflight,
        # and the preflight only passes for localhost.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.cookies.get(COOKIE) \
                and not request.headers.get("x-nc-client"):
            return JSONResponse({"detail": "Missing the X-NC-Client header"}, status_code=403)
        return await call_next(request)

    for module in (system, auth_routes, admin, work, knowledge, platform, code, runs, sessions, ai_routes):
        app.include_router(module.router)
    return app
