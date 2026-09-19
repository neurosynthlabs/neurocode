#!/usr/bin/env bash
# NeuroCode local stack: the web app (Vite) and the local API (FastAPI + Postgres), together.
#   ./scripts/dev.sh start | stop | restart | status | logs [web|api]
# Ports: web 5180 (5173 is usually taken by another project here), API 8787.
# NC_API=0 starts the web app alone, for working on a screen's layout: it holds no data of its own, so it
# shows "Not connected" until an API answers on $NC_API_PORT.
set -uo pipefail
cd "$(dirname "$0")/.."

WEB_PORT="${NC_PORT:-5180}"
API_PORT="${NC_API_PORT:-8787}"
export NC_API_PORT="$API_PORT"   # vite.config.ts points its /api proxy here

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
    api) uv run --project server uvicorn app.api.app:create_api --factory --app-dir server --host 127.0.0.1 --port "$API_PORT" --timeout-graceful-shutdown 5 ;;
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
  local s=$1 p pids
  if ! alive "$s"; then
    rm -f "$(pidf "$s")"
    if [ -n "$(owner "$s")" ]; then printf '▸ %s: port %s is held by pid %s, which this script did not start. Left alone.\n' "$s" "$(port "$s")" "$(owner "$s")"
    else printf '▸ %s not running\n' "$s"; fi
    return
  fi
  p=$(cat "$(pidf "$s")")
  pids="$p $(descendants "$p")"   # npm → sh → node and uv → uvicorn: take the whole tree
  kill $pids 2>/dev/null
  sleep 0.6
  kill -9 $pids 2>/dev/null
  rm -f "$(pidf "$s")"
  printf '\033[1;32m✓\033[0m %s stopped (pid %s)\n' "$s" "$p"
}

status_one() {
  local s=$1
  if alive "$s"; then printf '● %-3s running   pid %s   %s\n' "$s" "$(cat "$(pidf "$s")")" "$(url "$s")"
  elif [ -n "$(owner "$s")" ]; then printf '● %-3s port %s held by pid %s (not ours)\n' "$s" "$(port "$s")" "$(owner "$s")"
  else printf '○ %-3s stopped\n' "$s"; fi
}

start() {
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
  status)  status_one web; status_one api ;;
  *) printf 'usage: %s {start|stop|restart|status|logs [web|api]}\n' "$0"; exit 1 ;;
esac
