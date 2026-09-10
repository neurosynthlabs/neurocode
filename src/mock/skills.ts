import type { Skill } from '@/types';

/** Extra prototype-only fields layered on the shared `Skill` contract. */
export interface SkillRecord extends Skill {
  /** project id from @/mock/projects, or 'all' for every project */
  project: string;
  /** tokens the instruction body costs once loaded into context */
  tokens: number;
  /** times the trigger matched in the last 24h */
  loads24h: number;
  /** the instruction body an agent actually reads */
  body: string;
  /** deterministic trigger rules — when X matches, load this skill */
  rules: { when: string; then: string }[];
  author: string;
}

/** Sessions opened today — used to price "pin everything" against trigger-gated loading. */
export const sessionsToday = 138;

export const skills: SkillRecord[] = [
  {
    id: 'sk-01', name: 'Requirement Compiler', slug: 'requirement-compiler',
    description: 'Compiles broken Hinglish requirements into atomic, testable work orders with resolved file and table names.',
    scope: 'global', source: '~/.claude/skills/requirement-compiler/SKILL.md', enabled: true,
    triggers: ['prompt matches /(chahiye|karna hai|dikkat|galat|issue aa raha)/i', 'source = WhatsApp intake', 'agent = commander'],
    tools: ['Memory', 'Knowledge', 'SchemaGraph'], usedBy: ['commander', 'architect'],
    invocations: 1_284, lastUsed: '4 min ago', version: '3.2.1',
    project: 'all', tokens: 1_940, loads24h: 61, author: 'rajat',
    rules: [
      { when: 'user prompt contains Devanagari or Hinglish verb stems', then: 'load before the Commander plans' },
      { when: 'requirement arrives from the WhatsApp intake channel', then: 'load unconditionally' },
      { when: 'prompt is already a JIRA-style ticket', then: 'skip — nothing to compile' },
    ],
    body: `# requirement-compiler

Turn a raw Hinglish requirement into a technical work order. Never guess a table name.

## Steps
1. Split the message into atomic asks.
   "invoice me tax galat aa raha hai aur print bhi tut raha hai" is TWO asks.
2. Classify each ask: bug | change | new | question.
3. Resolve every business noun against MEM + the schema graph:
   "tax"           -> MST_TAX, SP_CalculateTax, TaxService.cs
   "invoice print" -> InvoicePrintController.cs, RPT_InvoiceA4.rdlc
   "party"         -> MST_PARTY (NOT MST_CUSTOMER, that is HIMS)
4. If a noun resolves to nothing, STOP and ask the PM exactly one question.
   Do not invent a table. Do not assume the module.
5. Emit: title, Given/When/Then acceptance, impacted files, risk, open questions.

## Output contract
- title      <= 90 chars, imperative, English
- acceptance 2-6 Given/When/Then blocks, each independently verifiable
- risk       LOW | MEDIUM | HIGH | CRITICAL + one line of justification
- files      real repo paths only, verified to exist

## Never
- Never write code in this skill.
- Never collapse two asks into one ticket to look tidy.`,
  },
  {
    id: 'sk-02', name: 'Impact Analysis', slug: 'impact-analysis',
    description: 'Walks the call graph, SP dependency tree and view chain to produce the true blast radius before an edit.',
    scope: 'global', source: '~/.claude/skills/impact-analysis/SKILL.md', enabled: true,
    triggers: ['tool = Edit | Write on *.cs', 'plan step type = code', 'agent = architect'],
    tools: ['Tree-sitter', 'LSP', 'Graph', 'MSSQL'], usedBy: ['architect', 'backend', 'reviewer'],
    invocations: 911, lastUsed: '11 min ago', version: '2.8.0',
    project: 'all', tokens: 2_610, loads24h: 44, author: 'rajat',
    rules: [
      { when: 'a plan touches a file with more than 12 inbound references', then: 'load and block until the report exists' },
      { when: 'the file is under Services/ or Repositories/', then: 'load' },
      { when: 'change is confined to *.test.ts', then: 'skip' },
    ],
    body: `# impact-analysis

Answer one question: if this changes, what breaks?

## Layers to walk (all four, always)
1. C# call graph via LSP references, depth 3.
2. Stored-proc dependency tree via sys.sql_expression_dependencies.
3. View + trigger chain on every table the proc writes.
4. Front-end consumers: grep the API route string, not the method name.

## Worked shape (TASK-492)
  TaxService.CalculateLineTax
    <- InvoiceService.Post                (14 refs)
    <- CreditNoteService.Reverse          (3 refs)
    <- SP_CalculateTax                    (called by 4 procs)
         -> TRANS_INVOICE_TAX  (write)
         -> VW_GSTR1_B2B       (read, breaks silently)

## Output
- table: symbol | kind | refs | risk | evidence path:line
- one CRITICAL line per legacy contract that would break
- an explicit "safe to change" verdict — never leave it implied`,
  },
  {
    id: 'sk-03', name: 'SOLID Enforcer', slug: 'solid-enforcer',
    description: 'Rejects fat services, static helpers and constructor-less classes against the ERP house SOLID rules.',
    scope: 'project', source: '.claude/skills/solid-enforcer/SKILL.md', enabled: true,
    triggers: ['tool = Write on **/*.cs', 'review stage = pre-handoff', 'diff adds a public class'],
    tools: ['Filesystem', 'Tree-sitter'], usedBy: ['backend', 'reviewer'],
    invocations: 764, lastUsed: '26 min ago', version: '1.9.4',
    project: 'erp', tokens: 1_480, loads24h: 38, author: 'rajat',
    rules: [
      { when: 'diff adds or edits a class under src/Services or src/Repositories', then: 'load' },
      { when: 'a method exceeds 60 lines', then: 'load and fail the review' },
      { when: 'file is a DTO or record', then: 'skip' },
    ],
    body: `# solid-enforcer

House rules for CAREWORKS-ERP. These beat any general C# advice.

## Hard fails
- No static business logic. TaxHelper.Calculate() is a fail; ITaxService is the fix.
- Every service takes its dependencies through the constructor. No service locator.
- One reason to change per class. InvoiceService does not know about GSTR1 export.
- No new() on a repository inside a service. Ever.
- Interface lives beside the implementation: ITaxService.cs next to TaxService.cs.

## Naming (legacy contract — do not modernise)
- Class:   PascalCase, suffix Service / Repository / Provider / Validator
- Private: _camelCase with underscore
- Async:   suffix Async, always returns Task<T>, never async void

## Known allowed violations
- LegacyTaxAdapter.cs is exempt (see ADR-52). Do not "fix" it.
- SP_CalculateTax stays a stored proc. Porting it to C# is out of scope.`,
  },
  {
    id: 'sk-04', name: 'Repository Pattern', slug: 'repository-pattern',
    description: 'Generates repository + interface pairs that never let a service touch TRANS_* tables directly.',
    scope: 'project', source: '.claude/skills/repository-pattern/SKILL.md', enabled: true,
    triggers: ['prompt mentions repository | data access | Dapper', 'diff contains SqlConnection', 'agent = backend'],
    tools: ['Filesystem', 'MSSQL', 'Memory'], usedBy: ['backend', 'database'],
    invocations: 522, lastUsed: '1 h ago', version: '2.1.0',
    project: 'erp', tokens: 1_720, loads24h: 21, author: 'rajat',
    rules: [
      { when: 'diff introduces SqlConnection, SqlCommand or a raw query string', then: 'load and rewrite' },
      { when: 'a service file references a TRANS_ table name', then: 'load, block the write' },
      { when: 'the query is a read-only report projection', then: 'load in advisory mode only' },
    ],
    body: `# repository-pattern

Data access shape for the ERP renewal. Copy InvoiceRepository.cs — do not invent.

## Contract
- Interface first:  IInvoiceRepository -> Repositories/Invoice/IInvoiceRepository.cs
- Impl:             Repositories/Invoice/InvoiceRepository.cs
- Dapper only. No EF Core in this codebase (ADR-19 rejected it in 2021).
- Every method takes an explicit IDbTransaction when it writes.

## Absolute rule
Services never name a TRANS_ table. If TaxService.cs contains the string
"TRANS_INVOICE" the PreToolUse hook blocks the write before you see it.

## Template
  public sealed class InvoiceRepository : IInvoiceRepository
  {
      private readonly ISqlConnectionFactory _factory;
      public InvoiceRepository(ISqlConnectionFactory factory) => _factory = factory;

      public async Task<int> InsertTaxLinesAsync(
          IReadOnlyList<InvoiceTaxLine> lines, IDbTransaction tx)  { ... }
  }`,
  },
  {
    id: 'sk-05', name: 'Stored-Proc Analysis', slug: 'sproc-analysis',
    description: 'Reads a 900-line stored procedure and returns branch table, temp-table lifecycle and the actual write set.',
    scope: 'project', source: '.claude/skills/sproc-analysis/SKILL.md', enabled: true,
    triggers: ['prompt mentions SP_ or stored proc', 'tool = MSSQL.describe_stored_procedure', 'agent = database'],
    tools: ['MSSQL', 'Graph', 'Memory'], usedBy: ['database', 'architect', 'backend'],
    invocations: 613, lastUsed: '18 min ago', version: '2.4.3',
    project: 'erp', tokens: 3_040, loads24h: 29, author: 'rajat',
    rules: [
      { when: 'a prompt or plan step names an SP_* identifier', then: 'load before any SQL is written' },
      { when: 'the proc is longer than 400 lines', then: 'load and force the branch table output' },
      { when: 'the proc is a thin CRUD wrapper under 40 lines', then: 'skip' },
    ],
    body: `# sproc-analysis

Legacy procs are the real spec. Read them before believing any C# comment.

## Produce, in this order
1. Signature: params, defaults, which are actually used.
2. Branch table: every IF / CASE that changes the write set, with the column that drives it.
3. Temp tables: #tmp lifecycle, and whether they leak across a TRY/CATCH.
4. Write set: every INSERT/UPDATE/DELETE target + the columns touched.
5. Silent failure modes: RETURN without RAISERROR, swallowed CATCH, @@ROWCOUNT ignored.

## SP_CalculateTax (1,142 lines) — known landmines
- @IsInterState defaults to 0 and is NOT set by the CreditNote path (BUG-883).
- CGST/SGST are written even when IGST applies; the reversal happens 300 lines later.
- Line 812 CATCH swallows a divide-by-zero on a 0% tax master row.
- MST_TAX.EffectiveFrom is compared with > not >=, so same-day rate changes miss.`,
  },
  {
    id: 'sk-06', name: 'Migration Writer', slug: 'migration-writer',
    description: 'Writes reversible, idempotent SQL Server migrations with an explicit rollback and a row-count guard.',
    scope: 'project', source: '.claude/skills/migration-writer/SKILL.md', enabled: true,
    triggers: ['plan step type = migration', 'prompt mentions ALTER TABLE | add column', 'agent = database'],
    tools: ['MSSQL', 'Filesystem', 'Git'], usedBy: ['database', 'devops'],
    invocations: 288, lastUsed: '3 h ago', version: '1.7.2',
    project: 'all', tokens: 1_560, loads24h: 9, author: 'rajat',
    rules: [
      { when: 'a plan step is typed migration', then: 'load' },
      { when: 'DDL targets a table with more than 1M rows', then: 'load and require the batched form' },
      { when: 'change is to a *_STG staging table', then: 'load in relaxed mode' },
    ],
    body: `# migration-writer

Every migration is a pair. Up without Down is not a migration, it is an outage.

## File layout
  db/migrations/2026-09-10_1142__add_isinterstate_to_trans_invoice.sql
  db/migrations/2026-09-10_1142__add_isinterstate_to_trans_invoice.down.sql

## Rules
- Guard every statement: IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE ...)
- TRANS_INVOICE has 41.2M rows. Any UPDATE runs in 50k batches with a WAITFOR DELAY.
- Never ALTER a column that a stored proc reads with SELECT *.
- Record expected row count in a comment; the deploy hook compares it after.

## Template head
  -- up: add IsInterState, backfill from MST_STATE join, 41.2M rows, ~9 min
  -- rollback: drop column, no data loss (column is derived)
  -- verified against CAREWORKS_ERP_UAT on 2026-09-09`,
  },
  {
    id: 'sk-07', name: 'Legacy Risk', slug: 'legacy-risk',
    description: 'Scores a change against 14 years of undocumented behaviour, silent contracts and downstream report consumers.',
    scope: 'project', source: '.claude/skills/legacy-risk/SKILL.md', enabled: true,
    triggers: ['risk gate before approval', 'file age > 8 years', 'file has no test coverage'],
    tools: ['Git', 'Memory', 'Knowledge', 'Graph'], usedBy: ['reviewer', 'architect', 'commander'],
    invocations: 704, lastUsed: '7 min ago', version: '3.0.0',
    project: 'erp', tokens: 2_180, loads24h: 47, author: 'rajat',
    rules: [
      { when: 'git blame shows the file untouched for more than 3 years', then: 'load, minimum risk MEDIUM' },
      { when: 'the file is read by an RDLC report or a GST export', then: 'load, minimum risk HIGH' },
      { when: 'file was created inside this renewal', then: 'skip — no legacy debt' },
    ],
    body: `# legacy-risk

Modern static analysis says this file is fine. It is not. Score it anyway.

## Signals (additive)
+3  file untouched > 3 years (git blame, not mtime)
+3  read by an RDLC report, a GST export or a bank file writer
+2  zero test coverage on the changed method
+2  a stored proc reads the same table in the same transaction
+2  behaviour depends on a MST_ master row an operator can edit at runtime
+1  the only author has left the company
-2  covered by a golden-file test that actually asserts numbers

## Bands
0-3 LOW   4-6 MEDIUM   7-9 HIGH   10+ CRITICAL (human approval mandatory)

## TASK-492 scored 8 -> HIGH
untouched since 2019 (+3), feeds GSTR1 export (+3), no tests on the reversal path (+2).`,
  },
  {
    id: 'sk-08', name: 'ADR Writer', slug: 'adr-writer',
    description: 'Writes and supersedes architecture decision records in the house format, with the rejected options kept.',
    scope: 'global', source: '~/.claude/skills/adr-writer/SKILL.md', enabled: true,
    triggers: ['plan introduces a new pattern or dependency', 'prompt mentions ADR', 'agent = docs'],
    tools: ['Filesystem', 'Git', 'Knowledge'], usedBy: ['docs', 'architect'],
    invocations: 156, lastUsed: 'yesterday 19:40', version: '1.5.1',
    project: 'all', tokens: 1_120, loads24h: 4, author: 'rajat',
    rules: [
      { when: 'a plan adds a dependency or changes a boundary', then: 'load' },
      { when: 'an existing ADR is contradicted by the plan', then: 'load and write a superseding ADR' },
      { when: 'change is a bug fix inside an existing pattern', then: 'skip' },
    ],
    body: `# adr-writer

One decision per file. Never edit an accepted ADR — supersede it.

## Path
  docs/adr/ADR-0052-tax-reversal-moves-into-taxservice.md

## Sections (all required, in order)
  # ADR-52: <decision in one imperative line>
  Status: Accepted | Superseded by ADR-nn | Rejected
  Date: 2026-09-08
  Deciders: Rajat (PM), Architect agent
  ## Context      — the forces, with numbers
  ## Decision     — what we will do, present tense
  ## Options rejected  — each with the one reason it lost
  ## Consequences — including the bad ones

## Rule
"Options rejected" is not optional. An ADR without it is a changelog entry.`,
  },
  {
    id: 'sk-09', name: 'Visual Diff', slug: 'visual-diff',
    description: 'Screenshots before/after at three breakpoints and fails on pixel drift outside the changed component box.',
    scope: 'global', source: '~/.claude/skills/visual-diff/SKILL.md', enabled: true,
    triggers: ['diff touches **/*.tsx or **/*.css', 'agent = frontend | vision', 'pre-handoff on a UI task'],
    tools: ['Playwright', 'Browser', 'Filesystem'], usedBy: ['frontend', 'vision', 'qa'],
    invocations: 448, lastUsed: '33 min ago', version: '2.2.6',
    project: 'all', tokens: 980, loads24h: 26, author: 'rajat',
    rules: [
      { when: 'the diff contains a .tsx, .css or a Tailwind class change', then: 'load' },
      { when: 'the route is unreachable without login', then: 'load with the seeded UAT session' },
      { when: 'diff is types-only', then: 'skip' },
    ],
    body: `# visual-diff

Claiming "UI updated" without a screenshot is not a handoff.

## Procedure
1. Capture baseline from origin/main build at 1440, 1280, 1100.
2. Apply the diff, rebuild, capture again on the same routes.
3. Mask: clocks, tnum counters, avatar seeds, anything with a timestamp.
4. Compare. Drift inside the changed component's bounding box is expected;
   drift outside it is a regression and fails the run.

## Threshold
- inside box:  report only
- outside box: > 0.35% pixels changed -> FAIL, attach both PNGs
- layout shift on any breakpoint -> FAIL regardless of pixel count

## Output
artifacts/visual/<task-id>/<route>-<width>-{base,head,diff}.png`,
  },
  {
    id: 'sk-10', name: 'Test Generator', slug: 'test-generator',
    description: 'Writes xUnit and Vitest cases from acceptance criteria, seeded with real ERP row shapes, not fake fixtures.',
    scope: 'global', source: '~/.claude/skills/test-generator/SKILL.md', enabled: true,
    triggers: ['plan step type = test', 'a public method changed with no covering test', 'agent = qa'],
    tools: ['Filesystem', 'Terminal', 'MSSQL'], usedBy: ['qa', 'backend', 'frontend'],
    invocations: 892, lastUsed: '9 min ago', version: '2.6.0',
    project: 'all', tokens: 2_240, loads24h: 41, author: 'rajat',
    rules: [
      { when: 'a changed method has zero covering tests', then: 'load and block handoff' },
      { when: 'acceptance criteria exist on the task', then: 'load and map one test per criterion' },
      { when: 'the change is a rename with no behaviour delta', then: 'skip' },
    ],
    body: `# test-generator

One test per acceptance criterion. Named after the behaviour, not the method.

## C# (xUnit + FluentAssertions)
  [Theory]
  [InlineData("27", "27", false)]   // Maharashtra -> Maharashtra, intra
  [InlineData("27", "29", true)]    // Maharashtra -> Karnataka, inter
  public async Task Posts_IGST_only_when_states_differ(...)

## Fixtures
- Seed from db/fixtures/erp_tax_2026.sql — real MST_TAX rows, real GSTIN prefixes.
- Never invent a tax rate. 0%, 0.25%, 3%, 5%, 12%, 18%, 28% and nothing else.
- Money is decimal(18,4). Assert to 4 places or the test is lying.

## Forbidden
- No Thread.Sleep. No DateTime.Now — inject IClock.
- No test named Test1 / ShouldWork / HappyPath.`,
  },
  {
    id: 'sk-11', name: 'Flake Hunter', slug: 'flake-hunter',
    description: 'Re-runs suspect specs 25x under load and quarantines anything that fails on timing, ordering or a shared row.',
    scope: 'global', source: '~/.claude/skills/flake-hunter/SKILL.md', enabled: true,
    triggers: ['a spec failed then passed in the same day', 'CI job flagged retry', 'agent = qa'],
    tools: ['Terminal', 'Filesystem', 'Git'], usedBy: ['qa', 'devops'],
    invocations: 176, lastUsed: '2 h ago', version: '1.3.8',
    project: 'all', tokens: 1_060, loads24h: 7, author: 'rajat',
    rules: [
      { when: 'the same spec has both a pass and a fail inside 24h', then: 'load and quarantine' },
      { when: 'a CI job used a retry to go green', then: 'load' },
      { when: 'the failure is a compile error', then: 'skip — that is not a flake' },
    ],
    body: `# flake-hunter

A retry that turns a build green is a bug you have agreed to pay for later.

## Loop
1. Re-run the spec 25x sequentially, then 25x with --parallel 8.
2. Re-run with the clock skewed +5h30m (IST vs UTC bites this repo constantly).
3. Re-run with the DB seeded in reverse insert order.

## Classify
timing     — awaits a fixed ms, or asserts on DateTime.Now
ordering   — depends on another spec's leftover row in TRANS_*
shared row — two specs write MST_TAX id 4
env        — passes local, fails in CI container only

## Action
Quarantine to tests/quarantine/ with an owner and a date.
Quarantine older than 14 days becomes a P2 task automatically.

## Current quarantine (ERP)
InvoicePostingTests.Reverses_CGST_on_interstate  — ordering, 6 days, owner QA agent`,
  },
  {
    id: 'sk-12', name: 'OWASP Sweep', slug: 'owasp-sweep',
    description: 'Targeted OWASP Top-10 pass over the diff only, with a legacy allowlist so it does not scream about 2012 code.',
    scope: 'global', source: '~/.claude/skills/owasp-sweep/SKILL.md', enabled: true,
    triggers: ['diff touches a controller, auth or a query string', 'pre-merge gate', 'agent = security'],
    tools: ['Filesystem', 'Tree-sitter', 'Terminal'], usedBy: ['security', 'reviewer'],
    invocations: 341, lastUsed: '52 min ago', version: '2.0.4',
    project: 'all', tokens: 2_460, loads24h: 18, author: 'rajat',
    rules: [
      { when: 'diff touches **/Controllers/** or any auth path', then: 'load, blocking' },
      { when: 'diff builds SQL by string concatenation', then: 'load, fail immediately' },
      { when: 'diff is confined to *.md or *.css', then: 'skip' },
    ],
    body: `# owasp-sweep

Scan the diff, not the repository. A full-repo scan on a 2.4M line ERP returns
noise and gets ignored, which is worse than not scanning.

## Checks, in priority order
A03 Injection      — string-concatenated SQL, Dapper without parameters
A01 Access control — [Authorize] missing on a new controller action
A02 Crypto         — MD5/SHA1, hardcoded IV, new RNGCryptoServiceProvider misuse
A08 Integrity      — deserialising user input with BinaryFormatter (still present in ERP)
A09 Logging        — logging a full request body containing GSTIN or PAN

## Legacy allowlist (documented, do not re-report)
  Legacy/ReportEngine/*.cs      — dynamic SQL, sandboxed, ADR-31
  Legacy/Payroll/CryptoUtil.cs  — 3DES, frozen until the payroll module is renewed

## Output
severity | file:line | rule | fix in one sentence | is it in the diff (yes/no)`,
  },
  {
    id: 'sk-13', name: 'Secret Scan', slug: 'secret-scan',
    description: 'Entropy plus pattern scan across staged files, config transforms and appsettings before anything is committed.',
    scope: 'global', source: '~/.claude/skills/secret-scan/SKILL.md', enabled: true,
    triggers: ['PreCommit hook', 'diff touches appsettings*.json or *.config', 'agent = security'],
    tools: ['Filesystem', 'Git', 'Terminal'], usedBy: ['security', 'devops', 'reviewer'],
    invocations: 1_027, lastUsed: '21 min ago', version: '1.8.9',
    project: 'all', tokens: 640, loads24h: 58, author: 'rajat',
    rules: [
      { when: 'git commit is about to run', then: 'load, blocking, always' },
      { when: 'a staged file matches appsettings*.json, *.pubxml, web.*.config', then: 'load' },
      { when: 'the match is inside tests/fixtures/ and prefixed FAKE_', then: 'allow' },
    ],
    body: `# secret-scan

Runs inside the PreCommit hook. Non-zero exit aborts the commit.

## Patterns
- SQL Server:  Password=, User ID=, Integrated Security=False
- Azure:       DefaultEndpointsProtocol=...AccountKey=
- JWT:         eyJ[A-Za-z0-9_-]{10,}\\.
- Private key: -----BEGIN (RSA|OPENSSH|EC) PRIVATE KEY-----
- Generic:     base64 >= 32 chars with Shannon entropy > 4.2

## Known offenders in history (already rotated, do not re-flag)
  src/Web/appsettings.Development.json @ 8f2c1ad  — UAT SQL password, rotated 2024-11
  deploy/erp-uat.pubxml @ 41b90e7                 — IIS deploy creds, rotated 2025-02

## Exit codes
0 clean | 2 secret found (commit blocked, path + line printed, value redacted)`,
  },
  {
    id: 'sk-14', name: 'React Conventions', slug: 'react-conventions',
    description: 'The house React 19 rules — no new state library, colocated hooks, token-only styling, no barrel files.',
    scope: 'project', source: '.claude/skills/react-conventions/SKILL.md', enabled: true,
    triggers: ['diff touches src/**/*.tsx', 'agent = frontend', 'new component created'],
    tools: ['Filesystem', 'Tree-sitter'], usedBy: ['frontend', 'vision', 'reviewer'],
    invocations: 596, lastUsed: '14 min ago', version: '4.1.0',
    project: 'aios', tokens: 1_380, loads24h: 34, author: 'rajat',
    rules: [
      { when: 'a new *.tsx file is created', then: 'load before the first write' },
      { when: 'the diff adds a dependency to package.json', then: 'load and reject state libraries' },
      { when: 'the file is a generated route stub', then: 'skip' },
    ],
    body: `# react-conventions

Match the file next to you. Preference beats correctness here.

## Rules
- State: useState / useMemo / useReducer. No Redux, Zustand, Jotai, MobX. Ever.
- Data: static mock modules under src/mock. No fetch, no react-query.
- Styling: theme tokens only — bg-surface, text-ink-2, border-line.
  A single bg-zinc-800 breaks all 8 themes and fails review.
- Components: one default export per page file, named sub-components colocated.
- No barrel index.ts re-exports outside src/components/os.
- Icons: lucide-react at size-3.5 / size-4, never larger inside a row.

## Density contract
body text 11-12.5px, rows <= 34px tall, radius sm, no shadow beyond border-line.

## TypeScript
strict + noUnusedLocals + verbatimModuleSyntax. import type for every type.`,
  },
  {
    id: 'sk-15', name: 'A11y Audit', slug: 'a11y-audit',
    description: 'Keyboard path, focus ring, contrast and label audit on the changed screens across all eight themes.',
    scope: 'global', source: '~/.claude/skills/a11y-audit/SKILL.md', enabled: false,
    triggers: ['diff adds an interactive element', 'route added to nav', 'agent = frontend'],
    tools: ['Playwright', 'Browser'], usedBy: ['frontend', 'qa'],
    invocations: 118, lastUsed: '2 days ago', version: '1.2.3',
    project: 'aios', tokens: 1_240, loads24h: 0, author: 'rajat',
    rules: [
      { when: 'the diff adds a button, input, dialog or a custom listbox', then: 'load' },
      { when: 'a new route appears in lib/nav.ts', then: 'load' },
      { when: 'currently disabled — renewal sprint took priority', then: 'never loads' },
    ],
    body: `# a11y-audit

Disabled during the renewal sprint. Re-enable before the first external pilot.

## Checks
- Every interactive element reachable by Tab, in visual order.
- Focus ring visible on all 8 themes (the two light themes fail most often).
- Dialog traps focus and returns it to the trigger on close.
- Contrast: text-dim on bg-surface measures 3.9:1 on theme "paper" — known fail.
- Every icon-only button has an accessible name.

## Known open
- Segmented control is div-based, arrow keys do nothing.
- DataTable rows are clickable <tr> with no role, unreachable by keyboard.`,
  },
  {
    id: 'sk-16', name: 'Changelog Builder', slug: 'changelog-builder',
    description: 'Turns merged commits into an operator-readable changelog, grouped by ERP module rather than by commit type.',
    scope: 'global', source: '~/.claude/skills/changelog-builder/SKILL.md', enabled: true,
    triggers: ['branch merged to main', 'release tag created', 'agent = docs'],
    tools: ['Git', 'Filesystem', 'Knowledge'], usedBy: ['docs', 'devops'],
    invocations: 94, lastUsed: 'yesterday 21:07', version: '1.4.0',
    project: 'all', tokens: 860, loads24h: 3, author: 'rajat',
    rules: [
      { when: 'a PR merges into main', then: 'load and append to Unreleased' },
      { when: 'a tag matching v*.* is created', then: 'load and cut the section' },
      { when: 'commit message starts with chore: or ci:', then: 'skip the commit, not the skill' },
    ],
    body: `# changelog-builder

The audience is an accountant in Nagpur, not a developer.

## Grouping
By ERP module — Invoicing, Inventory, Payroll, Reports, Masters — never by
feat/fix/chore. "fix(tax): reverse CGST" means nothing to the reader.

## Line shape
  Invoicing — Interstate invoices no longer show both CGST/SGST and IGST.
              Affects invoices dated on or after 2026-04-01. (TASK-492, BUG-883)

## Rules
- One line per user-visible change. Internal refactors go in a collapsed section.
- Always name the effective date when tax or rate behaviour changes.
- Link the task id and the bug id, both.
- If nothing user-visible shipped, write "Internal only" — do not pad.`,
  },
  {
    id: 'sk-17', name: 'Index Advisor', slug: 'index-advisor',
    description: 'Reads the actual plan cache and missing-index DMVs, then proposes indexes with a write-cost estimate.',
    scope: 'project', source: '.claude/skills/index-advisor/SKILL.md', enabled: true,
    triggers: ['query > 800ms in the slow log', 'prompt mentions slow | timeout | index', 'agent = database'],
    tools: ['MSSQL', 'Memory'], usedBy: ['database', 'backend'],
    invocations: 207, lastUsed: '4 h ago', version: '1.6.5',
    project: 'erp', tokens: 1_640, loads24h: 6, author: 'rajat',
    rules: [
      { when: 'a query in the slow log exceeds 800ms twice in an hour', then: 'load' },
      { when: 'a new WHERE clause hits a table over 5M rows', then: 'load' },
      { when: 'SQL Server suggests an index with impact under 25%', then: 'load, then reject it' },
    ],
    body: `# index-advisor

Never paste sys.dm_db_missing_index_details output as a recommendation.
That DMV suggests 11 overlapping indexes on TRANS_INVOICE alone.

## Method
1. Pull the plan from the cache, not from an estimated plan.
2. Consolidate suggestions: same leading column = one index, widest key wins.
3. Price the write side — TRANS_INVOICE takes ~14k inserts/day at month end.
4. Reject anything with impact < 25% or a key wider than 900 bytes.

## Live example
  TRANS_INVOICE_TAX (41.2M rows)
  proposed: IX_TRANS_INVOICE_TAX_InvoiceId_TaxCode INCLUDE (Amount, IsReversal)
  read gain: SP_CalculateTax 1,840ms -> 96ms
  write cost: +11% on the month-end posting batch, ~40s added to a 6m job
  verdict: ACCEPT, deploy in the 02:00 window`,
  },
  {
    id: 'sk-18', name: 'Multi-Modal Sweep', slug: 'multi-modal-sweep',
    description: 'Searches web, GitHub code, vendor docs and internal memory in parallel, then reconciles the disagreements.',
    scope: 'global', source: '~/.claude/skills/multi-modal-sweep/SKILL.md', enabled: true,
    triggers: ['agent = researcher', 'prompt asks how does X work | best way to', 'unknown library encountered'],
    tools: ['WebSearch', 'Browser', 'GitHub', 'Knowledge'], usedBy: ['researcher', 'architect'],
    invocations: 263, lastUsed: '1 h ago', version: '2.3.1',
    project: 'all', tokens: 1_320, loads24h: 12, author: 'rajat',
    rules: [
      { when: 'the Researcher agent starts any task', then: 'load' },
      { when: 'a library appears that is not in package.json or *.csproj', then: 'load' },
      { when: 'the question is answerable from MEM with confidence > 0.85', then: 'skip, answer from memory' },
    ],
    body: `# multi-modal-sweep

Four channels, always, before concluding anything.

1. Web search        — last 18 months only, drop SEO listicles
2. GitHub code       — real usage in repos with > 200 stars, read the tests
3. Official docs     — version-pinned to what this repo actually uses
4. Internal memory   — MEM entries for this project, they outrank the internet

## Reconcile
When channels disagree, say so explicitly and rank:
  internal memory > official docs (pinned) > GitHub usage > blog post

## Output per finding
claim | source url or MEM id | date | confidence 0-1 | contradicts (if any)

Never present a blog post as equal evidence to a version-pinned doc.`,
  },
  {
    id: 'sk-19', name: 'Source Ranking', slug: 'source-ranking',
    description: 'Scores research sources on recency, authority and version match, and kills anything written for an old major.',
    scope: 'global', source: '~/.claude/skills/source-ranking/SKILL.md', enabled: true,
    triggers: ['after multi-modal-sweep returns', 'more than 5 sources collected', 'agent = researcher'],
    tools: ['WebSearch', 'Knowledge'], usedBy: ['researcher', 'docs'],
    invocations: 231, lastUsed: '1 h ago', version: '1.9.0',
    project: 'all', tokens: 720, loads24h: 12, author: 'rajat',
    rules: [
      { when: 'multi-modal-sweep produced more than 5 sources', then: 'load, chained' },
      { when: 'any source lacks a publication date', then: 'load and score it 0 on recency' },
      { when: 'only one source exists', then: 'skip, flag as unverified instead' },
    ],
    body: `# source-ranking

score = 0.35*recency + 0.35*version_match + 0.20*authority + 0.10*corroboration

## recency
< 6 months 1.0 | < 18 months 0.7 | < 3 years 0.35 | older 0.1 | undated 0.0

## version_match
Compare against the pinned version in package.json / *.csproj.
A React 18 answer scored against React 19 caps at 0.4 regardless of authority.

## authority
official docs 1.0 | maintainer comment 0.9 | high-star repo 0.7 | Q&A 0.5 | blog 0.35

## Hard kill
- Anything about ASP.NET (non-Core) when the repo is ASP.NET Core 8.
- Anything using EF Core in this repo — ADR-19 rejected it, cite that instead.
- Aggregator pages that only restate another source.`,
  },
  {
    id: 'sk-20', name: 'UI Locator', slug: 'ui-locator',
    description: 'Maps a screenshot region back to the exact component file and line, before anything is edited.',
    scope: 'plugin', source: 'plugins/screenshot-to-code/skills/ui-locator/SKILL.md', enabled: true,
    triggers: ['an image is attached to the prompt', 'agent = vision', 'prompt says this button | yeh wala'],
    tools: ['Vision', 'Filesystem', 'Tree-sitter', 'Playwright'], usedBy: ['vision', 'frontend'],
    invocations: 174, lastUsed: '38 min ago', version: '1.4.7',
    project: 'all', tokens: 1_180, loads24h: 15, author: 'screenshot-to-code',
    rules: [
      { when: 'the prompt has an image attachment', then: 'load first, before any edit tool' },
      { when: 'the prompt uses a deictic phrase — this, yeh, wahan', then: 'load' },
      { when: 'the user already named a file path', then: 'skip' },
    ],
    body: `# ui-locator

"yeh button theek karo" with a screenshot. Find the file before touching anything.

## Steps
1. OCR every visible string in the region, keep the rarest one.
2. ripgrep that string across src/. Rare strings resolve in one hit.
3. If the string is dynamic, match on class signature + DOM shape instead.
4. Confirm by rendering the candidate route in Playwright and diffing the crop.
5. Report file:line and a confidence. Below 0.8, ask — do not guess.

## Anti-patterns
- Never match on a Tailwind class alone. "text-ink-2" appears 900+ times.
- Never assume the screenshot is current — check it against the running build.

## Output
component | file:line | confidence | route it renders on | screenshot crop path`,
  },
  {
    id: 'sk-21', name: 'Error Screenshot Triage', slug: 'error-screenshot-triage',
    description: 'Reads a WhatsApp screenshot of an ERP error dialog and maps it to the throwing line and the owning module.',
    scope: 'plugin', source: 'plugins/screenshot-to-code/skills/error-screenshot-triage/SKILL.md', enabled: true,
    triggers: ['image contains a dialog with the word Error | Exception', 'intake channel = WhatsApp', 'agent = vision'],
    tools: ['Vision', 'Filesystem', 'Memory', 'Git'], usedBy: ['vision', 'commander', 'qa'],
    invocations: 142, lastUsed: '2 h ago', version: '1.2.9',
    project: 'erp', tokens: 1_460, loads24h: 11, author: 'screenshot-to-code',
    rules: [
      { when: 'an attached image contains an exception dialog or a yellow ASP.NET error page', then: 'load' },
      { when: 'the screenshot came from the WhatsApp intake', then: 'load' },
      { when: 'a full stack trace is already pasted as text', then: 'skip' },
    ],
    body: `# error-screenshot-triage

Operators send a cropped phone photo of a dialog. That is the whole bug report.

## Extract
1. The message string, exactly, including the trailing period.
2. Any correlation id / ticket no visible in the title bar or status strip.
3. The screen the operator was on — read the window title, not the dialog.

## Map
- Search the exact string in resource files first (Resources/Messages.resx),
  then in throw new statements. Legacy messages are 70% resource-driven.
- "Object reference not set" with no id -> use the screen + the last deploy
  from the DevOps timeline to narrow to a module.

## Live example
photo: "Tax calculation failed for invoice INV-2026-03318."
resx:  Messages.resx :: TaxCalcFailed
throw: TaxService.cs:214 inside the interstate branch  -> BUG-883, TASK-492`,
  },
  {
    id: 'sk-22', name: 'Convention Check', slug: 'convention-check',
    description: 'Compares a diff against how the surrounding 40 files already do it and flags any invented pattern.',
    scope: 'project', source: '.claude/skills/convention-check/SKILL.md', enabled: true,
    triggers: ['pre-handoff review', 'diff adds a file in an existing folder', 'agent = reviewer'],
    tools: ['Filesystem', 'Tree-sitter', 'Git'], usedBy: ['reviewer', 'backend', 'frontend'],
    invocations: 683, lastUsed: '6 min ago', version: '2.5.2',
    project: 'all', tokens: 1_040, loads24h: 39, author: 'rajat',
    rules: [
      { when: 'a diff adds a file into a folder that already has 3+ siblings', then: 'load' },
      { when: 'the diff introduces a naming shape not seen elsewhere in the repo', then: 'load and fail' },
      { when: 'the folder is empty or new', then: 'skip — nothing to conform to' },
    ],
    body: `# convention-check

The correct pattern is whatever the neighbours already do. Consistency > elegance.

## Method
1. Take the 40 nearest sibling files by path distance.
2. Extract: file naming, folder depth, export style, error handling shape,
   logging call, test file location, async suffix usage.
3. Any property where the diff is a minority of one is a finding.

## Findings this week
- InvoiceTaxDto.cs used a record; all 61 sibling DTOs are sealed classes. FAIL.
- New page used export const Foo; all 30 pages use export default function. FAIL.
- Repository used Task<IEnumerable<T>>; siblings return Task<IReadOnlyList<T>>. FAIL.

## Escape hatch
A deliberate break needs an ADR. No ADR, no break.`,
  },
];

export const skillBySlug = (slug: string) => skills.find((s) => s.slug === slug);

export const skillScopes = ['all', 'global', 'project', 'plugin'] as const;
