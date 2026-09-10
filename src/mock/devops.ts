import type { Environment, Deployment, Container } from '@/types';

/* Local-only shapes (do not touch src/types) */
export interface PipelineStage {
  id: string;
  name: string;
  state: 'pass' | 'fail' | 'running' | 'queued' | 'skipped';
  durationS: number;
  detail: string;
}
export interface LogLine {
  id: string;
  t: string;
  level: 'info' | 'ok' | 'warn' | 'err' | 'debug';
  source: string;
  text: string;
}
export interface Secret {
  id: string;
  name: string;
  scope: 'global' | 'erp' | 'hims' | 'ride';
  store: string;
  lastRotated: string;
  rotationDays: number;
  readers: string[];
  usedBy: string;
}
export interface HealthCheck {
  id: string;
  name: string;
  env: string;
  target: string;
  status: 'ok' | 'warn' | 'down';
  latencyMs: number;
  lastRun: string;
  note: string;
}

export const environments: Environment[] = [
  {
    id: 'local', name: 'LOCAL', kind: 'local', status: 'ok',
    url: 'http://localhost:5173  ·  api http://localhost:5079',
    version: 'v3.15.0-dev+9f3ac21', deployedAt: '6 min ago', uptime: '2h 14m',
    cpu: 34, mem: 51, requests: '812 / hr', errorRate: 0.0, requiresApproval: false,
  },
  {
    id: 'staging', name: 'STAGING', kind: 'staging', status: 'warn',
    url: 'https://erp-staging.sofscript.internal',
    version: 'v3.14.2-rc4+4be9012', deployedAt: 'today 13:31', uptime: '4h 51m',
    cpu: 61, mem: 74, requests: '11.4k / hr', errorRate: 0.8, requiresApproval: false,
  },
  {
    id: 'production', name: 'PRODUCTION', kind: 'production', status: 'ok',
    url: 'https://erp.careworks.co.in',
    version: 'v3.14.1+5d2ee81', deployedAt: 'yesterday 19:04', uptime: '19h 18m',
    cpu: 47, mem: 68, requests: '38.7k / hr', errorRate: 0.11, requiresApproval: true,
  },
];

export const productionGate = {
  version: 'v3.14.2+9f3ac21',
  target: 'PRODUCTION · erp.careworks.co.in · 3 IIS nodes behind ARR',
  contains: [
    'TASK-492 · interstate CGST/SGST → IGST correction (TaxService, SP_CalculateTax)',
    'Migration 0042_backfill_tax_jurisdiction.sql — writes 41 rows into TRANS_INVOICE_ADJ',
    'TASK-476 · GSTR-1 B2CS bucketing (read-only surface)',
  ],
  risks: [
    'Data-touching migration inside the September GST filing window',
    'IIS app-pool recycle drops InProc sessions — TASK-501 fix is NOT in this build',
    'Rollback needs SP_RecomputeLedgerWindow over 5 months of TRANS_INVOICE (~6 min)',
  ],
  window: 'Tonight 22:30 – 23:15 IST (post-cutoff, pre-batch)',
  rollback: 'deploy/rollback.ps1 → v3.14.1+5d2ee81 · db/migrations/0042_rollback.sql (dry-run verified 13:48)',
  requestedBy: 'DevOps Engineer',
  approver: 'You (AI Project Manager)',
};

export const deployments: Deployment[] = [
  { id: 'd1',  env: 'PRODUCTION', version: 'v3.14.2+9f3ac21', status: 'awaiting_approval', by: 'DevOps Engineer', at: 'today 14:19', durationS: 0,   commit: '9f3ac21' },
  { id: 'd2',  env: 'STAGING',    version: 'v3.14.2-rc4+4be9012', status: 'success', by: 'DevOps Engineer', at: 'today 13:31', durationS: 92,  commit: '4be9012' },
  { id: 'd3',  env: 'STAGING',    version: 'v3.14.2-rc3+e17c6aa', status: 'failed',  by: 'DevOps Engineer', at: 'today 12:08', durationS: 61,  commit: 'e17c6aa' },
  { id: 'd4',  env: 'LOCAL',      version: 'v3.15.0-dev+9f3ac21', status: 'success', by: 'Backend Engineer', at: 'today 13:56', durationS: 18,  commit: '9f3ac21' },
  { id: 'd5',  env: 'STAGING',    version: 'v3.14.2-rc2+b6d40f9', status: 'success', by: 'QA Engineer',      at: 'today 11:12', durationS: 88,  commit: 'b6d40f9' },
  { id: 'd6',  env: 'PRODUCTION', version: 'v3.14.1+5d2ee81', status: 'success', by: 'You',             at: 'yesterday 19:04', durationS: 214, commit: '5d2ee81' },
  { id: 'd7',  env: 'PRODUCTION', version: 'v3.14.0+7ac1f30', status: 'rolled_back', by: 'You',         at: 'yesterday 16:41', durationS: 198, commit: '7ac1f30' },
  { id: 'd8',  env: 'STAGING',    version: 'v3.14.1-rc1+1f8b73c', status: 'success', by: 'DevOps Engineer', at: 'yesterday 15:20', durationS: 84, commit: '1f8b73c' },
  { id: 'd9',  env: 'STAGING',    version: 'v3.14.0-rc6+ae4900d', status: 'success', by: 'DevOps Engineer', at: 'yesterday 12:55', durationS: 79, commit: 'ae4900d' },
  { id: 'd10', env: 'LOCAL',      version: 'v3.14.9-dev+0a6c249', status: 'success', by: 'Frontend Engineer', at: 'yesterday 12:02', durationS: 15, commit: '0a6c249' },
  { id: 'd11', env: 'STAGING',    version: 'v3.14.0-rc5+6bb17f2', status: 'failed',  by: 'DevOps Engineer', at: 'yesterday 10:31', durationS: 44,  commit: '6bb17f2' },
  { id: 'd12', env: 'PRODUCTION', version: 'v3.13.8+c9d0aa2', status: 'success', by: 'You',              at: '2026-09-08 20:15', durationS: 226, commit: 'c9d0aa2' },
  { id: 'd13', env: 'STAGING',    version: 'v3.13.8-rc2+c9d0aa2', status: 'success', by: 'DevOps Engineer', at: '2026-09-08 17:40', durationS: 81, commit: 'c9d0aa2' },
  { id: 'd14', env: 'STAGING',    version: 'v3.14.2-rc5+3ee5c04', status: 'running', by: 'DevOps Engineer', at: 'today 14:22', durationS: 44,  commit: '3ee5c04' },
];

export const deployNotes: Record<string, string> = {
  d1: 'Blocked at the human gate. Migration 0042 makes this a data-touching release inside the GST filing window.',
  d3: 'Failed at stage `integration` — SP_CalculateTax had been altered on the staging DB but the app image was still rc2. Fixed by redeploying both together.',
  d7: 'ROLLED BACK 11 min after deploy. Invoice list p95 went 340ms → 4.2s: the new B2CS query missed IX_TRANS_INVOICE_state_date, so SQL Server picked a clustered scan over 8.1M rows. Rolled back to v3.13.8, index added on the branch, re-released as v3.14.1 the same evening.',
  d11: 'Failed at stage `scan` — trivy flagged a HIGH in a transitive System.Text.Json 8.0.0. Bumped to 8.0.5 and re-ran.',
  d14: 'In flight — frontend snapshot bundle for the invoice tax head change.',
};

export const containers: Container[] = [
  { id: 'c1', name: 'erp-api',          image: 'ghcr.io/sofscript/erp-api:3.14.2-rc4',  status: 'up',         cpu: 38, mem: '1.9 GB / 4 GB', ports: '5079→8080' },
  { id: 'c2', name: 'erp-web',          image: 'ghcr.io/sofscript/erp-web:3.14.2-rc4',  status: 'up',         cpu: 6,  mem: '212 MB / 512 MB', ports: '5173→80' },
  { id: 'c3', name: 'erp-worker',       image: 'ghcr.io/sofscript/erp-worker:3.14.2-rc4', status: 'up',       cpu: 21, mem: '740 MB / 2 GB', ports: '—' },
  { id: 'c4', name: 'mssql-2019',       image: 'mcr.microsoft.com/mssql/server:2019-CU27', status: 'up',      cpu: 54, mem: '11.2 GB / 16 GB', ports: '1433→1433' },
  { id: 'c5', name: 'redis-session',    image: 'redis:7.4-alpine',                      status: 'up',         cpu: 3,  mem: '96 MB / 512 MB', ports: '6379→6379' },
  { id: 'c6', name: 'qdrant-memory',    image: 'qdrant/qdrant:v1.12.4',                 status: 'up',         cpu: 12, mem: '2.4 GB / 6 GB', ports: '6333→6333' },
  { id: 'c7', name: 'vllm-qwen3-coder', image: 'vllm/vllm-openai:v0.9.2',               status: 'up',         cpu: 88, mem: '41 GB / 48 GB', ports: '8000→8000' },
  { id: 'c8', name: 'vllm-bge-m3',      image: 'vllm/vllm-openai:v0.9.2',               status: 'up',         cpu: 17, mem: '5.1 GB / 8 GB', ports: '8002→8000' },
  { id: 'c9', name: 'erp-nginx',        image: 'nginx:1.27-alpine',                     status: 'up',         cpu: 2,  mem: '48 MB / 256 MB', ports: '80,443' },
  { id: 'c10', name: 'sonarqube',       image: 'sonarqube:10.6-community',              status: 'restarting', cpu: 71, mem: '3.8 GB / 4 GB', ports: '9000→9000' },
  { id: 'c11', name: 'erp-legacy-rpt',  image: 'ghcr.io/sofscript/erp-rpt:2.9.14',      status: 'up',         cpu: 9,  mem: '1.1 GB / 2 GB', ports: '5088→8080' },
  { id: 'c12', name: 'playwright-grid', image: 'mcr.microsoft.com/playwright:v1.49.1',  status: 'down',       cpu: 0,  mem: '0 MB / 3 GB', ports: '4444→4444' },
  { id: 'c13', name: 'otel-collector',  image: 'otel/opentelemetry-collector:0.115.1',  status: 'up',         cpu: 8,  mem: '320 MB / 1 GB', ports: '4317→4317' },
  { id: 'c14', name: 'hims-api',        image: 'ghcr.io/sofscript/hims-api:3.2.7',      status: 'up',         cpu: 26, mem: '1.4 GB / 4 GB', ports: '5090→8080' },
];

export const pipeline: PipelineStage[] = [
  { id: 'p1', name: 'checkout',    state: 'pass',    durationS: 4,   detail: 'task/invoice-tax-backend @ 9f3ac21 · 2.4M lines, sparse' },
  { id: 'p2', name: 'restore',     state: 'pass',    durationS: 21,  detail: 'nuget 148 packages · cache hit 94%' },
  { id: 'p3', name: 'build',       state: 'pass',    durationS: 74,  detail: 'dotnet build -c Release · 0 errors, 2 warnings' },
  { id: 'p4', name: 'unit',        state: 'pass',    durationS: 41,  detail: '184 passed · 1 skipped (BUG-702)' },
  { id: 'p5', name: 'integration', state: 'pass',    durationS: 118, detail: '62 passed against erp_ci snapshot 2026-09-09' },
  { id: 'p6', name: 'package',     state: 'pass',    durationS: 57,  detail: 'erp-api:3.14.2-rc5 pushed · 412 MB' },
  { id: 'p7', name: 'scan',        state: 'running', durationS: 33,  detail: 'trivy + gitleaks · 61% of layers' },
  { id: 'p8', name: 'deploy',      state: 'queued',  durationS: 0,   detail: 'staging slot B · waits on scan' },
];

export const pipelineMeta = {
  ref: 'CI-2211',
  trigger: 'push · task/invoice-tax-backend',
  by: 'Backend Engineer',
  runner: 'self-hosted · ryzen-7950x · docker',
  startedAt: 'today 14:19:02',
  elapsed: '5m 48s',
};

export const logs: LogLine[] = [
  { id: 'l1',  t: '14:22:41.118', level: 'info',  source: 'erp-api',    text: 'Application started. Hosting environment: Staging' },
  { id: 'l2',  t: '14:22:41.402', level: 'debug', source: 'erp-api',    text: 'Registered IJurisdictionResolver -> JurisdictionResolver (Scoped)' },
  { id: 'l3',  t: '14:22:43.907', level: 'ok',    source: 'mssql-2019', text: 'Migration 0042_backfill_tax_jurisdiction applied to erp_staging in 3.2s' },
  { id: 'l4',  t: '14:22:44.010', level: 'info',  source: 'mssql-2019', text: '41 rows written to TRANS_INVOICE_ADJ (reason_code = TAXJUR)' },
  { id: 'l5',  t: '14:22:51.663', level: 'warn',  source: 'erp-api',    text: 'MST_TAX rate_code=GST05 returned state_code NULL, falling back to home state 27' },
  { id: 'l6',  t: '14:23:02.284', level: 'info',  source: 'erp-worker', text: 'Ledger recompute window 2026-04-01..2026-09-10 queued (job 88214)' },
  { id: 'l7',  t: '14:23:09.771', level: 'err',   source: 'sonarqube',  text: 'OutOfMemoryError: Java heap space — analysing 1148 stored procedures' },
  { id: 'l8',  t: '14:23:10.004', level: 'warn',  source: 'docker',     text: 'Container sonarqube exited with code 137, restart policy: always' },
  { id: 'l9',  t: '14:23:18.550', level: 'info',  source: 'erp-api',    text: 'GET /api/invoices/INV-2026-08812 200 in 41ms' },
  { id: 'l10', t: '14:23:18.552', level: 'ok',    source: 'erp-api',    text: 'Tax recalc INV-2026-08812: IGST 1809.00 (was CGST 904.50 / SGST 904.50)' },
  { id: 'l11', t: '14:23:26.119', level: 'debug', source: 'otel',       text: 'exported 214 spans to tempo in 88ms' },
  { id: 'l12', t: '14:23:31.844', level: 'warn',  source: 'erp-api',    text: 'Session InProc will drop on recycle — TASK-501 not in this build' },
  { id: 'l13', t: '14:23:40.207', level: 'info',  source: 'vllm',       text: 'Qwen3-Coder-Next: 1 request, 3184 prompt tok, 812 gen tok, 61.4 tok/s' },
  { id: 'l14', t: '14:23:47.900', level: 'err',   source: 'erp-legacy-rpt', text: 'Crystal report GSTR1_B2CS.rpt failed: field {TRANS_INVOICE.igst} not found' },
  { id: 'l15', t: '14:23:48.115', level: 'warn',  source: 'erp-legacy-rpt', text: 'Legacy report pack still reads the pre-ADR-52 column set — TASK-476 follow-up' },
  { id: 'l16', t: '14:24:01.330', level: 'info',  source: 'nginx',      text: '10.14.2.71 - - "GET /invoices?state=29 HTTP/1.1" 200 18412 "-" 0.112' },
  { id: 'l17', t: '14:24:12.687', level: 'ok',    source: 'ci',         text: 'stage package complete: erp-api:3.14.2-rc5 (412 MB) pushed to ghcr.io' },
  { id: 'l18', t: '14:24:19.004', level: 'info',  source: 'ci',         text: 'stage scan started: trivy image + gitleaks detect --no-git' },
  { id: 'l19', t: '14:24:33.512', level: 'warn',  source: 'trivy',      text: 'MEDIUM CVE-2025-21938 in System.Text.Json 8.0.4 (transitive, no fix yet)' },
  { id: 'l20', t: '14:24:44.271', level: 'info',  source: 'redis',      text: 'session keyspace: 1,884 keys, 96 MB, evicted 0' },
  { id: 'l21', t: '14:24:52.006', level: 'err',   source: 'erp-api',    text: 'SqlException 1205: deadlock on TRANS_INVOICE_ADJ, victim job 88214, retry 1/3' },
  { id: 'l22', t: '14:24:53.118', level: 'ok',    source: 'erp-worker', text: 'job 88214 retry succeeded in 1.9s' },
  { id: 'l23', t: '14:25:06.440', level: 'debug', source: 'qdrant',     text: 'collection erp_memory: 41,882 points, hnsw ef=128, search p95 11ms' },
  { id: 'l24', t: '14:25:14.909', level: 'info',  source: 'erp-api',    text: 'POST /api/invoices/recalc-batch 202 in 88ms (41 invoices queued)' },
  { id: 'l25', t: '14:25:22.771', level: 'warn',  source: 'docker',     text: 'playwright-grid is down; visual-diff stage will be skipped' },
  { id: 'l26', t: '14:25:35.118', level: 'ok',    source: 'health',     text: '/health/ready 200 · db ok · redis ok · qdrant ok · vllm ok' },
];

export const secrets: Secret[] = [
  { id: 's1',  name: 'ERP_SQL_CONNECTION_PROD',   scope: 'erp',    store: 'Vault kv/erp/prod',   lastRotated: '2026-08-14', rotationDays: 90, readers: ['DevOps Engineer', 'You'], usedBy: 'erp-api, erp-worker' },
  { id: 's2',  name: 'ERP_SQL_CONNECTION_STAGING', scope: 'erp',   store: 'Vault kv/erp/staging', lastRotated: '2026-09-01', rotationDays: 90, readers: ['DevOps Engineer', 'Database Engineer', 'You'], usedBy: 'erp-api (staging)' },
  { id: 's3',  name: 'GHCR_PUSH_TOKEN',           scope: 'global', store: 'Vault kv/ci',         lastRotated: '2026-07-29', rotationDays: 60, readers: ['DevOps Engineer'], usedBy: 'CI package stage' },
  { id: 's4',  name: 'IIS_MACHINE_KEY',           scope: 'erp',    store: 'Vault kv/erp/prod',   lastRotated: '2025-11-02', rotationDays: 365, readers: ['DevOps Engineer', 'You'], usedBy: 'IIS nodes web01-03' },
  { id: 's5',  name: 'GSTN_API_CLIENT_SECRET',    scope: 'erp',    store: 'Vault kv/erp/prod',   lastRotated: '2026-06-18', rotationDays: 180, readers: ['You'], usedBy: 'Gstr1ExportService' },
  { id: 's6',  name: 'SMTP_RELAY_PASSWORD',       scope: 'global', store: 'Vault kv/shared',     lastRotated: '2026-05-03', rotationDays: 120, readers: ['DevOps Engineer', 'You'], usedBy: 'invoice mailer, alerting' },
  { id: 's7',  name: 'QDRANT_API_KEY',            scope: 'global', store: '.env.local (host)',   lastRotated: '2026-09-02', rotationDays: 90, readers: ['DevOps Engineer', 'Memory Brain'], usedBy: 'qdrant-memory' },
  { id: 's8',  name: 'HIMS_PMI_READONLY_DSN',     scope: 'hims',   store: 'Vault kv/hims/prod',  lastRotated: '2026-08-30', rotationDays: 90, readers: ['Database Engineer', 'You'], usedBy: 'hims-api legacy bridge' },
  { id: 's9',  name: 'RIDE_STRIPE_RESTRICTED',    scope: 'ride',   store: 'Vault kv/ride',       lastRotated: '2026-09-05', rotationDays: 30, readers: ['You'], usedBy: 'ride payments service' },
  { id: 's10', name: 'OPENROUTER_FALLBACK_KEY',   scope: 'global', store: 'Vault kv/models',     lastRotated: '2026-08-21', rotationDays: 60, readers: ['DevOps Engineer', 'Model Router'], usedBy: 'remote model fallback' },
  { id: 's11', name: 'SONARQUBE_TOKEN',           scope: 'global', store: 'Vault kv/ci',         lastRotated: '2026-04-11', rotationDays: 90, readers: ['DevOps Engineer'], usedBy: 'CI scan stage' },
  { id: 's12', name: 'ERP_BACKUP_ENCRYPTION_KEY', scope: 'erp',    store: 'HSM slot 2',          lastRotated: '2026-01-09', rotationDays: 365, readers: ['You'], usedBy: 'nightly TRANS_* backup' },
];

export const infraKv: { k: string; v: string }[] = [
  { k: 'Orchestrator host', v: 'ryzen-7950x · 128 GB · RTX 4090 24 GB' },
  { k: 'Container runtime', v: 'Docker 27.4.1 · compose v2.31' },
  { k: 'Prod topology', v: '3× IIS 10 (web01-03) behind ARR, sticky off' },
  { k: 'Prod database', v: 'SQL Server 2019 CU27 · AG erp-ag01 (sync)' },
  { k: 'Backup', v: 'Full 01:00, diff every 4h, log every 15m · 41 d retained' },
  { k: 'CI runner', v: 'self-hosted · 2 concurrent jobs · avg queue 11s' },
  { k: 'Registry', v: 'ghcr.io/sofscript · 214 images · 88 GB' },
  { k: 'Observability', v: 'otel-collector → tempo + loki + prometheus' },
  { k: 'Alert routing', v: 'prod page → you · staging → daily digest' },
  { k: 'Deploy window', v: 'Mon–Thu 22:00–23:30 IST · never on filing days' },
  { k: 'Local model serving', v: 'vLLM 0.9.2 · Qwen3-Coder-Next + BGE-M3' },
  { k: 'Frozen surfaces', v: 'TRANS_* tables append-only · Crystal report pack v2.9.x' },
];

export const healthChecks: HealthCheck[] = [
  { id: 'h1', name: 'api /health/ready',      env: 'PRODUCTION', target: 'erp.careworks.co.in/health/ready', status: 'ok',   latencyMs: 38,   lastRun: '18s ago', note: 'db · redis · gstn reachable' },
  { id: 'h2', name: 'api /health/live',       env: 'PRODUCTION', target: 'web01-03 /health/live',            status: 'ok',   latencyMs: 11,   lastRun: '18s ago', note: '3/3 nodes' },
  { id: 'h3', name: 'sql AG sync state',      env: 'PRODUCTION', target: 'erp-ag01',                          status: 'ok',   latencyMs: 4,    lastRun: '1 min ago', note: 'synchronized, redo queue 0 KB' },
  { id: 'h4', name: 'session affinity probe', env: 'PRODUCTION', target: 'ARR → web01-03',                    status: 'warn', latencyMs: 62,   lastRun: '2 min ago', note: 'InProc sessions lost on recycle — TASK-501' },
  { id: 'h5', name: 'GSTN sandbox reachability', env: 'PRODUCTION', target: 'api.gst.gov.in',                 status: 'ok',   latencyMs: 411,  lastRun: '5 min ago', note: 'slow but inside SLA' },
  { id: 'h6', name: 'api /health/ready',      env: 'STAGING',    target: 'erp-staging.sofscript.internal',    status: 'ok',   latencyMs: 44,   lastRun: '26s ago', note: 'rc4 build' },
  { id: 'h7', name: 'migration drift check',  env: 'STAGING',    target: 'erp_staging',                        status: 'warn', latencyMs: 88,   lastRun: '9 min ago', note: '0042 applied here, not on prod' },
  { id: 'h8', name: 'crystal report pack',    env: 'STAGING',    target: 'erp-legacy-rpt',                     status: 'down', latencyMs: 0,    lastRun: '4 min ago', note: 'GSTR1_B2CS.rpt missing {igst} field' },
  { id: 'h9', name: 'vllm /v1/models',        env: 'LOCAL',      target: 'localhost:8000',                     status: 'ok',   latencyMs: 7,    lastRun: '12s ago', note: 'Qwen3-Coder-Next loaded' },
  { id: 'h10', name: 'qdrant collections',    env: 'LOCAL',      target: 'localhost:6333',                     status: 'ok',   latencyMs: 9,    lastRun: '12s ago', note: '6 collections, 41,882 points' },
  { id: 'h11', name: 'playwright grid',       env: 'LOCAL',      target: 'localhost:4444',                     status: 'down', latencyMs: 0,    lastRun: '3 min ago', note: 'container stopped after OOM' },
];
