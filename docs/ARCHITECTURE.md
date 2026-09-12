# NeuroCode architecture

NeuroCode is an AI engineering OS for one operator, or a small team, working on their own machine.
This document is the plan the code follows: what runs where, how data is stored, who may do what,
and how every AI feature reaches a model.

## Shape

```
Browser  ── React 19 + Vite, one store (src/lib/data.tsx), auth (src/lib/auth.tsx)
   │  same-origin /api in dev (Vite proxy); VITE_API_URL in a build
   ▼
FastAPI (server/app) ── routers → services → Store
   ├── SQLite, WAL mode: documents, identity, audit, usage, code index (server/neurocode.db)
   │     └── online backups, newest 20 kept (server/backups/)
   ├── secrets file, mode 0600: model API keys (server/secrets.json)
   ├── code index ─▶ Python ast · patterns for TS/JS, C#, Java, Go, T-SQL · git history
   └── AI gateway ─▶ DeepSeek (key set) · Ollama (model pulled) · offline rules (always)
                    └── every call written to the usage ledger
```

The public demo (Vercel) is the same web app with no API. It runs on the seed data inside the tab,
signed in as a demo owner, and never makes a request.

## Why these choices

- **SQLite in WAL mode.** One machine, zero operations, FTS5 built in, and concurrent reads while the
  API writes. The schema is plain SQL in numbered migrations, so moving to Postgres when the API is
  hosted for a team is a driver change, not a redesign.
- **Documents for the domain, relations for access.** Tasks, plans, memory and the rest are JSON
  documents shaped exactly like the frontend's types, with the fields the API filters on lifted into
  columns. Users, roles, sessions and the audit log are strictly relational, with foreign keys on.
- **One gateway for every model call.** Keys, routing, time-outs, the rejected-key breaker and the
  fallback all live in one place. A feature never talks to a provider directly.
- **Local first, honest always.** Every AI feature works with no key. Output made by rules, not a
  model, is labelled as such in the UI.

## Backend layout

```
server/app/
  main.py          app factory: config, store, middleware, routers
  context.py       what routes share: store, bus, secrets, accounts, gateway, record(), audit()
  db.py            the Store: migrations runner, documents, settings, FTS search
  migrations/      0001 documents · 0002 identity, access, audit, settings · 0003 AI records
                   0004 hot-path indexes, append-only audit, usage ledger · 0005 code index
  events.py        in-process fan-out to open tabs (Server-Sent Events)
  secrets.py       API keys on disk (0600), only ever reported masked
  auth.py          scrypt passwords, sessions, sign-in throttling, current_user / require()
  rbac.py          the permission catalogue and roles
  ai/lanes.py      the lanes: a provider and a model each, with their free limits written down
  ai/gateway.py    which lane answers, keys, breakers, budgets, test connection, the usage ledger
  ai/compiler.py   requirement → plan
  ai/features.py   ask memory, brainstorm, extract facts from text
  onboarding.py    clone or read a repository and measure it
  codeindex.py     parse the code into files, symbols and edges; search, impact and the module graph
  runtime.py       a worktree per agent: models write files, the branches merge, the tests run, you sign off
  routes/          auth · admin · work · knowledge · platform · code · runs · ai · system
```

## Data model

| Table | Kind | Holds |
|---|---|---|
| `projects`, `agents`, `tasks`, `approvals`, `plans`, `conflicts`, `mcp`, `prefs`, `decisions`, `brainstorms` | document | the domain, shaped like `src/types` |
| `memory` + `memory_fts` | document + FTS5 | facts, searchable by prefix and ranked |
| `activity` | document | the work log that streams to every tab |
| `workspace` | relational | the single workspace: name, creation date |
| `users`, `roles`, `role_permissions`, `user_roles` | relational | identity and access |
| `teams`, `team_members` | relational | groups of people |
| `sessions` | relational | SHA-256 of each session token, never the token |
| `audit_log` | relational, append-only | sign-ins and every change to access, keys, settings or the database |
| `settings` | relational | workspace settings (AI routing, provider options) |
| `ai_calls` | relational, STRICT | the usage ledger: every model attempt and every offline answer, with tokens and time |
| `code_files`, `code_symbols`, `code_edges` | relational, STRICT | the code index: files, what they declare, and what depends on what |
| `code_fts` | FTS5 | search over file paths and symbol names, camelCase split into words |
| `code_index_runs` | relational, STRICT | one row per project: when it was indexed, by which parsers, how much |
| `runs` | document | an agent run: its branch, worktree, steps, tests, review and diff |
| `run_logs` | relational, STRICT | everything a run wrote, oldest lines dropped past the cap |
| `schema_migrations` | relational | which migrations ran |

## Keeping the data safe

- **Backups** use SQLite's online backup API, so nothing stops while one is made. One is taken before
  every reset and before any migration runs on a database that already holds data; admins can take
  one from Admin → Database. The newest 20 are kept, readable only by the account running the API.
- **Integrity**: foreign keys are on for every connection, new tables are `STRICT`, and Admin → Database
  runs SQLite's integrity check plus a foreign-key check on demand.
- **The audit log is append-only**: triggers make the database itself refuse to change or delete an entry.
- **Compaction**: "Optimize & compact" refreshes the planner's statistics, merges the search indexes,
  vacuums the file and folds the write-ahead log back in.
- **Expired sessions** are removed every time the API starts.

## The code index

Onboarding a repository reads every source file into the index: its language, lines, a SHA-1, a
complexity count (decision points) and, in a git repository, its changes over 90 days. Python is parsed
by its own `ast`, so its symbols and imports are exact; TypeScript/JavaScript, C#, Java, Go and T-SQL are
read by patterns, and the index records which parser read which language. Imports are resolved to files
(relative paths, `@/` aliases, Python packages); C# and Java files depend on the files that declare the
types they name; any file that reads, writes or calls a table or procedure declared in the repository's
SQL gets an edge to it. Impact analysis walks those edges backwards with a recursive query, eight steps
deep, and reports direct users, everything reached through them, the modules crossed, the tests that
notice and the data touched. Re-indexing replaces a project's rows in one transaction.

## Access control

- **Permissions** are `resource:action` strings from one catalogue, `src/mock/rbac.ts`, which is also
  exported to the API's seed. Examples: `plans:compile`, `approvals:decide`, `memory:write`,
  `users:manage`, `workspace:admin`.
- **Roles** are named sets of permissions. Built in: Owner, Admin, AI Project Manager, Engineer and
  Viewer. Admins can add custom roles. A user's permissions are the union of their roles.
- **Enforcement** happens in the API on every route: reads need a session; each change needs its
  permission. The UI mirrors it with `can()`, so a button a role cannot use is disabled with a reason.
- **Guards:** the last active Owner cannot be demoted or disabled, and nobody can disable themselves.
- **Sessions:** an opaque random token in an HttpOnly, SameSite=Lax cookie, or a Bearer header for
  scripts. Changing a request needs the `X-NC-Client` header too, so a page on another site cannot
  make the browser act on your behalf. Five wrong passwords lock that account for 30 seconds.
- **First run:** with no users, the app opens the Setup wizard. It names the workspace and creates
  the first Owner, who then creates everyone else.

## The agent runtime

Dispatching a plan starts a run, and a run is real work on real code. It gets a git worktree and a
branch of its own (`neurocode/task-492`), branched from the project's current HEAD. Then, step by step:
a model is asked for the **complete contents** of the files one plan step needs; the files are written
inside the worktree and committed; the project's own test command runs there; the real diff is reviewed;
and the run stops at your signature in the approvals inbox. Refuse and the branch and worktree are removed.

**Several agents at once.** A plan's steps are grouped by the agent that owns them. One agent, one run.
More than one, and each agent gets a worktree and a branch of its own (`neurocode/task-492-backend`,
`…-frontend`) and they all write at the same time; a fourth **integration** run then brings their branches
in one by one, runs the tests on the combined result, reviews the whole diff and stops at your signature.
Two agents that touch the same file collide at that merge, never mid-edit: the merge is undone, the
files that collided are named on the run screen, and the rest still comes in. Stopping or discarding the
integration run stops and discards its agents with it.

**Merging.** Once a run is accepted, the Merge button on the run screen merges its branch into whatever
branch your repository has checked out — two clicks, `runs:merge`, and written into the audit log. It
refuses outright if your working tree has uncommitted changes, undoes itself and names the files if the
merge collides, and always hands back the command that undoes it (`git reset --hard <sha>`). Nothing is
ever merged while a run is still working, and nothing is merged twice.

Three rules keep it safe to leave running:

- **Nothing touches your working tree.** Every change happens in the worktree, on a new branch.
- **Nothing a model says is ever executed.** It may only propose file contents. Paths that are absolute,
  climb out with `..` or point into `.git` are refused outright, and files are capped in size and number.
- **One command, with your permission.** The only thing that runs is the project's own test command
  (`make test`, `pytest`, `npm test`, `go test`, `dotnet test`), detected from the repository. The first
  time a project would run it, the run pauses for your approval, and your answer is remembered.

With no model configured, the writing steps are skipped and say so, and the run still opens the worktree,
runs the tests and reviews what is there. The review falls back to rules (secrets, debugging leftovers,
TODOs, a diff with no test touched) and says it was read by rules, not a model.

## AI features

| Feature | With a model | Offline (no key) |
|---|---|---|
| Compile a requirement | full restatement, steps, risks, questions | keyword planner that keeps your wording |
| Ask memory | answer written from the matching facts, cited | the matching facts, quoted and cited |
| Brainstorm | problem, audience, MVP, risks, metrics, roadmap | the same structure as a guided template |
| Extract facts | facts proposed from pasted text | policy sentences picked by rules |

## Lanes: many small free models instead of one big paid one

A **lane** is a provider and a model together — `groq/llama-3.3-70b`, `cerebras/qwen-3-coder`,
`gemini/2.5-flash`, `mistral`, `openrouter`, `github`, the paid `deepseek`, and `ollama` on this Mac.
Each lane carries what it is good at (write, review, plan, chat) and the free tier's limits, and every
one of them speaks the same chat-completions shape, so adding a provider is a row in a table, not a
client.

The router picks the lane: free first, the paid one only when the free ones are spent, the local model
last — skipping any lane with no key, with its allowance spent for this minute or this day, that an
admin switched off, or that refused the key it holds. A call that fails moves to the next lane instead
of dropping to the offline rules, and only when every lane is spent do the rules answer and say so.

Two things fall out of that. **Agents that work at the same time are spread across different lanes**,
so four agents are four providers answering at once rather than four requests queued behind one
rate limit — the real ceiling on parallel work is requests-per-minute, not intelligence. And **the
reviewer avoids the lane that wrote the code**, so a second model reads the diff: with free lanes, a
second opinion costs nothing.

Limits are the router's own caps, not promises from a provider: every lane's model, base URL, calls a
minute and calls a day are editable in Admin → AI providers, where each lane shows whether it can
answer right now and what it has spent today. Keys live in the secrets file (or an environment
variable), one per lane, never in the database and never in a log line.

## API map

| Prefix | Needs | What |
|---|---|---|
| `/auth` | public / session | status, setup, login, logout, me, change password |
| `/admin` | `users:manage`, `roles:manage`, `teams:manage`, `audit:read`, `workspace:admin` | people, roles, teams, audit, workspace, AI providers, database, reset |
| `/tasks`, `/plans`, `/approvals`, `/decisions`, `/prefs` | session + the action's permission | the work |
| `/memory` | session + `memory:write` | facts, search, conflicts |
| `/projects`, `/mcp`, `/agents` | session + the action's permission | the platform |
| `/projects/{id}/code` | session; `projects:onboard` to re-index | summary, file tree, search, file detail, impact, module graph |
| `/runs` | session; `runs:run` to stop or discard, `runs:merge` to merge | agent runs, their output, their diff, the merge |
| `/ai` | `ai:use` | ask, brainstorm, extract |
| `/usage` | session (per-person detail for admins) | the usage ledger, by feature, provider and day |
| `/activity`, `/health` | session / public | the log, the live stream, liveness |

## Phases

- **Done:** 32 screens, calm UI, local API, persisted work, requirement compiler, repository
  onboarding, live sync, ⌘K, test suites; then (2026-09-11) the modular backend with migrations,
  accounts, roles and permissions, the audit log, first-run setup, the Admin screens, the AI gateway,
  and Ask / Brainstorm / Add from text working live, with or without a key; then the database
  hardening (backups, integrity checks, append-only audit, the usage ledger) and the code index behind
  live Code Intelligence, Architecture and impact analysis; then (2026-09-12) the agent runtime, several
  agents per task in parallel worktrees, and merging from the UI.
- **Waiting on you:** a valid DeepSeek key (Admin → AI providers) — without one the runtime opens the
  worktree, runs the tests and reviews, but skips the steps that write code; and the `workflow` scope on
  the GitHub token so CI can be pushed.
- **Next:** retrieval that earns the name — embeddings over the code index, memory and the project's
  documents, so every agent is grounded in *this* repository rather than in its own recollection;
  pushing a branch and opening a pull request from the run screen; tree-sitter parsers in place of the
  pattern sets; roles per project; Postgres and a hosted mode; single sign-on.
