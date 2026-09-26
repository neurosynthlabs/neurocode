# NeuroCode — working here

An AI engineering OS: React 19 + Vite web app (`src/`), FastAPI + Postgres API (`server/`), `nc` CLI (`cli/`),
Electron shell (`desktop/`), VS Code extension (`vscode/`). Architecture: `docs/ARCHITECTURE.md`. Screen rules:
`docs/DESIGN_SYSTEM.md` (read it before touching a page). Plans and reports live in the ignored `.plan/`.

## Run

- Postgres 16 with pgvector: `brew services start postgresql@16` (keg-only; `psql` is in `/opt/homebrew/opt/postgresql@16/bin`).
- `./scripts/dev.sh start|stop|restart|status|logs [web|api]` — web **5180**, API **8787** (5173 belongs to another project).
- No model lane is needed to run; every AI path says plainly when none can answer. Keys go in Admin → AI providers.

## Verify (all must pass before a commit)

```
npm run build                 # tsc -b + vite build (root `npx tsc --noEmit` checks nothing: use -p tsconfig.app.json)
npm run lint                  # oxlint
npm run test:unit
cd server && uv run pytest -q # the API; never two pytest processes on one test DB (use neurocode_test_1..N)
npm run audit:themes
node scripts/smoke.mjs        # every route, two themes, key interactions — needs a fresh dist/
node scripts/layout-lint.mjs  # nothing spills at 390 / 820
node scripts/e2e.mjs          # API + web + stub model + browser, from an empty workspace
node scripts/copy-measure.mjs # UI copy: words on the page, sentences over 20 words
shellcheck -x deploy/*.sh scripts/*.sh
```

Browser checks start their own throwaway stack (`scripts/stack.mjs`: emptied DB, fixture, stub model on the Groq lane).
CI (`.github/workflows/verify.yml`) runs the same list on every push; `deploy.yml` releases only after it passes.

## Deploy

- Live: https://141.148.215.167.sslip.io (Oracle A1, 4 OCPU / 24 GB, Mumbai; Caddy + API + Postgres in Docker).
- `npm run deploy` (= `deploy/push.sh`, host in `deploy/.host`): builds, ships, health-gates, rolls back on failure.
  `npm run deploy:status | deploy:logs | deploy:backup`. Domain move: `deploy/domain.sh <domain>` once DNS points here.
- GitHub: push only as **neurosynthlabs** (`gh auth switch --user neurosynthlabs`, push, switch back). Commit author
  must be `neurosynthlabs <neurosynthlabs@users.noreply.github.com>`.

## Traps

- UI text is also a test selector: e2e and smoke click buttons by name — grep `scripts/` before renaming a label.
- Every route needs an HTTP-level test; a service-level test hid a 500 (missing import) once.
- `create_api().openapi()` is the real import check: `from __future__ import annotations` hides a missing Pydantic model.
- FastAPI runs BackgroundTasks before the request's transaction commits: hand work off through `deps.hand_off`.
- Async SQLAlchemy: a relationship a serialiser touches must be `lazy="selectin"`; flush a parent before its child
  when models carry plain FK columns; Alembic never diffs enum values — `ALTER TYPE … ADD VALUE` by hand.
- `useRemote`'s `reload` must stay stable, or an effect depending on it loops forever.
- Anything that runs commands, kernels, queries or touches files gets an adversarial review before it ships.
- The repo is not prettier-formatted (`.prettierignore`): single quotes, ~120 columns, hand-packed lines.
