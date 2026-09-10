#!/usr/bin/env bash
# NeuroCode dev server.
#   ./scripts/dev.sh start | stop | restart | status | logs
# Port defaults to 5180 (5173 is usually taken by another project here).
set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${NC_PORT:-5180}"
LOG=/tmp/neurocode-dev.log
PIDF=/tmp/neurocode-dev.pid

alive() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
owner() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1; }

start() {
  if alive; then printf '▸ already running — pid %s  http://localhost:%s/\n' "$(cat "$PIDF")" "$PORT"; return; fi
  if [ -n "$(owner)" ]; then
    printf '✗ port %s is taken by pid %s (%s)\n' "$PORT" "$(owner)" "$(ps -p "$(owner)" -o comm= 2>/dev/null)"
    printf '  use a different port:  NC_PORT=5190 %s start\n' "$0"; return 1
  fi
  npm run dev -- --port "$PORT" --strictPort >"$LOG" 2>&1 &
  echo $! >"$PIDF"
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null "http://localhost:$PORT/" && break
    sleep 0.25
  done
  if curl -sf -o /dev/null "http://localhost:$PORT/"; then
    printf '\033[1;32m✓ dev\033[0m  http://localhost:%s/   pid %s   logs: %s\n' "$PORT" "$(cat "$PIDF")" "$LOG"
  else
    printf '✗ failed to start — last lines:\n'; tail -15 "$LOG"; rm -f "$PIDF"; return 1
  fi
}

stop() {
  local p; p="$( [ -f "$PIDF" ] && cat "$PIDF" || owner )"
  [ -z "${p:-}" ] && { printf '▸ not running\n'; rm -f "$PIDF"; return; }
  pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null
  sleep 0.6; kill -9 "$p" 2>/dev/null
  rm -f "$PIDF"
  printf '\033[1;32m✓\033[0m stopped (pid %s)\n' "$p"
}

case "${1:-status}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; sleep 0.4; start ;;
  logs)    tail -f "$LOG" ;;
  status)
    if alive; then printf '● running   pid %s   http://localhost:%s/\n' "$(cat "$PIDF")" "$PORT"
    elif [ -n "$(owner)" ]; then printf '● port %s held by pid %s (not ours)\n' "$PORT" "$(owner)"
    else printf '○ stopped\n'; fi ;;
  *) printf 'usage: %s {start|stop|restart|status|logs}\n' "$0"; exit 1 ;;
esac
