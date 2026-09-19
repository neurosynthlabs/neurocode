# NeuroCode local API

FastAPI over Postgres 16. It holds the whole workspace — accounts, the work, memory, the code index, agent
runs and sessions — and it does the parts of the product that act: compiling requirements into plans,
onboarding and indexing repositories, running agents in git worktrees, answering from retrieval, and
routing every model call through one gateway.

It is a single-workspace service for one machine. It binds to `127.0.0.1`, every route except
`/health` and the sign-in routes needs a session, and a change riding on the session cookie must also carry
the `X-NC-Client` header. Do not expose it on a network as it stands.

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

Model keys are not settings: they live in `server/secrets.json` (mode 0600), set from Admin → AI
providers, and are only ever reported masked.

## Endpoints

123 paths. The running API describes every one at `http://127.0.0.1:8787/docs`; this is the map.

| Family                              | Paths | What it holds                                                                                                                                                                 |
| ----------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/auth`                             |     7 | setup, sign-in and out, who is asking, password · the catalogue: permission labels, role names, the roster                                                                    |
| `/admin`                            |    17 | people, roles, teams, permissions, workspace and its security rules, audit log · database health, backup, check, optimize, reset (empties the work) · AI lanes and their test |
| `/projects`                         |     2 | the projects, and onboarding one — the clone, scan and index run after the response                                                                                           |
| `/projects/{pid}/code`              |    11 | the index summary, file tree, one file, search, impact, module graph, re-index · retrieval, its documents and its rebuild                                                     |
| `/projects/{pid}/git`, `/tests`     |     6 | branches, commits, worktrees, conflicts and diffs read from git · the test command, its failures, and the expectations people set                                             |
| `/tasks`                            |     3 | the board, a move, a checklist tick                                                                                                                                           |
| `/plans`                            |     6 | compile, read, recompile (a model is needed: 409 with none, 502 when the provider fails), answer or defer, dispatch                                                           |
| `/approvals`                        |     2 | the inbox, and a final decision — which resumes the run waiting on it                                                                                                         |
| `/permissions/rules`                |     1 | the standing rules the runtime applies: each project's answer to its first test run, and who gave it                                                                          |
| `/runs`                             |     7 | agent runs, their logs from a line you already hold, the diff, cancel, discard, merge, and rework: a new run told what to change                                              |
| `/sessions`                         |     4 | conversations that can read the code, turn by turn                                                                                                                            |
| `/memory`                           |     7 | search, add, pin, archive · recent recalls · conflicts: filed by a person, and settled by keeping one side                                                                    |
| `/ai`                               |     4 | ask memory, brainstorm (needs a model), extract facts from text, the briefs                                                                                                   |
| `/mcp`                              |     3 | registered servers · trust (a stdio command is never launched untrusted) · check: connect once, list tools, record what happened                                              |
| `/workflows`, `/evals`, `/research` |    19 | reusable step lists that become plans · eval suites scored against the real features · research answered from retrieval, cited                                                |
| `/testing`, `/extensions`, `/ops`   |    14 | the test runs · skills, commands, hooks and plugins read from disk (hooks are shown, never run) · this machine: services, checks, logs, keys                                  |
| `/prefs`, `/decisions`              |     4 | screen settings, and decisions that are made once                                                                                                                             |
| `/agents`, `/models`, `/usage`      |     3 | the roster with its real throughput and spend, the lanes and routes, the ledger priced by day, agent, project and call                                                        |
| `/activity`                         |     2 | the feed, and `/activity/stream`: Server-Sent Events for `activity`, `change`, `run` and `chat`                                                                               |
| `/health`                           |     1 | public: the database, its row counts, and which model would answer                                                                                                            |

## How the web app uses it

Screens read the workspace through `useData()` in `src/lib/data.tsx`, and screens with data of their own
through `useRemote()`. The web app holds no data of its own: when no API answers, it says it is not
connected and how to start one.
