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
uv run pytest -q                                # 220 tests, against neurocode_test
```

From the repository root, `npm run dev:start` runs this and the web app together, and checks the database
first — no server, no database and no migrations each print the one command that fixes them
(`uv run python -m app.data.check` asks the same question on its own).

A brand-new workspace opens on the sample work, once. A workspace that already holds work is never
reseeded, and one where someone cleared the sample away does not get it back on a restart.

Coming from the SQLite version: `uv run python ../scripts/import-sqlite.py --dry-run`, then without
`--dry-run`. Accounts come across with their passwords, and signed-in browsers stay signed in.

## Settings

Everything is `NEUROCODE_*`, read from the environment or `server/.env` (`app/settings.py` lists them all):

| Variable                      | Default                                                             |                                                                                |
| ----------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `NEUROCODE_DATABASE_URL`      | `postgresql+asyncpg://neurocode:neurocode@127.0.0.1:5432/neurocode` | a plain `postgres://` URL is upgraded                                          |
| `NEUROCODE_TEST_DATABASE_URL` | `…/neurocode_test`                                                  | what the tests migrate and roll back inside                                    |
| `NEUROCODE_COMPILER`          | `auto`                                                              | `auto` · `free` · `local` · `rules` · a lane id; the tests and e2e pin `rules` |
| `NEUROCODE_PG_BIN_DIR`        | —                                                                   | where `pg_dump` is, if it is not on PATH or in a usual install location        |
| `NEUROCODE_CORS_ORIGIN_REGEX` | localhost only                                                      | origins allowed to carry the session cookie                                    |

Model keys are not settings: they live in `server/secrets.json` (mode 0600), set from Admin → AI
providers, and are only ever reported masked.

## Endpoints

86 of them. The running API describes every one at `http://127.0.0.1:8787/docs`; this is the map.

| Family                           |     | What it holds                                                                                                                       |
| -------------------------------- | --- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `/auth`                          | 6   | setup, sign-in and out, who is asking, password                                                                                     |
| `/admin`                         | 24  | people, roles, teams, permissions, workspace, audit log · database health, backup, check, optimize, reset · AI lanes and their test |
| `/projects`                      | 3   | the projects, and onboarding one — the clone, scan and index run after the response                                                 |
| `/projects/{pid}/code`           | 9   | the index summary, file tree, one file, search, impact, module graph, re-index · retrieval and its rebuild                          |
| `/tasks`                         | 4   | the board, a move, a checklist tick                                                                                                 |
| `/plans`                         | 6   | compile, read, recompile, answer or defer a question, dispatch                                                                      |
| `/approvals`                     | 2   | the inbox, and a final decision — which resumes the run waiting on it                                                               |
| `/runs`                          | 6   | agent runs, their logs from a line you already hold, the diff, cancel, discard, merge                                               |
| `/sessions`                      | 5   | conversations that can read the code, turn by turn                                                                                  |
| `/memory`                        | 6   | search, add, pin, archive, conflicts and their resolution                                                                           |
| `/ai`                            | 4   | ask memory, brainstorm, extract facts from text                                                                                     |
| `/mcp`                           | 2   | registered servers, untrusted until promoted                                                                                        |
| `/prefs`, `/decisions`           | 4   | screen settings, and decisions that are made once                                                                                   |
| `/agents`, `/activity`, `/usage` | 4   | the roster with its real throughput and spend, the feed, the usage ledger                                                           |
| `/activity/stream`               |     | Server-Sent Events: `activity`, `change`, `run` and `chat`                                                                          |
| `/health`                        | 1   | public: the database, its row counts, and which model would answer                                                                  |

## How the web app uses it

Screens read the workspace through `useData()` in `src/lib/data.tsx`, and screens with data of their own
through `useRemote()`. When no API answers, the app paints the same sample work and keeps changes in the
tab — which is how the public demo runs, without ever sending a request.
