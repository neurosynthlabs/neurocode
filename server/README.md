# NeuroCode local API

FastAPI over one SQLite file. It stores the operator's side of the product: approvals, task status and
checklists, and memory pins and archives. Every change goes into the activity log and is pushed to open
tabs over Server-Sent Events. Memory search uses SQLite FTS5.

It binds to `127.0.0.1` and has no authentication. It is a single-operator, local-first service; do not
expose it on a network.

## Run

```bash
npm run dev:start    # web on :5180 and API on :8787 — Vite proxies /api to the API
npm run api          # the API alone, in the foreground
npm run api:test     # pytest
npm run e2e          # API + web app + headless browser, on a throwaway database
```

The first start creates `server/.venv` with uv and seeds `server/neurocode.db` (git-ignored).
**Settings → Reset database** puts the seed back, and so does
`curl -X POST -H 'X-Confirm: reset' 127.0.0.1:8787/admin/reset`.

## Seed

`server/seed/seed.json` is generated from the frontend mocks in `src/mock/`. Run `npm run seed` after you
change them. The API and the static demo share that one source, so they cannot drift apart.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | row count per table |
| GET | `/projects`, `/agents` | reference data |
| GET | `/tasks?project=`, `/tasks/{ref}` | |
| PATCH | `/tasks/{ref}` | `{status}`, one of the six board columns |
| POST | `/tasks/{ref}/checklist/{id}` | `{done}` |
| GET | `/approvals?status=` | |
| POST | `/approvals/{ref}/approve`, `/approvals/{ref}/deny` | final: a second decision returns 409 |
| GET | `/memory?q=&category=&project=&include_archived=` | `q` is FTS5, prefix-matched word by word, ranked |
| POST | `/memory/{ref}/pin` | `{pinned}` |
| POST | `/memory/{ref}/archive` | hidden from recall, never deleted |
| GET | `/activity?limit=` | newest first |
| GET | `/activity/stream` | `text/event-stream`, event name `activity` |
| POST | `/admin/reset` | requires the header `X-Confirm: reset` |

## How the web app uses it

Screens read this data through `useData()` in `src/lib/data.tsx`. When no API answers, the app paints
from the same seed and keeps changes in the tab. The public Vercel demo works that way, and it never
sends a request.
