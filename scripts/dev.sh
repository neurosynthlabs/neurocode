#!/usr/bin/env bash
# NeuroCode local stack: the web app (Vite) and the local API (FastAPI + Postgres), together.
#   ./scripts/dev.sh start | stop | restart | status | logs [web|api]
# Ports: web 5180 (5173 is usually taken by another project here), API 8787.
# NC_API=0 starts the web app alone, for working on a screen's layout: it holds no data of its own, so it
# shows "Not connected" until an API answers on $NC_API_PORT.
set -uo pipefail
# Everything below is relative to the repository, and a shell that could not get there would start the wrong
# things in the wrong place.
cd "$(dirname "$0")/.." || { echo "cannot find the repository from $0" >&2; exit 1; }

WEB_PORT="${NC_PORT:-5180}"
API_PORT="${NC_API_PORT:-8787}"
export NC_API_PORT="$API_PORT"   # vite.config.ts points its /api proxy here

# Reaching this stack from a phone or a second laptop on the same Wi-Fi. One switch, because half of it
# is useless: a web app on the LAN whose API still answers only on 127.0.0.1 loads and then says it is
# not connected, for ever. NEUROCODE_LISTEN_ON_LAN is the API's own setting (server/app/settings.py,
# which also widens the allowed origins to the private ranges); NC_LAN is what vite.config.ts reads.
# Off unless it is set, and the API prints what it exposes every time it starts with it on.
case "$(printf '%s' "${NEUROCODE_LISTEN_ON_LAN:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes) API_HOST=0.0.0.0; export NC_LAN=1 ;;
  *)          API_HOST=127.0.0.1 ;;
esac

pidf()  { echo "/tmp/neurocode-$1.pid"; }
logf()  { echo "/tmp/neurocode-$1.log"; }
port()  { if [ "$1" = web ]; then echo "$WEB_PORT"; else echo "$API_PORT"; fi; }
url()   { if [ "$1" = web ]; then echo "http://localhost:$WEB_PORT/"; else echo "http://127.0.0.1:$API_PORT/health"; fi; }
alive() { [ -f "$(pidf "$1")" ] && kill -0 "$(cat "$(pidf "$1")")" 2>/dev/null; }
owner() { lsof -nP -iTCP:"$(port "$1")" -sTCP:LISTEN -t 2>/dev/null | head -1; }
descendants() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do echo "$c"; descendants "$c"; done; }

launch() {
  case $1 in
    web) npm run dev -- --port "$WEB_PORT" --strictPort ;;
    api) uv run --project server uvicorn app.api.app:create_api --factory --app-dir server --host "$API_HOST" --port "$API_PORT" --timeout-graceful-shutdown 5 ;;
  esac
}

start_one() {
  local s=$1
  if alive "$s"; then printf '▸ %s already running   pid %s   %s\n' "$s" "$(cat "$(pidf "$s")")" "$(url "$s")"; return; fi
  if [ -n "$(owner "$s")" ]; then
    printf '✗ %s: port %s is taken by pid %s (%s)\n' "$s" "$(port "$s")" "$(owner "$s")" "$(ps -p "$(owner "$s")" -o comm= 2>/dev/null)"
    return 1
  fi
  launch "$s" >"$(logf "$s")" 2>&1 &
  echo $! >"$(pidf "$s")"
  # the API's first start also installs its dependencies, so give it time
  for _ in $(seq 1 120); do curl -sf -o /dev/null "$(url "$s")" && break; sleep 0.25; done
  if curl -sf -o /dev/null "$(url "$s")"; then
    printf '\033[1;32m✓ %s\033[0m  %s   pid %s   logs: %s\n' "$s" "$(url "$s")" "$(cat "$(pidf "$s")")" "$(logf "$s")"
  else
    printf '✗ %s failed to start. Last lines:\n' "$s"; tail -15 "$(logf "$s")"; stop_one "$s" >/dev/null; return 1
  fi
}

stop_one() {
  local s=$1 p
  if ! alive "$s"; then
    rm -f "$(pidf "$s")"
    if [ -n "$(owner "$s")" ]; then printf '▸ %s: port %s is held by pid %s, which this script did not start. Left alone.\n' "$s" "$(port "$s")" "$(owner "$s")"
    else printf '▸ %s not running\n' "$s"; fi
    return
  fi
  p=$(cat "$(pidf "$s")")
  # npm → sh → node and uv → uvicorn: take the whole tree. A list of pids is a list of words on purpose.
  # shellcheck disable=SC2046,SC2086
  set -- $p $(descendants "$p")
  kill "$@" 2>/dev/null
  sleep 0.6
  kill -9 "$@" 2>/dev/null
  rm -f "$(pidf "$s")"
  printf '\033[1;32m✓\033[0m %s stopped (pid %s)\n' "$s" "$p"
}

status_one() {
  local s=$1
  if alive "$s"; then printf '● %-3s running   pid %s   %s\n' "$s" "$(cat "$(pidf "$s")")" "$(url "$s")"
  elif [ -n "$(owner "$s")" ]; then printf '● %-3s port %s held by pid %s (not ours)\n' "$s" "$(port "$s")" "$(owner "$s")"
  else printf '○ %-3s stopped\n' "$s"; fi
}

# This machine's address on the network, for the line below. macOS answers with ipconfig, Linux with
# hostname -I; a machine that answers with neither is simply not told an address, which is better than
# being told a wrong one.
lan_address() {
  ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null \
    || { hostname -I 2>/dev/null | awk '{print $1}'; }
}

# Who can reach this stack — printed on start and on status, because "it is only on my laptop" is the
# assumption every one of these ports is otherwise read with.
reach() {
  if [ "$API_HOST" = 0.0.0.0 ]; then
    addr=$(lan_address)
    printf '▸ NEUROCODE_LISTEN_ON_LAN: the web app and the API answer on every interface (0.0.0.0).\n'
    if [ -n "$addr" ]; then
      printf '  On the same Wi-Fi: http://%s:%s — and so is anyone else on this network.\n' "$addr" "$WEB_PORT"
    fi
  else
    printf '▸ this machine only (127.0.0.1). NEUROCODE_LISTEN_ON_LAN=true opens it to the network.\n'
  fi
}

start() {
  reach
  if [ "${NC_API:-1}" = 0 ]; then
    printf '▸ NC_API=0: web app only. It says "Not connected" until an API answers on %s\n' "$API_PORT"
  elif ! command -v uv >/dev/null 2>&1; then
    printf '▸ uv not found: web app only, and it holds no data without the API. Install uv: https://docs.astral.sh/uv/\n'
  elif ! trouble=$(uv run --project server --directory server python -m app.data.check); then
    # Asked before uvicorn, because no server, no database and no migrations all look the same in
    # its log — and each has a different one-line fix, which this prints.
    printf '✗ api: %s\n' "$trouble"
    printf '▸ starting the web app anyway. It shows "Not connected" and how to fix it until the API answers.\n'
  else
    start_one api || printf '▸ starting the web app anyway. It shows "Not connected" and how to fix it until the API answers.\n'
  fi
  start_one web
}

case "${1:-status}" in
  start)   start ;;
  stop)    stop_one web; stop_one api ;;
  restart) stop_one web; stop_one api; sleep 0.4; start ;;
  logs)    tail -f "$(logf "${2:-web}")" ;;
  status)  status_one web; status_one api; reach ;;
  *) printf 'usage: %s {start|stop|restart|status|logs [web|api]}\n' "$0"; exit 1 ;;
esac
