# nc — NeuroCode in the terminal

`nc` is a client of the same API the web app and the desktop app use. It signs in with a personal access
token, so it can do exactly what you can do in the browser and nothing more: every permission, gate and
rule is the server's.

## Install

```sh
uv tool install ./cli          # from the repository root; puts `nc` on your PATH
```

For working on it: `cd cli && uv venv && uv pip install -e . pytest && .venv/bin/pytest -q`.

## Sign in

```sh
nc login http://localhost:5180               # the web app's address (its API is under /api)
nc login https://neurocode.example.com --email you@example.com
nc login http://127.0.0.1:8787 --web http://localhost:5180   # an API reached directly
nc login https://… --token -                 # a token made in Settings → Access tokens, read from stdin
```

With your email and password, `nc login` signs in once, makes a token called “nc on <this machine>” and signs
that session out again. `--scope` (repeatable) limits what the token may do and `--days` when it stops
working. A token with no scopes carries everything you hold **except `machine:access`**: a shell on the
server's machine is only ever carried by a token that names it.

The token goes to the system keychain (macOS Keychain, Secret Service, Windows Credential Locker). Where
there is none — a server over SSH, a container — it is kept in `~/.config/neurocode/credentials.json`,
readable by you only (0600). `nc logout` forgets it here; revoke it in Settings → Access tokens.

Scripts and CI can skip the files altogether: `NC_URL`, `NC_TOKEN`, `NC_WEB` and `NC_PROJECT` win over
anything saved. `NC_CONFIG_DIR` moves the saved profile.

## Commands

Every command takes `--json` and then prints the API's own answer and nothing else. Exit codes: 0 done,
1 refused (the API's words on stderr), 2 usage, 3 waiting on a person (a permission card or a gate),
4 `--watch --timeout` gave up while the run was still going.

| Command | What it does |
| --- | --- |
| `nc status` | The server, who you are, what waits on a person, the runs under way |
| `nc projects` · `nc use <id>` | The workspace's projects; the one the other commands default to |
| `nc open [project] [--desktop/--browser]` | The project's Workbench, in the desktop app when it is installed |
| `nc ask ["<question>"] [-p id] [-s SES-…] [-c]` | A question, its answer streamed as it is written; permission cards asked in the terminal |
| `nc chat [-p id] [-s SES-…] [-c]` | A full-screen session — see below |
| `nc sessions` | Recent sessions, and which wait on you |
| `nc plan ["<requirement>"] [--from file\|-] [-p id]` | Compile a plan: steps, risk, files, criteria, open questions |
| `nc plans` · `nc show-plan <ref>` · `nc plan-answer <plan> <n> "<answer>"` · `nc dispatch <ref> [--goal N] [--step-gate]` | Plans, one in full, answering an open question, and handing one to the agents |
| `nc approvals [--all]` · `nc approve <gate> [--scope once\|run\|project] [--answer …]` · `nc deny <gate>` | Gates |
| `nc runs [ref] [--watch] [--timeout s]` | Runs; one run's steps and log; `--watch` follows the log live (`--json`: one JSON object per line) |
| `nc diff [run] [--file glob] [--stat] [--raw]` | The run's change, read here — see below |
| `nc review <run> [--again]` · `nc accept <run>` · `nc send-back <run> --notes "…"` | The review's findings, and the two answers that end a run |
| `nc stop <run>` · `nc discard <run>` · `nc revert <run> <step> [--redo]` | Stop it; remove its worktree and branch; take its branch back to a step |
| `nc merge <run>` · `nc push <run> [--remote r]` | Merge an accepted run (prints the undo); push its branch (prints the compare link) |
| `nc memory search "<words>"` · `nc memory add "<title>" "<fact>\|-"` | What the workspace remembers |

Lists take `--limit` and `--offset`. When one comes back exactly full, `nc` says so on stderr: the
routes do not send their page's total yet, so a list of 100 may be the first 100 of 412.

## Reading a run, and signing it

```sh
nc diff RUN-7                      # the patch, coloured, cut to your window, paged when it is long
nc diff RUN-7 --file 'src/**'      # only these files, and how many of them there are
nc diff RUN-7 --stat               # the files and their counts, counted from the patch itself
nc diff RUN-7 --raw | git apply --3way   # the bytes, exactly — the default whenever stdout is a pipe
nc review RUN-7                    # the verdict in the reviewer's own words, the findings, the receipt
nc accept RUN-7                    # sign the run's own signature gate — never another gate of its own
nc send-back RUN-7 --notes "credit notes still round twice"   # the same plan again, told what was wrong
```

`nc review` exits 3 while the signature is waiting, and 0 once it is decided. `nc diff` with no run means
the one run here that waits on you; more than one, and it names them and exits 2.

There is no `--yes`, and there will not be one. It would mean a signature nobody read and a merge nobody
watched, which is the single thing the gates and the review receipt exist to prevent. `nc diff` reads the
change, `nc accept` signs it, and both are a person's to type.

## Pipes

```sh
nc ask < bug-report.md                     # what is piped in is the question
cat trace.log | nc ask "why does this fail"   # the question, then the piped text under a marker
nc plan --from requirement.md
nc memory add "Invoice rounding" -         # the fact's body from stdin
```

Anything piped in is capped at 100 KB and refused above it, with both sizes named, rather than cut
silently — a lane is charged for every byte of a prompt.

## Carrying on where you left off

`nc ask -c` and `nc chat -c` open the session this machine was last in for that project, falling back to
the newest the server holds. Which one it chose, and when it was last spoken to, is always printed:
resuming silently is how a question lands in a week-old conversation and is answered from the wrong
context.

## Scripts and CI

`NC_URL`, `NC_TOKEN`, `NC_WEB` and `NC_PROJECT` mean nothing is ever written to disk. Then:

- `nc ask --on-permission refuse` answers a permission card by refusing it, so a nightly script does not
  leave a trail of sessions waiting for someone who will never see them. The default, `wait`, leaves the
  card for a person and exits 3.
- `nc runs <ref> --watch --timeout 900` gives up after 900 seconds with exit code 4 and says the run is
  still going. Nothing is stopped; only the watching ends. `--timeout 0` (the default) waits for ever.
- `nc runs <ref> --watch --json` prints one JSON object per line, which is what a script can follow.

## `nc chat`

The session the web app's Sessions screen shows, live in both at once. The answer streams in as it is
written, with its reasoning folded underneath (select it to unfold); each tool call is a line of its own; a
permission card shows three buttons — Allow once, Allow for this session, Refuse.

- `@` then a few letters attaches a file, symbol, fact or plan (↑↓ or Tab to choose, Enter to take).
- `/` offers `/help`, `/new`, `/sessions`, `/stop`, `/compact`, `/plan`, `/export`, `/quit`, and the
  project's own slash commands, which the server expands exactly as it does for the web.
- Esc stops the answer, Ctrl+N starts a new session, Ctrl+Q leaves.
