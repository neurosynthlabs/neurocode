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

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent

LOCAL_DB = "postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode"
# The same server, its own database — spelled out rather than derived from the line above. Replacing
# "/neurocode" in that URL matches the *user name* first, which silently asked Postgres for a role
# that does not exist. Two plain constants cannot do that.
LOCAL_TEST_DB = "postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode_test"

#: The origins a browser may carry the session cookie from when the API answers on this machine only.
LOCAL_ORIGINS = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"
#: And when it has been told to answer on the LAN: the same, plus the addresses a private network
#: actually hands out — RFC 1918's three ranges, IPv6 loopback, and the `.local` names Bonjour
#: advertises a Mac under. Anchored at both ends, so `192.168.1.5.example.com` is not one of them.
PRIVATE_ORIGINS = (
    r"^https?://(localhost|127\.0\.0\.1|\[::1\]"
    r"|10(\.\d{1,3}){3}"
    r"|192\.168(\.\d{1,3}){2}"
    r"|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}"
    r"|[A-Za-z0-9][A-Za-z0-9-]*\.local)(:\d+)?$"
)


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
    #: The Workbench's machine access — browsing folders, reading and saving files, a terminal, running and
    #: debugging. On for a local install; a server reachable from the internet sets it false unless its
    #: owner means to hand a shell to whoever signs in as an Owner.
    machine_access: bool = True
    #: Set on a server reachable from the internet: the setup wizard then asks for it before anyone may create
    #: the first Owner, so whoever finds the address first cannot claim the workspace. Empty on a laptop.
    setup_token: str = ""
    #: The session cookie is sent over HTTPS only. On for anything served through TLS; off for plain
    #: http://localhost, where a Secure cookie would never be sent back.
    cookie_secure: bool = False
    #: Where the folder browser may go, separated by ':'. `~` is the home of the account the API runs as.
    machine_roots: str = "~"

    #: The fence around every command the runtime runs — the project's own tests, its checks, a custom
    #: tool. On means "use whatever this machine has" (macOS Seatbelt, bubblewrap or unshare on Linux);
    #: a machine with none says so on the run screen rather than pretending. This is a floor: turning it
    #: off here cannot be undone from a screen, because the fence protects the machine this API runs on,
    #: and that is the deployment's call, not the workspace's.
    sandbox: bool = True
    #: Whether a sandboxed command may reach the network. Off, because a test suite has no business
    #: posting anywhere; a suite that installs packages needs it, and Admin → Workspace is where a person
    #: says so knowingly. This is only the starting answer — the workspace's own wins once it is set.
    sandbox_network: bool = False

    #: Jupyter kernels the Workbench's notebooks run on. Each is a process holding whatever the notebook
    #: loaded (a dataframe, a model on the GPU), so the server keeps a ceiling on how many run at once, in
    #: all and per person, and shuts one down once nobody has had its notebook open for `kernel_idle_minutes`
    #: (0 keeps them until the notebook is closed or the API stops).
    kernels_max: int = Field(default=8, ge=0, le=64)
    kernels_per_person: int = Field(default=4, ge=1, le=64)
    kernel_idle_minutes: int = Field(default=60, ge=0, le=7 * 24 * 60)
    #: How long a kernel is given to start and answer its first request: a large environment imports slowly.
    kernel_start_seconds: int = Field(default=60, ge=5, le=600)

    #: The routines' scheduler: a loop in the API's lifespan that fires due routines every thirty seconds.
    #: Several API processes may all run it — an advisory lock lets only one claim a routine — and the
    #: tests switch it off, so nothing fires on its own while a test is looking.
    scheduler: bool = True

    # ── the web layer ────────────────────────────────────────────
    #: Origins allowed to carry the session cookie. Local by default; add a domain to host it.
    cors_origin_regex: str = LOCAL_ORIGINS
    #: Answer on this machine's network address as well as on 127.0.0.1 — which is what lets a phone
    #: or a second laptop on the same Wi-Fi reach this API at all. Off, and off is the right default:
    #: turning it on hands the workspace, and on a machine with machine access the Workbench's shell,
    #: to everyone on that network who has an account or a token. It changes exactly two things —
    #: `bind_host` below, and the allowed origins (`_widen_origins_for_the_lan`) — and `reach_notice()`
    #: is what it means, in sentences.
    listen_on_lan: bool = False
    session_days: int = Field(default=14, ge=1, le=365)
    #: Five wrong passwords lock an account for this long.
    lockout_seconds: int = Field(default=30, ge=0)
    login_attempts: int = Field(default=5, ge=1)

    # ── how long history is kept ─────────────────────────────────
    #: These tables only ever grow: a run writes hundreds of log lines, a five-minute routine fires a
    #: hundred thousand times a year, and nothing ever deleted a row of any of them. `0` means keep
    #: everything, which is the honest default for the two the product calls history rather than noise
    #: — the usage ledger behind every budget, and the audit log, which is append-only in the database
    #: itself and can therefore never be pruned at all.
    run_log_days: int = Field(default=90, ge=0, le=3650)
    activity_days: int = Field(default=365, ge=0, le=3650)
    ledger_days: int = Field(default=0, ge=0, le=3650)
    fire_days: int = Field(default=365, ge=0, le=3650)
    recall_days: int = Field(default=365, ge=0, le=3650)
    login_attempt_days: int = Field(default=90, ge=0, le=3650)
    #: Rows per DELETE. Small enough that no statement holds a lock long enough for a person to notice,
    #: large enough that a year of arrears still clears in one pass.
    prune_rows: int = Field(default=5_000, ge=100, le=100_000)
    #: Prune once a day from the scheduler, as well as from the button on Admin → Database.
    prune_daily: bool = True

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

    @model_validator(mode="after")
    def _widen_origins_for_the_lan(self) -> Settings:
        """Listening on the LAN and refusing every LAN origin is a server nobody can sign in to.

        The browser sends `Origin: http://192.168.1.14:5180`, the default regex allows only localhost,
        and the sign-in fails with a CORS error rather than a sentence — the worst kind of refusal,
        because it appears in the console and not on the screen. So turning the switch on widens the
        allowed origins to the private ranges and `.local` names, which is exactly the set of places
        the API has just become reachable from, and no wider.

        A `NEUROCODE_CORS_ORIGIN_REGEX` someone wrote themselves is never touched: they have already
        said which origins they mean, and quietly adding to that list would be the API deciding
        something its operator had decided.
        """
        if self.listen_on_lan and self.cors_origin_regex == LOCAL_ORIGINS:
            self.cors_origin_regex = PRIVATE_ORIGINS
        return self

    @property
    def bind_host(self) -> str:
        """The address the API is told to listen on: this machine only, or every interface on it.

        Read by `scripts/dev.sh` when it starts uvicorn, and said aloud at start-up, so the switch and
        the socket can never disagree about which one is in force.
        """
        return "0.0.0.0" if self.listen_on_lan else "127.0.0.1"  # noqa: S104 — the point of the switch

    def reach_notice(self) -> list[str]:
        """What listening on the LAN means, in sentences a person can act on — logged at start-up.

        Empty when the API is bound to this machine, because then there is nothing to warn anybody
        about. Each sentence is one true consequence of the switch being on, in the order that
        matters: who can reach it, what the cookie is crossing, and the one combination that hands
        out a shell.
        """
        if not self.listen_on_lan:
            return []
        said = [
            f"Listening on {self.bind_host}: everyone on this network can reach this API, and anyone "
            "with an account or a personal access token can sign in to your workspace.",
            "Over plain http the session cookie crosses the network in the clear. Put it behind HTTPS "
            "(deploy/ has a Caddy that does) before using this anywhere you do not trust.",
        ]
        if self.machine_access:
            said.append(
                "Machine access is on as well, so an Owner signing in from another device gets the "
                "Workbench: this machine's files, terminals and debugger. Set "
                "NEUROCODE_MACHINE_ACCESS=false unless that is what you meant.")
        return said


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
