import type { CodeNode, SymbolDetail } from '@/types';

export const repoTree: CodeNode[] = [
  {
    id: 'fe', name: 'Frontend', path: 'web/', kind: 'folder', lang: 'TypeScript', loc: 486_200, children: [
      { id: 'fe-inv', name: 'invoice', path: 'web/src/modules/invoice/', kind: 'folder', children: [
        { id: 'sym-invsum', name: 'InvoiceTaxSummary.tsx', path: 'web/src/modules/invoice/InvoiceTaxSummary.tsx', kind: 'component', lang: 'TSX', loc: 214, risk: 'MEDIUM', lastChanged: '18 min ago', owner: 'frontend' },
        { id: 'f2', name: 'InvoiceForm.tsx', path: 'web/src/modules/invoice/InvoiceForm.tsx', kind: 'component', lang: 'TSX', loc: 612, risk: 'MEDIUM', lastChanged: '3 d ago', owner: 'frontend' },
        { id: 'f3', name: 'useInvoiceTotals.ts', path: 'web/src/modules/invoice/useInvoiceTotals.ts', kind: 'file', lang: 'TS', loc: 148, risk: 'HIGH', lastChanged: '18 min ago', owner: 'frontend' },
      ]},
      { id: 'fe-cust', name: 'customer', path: 'web/src/modules/customer/', kind: 'folder', children: [
        { id: 'f4', name: 'BulkUploadDialog.tsx', path: 'web/src/modules/customer/BulkUploadDialog.tsx', kind: 'component', lang: 'TSX', loc: 340, risk: 'LOW', lastChanged: '25 min ago', owner: 'frontend' },
        { id: 'f5', name: 'UploadErrorDialog.tsx', path: 'web/src/modules/customer/UploadErrorDialog.tsx', kind: 'component', lang: 'TSX', loc: 176, risk: 'LOW', lastChanged: '1 h ago', owner: 'frontend' },
      ]},
      { id: 'fe-rep', name: 'reports', path: 'web/src/modules/reports/', kind: 'folder', children: [
        { id: 'sym-excel', name: 'ExcelExport.ts', path: 'web/src/modules/reports/ExcelExport.ts', kind: 'file', lang: 'TS', loc: 288, risk: 'MEDIUM', lastChanged: '2 d ago', owner: 'frontend' },
        { id: 'f7', name: 'ReportGrid.tsx', path: 'web/src/modules/reports/ReportGrid.tsx', kind: 'component', lang: 'TSX', loc: 704, risk: 'LOW', lastChanged: '25 min ago', owner: 'frontend' },
      ]},
    ],
  },
  {
    id: 'be', name: 'Backend', path: 'server/', kind: 'folder', lang: 'C#', loc: 1_310_400, children: [
      { id: 'be-bill', name: 'Careworks.Erp.Billing', path: 'server/Careworks.Erp.Billing/', kind: 'folder', children: [
        { id: 'sym-invsvc', name: 'InvoiceService.cs', path: 'server/Careworks.Erp.Billing/Services/InvoiceService.cs', kind: 'class', lang: 'C#', loc: 412, risk: 'HIGH', lastChanged: '4 min ago', owner: 'backend' },
        { id: 'b2', name: 'InvoiceController.cs', path: 'server/Careworks.Erp.Billing/Controllers/InvoiceController.cs', kind: 'class', lang: 'C#', loc: 198, risk: 'LOW', lastChanged: '3 w ago', owner: 'backend' },
        { id: 'b3', name: 'InvoiceLineMapper.cs', path: 'server/Careworks.Erp.Billing/Mappers/InvoiceLineMapper.cs', kind: 'class', lang: 'C#', loc: 122, risk: 'MEDIUM', lastChanged: '12 min ago', owner: 'backend' },
      ]},
      { id: 'be-tax', name: 'Careworks.Erp.Tax', path: 'server/Careworks.Erp.Tax/', kind: 'folder', children: [
        { id: 'sym-taxsvc', name: 'TaxService.cs', path: 'server/Careworks.Erp.Tax/Services/TaxService.cs', kind: 'class', lang: 'C#', loc: 286, risk: 'CRITICAL', lastChanged: '2 min ago', owner: 'backend' },
        { id: 'sym-resolver', name: 'ITaxJurisdictionResolver.cs', path: 'server/Careworks.Erp.Tax/Contracts/ITaxJurisdictionResolver.cs', kind: 'class', lang: 'C#', loc: 34, risk: 'LOW', lastChanged: '9 min ago', owner: 'backend' },
        { id: 'b6', name: 'TaxSlabRepository.cs', path: 'server/Careworks.Erp.Tax/Repositories/TaxSlabRepository.cs', kind: 'class', lang: 'C#', loc: 164, risk: 'MEDIUM', lastChanged: '6 d ago', owner: 'backend' },
      ]},
      { id: 'be-auth', name: 'Careworks.Erp.Auth', path: 'server/Careworks.Erp.Auth/', kind: 'folder', children: [
        { id: 'sym-session', name: 'SessionProvider.cs', path: 'server/Careworks.Erp.Auth/SessionProvider.cs', kind: 'class', lang: 'C#', loc: 240, risk: 'HIGH', lastChanged: '5 h ago', owner: 'backend' },
      ]},
      { id: 'be-gl', name: 'Careworks.Erp.Gl', path: 'server/Careworks.Erp.Gl/', kind: 'folder', children: [
        { id: 'sym-posting', name: 'PostingEngine.cs', path: 'server/Careworks.Erp.Gl/PostingEngine.cs', kind: 'class', lang: 'C#', loc: 918, risk: 'CRITICAL', lastChanged: '4 mo ago', owner: 'backend' },
      ]},
    ],
  },
  {
    id: 'db', name: 'Database', path: 'db/', kind: 'folder', lang: 'T-SQL', loc: 512_800, children: [
      { id: 'db-proc', name: 'procedures', path: 'db/procedures/', kind: 'folder', children: [
        { id: 'sym-sp-tax', name: 'SP_CalculateTax.sql', path: 'db/procedures/SP_CalculateTax.sql', kind: 'sproc', lang: 'T-SQL', loc: 412, risk: 'CRITICAL', lastChanged: '7 min ago', owner: 'database' },
        { id: 'd2', name: 'SP_GetInvoice.sql', path: 'db/procedures/SP_GetInvoice.sql', kind: 'sproc', lang: 'T-SQL', loc: 188, risk: 'LOW', lastChanged: '5 mo ago', owner: 'database' },
        { id: 'd3', name: 'SP_CommitCustomerImport.sql', path: 'db/procedures/SP_CommitCustomerImport.sql', kind: 'sproc', lang: 'T-SQL', loc: 96, risk: 'MEDIUM', lastChanged: 'yesterday', owner: 'database' },
        { id: 'd4', name: 'SP_ReconcileBankStatement.sql', path: 'db/procedures/SP_ReconcileBankStatement.sql', kind: 'sproc', lang: 'T-SQL', loc: 604, risk: 'HIGH', lastChanged: '6 d ago', owner: 'database' },
      ]},
      { id: 'db-tab', name: 'tables', path: 'db/tables/', kind: 'folder', children: [
        { id: 'sym-msttax', name: 'MST_TAX', path: 'db/tables/MST_TAX.sql', kind: 'table', lang: 'T-SQL', loc: 42, risk: 'HIGH', lastChanged: '3 h ago', owner: 'database' },
        { id: 'd6', name: 'TRANS_INVOICE', path: 'db/tables/TRANS_INVOICE.sql', kind: 'table', lang: 'T-SQL', loc: 68, risk: 'CRITICAL', lastChanged: '8 mo ago', owner: 'database' },
        { id: 'd7', name: 'MST_STATE', path: 'db/tables/MST_STATE.sql', kind: 'table', lang: 'T-SQL', loc: 18, risk: 'LOW', lastChanged: '2 y ago', owner: 'database' },
      ]},
      { id: 'db-mig', name: 'migrations', path: 'db/migrations/', kind: 'folder', children: [
        { id: 'd8', name: '0042_backfill_tax_jurisdiction.sql', path: 'db/migrations/0042_backfill_tax_jurisdiction.sql', kind: 'file', lang: 'T-SQL', loc: 74, risk: 'HIGH', lastChanged: '22 min ago', owner: 'database' },
        { id: 'd9', name: '0043_stg_customer_import.sql', path: 'db/migrations/0043_stg_customer_import.sql', kind: 'file', lang: 'T-SQL', loc: 58, risk: 'MEDIUM', lastChanged: 'yesterday', owner: 'database' },
      ]},
    ],
  },
  {
    id: 'inf', name: 'Infrastructure', path: 'deploy/', kind: 'folder', lang: 'YAML', loc: 18_400, children: [
      { id: 'i1', name: 'iis/apppool-recycle.ps1', path: 'deploy/iis/apppool-recycle.ps1', kind: 'file', lang: 'PowerShell', loc: 64, risk: 'HIGH', lastChanged: '5 h ago', owner: 'devops' },
      { id: 'i2', name: 'docker/redis.yml', path: 'deploy/docker/redis.yml', kind: 'file', lang: 'YAML', loc: 32, risk: 'MEDIUM', lastChanged: '5 h ago', owner: 'devops' },
      { id: 'i3', name: 'ci/build.yml', path: 'deploy/ci/build.yml', kind: 'file', lang: 'YAML', loc: 148, risk: 'LOW', lastChanged: '2 w ago', owner: 'devops' },
    ],
  },
  {
    id: 'tests', name: 'Tests', path: 'tests/', kind: 'folder', lang: 'C#', loc: 214_600, children: [
      { id: 'sym-taxtest', name: 'JurisdictionTests.cs', path: 'tests/Careworks.Erp.Tax.Tests/JurisdictionTests.cs', kind: 'class', lang: 'C#', loc: 322, risk: 'LOW', lastChanged: '6 min ago', owner: 'qa' },
      { id: 't2', name: 'InvoiceTaxLegacyTest.cs', path: 'tests/Careworks.Erp.Billing.Tests/InvoiceTaxLegacyTest.cs', kind: 'class', lang: 'C#', loc: 148, risk: 'MEDIUM', lastChanged: '11 mo ago', owner: 'qa' },
      { id: 't3', name: 'BulkImportE2E.spec.ts', path: 'tests/e2e/BulkImportE2E.spec.ts', kind: 'file', lang: 'TS', loc: 210, risk: 'LOW', lastChanged: '2 h ago', owner: 'qa' },
    ],
  },
];

export const symbols: Record<string, SymbolDetail> = {
  'sym-invsvc': {
    id: 'sym-invsvc', name: 'InvoiceService', path: 'server/Careworks.Erp.Billing/Services/InvoiceService.cs',
    kind: 'class · C#', loc: 412, complexity: 74, churn: 28, risk: 'HIGH', lastChanged: '4 min ago',
    summary: 'Assembles an invoice from a sales order: resolves the customer, expands lines, calls TaxService for each line, applies discounts, then persists through IInvoiceRepository. It still calls SP_CalculateTax directly for the header total — the last direct procedure call left in the billing module, and the reason TASK-492 exists.',
    dependencies: ['ITaxService', 'ICustomerService', 'IInvoiceRepository', 'InvoiceLineMapper', 'IClock'],
    usedBy: ['InvoiceController', 'OrderService', 'ReportingService', 'CreditNoteService', 'BulkInvoiceJob'],
    database: ['TRANS_INVOICE', 'TRANS_INVOICE_LINE', 'MST_CUSTOMER'],
    storedProcedures: ['SP_CalculateTax', 'SP_GetInvoice', 'SP_CommitInvoice'],
    tests: ['InvoiceServiceTests (34)', 'InvoiceTaxLegacyTest (6, 1 expected-fail)', 'BulkInvoiceJobTests (11)'],
    knownBugs: ['BUG-883 — CGST/SGST reversed on interstate orders', 'BUG-742 — credit note reuses the original invoice date (closed)'],
    decisions: ['ADR-52 (proposed) — single jurisdiction resolver', 'ADR-47 — money rounds half-away-from-zero', 'MEM-142 — TRANS_* are append-only'],
  },
  'sym-taxsvc': {
    id: 'sym-taxsvc', name: 'TaxService', path: 'server/Careworks.Erp.Tax/Services/TaxService.cs',
    kind: 'class · C#', loc: 286, complexity: 81, churn: 34, risk: 'CRITICAL', lastChanged: '2 min ago',
    summary: 'Resolves the applicable tax heads and rates for an invoice line. Being reshaped by TASK-492 to expose ResolveJurisdiction as the single source of place-of-supply truth. Four modules depend on its current signature, which is why the reviewer flagged the change as legacy-risk.',
    dependencies: ['ITaxSlabRepository', 'ITaxJurisdictionResolver', 'IStateRepository'],
    usedBy: ['InvoiceService', 'CreditNoteService', 'PurchaseService', 'DispatchService'],
    database: ['MST_TAX', 'MST_TAX_SLAB', 'MST_STATE', 'MST_TAX_LINE'],
    storedProcedures: ['SP_CalculateTax', 'SP_GetTaxSlab'],
    tests: ['JurisdictionTests (18)', 'TaxServiceTests (41)', 'SezMatrixTests (9)'],
    knownBugs: ['BUG-883 — jurisdiction derived from billing state instead of place of supply'],
    decisions: ['ADR-52 (proposed)', 'ADR-51 — procedures are renamed, never dropped'],
  },
  'sym-sp-tax': {
    id: 'sym-sp-tax', name: 'SP_CalculateTax', path: 'db/procedures/SP_CalculateTax.sql',
    kind: 'stored procedure · T-SQL', loc: 412, complexity: 91, churn: 34, risk: 'CRITICAL', lastChanged: '7 min ago',
    summary: '412 lines with nine nested branches. Derives the tax jurisdiction twice — at L288 from the billing address and again at L341 from the sales order — and the two disagree for SEZ and union-territory supplies. Eleven callers depend on its current signature.',
    dependencies: ['MST_TAX', 'MST_TAX_SLAB', 'MST_STATE', 'fn_GetFinancialYear'],
    usedBy: ['TaxService', 'InvoiceService', 'SP_CommitInvoice', 'SP_PostCreditNote', 'ReportingService', 'SP_RecomputeInvoice'],
    database: ['MST_TAX', 'MST_TAX_SLAB', 'MST_STATE'],
    storedProcedures: ['SP_GetTaxSlab', 'fn_GetFinancialYear'],
    tests: ['SP_CalculateTax_Contract (12)', 'SezMatrixTests (9)'],
    knownBugs: ['BUG-883 — double derivation of jurisdiction', 'BUG-601 — financial-year boundary off by one day (closed)'],
    decisions: ['ADR-51 — deprecation policy', 'ADR-52 (proposed) — stop re-deriving jurisdiction'],
  },
  'sym-msttax': {
    id: 'sym-msttax', name: 'MST_TAX', path: 'db/tables/MST_TAX.sql',
    kind: 'table · 2.1M rows', loc: 42, complexity: 24, churn: 6, risk: 'HIGH', lastChanged: '3 h ago',
    summary: 'Master tax configuration, effective-dated. Every row carries a validity window, so any query without an effective-date predicate silently reads superseded rates — a mistake made three times in the last two years.',
    dependencies: [],
    usedBy: ['SP_CalculateTax', 'SP_GetTaxSlab', 'TaxSlabRepository', 'ReportingService'],
    database: ['MST_TAX_SLAB (FK)', 'MST_STATE (FK)'],
    storedProcedures: ['SP_CalculateTax', 'SP_GetTaxSlab'],
    tests: ['TaxMasterContractTests (7)'],
    knownBugs: ['BUG-518 — reports read superseded rates when the date predicate is omitted (closed)'],
    decisions: ['MEM-209 — every MST_TAX read needs an effective-date predicate'],
  },
  'sym-session': {
    id: 'sym-session', name: 'SessionProvider', path: 'server/Careworks.Erp.Auth/SessionProvider.cs',
    kind: 'class · C#', loc: 240, complexity: 54, churn: 12, risk: 'HIGH', lastChanged: '5 h ago',
    summary: 'Wraps ASP.NET session state. Configured in-proc on all three IIS nodes with no affinity at the load balancer, so every app-pool recycle drops every session on that node — the cause of TASK-501.',
    dependencies: ['IHttpContextAccessor', 'IUserRepository'],
    usedBy: ['AuthController', 'AuthorizationMiddleware', 'AuditInterceptor'],
    database: ['MST_USER', 'TRANS_AUDIT'],
    storedProcedures: ['SP_GetUserClaims'],
    tests: ['SessionProviderTests (14)'],
    knownBugs: ['BUG-905 — users dropped ~20 minutes after sign-in'],
    decisions: ['ADR-50 (proposed) — Redis-backed session state'],
  },
  'sym-posting': {
    id: 'sym-posting', name: 'PostingEngine', path: 'server/Careworks.Erp.Gl/PostingEngine.cs',
    kind: 'class · C#', loc: 918, complexity: 88, churn: 3, risk: 'CRITICAL', lastChanged: '4 mo ago',
    summary: 'Double-entry posting for the general ledger. The balance invariant is enforced in T-SQL rather than here, and no test asserts the ledger balances after a partial failure. Almost nobody touches it, which is exactly why it is dangerous.',
    dependencies: ['ILedgerRepository', 'IPeriodService', 'ITransactionScope'],
    usedBy: ['InvoiceService', 'PaymentService', 'PayrollService', 'BankReconciliationService'],
    database: ['TRANS_LEDGER', 'TRANS_LEDGER_LINE', 'MST_ACCOUNT'],
    storedProcedures: ['SP_PostLedger', 'SP_ClosePeriod'],
    tests: ['PostingEngineTests (22)'],
    knownBugs: ['BUG-311 — partial failure can leave an unbalanced batch (open, 4 months)'],
    decisions: ['ADR-44 — audit capture by trigger for legacy tables'],
  },
  'sym-invsum': {
    id: 'sym-invsum', name: 'InvoiceTaxSummary', path: 'web/src/modules/invoice/InvoiceTaxSummary.tsx',
    kind: 'component · TSX', loc: 214, complexity: 31, churn: 19, risk: 'MEDIUM', lastChanged: '18 min ago',
    summary: 'Renders the tax breakdown block on an invoice. Currently assumes a CGST/SGST pair always exists; an interstate invoice must show a single IGST head instead, which is step 5 of PLAN-492.',
    dependencies: ['useInvoiceTotals', 'formatMoney', 'TaxHeadRow'],
    usedBy: ['InvoiceForm', 'InvoicePrintPreview', 'CreditNoteForm'],
    database: [],
    storedProcedures: [],
    tests: ['InvoiceTaxSummary.spec.tsx (9)', 'invoice-print.spec.ts (E2E, 4)'],
    knownBugs: ['BUG-883 — displays two heads where one is correct'],
    decisions: ['ADR-48 — new DataGrid is the single grid primitive'],
  },
  'sym-excel': {
    id: 'sym-excel', name: 'ExcelExport', path: 'web/src/modules/reports/ExcelExport.ts',
    kind: 'module · TS', loc: 288, complexity: 36, churn: 11, risk: 'MEDIUM', lastChanged: '2 d ago',
    summary: 'Builds an XLSX in the browser by buffering the entire result set in memory. Fine to about 60k rows; past roughly 100k the tab stalls and the request times out. TASK-513 will move it to a streaming writer.',
    dependencies: ['xlsx', 'useReportQuery', 'formatMoney'],
    usedBy: ['ReportGrid', 'InvoiceRegister', 'StockLedgerReport'],
    database: [],
    storedProcedures: ['SP_GetInvoiceRegister'],
    tests: ['ExcelExport.spec.ts (6)'],
    knownBugs: ['BUG-991 — export times out past ~100k rows'],
    decisions: [],
  },
  'sym-resolver': {
    id: 'sym-resolver', name: 'ITaxJurisdictionResolver', path: 'server/Careworks.Erp.Tax/Contracts/ITaxJurisdictionResolver.cs',
    kind: 'interface · C#', loc: 34, complexity: 4, churn: 1, risk: 'LOW', lastChanged: '9 min ago',
    summary: 'New contract introduced by TASK-492. One method: resolve the place of supply for a sales order into a jurisdiction. Written interface-first per the project rules, before any implementation existed.',
    dependencies: [],
    usedBy: ['TaxService', 'InvoiceService'],
    database: [],
    storedProcedures: [],
    tests: ['JurisdictionTests (18)'],
    decisions: ['ADR-52 (proposed)', 'Project rule r5 — interface-first for every new service'],
    knownBugs: [],
  },
  'sym-taxtest': {
    id: 'sym-taxtest', name: 'JurisdictionTests', path: 'tests/Careworks.Erp.Tax.Tests/JurisdictionTests.cs',
    kind: 'test class · C#', loc: 322, complexity: 18, churn: 22, risk: 'LOW', lastChanged: '6 min ago',
    summary: 'The interstate matrix written for TASK-492: intrastate, interstate, SEZ, export and union territory. Eighteen cases, all currently green — including three that were red until the SEZ branch was routed through the resolver.',
    dependencies: ['TaxService', 'ITaxJurisdictionResolver', 'FakeSlabRepository'],
    usedBy: [],
    database: [],
    storedProcedures: [],
    tests: [],
    knownBugs: [],
    decisions: ['ADR-52 (proposed)'],
  },
};

export const indexStatus = {
  parser: 'Tree-sitter 0.24 + OmniSharp LSP + git log',
  filesParsed: 18_442,
  symbolsIndexed: 214_806,
  callEdges: 641_290,
  languages: ['C#', 'T-SQL', 'TypeScript', 'TSX', 'PowerShell', 'YAML'],
  lastFullIndex: 'today 07:02 · 12 min 04 s',
  incremental: 'on save · median 340 ms',
  unresolved: 96,
};

export const questions = [
  { id: 'q1', label: 'Who calls this?', pane: 'usedBy' as const },
  { id: 'q2', label: 'What breaks if I change it?', pane: 'impact' as const },
  { id: 'q3', label: 'What is the database side?', pane: 'database' as const },
  { id: 'q4', label: 'Which tests cover this?', pane: 'tests' as const },
  { id: 'q5', label: 'What went wrong here before?', pane: 'bugs' as const },
];
