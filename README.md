# NeuroCode

**An AI engineering OS for your team, on your own machine.**

You are the AI Project Manager and the final approver. Everything else — planning, research,
coding, testing, review, DevOps, legacy analysis, memory and knowledge management — is done by
twelve specialist agents working in isolated git worktrees.

This repository holds the product in three ways in — a web app (32 screens and an Admin section), `nc` for
the terminal (`cli/`) and a desktop app (`desktop/`) — over one local API: FastAPI on Postgres 16, with
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

The terminal client and the desktop app use the same API:

```bash
uv tool install ./cli && nc login http://localhost:5180   # then: nc ask, nc chat, nc plan, nc runs --watch …
npm run desktop:dev                                       # the desktop app against the dev servers
npm run desktop:build                                     # an arm64 .dmg in desktop/dist (unsigned without a Developer ID)
```

Hosting a server of your own (Docker Compose with Postgres, the API and Caddy for HTTPS) is in `deploy/README.md`.

Checks: `npm run build` · `npm run smoke` · `npm run lint:layout` · `npm run api:test` · `npm run e2e` ·
`npm run desktop:test` · `cd cli && uv run pytest`.
The browser checks start their own stack — a throwaway Postgres database, the API and a stub model — so
they never touch your workspace and never reach a real provider.

## Conventions

`docs/DESIGN_SYSTEM.md` is the binding contract for this codebase — token names, density rules,
component sources and the shadcn cheatsheet.
