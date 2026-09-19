"""Every knob this API has, in one typed place.

Configuration used to be `os.environ.get` scattered through a dozen modules, which meant nobody could
answer "what does this deployment actually do?" without reading all of them. Now it is one object,
validated once at start-up, with defaults that make a fresh clone run on a laptop with no setup.

Environment wins over defaults, and `NEUROCODE_` prefixes everything: `NEUROCODE_DATABASE_URL`,
`NEUROCODE_LOG_LEVEL`, and so on. `server/.env` is read if it is there.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent

LOCAL_DB = "postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode"
# The same server, its own database — spelled out rather than derived from the line above. Replacing
# "/neurocode" in that URL matches the *user name* first, which silently asked Postgres for a role
# that does not exist. Two plain constants cannot do that.
LOCAL_TEST_DB = "postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEUROCODE_", env_file=SERVER_DIR / ".env",
                                      env_file_encoding="utf-8", extra="ignore")

    # ── the database ─────────────────────────────────────────────
    database_url: str = LOCAL_DB
    test_database_url: str = LOCAL_TEST_DB
    #: Connections kept open. A laptop needs few; a shared box wants more.
    pool_size: int = Field(default=5, ge=1, le=100)
    pool_overflow: int = Field(default=10, ge=0, le=100)
    #: Recycle a connection after this long, so a laptop waking from sleep never serves a dead socket.
    pool_recycle_seconds: int = Field(default=900, ge=60)
    echo_sql: bool = False

    # ── where things live on disk ────────────────────────────────
    secrets_path: Path = SERVER_DIR / "secrets.json"
    #: Where a workspace from the SQLite version was kept. Nothing reads it any more except
    #: scripts/import-sqlite.py, which carries one across.
    legacy_sqlite_path: Path = SERVER_DIR / "neurocode.db"
    backups_dir: Path = SERVER_DIR / "backups"
    #: Where pg_dump lives, when it is not on PATH. Homebrew's postgresql@16 is keg-only, so on the most
    #: common Mac install it is not — which is why the usual install locations are searched as well.
    pg_bin_dir: Path | None = None
    #: Where each run's git worktree is made, one folder per project. The runtime reads this setting —
    #: it used to keep its own copy of the path, so a deployment that moved it moved only the screen.
    worktrees_dir: Path = SERVER_DIR / ".worktrees"
    #: Where a project onboarded from a git URL is cloned. `.repos` is where clones have always gone.
    repos_dir: Path = SERVER_DIR / ".repos"

    # ── the web layer ────────────────────────────────────────────
    #: Origins allowed to carry the session cookie. Local by default; add a domain to host it.
    cors_origin_regex: str = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"
    session_days: int = Field(default=14, ge=1, le=365)
    #: Five wrong passwords lock an account for this long.
    lockout_seconds: int = Field(default=30, ge=0)
    login_attempts: int = Field(default=5, ge=1)

    # ── models ───────────────────────────────────────────────────
    #: auto | free | local | rules | a lane id. The environment pinning it wins over the workspace setting.
    compiler: str = ""
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _asyncpg(cls, v: str) -> str:
        """A plain postgres:// URL (what every host hands out) is upgraded to the async driver."""
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def sync_database_url(self) -> str:
        """The same database for tools that cannot speak async — Alembic's offline mode, psql URLs."""
        return self.database_url.replace("+asyncpg", "")

    @property
    def blocking_database_url(self) -> str:
        """The same database again, for the one component that is blocking by nature: the AI gateway
        waits on model providers from a worker thread, so it speaks psycopg rather than asyncpg."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
