# NeuroCode

**An AI engineering OS for a single operator.**

You are the AI Project Manager and the final approver. Everything else — planning, research,
coding, testing, review, DevOps, legacy analysis, memory and knowledge management — is done by
twelve specialist agents working in isolated git worktrees.

This repository is the **static frontend prototype**: 32 screens, real-shaped mock data, no backend.
It exists so the UX can be settled before a single service is written.

## What is in here

| Area | Screens |
|---|---|
| Workspace | Command Center · Projects · Project Overview |
| Intelligence | Memory · Knowledge · Code Intelligence · Architecture |
| Execution | Agents · Tasks · Plans · Live Runs · Workflows · Testing · Review · Git & Worktrees · DevOps |
| Platform | Skills · Commands · Hooks · Plugins · MCP & Tools · ACP Bridge · Models & Router |
| Thinking | Brainstorm · Research |
| Governance | Activity · Sessions · Permissions · Cost & Usage · Evals · Settings |

Plus a login screen and a ⌘K palette that searches code, memory, tasks, decisions, commits and tests.

## The ideas the UI is arguing for

- **Memory is the moat.** Facts carry a reason, a source and evidence. They strengthen when used and
  decay when they are not — except pinned facts and decisions, which never do.
- **Legacy knowledge is parsed, not guessed.** Tree-sitter + LSP + git history produce a call graph,
  and impact analysis reads from it rather than from naming conventions.
- **The requirement compiler.** Broken Hinglish in, an evidenced plan out — including the open
  questions it refuses to guess at.
- **Parallel agents, isolated worktrees.** Collisions surface at merge, never mid-edit.
- **The human is the scarce resource.** LOW risk runs automatically, MEDIUM needs a reviewer,
  HIGH always stops at your signature. Deny rules cannot be talked past.
- **Local first.** 71% of calls are served by local weights — which is the cheap option and the
  private one at the same time.

## Stack

React 19 · Vite 8 · TypeScript · Tailwind v4 · shadcn/ui (Base UI) · React Router 7 · Lucide

## Theming

248 palettes × 7 modes × 64 ground tones, plus live corner radius, six font families, four base
sizes and three sidebar styles — all driven by CSS variables written at runtime, so every one of the
32 screens repaints without a rebuild.

## Run it

```bash
npm install
npm run dev      # http://localhost:5173
npm run build
```

## Conventions

`docs/DESIGN_SYSTEM.md` is the binding contract for this codebase — token names, density rules,
component sources and the shadcn cheatsheet.
