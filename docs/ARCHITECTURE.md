# NeuroCode architecture

NeuroCode is an AI engineering OS for one operator, or a small team, working on their own machine.
This document is the plan the code follows: what runs where, how data is stored, who may do what,
and how every AI feature reaches a model.

## Shape

```
Browser  ── React 19 + Vite, one store (src/lib/data.tsx), auth (src/lib/auth.tsx), the catalogue (src/lib/access.ts)
   │  same-origin /api (the Vite proxy in dev and preview); VITE_API_URL when the API lives elsewhere
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

The web app holds no data of its own. With no API it says **Not connected**, why, and the one command
that starts one — there is no demo mode and no sample workspace to fall back on, so nothing on a screen
was ever invented by the browser. A brand-new workspace is empty too, except for the product's
catalogue — the permissions, the built-in roles and the agent roster, in `server/app/data/catalogue.json`,
written again on every start. Everything else in it, someone made.

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
- **Local first, honest always.** Nothing is made up when no model can answer. Planning and
  brainstorming refuse, and say which free key to add; asking memory quotes the matching facts,
  extracting facts picks policy sentences, and a review reads the diff by rules — each labelled as
  rules in the UI. A number appears on a screen only when something measured it.

## Backend layout

```
server/app/
  settings.py      every knob, typed and validated once (NEUROCODE_*, server/.env)
  data/            engine and pooling · the declarative Base · catalogue.json and the roster · the loader
                   the SQLite importer uses · a readiness check
  models/          43 tables in six files: identity · work · knowledge · code · runtime · platform
  repositories/    the questions the screens ask, answered in SQL; nothing here commits
  services/        the decisions: identity, work and its gates, plans, runs, chat, retrieval, testing,
                   workflows, evals, research, extensions, git, ops,
                   onboarding, indexing, and the errors a route turns into a status code
  api/             deps.py (session, who is asking, require(...)) · errors.py · app.py
                   routes_auth · _work · _plans · _knowledge · _platform · _sessions · _runs
                   routes_code · _ai · _system · _admin · _admin_system · _testing · _git · _extensions
                   routes_workflows · _evals · _research · _ops · _permissions · stream.py (SSE)
  agent/git.py     git as plain blocking functions: worktrees, safe paths, merges, tests
  agent/testparse  what a test runner printed, read into passed/failed/skipped and named failures
  agent/coverage   the coverage report a test step left on disk, summed by top-level folder
  ai/lanes.py      the lanes: a provider and a model each, with their free limits written down
  ai/gateway.py    which lane answers, keys, breakers, budgets, test connection
  ai/ledger.py     the four things the gateway asks of a database, and who answers them
  ai/compiler.py   requirement → plan
  ai/features.py   ask memory, brainstorm, extract facts from text
  services/mcp.py  a real MCP client: initialize and tools/list over stdio or HTTP, once, on request
  events.py        in-process fan-out to open tabs (Server-Sent Events)
  secrets.py       API keys on disk (0600), only ever reported masked
server/alembic/    the migrations, and the only thing that creates a schema
```

The AI gateway is the one component that is not async, and deliberately: it spends its time waiting
on model providers, always from a worker thread, and a thread cannot hold the request's session. It is
given a four-method port instead — read a setting, write a setting, how much has this lane spent today,
append a line — implemented over a small blocking psycopg pool against the same database.

## Data model

55 tables, all related, all typed, in six groups. Every one is created by Alembic — the schema is never
written by hand, and a test that passes against a hand-made schema proves nothing.

| Group | Tables | Holds |
|---|---|---|
| **identity** (11) | `workspace`, `users`, `roles`, `role_permissions`, `user_roles`, `teams`, `team_members`, `sessions`, `audit_log`, `login_attempts`, `settings` | who exists, what they may do, and every attempt to sign in |
| **work** (15) | `projects`, `agents`, `tasks`, `task_checklist`, `task_agents`, `plans`, `plan_steps`, `plan_questions`, `approvals`, `decisions`, `prefs`, `activity`, `test_expectations`, `workflow_definitions`, `workflow_steps` | the domain, the reusable workflows, and the log that streams to every open tab |
| **knowledge** (9) | `memory_facts`, `memory_tags`, `memory_hits`, `memory_conflicts`, `chunks`, `retrieval_runs`, `research_reports`, `research_angles`, `research_citations` | what is remembered, each time it was used, what can be retrieved, and research built on it |
| **code** (4) | `code_files`, `code_symbols`, `code_edges`, `code_index_runs` | the index: files, what they declare, what depends on what |
| **runtime** (8) | `runs`, `run_steps`, `run_conflicts`, `run_logs`, `chats`, `chat_messages`, `test_failures`, `test_coverage` | agent runs and sessions, turn by turn, and what their tests found |
| **platform** (8) | `mcp_servers`, `mcp_tools`, `brainstorms`, `eval_suites`, `eval_cases`, `eval_runs`, `eval_results`, `ai_calls` | the tools it can reach, the evals, and the usage ledger behind every budget |

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

- **Permissions** are `resource:action` strings from one catalogue, `server/app/data/catalogue.json`,
  served to anyone signed in at `GET /auth/catalogue` so every screen can name what a role lacks. Examples: `plans:compile`, `approvals:decide`, `memory:write`,
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

| Feature | With a model | With no model |
|---|---|---|
| Compile a requirement | restatement, steps, risks, questions, a confidence of its own | refused (409), with the free key to add; a provider's failure comes back as 502 with its reason |
| Ask memory | answer written from the matching facts, cited | the matching facts, quoted and cited, labelled "memory search, no model" |
| Brainstorm | problem, audience, MVP, risks, metrics, roadmap | refused (409), as compiling is |
| Extract facts | facts proposed from pasted text | policy sentences picked by rules, labelled |

Every time a fact is cited or handed to a model — an answer, a session's grounding, research, a
compile — a row goes into `memory_hits`, so how much a fact is used is counted, not estimated.

## Retrieval: finding the few pieces that bear on a question

A **chunk** is the smallest piece worth retrieving on its own — a symbol with the lines that follow it,
a section of a document, a remembered fact. Chunks keep their own text, so an answer can quote them and
say where they came from (`pkg/tax.py#apply_gst:118`), instead of describing code from memory.

Two searches run over the same rows and are fused by reciprocal rank:

- **lexical**, Postgres full text over the text (`tsvector`, ranked by `ts_rank`). No key, no model, no waiting. It finds what you can name.
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
of giving up, and only when every lane is spent does the feature say so: compiling and brainstorming
refuse, and the features with an honest rule-based answer give that, labelled.

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

124 paths. The running API describes every one at `/docs`; this is the map, with what each family needs.

| Prefix | Needs | What |
|---|---|---|
| `/auth` | public / session | status, setup, login, logout, me, change password |
| `/admin` | `users:manage`, `roles:manage`, `teams:manage`, `audit:read`, `workspace:admin` | people, roles, teams, audit, workspace, AI providers, database, reset |
| `/tasks`, `/plans`, `/approvals`, `/decisions`, `/prefs` | session + the action's permission | the work; deciding a run's gate resumes the run |
| `/memory` | session + `memory:write` | facts, search, archive, recalls (`/memory/hits`), conflicts: record one, keep a side |
| `/projects` | session + `projects:onboard` | the projects, and onboarding one |
| `/mcp` | session; `mcp:manage` (+ `workspace:admin` to trust or check a stdio server) | registered servers, trust, and a real check: connect, list tools, record latency |
| `/permissions/rules` | session | the standing rules the runtime applies: each project's answer to its first test run, and who gave it |
| `/projects/{id}/code` | session; `projects:onboard` to re-index | summary, file tree, search, file detail, impact, module graph |
| `/projects/{id}/code/retrieval` | session; `projects:onboard` to build | the chunks, the documents, and what bears on a question |
| `/projects/{id}/git` | session | the checkout's branches, commits, worktrees, conflicts and diffs, read from git |
| `/runs` | session; `runs:run` to stop, discard or send back, `runs:merge` to merge | agent runs, their output, their diff, the merge, and rework: a new run told what to change |
| `/testing`, `/projects/{id}/tests` | session; `runs:run` to run, `decisions:make` for expectations | each project's own test command, the failures it named, coverage it left |
| `/workflows` | session; `workflows:write` to author; `plans:compile` + `plans:decide` to run | reusable step lists that become ordinary plans and runs |
| `/evals` | session; `evals:write` to author, `ai:use` to run, `memory:write` for a lesson | suites of cases scored by stated checks against the real features |
| `/research` | session; `ai:use` to start | a question split into angles, each answered from retrieval, cited |
| `/extensions` | session | skills, commands, hooks and plugins read from disk — hooks are shown, never run |
| `/sessions` | session; `sessions:chat` to ask | conversations, their turns, their tool calls |
| `/ai` | `ai:use` | ask, brainstorm, extract |
| `/agents`, `/models`, `/usage` | session (per-person usage for admins) | the roster from what runs did, the router and its lanes, the usage ledger priced per lane, by day, agent, project, and the costliest calls |
| `/ops` | session; `workspace:admin` for secrets | this machine: services, checks, deliveries, logs, containers, which keys exist |
| `/activity`, `/activity/summary`, `/health` | session / public | the log, the live stream, its figures over every row, liveness |

## Phases

- **Done:** 39 screens on a calm UI; the Postgres back end — relational, migrated, 366 tests — with
  accounts, roles, an audit log the database keeps append-only, and the AI gateway routing across free
  model lanes; requirement → plan → questions → agents in parallel worktrees → tests → a review from a
  different model → your signature, or sent back with notes → merge; memory with recorded recalls,
  retrieval (words and meaning, fused) and the code index; sessions that read the code; real back ends
  behind every screen, and, as of 2026-09-16, **no sample data anywhere**: no demo mode, no seed, no
  offline planner or brainstorm template, and no figure nothing measured. The ACP screen is gone, because
  nothing on this machine speaks it, and so is the table of tool rules nothing enforced. The checks run on
  the real stack — a throwaway Postgres, the API and a stub model (`scripts/stack.mjs`) — building their
  own data: the end-to-end run from an empty workspace, and the smoke and layout lint on the tests' fixture.
- **Decided, on purpose:** NeuroCode never executes a repository's hooks or a command's shell lines; a
  project's own tests run only after a person allows it, once per project.
- **Waiting on you:** a model key (Admin → AI providers) for planning, brainstorming and anything that
  writes code — without one those refuse and say so; and the `workflow` scope on the GitHub
  token so CI can be pushed.
- **Next:** grounding the compiler and the agents in retrieval the way sessions and research already
  are; MCP servers as tools a session can reach; pushing a branch and opening a pull request from the run
  screen; tree-sitter parsers in place of the pattern sets; roles per project; a hosted mode; single
  sign-on.
