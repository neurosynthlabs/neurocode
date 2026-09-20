#!/usr/bin/env bash
# Take a backup now and bring it here, because a backup that only exists on the machine it was taken from is
# not a backup. The server keeps its own copies too (a nightly timer, set up by setup-vm.sh).
#
#   deploy/backup.sh               → deploy/backups/neurocode-<when>.sql.gz on this Mac
# shellcheck source=deploy/_common.sh
. "$(dirname "$0")/_common.sh"
resolve_host "" >/dev/null
OUT="$DEPLOY_DIR/backups"; mkdir -p "$OUT"

bold "Asking the server for a fresh dump"
# shellcheck disable=SC2119  # no environment to pass: the script on stdin is all of it
NAME=$(remote <<'REMOTE'
set -euo pipefail
cd /opt/neurocode/deploy
name="neurocode-$(date -u +%Y%m%d-%H%M%S).sql.gz"
# pg_dump from the API container: it has the client of the right major version and the database's password.
docker compose exec -T api sh -c "mkdir -p /data/backups && pg_dump --dbname \"\$NEUROCODE_DATABASE_URL\" \
  --format=plain --no-owner --no-privileges | gzip -9 > /data/backups/$name"
echo "$name"
REMOTE
)
NAME=$(printf '%s' "$NAME" | tail -1 | tr -d '\r')
[ -n "$NAME" ] || die "The server did not make a dump."

bold "Copying $NAME here"
ssh_to "cd /opt/neurocode/deploy && docker compose exec -T api cat /data/backups/$NAME" > "$OUT/$NAME"
[ -s "$OUT/$NAME" ] || { rm -f "$OUT/$NAME"; die "The copy came back empty."; }
note "$OUT/$NAME ($(du -h "$OUT/$NAME" | cut -f1))"

# Keep the last ten here; the server prunes its own. Newest first, by modification time, whatever the names are.
find "$OUT" -name 'neurocode-*.sql.gz' -type f -print0 2>/dev/null \
  | xargs -0 ls -1t 2>/dev/null | tail -n +11 | while IFS= read -r old; do rm -- "$old"; done
bold "Restoring one, if it ever comes to that"
note "gunzip -c $OUT/$NAME | ssh -i $KEY $HOST 'cd /opt/neurocode/deploy && docker compose exec -T db psql -U neurocode -d neurocode'"
