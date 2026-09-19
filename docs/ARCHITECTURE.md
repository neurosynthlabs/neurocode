# NeuroCode architecture

NeuroCode is an AI engineering OS for one operator, or a small team, working on their own machine.
This document is the plan the code follows: what runs where, how data is stored, who may do what,
and how every AI feature reaches a model.

## Shape

```
Web app       React 19 + Vite, one store (src/lib/data.tsx), auth (src/lib/auth.tsx), the catalogue (src/lib/access.ts)
Desktop app   Electron (desktop/): the same web app, a local server that proxies /api, native dialogs, menus, Dock badge
Terminal      `nc` (cli/): Typer + Rich + Textual, signed in with a personal access token
   │  same-origin /api (the Vite proxy in dev, Caddy in production, the desktop's own server) · SSE · WebSockets
   ▼
FastAPI (server/app/api) ── routes → services → repositories → Postgres
   ├── Postgres 16: 70 tables, related and typed, migrated by Alembic
   │     ├── pgvector  embeddings, cosine distance under an HNSW index
   │     ├── tsvector  full text, generated columns, ranked by ts_rank
   │     └── pg_trgm + citext  fuzzy matching, and names that compare without case
   ├── secrets file, mode 0600: model API keys (server/secrets.json)
   ├── code index ─▶ Python ast · tree-sitter for 25 languages · T-SQL patterns · git history
   ├── agent runtime ─▶ git worktrees, the project's own tests and checks, tool rules, gates
   ├── this machine (Owner only, off when hosted) ─▶ files, PTY terminals, run configs, debugpy / Node
   │     inspector, the project's own checkers, language servers, Jupyter kernels, DuckDB over data files
   ├── scheduler ─▶ routines on a cron cadence, one process at a time under an advisory lock
   └── AI gateway ─▶ eight lanes: free tiers first, then paid, then a local model
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
  data/            engine and pooling · the declarative Base · catalogue.json and the roster · tech.json and the
                   Blueprint template bank (blueprints/*.json) · change announcements for the stream · a readiness check
  models/          70 tables in seven files: identity · work · knowledge · code · runtime · platform · enums
  repositories/    the questions the screens ask, answered in SQL; nothing here commits
  services/        the decisions: identity and tokens, work and its gates, plans and taste, runs, chat, custom
                   agents and reviews, retrieval, testing, workflows, routines and the inbox, evals, research,
                   extensions, git, ops, onboarding, sources and references, archives, Blueprints, indexing,
                   and this machine — files, terminals, debugging, diagnostics, language servers,
                   notebooks, data files
  api/             deps.py (session or token, who is asking, require(...)) · errors.py · app.py · stream.py (SSE)
                   32 route files, one per family (see the API map)
  agent/           git as plain blocking functions: worktrees, safe paths, merges, tests; what a test runner
                   printed; coverage; reviewing any diff
  ai/              lanes · gateway · ledger · compiler (requirement → plan) · features · blueprint
  codeindex.py     the index: every file, its symbols, imports and complexity
  treesitter.py    the queries that read 25 languages from their syntax trees
  secrets.py       API keys on disk (0600), only ever reported masked
server/alembic/    the migrations, and the only thing that creates a schema
cli/               `nc`, the terminal client — its own package and environment
desktop/           the Electron shell
deploy/            Docker Compose (Postgres with pgvector, the API, Caddy with HTTPS) for a server of your own
```

The AI gateway is the one component that is not async, and deliberately: it spends its time waiting
on model providers, always from a worker thread, and a thread cannot hold the request's session. It is
given a four-method port instead — read a setting, write a setting, how much has this lane spent today,
append a line — implemented over a small blocking psycopg pool against the same database.

## Data model

70 tables, all related, all typed, in six groups. Every one is created by Alembic — the schema is never
written by hand, and a test that passes against a hand-made schema proves nothing.

| Group | Tables | Holds |
|---|---|---|
| **identity** (12) | `workspace`, `users`, `roles`, `role_permissions`, `user_roles`, `teams`, `team_members`, `sessions`, `api_tokens`, `audit_log`, `login_attempts`, `settings` | who exists, what they may do, how scripts sign in, and every attempt to sign in |
| **work** (25) | `projects`, `project_sources`, `project_references`, `agents`, `custom_agents`, `tasks`, `task_checklist`, `task_agents`, `plans`, `plan_steps`, `plan_questions`, `plan_comments`, `approvals`, `decisions`, `prefs`, `activity`, `test_expectations`, `workflow_definitions`, `workflow_steps`, `schedules`, `schedule_fires`, `run_configs`, `blueprints`, `blueprint_templates`, `code_reviews` | the domain: projects of many sources, plans shaped with a person, gates, workflows and routines, Blueprints, reviews, and the log that streams to every open tab |
| **knowledge** (11) | `memory_facts`, `memory_tags`, `memory_hits`, `memory_conflicts`, `chunks`, `retrieval_runs`, `research_reports`, `research_angles`, `research_citations`, `taste_signals`, `taste_rules` | what is remembered, each time it was used, what can be retrieved, research built on it, and the taste learned from what people accept |
| **code** (4) | `code_files`, `code_symbols`, `code_edges`, `code_index_runs` | the index: files, what they declare (with where it starts and ends), what depends on what |
| **runtime** (9) | `runs`, `run_steps`, `run_conflicts`, `run_logs`, `chats`, `chat_messages`, `chat_files`, `test_failures`, `test_coverage` | agent runs and sessions, turn by turn, what was attached, and what their tests found |
| **platform** (9) | `mcp_servers`, `mcp_tools`, `tool_rules`, `brainstorms`, `eval_suites`, `eval_cases`, `eval_runs`, `eval_results`, `ai_calls` | the tools it can reach and the rules for every tool, the evals, and the usage ledger behind every budget |

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

Onboarding a source reads every file into the index: its language, lines, a SHA-1, a complexity count
(decision points in the syntax tree, so a comment that says "if" counts for nothing) and, in a git
repository, its changes over 90 days. Python and notebooks are parsed by Python's own `ast`; 25 more
languages — TypeScript and JavaScript (with JSX), Go, Rust, Java, Kotlin, Scala, C#, C, C++, Ruby, PHP,
Swift, Dart, Lua, R, Julia, Elixir, Haskell, OCaml, Zig, Shell, PowerShell and the script blocks of Vue
and Svelte — by tree-sitter, from grammars that ship with the server, so nothing is downloaded and it
works offline; T-SQL keeps its patterns, which know tables and procedures. The index records which
parser read each language, and names the languages it saw but could not read rather than pretending.

Each file yields its declarations (with the line they start and end on and whether they are exported),
its imports, the names it calls and the types it uses. Imports resolve the way each language does it —
relative paths and index files, `go.mod` modules, Rust's `crate::`, Java, Kotlin and C# packages and
namespaces, quoted includes, `require_relative`, `package:` imports — and a file also depends on the file
that declares a type or function it names. Any file that reads, writes or calls a table or procedure
declared in the repository's SQL gets an edge to it. Impact analysis walks those edges backwards with a
recursive query, eight steps deep, and reports direct users, everything reached through them, the
modules crossed, the tests that notice and the data touched — with a confidence that says how exact the
parsers involved were. A large repository is parsed by worker processes and written in bulk;
re-indexing replaces a project's rows in one transaction. A project's further sources are indexed
under their labels, so one search spans them all.

## Access control

- **Permissions** are `resource:action` strings from one catalogue, `server/app/data/catalogue.json`,
  served to anyone signed in at `GET /auth/catalogue` so every screen can name what a role lacks — 23 of
  them, from `plans:compile` and `approvals:decide` to `agents:manage`, `rules:manage` and `machine:access`.
- **Roles** are named sets of permissions. Built in: Owner, Admin, AI Project Manager, Engineer and Viewer,
  written again from the catalogue on every start. Admins can add custom roles. A user's permissions are
  the union of their roles.
- **Enforcement** happens in the API on every route: reads need a session; each change needs its
  permission. The UI mirrors it with `can()`, so a button a role cannot use is disabled with a reason.
- **Guards:** the last active Owner cannot be demoted or disabled, and nobody can disable themselves.
- **Sessions:** an opaque random token in an HttpOnly, SameSite=Lax cookie (Secure behind HTTPS).
  Changing a request needs the `X-NC-Client` header too, so a page on another site cannot make the
  browser act on your behalf. Five wrong passwords lock that account for 30 seconds.
- **Personal access tokens** (`nc_pat_…`, Settings → Access tokens) are how `nc` and scripts sign in: a
  token acts as its person, cut to the scopes it names, stored only as a hash, expiring and revocable,
  every use touching `last_used_at`. An empty scope list means everything the person holds **except**
  `machine:access` — a leaked token cannot open a shell unless it was made to — and a token can never
  make or revoke tokens.
- **This machine** — browsing and editing files, terminals, running and debugging, checks, language
  servers, kernels, data files — needs `machine:access` (the Owner's), stays inside
  `NEUROCODE_MACHINE_ROOTS`, and is switched off altogether on a hosted server
  (`NEUROCODE_MACHINE_ACCESS=false`). WebSockets check the same session or token and their origin.
- **First run:** with no users, the app opens the Setup wizard. It names the workspace and creates
  the first Owner, who then creates everyone else. On a server reachable from the internet, setup also
  needs `NEUROCODE_SETUP_TOKEN`, so nobody else can claim the workspace first.

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

**Governed, not unattended.** Every file an agent writes and every command a run executes is checked
against the tool rules — `allow`, `ask` or `deny` by glob, for the workspace or one project — before it
happens. An `ask` stops the run at a gate that takes more than yes or no: allowed once, for the rest of
this run, or always in this project (which writes an audited rule). An agent that is unsure can stop and
ask a question in words; the answer resumes the step and is kept in the project's memory. A plan can be
dispatched step by step, with a gate before each one, or with a goal budget: the run goes again until a
model on another lane judges every acceptance criterion met, or the attempts run out. Every step's commit
is kept, so one can be reverted; an accepted run can be pushed to the remote with the compare link.

**Owners beyond the roster.** A plan or workflow step can be owned by a custom agent — the workspace's,
the project's, or one the repository declares in `.neurocode/agents/*.md` or `.claude/agents/*.md` — as
long as it is a subagent that may write files. Its instructions come first; the runtime's rules follow
and win, and its tools can only narrow what the tool rules allow.

**Checks and reviews.** Beside the tests, a run executes the project's own checks (lint, type checks),
and what they print is read into `file:line:col` problems on the run. The review, like a review of any
diff a person asks for on demand, is read on a lane other than the writer's, briefed by the repository's
`REVIEW.md`.

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
normalised on the way in and stored in a pgvector column under an HNSW index, so searching is one SQL
query and needs no vector service of its own.

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

The catalogue: `find` and `search_code` (retrieval and the index), `read_file` (bounded, inside the
project, never `..` and never `.git`), `list_files`, `impact`, `search_memory`, `project_summary`,
`load_skill`, and — behind the tool rules — `web_fetch`, `web_search` and the tools of registered MCP
servers. A rule that says `ask` puts a permission card in the conversation: allow once, allow for this
session, or refuse. Answers stream as they are written, with the model's reasoning folded away; a long
session is compacted into a summary turn before it overflows the lane's window, and the meter says how
full it is. A session can be forked at any turn, exported as Markdown, given files and pictures, and
asked through an agent ("Ask <agent>"), which answers with that agent's instructions, lane and tools.

Every turn is written to the database the moment it happens — your question **before** the model is
ever called, each tool call with what it returned, then the answer with the lane that wrote it. A
session survives a reload, a restart and a crash. With no lane able to answer, the question is still
kept and the session says so plainly rather than inventing one.

## The Workbench: this machine, in the browser

On the machine the API runs on — never on a hosted server — the Owner works in the Workbench as in an
editor: folders and files anywhere inside the machine's roots (a native dialog in the desktop app), a
CodeMirror editor that saves only over the version it opened (a stale save is a 409, not a lost change),
terminals that are real PTYs over a WebSocket, run configurations, and a debugger — Python through
debugpy and the Debug Adapter Protocol, Node through its inspector — with breakpoints, stepping, the stack
and variables. A project may hold several sources (repositories or folders, each with a label), and its
paths carry the label, so one tree, one search and one terminal list span them all.

What the Workbench adds for any language and for data work:

- **Problems.** "Check" runs the project's own checkers — tsc, eslint or oxlint, ruff, mypy, pyright,
  go vet, cargo, Gradle or Maven, dotnet build — only those the repository declares and the machine has,
  from its `node_modules/.bin` or its `.venv` first. Their machine-readable output becomes problems in a
  panel, counts in the status bar and underlines in the editor. A tool that is declared but missing is
  named, with what installs it. When a language server is installed (pyright, typescript-language-server,
  gopls, rust-analyzer), hover and go to definition come from it; none is ever installed silently.
- **Notebooks.** An `.ipynb` opens as cells — Markdown, code, outputs (text, images, tables, errors) — that
  can be edited, moved and saved like any file, and run through a Jupyter kernel started for the project's
  own environment, streamed over a WebSocket; interrupt, restart, and "Explain this cell" / "Fix this
  error", which start a session.
- **Data.** CSV, TSV, Parquet, JSON Lines and SQLite files open as a grid paged and sorted by the server,
  with exact column statistics and a small chart per column, and a SQL box: DuckDB over the file as a view
  named `data` (SQLite's own tables for a database), one read-only statement, with external access switched
  off and the configuration locked — so a query reads that file and nothing else, not another file and not
  the network.

## Three ways in: web, terminal, desktop

- **Web.** Every feature, in any browser, on any screen down to a phone's width.
- **Terminal.** `nc` (`uv tool install ./cli`) signs in with a personal access token kept in the OS
  keychain and talks to any NeuroCode server: `nc ask`, a full-screen `nc chat` with streaming answers,
  permission cards, `@` mentions and slash commands, `nc plan`, `nc approve` / `deny`, `nc runs --watch`,
  `nc merge`, `nc push`, `nc memory`, `nc open`. Every command takes `--json`, and exits 3 when it is
  waiting on a person, so scripts can tell.
- **Desktop.** An Electron app (`npm run desktop:build`, an arm64 .dmg) finds a running API or starts one,
  serves the same web app through a local server on one origin, locks navigation to it, and adds what
  only a desktop can do: native folder and file dialogs, Reveal in Finder, Open in your editor at a line,
  native notifications, the approvals count on the Dock, menus and shortcuts, and `neurocode://` links —
  which is how `nc open` hands a project to it.

## Routines and the inbox

A **routine** is a requirement or a workflow on a cadence (five-field cron, in UTC), behind a webhook, or
on "Run now". A scheduler loop in the API fires due routines under a Postgres advisory lock, so two
processes never fire the same one. A fire acts as the routine's maker, with their permissions checked at
fire time, and goes through exactly the path a person's would: compiled, dispatched, stopping at the
project's one-time test gate and at the signature — nothing merges unattended. A routine whose last fire
is still waiting does not fire again, and says why. A webhook's body reaches the compiler only as quoted
data, never as instructions.

The **inbox** on the Command Center answers "what needs me": approvals, agents' questions, runs at the
signature and sessions waiting on a permission; what is working; and what finished since your last visit.
It can notify you — through the desktop app natively, or the browser's notifications when you allow them.

## Starting a project, and Blueprints

A project starts from a folder on this machine, an archive (a zip or tarball on the machine or uploaded —
every entry checked before anything is written, unpacked into a new folder only), a clone, or an empty
folder made and committed once. It can hold further sources, **reference** sources that are indexed and
read for grounding but never written, and references to other projects.

For something that does not exist yet, the **Blueprint** wizard asks what is being built — the idea, who it is
for, scale and traffic, data and compliance, team, hosting, budget and timeline — and offers an architecture from a bank of templates (`server/app/data/blueprints`, 17
of them, from a Next.js full stack and a FastAPI + React SPA to Kafka event streaming, a RAG service, a
data pipeline and a desktop app) over a catalogue of 242 technologies, or a blank one. Each of its sixteen
layers — front end, mobile, desktop, back end, database, cache, queue, search, auth, AI/ML, data,
infrastructure, CI/CD, observability, testing, hosting — is edited by a person; a model may
review it and propose changes, each accepted or rejected on its own. A person's own templates are kept in
their library and can be exported and imported as JSON or YAML. Scaffolding makes the repository,
onboards it and compiles the plan whose agents write the code — through the same review and signature
as everything else.

## Hosting a server of your own

`deploy/` holds a Docker Compose stack for one small server (Oracle Cloud's Always Free Ampere VM is the
one it is written for): Postgres 16 with pgvector, the API image, and Caddy in front with automatic HTTPS,
serving the built web app and proxying `/api` with streaming kept open. Migrations run when the API
starts. A hosted server keeps session cookies `Secure`, trusts the proxy's headers, needs the setup token
for the first Owner, and has machine access switched off: the Workbench's machine features belong to the
machine a person sits at.

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

244 paths in 32 route files. The running API describes every one at `/docs`; this is the map, with what
each family needs. "Machine" means `machine:access` with machine access switched on (it is off on a
hosted server).

| Prefix | Needs | What |
|---|---|---|
| `/auth` | public / session | status, setup (with the setup token on a hosted server), login, logout, me, change password, the catalogue |
| `/tokens` | a signed-in person (never a token) | your personal access tokens: make one (shown once), list, revoke |
| `/admin` | `users:manage`, `roles:manage`, `teams:manage`, `audit:read`, `workspace:admin` | people, roles, teams, audit, workspace, AI providers, database, reset |
| `/tasks`, `/plans`, `/approvals`, `/decisions`, `/prefs` | session + the action's permission | the work; plans shaped step by step, with acceptance criteria, comments and revisions; deciding a gate — with a scope, or an answer in words — resumes the run |
| `/memory`, `/taste` | session + `memory:write` | facts, search, archive, recalls, conflicts; the taste learned from what people accept and reject |
| `/projects` | session + `projects:onboard` | projects and onboarding; sources, reference sources and referenced projects; code index and retrieval; git; tests; sessions; plans; reviews on demand |
| `/blueprints` | session; `plans:compile` to design; `projects:onboard` + machine to scaffold | the technology catalogue, the template bank and a person's own, blueprints, a model's review, export and import, scaffolding |
| `/machine`, `/run-configs`, `/debug` | machine | places and folders, files (read, save over what was opened, new, mkdir), archives and empty projects, terminals (WebSocket), run configurations, debug sessions (WebSocket) |
| `/diagnostics`, `/lsp` | machine | the checkers a folder declares, checks and their problems, a file's problems; hover, definition and symbols from a language server |
| `/notebooks` | machine | open and save a notebook, kernels for its language, start, interrupt, restart, stop; cells run over a WebSocket |
| `/data` | machine | open a data file, rows (paged, sorted), column statistics, a chart, one read-only query |
| `/runs` | session; `runs:run` to stop, discard, revert or send back; `runs:merge` to merge or push | agent runs, their output, gates, checks and their problems, the diff, the merge, the push, rework |
| `/reviews`, `/agents` | session; `runs:run` to ask for a review; `agents:manage` to write an agent; `sessions:chat` to try one | reviews of any diff and what they became; the roster and custom agents, including those a repository declares |
| `/permissions` | session; `rules:manage` to write a rule | the standing answers the runtime applies, and the tool rules — allow, ask or deny — for every tool |
| `/schedules`, `/inbox` | session; `workflows:write` to write a routine, plus `plans:compile` + `plans:decide` to run it now; the webhook's own token | routines, their fires and webhooks; what needs you, what is working, what finished since you last looked |
| `/sessions` | session; `sessions:chat` to ask | conversations, their turns and tool calls, permission cards, uploads, forks, exports, compaction |
| `/testing` | session; `runs:run` to run, `decisions:make` for expectations | each project's own test command, the failures it named, coverage it left |
| `/workflows` | session; `workflows:write` to author; `plans:compile` + `plans:decide` to run | reusable step lists that become ordinary plans and runs |
| `/evals` | session; `evals:write` to author, `ai:use` to run, `memory:write` for a lesson | suites of cases scored by stated checks against the real features |
| `/research` | session; `ai:use` to start | a question split into angles, each answered from retrieval, cited |
| `/mcp` | session; `mcp:manage` (+ `workspace:admin` to trust a stdio server) | registered MCP servers, a real check and tool calls |
| `/web` | session; `ai:use` to search or fetch; `workspace:admin` for the search key | the web tools: whether search can answer, a search, a page fetched through the address guard |
| `/extensions` | session | skills, commands, hooks and plugins read from disk — hooks are shown, never run |
| `/ai` | `ai:use` | ask, brainstorm, extract |
| `/models`, `/usage` | session (per-person usage for admins) | the router and its lanes, the usage ledger priced per lane, by day, agent, project, and the costliest calls |
| `/ops` | session; `workspace:admin` for secrets | this machine: services, checks, deliveries, logs, containers, which keys exist |
| `/activity`, `/health` | session / public | the log, the live stream (activity, changes, run lines, session turns, routines, reviews, reset), its figures, liveness |

## Phases

- **Done:** the Postgres back end — 70 tables, 244 paths, about 800 tests — with accounts, roles, personal
  access tokens and an append-only audit log; the AI gateway routing across free model lanes;
  requirement → plan shaped with a person → agents in parallel worktrees, governed by tool rules and
  gates → tests, checks and a review from a different model → your signature → merge or push; memory,
  retrieval and taste; the code index for 25+ languages; sessions that read the code, the web and MCP
  tools behind permission cards; custom agents and reviews of any diff; routines and the inbox; Blueprints;
  the Workbench on this machine — files, terminals, run, debug, problems, language servers, notebooks and
  data; and the three ways in — web, `nc` in the terminal, and the desktop app. No sample data anywhere:
  a new workspace is empty, and no figure appears that nothing measured. The checks run on the real
  stack — a throwaway Postgres, the API and a stub model (`scripts/stack.mjs`) — building their own data:
  the end-to-end run from an empty workspace (38 steps, from setup to a notebook's kernel), and the smoke
  and layout lint on the tests' fixture.
- **Decided, on purpose:** NeuroCode never executes a repository's hooks or a command's shell lines on a
  model's say-so; a project's own tests and checks run only after a person allowed them; a hosted server
  has no machine access; language servers, kernels and checkers are the machine's own, never installed
  silently.
- **Waiting on you:** a model key (Admin → AI providers) for planning, brainstorming and anything that
  writes code — without one those refuse and say so; the `workflow` scope on the GitHub token so CI can
  be pushed; a Developer ID to sign the desktop app.
- **Next:** hosting on the Oracle Always Free VM with eurex.dev; roles per project; single sign-on; the
  server bundled inside the desktop app.
