# The NeuroCode API as a container: FastAPI under uvicorn, Alembic migrations on start, git and pg_dump for
# the runtime and the backups, and Node and make so a project's own tests can run in its worktrees. Built for
# whichever architecture the host has — arm64 on an Oracle Ampere VM or an Apple Silicon Mac, amd64 elsewhere.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# pg_dump must match the server's major version (16), which Debian's own packages do not ship: PGDG's apt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg git make openssh-client \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 nodejs npm \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# The whole app imported with only the runtime dependencies, as it will run. The tests install the dev group,
# so a module that needs a package only the tests have passes every one of them and fails here, at build,
# rather than as a release that never comes up.
RUN .venv/bin/python -c "import app.api.app"

# The API never runs as root: a plain user that owns only /data, where everything it writes lives.
RUN useradd --create-home --uid 10001 neurocode && mkdir -p /data && chown neurocode:neurocode /data \
 && chmod 0755 /usr/local/bin/docker-entrypoint.sh
USER neurocode

ENV PATH="/app/.venv/bin:$PATH" \
    NEUROCODE_PG_BIN_DIR=/usr/lib/postgresql/16/bin \
    NEUROCODE_REPOS_DIR=/data/repos \
    NEUROCODE_WORKTREES_DIR=/data/worktrees \
    NEUROCODE_BACKUPS_DIR=/data/backups \
    NEUROCODE_SECRETS_PATH=/data/secrets.json

EXPOSE 8787
ENTRYPOINT ["docker-entrypoint.sh"]
