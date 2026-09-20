#!/usr/bin/env bash
# What the server is doing, in one screen: the containers and their health, what the API says about itself,
# the certificate, the machine's disk and memory, and when it was last backed up.
#
#   deploy/status.sh            the server deploy/push.sh last used
#   deploy/status.sh ubuntu@<ip>
# shellcheck source=deploy/_common.sh
. "$(dirname "$0")/_common.sh"
resolve_host "${1:-}" >/dev/null

DOMAIN=$(server_domain)
bold "$HOST · $DOMAIN"

bold "Containers"
compose "ps --format 'table {{.Service}}\t{{.Status}}\t{{.Image}}'"

bold "The API"
health=$(curl -s --max-time 10 "https://$DOMAIN/api/health" || true)
if [ -n "$health" ]; then
  printf '%s' "$health" | python3 -c 'import json,sys
h = json.load(sys.stdin)
print(f"  ok: {h[\"ok\"]} · database {h[\"db\"]}")
print("  rows:", ", ".join(f"{k} {v}" for k, v in sorted(h.get("counts", {}).items())))
c = h.get("compiler", {})
print(f"  model: {c.get(\"provider\")} {c.get(\"model\")} · {c.get(\"lanes\", 0)} lane(s) ready")' 2>/dev/null || note "$health"
else
  note "https://$DOMAIN/api/health did not answer"
fi

bold "Certificate"
# `openssl s_client` speaks to the name the certificate is for, so this is what a browser would be shown.
echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
  | openssl x509 -noout -issuer -dates 2>/dev/null | sed 's/^/  /' || note "no certificate yet"

bold "The machine"
# shellcheck disable=SC2016  # these expand on the server, not here
ssh_to 'printf "  uptime:%s\n" "$(uptime | sed "s/.*up //;s/,.*load/, load/")"; \
  printf "  memory: %s\n" "$(free -h | awk "/^Mem:/ {print \$3 \" used of \" \$2}")"; \
  printf "  disk:   %s\n" "$(df -h / | awk "NR==2 {print \$3 \" used of \" \$2 \" (\" \$5 \")\"}")"; \
  printf "  docker: %s\n" "$(docker system df --format "{{.Type}} {{.Size}}" | tr "\n" " ")"'

bold "Backups"
ssh_to 'cd /opt/neurocode/deploy && docker compose exec -T api sh -c "ls -lh /data/backups 2>/dev/null | tail -4"' \
  | sed 's/^/  /' || note "none yet"
