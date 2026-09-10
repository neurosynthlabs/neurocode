import type { GraphNode, GraphEdge, ImpactReport } from '@/types';

/* Layered layout: ui(0) → api(1) → service(2) → repo(3) → sproc(4) → table(5) */
export const nodes: GraphNode[] = [
  { id: 'ui-invoice',  label: 'InvoiceForm',        layer: 'ui',       risk: 'LOW',      x: 90,  y: 60 },
  { id: 'ui-taxsum',   label: 'InvoiceTaxSummary',  layer: 'ui',       risk: 'MEDIUM',   x: 300, y: 60 },
  { id: 'ui-reports',  label: 'ReportGrid',         layer: 'ui',       risk: 'LOW',      x: 510, y: 60 },
  { id: 'ui-dispatch', label: 'DispatchPanel',      layer: 'ui',       risk: 'LOW',      x: 720, y: 60 },

  { id: 'api-invoice', label: 'InvoiceController',  layer: 'api',      risk: 'LOW',      x: 90,  y: 160 },
  { id: 'api-order',   label: 'OrderController',    layer: 'api',      risk: 'LOW',      x: 300, y: 160 },
  { id: 'api-report',  label: 'ReportController',   layer: 'api',      risk: 'LOW',      x: 510, y: 160 },
  { id: 'api-dispatch',label: 'DispatchController', layer: 'api',      risk: 'LOW',      x: 720, y: 160 },

  { id: 'svc-invoice', label: 'InvoiceService',     layer: 'service',  risk: 'HIGH',     x: 90,  y: 268 },
  { id: 'svc-tax',     label: 'TaxService',         layer: 'service',  risk: 'CRITICAL', x: 300, y: 268 },
  { id: 'svc-customer',label: 'CustomerService',    layer: 'service',  risk: 'LOW',      x: 510, y: 268 },
  { id: 'svc-report',  label: 'ReportingService',   layer: 'service',  risk: 'MEDIUM',   x: 660, y: 268 },
  { id: 'svc-dispatch',label: 'DispatchService',    layer: 'service',  risk: 'MEDIUM',   x: 820, y: 268 },
  { id: 'svc-credit',  label: 'CreditNoteService',  layer: 'service',  risk: 'MEDIUM',   x: 190, y: 340 },

  { id: 'repo-invoice',label: 'InvoiceRepository',  layer: 'repo',     risk: 'LOW',      x: 90,  y: 420 },
  { id: 'repo-tax',    label: 'TaxSlabRepository',  layer: 'repo',     risk: 'MEDIUM',   x: 300, y: 420 },
  { id: 'repo-cust',   label: 'CustomerRepository', layer: 'repo',     risk: 'LOW',      x: 510, y: 420 },
  { id: 'resolver',    label: 'ITaxJurisdictionResolver', layer: 'service', risk: 'LOW', x: 470, y: 340 },

  { id: 'sp-tax',      label: 'SP_CalculateTax',    layer: 'sproc',    risk: 'CRITICAL', x: 300, y: 512 },
  { id: 'sp-invoice',  label: 'SP_CommitInvoice',   layer: 'sproc',    risk: 'HIGH',     x: 90,  y: 512 },
  { id: 'sp-slab',     label: 'SP_GetTaxSlab',      layer: 'sproc',    risk: 'MEDIUM',   x: 510, y: 512 },

  { id: 'tbl-tax',     label: 'MST_TAX',            layer: 'table',    risk: 'HIGH',     x: 300, y: 604 },
  { id: 'tbl-slab',    label: 'MST_TAX_SLAB',       layer: 'table',    risk: 'MEDIUM',   x: 470, y: 604 },
  { id: 'tbl-state',   label: 'MST_STATE',          layer: 'table',    risk: 'LOW',      x: 640, y: 604 },
  { id: 'tbl-invoice', label: 'TRANS_INVOICE',      layer: 'table',    risk: 'CRITICAL', x: 90,  y: 604 },
  { id: 'ext-eway',    label: 'E-Way Bill API',     layer: 'external', risk: 'MEDIUM',   x: 820, y: 420 },
];

export const edges: GraphEdge[] = [
  { from: 'ui-invoice', to: 'api-invoice', kind: 'calls' },
  { from: 'ui-taxsum', to: 'api-invoice', kind: 'calls' },
  { from: 'ui-reports', to: 'api-report', kind: 'calls' },
  { from: 'ui-dispatch', to: 'api-dispatch', kind: 'calls' },
  { from: 'api-invoice', to: 'svc-invoice', kind: 'calls' },
  { from: 'api-order', to: 'svc-invoice', kind: 'calls' },
  { from: 'api-report', to: 'svc-report', kind: 'calls' },
  { from: 'api-dispatch', to: 'svc-dispatch', kind: 'calls' },
  { from: 'svc-invoice', to: 'svc-tax', kind: 'depends' },
  { from: 'svc-invoice', to: 'svc-customer', kind: 'depends' },
  { from: 'svc-invoice', to: 'repo-invoice', kind: 'depends' },
  { from: 'svc-credit', to: 'svc-tax', kind: 'depends' },
  { from: 'svc-report', to: 'svc-tax', kind: 'depends' },
  { from: 'svc-dispatch', to: 'svc-tax', kind: 'depends' },
  { from: 'svc-dispatch', to: 'ext-eway', kind: 'calls' },
  { from: 'svc-tax', to: 'resolver', kind: 'depends' },
  { from: 'svc-tax', to: 'repo-tax', kind: 'depends' },
  { from: 'svc-customer', to: 'repo-cust', kind: 'depends' },
  { from: 'svc-invoice', to: 'sp-tax', kind: 'calls' },
  { from: 'svc-tax', to: 'sp-tax', kind: 'calls' },
  { from: 'repo-invoice', to: 'sp-invoice', kind: 'calls' },
  { from: 'repo-tax', to: 'sp-slab', kind: 'calls' },
  { from: 'sp-tax', to: 'tbl-tax', kind: 'reads' },
  { from: 'sp-tax', to: 'tbl-slab', kind: 'reads' },
  { from: 'sp-tax', to: 'tbl-state', kind: 'reads' },
  { from: 'sp-slab', to: 'tbl-slab', kind: 'reads' },
  { from: 'sp-invoice', to: 'tbl-invoice', kind: 'writes' },
  { from: 'repo-cust', to: 'tbl-invoice', kind: 'reads' },
];

export const impacts: Record<string, ImpactReport> = {
  'svc-tax': {
    target: 'TaxService',
    risk: 'HIGH', confidence: 91,
    modules: 7, apis: 12, tests: 18, legacyDeps: 2,
    blastRadius: [
      { label: 'Services that call it', items: ['InvoiceService', 'CreditNoteService', 'ReportingService', 'DispatchService'] },
      { label: 'API surface affected', items: ['POST /invoices', 'PUT /invoices/{id}', 'POST /credit-notes', 'GET /reports/tax-register', 'POST /dispatch/eway'] },
      { label: 'Database objects', items: ['MST_TAX', 'MST_TAX_SLAB', 'MST_STATE', 'MST_TAX_LINE'] },
      { label: 'Stored procedures', items: ['SP_CalculateTax (11 callers)', 'SP_GetTaxSlab'] },
      { label: 'Test suites', items: ['JurisdictionTests (18)', 'TaxServiceTests (41)', 'SezMatrixTests (9)', 'InvoiceTaxLegacyTest (6)'] },
      { label: 'Legacy contracts at risk', items: ['ADR-47 — half-away-from-zero rounding', 'MEM-142 — TRANS_* are append-only'] },
    ],
    warnings: [
      'SP_CalculateTax has 11 callers and its signature changes. All 11 must move in the same worktree or the build breaks at deploy, not at compile.',
      'DispatchService feeds the E-Way Bill API, which is an external contract — a wrong tax head there is a statutory filing error, not a display bug.',
      'InvoiceTaxLegacyTest is expected to stay red. If it turns green, the legacy rounding contract has silently changed.',
    ],
    recommendation:
      'Proceed, but under three independent verification passes rather than one: correctness, legacy-contract, and statutory-filing. Ship the resolver first as an additive interface, migrate callers one at a time, and only then delete the duplicate derivation in SP_CalculateTax.',
  },
  'svc-invoice': {
    target: 'InvoiceService',
    risk: 'HIGH', confidence: 88,
    modules: 5, apis: 9, tests: 51, legacyDeps: 2,
    blastRadius: [
      { label: 'Callers', items: ['InvoiceController', 'OrderService', 'ReportingService', 'CreditNoteService', 'BulkInvoiceJob'] },
      { label: 'API surface affected', items: ['POST /invoices', 'GET /invoices/{id}', 'PUT /invoices/{id}', 'POST /orders/{id}/invoice'] },
      { label: 'Database objects', items: ['TRANS_INVOICE', 'TRANS_INVOICE_LINE', 'MST_CUSTOMER'] },
      { label: 'Test suites', items: ['InvoiceServiceTests (34)', 'InvoiceTaxLegacyTest (6)', 'BulkInvoiceJobTests (11)'] },
      { label: 'Legacy contracts at risk', items: ['MEM-142 — TRANS_INVOICE is append-only', 'ADR-47 — money rounding'] },
    ],
    warnings: [
      'It is the last place in the billing module that calls a stored procedure directly. Removing that call is the point of TASK-492 — do not accidentally add a second one.',
      'BulkInvoiceJob runs nightly and is not covered by the E2E suite.',
    ],
    recommendation:
      'Safe to change behind the existing tests, provided the direct SP_CalculateTax call is replaced rather than duplicated. Add a nightly-job smoke test before merging — it is the only uncovered caller.',
  },
  'sp-tax': {
    target: 'SP_CalculateTax',
    risk: 'CRITICAL', confidence: 84,
    modules: 9, apis: 14, tests: 21, legacyDeps: 4,
    blastRadius: [
      { label: 'Direct callers', items: ['TaxService', 'InvoiceService', 'SP_CommitInvoice', 'SP_PostCreditNote', 'SP_RecomputeInvoice', 'ReportingService'] },
      { label: 'Downstream reports', items: ['GST tax register', 'Sales register', 'Monthly filing extract'] },
      { label: 'Tables read', items: ['MST_TAX', 'MST_TAX_SLAB', 'MST_STATE'] },
      { label: 'Legacy contracts at risk', items: ['ADR-47 rounding', 'ADR-51 no-drop policy', 'Statutory filing format', 'MEM-209 effective-date predicate'] },
    ],
    warnings: [
      'A signature change on a procedure with 11 callers cannot be deployed incrementally against a single database. Either every caller ships together, or the procedure keeps a defaulted parameter for one release.',
      'The monthly filing extract reads this procedure directly. A wrong result is a statutory error with a penalty attached.',
      'Permission rule p10 denies DROP outright — the old branch is renamed, never removed.',
    ],
    recommendation:
      'Do not change the signature in place. Add the jurisdiction as an optional parameter that defaults to the current derivation, migrate all 11 callers, verify the filing extract byte-for-byte against last month, and only then make the parameter required.',
  },
  'tbl-invoice': {
    target: 'TRANS_INVOICE',
    risk: 'CRITICAL', confidence: 96,
    modules: 12, apis: 18, tests: 64, legacyDeps: 5,
    blastRadius: [
      { label: 'Writers', items: ['SP_CommitInvoice (only sanctioned writer)'] },
      { label: 'Readers', items: ['InvoiceRepository', 'ReportingService', 'PostingEngine', 'BankReconciliationService', 'DispatchService'] },
      { label: 'Triggers', items: ['TRG_Audit_TRANS_INVOICE'] },
      { label: 'Legacy contracts at risk', items: ['MEM-142 append-only', 'GL reconciliation', 'Audit trail immutability', 'Statutory retention', 'ADR-44 trigger-based audit'] },
    ],
    warnings: [
      'No agent may write this table directly. MEM-142 is enforced by a PreToolUse hook, not by convention.',
      'Any column addition silently widens the audit row through TRG_Audit_TRANS_INVOICE — 38 tables share that pattern.',
      'The GL reconciles against this table monthly. An in-place amendment reopens a closed period.',
    ],
    recommendation:
      'Corrections go through a credit note and a re-raise, never an UPDATE. If a backfill is genuinely required, it runs through SP_CommitTaxCorrection with a human approval and a verified rollback — which is exactly what APPR-119 is asking for.',
  },
  'svc-dispatch': {
    target: 'DispatchService',
    risk: 'MEDIUM', confidence: 79,
    modules: 3, apis: 4, tests: 12, legacyDeps: 1,
    blastRadius: [
      { label: 'Callers', items: ['DispatchController', 'BulkDispatchJob'] },
      { label: 'External contracts', items: ['E-Way Bill API (government)'] },
      { label: 'Database objects', items: ['TRANS_DISPATCH', 'MST_TAX'] },
      { label: 'Test suites', items: ['DispatchServiceTests (12)'] },
    ],
    warnings: [
      'The retry wrapper added after the March contract change has never been load-tested.',
      'The E-Way Bill API is an external government contract — its failure modes are not under your control.',
    ],
    recommendation:
      'Low blast radius internally, but the external dependency makes failures visible to regulators. Add a contract test against the sandbox endpoint before touching the tax path here.',
  },
};

export const layers = [
  { id: 'ui',       label: 'UI',                 note: 'React components' },
  { id: 'api',      label: 'API',                note: 'ASP.NET controllers' },
  { id: 'service',  label: 'Service',            note: 'Business logic' },
  { id: 'repo',     label: 'Repository',         note: 'Data access' },
  { id: 'sproc',    label: 'Stored procedure',   note: 'T-SQL' },
  { id: 'table',    label: 'Table',              note: 'SQL Server' },
  { id: 'external', label: 'External',           note: 'Third-party contracts' },
] as const;

export const recentlyChanged = new Set(['svc-tax', 'svc-invoice', 'sp-tax', 'ui-taxsum', 'resolver', 'tbl-tax']);
