# NeuroCode — build contract

Read this before writing any screen. Every page in this app must look like it was
drawn by one person on one afternoon.

## What the product is

A single-operator **NeuroCode**. The human is the *AI Project Manager and final
approver*; agents do planning, research, coding, testing, review, DevOps, legacy analysis,
memory and knowledge management. Requirements arrive in broken Hindi/Hinglish and get
compiled into technical work. The moat is memory + legacy knowledge + orchestration,
not "another coding chat".

**There is no mock data.** Every screen reads the real workspace through the local API, and a new
workspace is empty. So the rules are about honesty rather than realism:

- A number, chart, badge, status or sentence appears only when something measured or stored it. No
  invented defaults, no constant-step formulas, no `Math.random`, no hard-coded names, dates, counts or prices.
- A list that is empty shows `<Empty>` saying what fills it, with a button to the action that does.
- A control does something real, or it is not there. No disabled "coming soon", no toast that pretends.
- Copy never says demo, sample or seed. Placeholders may show an example of what to type.

## Non-negotiable visual rules

The product should feel like a native macOS app: calm, roomy and legible. Nothing crammed.

- **Calm, not dense.** Base font is 14px in the system font (SF on a Mac). Body text in cards is
  13.5–14px, secondary text 12.5–13px, and nothing goes below 11px. Give cards room (`px-5 py-4`)
  and leave `gap-4`/`gap-5` between them.
- **Cards are grouped lists, not boxes in boxes.** Use `<Panel>`: its header is text on the card,
  not a bordered strip. Separate rows inside with hairlines (`divide-y divide-line/60`). Never put a
  bordered box inside a bordered box.
- **Capsules, not shouting.** `<Tag>` and `<RiskPill>` are soft, sentence-case capsules. Do not write
  UPPERCASE labels; the uppercase `eyebrow` class is only for column headers on boards and tables.
- **Lead with the one thing a screen is for.** Reference material (ASCII trees, raw configs, long
  logs) goes behind a tab or below the fold, never above the work.
- **One accent per theme.** `text-brand` / `bg-brand` is the single accent. Status colours
  (`ok/warn/danger/info/violet`) are for status, and for the sidebar's section tints.
- **Never hardcode a hex value or a Tailwind palette class** (`bg-zinc-900`, `text-blue-500`).
  Only theme tokens: `bg-bg bg-base bg-surface bg-surface-2 bg-surface-3 border-line
  border-line-strong text-ink text-ink-2 text-soft text-dim text-brand text-ok text-warn
  text-danger text-info text-violet`. In inline styles use the `--os-*` and `--rail-*` variables
  (`--os-ok`, `--os-warn`, `--os-danger`, `--os-info`, `--os-violet`, `--os-brand`). The app has
  248 switchable palettes in light and dark modes, and a hardcoded colour breaks every one of them.
- Radii come from `--radius` (`rounded-lg` for controls, `rounded-xl` for cards). Numbers use the
  `tnum` class. IDs, paths, refs, SHAs, timestamps and commands are `font-mono`.
- Desktop-first at 1440px, and every screen must also work at 820px (tablet) and 390px (phone):
  `npm run lint:layout` checks 390 and 820 and fails on anything that spills, and on a screen that
  never rendered (offline, crashed, signed out or empty), since that one was not checked at all.

## File conventions

- Pages live at `src/pages/<Name>.tsx` and `export default function <Name>()`.
- A screen's own API calls and response types live at `src/lib/live/<area>.ts`; shared document types
  live in `src/lib/api.ts` and `src/types/index.ts`.
- Page-specific sub-components live at `src/components/<area>/<Component>.tsx`.
- Every screen is listed once in `src/lib/nav.ts`, with its `section` and, when it belongs with
  others, a `sub`. The sidebar, the smoke test and ⌘K all read that list.
- Change `src/App.tsx`, `src/index.css`, `src/components/os/index.tsx`, `src/components/ui/*` and
  `src/types/index.ts` only on purpose, in small edits: every screen depends on them.
- No `any`. No unused variables or imports — `tsc` runs with `strict`, `noUnusedLocals`
  and `noUnusedParameters`, and a single violation fails the whole build.
- `verbatimModuleSyntax` is on: import types with `import type { X } from '...'`.

## Component sources

**`@/components/os`** — the dense OS kit (read the file, it is short):
`Page PageHeader PageBody Panel SectionTitle Divider Tag RiskPill Dot StatusText Bar
MeterRow BlockBar Stat StatGrid KV DataTable Row Cell Segmented ListRow Split Empty
Ascii Mono Kbd Avatar2 cx TEXT_TONE Field SelectField Toolbar Trend Ring Sparkline MiniBars
LogoMark Wordmark Wizard`

**`@/components/ui/*`** — shadcn (base-nova style, Base UI primitives), already installed:
`button card badge tabs dialog command dropdown-menu tooltip scroll-area separator input
select switch progress table sheet popover avatar accordion alert skeleton label textarea
toggle-group breadcrumb sonner`.
Use shadcn for interactive primitives (Button, Dialog, Tabs, Popover, DropdownMenu, Switch,
Sheet, Tooltip, Input, Textarea, Select). Use the OS kit for layout, density and status.
Check the actual export names in the file before importing — this is shadcn v4 on Base UI,
so `TabsList/TabsTrigger/TabsContent`, `PopoverTrigger` renders a real button, etc.

**Icons**: `lucide-react`, sized `size-3.5` or `size-4`.

## Page anatomy

```tsx
export default function Foo() {
  return (
    <Page>
      <PageHeader title="…" subtitle="…" actions={…}>
        {/* optional Tabs / filter bar */}
      </PageHeader>
      <PageBody>…</PageBody>
    </Page>
  );
}
```

`Page` fills the viewport; `PageBody` is the only scroll container. For master/detail
screens use `<Split left={…} right={…} />` inside a `PageBody` with `className="p-0"`
plus your own padding, or give `PageBody` `className="h-full"` and lay out with flex.

## Interactivity expectations

Nothing may feel dead. Every screen needs **real local state**: selection, tab switching,
filtering, search, expand/collapse, kanban filtering, log auto-scroll. Use `useState`/`useMemo`.

The operator's own actions go through the data store, never through local state:

- `useData()` from `@/lib/data` owns **projects, approvals, tasks, plans, memory facts and their
  conflicts, MCP servers and the activity log**. Read them from there (projects through
  `useProject().all`), so a decision made on one screen shows on every other one. Toggles (`decide`, `moveTask`, `toggleCheck`, `setPinned`, `archive`) are optimistic. Actions
  that create or reshape documents (`createProject`, `registerMcp`, `resolveConflict`,
  `settleQuestion`, `dispatchPlan`, `compile`, `recompile`) wait for the server. Every action writes
  its own activity event and resolves to `false` or `null` when it was not saved.
- Those actions persist to Postgres through the local API, and what anyone changes streams back over
  SSE. `mode` is `connecting`, `live` or `offline`; the shell shows one "Not connected" panel when it is
  offline, so a screen never branches on it for content.
- Screen data outside that store goes through `useRemote(key, load)`.
- Screen state that must survive a reload (a switch, a mode, a setting) uses `usePref(key, DEFAULTS)`
  with a module-level `DEFAULTS`. A verdict that is final once given uses `useDecision(key)` or
  `recordDecision`. Neither belongs in `useState`.
- Something that needs a backend that does not exist yet is built on the server first, or left out.

## Shared sources you must reuse (do not duplicate or redefine)

- `@/lib/data` — `useData()`: the operator's state and every action on it; `inFlight(plan)`
- `@/lib/project-context` — `useProject()` returns the active project and `all` of them
- `@/lib/access` — `useAccess()`: permission labels, role names and the agent roster from `GET /auth/catalogue`;
  `permissionLabel`, `roleName`, `agentName`
- `@/lib/auth` — `useAuth()`: who is signed in, `can(perm)`
- `@/lib/remote` — `useRemote(key, load)` for a screen's own reads
- `@/lib/theme` — `useTheme()`

The browser checks build their own data. `npm run e2e` starts from an empty workspace and makes
everything it looks at; `npm run smoke` and `npm run lint:layout` load the API tests' fixture
(`server/tests/fixtures/workspace.json`) into a throwaway database. Nothing reads the fixture in the app.

## Verify before you finish

```
npm run build          # tsc -b + vite build; no chunk may pass 500 KB
npm run smoke          # every route in two themes, plus the key interactions, headless
npm run lint:layout    # nothing spills at 390 / 820 (LINT_WIDTHS=390,820,1440 adds desktop)
npm run audit:themes   # text contrast across every palette × mode × ground
npm run api:test       # the local API
npm run e2e            # API + web app + stub model + browser, from an empty workspace
```
All of them must pass. Fix anything you broke.

---

# shadcn cheatsheet — AUTHORITATIVE

Do **not** read `node_modules` or the shadcn component sources. Everything you need is here.
This project is shadcn v4 (`base-nova` style) on **Base UI** primitives. All imports are from
`@/components/ui/<file>`.

```tsx
import { Button } from '@/components/ui/button';
// variant: default | outline | secondary | ghost | destructive | link
// size:    default | xs | sm | lg | icon | icon-xs | icon-sm
<Button size="xs" variant="outline" onClick={fn}><Play className="size-3" />Run</Button>

import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
// Base UI API: value/onValueChange on <Tabs>, `value` on trigger and content
<Tabs value={tab} onValueChange={(v) => setTab(v as string)}>
  <TabsList><TabsTrigger value="a">A</TabsTrigger></TabsList>
  <TabsContent value="a">…</TabsContent>
</Tabs>
// NOTE: prefer the OS kit's <Segmented> or plain buttons for dense in-page switching.
// Tabs are heavier; use them only for real page-level sections.

import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
         DialogFooter, DialogTrigger, DialogClose } from '@/components/ui/dialog';
<Dialog open={open} onOpenChange={setOpen}>
  <DialogContent className="max-w-2xl">
    <DialogHeader><DialogTitle>…</DialogTitle><DialogDescription>…</DialogDescription></DialogHeader>
    …
    <DialogFooter><Button onClick={() => setOpen(false)}>Close</Button></DialogFooter>
  </DialogContent>
</Dialog>

import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
<Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
  <SheetContent side="right" className="w-[560px] sm:max-w-none">…</SheetContent>
</Sheet>
// `side` is one of: right | left | top | bottom

import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
// PopoverTrigger renders a real <button> — pass className directly, no asChild needed
<Popover open={o} onOpenChange={setO}>
  <PopoverTrigger className="…">Open</PopoverTrigger>
  <PopoverContent align="start" className="w-72 p-1">…</PopoverContent>
</Popover>

import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
// TooltipProvider is already mounted in App.tsx — do not add another
<Tooltip><TooltipTrigger render={<div />}>{child}</TooltipTrigger>
  <TooltipContent side="right">text</TooltipContent></Tooltip>

import { Switch } from '@/components/ui/switch';
<Switch checked={on} onCheckedChange={setOn} />

import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
<Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="…" className="h-7 text-[12px]" />

import { Progress } from '@/components/ui/progress';   // <Progress value={62} />
import { Separator } from '@/components/ui/separator';  // <Separator /> — or the OS kit's <Divider />
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert';
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from '@/components/ui/accordion';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';   // toast.success('Merged'), toast('Queued'), toast.error('Blocked')
```

**`CommandDialog` does not create the cmdk root.** Always nest a `<Command>` inside it —
`<CommandDialog><Command><CommandInput/><CommandList>…</CommandList></Command></CommandDialog>` —
or every `CommandInput`/`CommandItem` throws `reading 'subscribe'` the moment it opens.

**`DialogContent` ships `sm:max-w-sm`.** Widen it with `sm:max-w-2xl`, never plain `max-w-2xl`.

**Select is fiddly on Base UI — do not use `@/components/ui/select`.** For dropdown filters use
a native element styled with tokens:

```tsx
<select value={v} onChange={(e) => setV(e.target.value)}
  className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
  {opts.map((o) => <option key={o} value={o} className="bg-surface">{o}</option>)}
</select>
```

**Prefer the OS kit over shadcn** for: layout (`Page/PageHeader/PageBody/Panel/Split`),
density (`DataTable/Row/Cell/KV/Stat`), status (`Tag/RiskPill/Dot/StatusText`) and
meters (`Bar/MeterRow/BlockBar`). Use shadcn only for real interactive primitives
(Button, Dialog, Sheet, Popover, Switch, Input, Textarea, Tooltip, toast).

**Icons**: `import { Play, Pause, Search, ChevronRight } from 'lucide-react'` — `size-3.5`.
If unsure an icon name exists, use a common one (Circle, Dot, FileText, Folder, Play,
Check, X, AlertTriangle, ChevronRight, Search, Filter, Plus, RefreshCw, Terminal, Database,
GitBranch, Bot, Brain, Clock, Zap, Shield, Settings). Do not grep lucide's dist.

---

# Multi-step flows

Anything that is really several decisions — onboarding a repository, registering an MCP server — is a
`Wizard` from the OS kit, not a long single form. Steps are data:

```tsx
<Wizard open={open} onOpenChange={setOpen} title="…" finishLabel="Start" onFinish={…}
  steps={[
    { id: 'repo', title: 'Repository', hint: 'where the code lives', valid: repo.length > 3,
      blocker: 'Enter a repository', content: <…/> },
    { id: 'review', title: 'Review', content: <…/> },
  ]} />
```

`valid: false` disables Next until the step is complete; completed steps stay clickable in the stepper.

# Verify before you ship

- `npx tsc --noEmit -p tsconfig.app.json` — types
- `npm run smoke` — renders every route in a dark and a light theme and drives the palette, the
  Appearance sheet, both wizards and a task sheet. Catches render-time throws tsc cannot see.
- `./scripts/deploy.sh "message"` — builds, commits as `neurosynthlabs` (Vercel blocks any other
  author on this team), pushes, deploys, and polls until the deployment is READY or fails loudly.
