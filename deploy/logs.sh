#!/usr/bin/env bash
# The server's logs, followed. `api` unless you name another service.
#
#   deploy/logs.sh                 the API, from the last 100 lines
#   deploy/logs.sh web 500         Caddy, from the last 500
#   deploy/logs.sh db
. "$(dirname "$0")/_common.sh"
resolve_host "" >/dev/null
SERVICE=${1:-api}
LINES=${2:-100}
exec ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -t "$HOST" \
  "cd /opt/neurocode/deploy && docker compose logs -f --tail $LINES $SERVICE"
