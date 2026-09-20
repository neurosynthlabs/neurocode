#!/usr/bin/env bash
# Serve the same server under another name — from <ip>.sslip.io to your own domain, or between domains.
#
#   deploy/domain.sh eurex.dev
#
# Point the name at the server first (an A record → its IP, and with Cloudflare, "DNS only" rather than the
# orange cloud: a proxied name answers the certificate challenge itself, and Caddy never gets one of its own).
# shellcheck source=deploy/_common.sh
. "$(dirname "$0")/_common.sh"
NAME=${1:?usage: deploy/domain.sh <domain>}
resolve_host "${2:-}" >/dev/null

IP=${HOST#*@}
resolved=$(dig +short "$NAME" A | tail -1)
[ -n "$resolved" ] || die "$NAME does not resolve yet. Add an A record → $IP and try again (DNS can take a few minutes)."
[ "$resolved" = "$IP" ] || note "warning: $NAME resolves to $resolved, and the server is $IP — if that is Cloudflare's proxy, switch the record to DNS only."

bold "Telling the server it is $NAME"
remote "NAME='$NAME'" <<'REMOTE'
set -euo pipefail
cd /opt/neurocode/deploy
sed -i "s|^DOMAIN=.*|DOMAIN=$NAME|" .env
docker compose up -d web
REMOTE

bold "Waiting for the certificate"
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$NAME/api/health" || true)
  [ "$code" = 200 ] && { note "https://$NAME/api/health → 200"; bold "Live: https://$NAME"; exit 0; }
  sleep 5
done
die "https://$NAME did not answer. deploy/logs.sh web shows what Caddy tried."
