import type { Worktree, Commit } from '@/types';

/* Local-only shapes (do not touch src/types) */
export interface ChangedFile {
  path: string;
  change: 'M' | 'A' | 'D' | 'R';
  additions: number;
  deletions: number;
  hunk: string;
}
export interface MergePreview {
  worktreeId: string;
  target: string;
  result: 'clean' | 'collides' | 'stale';
  files: number;
  note: string;
  collidesWith?: string;
  onFile?: string;
}
export interface ConflictHunk {
  side: 'ours' | 'theirs';
  branch: string;
  agent: string;
  commit: string;
  at: string;
  lines: string;
}
export interface Conflict {
  id: string;
  file: string;
  taskRef: string;
  region: string;
  hunks: ConflictHunk[];
  policy: string[];
  resolution: string;
  resolvedBy: string;
}
export interface PrCheck {
  id: string;
  name: string;
  status: 'pass' | 'fail' | 'running' | 'skipped' | 'warn';
  detail: string;
  durationS: number;
}
export interface PullRequest {
  id: string;
  number: string;
  title: string;
  branch: string;
  base: string;
  author: string;
  state: 'draft' | 'open' | 'awaiting_human' | 'merged' | 'blocked';
  taskRef: string;
  files: number;
  additions: number;
  deletions: number;
  commits: number;
  openedAt: string;
  reviewers: string[];
  checks: PrCheck[];
  humanGate: string;
  body: string;
}

export const worktreeTree = `careworks-erp/  (bare)  ~/work/erp.git
│
├── main                            ● clean      v3.14.1  ← protected, human-merge only
│   │
│   ├── task/invoice-tax-backend    ◆ dirty      backend    TASK-492  +214 −63
│   ├── task/invoice-tax-frontend   ◆ dirty      frontend   TASK-492  +96  −18
│   ├── task/invoice-tax-db         ▲ ahead 3    database   TASK-492  +181 −7
│   └── task/invoice-tax-tests      ● clean      qa         TASK-492  +402 −11
│
├── task/bulk-upload-frontend       ◆ dirty      frontend   TASK-488  +512 −140
├── task/bulk-upload-backend        ▲ ahead 2    backend    TASK-488  +288 −44
├── task/auth-session               ✖ conflict   devops     TASK-501  +41  −29
├── task/gstr1-export               ● clean      backend    TASK-476  +330 −12
├── fix/ledger-rounding-paise       ✔ merged     backend    TASK-461  +18  −6
└── chore/sp-catalog-refresh        ▲ ahead 1    database   TASK-455  +64  −0

legend   ● clean   ◆ dirty   ▲ ahead   ✖ conflict   ✔ merged`;

export const worktrees: Worktree[] = [
  {
    id: 'wt1', branch: 'task/invoice-tax-backend', path: '~/work/erp/wt/invoice-tax-backend',
    agent: 'backend', taskRef: 'TASK-492', status: 'dirty',
    filesChanged: 7, additions: 214, deletions: 63, ahead: 4, behind: 1,
    lastCommit: 'fix(tax): resolve jurisdiction from place-of-supply', lastCommitAt: '4 min ago',
  },
  {
    id: 'wt2', branch: 'task/invoice-tax-db', path: '~/work/erp/wt/invoice-tax-db',
    agent: 'database', taskRef: 'TASK-492', status: 'ahead',
    filesChanged: 4, additions: 181, deletions: 7, ahead: 3, behind: 1,
    lastCommit: 'feat(db): 0042_backfill_tax_jurisdiction.sql + rollback',
    lastCommitAt: '11 min ago',
  },
  {
    id: 'wt3', branch: 'task/invoice-tax-tests', path: '~/work/erp/wt/invoice-tax-tests',
    agent: 'qa', taskRef: 'TASK-492', status: 'clean',
    filesChanged: 6, additions: 402, deletions: 11, ahead: 5, behind: 1,
    lastCommit: 'test(tax): interstate CGST/SGST matrix, 22 cases', lastCommitAt: '19 min ago',
  },
  {
    id: 'wt4', branch: 'task/invoice-tax-frontend', path: '~/work/erp/wt/invoice-tax-frontend',
    agent: 'frontend', taskRef: 'TASK-492', status: 'dirty',
    filesChanged: 3, additions: 96, deletions: 18, ahead: 2, behind: 1,
    lastCommit: 'fix(invoice): show split head labels from server, not client map',
    lastCommitAt: '26 min ago',
  },
  {
    id: 'wt5', branch: 'task/bulk-upload-frontend', path: '~/work/erp/wt/bulk-upload-frontend',
    agent: 'frontend', taskRef: 'TASK-488', status: 'dirty',
    filesChanged: 11, additions: 512, deletions: 140, ahead: 6, behind: 3,
    lastCommit: 'feat(customer): drag-drop CSV zone with row-level error table',
    lastCommitAt: '34 min ago',
  },
  {
    id: 'wt6', branch: 'task/bulk-upload-backend', path: '~/work/erp/wt/bulk-upload-backend',
    agent: 'backend', taskRef: 'TASK-488', status: 'ahead',
    filesChanged: 8, additions: 288, deletions: 44, ahead: 2, behind: 3,
    lastCommit: 'feat(customer): chunked staging insert, 5k rows per tx',
    lastCommitAt: '52 min ago',
  },
  {
    id: 'wt7', branch: 'task/auth-session', path: '~/work/erp/wt/auth-session',
    agent: 'devops', taskRef: 'TASK-501', status: 'conflict',
    filesChanged: 4, additions: 41, deletions: 29, ahead: 1, behind: 9,
    lastCommit: 'chore(iis): move session state to SQL Server mode',
    lastCommitAt: '1 h 12 min ago',
  },
  {
    id: 'wt8', branch: 'task/gstr1-export', path: '~/work/erp/wt/gstr1-export',
    agent: 'backend', taskRef: 'TASK-476', status: 'clean',
    filesChanged: 9, additions: 330, deletions: 12, ahead: 7, behind: 2,
    lastCommit: 'feat(gstr1): B2CS bucketing per state_code + rate slab',
    lastCommitAt: '3 h ago',
  },
  {
    id: 'wt9', branch: 'fix/ledger-rounding-paise', path: '~/work/erp/wt/ledger-rounding',
    agent: 'backend', taskRef: 'TASK-461', status: 'merged',
    filesChanged: 2, additions: 18, deletions: 6, ahead: 0, behind: 0,
    lastCommit: 'fix(ledger): banker rounding at 2dp, matches SP_PostLedger',
    lastCommitAt: 'yesterday 18:40',
  },
  {
    id: 'wt10', branch: 'chore/sp-catalog-refresh', path: '~/work/erp/wt/sp-catalog',
    agent: 'database', taskRef: 'TASK-455', status: 'ahead',
    filesChanged: 1, additions: 64, deletions: 0, ahead: 1, behind: 5,
    lastCommit: 'chore(db): regenerate 1148 sproc signatures into catalog.json',
    lastCommitAt: 'yesterday 15:02',
  },
  {
    id: 'wt11', branch: 'task/opd-billing-split', path: '~/work/hims/wt/opd-billing-split',
    agent: 'backend', taskRef: 'TASK-310', status: 'dirty',
    filesChanged: 5, additions: 147, deletions: 61, ahead: 2, behind: 0,
    lastCommit: 'refactor(opd): extract BillingHeadResolver from OpdBillService',
    lastCommitAt: '2 h ago',
  },
  {
    id: 'wt12', branch: 'feat/surge-pricing-engine', path: '~/work/ride/wt/surge-pricing',
    agent: 'backend', taskRef: 'TASK-208', status: 'clean',
    filesChanged: 6, additions: 271, deletions: 4, ahead: 3, behind: 0,
    lastCommit: 'feat(pricing): h3-cell demand window, 90s decay', lastCommitAt: '4 h ago',
  },
];

export const changedFiles: Record<string, ChangedFile[]> = {
  wt1: [
    {
      path: 'src/Services/TaxService.cs', change: 'M', additions: 34, deletions: 11,
      hunk: `@@ -118,11 +118,34 @@ public class TaxService : ITaxService
-        var state = invoice.BillingAddress.StateCode;
-        if (state == company.StateCode)
-            return TaxSplit.Intra(cgst, sgst);
-        return TaxSplit.Intra(sgst, cgst);   // BUG-883: heads swapped
+        // ADR-52 — jurisdiction resolves from place-of-supply, never billing state.
+        var pos = _jurisdiction.ResolvePlaceOfSupply(invoice);
+        if (pos.StateCode == company.StateCode)
+            return TaxSplit.Intra(cgst: half, sgst: half);
+
+        // interstate => single IGST head, no CGST/SGST at all
+        return TaxSplit.Inter(igst: cgst + sgst);
     }
+
+    public JurisdictionResult ResolveJurisdiction(Invoice invoice)
+        => _jurisdiction.Resolve(invoice, _clock.Today);`,
    },
    {
      path: 'src/Services/InvoiceService.cs', change: 'M', additions: 41, deletions: 22,
      hunk: `@@ -402,9 +402,18 @@ public async Task<Invoice> RecalculateAsync(int invoiceId)
-        var split = _tax.Calculate(inv.Lines, inv.BillingAddress.StateCode);
+        var split = _tax.Calculate(inv.Lines, _tax.ResolveJurisdiction(inv));
         foreach (var line in inv.Lines)
-            line.TaxHead = split.HeadFor(line);
+        {
+            line.TaxHead   = split.HeadFor(line);
+            line.TaxAmount = split.AmountFor(line);   // was rounded twice, MEM-142
+        }`,
    },
    {
      path: 'src/Services/Jurisdiction/JurisdictionResolver.cs', change: 'A', additions: 88, deletions: 0,
      hunk: `@@ -0,0 +1,88 @@
+namespace Careworks.Erp.Services.Jurisdiction;
+
+/// Single source of truth for place-of-supply. Replaces the four
+/// inline state comparisons found by the Architect (impact IMP-3311).
+public sealed class JurisdictionResolver : IJurisdictionResolver
+{
+    private readonly IMasterTaxRepository _mst;
+    public JurisdictionResult Resolve(Invoice inv, DateOnly asOf) { ... }
+}`,
    },
    {
      path: 'src/Interfaces/IJurisdictionResolver.cs', change: 'A', additions: 19, deletions: 0,
      hunk: `@@ -0,0 +1,19 @@
+public interface IJurisdictionResolver
+{
+    JurisdictionResult Resolve(Invoice invoice, DateOnly asOf);
+    PlaceOfSupply ResolvePlaceOfSupply(Invoice invoice);
+}`,
    },
    {
      path: 'src/Repositories/MasterTaxRepository.cs', change: 'M', additions: 26, deletions: 9,
      hunk: `@@ -71,9 +71,26 @@ public class MasterTaxRepository : IMasterTaxRepository
-        const string sql = "SELECT * FROM MST_TAX WHERE rate_code = @code";
+        // 41 MST_TAX rows carry state_code NULL (pre-2019 data). Fall back to
+        // the company home state instead of throwing — legacy contract.
+        const string sql = @"SELECT rate_code, cgst, sgst, igst,
+                                    ISNULL(state_code, @homeState) AS state_code
+                             FROM MST_TAX WHERE rate_code = @code";`,
    },
    {
      path: 'src/Program.cs', change: 'M', additions: 3, deletions: 0,
      hunk: `@@ -58,6 +58,9 @@ builder.Services.AddScoped<ITaxService, TaxService>();
+builder.Services.AddScoped<IJurisdictionResolver, JurisdictionResolver>();
+builder.Services.AddScoped<IMasterTaxRepository, MasterTaxRepository>();`,
    },
    {
      path: 'docs/adr/ADR-52-jurisdiction-resolver.md', change: 'A', additions: 61, deletions: 0,
      hunk: `@@ -0,0 +1,61 @@
+# ADR-52 — One jurisdiction resolver
+Status: accepted (2026-09-10) · supersedes the inline state checks
+Evidence: BUG-883, MEM-142, SP_CalculateTax L288-L341`,
    },
  ],
  wt2: [
    {
      path: 'db/migrations/0042_backfill_tax_jurisdiction.sql', change: 'A', additions: 96, deletions: 0,
      hunk: `@@ -0,0 +1,96 @@
+-- 0042 backfill place-of-supply for 41 invoices raised 2026-04-01..2026-09-09
+-- TRANS_INVOICE is append-only (MEM-142) => write the correction into
+-- TRANS_INVOICE_ADJ and let SP_PostLedger fold it.
+BEGIN TRAN;
+  INSERT INTO TRANS_INVOICE_ADJ (invoice_id, reason_code, cgst_delta, sgst_delta, igst_delta)
+  SELECT i.invoice_id, 'TAXJUR', -i.cgst, -i.sgst, (i.cgst + i.sgst)
+  FROM TRANS_INVOICE i
+  JOIN MST_TAX t ON t.rate_code = i.rate_code
+  WHERE i.place_of_supply <> i.billing_state_code
+    AND i.invoice_date >= '2026-04-01';
+COMMIT;`,
    },
    {
      path: 'db/migrations/0042_rollback.sql', change: 'A', additions: 28, deletions: 0,
      hunk: `@@ -0,0 +1,28 @@
+DELETE FROM TRANS_INVOICE_ADJ WHERE reason_code = 'TAXJUR'
+  AND created_at >= '2026-09-10';
+EXEC SP_RecomputeLedgerWindow @from = '2026-04-01', @to = '2026-09-10';`,
    },
    {
      path: 'db/procs/SP_CalculateTax.sql', change: 'M', additions: 44, deletions: 7,
      hunk: `@@ -288,7 +288,44 @@ ALTER PROCEDURE dbo.SP_CalculateTax
-    IF @BillingState = @CompanyState
-        SELECT @Cgst = @Half, @Sgst = @Half
-    ELSE
-        SELECT @Cgst = @Half, @Sgst = @Half   -- interstate branch never split IGST
+    IF @PlaceOfSupply = @CompanyState
+        SELECT @Cgst = @Half, @Sgst = @Half, @Igst = 0
+    ELSE
+        SELECT @Cgst = 0, @Sgst = 0, @Igst = @Base * @Rate / 100.0`,
    },
    {
      path: 'db/catalog/MST_TAX.notes.md', change: 'M', additions: 13, deletions: 0,
      hunk: `@@ -12,0 +13,13 @@
+41 rows have state_code NULL — inserted by the 2019 bulk import that
+predates the state master. Do not backfill them blindly; three are
+union-territory codes that map to a different rate slab.`,
    },
  ],
  wt3: [
    {
      path: 'tests/Unit/TaxServiceJurisdictionTests.cs', change: 'A', additions: 186, deletions: 0,
      hunk: `@@ -0,0 +1,186 @@
+[Theory]
+[InlineData("27", "27", 9.0, 9.0, 0.0)]   // MH -> MH  intra
+[InlineData("27", "29", 0.0, 0.0, 18.0)]  // MH -> KA  interstate
+[InlineData("27", null, 9.0, 9.0, 0.0)]   // legacy NULL state, home fallback
+public void Splits_by_place_of_supply(...)`,
    },
    {
      path: 'tests/Integration/InvoiceRecalcTests.cs', change: 'A', additions: 121, deletions: 0,
      hunk: `@@ -0,0 +1,121 @@
+// INV-2026-08812 is the invoice the PM reported. Locked as a golden case.
+[Fact] public async Task INV_2026_08812_recalcs_to_igst_1809_00() { ... }`,
    },
    {
      path: 'tests/Legacy/InvoiceTaxLegacyTest.cs', change: 'M', additions: 14, deletions: 9,
      hunk: `@@ -44,9 +44,14 @@
-    // known-failing since 2021, kept for the rounding contract
+    // Still failing — pre-existing legacy behaviour, NOT a regression from
+    // TASK-492. QA flagged it separately so the merge gate stays green.
+    [Fact(Skip = "legacy-expected, tracked as BUG-702")]`,
    },
    {
      path: 'tests/fixtures/mst_tax_seed.sql', change: 'M', additions: 52, deletions: 2,
      hunk: `@@ -3,2 +3,52 @@
+INSERT INTO MST_TAX (rate_code, cgst, sgst, igst, state_code) VALUES
+ ('GST18', 9.0, 9.0, 18.0, '27'), ('GST18', 9.0, 9.0, 18.0, '29'),
+ ('GST05', 2.5, 2.5,  5.0, NULL);`,
    },
    {
      path: 'tests/Playwright/invoice-tax.spec.ts', change: 'A', additions: 29, deletions: 0,
      hunk: `@@ -0,0 +1,29 @@
+test('interstate invoice shows one IGST row, no CGST/SGST rows', async ({ page }) => {
+  await page.goto('/invoices/INV-2026-08812');
+  await expect(page.getByTestId('tax-head-igst')).toHaveText('IGST 18%');
+});`,
    },
  ],
  wt4: [
    {
      path: 'src/features/invoice/TaxSplitTable.tsx', change: 'M', additions: 58, deletions: 12,
      hunk: `@@ -31,12 +31,58 @@ export function TaxSplitTable({ invoice }: Props) {
-  const heads = invoice.state === company.state ? ['CGST', 'SGST'] : ['CGST', 'SGST'];
+  // heads now come from the server payload — the client must never
+  // re-derive jurisdiction (ADR-52)
+  const heads = invoice.taxSplit.heads;`,
    },
    {
      path: 'src/features/invoice/useInvoiceTotals.ts', change: 'M', additions: 27, deletions: 6,
      hunk: `@@ -9,6 +9,27 @@
-  const total = lines.reduce((s, l) => s + l.cgst + l.sgst, 0);
+  const total = lines.reduce((s, l) => s + l.taxAmount, 0);`,
    },
    {
      path: 'src/features/invoice/__snapshots__/TaxSplitTable.snap', change: 'M', additions: 11, deletions: 0,
      hunk: `@@ -18,0 +19,11 @@
+ IGST 18%   ₹ 1,809.00
-CGST 9%    ₹   904.50
-SGST 9%    ₹   904.50`,
    },
  ],
  wt7: [
    {
      path: 'src/Services/TaxService.cs', change: 'M', additions: 12, deletions: 19,
      hunk: `@@ -118,19 +118,12 @@ public class TaxService : ITaxService
-        var state = invoice.BillingAddress.StateCode;
+        // session-scoped tenant state so IIS SQL session mode can rehydrate
+        var state = _session.Get<string>("tenant.stateCode")
+                    ?? invoice.BillingAddress.StateCode;`,
    },
    {
      path: 'src/Web.config', change: 'M', additions: 9, deletions: 4,
      hunk: `@@ -22,4 +22,9 @@
-  <sessionState mode="InProc" timeout="20" />
+  <sessionState mode="SQLServer" timeout="480"
+                sqlConnectionString="..." allowCustomSqlDatabase="true" />`,
    },
    {
      path: 'deploy/iis/app-pool.ps1', change: 'M', additions: 14, deletions: 6,
      hunk: `@@ -11,6 +11,14 @@
-Set-ItemProperty $pool -Name recycling.periodicRestart.time -Value "00:20:00"
+Set-ItemProperty $pool -Name recycling.periodicRestart.time -Value "00:00:00"
+Set-ItemProperty $pool -Name processModel.idleTimeout      -Value "00:00:00"`,
    },
    {
      path: 'src/Middleware/SessionDiagnostics.cs', change: 'A', additions: 6, deletions: 0,
      hunk: `@@ -0,0 +1,6 @@
+// logs session id + node on every 401 so we can prove affinity loss`,
    },
  ],
  wt5: [
    {
      path: 'src/features/customer/BulkUploadDropzone.tsx', change: 'A', additions: 204, deletions: 0,
      hunk: `@@ -0,0 +1,204 @@
+export function BulkUploadDropzone({ onChunk }: Props) {
+  // 50k rows => 10 chunks of 5k, matches the backend tx boundary
+}`,
    },
    {
      path: 'src/features/customer/RowErrorTable.tsx', change: 'A', additions: 148, deletions: 0,
      hunk: `@@ -0,0 +1,148 @@
+// row-level errors, virtualised — 50k rows must not mount 50k nodes`,
    },
    {
      path: 'src/features/customer/CustomerList.tsx', change: 'M', additions: 63, deletions: 41,
      hunk: `@@ -88,41 +88,63 @@
-  const [rows, setRows] = useState<Customer[]>([]);
+  const { rows, refetch } = useCustomerPage(page, pageSize);`,
    },
  ],
  wt8: [
    {
      path: 'src/Services/Gstr1ExportService.cs', change: 'A', additions: 212, deletions: 0,
      hunk: `@@ -0,0 +1,212 @@
+// B2CS bucketing: group by (state_code, rate) then sum taxable value.
+// Uses the same JurisdictionResolver as TASK-492 — do not fork it.`,
    },
    {
      path: 'db/procs/SP_Gstr1B2csBuckets.sql', change: 'A', additions: 88, deletions: 0,
      hunk: `@@ -0,0 +1,88 @@
+CREATE PROCEDURE dbo.SP_Gstr1B2csBuckets @from DATE, @to DATE AS`,
    },
  ],
};

export const mergePreview: MergePreview[] = [
  { worktreeId: 'wt3', target: 'main', result: 'clean', files: 6, note: 'test-only surface, no source overlap' },
  { worktreeId: 'wt2', target: 'main', result: 'clean', files: 4, note: 'db/ tree untouched by other worktrees' },
  { worktreeId: 'wt4', target: 'main', result: 'clean', files: 3, note: 'src/features/invoice/* owned solely by frontend' },
  {
    worktreeId: 'wt1', target: 'main', result: 'collides', files: 7,
    note: 'overlapping hunk at TaxService.cs L118-L137',
    collidesWith: 'task/auth-session', onFile: 'src/Services/TaxService.cs',
  },
  {
    worktreeId: 'wt7', target: 'main', result: 'collides', files: 4,
    note: 'same region rewritten for session-scoped tenant state',
    collidesWith: 'task/invoice-tax-backend', onFile: 'src/Services/TaxService.cs',
  },
  { worktreeId: 'wt6', target: 'main', result: 'stale', files: 8, note: '3 behind — rebase onto main before compare' },
  { worktreeId: 'wt5', target: 'main', result: 'stale', files: 11, note: '3 behind, CustomerList.tsx moved on main' },
  { worktreeId: 'wt8', target: 'main', result: 'clean', files: 9, note: 'reuses JurisdictionResolver, no redefinition' },
  { worktreeId: 'wt10', target: 'main', result: 'stale', files: 1, note: '5 behind — catalog regen must run after 0042' },
];

export const conflicts: Conflict[] = [
  {
    id: 'cf1',
    file: 'src/Services/TaxService.cs',
    taskRef: 'TASK-492 ✕ TASK-501',
    region: 'L118 – L137  ·  TaxService.Calculate(...)',
    hunks: [
      {
        side: 'ours', branch: 'task/invoice-tax-backend', agent: 'backend',
        commit: '9f3ac21', at: '13:55:18',
        lines: `        // ADR-52 — jurisdiction resolves from place-of-supply,
        // never from the billing address state.
        var pos = _jurisdiction.ResolvePlaceOfSupply(invoice);

        if (pos.StateCode == company.StateCode)
            return TaxSplit.Intra(cgst: half, sgst: half);

        return TaxSplit.Inter(igst: cgst + sgst);`,
      },
      {
        side: 'theirs', branch: 'task/auth-session', agent: 'devops',
        commit: 'c40e7bd', at: '12:48:02',
        lines: `        // session-scoped tenant state so the IIS SQLServer session
        // mode can rehydrate after an app-pool recycle.
        var state = _session.Get<string>("tenant.stateCode")
                    ?? invoice.BillingAddress.StateCode;

        if (state == company.StateCode)
            return TaxSplit.Intra(cgst: half, sgst: half);

        return TaxSplit.Intra(sgst: half, cgst: half);`,
      },
    ],
    policy: [
      'Ownership: the task that declared the file in its plan wins the region — TASK-492 declared TaxService.cs, TASK-501 did not.',
      'A HIGH-risk branch (TASK-501, blocked on APPR-118) never auto-merges over an in-flight MEDIUM branch.',
      'MEM-142 outranks convenience: TRANS_INVOICE semantics may not be re-derived on the session object.',
      'Reviewer must re-run on the merged region before the human gate — a resolved conflict is a new diff, not a carry-over approval.',
    ],
    resolution:
      'Take ours (TASK-492). The session lookup is re-applied by the DevOps agent as a decorator around IJurisdictionResolver instead of an inline read, so both intents survive. TASK-501 rebases; its 3 remaining files merge clean.',
    resolvedBy: 'Orchestrator → proposed · Code Reviewer → seconded · awaiting human confirm',
  },
];

export const commits: Commit[] = [
  { sha: '9f3ac21', message: 'fix(tax): resolve jurisdiction from place-of-supply, not billing state', author: 'Backend Engineer', at: '13:55', files: 3, branch: 'task/invoice-tax-backend' },
  { sha: '7d1b8e4', message: 'refactor(tax): extract JurisdictionResolver behind IJurisdictionResolver', author: 'Backend Engineer', at: '13:54', files: 4, branch: 'task/invoice-tax-backend' },
  { sha: 'a02f55c', message: 'fix(repo): ISNULL(state_code, home) for the 41 pre-2019 MST_TAX rows', author: 'Backend Engineer', at: '13:51', files: 1, branch: 'task/invoice-tax-backend' },
  { sha: '4be9012', message: 'feat(db): 0042_backfill_tax_jurisdiction.sql + verified rollback', author: 'Database Engineer', at: '13:48', files: 2, branch: 'task/invoice-tax-db' },
  { sha: 'e17c6aa', message: 'fix(sp): SP_CalculateTax interstate branch emits IGST, drops split heads', author: 'Database Engineer', at: '13:41', files: 1, branch: 'task/invoice-tax-db' },
  { sha: '2c88f31', message: 'docs(db): note the 3 union-territory codes hiding in the NULL state rows', author: 'Database Engineer', at: '13:36', files: 1, branch: 'task/invoice-tax-db' },
  { sha: 'b6d40f9', message: 'test(tax): interstate CGST/SGST matrix, 22 cases incl. NULL-state legacy', author: 'QA Engineer', at: '13:33', files: 2, branch: 'task/invoice-tax-tests' },
  { sha: '55a1e7d', message: 'test(invoice): lock INV-2026-08812 as the golden recalc case', author: 'QA Engineer', at: '13:29', files: 1, branch: 'task/invoice-tax-tests' },
  { sha: 'f9027b1', message: 'test(legacy): skip BUG-702 rounding test with an explicit reason', author: 'QA Engineer', at: '13:22', files: 1, branch: 'task/invoice-tax-tests' },
  { sha: '3ee5c04', message: 'fix(invoice): render tax heads from server payload (ADR-52)', author: 'Frontend Engineer', at: '13:18', files: 2, branch: 'task/invoice-tax-frontend' },
  { sha: 'd71aa88', message: 'chore(invoice): update TaxSplitTable snapshot to single IGST row', author: 'Frontend Engineer', at: '13:14', files: 1, branch: 'task/invoice-tax-frontend' },
  { sha: 'c40e7bd', message: 'chore(iis): move session state to SQLServer mode, kill 20-min recycle', author: 'DevOps Engineer', at: '12:48', files: 3, branch: 'task/auth-session' },
  { sha: '81f3d5e', message: 'feat(customer): drag-drop CSV zone with virtualised row-error table', author: 'Frontend Engineer', at: '12:31', files: 2, branch: 'task/bulk-upload-frontend' },
  { sha: '0a6c249', message: 'feat(customer): chunked staging insert, 5k rows per transaction', author: 'Backend Engineer', at: '11:57', files: 4, branch: 'task/bulk-upload-backend' },
  { sha: '6bb17f2', message: 'feat(gstr1): B2CS bucketing per state_code + rate slab', author: 'Backend Engineer', at: '10:44', files: 3, branch: 'task/gstr1-export' },
  { sha: 'ae4900d', message: 'chore(db): regenerate 1148 sproc signatures into catalog.json', author: 'Database Engineer', at: 'yest 15:02', files: 1, branch: 'chore/sp-catalog-refresh' },
  { sha: '1f8b73c', message: 'fix(ledger): banker rounding at 2dp to match SP_PostLedger', author: 'Backend Engineer', at: 'yest 18:40', files: 2, branch: 'fix/ledger-rounding-paise' },
  { sha: '5d2ee81', message: 'Merge pull request #1418 from fix/ledger-rounding-paise', author: 'You', at: 'yest 18:52', files: 2, branch: 'main' },
];

export const pullRequests: PullRequest[] = [
  {
    id: 'pr1', number: '#1421',
    title: 'fix(tax): interstate invoices must emit IGST, not a reversed CGST/SGST split',
    branch: 'task/invoice-tax-backend + -db + -tests + -frontend', base: 'main',
    author: 'AI Commander (on behalf of 4 agents)', state: 'awaiting_human', taskRef: 'TASK-492',
    files: 20, additions: 893, deletions: 99, commits: 11, openedAt: 'today 14:02',
    reviewers: ['Code Reviewer', 'Security Engineer', 'Architect'],
    checks: [
      { id: 'k1', name: 'build / dotnet 8', status: 'pass', detail: '0 errors, 2 warnings (CS8618 ×2, pre-existing)', durationS: 74 },
      { id: 'k2', name: 'test / unit', status: 'pass', detail: '184 passed, 0 failed', durationS: 41 },
      { id: 'k3', name: 'test / integration', status: 'pass', detail: '62 passed, 0 failed', durationS: 118 },
      { id: 'k4', name: 'test / api contract', status: 'pass', detail: '41 passed', durationS: 33 },
      { id: 'k5', name: 'test / legacy suite', status: 'warn', detail: '1 skipped — BUG-702 rounding, pre-existing', durationS: 27 },
      { id: 'k6', name: 'review / Code Reviewer', status: 'pass', detail: 'round 2 approved, REV-892 + REV-893 resolved', durationS: 256 },
      { id: 'k7', name: 'security / dep + secret scan', status: 'pass', detail: '0 high, 0 secrets, 3 low (transitive)', durationS: 62 },
      { id: 'k8', name: 'db / migration dry-run', status: 'pass', detail: '0042 applied to snapshot erp_2026_09_09, rollback verified', durationS: 191 },
      { id: 'k9', name: 'merge / conflict scan', status: 'fail', detail: 'TaxService.cs collides with task/auth-session', durationS: 8 },
      { id: 'k10', name: 'deploy / staging preview', status: 'running', detail: 'building image erp-api:pr-1421', durationS: 44 },
    ],
    humanGate:
      'MEDIUM risk + a data-touching migration + one unresolved worktree collision. Merge stays blocked until the human PM confirms the conflict resolution and the 0042 backfill window.',
    body:
      'Interstate orders were splitting into CGST/SGST with the heads reversed (BUG-883). Root cause is four inline billing-state comparisons; ADR-52 replaces them with one JurisdictionResolver. 41 historic invoices are corrected through TRANS_INVOICE_ADJ because TRANS_INVOICE is append-only (MEM-142).',
  },
  {
    id: 'pr2', number: '#1419',
    title: 'feat(gstr1): B2CS bucketing per state_code and rate slab',
    branch: 'task/gstr1-export', base: 'main', author: 'Backend Engineer',
    state: 'open', taskRef: 'TASK-476',
    files: 9, additions: 330, deletions: 12, commits: 7, openedAt: 'today 10:52',
    reviewers: ['Code Reviewer'],
    checks: [
      { id: 'k1', name: 'build / dotnet 8', status: 'pass', detail: '0 errors', durationS: 69 },
      { id: 'k2', name: 'test / unit', status: 'pass', detail: '96 passed', durationS: 28 },
      { id: 'k3', name: 'review / Code Reviewer', status: 'running', detail: 'round 1 · 9 files', durationS: 88 },
      { id: 'k4', name: 'security / dep audit', status: 'skipped', detail: 'no dependency delta', durationS: 0 },
    ],
    humanGate: 'LOW risk, read-only surface — auto-merge allowed once the reviewer returns green.',
    body: 'GSTR-1 B2CS section was aggregating on the billing state. Reuses the TASK-492 resolver rather than forking it.',
  },
  {
    id: 'pr3', number: '#1420',
    title: 'chore(iis): SQLServer session state, remove the 20-minute app-pool recycle',
    branch: 'task/auth-session', base: 'main', author: 'DevOps Engineer',
    state: 'blocked', taskRef: 'TASK-501',
    files: 4, additions: 41, deletions: 29, commits: 3, openedAt: 'today 12:55',
    reviewers: ['Security Engineer', 'Code Reviewer'],
    checks: [
      { id: 'k1', name: 'build / dotnet 8', status: 'pass', detail: '0 errors', durationS: 71 },
      { id: 'k2', name: 'test / unit', status: 'fail', detail: '3 failed — TaxServiceTests, region conflicts with #1421', durationS: 39 },
      { id: 'k3', name: 'merge / conflict scan', status: 'fail', detail: 'src/Services/TaxService.cs L118-L137', durationS: 7 },
      { id: 'k4', name: 'security / prod config review', status: 'warn', detail: 'writes a production connection string — APPR-118 required', durationS: 15 },
    ],
    humanGate: 'HIGH risk — touches production IIS configuration. Blocked on APPR-118 and on the TaxService.cs conflict resolution.',
    body: 'Users drop after ~20 minutes. InProc session state dies with every app-pool recycle; move to SQLServer mode and disable the periodic restart.',
  },
];
