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
   ├── SQLite, WAL mode: documents, identity, audit, settings (server/neurocode.db)
   ├── secrets file, mode 0600: model API keys (server/secrets.json)
   └── AI gateway ─▶ DeepSeek (key set) · Ollama (model pulled) · offline rules (always)
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
  events.py        in-process fan-out to open tabs (Server-Sent Events)
  secrets.py       API keys on disk (0600), only ever reported masked
  auth.py          scrypt passwords, sessions, sign-in throttling, current_user / require()
  rbac.py          the permission catalogue and roles
  ai/gateway.py    provider choice, keys, breaker, test connection
  ai/compiler.py   requirement → plan
  ai/features.py   ask memory, brainstorm, extract facts from text
  onboarding.py    clone or read a repository and measure it
  routes/          auth · admin · work · knowledge · platform · ai · system
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
| `audit_log` | relational | sign-ins and every change to access, keys or settings |
| `settings` | relational | workspace settings (AI routing, provider options) |
| `schema_migrations` | relational | which migrations ran |

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
| `/admin` | `users:manage`, `roles:manage`, `teams:manage`, `audit:read`, `workspace:admin` | people, roles, teams, audit, workspace, AI providers, reset |
| `/tasks`, `/plans`, `/approvals`, `/decisions`, `/prefs` | session + the action's permission | the work |
| `/memory` | session + `memory:write` | facts, search, conflicts |
| `/projects`, `/mcp`, `/agents` | session + the action's permission | the platform |
| `/ai` | `ai:use` | ask, brainstorm, extract |
| `/activity`, `/health` | session / public | the log, the live stream, liveness |

## Phases

- **Done:** 32 screens, calm UI, local API, persisted work, requirement compiler, repository
  onboarding, live sync, ⌘K, test suites; then (2026-09-11) the modular backend with migrations,
  accounts, roles and permissions, the audit log, first-run setup, the Admin screens, the AI gateway,
  and Ask / Brainstorm / Add from text working live, with or without a key.
- **Waiting on you:** a valid DeepSeek key (Admin → AI providers) for model answers, and the
  `workflow` scope on the GitHub token so CI can be pushed.
- **Next:** the agent runtime (a real worktree per task, a model writing the diff, tests run in the
  worktree, review of the real diff, all through the approvals gate); tree-sitter indexing for
  Architecture and Code Intelligence; roles per project; Postgres and a hosted mode; single sign-on.
