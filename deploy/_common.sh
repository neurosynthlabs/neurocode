# shellcheck shell=bash
# What every deploy script needs: which server, how to reach it, and how to say things plainly.
# Sourced, never run: `. "$(dirname "$0")/_common.sh"`.
#
# The server is named once — `deploy/push.sh ubuntu@1.2.3.4` — and remembered in deploy/.host, so every
# command after that is just `deploy/status.sh`. An explicit argument always wins over what is remembered.
set -euo pipefail

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC2034  # the scripts that source this use it
ROOT=$(cd "$DEPLOY_DIR/.." && pwd)
HOST_FILE="$DEPLOY_DIR/.host"
KEY=${NEUROCODE_SSH_KEY:-$HOME/.ssh/neurocode_oci}

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# The server, from the argument, the environment, or the last one used.
resolve_host() {
  local given=${1:-${NEUROCODE_HOST:-}}
  if [ -z "$given" ] && [ -f "$HOST_FILE" ]; then given=$(cat "$HOST_FILE"); fi
  [ -n "$given" ] || die "Which server? Give it once: deploy/push.sh ubuntu@<ip> — it is remembered after that."
  case "$given" in *@*) HOST=$given ;; *) HOST=ubuntu@$given ;; esac   # a bare IP means the default account
  printf '%s' "$HOST" > "$HOST_FILE"
}

# `accept-new` trusts a server's key the first time and pins it after: a changed key then stops the deploy,
# which is the point of knowing it at all.
ssh_to() { ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$HOST" "$@"; }

# Run a script (on stdin) on the server as one unit of work, under a lock, so two deploys never interleave.
# Anything passed here is set in its environment: `remote "DOMAIN_WANTED=example.com" <<'EOF'`.
remote() { ssh_to "$* flock -w 600 /tmp/neurocode-deploy.lock bash -s"; }

# rsync over the same key, without a second copy of the ssh flags.
copy() { rsync -az --delete -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" "$@"; }

compose() { ssh_to "cd /opt/neurocode/deploy && docker compose $*"; }

# What the server serves itself under, read from its own .env — the one place that knows.
server_domain() { ssh_to "grep -m1 '^DOMAIN=' /opt/neurocode/deploy/.env | cut -d= -f2" 2>/dev/null | tr -d '\r'; }
