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
1 refused (the API's words on stderr), 2 usage, 3 waiting on a person (a permission card or a gate).

| Command | What it does |
| --- | --- |
| `nc status` | The server, who you are, what waits on a person, the runs under way |
| `nc projects` · `nc use <id>` | The workspace's projects; the one the other commands default to |
| `nc open [project] [--desktop/--browser]` | The project's Workbench, in the desktop app when it is installed |
| `nc ask "<question>" [-p id] [-s SES-…]` | A question, its answer streamed as it is written; permission cards asked in the terminal |
| `nc chat [-p id] [-s SES-…]` | A full-screen session — see below |
| `nc sessions` | Recent sessions, and which wait on you |
| `nc plan "<requirement>" [-p id]` | Compile a plan: steps, risk, files, criteria, open questions |
| `nc plans` · `nc show-plan <ref>` · `nc dispatch <ref> [--goal N] [--step-gate]` | Plans, one in full, and handing one to the agents |
| `nc approvals [--all]` · `nc approve <gate> [--scope once\|run\|project] [--answer …]` · `nc deny <gate>` | Gates |
| `nc runs [ref] [--watch]` | Runs; one run's steps and log; `--watch` follows the log live (`--json`: one JSON object per line) |
| `nc merge <run>` · `nc push <run> [--remote r]` | Merge an accepted run (prints the undo); push its branch (prints the compare link) |
| `nc memory search "<words>"` · `nc memory add "<title>" "<fact>"` | What the workspace remembers |

## `nc chat`

The session the web app's Sessions screen shows, live in both at once. The answer streams in as it is
written, with its reasoning folded underneath (select it to unfold); each tool call is a line of its own; a
permission card shows three buttons — Allow once, Allow for this session, Refuse.

- `@` then a few letters attaches a file, symbol, fact or plan (↑↓ or Tab to choose, Enter to take).
- `/` offers `/help`, `/new`, `/sessions`, `/stop`, `/compact`, `/plan`, `/export`, `/quit`, and the
  project's own slash commands, which the server expands exactly as it does for the web.
- Esc stops the answer, Ctrl+N starts a new session, Ctrl+Q leaves.
