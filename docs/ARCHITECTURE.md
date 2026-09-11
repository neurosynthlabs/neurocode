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
  ai/gateway.py    provider choice, keys, breaker, test connection, the usage ledger
  ai/compiler.py   requirement → plan
  ai/features.py   ask memory, brainstorm, extract facts from text
  onboarding.py    clone or read a repository and measure it
  codeindex.py     parse the code into files, symbols and edges; search, impact and the module graph
  routes/          auth · admin · work · knowledge · platform · code · ai · system
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

## AI features

| Feature | With a model | Offline (no key) |
|---|---|---|
| Compile a requirement | full restatement, steps, risks, questions | keyword planner that keeps your wording |
| Ask memory | answer written from the matching facts, cited | the matching facts, quoted and cited |
| Brainstorm | problem, audience, MVP, risks, metrics, roadmap | the same structure as a guided template |
| Extract facts | facts proposed from pasted text | policy sentences picked by rules |

The order under `auto` is DeepSeek (a key is set), then Ollama (the model is pulled), then the offline
rules. Keys are set in Admin → AI providers and stored in the secrets file. A key the provider
rejects is noted once and not sent again until it changes.

## API map

| Prefix | Needs | What |
|---|---|---|
| `/auth` | public / session | status, setup, login, logout, me, change password |
| `/admin` | `users:manage`, `roles:manage`, `teams:manage`, `audit:read`, `workspace:admin` | people, roles, teams, audit, workspace, AI providers, database, reset |
| `/tasks`, `/plans`, `/approvals`, `/decisions`, `/prefs` | session + the action's permission | the work |
| `/memory` | session + `memory:write` | facts, search, conflicts |
| `/projects`, `/mcp`, `/agents` | session + the action's permission | the platform |
| `/projects/{id}/code` | session; `projects:onboard` to re-index | summary, file tree, search, file detail, impact, module graph |
| `/ai` | `ai:use` | ask, brainstorm, extract |
| `/usage` | session (per-person detail for admins) | the usage ledger, by feature, provider and day |
| `/activity`, `/health` | session / public | the log, the live stream, liveness |

## Phases

- **Done:** 32 screens, calm UI, local API, persisted work, requirement compiler, repository
  onboarding, live sync, ⌘K, test suites; then (2026-09-11) the modular backend with migrations,
  accounts, roles and permissions, the audit log, first-run setup, the Admin screens, the AI gateway,
  and Ask / Brainstorm / Add from text working live, with or without a key; then the database
  hardening (backups, integrity checks, append-only audit, the usage ledger) and the code index behind
  live Code Intelligence, Architecture and impact analysis.
- **Waiting on you:** a valid DeepSeek key (Admin → AI providers) for model answers, and the
  `workflow` scope on the GitHub token so CI can be pushed.
- **Next:** the agent runtime (a real worktree per task, a model writing the diff, tests run in the
  worktree, review of the real diff, all through the approvals gate); tree-sitter parsers in place of
  the pattern sets; roles per project; Postgres and a hosted mode; single sign-on.
