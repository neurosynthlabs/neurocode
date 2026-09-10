#!/usr/bin/env bash
# NeuroCode — commit, push to GitHub, deploy to Vercel.
#   ./scripts/deploy.sh "commit message"      → build check, push, production deploy
#   ./scripts/deploy.sh -p "message"          → preview deploy instead of production
#   ./scripts/deploy.sh -s "message"          → skip the build check (faster, riskier)
set -euo pipefail
cd "$(dirname "$0")/.."

ACCOUNT=neurosynthlabs
TARGET=--prod
SKIP_BUILD=0

while getopts "psh" o; do
  case $o in
    p) TARGET="" ;;
    s) SKIP_BUILD=1 ;;
    h) sed -n '2,6p' "$0" | sed 's/^# \?//'; exit 0 ;;
  esac
done
shift $((OPTIND - 1))
MSG="${1:-chore: update $(date '+%Y-%m-%d %H:%M')}"

say() { printf '\033[1;34m▸\033[0m %s\n' "$1"; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$1" >&2; exit 1; }

# ── 1. never ship a broken build ─────────────────────────────
if [ "$SKIP_BUILD" -eq 0 ]; then
  say "typecheck + build"
  npm run build >/tmp/nc-build.log 2>&1 || { tail -30 /tmp/nc-build.log; die "build failed — nothing was pushed"; }
  printf '  bundle: %s\n' "$(du -sh dist | cut -f1)"
fi

# ── 2. github ────────────────────────────────────────────────
if [ -n "$(git status --porcelain)" ]; then
  say "commit: $MSG"
  git add -A
  git commit -q -m "$MSG"
else
  say "working tree clean — nothing to commit"
fi

CURRENT=$(gh auth status 2>&1 | awk '/Active account: true/{found=1} /Logged in to/{acct=$NF} found && acct {print acct; exit}' || true)
if [ "${CURRENT:-}" != "$ACCOUNT" ]; then
  say "switching gh account → $ACCOUNT"
  gh auth switch --user "$ACCOUNT" >/dev/null 2>&1 || die "gh account '$ACCOUNT' not available — run: gh auth login"
fi

say "push origin $(git rev-parse --abbrev-ref HEAD)"
git push -q origin HEAD

# ── 3. vercel ────────────────────────────────────────────────
say "vercel deploy${TARGET:+ (production)}"
OUT=$(vercel deploy $TARGET --yes 2>&1) || { echo "$OUT" | tail -20; die "deploy failed"; }
URL=$(echo "$OUT" | grep -Eo 'https://[a-z0-9.-]+\.vercel\.app' | tail -1)

printf '\n\033[1;32m✓ live\033[0m  %s\n' "${URL:-see output above}"
printf '  repo   https://github.com/%s/neurocode\n' "$ACCOUNT"
printf '  commit %s  %s\n' "$(git rev-parse --short HEAD)" "$MSG"
