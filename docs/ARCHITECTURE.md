# NeuroCode architecture

NeuroCode is an AI engineering OS for one operator, or a small team, working on their own machine.
This document is the plan the code follows: what runs where, how data is stored, who may do what,
and how every AI feature reaches a model.

## Shape

```
Browser  ── React 19 + Vite, one store (src/lib/data.tsx), auth (src/lib/auth.tsx)
   │  same-origin /api in dev (Vite proxy); VITE_API_URL in a build
   ▼
FastAPI (server/app/api) ── routes → services → repositories → Postgres
   ├── Postgres 16: every table, related and typed, migrated by Alembic
   │     ├── pgvector  embeddings, cosine distance under an HNSW index
   │     ├── tsvector  full text, generated columns, ranked by ts_rank
   │     └── pg_trgm + citext  fuzzy matching, and names that compare without case
   ├── secrets file, mode 0600: model API keys (server/secrets.json)
   ├── code index ─▶ Python ast · patterns for TS/JS, C#, Java, Go, T-SQL · git history
   └── AI gateway ─▶ eight lanes: free tiers first, then paid, then a local model, then rules
                     its own blocking pool (psycopg), because it waits on providers from a thread
                     every call written to the usage ledger
```

The public demo (Vercel) is the same web app with no API. It runs on the seed data inside the tab,
signed in as a demo owner, and never makes a request.

## Why these choices

- **Postgres, not SQLite.** The first version kept the domain as JSON documents in SQLite with the
  filtered fields lifted into columns. It worked, and it hid things: a project's task counts were a
  stored number that drifted from the truth, "which tasks is this agent carrying" was a scan of every
  task, and nothing stopped a task pointing at a project that had been deleted. Postgres answers those
  questions itself — a `GROUP BY` with an index behind it, a join, a foreign key — and brings the three
  things retrieval actually needs in one server: full text, vectors, and trigram matching.
- **Relational throughout.** Every entity is a table with real columns, real foreign keys and real
  constraints. `JSONB` is used only where the shape genuinely varies — a compiler's own report, a
  review's findings — never as a substitute for modelling something.
- **Layers, and only downwards.** `settings → data → models → repositories → services → api`. A route
  parses and permits; a service decides; a repository is the only thing that knows any SQL. It is what
  lets a service be tested against a throwaway database without changing a line.
- **One transaction per request.** The session dependency *is* the unit of work: everything a request
  does commits together, and a request that raises rolls back whole. The old store committed each
  statement on its own, which meant a failure halfway through left half a change behind.
- **One gateway for every model call.** Keys, routing, time-outs, the rejected-key breaker, the free-tier
  budgets and the fallback all live in one place. A feature never talks to a provider directly.
- **Local first, honest always.** Every AI feature works with no key. Output made by rules, not a
  model, is labelled as such in the UI.

## Backend layout

```
server/app/
  settings.py      every knob, typed and validated once (NEUROCODE_*, server/.env)
  data/            engine and pooling · the declarative Base · the seed loader · a readiness check
  models/          43 tables in six files: identity · work · knowledge · code · runtime · platform
  repositories/    the questions the screens ask, answered in SQL; nothing here commits
  services/        the decisions: identity, work and its gates, plans, runs, chat, retrieval,
                   onboarding, indexing, and the errors a route turns into a status code
  api/             deps.py (session, who is asking, require(...)) · errors.py · app.py
                   routes_auth · _work · _plans · _knowledge · _platform · _sessions · _runs
                   routes_code · _ai · _system · _admin · _admin_system · stream.py (SSE)
  agent/git.py     git as plain blocking functions: worktrees, safe paths, merges, tests
  ai/lanes.py      the lanes: a provider and a model each, with their free limits written down
  ai/gateway.py    which lane answers, keys, breakers, budgets, test connection
  ai/ledger.py     the four things the gateway asks of a database, and who answers them
  ai/compiler.py   requirement → plan
  ai/features.py   ask memory, brainstorm, extract facts from text
  events.py        in-process fan-out to open tabs (Server-Sent Events)
  secrets.py       API keys on disk (0600), only ever reported masked
server/alembic/    the migrations, and the only thing that creates a schema
```

The AI gateway is the one component that is not async, and deliberately: it spends its time waiting
on model providers, always from a worker thread, and a thread cannot hold the request's session. It is
given a four-method port instead — read a setting, write a setting, how much has this lane spent today,
append a line — implemented over a small blocking psycopg pool against the same database.

## Data model

43 tables, all related, all typed, in six groups. Every one is created by Alembic — the schema is never
written by hand, and a test that passes against a hand-made schema proves nothing.

| Group | Tables | Holds |
|---|---|---|
| **identity** (11) | `workspace`, `users`, `roles`, `role_permissions`, `user_roles`, `teams`, `team_members`, `sessions`, `audit_log`, `login_attempts`, `settings` | who exists, what they may do, and every attempt to sign in |
| **work** (12) | `projects`, `agents`, `tasks`, `task_checklist`, `task_agents`, `plans`, `plan_steps`, `plan_questions`, `approvals`, `decisions`, `prefs`, `activity` | the domain, and the log that streams to every open tab |
| **knowledge** (5) | `memory_facts`, `memory_tags`, `memory_conflicts`, `chunks`, `retrieval_runs` | what is remembered, and what can be retrieved |
| **code** (4) | `code_files`, `code_symbols`, `code_edges`, `code_index_runs` | the index: files, what they declare, what depends on what |
| **runtime** (6) | `runs`, `run_steps`, `run_conflicts`, `run_logs`, `chats`, `chat_messages` | agent runs and sessions, turn by turn |
| **platform** (5) | `mcp_servers`, `mcp_tools`, `permission_rules`, `brainstorms`, `ai_calls` | the tools it can reach, and the usage ledger behind every budget |

Types that do work, rather than being decoration:

- `citext` for emails and names, so `Rajat` and `rajat` are one person and the unique index says so.
- `tsvector`, generated and stored, over memory facts and chunks; ranked with `ts_rank`.
- `vector(1536)` for embeddings, under an HNSW index with `vector_cosine_ops`. Shorter vectors are
  padded, so changing the embedding model does not mean changing the schema.
- `pg_trgm` for the searches where a person half-remembers a name.
- `JSONB` only where the shape genuinely varies — a compiler's report, a review's findings, a run's
  targets — never as a place to avoid modelling something.
- Enum types for the small closed sets (a run's status, a log's level). Alembic does not diff enum
  *values*, so adding one is a hand-written migration; there is one in the tree that says so.

Two rules hold in `repositories/`: **nothing returns everything** — every list is paged, with a ceiling
the caller cannot raise — and **nothing commits**, because the unit of work belongs to the request.

## Keeping the data safe

- **Backups** are `pg_dump` into `server/backups/`, taken before every reset and on demand from
  Admin → Database, readable only by the account running the API.
- **Integrity** is the database's own job now: foreign keys with the right `ON DELETE` on every
  relationship, unique constraints where a duplicate would be a bug, and checks on the ranges.
  Admin → Database confirms the connection, that the schema is at Alembic head, and that nothing
  violates a constraint.
- **The audit log is append-only** and outlives the accounts it describes: deleting a user sets the
  entry's `user_id` to null rather than erasing what they did.
- **Sign-in throttling is a table.** It used to be a dictionary in the process, which forgot everything
  on a restart and protected nothing at all once there were two workers.
- **Compaction**: "Optimize & compact" runs `VACUUM ANALYZE` and reindexes, on its own connection —
  `VACUUM` cannot run inside a transaction.
- **Expired sessions** are removed every time the API starts.
- **Coming from the old stack**: `scripts/import-sqlite.py` carries a running workspace across.
  Passwords come over untouched — both stacks store `scrypt$n$r$p$salt$digest`, parameters and all — so
  everyone signs in afterwards with the password they already have.

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

## Retrieval: finding the few pieces that bear on a question

A **chunk** is the smallest piece worth retrieving on its own — a symbol with the lines that follow it,
a section of a document, a remembered fact. Chunks keep their own text, so an answer can quote them and
say where they came from (`pkg/tax.py#apply_gst:118`), instead of describing code from memory.

Two searches run over the same rows and are fused by reciprocal rank:

- **lexical**, FTS5 over the text. No key, no model, no waiting. It finds what you can name.
- **semantic**, cosine over embeddings, when a lane makes them. It finds what you meant but could not
  name — "where is the interstate tax split" reaching `apply_gst_breakup`.

Fusing them means neither has to win outright, and retrieval keeps working with no model at all: it
says it is lexical only rather than quietly becoming worse. Embeddings come from whichever lane serves
them — Gemini, Mistral, GitHub Models, or `nomic-embed-text` in Ollama on this machine — in batches,
normalised on the way in, stored as float32 blobs, so searching is a dot product and needs no
numerical library and no vector service.

Retrieval rides on the code index: indexing a project re-chunks it, so the chunks always match the
code as it is now. A failure there is reported and never loses an index that succeeded.

**Every session is grounded before the model is asked anything.** The question goes to retrieval first,
and what comes back is written into the conversation as its own turn — so the person can see exactly
what the model was handed, and the model can quote its refs. `find` is also a tool, so it can search
again mid-answer with better words.

## Sessions: a conversation that can act

You ask; a model answers, or it reaches for a tool; the tool runs **here**, and what it found goes back
into the conversation. The model never runs anything itself — it names a tool from a fixed catalogue
and the arguments are checked before anything happens, the same rule as the runtime, where a model may
only propose whole files.

The catalogue reads and nothing in it writes: `search_code` (the index), `read_file` (bounded, inside
the project, never `..` and never `.git`), `list_files`, `impact` (what depends on this, through the
dependency edges), `search_memory`, `project_summary`. Six tool calls at most per question; on the last
one the model must answer with what it has, so a session always ends in words. A tool that refuses or
breaks reports it into the conversation instead of ending it, and the model can correct itself.

Every turn is written to the database the moment it happens — your question **before** the model is
ever called, each tool call with what it returned, then the answer with the lane that wrote it. A
session survives a reload, a restart and a crash. With no lane able to answer, the question is still
kept and the session says so plainly rather than inventing one.

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
| `/projects/{id}/code/retrieval` | session; `projects:onboard` to build | the chunks, and what bears on a question |
| `/runs` | session; `runs:run` to stop or discard, `runs:merge` to merge | agent runs, their output, their diff, the merge |
| `/sessions` | session; `sessions:chat` to ask | conversations, their turns, their tool calls |
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
  agents per task in parallel worktrees, merging from the UI, model lanes (many free providers, one
  router), sessions (a conversation that reads the code with tools and keeps every turn), and retrieval
  (chunks, embeddings where a lane makes them, hybrid search grounding every session).
- **Waiting on you:** a valid DeepSeek key (Admin → AI providers) — without one the runtime opens the
  worktree, runs the tests and reviews, but skips the steps that write code; and the `workflow` scope on
  the GitHub token so CI can be pushed.
- **Next:** grounding the compiler and the agents in retrieval the way sessions already are; MCP servers
  as tools a session can actually reach; pushing a branch and opening a pull request from the run
  screen; tree-sitter parsers in place of the pattern sets; roles per project; Postgres and a hosted
  mode; single sign-on.
