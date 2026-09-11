# NeuroCode local API

FastAPI over one SQLite file. It stores the operator's side of the product and does the parts of
the engine that are real today:

- **Approvals, tasks, memory**: decisions, status, checklists, pins and archives persist.
- **Requirement compiler**: a requirement in English or Hinglish becomes a stored plan and a task.
- **Onboarding**: a git remote is shallow-cloned, or a local folder is read, and then measured
  (files, lines, languages, SQL tables and procedures, modules). The semantic passes (syntax trees,
  call graph, business rules) are not connected yet, and the project record says so.
- **Memory**: FTS5 search, conflict resolution, and answers to a plan's questions saved as business rules.
- **MCP registry**: servers added in the wizard are stored untrusted and disconnected.

Every change goes into the activity log and is pushed to open tabs over Server-Sent Events.

It binds to `127.0.0.1` and has no authentication. It is a single-operator, local-first service; do not
expose it on a network.

## Run

```bash
npm run dev:start    # web on :5180 and API on :8787; Vite proxies /api to the API
npm run api          # the API alone, in the foreground
npm run api:test     # pytest
npm run e2e          # API + web app + headless browser, on a throwaway database
```

The first start creates `server/.venv` with uv and seeds `server/neurocode.db` (git-ignored). A database
made by an older version gains any new tables, seeded, and keeps its data.
**Settings → Reset database** puts the seed back, and so does
`curl -X POST -H 'X-Confirm: reset' 127.0.0.1:8787/admin/reset`.

## The compiler

`NEUROCODE_COMPILER` picks the planner. `auto` (the default) tries them in this order:

| Provider | When | Notes |
|---|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` is set | The requirement and the matching memory facts are sent to DeepSeek. |
| Ollama | a local Ollama has `NEUROCODE_OLLAMA_MODEL` pulled | Nothing leaves the machine; a 7B model needs about 5 GB of free RAM. |
| offline planner | always | Keyword rules, not a model. The UI labels its plans that way. |

Put these in `server/.env`, which is git-ignored and read at startup; `server/.env.example` lists them.
If a model's answer fails to parse or validate, it is not stored: the offline planner stands in, and
the activity log says so. Tests and `npm run e2e` always use the offline planner.

## Seed

`server/seed/seed.json` is generated from the frontend mocks in `src/mock/`. Run `npm run seed` after you
change them; CI fails when the two disagree.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | row counts, and which compiler is active |
| GET | `/projects` | |
| POST | `/projects` | onboard a repository: `{source: git \| local, repo, branch, excluded, rules, …}`; the clone and scan run after the response |
| GET | `/tasks?project=`, `/tasks/{ref}` | |
| PATCH | `/tasks/{ref}` | `{status}`, one of the six board columns |
| POST | `/tasks/{ref}/checklist/{id}` | `{done}` |
| GET | `/approvals?status=` | |
| POST | `/approvals/{ref}/approve`, `/approvals/{ref}/deny` | final: a second decision returns 409 |
| GET | `/plans` | newest compiled first |
| POST | `/plans/compile` | `{requirement, projectId}`, creates `PLAN-n` and `TASK-n` |
| POST | `/plans/{ref}/questions/{i}` | `{answer}` saves a business rule to memory; `{defer: true}` records the assumption |
| POST | `/plans/{ref}/dispatch` | refused while any question is open |
| POST | `/plans/{ref}/recompile` | keeps answered and deferred questions settled |
| GET | `/memory?q=&category=&project=&include_archived=` | `q` is FTS5, prefix-matched word by word, ranked |
| POST | `/memory/{ref}/pin` | `{pinned}` |
| POST | `/memory/{ref}/archive` | hidden from recall, never deleted |
| GET | `/memory/conflicts` | open contradictions |
| POST | `/memory/conflicts/{id}/resolve` | `{keep: a \| b \| adr}`; the losing fact is archived as superseded |
| GET | `/mcp/servers` | |
| POST | `/mcp/servers` | `{name, transport, command, scope, defaultEffect, config}` |
| GET | `/agents` | |
| GET | `/activity?limit=` | newest first |
| GET | `/activity/stream` | `text/event-stream`: `activity` (a log line) and `change` (a document put or dropped) |
| POST | `/admin/reset` | requires the header `X-Confirm: reset` |

## How the web app uses it

Screens read this data through `useData()` in `src/lib/data.tsx`. When no API answers, the app paints
from the same seed and keeps changes in the tab. The public Vercel demo works that way, and it never
sends a request.
