# NeuroCode — build contract

Read this before writing any screen. Every page in this app must look like it was
drawn by one person on one afternoon.

## What the product is

A single-operator **NeuroCode**. The human is the *AI Project Manager and final
approver*; agents do planning, research, coding, testing, review, DevOps, legacy analysis,
memory and knowledge management. Requirements arrive in broken Hindi/Hinglish and get
compiled into technical work. The moat is memory + legacy knowledge + orchestration,
not "another coding chat".

Everything is **static mock data**. There is no backend and there will not be one.
Every number, log line and name must look like it came from a real day of work on a
14-year-old ERP — never `Lorem ipsum`, never `Item 1 / Item 2`, never round marketing numbers.

## Non-negotiable visual rules

- **Solid colour only.** No gradients, no glassmorphism, no blur, no shadow-heavy cards.
- **Dense, information-first.** Base font is 13px; most UI text is 11–12.5px. Tight rows.
  A screen should feel like a terminal-grade console, not a marketing dashboard.
- **One accent per theme.** Use `text-brand` / `bg-brand` for the single accent. Never
  introduce a second brand colour. Status colours (`ok/warn/danger/info/violet`) are for
  status only.
- **Never hardcode a hex value or a Tailwind palette class** (`bg-zinc-900`, `text-blue-500`).
  Only theme tokens: `bg-bg bg-base bg-surface bg-surface-2 bg-surface-3 border-line
  border-line-strong text-ink text-ink-2 text-soft text-dim text-brand text-ok text-warn
  text-danger text-info text-violet`. The app has 8 switchable themes (6 dark, 2 light) and
  a hardcoded colour breaks every one of them.
- Small radii (`rounded-xs/sm/md`). Numbers use the `tnum` class. IDs, paths, refs, SHAs,
  timestamps and commands are `font-mono`.
- Section labels use the `eyebrow` class (10px uppercase).
- Desktop-first, 1440px. Must not break below ~1100px, but no mobile design work needed.

## File conventions

- Pages live at `src/pages/<Name>.tsx` and `export default function <Name>()`.
- Page-specific mock data lives at `src/mock/<name>.ts`, typed against `src/types/index.ts`.
- Page-specific sub-components live at `src/components/<area>/<Component>.tsx`.
- **Never edit** `src/App.tsx`, `src/lib/nav.ts`, `src/index.css`, `src/components/os/index.tsx`,
  `src/components/ui/*`, `src/types/index.ts`, or another agent's page/mock. Routes already exist.
- If you need a type that is missing, define it locally in your own mock file. Do not touch `src/types`.
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

This is a static prototype, but it must not feel dead. Every screen needs **real local
state**: selection, tab switching, filtering, search, expand/collapse, kanban column
filtering, log auto-scroll, etc. Use `useState`/`useMemo`. Buttons that would mutate a
backend can be no-ops or show a `sonner` toast — but selection and filtering must work.

## Shared mock data you must reuse (do not duplicate or redefine)

- `@/mock/projects` — `projects`, `getProject`, `projectName`, `activeProjectId`
- `@/mock/agents` — `agents`, `getAgent`, `agentName`
- `@/mock/tasks` — `tasks`, `byStatus`, `getTask`
- `@/mock/activity` — `activity`
- `@/mock/permissions` — `permissionRules`, `approvals`
- `@/lib/project-context` — `useProject()` returns the active project
- `@/lib/theme` — `useTheme()`

Names must stay consistent across screens: the ERP tax bug is always **TASK-492 /
BUG-883 / InvoiceService.cs / TaxService.cs / SP_CalculateTax / MST_TAX / TRANS_INVOICE /
MEM-142 / ADR-52**. Agents are always the twelve in `@/mock/agents`. Models are always
Qwen3-Coder-Next, Kimi-K2.5, DeepSeek-V3.2, GLM-4.7, Qwen3-Next-80B, BGE-M3, BGE-Reranker-v2.

## Verify before you finish

```
npx tsc --noEmit -p tsconfig.app.json
```
must be clean for the files you touched. Fix anything you broke.

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
