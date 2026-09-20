# NeuroCode

**An AI engineering OS for your team, on your own machine.**

You are the AI Project Manager and the final approver. Everything else — planning, research,
coding, testing, review, DevOps, legacy analysis, memory and knowledge management — is done by
twelve specialist agents working in isolated git worktrees.

This repository holds the product in five ways in — a web app (32 screens and an Admin section) that
installs on a phone, `nc` for the terminal (`cli/`), a VS Code extension (`vscode/`), a desktop app
(`desktop/`) and a GitHub action (`.github/actions/neurocode`) — over one local API: FastAPI on Postgres 16, with
accounts, roles, personal access tokens and an append-only audit log, an agent runtime governed by tool rules
and gates, a code index that reads 25+ languages, and an AI gateway that routes across free model lanes.
`docs/ARCHITECTURE.md` has the whole shape. There is no sample data in it: a new workspace is empty until you
open a folder or start a project, and the web app with no API behind it says it is not connected rather than
showing anything invented.

## What is in here

| Area | Screens |
|---|---|
| Home | Command Center (with the inbox) · Activity · Projects · Project Overview |
| Build — Planning | Blueprints · Plans · Tasks |
| Build — Execution | Workbench · Agents · Live Runs · Workflows · Routines |
| Build — Quality | Testing · Review · Evals |
| Build — Delivery | Git & Worktrees · DevOps |
| Knowledge | Memory · Knowledge · Code Intelligence · Architecture · Brainstorm · Research |
| Platform | Skills · Commands · Hooks · Plugins · MCP & Tools · Models & Router |
| Governance | Permissions · Cost & Usage · Sessions · Settings |

Plus Admin (People · Roles & permissions · Teams · AI providers · Audit log · Workspace · Database), first-run
setup, sign-in, and a ⌘K palette that searches code, memory, tasks, decisions, commits and tests.

The **Workbench** is the machine the API runs on, in the browser: folders and files, an editor, terminals,
run configurations, a debugger (Python and Node), problems from the project's own checkers, hover and go to
definition from a language server, Jupyter notebooks run through a real kernel, and data files (CSV, Parquet,
JSON Lines, SQLite) with statistics, charts and a read-only SQL box.

## The ideas the UI is arguing for

- **Memory is the moat.** Facts carry a reason, a source and evidence, and every time one is cited or
  handed to a model the use is recorded — so what memory is worth is counted, not asserted.
- **Code is parsed, not guessed.** Python's own parser, tree-sitter for 25 more languages and patterns for
  T-SQL build a dependency graph, and impact analysis walks it rather than naming conventions.
- **The requirement compiler.** A rough requirement in, in any language, an evidenced plan out — including
  the open questions it refuses to guess at, and acceptance criteria a person can shape.
- **Agents work in isolated worktrees, under rules.** A run branches from your HEAD, writes whole files,
  runs the project's own tests and checks, reviews its real diff on another model and stops at your
  signature. Every edit and command passes the tool rules first; an agent that is unsure asks. Nothing is
  merged for you, and nothing a model says is ever executed.
- **The human is the scarce resource.** Writing in a worktree needs nobody; a project's test command
  asks you once and remembers; a gate can be allowed once, for the run or always; accepting a changed run
  and merging it always wait for a person. Routines run on a schedule and still stop at your signature.
- **Local first, honest always.** Free model lanes first, a local Ollama model when you have one, and
  when nothing can answer the screen says so instead of inventing a plan.

## Stack

React 19 · Vite 8 · TypeScript · Tailwind v4 · shadcn/ui (Base UI) · React Router 7 · Lucide

## Theming

248 palettes × 7 modes × 64 ground tones, plus live corner radius, six font families, four base
sizes and three sidebar styles — all driven by CSS variables written at runtime, so every screen
repaints without a rebuild.

## Run it

```bash
npm install
brew install postgresql@16 pgvector && brew services start postgresql@16   # once
uv run --project server python scripts/bootstrap-db.py                    # once: databases + extensions
(cd server && uv run alembic upgrade head)                                  # after every pull
npm run dev:start   # web on http://localhost:5180, API on 127.0.0.1:8787 (the API needs uv)
```

Coming from the SQLite version? `uv run --project server python scripts/import-sqlite.py` carries the
workspace across, accounts and passwords included.

The first visit opens the setup wizard: name the workspace and create the Owner account. Planning,
brainstorming and agent runs need a model: add a free key (Groq, Cerebras or Gemini take a minute) or a
local Ollama model in Admin → AI providers. Asking memory and extracting facts still work without one,
labelled as rules.

The terminal client, the editor extension and the desktop app use the same API:

```bash
uv tool install ./cli && nc login http://localhost:5180   # then: nc ask, nc chat, nc plan, nc runs --watch …
(cd vscode && npm install && npm run package)             # a .vsix: code --install-extension neurocode-0.9.4.vsix
npm run desktop:dev                                       # the desktop app against the dev servers
npm run desktop:build                                     # an arm64 .dmg in desktop/dist (unsigned without a Developer ID)
```

The extension signs in with a personal access token and adds four things and no more: **Ask NeuroCode
about this selection** (a session with the file attached and the lines quoted), **Compile this as a
requirement**, **Open in the Workbench**, and a Runs view following the same live stream every open
tab reads. It never signs, merges or answers a gate — those need the diff and the review in front of
you, so it opens the web app there instead. `vscode/README.md` has the rest.

## On a phone, and from another device

The web app installs: open it on a phone and add it to the home screen, and it opens without a
network. The shell is cached; **your workspace never is**. A phone out of signal shows the app saying
it cannot reach its server — not yesterday's numbers presented as today's.

Which leaves one question: can the phone reach a server? A hosted one, over HTTPS, yes
(`deploy/README.md` — Docker Compose with Postgres, the API and Caddy). The API on this laptop, no:
it binds to `127.0.0.1`. One switch changes that, and it is off on purpose.

```bash
NEUROCODE_LISTEN_ON_LAN=true npm run dev:start   # prints the address to type on your phone
./scripts/dev.sh status                          # says which of the two it is, either way
```

With it on, the API binds every interface and allows origins from the private ranges and `.local`
names — and nothing else. What that costs, which the API prints every time it starts this way:
everyone on your network can reach it, and anyone with an account or a token can sign in to your
workspace; over plain http the session cookie crosses in the clear; and with machine access still on,
an Owner signing in from another device gets the Workbench — this machine's files, terminals and
debugger. Safe on your own Wi-Fi for an afternoon of testing on a phone. Not safe on a café network,
not safe on an office VLAN you do not run, and never a substitute for hosting one properly.

## From a workflow

`.github/actions/neurocode` installs `nc` from this repository's own `cli/` and runs it against a
server with a token, so CI can compile a requirement, dispatch a plan, or wait on a run:

```yaml
      - name: Hand it to the agents
        id: run
        uses: ./.github/actions/neurocode
        with:
          server: ${{ secrets.NEUROCODE_SERVER }}
          token: ${{ secrets.NEUROCODE_TOKEN }}
          command: dispatch
          plan: PLAN-12

      - name: Wait for it
        uses: ./.github/actions/neurocode
        with:
          server: ${{ secrets.NEUROCODE_SERVER }}
          token: ${{ secrets.NEUROCODE_TOKEN }}
          command: wait
          run: ${{ steps.run.outputs.ref }}
```

The job goes red the moment the run reaches your signature, with nothing merged and nothing lost.
`accept`, `merge` and `approve` are refused there outright: there is no `--yes` in `nc`, and a
workflow allowed to type `nc accept` would be that `--yes` by another route.
`.github/actions/neurocode/README.md` has every input and the whole workflow.

Checks: `npm run build` · `npm run smoke` · `npm run lint:layout` · `npm run api:test` · `npm run e2e` ·
`npm run test:unit` · `npm run desktop:test` · `cd cli && uv run pytest` · `cd vscode && npm test` ·
`node --test .github/actions/neurocode/test/`.
The browser checks start their own stack — a throwaway Postgres database, the API and a stub model — so
they never touch your workspace and never reach a real provider.

## Conventions

`docs/DESIGN_SYSTEM.md` is the binding contract for this codebase — token names, density rules,
component sources and the shadcn cheatsheet.
