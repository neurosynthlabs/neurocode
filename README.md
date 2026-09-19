# NeuroCode

**An AI engineering OS for your team, on your own machine.**

You are the AI Project Manager and the final approver. Everything else — planning, research,
coding, testing, review, DevOps, legacy analysis, memory and knowledge management — is done by
twelve specialist agents working in isolated git worktrees.

This repository holds the web app (39 screens, with an Admin section) and a local API: FastAPI over
Postgres 16, with accounts, roles and permissions, an append-only audit log, an agent runtime that works in
git worktrees, and an AI gateway that routes across free model lanes. `docs/ARCHITECTURE.md` has the whole
shape. There is no sample data in it: a new workspace is empty until you onboard a repository, and the web
app with no API behind it says it is not connected rather than showing anything invented.

## What is in here

| Area | Screens |
|---|---|
| Workspace | Command Center · Projects · Project Overview |
| Intelligence | Memory · Knowledge · Code Intelligence · Architecture |
| Execution | Agents · Tasks · Plans · Live Runs · Workflows · Testing · Review · Git & Worktrees · DevOps |
| Platform | Skills · Commands · Hooks · Plugins · MCP & Tools · Models & Router |
| Thinking | Brainstorm · Research |
| Governance | Activity · Sessions · Permissions · Cost & Usage · Evals · Settings |

Plus Admin (People · Roles & permissions · Teams · AI providers · Audit log · Workspace), first-run
setup, sign-in, and a ⌘K palette that searches code, memory, tasks, decisions, commits and tests.

## The ideas the UI is arguing for

- **Memory is the moat.** Facts carry a reason, a source and evidence, and every time one is cited or
  handed to a model the use is recorded — so what memory is worth is counted, not asserted.
- **Code is parsed, not guessed.** Python's own parser and pattern sets for TS/JS, C#, Java, Go and
  T-SQL build a dependency graph, and impact analysis walks it rather than naming conventions.
- **The requirement compiler.** Broken Hinglish in, an evidenced plan out — including the open
  questions it refuses to guess at.
- **Agents work in isolated worktrees.** A run branches from your HEAD, writes whole files, runs the
  project's own tests, reviews its real diff and stops at your signature. Nothing is merged for you,
  and nothing a model says is ever executed.
- **The human is the scarce resource.** Writing in a worktree needs nobody; a project's test command
  asks you once and remembers; accepting a changed run and merging it always wait for a person.
- **Local first, honest always.** Free model lanes first, a local Ollama model when you have one, and
  when nothing can answer the screen says so instead of inventing a plan.

## Stack

React 19 · Vite 8 · TypeScript · Tailwind v4 · shadcn/ui (Base UI) · React Router 7 · Lucide

## Theming

248 palettes × 7 modes × 64 ground tones, plus live corner radius, six font families, four base
sizes and three sidebar styles — all driven by CSS variables written at runtime, so every one of the
39 screens repaints without a rebuild.

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

Checks: `npm run build` · `npm run smoke` · `npm run lint:layout` · `npm run api:test` · `npm run e2e`.
The browser checks start their own stack — a throwaway Postgres database, the API and a stub model — so
they never touch your workspace and never reach a real provider.

## Conventions

`docs/DESIGN_SYSTEM.md` is the binding contract for this codebase — token names, density rules,
component sources and the shadcn cheatsheet.
