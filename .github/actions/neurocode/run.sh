#!/usr/bin/env bash
# The body of the NeuroCode action: one `nc` command, and an honest answer about what it means.
#
# Kept as a script rather than inline YAML so it can be run here, with a stub `nc` on the PATH, and
# read back by test/action.test.mjs — a workflow step that is only ever exercised by pushing a commit
# is a step nobody can fix on the day it matters.
#
# Everything it depends on arrives as INPUT_* in the environment, exactly as action.yml sets it, and
# everything it produces goes to $GITHUB_OUTPUT. Nothing here decides anything about the work itself:
# `nc` talks to the server, the server decides, and this only translates "a person is needed" into
# something a workflow can see.
set -uo pipefail

fail() { printf '::error::%s\n' "$1"; exit 1; }
out()  { printf '%s=%s\n' "$1" "$2" >> "${GITHUB_OUTPUT:-/dev/null}"; }

# A multi-line value needs a delimiter GitHub will not find inside the value itself.
out_block() {
  local name=$1 value=$2 edge
  edge="nc_$(date +%s)_$RANDOM"
  { printf '%s<<%s\n' "$name" "$edge"; printf '%s\n' "$value"; printf '%s\n' "$edge"; } \
    >> "${GITHUB_OUTPUT:-/dev/null}"
}

[ -n "${INPUT_SERVER:-}" ] || fail "The NeuroCode action needs 'server': the web app's address, or an API's own."
[ -n "${INPUT_TOKEN:-}" ]  || fail "The NeuroCode action needs 'token': a personal access token from a secret (Settings → Access tokens). Never a password."

# `nc` is a command on every Linux runner already — netcat. Ours is the one whose `version` command
# prints JSON naming itself; netcat reads those two words as a host and a port and fails.
command -v nc >/dev/null 2>&1 || fail "nc is not on the PATH: the install step did not finish."
if ! nc version --json 2>/dev/null | grep -q '"nc"'; then
  fail "The 'nc' on this PATH is $(command -v nc), which is not NeuroCode's (it is probably netcat). ~/.local/bin has to come first on the PATH."
fi

# The environment is how `nc` is pointed at a server without writing a secret to disk anywhere.
export NC_URL="$INPUT_SERVER"
export NC_TOKEN="$INPUT_TOKEN"
[ -n "${INPUT_PROJECT:-}" ] && export NC_PROJECT="$INPUT_PROJECT"

argv=()
case "${INPUT_COMMAND:-}" in
  status)
    argv=(status)
    ;;
  approvals)
    argv=(approvals)
    ;;
  compile)
    [ -n "${INPUT_PROJECT:-}" ] || fail "compile needs 'project': which project the requirement is for."
    if [ -n "${INPUT_REQUIREMENT_FILE:-}" ]; then
      [ -f "$INPUT_REQUIREMENT_FILE" ] || fail "No such file in the checkout: $INPUT_REQUIREMENT_FILE"
      argv=(plan --from "$INPUT_REQUIREMENT_FILE" --project "$INPUT_PROJECT")
    else
      [ -n "${INPUT_REQUIREMENT:-}" ] || fail "compile needs 'requirement' (or 'requirement-file'): what you want built, in your words."
      argv=(plan "$INPUT_REQUIREMENT" --project "$INPUT_PROJECT")
    fi
    ;;
  dispatch)
    [ -n "${INPUT_PLAN:-}" ] || fail "dispatch needs 'plan': the plan to hand to the agents (PLAN-…)."
    argv=(dispatch "$INPUT_PLAN")
    [ -n "${INPUT_GOAL_BUDGET:-}" ] && argv+=(--goal "$INPUT_GOAL_BUDGET")
    ;;
  wait)
    # A run, named. `nc runs --watch` with no run follows every run for ever and never ends, which in
    # a workflow is a job that hangs until the runner kills it — so this asks for the one to wait on,
    # which is what a dispatch step's `ref` output is.
    [ -n "${INPUT_RUN:-}" ] || fail "wait needs 'run': the run to follow (RUN-…) — a dispatch step's 'ref' output is one."
    argv=(runs "$INPUT_RUN" --watch --timeout "${INPUT_TIMEOUT:-900}")
    ;;
  nc)
    [ -n "${INPUT_ARGS:-}" ] || fail "command: nc needs 'args': the nc arguments to run."
    # Split on whitespace, honouring quotes, the way a person typed them into the workflow.
    eval "argv=($INPUT_ARGS)"
    # And then the one thing a workflow may not be: the person. `nc` has no --yes, deliberately — it
    # would mean a signature nobody read and a merge nobody watched — and a workflow allowed to type
    # `nc accept` would be that --yes by another route. These six stay a person's to type.
    case "${argv[0]:-}" in
      accept|approve|deny|merge|push|send-back)
        fail "'${argv[0]}' decides something, and that is a person's to type. A workflow may compile, dispatch and wait; signing a run, answering a gate and merging happen where the diff and the review are."
        ;;
    esac
    ;;
  '')
    fail "The NeuroCode action needs 'command': status, compile, dispatch, wait, approvals, or nc."
    ;;
  *)
    fail "'${INPUT_COMMAND}' is not something this action does. It is one of: status, compile, dispatch, wait, approvals, nc."
    ;;
esac

printf '▸ nc %s\n' "${argv[*]}"
said=$(nc "${argv[@]}" --json)
code=$?

out_block json "$said"
out exit-code "$code"

# What the answer refers to. `runRef` wins over `ref` because dispatching a plan answers with both,
# and the run is the thing a following step waits on. `--watch --json` prints one object per line, so
# it is the last line that says how it ended.
read -r ref status <<<"$(printf '%s' "$said" | python3 -c '
import json, sys
lines = [l for l in sys.stdin.read().splitlines() if l.strip()]
found = {}
for line in reversed(lines):
    try:
        body = json.loads(line)
    except ValueError:
        continue
    if isinstance(body, dict):
        found = body
    break
ref = found.get("runRef") or found.get("ref") or ""
print(ref, found.get("status") or "")
' 2>/dev/null)"
out ref "${ref:-}"
out status "${status:-}"

# A list of gates is a list of things waiting on a person, however cheerfully it exits.
if [ "${INPUT_COMMAND}" = approvals ] && [ "$code" -eq 0 ]; then
  pending=$(printf '%s' "$said" | python3 -c '
import json, sys
try:
    body = json.load(sys.stdin)
except Exception:
    body = []
print(len(body) if isinstance(body, list) else 0)
' 2>/dev/null || echo 0)
  if [ "${pending:-0}" -gt 0 ]; then
    out waiting true
    if [ "${INPUT_FAIL_ON_WAITING:-true}" = true ]; then
      fail "$pending gate(s) are waiting on a person in NeuroCode. Open the approvals inbox, or set fail-on-waiting: false."
    fi
    printf '::notice::%s gate(s) are waiting on a person in NeuroCode.\n' "$pending"
    exit 0
  fi
fi

case "$code" in
  0)
    out waiting false
    ;;
  3)
    # `nc`'s exit code 3: a gate, a permission card or the signature at the end of a run. Not a
    # failure of the work — a request for a person, which is what this action exists to surface.
    out waiting true
    if [ "${INPUT_FAIL_ON_WAITING:-true}" = true ]; then
      fail "NeuroCode is waiting on a person${ref:+ ($ref)}. Nothing has been merged and nothing is lost: open it in NeuroCode, decide, and run this again. Set fail-on-waiting: false to carry on instead."
    fi
    printf '::notice::NeuroCode is waiting on a person%s.\n' "${ref:+ ($ref)}"
    exit 0
    ;;
  4)
    out waiting false
    fail "The run was still going after ${INPUT_TIMEOUT:-900}s${ref:+ ($ref)}. Nothing was stopped — only the watching ended. Raise 'timeout', or wait again."
    ;;
  2)
    out waiting false
    fail "nc refused these arguments (exit 2). Its own words are above."
    ;;
  *)
    out waiting false
    fail "NeuroCode refused${ref:+ ($ref)} — its own words are above (nc exit $code)."
    ;;
esac
