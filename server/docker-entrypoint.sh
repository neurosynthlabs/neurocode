#!/bin/sh
# Migrate, then serve. The schema is only ever Alembic's; a container that cannot migrate does not serve.
set -eu
mkdir -p "$NEUROCODE_REPOS_DIR" "$NEUROCODE_WORKTREES_DIR" "$NEUROCODE_BACKUPS_DIR"
alembic upgrade head
# Behind Caddy on a private network: the forwarded client address is the real one, so sign-in lockout and the
# audit log see who asked, not the proxy.
exec uvicorn app.api.app:create_api --factory --host 0.0.0.0 --port 8787 \
  --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 5
