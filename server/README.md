# NeuroCode local API

FastAPI over Postgres 16. It holds the whole workspace — accounts, the work, memory, the code index, agent
runs and sessions — and it does the parts of the product that act: compiling requirements into plans,
onboarding and indexing repositories, running agents in git worktrees, answering from retrieval, and
routing every model call through one gateway.

It is a single-workspace service. Locally it binds to `127.0.0.1`; every route except `/health` and the
sign-in routes needs a session or a personal access token, and a change riding on the session cookie must also
carry the `X-NC-Client` header. To serve it beyond this machine, use `../deploy/` — HTTPS in front, Secure
cookies, the setup token, and machine access switched off — rather than exposing this process directly.

`docs/ARCHITECTURE.md` at the repository root is the design; this file is how to run it.

## Run

```bash
brew install postgresql@16 pgvector && brew services start postgresql@16   # once
uv run python ../scripts/bootstrap-db.py        # once: the databases, and the extensions a superuser must enable
uv run alembic upgrade head                     # after every pull
uv run uvicorn app.api.app:create_api --factory --host 127.0.0.1 --port 8787
uv run pytest -q                                # against neurocode_test, on the fixture in tests/fixtures
```

From the repository root, `npm run dev:start` runs this and the web app together, and checks the database
first — no server, no database and no migrations each print the one command that fixes them
(`uv run python -m app.data.check` asks the same question on its own).

A brand-new workspace is empty. The only rows it holds from the start are the product's catalogue — the
permissions, the built-in roles and the agent roster, from `app/data/catalogue.json` — and those are
written again on every start, so an upgrade brings whatever was added to them. Everything else is made
by someone: onboard a repository, compile a requirement, add a fact. Admin → Database → Reset empties the
workspace again (after a `pg_dump` backup) and keeps people, roles, teams, the roster, keys, settings and
the audit log. The tests bring their own workspace, `tests/fixtures/workspace.json`.

Coming from the SQLite version: `uv run python ../scripts/import-sqlite.py --dry-run`, then without
`--dry-run`. Accounts come across with their passwords, and signed-in browsers stay signed in. The old
store wrote its sample workspace into itself on every start; those rows are named in the script, left
behind, and counted in its output.

## Settings

Everything is `NEUROCODE_*`, read from the environment or `server/.env` (`app/settings.py` lists them all):

| Variable                      | Default                                                             |                                                                                |
| ----------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `NEUROCODE_DATABASE_URL`      | `postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode` | a plain `postgres://` URL is upgraded                                          |
| `NEUROCODE_TEST_DATABASE_URL` | `…/neurocode_test`                                                  | what the tests migrate and roll back inside                                    |
| `NEUROCODE_COMPILER`          | `auto`                                                              | `auto` · `free` · `local` · `rules` (no model: compile and brainstorm refuse) · a lane id |
| `NEUROCODE_PG_BIN_DIR`        | —                                                                   | where `pg_dump` is, if it is not on PATH or in a usual install location        |
| `NEUROCODE_CORS_ORIGIN_REGEX` | localhost only                                                      | origins allowed to carry the session cookie                                    |
| `NEUROCODE_MACHINE_ACCESS`    | `true`                                                              | the Workbench's machine features; `false` on a hosted server                   |
| `NEUROCODE_MACHINE_ROOTS`     | `~`                                                                 | the folders the machine features may open, `:`-separated                        |
| `NEUROCODE_SETUP_TOKEN`       | —                                                                   | required to create the first Owner when set (always set it on a hosted server) |
| `NEUROCODE_COOKIE_SECURE`     | `false`                                                             | `true` behind HTTPS                                                            |
| `NEUROCODE_SCHEDULER`         | `true`                                                              | the routines' scheduler loop                                                   |
| `NEUROCODE_KERNELS_MAX`       | `8`                                                                 | Jupyter kernels at once (also `_KERNELS_PER_PERSON`, `_KERNEL_IDLE_MINUTES`)   |

Model keys are not settings: they live in `server/secrets.json` (mode 0600), set from Admin → AI
providers, and are only ever reported masked.

## Endpoints

244 paths. The running API describes every one at `http://127.0.0.1:8787/docs`; this is the map.

| Family | Paths | What it holds |
|---|---:|---|
| `/auth` | 7 | setup (with the setup token on a hosted server), sign-in and out, who is asking, password · the catalogue: permission labels, role names, the roster |
| `/tokens` | 2 | your personal access tokens — made (shown once), listed, revoked; managed only from a signed-in session, never by a token |
| `/admin` | 17 | people, roles, teams, permissions, workspace and its security rules, audit log · database health, backup, check, optimize, reset · AI lanes and their test |
| `/projects` | 17 | the projects and onboarding · sources, reference sources and referenced projects · plans, sessions and reviews of a project · tests |
| `/projects/{pid}/code` | 11 | the index summary (languages and their parsers), file tree, one file, search, impact, module graph, re-index · retrieval |
| `/projects/{pid}/git`, `/tests` | 6 | branches, commits, worktrees, conflicts and diffs read from git · the test command, its failures, and expectations |
| `/blueprints` | 13 | the technology catalogue, the template bank and a person's own, blueprints and their revisions, a model's review, export and import (JSON, YAML), scaffolding |
| `/tasks`, `/plans`, `/approvals` | 16 | the board · compile, shape (steps, acceptance criteria, comments, revisions), answer, dispatch · the gates, answered with a scope or in words |
| `/permissions` | 4 | the standing answers the runtime applies, and the tool rules — allow, ask or deny for every tool — with a dry run |
| `/runs` | 10 | agent runs, their logs, the diff, checks and their problems, cancel, discard, revert a step, merge, push, rework |
| `/reviews`, `/agents` | 7 | reviews of any diff on demand, sent to a session or made into a plan · the roster and custom agents, and trying one |
| `/sessions` | 14 | conversations that can act: turns, tool calls, permission cards, uploads, forks, exports, compaction |
| `/schedules`, `/inbox` | 9 | routines, their cadence, fires and webhook · what needs you, what is working, what finished since you last looked |
| `/memory`, `/taste` | 13 | search, add, pin, archive, recalls, conflicts · taste signals and the rules learned from them |
| `/machine`, `/run-configs`, `/debug` | 20 | this machine (Owner, machine access on): places, files, archives, empty projects, terminals and their socket, run configurations, debug sessions |
| `/diagnostics`, `/lsp` | 11 | the checkers a folder declares, checks and their problems · hover, definition and symbols from a language server |
| `/notebooks`, `/data` | 13 | notebooks: open, save, kernels, run over a socket · data files: open, rows, statistics, a chart, one read-only query |
| `/ai`, `/web`, `/mcp` | 11 | ask memory, brainstorm, extract facts · web search and fetch through the address guard · MCP servers, trust, check, tool calls |
| `/workflows`, `/evals`, `/research` | 19 | reusable step lists that become plans · eval suites scored against the real features · research answered from retrieval, cited |
| `/testing`, `/extensions`, `/ops` | 14 | the test runs · skills, commands, hooks and plugins read from disk (hooks are shown, never run) · this machine's services, logs and keys |
| `/prefs`, `/decisions` | 4 | screen settings, and decisions that are made once |
| `/models`, `/usage` | 2 | the lanes and routes, the ledger priced by day, agent, project and call |
| `/activity`, `/health` | 4 | the feed and its live stream (`activity`, `change`, `run`, `chat`, `routine`, `review`, `reset`) · liveness |

## How the web app uses it

Screens read the workspace through `useData()` in `src/lib/data.tsx`, and screens with data of their own
through `useRemote()`. The web app holds no data of its own: when no API answers, it says it is not
connected and how to start one.
