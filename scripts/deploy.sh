#!/usr/bin/env bash
# NeuroCode — commit, push to GitHub, deploy to Vercel, and wait until it is actually live.
#   ./scripts/deploy.sh "commit message"      → build check, push, production deploy
#   ./scripts/deploy.sh -p "message"          → preview deploy instead of production
#   ./scripts/deploy.sh -s "message"          → skip the build check (faster, riskier)
#
# Why the author check exists: the Vercel team is on Hobby, which BLOCKS any deployment whose
# commit author is not a member of the team. Commits authored as rajatrajawat were accepted by
# GitHub and silently blocked by Vercel — and `vercel deploy` then waited forever for a READY
# that never came. So every commit this script makes is authored as the account Vercel knows,
# and the deploy is created with --no-wait and polled, so a BLOCKED state fails fast and loudly.
set -euo pipefail
cd "$(dirname "$0")/.."

ACCOUNT=neurosynthlabs
AUTHOR_NAME=neurosynthlabs
AUTHOR_EMAIL=neurosynthlabs@users.noreply.github.com
TARGET=--prod
SKIP_BUILD=0
WAIT_SECS=480
PREV_GH=""

while getopts "psh" o; do
  case $o in
    p) TARGET="" ;;
    s) SKIP_BUILD=1 ;;
    h) sed -n '2,6p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) exit 1 ;;
  esac
done
shift $((OPTIND - 1))
MSG="${1:-chore: update $(date '+%Y-%m-%d %H:%M')}"

say() { printf '\033[1;34m▸\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31m✗\033[0m %s\n' "$1" >&2; exit 1; }

# ── 1. never ship a broken build ─────────────────────────────
if [ "$SKIP_BUILD" -eq 0 ]; then
  say "typecheck + build"
  npm run build >/tmp/nc-build.log 2>&1 || { tail -30 /tmp/nc-build.log; die "build failed — nothing was pushed"; }
  printf '  bundle: %s\n' "$(du -sh dist | cut -f1)"
fi

# ── 2. commit as the account Vercel recognises ───────────────
git config user.name "$AUTHOR_NAME"
git config user.email "$AUTHOR_EMAIL"

if [ -n "$(git status --porcelain)" ]; then
  say "commit: $MSG"
  git add -A
  git commit -q -m "$MSG"
else
  say "working tree clean — nothing to commit"
fi

HEAD_AUTHOR=$(git log -1 --format=%ae)
if [ "$HEAD_AUTHOR" != "$AUTHOR_EMAIL" ]; then
  say "HEAD is authored by $HEAD_AUTHOR — Vercel would block it; adding a release commit as $AUTHOR_NAME"
  git commit -q --allow-empty -m "chore(release): deploy $(git rev-parse --short HEAD) as $AUTHOR_NAME"
fi

# ── 3. github ────────────────────────────────────────────────
# gh's active account is the git credential helper for EVERY https push on this machine, so
# switch only for this one push and switch straight back — the trap covers a mid-script death.
restore_gh() {
  if [ -n "$PREV_GH" ] && [ "$PREV_GH" != "$ACCOUNT" ]; then
    gh auth switch --user "$PREV_GH" >/dev/null 2>&1 || true
    PREV_GH=""
  fi
}
trap restore_gh EXIT
PREV_GH=$(gh api user --jq .login 2>/dev/null || true)
if [ "$PREV_GH" != "$ACCOUNT" ]; then
  say "switching gh account → $ACCOUNT for the push (back to ${PREV_GH:-the previous account} right after)"
  gh auth switch --user "$ACCOUNT" >/dev/null 2>&1 || die "gh account '$ACCOUNT' not available — run: gh auth login"
fi
# GitHub refuses a push that adds or changes a workflow unless the token carries the `workflow` scope.
# Say so before pushing, instead of letting git fail with a message about OAuth apps.
if git diff --name-only '@{u}..HEAD' 2>/dev/null | grep -q '^\.github/workflows/' \
   && ! gh auth status --active 2>&1 | grep -q "'workflow'"; then
  die "this push changes .github/workflows and $ACCOUNT's token lacks the workflow scope. Run: gh auth refresh -h github.com -s workflow (with $ACCOUNT active), then deploy again — the commit is kept"
fi
say "push origin $(git rev-parse --abbrev-ref HEAD)"
git push -q origin HEAD
restore_gh

# ── 4. vercel — create without blocking, then poll the real state ──
say "vercel deploy${TARGET:+ (production)}"
OUT=$(vercel deploy $TARGET --yes --no-wait 2>&1) || { echo "$OUT" | tail -20; die "vercel refused the deployment"; }
URL=$(echo "$OUT" | grep -Eo 'https://[a-z0-9.-]+\.vercel\.app' | head -1 || true)
[ -n "$URL" ] || { echo "$OUT" | tail -20; die "no deployment URL in the CLI output"; }
printf '  %s\n' "$URL"

inspect() {
  vercel inspect "$URL" --json 2>/dev/null | python3 -c '
import sys, json
t = sys.stdin.read()
i = t.find("{")
d = json.loads(t[i:]) if i >= 0 else {}
print(d.get("readyState", "UNKNOWN") + "|" + " ".join("https://" + a for a in d.get("aliases", [])))'
}

START=$SECONDS
elapsed=0
STATE=UNKNOWN
ALIASES=""
while :; do
  IFS='|' read -r STATE ALIASES <<<"$(inspect || echo 'UNKNOWN|')"
  elapsed=$((SECONDS - START))
  case "$STATE" in
    READY) break ;;
    ERROR|CANCELED) die "deployment $STATE — see: vercel inspect $URL --logs" ;;
    BLOCKED) die "deployment BLOCKED — the commit author is not a member of the Vercel team. HEAD: $(git log -1 --format='%an <%ae>')" ;;
  esac
  [ "$elapsed" -ge "$WAIT_SECS" ] && die "still $STATE after ${elapsed}s — see: vercel inspect $URL"
  printf '\r  %-9s %3ss' "$STATE" "$elapsed"
  sleep 5
done

printf '\r\033[1;32m✓ live\033[0m  %s  (%ss)\n' "$URL" "$elapsed"
for a in $ALIASES; do printf '         %s\n' "$a"; done
printf '  repo   https://github.com/%s/neurocode\n' "$ACCOUNT"
printf '  commit %s  %s\n' "$(git rev-parse --short HEAD)" "$(git log -1 --format=%s)"
