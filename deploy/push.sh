#!/usr/bin/env bash
# A release: build the web app here, copy this tree to the server, build the API image there, start the stack
# and prove it answers — and if it does not, put the version that was running back.
#
#   deploy/push.sh ubuntu@<ip>      the first time (the server is remembered in deploy/.host afterwards)
#   deploy/push.sh                  every release after that
#
# The first push writes the server's .env with random secrets and prints the setup token the first sign-up
# asks for. The name it is served under comes from NEUROCODE_DOMAIN on that first push; with no domain of your
# own yet, <ip>.sslip.io resolves to the machine and Let's Encrypt issues for it, so even the first deploy is
# real HTTPS rather than a warning page:
#
#   NEUROCODE_DOMAIN=1.2.3.4.sslip.io deploy/push.sh ubuntu@1.2.3.4
#
# To move it to your own name later: deploy/domain.sh eurex.dev.
. "$(dirname "$0")/_common.sh"
resolve_host "${1:-}" >/dev/null

bold "Building the web app"
(cd "$ROOT" && npm run build >/dev/null) || die "The web app did not build — nothing was sent."
note "dist/ is $(du -sh "$ROOT/dist" | cut -f1)"

bold "Copying to $HOST"
copy --exclude .env --exclude .host "$DEPLOY_DIR/" "$HOST:/opt/neurocode/deploy/"
copy "$ROOT/dist/" "$HOST:/opt/neurocode/dist/"
copy --exclude-from="$ROOT/server/.dockerignore" "$ROOT/server/" "$HOST:/opt/neurocode/server/"

bold "Building and starting it there"
# Everything below runs on the server, as one locked unit: keep the image that is running as `previous`, build
# the new one, start it, and wait for the API's own health check. A release that cannot answer is not a
# release: the previous image goes back and the deploy fails loudly, with the lines that explain why.
remote "DOMAIN_WANTED='${NEUROCODE_DOMAIN:-}'" <<'REMOTE' \
  || die "The release did not come up. The previous version is running; the log above says why."
set -euo pipefail
cd /opt/neurocode/deploy

if [ ! -f .env ]; then
  rand() { head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32; }
  sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand)|" \
      -e "s|^NEUROCODE_DB_PASSWORD=.*|NEUROCODE_DB_PASSWORD=$(rand)|" \
      -e "s|^NEUROCODE_SETUP_TOKEN=.*|NEUROCODE_SETUP_TOKEN=$(rand)|" \
      ${DOMAIN_WANTED:+-e "s|^DOMAIN=.*|DOMAIN=$DOMAIN_WANTED|"} .env.template > .env
  chmod 600 .env
  echo "First run: wrote .env for $(grep '^DOMAIN=' .env | cut -d= -f2)."
  echo "SETUP TOKEN (asked once, when you create the first Owner): $(grep '^NEUROCODE_SETUP_TOKEN=' .env | cut -d= -f2)"
fi

docker image inspect neurocode-api:latest >/dev/null 2>&1 && docker tag neurocode-api:latest neurocode-api:previous
docker compose build api
docker compose up -d --remove-orphans

# The API runs its migrations at start, so "healthy" here means the schema is at head and it answers. Twenty
# tries, five seconds apart: a cold Postgres and a long migration both fit inside that.
for i in $(seq 1 20); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || echo starting)
  [ "$state" = healthy ] && break
  [ "$i" = 20 ] && {
    echo "--- the API never became healthy ---"
    docker compose logs --tail 60 api
    if docker image inspect neurocode-api:previous >/dev/null 2>&1; then
      echo "--- putting the previous image back ---"
      docker tag neurocode-api:previous neurocode-api:latest
      docker compose up -d --no-build api
    fi
    exit 1
  }
  sleep 5
done

docker image prune -f >/dev/null 2>&1 || true      # the layers no tag points at any more
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
REMOTE

DOMAIN=$(server_domain)
bold "Checking it from here"
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$DOMAIN/api/health" || true)
  [ "$code" = 200 ] && break
  # The first deploy waits for Let's Encrypt, which needs port 80 to reach the server and the name to resolve.
  [ "$i" = 1 ] && note "waiting for HTTPS on $DOMAIN (the certificate is issued on the first request)…"
  [ "$i" = 30 ] && die "https://$DOMAIN/api/health answered $code. Check DNS and ports 80/443: deploy/status.sh"
  sleep 5
done
note "https://$DOMAIN/api/health → 200"
bold "Live: https://$DOMAIN"
