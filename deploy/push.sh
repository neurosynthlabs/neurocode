#!/usr/bin/env bash
# From this machine: build the web app, copy the stack to the server, and (re)start it there. The first push
# also writes the server's .env with random secrets and prints the setup token the first sign-up asks for.
#
#   deploy/push.sh ubuntu@<ip>                 (key: ~/.ssh/neurocode_oci, or NEUROCODE_SSH_KEY)
set -euo pipefail
HOST=${1:?usage: deploy/push.sh user@host}
KEY=${NEUROCODE_SSH_KEY:-$HOME/.ssh/neurocode_oci}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"
cd "$ROOT"

npm run build
rsync -az --delete -e "$SSH" --exclude .env deploy/ "$HOST:/opt/neurocode/deploy/"
rsync -az --delete -e "$SSH" dist/ "$HOST:/opt/neurocode/dist/"
rsync -az --delete -e "$SSH" --exclude-from=server/.dockerignore server/ "$HOST:/opt/neurocode/server/"

$SSH "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/neurocode/deploy
if [ ! -f .env ]; then
  rand() { head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32; }
  sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand)|" \
      -e "s|^NEUROCODE_DB_PASSWORD=.*|NEUROCODE_DB_PASSWORD=$(rand)|" \
      -e "s|^NEUROCODE_SETUP_TOKEN=.*|NEUROCODE_SETUP_TOKEN=$(rand)|" .env.template > .env
  chmod 600 .env
  echo "Wrote .env. The setup wizard will ask for this token once: $(grep '^NEUROCODE_SETUP_TOKEN=' .env | cut -d= -f2)"
fi
docker compose up -d --build
docker compose ps
REMOTE
