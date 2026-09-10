/* ═══════════════════════════════════════════════════════════════
   MODULE MAP + project-level knowledge (rules, ADRs, risky areas,
   infra, onboarding pipeline, global brain).
   Used by /projects and /projects/:projectId.
   ═══════════════════════════════════════════════════════════════ */
import type { Risk } from '@/types';

export type Renewal = 'legacy' | 'in-renewal' | 'renewed';

export interface ModuleRow {
  id: string;
  name: string;
  path: string;
  loc: number;
  files: number;
  lastTouched: string;
  risk: Risk;
  coverage: number;
  understood: number;
  owner: string;          // agent id from @/mock/agents
  renewal: Renewal;
  tables: string[];
  sprocs: number;
  openBugs: number;
  note: string;
}

/* ── ERP — the 14-year-old accounting + inventory system ───────── */
const erp: ModuleRow[] = [
  {
    id: 'erp-billing', name: 'Billing', path: 'server/Careworks.Erp.Billing/',
    loc: 214_880, files: 318, lastTouched: '2 min ago', risk: 'HIGH', coverage: 71, understood: 93,
    owner: 'backend', renewal: 'in-renewal', tables: ['TRANS_INVOICE', 'TRANS_INVOICE_LINE', 'MST_TAX_LINE'], sprocs: 74, openBugs: 6,
    note: 'InvoiceService.cs still calls SP_CalculateTax directly — TASK-492 is unwinding that into TaxService.ResolveJurisdiction.',
  },
  {
    id: 'erp-tax', name: 'Tax', path: 'server/Careworks.Erp.Tax/',
    loc: 61_402, files: 94, lastTouched: '6 min ago', risk: 'CRITICAL', coverage: 64, understood: 88,
    owner: 'database', renewal: 'in-renewal', tables: ['MST_TAX', 'MST_TAX_SLAB', 'MST_STATE'], sprocs: 31, openBugs: 4,
    note: 'CGST/SGST vs IGST branch lives at SP_CalculateTax L288. 3 dead procedures pending deprecation (TASK-514, ADR-51).',
  },
  {
    id: 'erp-inventory', name: 'Inventory', path: 'server/Careworks.Erp.Inventory/',
    loc: 188_140, files: 271, lastTouched: '4 h ago', risk: 'HIGH', coverage: 58, understood: 84,
    owner: 'backend', renewal: 'legacy', tables: ['MST_ITEM', 'TRANS_STOCK', 'TRANS_STOCK_BATCH'], sprocs: 96, openBugs: 9,
    note: 'Reorder point still on the 2013 fixed-quantity formula. Batch/expiry logic duplicated in 3 procedures.',
  },
  {
    id: 'erp-customer', name: 'Customer Master', path: 'server/Careworks.Erp.CustomerMaster/',
    loc: 74_260, files: 118, lastTouched: '18 min ago', risk: 'MEDIUM', coverage: 76, understood: 91,
    owner: 'backend', renewal: 'in-renewal', tables: ['MST_CUSTOMER', 'MST_CUSTOMER_GST', 'MST_ADDRESS'], sprocs: 22, openBugs: 2,
    note: 'Bulk CSV upload (TASK-488) adds a staging table so partial failures roll back cleanly.',
  },
  {
    id: 'erp-purchase', name: 'Purchase', path: 'server/Careworks.Erp.Purchase/',
    loc: 131_905, files: 196, lastTouched: '2 d ago', risk: 'MEDIUM', coverage: 61, understood: 79,
    owner: 'backend', renewal: 'legacy', tables: ['TRANS_PO', 'TRANS_GRN', 'MST_VENDOR'], sprocs: 58, openBugs: 5,
    note: 'GRN posting writes to TRANS_STOCK inside the same transaction as the PO close — no idempotency key.',
  },
  {
    id: 'erp-sales', name: 'Sales Order', path: 'server/Careworks.Erp.SalesOrder/',
    loc: 142_318, files: 208, lastTouched: '1 d ago', risk: 'HIGH', coverage: 63, understood: 82,
    owner: 'backend', renewal: 'legacy', tables: ['TRANS_SO', 'TRANS_SO_LINE', 'MST_PRICE_LIST'], sprocs: 67, openBugs: 7,
    note: 'Place-of-supply is copied from the billing address at order creation — the root of BUG-883.',
  },
  {
    id: 'erp-gl', name: 'General Ledger', path: 'server/Careworks.Erp.Gl/',
    loc: 96_744, files: 141, lastTouched: '5 d ago', risk: 'CRITICAL', coverage: 44, understood: 68,
    owner: 'architect', renewal: 'legacy', tables: ['TRANS_LEDGER', 'MST_ACCOUNT', 'TRANS_VOUCHER'], sprocs: 88, openBugs: 3,
    note: 'Nobody has touched the posting engine since 2019. Double-entry invariants are enforced in T-SQL, not in C#.',
  },
  {
    id: 'erp-payroll', name: 'Payroll', path: 'server/Careworks.Erp.Payroll/',
    loc: 88_012, files: 127, lastTouched: '11 d ago', risk: 'HIGH', coverage: 39, understood: 57,
    owner: 'architect', renewal: 'legacy', tables: ['MST_EMPLOYEE', 'TRANS_SALARY', 'MST_PF_SLAB'], sprocs: 44, openBugs: 8,
    note: 'Statutory slabs hardcoded per financial year in a 1,900-line procedure. Lowest understanding in the repo.',
  },
  {
    id: 'erp-reports', name: 'Reports', path: 'web/src/modules/reports/',
    loc: 63_470, files: 174, lastTouched: '25 min ago', risk: 'LOW', coverage: 82, understood: 95,
    owner: 'frontend', renewal: 'renewed', tables: ['VW_SALES_SUMMARY', 'VW_STOCK_AGEING'], sprocs: 39, openBugs: 1,
    note: 'Migrated to the new DataGrid in TASK-479. XLSX export still buffers in memory — TASK-513 will stream it.',
  },
  {
    id: 'erp-auth', name: 'Auth & Sessions', path: 'server/Careworks.Erp.Auth/',
    loc: 29_118, files: 61, lastTouched: '1 h ago', risk: 'CRITICAL', coverage: 55, understood: 86,
    owner: 'security', renewal: 'in-renewal', tables: ['MST_USER', 'MST_ROLE', 'TRANS_LOGIN_AUDIT'], sprocs: 12, openBugs: 4,
    note: 'In-proc session state dies with every app-pool recycle — TASK-501 blocked on APPR-118 (Redis provider).',
  },
  {
    id: 'erp-notify', name: 'Notifications', path: 'server/Careworks.Erp.Notifications/',
    loc: 21_664, files: 48, lastTouched: '3 d ago', risk: 'LOW', coverage: 70, understood: 89,
    owner: 'backend', renewal: 'renewed', tables: ['TRANS_NOTIFY_QUEUE', 'MST_TEMPLATE'], sprocs: 7, openBugs: 0,
    note: 'Rewritten on a queue + retry table in Q1. The only module with a real dead-letter path.',
  },
  {
    id: 'erp-audit', name: 'Audit Trail', path: 'server/Careworks.Erp.Audit/',
    loc: 17_902, files: 34, lastTouched: '9 d ago', risk: 'MEDIUM', coverage: 48, understood: 74,
    owner: 'security', renewal: 'legacy', tables: ['TRANS_AUDIT_LOG', 'TRANS_AUDIT_FIELD'], sprocs: 19, openBugs: 2,
    note: 'Trigger-based capture on 38 tables. Adding a column anywhere silently widens the audit row.',
  },
  {
    id: 'erp-bank', name: 'Bank Reconciliation', path: 'server/Careworks.Erp.BankRecon/',
    loc: 54_270, files: 89, lastTouched: '6 d ago', risk: 'HIGH', coverage: 41, understood: 63,
    owner: 'database', renewal: 'legacy', tables: ['TRANS_BANK_STMT', 'TRANS_RECON_MATCH'], sprocs: 36, openBugs: 6,
    note: 'Fuzzy matcher is a 700-line procedure with a hand-tuned score threshold nobody can explain.',
  },
  {
    id: 'erp-dispatch', name: 'Dispatch', path: 'server/Careworks.Erp.Dispatch/',
    loc: 47_311, files: 76, lastTouched: '3 d ago', risk: 'MEDIUM', coverage: 66, understood: 81,
    owner: 'backend', renewal: 'in-renewal', tables: ['TRANS_DISPATCH', 'TRANS_EWAY_BILL'], sprocs: 24, openBugs: 3,
    note: 'E-way bill API contract changed in March; the retry wrapper was added but never load-tested.',
  },
];

/* ── HIMS ─────────────────────────────────────────────────────── */
const hims: ModuleRow[] = [
  { id: 'h-opd', name: 'OPD Registration', path: 'server/Hims.Opd/', loc: 92_410, files: 148, lastTouched: '12 min ago', risk: 'HIGH', coverage: 57, understood: 80, owner: 'backend', renewal: 'in-renewal', tables: ['TRANS_OPD_VISIT', 'MST_PATIENT'], sprocs: 41, openBugs: 5, note: 'Receipt and ledger round money in two different places — TASK-503.' },
  { id: 'h-ipd', name: 'IPD & Bed Management', path: 'server/Hims.Ipd/', loc: 118_260, files: 172, lastTouched: '2 h ago', risk: 'HIGH', coverage: 49, understood: 71, owner: 'backend', renewal: 'legacy', tables: ['TRANS_IPD_ADMISSION', 'MST_BED'], sprocs: 63, openBugs: 7, note: 'Bed transfer history is reconstructed from the audit log, not stored.' },
  { id: 'h-billing', name: 'Hospital Billing', path: 'server/Hims.Billing/', loc: 104_880, files: 159, lastTouched: '38 min ago', risk: 'CRITICAL', coverage: 52, understood: 77, owner: 'database', renewal: 'in-renewal', tables: ['TRANS_BILL', 'MST_TARIFF'], sprocs: 58, openBugs: 9, note: 'Tariff resolution walks 4 fallback levels — scheme, payer, plan, default.' },
  { id: 'h-pharmacy', name: 'Pharmacy', path: 'server/Hims.Pharmacy/', loc: 71_540, files: 112, lastTouched: '1 d ago', risk: 'MEDIUM', coverage: 61, understood: 74, owner: 'backend', renewal: 'legacy', tables: ['TRANS_PHARM_ISSUE', 'MST_DRUG'], sprocs: 34, openBugs: 4, note: 'Batch expiry checked at issue time only, never at reservation.' },
  { id: 'h-lab', name: 'Diagnostics / LIS', path: 'server/Hims.Lab/', loc: 66_190, files: 98, lastTouched: '20 min ago', risk: 'MEDIUM', coverage: 58, understood: 69, owner: 'backend', renewal: 'legacy', tables: ['TRANS_LAB_ORDER', 'TRANS_LAB_RESULT'], sprocs: 29, openBugs: 3, note: 'HL7 inbound parser tolerates malformed segments silently — TASK-517 depends on it.' },
  { id: 'h-docs', name: 'Clinical Documents', path: 'web/src/modules/documents/', loc: 38_720, files: 84, lastTouched: '4 h ago', risk: 'LOW', coverage: 73, understood: 88, owner: 'frontend', renewal: 'renewed', tables: ['TRANS_DISCHARGE_SUMMARY'], sprocs: 11, openBugs: 1, note: 'Department-wise discharge templates landed in TASK-519.' },
  { id: 'h-insurance', name: 'Insurance / TPA', path: 'server/Hims.Insurance/', loc: 58_340, files: 91, lastTouched: '5 d ago', risk: 'HIGH', coverage: 44, understood: 61, owner: 'architect', renewal: 'legacy', tables: ['TRANS_CLAIM', 'MST_TPA'], sprocs: 37, openBugs: 6, note: 'Claim state machine is implicit — 11 status codes, no transition table.' },
  { id: 'h-auth', name: 'Auth & Roles', path: 'server/Hims.Auth/', loc: 22_105, files: 44, lastTouched: '8 d ago', risk: 'MEDIUM', coverage: 62, understood: 84, owner: 'security', renewal: 'in-renewal', tables: ['MST_USER', 'MST_ROLE'], sprocs: 9, openBugs: 2, note: 'Shares the legacy PMI user table — cannot change the password hash scheme unilaterally.' },
  { id: 'h-reports', name: 'MIS Reports', path: 'web/src/modules/mis/', loc: 44_960, files: 103, lastTouched: '2 d ago', risk: 'LOW', coverage: 66, understood: 82, owner: 'frontend', renewal: 'renewed', tables: ['VW_OPD_DAILY', 'VW_BED_OCCUPANCY'], sprocs: 26, openBugs: 1, note: 'Occupancy view recalculates at 00:05; anything intraday is approximate.' },
  { id: 'h-notify', name: 'Notifications', path: 'server/Hims.Notifications/', loc: 15_880, files: 31, lastTouched: '13 d ago', risk: 'LOW', coverage: 54, understood: 79, owner: 'backend', renewal: 'legacy', tables: ['TRANS_SMS_QUEUE'], sprocs: 6, openBugs: 0, note: 'SMS gateway credentials still read from web.config on each send.' },
];

/* ── Taxi (greenfield) ────────────────────────────────────────── */
const taxi: ModuleRow[] = [
  { id: 'x-supply', name: 'Driver Supply', path: 'services/supply/', loc: 14_820, files: 86, lastTouched: '3 h ago', risk: 'LOW', coverage: 88, understood: 96, owner: 'backend', renewal: 'renewed', tables: ['driver', 'driver_document'], sprocs: 0, openBugs: 1, note: 'KYC state machine from TASK-470, fully covered by contract tests.' },
  { id: 'x-demand', name: 'Rider App API', path: 'services/demand/', loc: 12_140, files: 71, lastTouched: '5 h ago', risk: 'LOW', coverage: 84, understood: 94, owner: 'backend', renewal: 'renewed', tables: ['rider', 'ride_request'], sprocs: 0, openBugs: 0, note: 'Idempotency key required on every booking mutation.' },
  { id: 'x-match', name: 'Matching Engine', path: 'services/matching/', loc: 9_670, files: 42, lastTouched: '1 d ago', risk: 'MEDIUM', coverage: 79, understood: 90, owner: 'architect', renewal: 'renewed', tables: ['ride', 'dispatch_offer'], sprocs: 0, openBugs: 2, note: 'H3 cell radius widened twice; needs a load test before surge lands.' },
  { id: 'x-pricing', name: 'Pricing', path: 'services/pricing/', loc: 6_310, files: 28, lastTouched: '30 min ago', risk: 'MEDIUM', coverage: 71, understood: 87, owner: 'backend', renewal: 'in-renewal', tables: ['fare_rule', 'surge_window'], sprocs: 0, openBugs: 1, note: 'Surge engine in planning (TASK-506) — capped at 2.5x per zone.' },
  { id: 'x-payments', name: 'Payments', path: 'services/payments/', loc: 8_940, files: 39, lastTouched: '2 d ago', risk: 'HIGH', coverage: 81, understood: 92, owner: 'backend', renewal: 'renewed', tables: ['payment', 'refund'], sprocs: 0, openBugs: 1, note: 'Webhook replay protection uses a 24h dedupe window in Redis.' },
  { id: 'x-trust', name: 'Trust & Safety', path: 'services/trust/', loc: 5_220, files: 24, lastTouched: '5 d ago', risk: 'MEDIUM', coverage: 76, understood: 89, owner: 'security', renewal: 'renewed', tables: ['cancellation', 'sos_event'], sprocs: 0, openBugs: 0, note: 'Cancellation rule engine shipped in TASK-448.' },
  { id: 'x-web', name: 'Rider Web', path: 'apps/web/', loc: 18_460, files: 132, lastTouched: '4 h ago', risk: 'LOW', coverage: 74, understood: 93, owner: 'frontend', renewal: 'renewed', tables: [], sprocs: 0, openBugs: 2, note: 'Next.js 15 app router; every page is a server component by default.' },
  { id: 'x-ops', name: 'Ops Console', path: 'apps/ops/', loc: 7_980, files: 58, lastTouched: '6 d ago', risk: 'LOW', coverage: 62, understood: 85, owner: 'frontend', renewal: 'in-renewal', tables: [], sprocs: 0, openBugs: 1, note: 'Internal-only; auth is behind the VPN, not the product IdP.' },
];

/* ── FIFA (paused) ────────────────────────────────────────────── */
const fifa: ModuleRow[] = [
  { id: 'f-ingest', name: 'Event Ingestion', path: 'apps/ingest/', loc: 11_240, files: 46, lastTouched: '6 d ago', risk: 'MEDIUM', coverage: 44, understood: 78, owner: 'backend', renewal: 'in-renewal', tables: ['match_event'], sprocs: 0, openBugs: 3, note: 'Backfill blocked with the project (TASK-523).' },
  { id: 'f-model', name: 'Stat Models', path: 'packages/models/', loc: 8_610, files: 33, lastTouched: '7 d ago', risk: 'LOW', coverage: 51, understood: 82, owner: 'architect', renewal: 'renewed', tables: ['xg_snapshot'], sprocs: 0, openBugs: 1, note: 'xG model pinned to the 2024 coefficients.' },
  { id: 'f-api', name: 'Public API', path: 'apps/api/', loc: 6_980, files: 29, lastTouched: '8 d ago', risk: 'LOW', coverage: 47, understood: 80, owner: 'backend', renewal: 'renewed', tables: [], sprocs: 0, openBugs: 0, note: 'Rate-limited to 60 rpm per key.' },
  { id: 'f-web', name: 'Match Web', path: 'apps/web/', loc: 13_420, files: 88, lastTouched: '6 d ago', risk: 'LOW', coverage: 38, understood: 74, owner: 'frontend', renewal: 'in-renewal', tables: [], sprocs: 0, openBugs: 2, note: 'Live timeline component re-renders the whole match on each tick.' },
  { id: 'f-etl', name: 'ClickHouse ETL', path: 'ops/etl/', loc: 4_310, files: 19, lastTouched: '9 d ago', risk: 'MEDIUM', coverage: 29, understood: 66, owner: 'database', renewal: 'legacy', tables: ['events_raw', 'events_agg'], sprocs: 0, openBugs: 1, note: 'Materialised views rebuilt by hand after every schema change.' },
  { id: 'f-jobs', name: 'Scheduled Jobs', path: 'ops/jobs/', loc: 2_140, files: 11, lastTouched: '11 d ago', risk: 'LOW', coverage: 33, understood: 71, owner: 'devops', renewal: 'legacy', tables: [], sprocs: 0, openBugs: 0, note: 'Cron on a single box — no leader election.' },
];

/* ── NeuroCode (self) ─────────────────────────────────── */
const aios: ModuleRow[] = [
  { id: 'a-memory', name: 'Memory Brain', path: 'services/memory/', loc: 7_420, files: 38, lastTouched: '8 min ago', risk: 'MEDIUM', coverage: 44, understood: 71, owner: 'backend', renewal: 'in-renewal', tables: ['memory_fact', 'memory_hit'], sprocs: 0, openBugs: 2, note: 'Decay + reinforcement policy landing in TASK-509.' },
  { id: 'a-orchestrator', name: 'Orchestrator', path: 'services/orchestrator/', loc: 9_180, files: 52, lastTouched: '22 min ago', risk: 'HIGH', coverage: 38, understood: 64, owner: 'architect', renewal: 'in-renewal', tables: ['run', 'run_step'], sprocs: 0, openBugs: 4, note: 'Worktree cleanup on a crashed run is still manual.' },
  { id: 'a-router', name: 'Model Router', path: 'services/router/', loc: 3_640, files: 21, lastTouched: '1 h ago', risk: 'MEDIUM', coverage: 51, understood: 78, owner: 'backend', renewal: 'renewed', tables: ['route_rule'], sprocs: 0, openBugs: 1, note: 'Local-first: falls back to remote only above a quality threshold.' },
  { id: 'a-knowledge', name: 'Knowledge / RAG', path: 'services/knowledge/', loc: 5_910, files: 34, lastTouched: '3 h ago', risk: 'MEDIUM', coverage: 33, understood: 59, owner: 'backend', renewal: 'legacy', tables: ['doc', 'chunk'], sprocs: 0, openBugs: 3, note: 'BGE-M3 embeddings + BGE-Reranker-v2; chunker still splits mid-table.' },
  { id: 'a-acp', name: 'ACP Bridge', path: 'services/acp/', loc: 1_880, files: 14, lastTouched: '35 min ago', risk: 'LOW', coverage: 21, understood: 48, owner: 'backend', renewal: 'legacy', tables: [], sprocs: 0, openBugs: 1, note: 'Zed session lifecycle mapping in planning (TASK-521).' },
  { id: 'a-permissions', name: 'Permission Gate', path: 'services/permissions/', loc: 2_460, files: 17, lastTouched: '2 h ago', risk: 'HIGH', coverage: 62, understood: 83, owner: 'security', renewal: 'renewed', tables: ['rule', 'approval'], sprocs: 0, openBugs: 0, note: 'DROP / rm -rf / secrets are hard denials with no override flag.' },
  { id: 'a-console', name: 'Console UI', path: 'web/src/', loc: 12_740, files: 96, lastTouched: 'just now', risk: 'LOW', coverage: 27, understood: 62, owner: 'frontend', renewal: 'in-renewal', tables: [], sprocs: 0, openBugs: 2, note: '31 screens on one token contract — 8 themes repaint everything.' },
  { id: 'a-evals', name: 'Eval Runner', path: 'services/evals/', loc: 3_120, files: 23, lastTouched: '4 h ago', risk: 'MEDIUM', coverage: 41, understood: 66, owner: 'qa', renewal: 'legacy', tables: ['eval_case', 'eval_run'], sprocs: 0, openBugs: 1, note: 'reviewer-strictness regressed 92 → 89 on legacy-contract cases.' },
  { id: 'a-hooks', name: 'Hook Engine', path: 'services/hooks/', loc: 1_540, files: 12, lastTouched: '6 h ago', risk: 'LOW', coverage: 48, understood: 74, owner: 'devops', renewal: 'renewed', tables: ['hook', 'hook_fire'], sprocs: 0, openBugs: 0, note: 'Blocking hooks time out at 8s and fail closed.' },
];

export const modulesByProject: Record<string, ModuleRow[]> = { erp, hims, taxi, fifa, aios };
export const getModules = (projectId: string) => modulesByProject[projectId] ?? erp;

/* ── Project rules ────────────────────────────────────────────── */
export interface ProjectRule {
  id: string;
  rule: string;
  detail: string;
  enforcedBy: string;
  severity: 'blocker' | 'major' | 'minor';
  violations24h: number;
}

const erpRules: ProjectRule[] = [
  { id: 'r1', rule: 'Strict SOLID — one reason to change per class', detail: 'A service that both resolves tax and formats an invoice gets split before review passes.', enforcedBy: 'Code Reviewer · solid-enforcer', severity: 'blocker', violations24h: 3 },
  { id: 'r2', rule: 'Class-object naming: <Noun>Service / I<Noun>Service / <Noun>Repository', detail: 'No Helper, Util, Manager or Common suffixes anywhere under server/.', enforcedBy: 'Code Reviewer · convention-check', severity: 'major', violations24h: 1 },
  { id: 'r3', rule: 'No direct writes to TRANS_* tables', detail: 'Every transactional write goes through a repository + stored procedure. MEM-142 (HIGH).', enforcedBy: 'Database Engineer + PreToolUse hook', severity: 'blocker', violations24h: 0 },
  { id: 'r4', rule: 'Repository pattern — no SQL text inside a service', detail: 'Services take an I*Repository. A raw SqlCommand in a service file fails the build gate.', enforcedBy: 'Backend Engineer · repository-pattern', severity: 'blocker', violations24h: 2 },
  { id: 'r5', rule: 'Interface-first for every new service', detail: 'Interface + registration in ServiceCollectionExtensions before the implementation is written.', enforcedBy: 'Architect', severity: 'major', violations24h: 0 },
  { id: 'r6', rule: 'Legacy rounding behaviour is a contract, not a bug', detail: 'Money rounds half-away-from-zero at 2 dp because 11 years of ledgers depend on it.', enforcedBy: 'QA Engineer · regression-detector', severity: 'blocker', violations24h: 1 },
  { id: 'r7', rule: 'Every migration ships with a verified rollback', detail: 'Dry-run against the nightly snapshot; the rollback is executed in the dry-run too.', enforcedBy: 'Database Engineer + APPR gate', severity: 'blocker', violations24h: 0 },
  { id: 'r8', rule: 'No new dependency without human approval', detail: 'papaparse@5.4.1 is currently waiting on APPR-120.', enforcedBy: 'Permission rule p7', severity: 'major', violations24h: 4 },
  { id: 'r9', rule: 'Stored procedures are renamed, never dropped', detail: 'zz_deprecated_ prefix, observe one quarter, then a human removes it by hand (ADR-51).', enforcedBy: 'Permission rule p10 (hard deny)', severity: 'blocker', violations24h: 0 },
  { id: 'r10', rule: 'Match the surrounding code over the better pattern', detail: 'A module mid-renewal keeps its old shape until the whole module moves.', enforcedBy: 'Architect · pattern-detector', severity: 'minor', violations24h: 6 },
];

const genericRules: ProjectRule[] = [
  { id: 'g1', rule: 'Strict SOLID — one reason to change per class', detail: 'Enforced at review; blocker findings stop the pipeline.', enforcedBy: 'Code Reviewer · solid-enforcer', severity: 'blocker', violations24h: 1 },
  { id: 'g2', rule: 'Interface-first for every new service', detail: 'Contract and tests before implementation.', enforcedBy: 'Architect', severity: 'major', violations24h: 0 },
  { id: 'g3', rule: 'Repository pattern — no SQL text inside a service', detail: 'Data access lives behind a repository or a typed query module.', enforcedBy: 'Backend Engineer · repository-pattern', severity: 'blocker', violations24h: 0 },
  { id: 'g4', rule: 'Idempotency key on every mutating endpoint', detail: 'Retries must never double-charge or double-book.', enforcedBy: 'Code Reviewer · api-contract', severity: 'blocker', violations24h: 2 },
  { id: 'g5', rule: 'No new dependency without human approval', detail: 'Supply chain surface is the human PM’s signature.', enforcedBy: 'Permission rule p7', severity: 'major', violations24h: 0 },
  { id: 'g6', rule: 'Every deploy proves its rollback first', detail: 'Staging-first, always; production carries a human approval.', enforcedBy: 'DevOps Engineer', severity: 'blocker', violations24h: 0 },
];

export const rulesByProject: Record<string, ProjectRule[]> = {
  erp: erpRules, hims: erpRules.slice(0, 7), taxi: genericRules, fifa: genericRules.slice(0, 4), aios: genericRules,
};

/* ── ADRs ─────────────────────────────────────────────────────── */
export interface Adr {
  id: string;
  ref: string;
  title: string;
  status: 'accepted' | 'proposed' | 'superseded' | 'rejected';
  at: string;
  by: string;
  summary: string;
  supersedes?: string;
}

const erpAdrs: Adr[] = [
  { id: 'd52', ref: 'ADR-52', title: 'Single jurisdiction resolver for tax', status: 'proposed', at: '14:08 today', by: 'Documentation Agent', summary: 'Place-of-supply is resolved once in TaxService.ResolveJurisdiction and passed down; SP_CalculateTax stops re-deriving it from the billing address.' },
  { id: 'd51', ref: 'ADR-51', title: 'Stored procedure deprecation policy', status: 'accepted', at: 'yesterday 17:40', by: 'Architect', summary: 'Dead procedures are renamed zz_deprecated_*, observed for one quarter, then removed manually. No agent may DROP.' },
  { id: 'd50', ref: 'ADR-50', title: 'Redis-backed session state on IIS', status: 'proposed', at: '2 d ago', by: 'DevOps Engineer', summary: 'In-proc session cannot survive app-pool recycles across 3 nodes. Custom provider + rolling restart, rollback = revert web.config.' },
  { id: 'd49', ref: 'ADR-49', title: 'Staging table for all bulk imports', status: 'accepted', at: '4 d ago', by: 'Database Engineer', summary: 'CSV/XLSX imports land in a staging table first so a partial failure rolls back without touching TRANS_*.' },
  { id: 'd48', ref: 'ADR-48', title: 'New DataGrid as the single grid primitive', status: 'accepted', at: '9 d ago', by: 'Frontend Engineer', summary: 'The 2014 grid is frozen. Every migrated screen must keep column parity and both export formats.' },
  { id: 'd47', ref: 'ADR-47', title: 'Money rounding is half-away-from-zero at 2 dp', status: 'accepted', at: '3 w ago', by: 'Architect', summary: 'Documented as a contract, not a defect. Any change requires a signed-off reconciliation of historical ledgers.' },
  { id: 'd44', ref: 'ADR-44', title: 'Audit capture by trigger', status: 'superseded', at: '2 mo ago', by: 'Architect', summary: 'Trigger-based capture retained for legacy tables only; new modules emit audit events from the repository layer.', supersedes: 'ADR-31' },
  { id: 'd41', ref: 'ADR-41', title: 'Reject EF Core migrations for the legacy schema', status: 'rejected', at: '4 mo ago', by: 'Database Engineer', summary: 'Rejected — 382 tables with trigger-based audit cannot be modelled safely. Hand-written SQL migrations remain the standard.' },
];

const genericAdrs: Adr[] = [
  { id: 'gd9', ref: 'ADR-09', title: 'H3 cells for driver matching', status: 'accepted', at: '6 d ago', by: 'Architect', summary: 'Geospatial matching on H3 resolution 8 with a widening radius, capped at 3 rings.' },
  { id: 'gd8', ref: 'ADR-08', title: 'Idempotency keys on booking mutations', status: 'accepted', at: '11 d ago', by: 'Backend Engineer', summary: 'Every POST that creates money or a ride carries a client-generated key, deduped for 24h.' },
  { id: 'gd7', ref: 'ADR-07', title: 'Surge capped at 2.5x per zone', status: 'proposed', at: '30 min ago', by: 'Architect', summary: 'Demand/supply ratio drives a per-zone multiplier with a hard ceiling and a 5-minute smoothing window.' },
  { id: 'gd6', ref: 'ADR-06', title: 'Server components by default', status: 'accepted', at: '3 w ago', by: 'Frontend Engineer', summary: 'Client components are opt-in and must justify the boundary in the PR description.' },
];

export const adrsByProject: Record<string, Adr[]> = {
  erp: erpAdrs, hims: erpAdrs.slice(2, 7), taxi: genericAdrs, fifa: genericAdrs.slice(0, 3), aios: genericAdrs.slice(1),
};

/* ── Risky areas ──────────────────────────────────────────────── */
export interface RiskyArea {
  id: string;
  path: string;
  risk: Risk;
  churn: number;
  complexity: number;
  reason: string;
  lastIncident: string;
}

const erpRisky: RiskyArea[] = [
  { id: 'k1', path: 'db/procedures/SP_CalculateTax.sql', risk: 'CRITICAL', churn: 34, complexity: 91, reason: '412 lines, 9 nested branches, jurisdiction derived twice. Directly behind BUG-883.', lastIncident: 'today 13:52' },
  { id: 'k2', path: 'server/Careworks.Erp.Gl/PostingEngine.cs', risk: 'CRITICAL', churn: 3, complexity: 88, reason: 'Double-entry invariants live in T-SQL. No test asserts the balance after a partial failure.', lastIncident: '4 mo ago' },
  { id: 'k3', path: 'server/Careworks.Erp.Auth/SessionProvider.cs', risk: 'HIGH', churn: 12, complexity: 54, reason: 'In-proc session across 3 IIS nodes; every recycle logs users out.', lastIncident: 'today 08:02' },
  { id: 'k4', path: 'db/procedures/SP_ReconcileBankStatement.sql', risk: 'HIGH', churn: 7, complexity: 83, reason: 'Hand-tuned fuzzy score threshold with no documented derivation.', lastIncident: '6 d ago' },
  { id: 'k5', path: 'server/Careworks.Erp.Payroll/StatutorySlabs.cs', risk: 'HIGH', churn: 21, complexity: 79, reason: 'Slabs hardcoded per financial year; every April is a manual patch.', lastIncident: '11 d ago' },
  { id: 'k6', path: 'server/Careworks.Erp.Inventory/BatchAllocator.cs', risk: 'HIGH', churn: 18, complexity: 72, reason: 'FEFO allocation duplicated in C# and in SP_AllocateBatch — they disagree on expiry ties.', lastIncident: '3 w ago' },
  { id: 'k7', path: 'server/Careworks.Erp.Purchase/GrnPostingService.cs', risk: 'MEDIUM', churn: 15, complexity: 61, reason: 'PO close and stock write share a transaction with no idempotency key.', lastIncident: '2 mo ago' },
  { id: 'k8', path: 'server/Careworks.Erp.Dispatch/EwayBillClient.cs', risk: 'MEDIUM', churn: 9, complexity: 44, reason: 'Retry wrapper added after the March contract change, never load-tested.', lastIncident: '5 w ago' },
  { id: 'k9', path: 'db/triggers/TRG_Audit_TRANS_INVOICE.sql', risk: 'MEDIUM', churn: 4, complexity: 39, reason: 'Column adds silently widen the audit row; 38 tables share this pattern.', lastIncident: '9 d ago' },
  { id: 'k10', path: 'web/src/modules/reports/ExcelExport.ts', risk: 'MEDIUM', churn: 11, complexity: 36, reason: 'Buffers the whole result set in memory — times out past ~100k rows.', lastIncident: 'today 12:20' },
];

const genericRisky: RiskyArea[] = [
  { id: 'gk1', path: 'services/matching/RadiusStrategy.ts', risk: 'MEDIUM', churn: 22, complexity: 58, reason: 'Radius widened twice without a load test; surge will multiply the fan-out.', lastIncident: '1 d ago' },
  { id: 'gk2', path: 'services/payments/WebhookHandler.ts', risk: 'HIGH', churn: 9, complexity: 47, reason: 'Replay protection depends on a Redis key surviving a failover.', lastIncident: '2 w ago' },
  { id: 'gk3', path: 'services/pricing/FareCalculator.ts', risk: 'MEDIUM', churn: 14, complexity: 41, reason: 'Fare rules resolved at request time with no snapshot of the applied rule.', lastIncident: '4 d ago' },
  { id: 'gk4', path: 'apps/web/app/(ride)/track/page.tsx', risk: 'LOW', churn: 31, complexity: 28, reason: 'Live tracking re-renders the whole tree on each position tick.', lastIncident: '3 d ago' },
];

export const riskyByProject: Record<string, RiskyArea[]> = {
  erp: erpRisky, hims: erpRisky.slice(1, 8), taxi: genericRisky, fifa: genericRisky.slice(2), aios: genericRisky.slice(0, 3),
};

/* ── Stack / infra KV ─────────────────────────────────────────── */
export interface InfraKv { k: string; v: string; mono?: boolean }

export const infraByProject: Record<string, InfraKv[]> = {
  erp: [
    { k: 'Runtime', v: '.NET 8.0.11 · C# 12' },
    { k: 'Frontend', v: 'React 18.3 · Vite 5 · MUI 5' },
    { k: 'Database', v: 'SQL Server 2019 (15.0.4360)' },
    { k: 'Host', v: 'IIS 10 · 3 nodes behind F5' },
    { k: 'Branch', v: 'main → release/2026.09', mono: true },
    { k: 'Build', v: 'dotnet build · 4m 12s', mono: true },
    { k: 'Test command', v: 'dotnet test --filter Category!=Slow', mono: true },
    { k: 'Migrations', v: 'db/migrations/0041 … 0042 (pending)', mono: true },
    { k: 'CI', v: 'Azure DevOps · pipeline erp-main' },
    { k: 'Staging', v: 'erp-stg.internal · v2026.09.14' },
    { k: 'Production', v: 'erp.careworks.in · v2026.08.31' },
    { k: 'Snapshot', v: 'nightly 02:15 · 214 GB', mono: true },
  ],
  hims: [
    { k: 'Runtime', v: '.NET 8.0.11 · C# 12' },
    { k: 'Frontend', v: 'React 19.0 · Vite 6' },
    { k: 'Database', v: 'SQL Server 2017 + Redis 7.2' },
    { k: 'Host', v: 'IIS 10 · 2 nodes' },
    { k: 'Branch', v: 'main → release/v3.2', mono: true },
    { k: 'Legacy contract', v: 'PMI schema — read-compatible, no DDL' },
    { k: 'CI', v: 'GitHub Actions · hims-ci.yml', mono: true },
    { k: 'Staging', v: 'hims-stg.internal · v3.2.7' },
    { k: 'Production', v: 'hims.jaslok.local · v3.1.9' },
  ],
  taxi: [
    { k: 'Runtime', v: 'Node 22 · Python 3.12 (FastAPI)' },
    { k: 'Frontend', v: 'Next.js 15 · App Router' },
    { k: 'Database', v: 'PostgreSQL 16 · Redis 7.2' },
    { k: 'Host', v: 'Fly.io · 3 regions' },
    { k: 'Branch', v: 'main (trunk-based)', mono: true },
    { k: 'CI', v: 'GitHub Actions · 6m 40s', mono: true },
    { k: 'Staging', v: 'ride-stg.fly.dev · v3.14.2' },
    { k: 'Production', v: 'rideapp.in · v3.13.8' },
  ],
  fifa: [
    { k: 'Runtime', v: 'Node 22 · Bun 1.2 (scripts)' },
    { k: 'Frontend', v: 'React 19 · Vite 6' },
    { k: 'Database', v: 'ClickHouse 24.8' },
    { k: 'Host', v: 'Hetzner CX42 · single box' },
    { k: 'Branch', v: 'main (frozen)', mono: true },
    { k: 'Status', v: 'Paused — agents released, memory retained' },
  ],
  aios: [
    { k: 'Runtime', v: 'Python 3.12 · FastAPI · LangGraph' },
    { k: 'Frontend', v: 'React 19 · Vite 8 · Tailwind 4' },
    { k: 'Vector store', v: 'Qdrant 1.12 · BGE-M3 (1024d)' },
    { k: 'Rerank', v: 'BGE-Reranker-v2 · top-50 → top-8' },
    { k: 'Local models', v: 'vLLM · Qwen3-Coder-Next, Qwen3-Next-80B' },
    { k: 'Remote models', v: 'DeepSeek-V3.2, Kimi-K2.5, GLM-4.7' },
    { k: 'Branch', v: 'main → feat/memory-decay', mono: true },
    { k: 'CI', v: 'GitHub Actions · aios-ci.yml', mono: true },
  ],
};

/* ── 18-step onboarding pipeline ──────────────────────────────── */
export interface OnboardStep { n: number; label: string; detail: string; agent: string; est: string }

export const onboardingSteps: OnboardStep[] = [
  { n: 1,  label: 'Clone & detect stack',        detail: 'Read manifests, lockfiles, .csproj / package.json / requirements.txt', agent: 'Architect', est: '20s' },
  { n: 2,  label: 'Map repository tree',          detail: 'Folder taxonomy, entry points, generated-code exclusions',            agent: 'Architect', est: '1m' },
  { n: 3,  label: 'Parse AST (tree-sitter)',      detail: 'Classes, interfaces, methods, call edges across every language',      agent: 'Architect', est: '6m' },
  { n: 4,  label: 'Index symbols via LSP',        detail: 'Go-to-definition graph, references, unresolved imports',              agent: 'Architect', est: '3m' },
  { n: 5,  label: 'Connect database',             detail: 'Read-only credentials against a snapshot replica',                    agent: 'Database Engineer', est: '15s' },
  { n: 6,  label: 'Parse schema',                 detail: 'Tables, columns, keys, indexes, triggers, views',                     agent: 'Database Engineer', est: '4m' },
  { n: 7,  label: 'Parse stored procedures',      detail: 'Body text, table touch-map, procedure-to-procedure calls',            agent: 'Database Engineer', est: '9m' },
  { n: 8,  label: 'Link code ↔ database',         detail: 'Which service calls which procedure, which procedure writes what',    agent: 'Architect', est: '5m' },
  { n: 9,  label: 'Detect conventions',           detail: 'Naming, folder layout, error handling, DI style, test placement',     agent: 'Code Reviewer', est: '3m' },
  { n: 10, label: 'Detect anti-patterns',         detail: 'God classes, SQL in services, duplicated business logic',             agent: 'Code Reviewer', est: '4m' },
  { n: 11, label: 'Build architecture graph',     detail: 'UI → API → service → repository → procedure → table layering',        agent: 'Architect', est: '2m' },
  { n: 12, label: 'Mine git history',             detail: 'Churn, hotspots, ownership, bug-fix density per file',                agent: 'Architect', est: '7m' },
  { n: 13, label: 'Extract business rules',       detail: 'Rules embedded in procedures and validators, written as facts',       agent: 'AI Commander', est: '11m' },
  { n: 14, label: 'Ingest docs & tickets',        detail: 'READMEs, ADRs, Jira exports, meeting notes → chunked + embedded',     agent: 'Documentation Agent', est: '6m' },
  { n: 15, label: 'Score risk per module',        detail: 'Complexity × churn × coverage gap → module risk grade',               agent: 'Architect', est: '1m' },
  { n: 16, label: 'Seed project memory',          detail: 'Isolated memory namespace, category-wise facts with confidence',      agent: 'Memory Brain', est: '2m' },
  { n: 17, label: 'Draft project rules',          detail: 'SOLID, naming, repository pattern, forbidden writes — human edits',   agent: 'AI Commander', est: '40s' },
  { n: 18, label: 'Baseline test + build',        detail: 'Run the suite once so every later regression has a reference',        agent: 'QA Engineer', est: '8m' },
];

/* ── Global AI brain vs isolated project memory ───────────────── */
export interface BrainBucket {
  id: string;
  label: string;
  count: number;
  examples: string[];
  note: string;
}

export const globalBrain: BrainBucket[] = [
  {
    id: 'b1', label: 'Programming knowledge', count: 4_182,
    examples: ['C# 12 collection expressions are safe on .NET 8', 'SQL Server: OPTION (RECOMPILE) on parameter-sniffed reports', 'React 19 — useOptimistic replaces the manual rollback pattern'],
    note: 'Language, framework and runtime facts. Never project-scoped.',
  },
  {
    id: 'b2', label: 'Your workflow', count: 316,
    examples: ['You approve migrations only with a dry-run count attached', 'You want Hinglish requirements compiled, never answered literally', 'You reject any PR that renames a stored procedure'],
    note: 'How the human PM actually works — learned from 418 approvals.',
  },
  {
    id: 'b3', label: 'Preferred architecture', count: 148,
    examples: ['Interface-first, repository pattern, no SQL in services', 'One reason to change per class, always', 'Feature folders over layer folders in greenfield work'],
    note: 'Carried into every new project on day one.',
  },
  {
    id: 'b4', label: 'Reusable skills', count: 63,
    examples: ['solid-enforcer', 'sproc-analysis', 'legacy-risk', 'migration-writer', 'visual-diff'],
    note: 'Skill packs any project can mount without re-learning.',
  },
  {
    id: 'b5', label: 'Lessons learned', count: 274,
    examples: ['Reviewer missed a repository-pattern break → rule added to the skill', 'Never trust a test that was not executed in this run', 'Legacy-expected failures must be reported separately from new breaks'],
    note: 'Written back by the self-improvement loop after every failed task.',
  },
  {
    id: 'b6', label: 'Tooling & environment', count: 91,
    examples: ['vLLM local route saves ~$0.40 per coding task', 'BGE-Reranker-v2 top-50 → top-8 beats raw vector recall', 'dotnet-format runs as a PostToolUse hook, not a pre-commit'],
    note: 'Router, MCP and hook facts shared across every workspace.',
  },
];

export interface IsolatedMemory { projectId: string; facts: number; rules: number; decisions: number; legacy: number; note: string }

export const isolatedMemory: IsolatedMemory[] = [
  { projectId: 'erp',  facts: 3_914, rules: 210, decisions: 52, legacy: 1_486, note: 'MST_TAX semantics never leak into HIMS.' },
  { projectId: 'hims', facts: 2_260, rules: 141, decisions: 34, legacy: 902,   note: 'PMI schema contract is HIMS-only.' },
  { projectId: 'taxi', facts: 611,   rules: 48,  decisions: 9,  legacy: 0,     note: 'Greenfield — conventions inherited from the global brain.' },
  { projectId: 'fifa', facts: 288,   rules: 22,  decisions: 4,  legacy: 0,     note: 'Retained while paused; agents released.' },
  { projectId: 'aios', facts: 744,   rules: 66,  decisions: 17, legacy: 0,     note: 'The OS observing itself.' },
];
