import type { TestSuite, FailedTest, Review } from '@/types';

export const suites: TestSuite[] = [
  { id: 's1', name: 'Build', kind: 'build', status: 'pass', passed: 1, total: 1, durationS: 38, runner: 'dotnet build -warnaserror' },
  { id: 's2', name: 'Lint', kind: 'lint', status: 'pass', passed: 1, total: 1, durationS: 6, runner: 'dotnet format --verify + oxlint' },
  { id: 's3', name: 'Unit', kind: 'unit', status: 'pass', passed: 184, total: 184, durationS: 41, runner: 'dotnet test · xunit' },
  { id: 's4', name: 'Integration', kind: 'integration', status: 'pass', passed: 62, total: 62, durationS: 118, runner: 'dotnet test · Testcontainers' },
  { id: 's5', name: 'API contract', kind: 'api', status: 'pass', passed: 41, total: 41, durationS: 27, runner: 'schemathesis' },
  { id: 's6', name: 'E2E', kind: 'e2e', status: 'pass', passed: 27, total: 27, durationS: 214, runner: 'playwright · chromium' },
  { id: 's7', name: 'Visual', kind: 'visual', status: 'warn', passed: 22, total: 24, durationS: 96, runner: 'playwright · pixel diff' },
  { id: 's8', name: 'Security', kind: 'security', status: 'pass', passed: 9, total: 9, durationS: 52, runner: 'trivy + gitleaks' },
  { id: 's9', name: 'Regression', kind: 'regression', status: 'warn', passed: 2_411, total: 2_412, durationS: 340, runner: 'dotnet test · full tax surface' },
];

export const failures: FailedTest[] = [
  {
    id: 'f1', name: 'InvoiceTaxLegacyTest.Rounds_HalfAwayFromZero_At_TwoDecimals', suite: 'Regression',
    reason: 'Existing legacy behaviour. Money rounds half-away-from-zero because eleven years of posted ledgers depend on it; this assertion has been red since it was written as documentation of that contract.',
    legacyExpected: true,
    aiRecommendation: 'DO NOT modify production logic. This failure is the contract, not a defect. If it ever turns green, ADR-47 has been silently broken and the change must be reverted.',
    file: 'tests/Careworks.Erp.Billing.Tests/InvoiceTaxLegacyTest.cs', line: 64,
    diff: `  Expected: 1234.57\n  Actual:   1234.56\n         at InvoiceTaxLegacyTest.Rounds_HalfAwayFromZero_At_TwoDecimals()\n         MidpointRounding.ToEven is used by decimal.Round by default —\n         the ledger uses AwayFromZero. Documented in ADR-47.`,
  },
  {
    id: 'f2', name: 'InvoiceTaxSummary › renders a single IGST head interstate', suite: 'Visual',
    reason: 'Baseline still shows the CGST/SGST pair. The component now renders one IGST row, which is the intended change from TASK-492 — the baseline has not been re-approved yet.',
    legacyExpected: false,
    aiRecommendation: 'Approve the new baseline once a human has confirmed the invoice print layout. Pixel diff is 4.1%, entirely inside the tax block.',
    file: 'tests/e2e/invoice-print.spec.ts', line: 118,
  },
  {
    id: 'f3', name: 'BulkImportE2E › 50k rows complete under 90s', suite: 'E2E',
    reason: 'Flaky. Passes on the self-hosted runner (72s) and fails on CI containers (94-108s) where the database volume is network-backed.',
    legacyExpected: false,
    aiRecommendation: 'Quarantine and re-time against the CI disk profile rather than loosening the budget — the 90s number came from an operator expectation, not from a measurement.',
    file: 'tests/e2e/BulkImportE2E.spec.ts', line: 41,
  },
];

export const coverage = [
  { layer: 'Backend · services', pct: 78, lines: '214k / 274k' },
  { layer: 'Backend · repositories', pct: 84, lines: '46k / 55k' },
  { layer: 'Database · procedures', pct: 41, lines: '212k / 512k' },
  { layer: 'Frontend · modules', pct: 66, lines: '318k / 482k' },
  { layer: 'Frontend · shared', pct: 81, lines: '92k / 114k' },
  { layer: 'Infrastructure', pct: 22, lines: '4k / 18k' },
];

export const runs = [
  { id: 'r1', ref: 'CI-2211', trigger: 'Backend Engineer', branch: 'task/invoice-tax-backend', durationS: 348, pass: 99.9, commit: 'a82f91c', at: '14:19' },
  { id: 'r2', ref: 'CI-2210', trigger: 'QA Engineer', branch: 'task/invoice-tax-tests', durationS: 302, pass: 100, commit: '4d0e17b', at: '13:58' },
  { id: 'r3', ref: 'CI-2209', trigger: 'Hook · Stop', branch: 'task/bulk-upload-frontend', durationS: 288, pass: 97.4, commit: '9c31aa0', at: '13:21' },
  { id: 'r4', ref: 'CI-2208', trigger: 'You', branch: 'main', durationS: 402, pass: 100, commit: '18b7f24', at: '12:44' },
  { id: 'r5', ref: 'CI-2207', trigger: 'Frontend Engineer', branch: 'task/reports-grid', durationS: 265, pass: 100, commit: 'e04c9d1', at: '11:52' },
  { id: 'r6', ref: 'CI-2206', trigger: 'Database Engineer', branch: 'task/sproc-audit', durationS: 194, pass: 100, commit: '77a2b3e', at: '11:08' },
  { id: 'r7', ref: 'CI-2205', trigger: 'Hook · PreCommit', branch: 'task/opd-rounding', durationS: 156, pass: 94.1, commit: 'bb61c02', at: '10:31' },
  { id: 'r8', ref: 'CI-2204', trigger: 'QA Engineer', branch: 'task/driver-kyc', durationS: 341, pass: 100, commit: '2f8e440', at: '09:47' },
  { id: 'r9', ref: 'CI-2203', trigger: 'Backend Engineer', branch: 'task/auth-session', durationS: 210, pass: 88.2, commit: 'c19d7a5', at: '08:55' },
  { id: 'r10', ref: 'CI-2202', trigger: 'Hook · Stop', branch: 'task/memory-decay', durationS: 122, pass: 100, commit: '5aa03f9', at: '08:12' },
  { id: 'r11', ref: 'CI-2201', trigger: 'You', branch: 'main', durationS: 388, pass: 99.9, commit: 'd7c1e88', at: 'yesterday 19:40' },
  { id: 'r12', ref: 'CI-2200', trigger: 'Frontend Engineer', branch: 'task/discharge-templates', durationS: 176, pass: 100, commit: '3e90ba7', at: 'yesterday 17:22' },
];

export const visualDiffs = [
  { id: 'v1', name: 'Invoice · tax block', baseline: 'CGST 9% + SGST 9%', current: 'IGST 18%', diff: 4.1, verdict: 'intended — TASK-492' },
  { id: 'v2', name: 'Reports · grid header', baseline: 'legacy grid', current: 'new DataGrid', diff: 11.7, verdict: 'approved yesterday' },
  { id: 'v3', name: 'Customer · bulk upload', baseline: '—', current: 'new dialog', diff: 100, verdict: 'new surface, no baseline' },
  { id: 'v4', name: 'Invoice · print preview', baseline: 'v3.13', current: 'v3.14', diff: 0.0, verdict: 'unchanged' },
];

export const reviews: Review[] = [
  {
    id: 'rv1', ref: 'REV-2611', taskRef: 'TASK-492', projectId: 'erp', reviewer: 'Code Reviewer', verdict: 'changes_requested',
    round: 1, createdAt: '14:00 today', filesChanged: 7, additions: 214, deletions: 96,
    reviewerNote: 'Modification of TaxService affects 4 modules. The resolver is the right shape, but the procedure signature change cannot ship in one step against a single database — either every caller lands together or the parameter is defaulted for one release.',
    checks: [
      { id: 'c1', label: 'SOLID', status: 'pass', note: 'ResolveJurisdiction is one reason to change. Correctly extracted.' },
      { id: 'c2', label: 'Naming convention', status: 'fail', note: 'TaxHelper.cs introduced — Helper suffix is banned by project rule r2.' },
      { id: 'c3', label: 'Existing architecture', status: 'pass', note: 'Interface-first, registered in ServiceCollectionExtensions.' },
      { id: 'c4', label: 'Repository pattern', status: 'pass', note: 'No SQL text reached a service.' },
      { id: 'c5', label: 'Null handling', status: 'fail', note: 'place_of_supply is nullable for pre-2019 orders and is dereferenced unguarded.' },
      { id: 'c6', label: 'Error handling', status: 'pass', note: 'Jurisdiction failure surfaces as a domain error, not an exception.' },
      { id: 'c7', label: 'Tests', status: 'pass', note: '18 new cases including SEZ and union territory.' },
      { id: 'c8', label: 'Migration rollback', status: 'pass', note: '0042 dry-run verified, rollback executed in the dry-run.' },
      { id: 'c9', label: 'Legacy risk', status: 'warn', note: 'SP_CalculateTax has 11 callers. Signature change is not incrementally deployable.' },
      { id: 'c10', label: 'Security', status: 'pass', note: 'No new input surface.' },
    ],
    findings: [
      { id: 'f1', severity: 'blocker', file: 'server/Careworks.Erp.Tax/TaxHelper.cs', line: 1, rule: 'r2 · class-object naming', agent: 'Code Reviewer',
        message: 'Helper suffix is banned. This file is a service and should be named for what it does.',
        suggestion: 'Rename to JurisdictionResolver.cs and register it behind ITaxJurisdictionResolver, which already exists.' },
      { id: 'f2', severity: 'blocker', file: 'server/Careworks.Erp.Tax/Services/TaxService.cs', line: 148, rule: 'null safety', agent: 'Code Reviewer',
        message: 'order.PlaceOfSupply is dereferenced without a guard. It is null for every order raised before the 2019 schema change — 1.8M rows.',
        suggestion: 'Fall back to the billing state when PlaceOfSupply is null, and record that fallback on the invoice so the decision is auditable.' },
      { id: 'f3', severity: 'major', file: 'db/procedures/SP_CalculateTax.sql', line: 288, rule: 'legacy contract', agent: 'Architect',
        message: 'The jurisdiction parameter is required, which breaks all 11 callers on deploy.',
        suggestion: 'Default the parameter to the current derivation for one release, migrate callers, then make it required.' },
      { id: 'f4', severity: 'minor', file: 'web/src/modules/invoice/InvoiceTaxSummary.tsx', line: 74, rule: 'a11y', agent: 'Frontend Engineer',
        message: 'The tax head switch changes a table header without announcing it to assistive tech.',
        suggestion: 'Add aria-live="polite" to the tax block wrapper.' },
      { id: 'f5', severity: 'nit', file: 'server/Careworks.Erp.Tax/Services/TaxService.cs', line: 202, rule: 'style', agent: 'Code Reviewer',
        message: 'Two blank lines between members where the file uses one everywhere else.', suggestion: 'dotnet format will fix this on the next PostToolUse hook.' },
    ],
  },
  {
    id: 'rv2', ref: 'REV-2612', taskRef: 'TASK-492', projectId: 'erp', reviewer: 'Code Reviewer', verdict: 'approved',
    round: 2, createdAt: '14:20 today', filesChanged: 7, additions: 231, deletions: 104,
    reviewerNote: 'All blockers resolved. The defaulted parameter is the right call — it makes this deployable without a coordinated release. Approved for the human merge gate.',
    checks: [
      { id: 'c1', label: 'SOLID', status: 'pass', note: 'Unchanged.' },
      { id: 'c2', label: 'Naming convention', status: 'pass', note: 'Renamed to JurisdictionResolver.' },
      { id: 'c3', label: 'Existing architecture', status: 'pass', note: 'Unchanged.' },
      { id: 'c4', label: 'Repository pattern', status: 'pass', note: 'Unchanged.' },
      { id: 'c5', label: 'Null handling', status: 'pass', note: 'Documented fallback, recorded on the invoice.' },
      { id: 'c6', label: 'Error handling', status: 'pass', note: 'Unchanged.' },
      { id: 'c7', label: 'Tests', status: 'pass', note: '+3 cases covering the pre-2019 null path.' },
      { id: 'c8', label: 'Migration rollback', status: 'pass', note: 'Unchanged.' },
      { id: 'c9', label: 'Legacy risk', status: 'warn', note: 'Still MEDIUM — the parameter must be made required in a follow-up, tracked as TASK-524.' },
      { id: 'c10', label: 'Security', status: 'pass', note: 'Unchanged.' },
    ],
    findings: [],
  },
  {
    id: 'rv3', ref: 'REV-2608', taskRef: 'TASK-488', projectId: 'erp', reviewer: 'Code Reviewer', verdict: 'changes_requested',
    round: 1, createdAt: 'yesterday 15:22', filesChanged: 11, additions: 402, deletions: 38,
    reviewerNote: 'A hand-rolled CSV parser is reinventing a solved problem, and it already mishandles quoted newlines. Ask for the dependency instead.',
    checks: [
      { id: 'c1', label: 'SOLID', status: 'pass', note: 'Import service does one thing.' },
      { id: 'c2', label: 'Naming convention', status: 'pass', note: 'Clean.' },
      { id: 'c3', label: 'Existing architecture', status: 'pass', note: 'Follows ADR-49.' },
      { id: 'c4', label: 'Repository pattern', status: 'pass', note: 'Staging table accessed through a repository.' },
      { id: 'c5', label: 'Null handling', status: 'pass', note: 'Empty cells handled explicitly.' },
      { id: 'c6', label: 'Error handling', status: 'warn', note: 'Row-level errors collected but not capped — a 50k-row garbage file builds a 50k-entry array.' },
      { id: 'c7', label: 'Tests', status: 'fail', note: 'No test for a quoted field containing a newline.' },
      { id: 'c8', label: 'Migration rollback', status: 'pass', note: '0043 verified.' },
      { id: 'c9', label: 'Legacy risk', status: 'pass', note: 'New surface, nothing legacy touched.' },
      { id: 'c10', label: 'Security', status: 'warn', note: 'CSV formula injection is neutralised on write but not on the error preview.' },
    ],
    findings: [
      { id: 'g1', severity: 'blocker', file: 'web/src/modules/customer/csvParse.ts', line: 22, rule: 'reinvention', agent: 'Code Reviewer',
        message: 'Hand-rolled parser splits on commas and breaks on a quoted field containing a newline — which appears in 3 of the 5 sample files.',
        suggestion: 'Request papaparse@5.4.1 through the approval queue (APPR-120). Two days of parser work is worse than one dependency.' },
      { id: 'g2', severity: 'major', file: 'server/Careworks.Erp.Customer/Services/CustomerImportService.cs', line: 96, rule: 'resource bound', agent: 'Security Engineer',
        message: 'Row-error collection is unbounded.', suggestion: 'Cap at the first 500 errors and report the remainder as a count.' },
    ],
  },
];
