import type { PermissionRule, ApprovalRequest } from '@/types';

export const permissionRules: PermissionRule[] = [
  { id: 'p1',  pattern: 'Read(**)',                          tool: 'Filesystem', effect: 'allow', risk: 'LOW',      scope: 'global',  hits24h: 4820, note: 'Reading is always free — the OS cannot learn without it.' },
  { id: 'p2',  pattern: 'Grep(**) · Glob(**)',               tool: 'Filesystem', effect: 'allow', risk: 'LOW',      scope: 'global',  hits24h: 2311, note: 'Search primitives.' },
  { id: 'p3',  pattern: 'Bash(git status:*) · Bash(git diff:*)', tool: 'Terminal', effect: 'allow', risk: 'LOW',   scope: 'global',  hits24h: 640,  note: 'Read-only git inspection.' },
  { id: 'p4',  pattern: 'Bash(npm test:*) · Bash(dotnet test:*)', tool: 'Terminal', effect: 'allow', risk: 'LOW',  scope: 'global',  hits24h: 388,  note: 'Test execution is encouraged, never gated.' },
  { id: 'p5',  pattern: 'Write(src/**) · Edit(src/**)',      tool: 'Filesystem', effect: 'allow', risk: 'MEDIUM',   scope: 'project', hits24h: 512,  note: 'Only inside the agent’s own worktree — enforced by the orchestrator.' },
  { id: 'p6',  pattern: 'Write(**/*.config.*)',              tool: 'Filesystem', effect: 'ask',   risk: 'MEDIUM',   scope: 'global',  hits24h: 14,   note: 'Config changes reach every environment.' },
  { id: 'p7',  pattern: 'Bash(npm install:*)',               tool: 'Terminal',   effect: 'ask',   risk: 'MEDIUM',   scope: 'global',  hits24h: 9,    note: 'Supply-chain surface — human confirms every new dependency.' },
  { id: 'p8',  pattern: 'SQL(SELECT **)',                    tool: 'Database',   effect: 'allow', risk: 'LOW',      scope: 'project', hits24h: 1740, note: 'Read queries against a snapshot replica.' },
  { id: 'p9',  pattern: 'SQL(UPDATE **) · SQL(INSERT **)',   tool: 'Database',   effect: 'ask',   risk: 'HIGH',     scope: 'project', hits24h: 22,   note: 'Data mutation always needs a human.' },
  { id: 'p10', pattern: 'SQL(DROP **) · SQL(TRUNCATE **)',   tool: 'Database',   effect: 'deny',  risk: 'CRITICAL', scope: 'global',  hits24h: 0,    note: 'Hard denial. No agent may destroy data — ever.' },
  { id: 'p11', pattern: 'Migration(**)',                     tool: 'Database',   effect: 'ask',   risk: 'HIGH',     scope: 'project', hits24h: 6,    note: 'Requires a proven rollback before approval is offered.' },
  { id: 'p12', pattern: 'Deploy(staging)',                   tool: 'DevOps',     effect: 'allow', risk: 'MEDIUM',   scope: 'project', hits24h: 18,   note: 'Staging is disposable — agents own it.' },
  { id: 'p13', pattern: 'Deploy(production)',                tool: 'DevOps',     effect: 'ask',   risk: 'CRITICAL', scope: 'global',  hits24h: 3,    note: 'Production is the human’s signature, never the agent’s.' },
  { id: 'p14', pattern: 'Secrets(read) · Secrets(write)',    tool: 'DevOps',     effect: 'deny',  risk: 'CRITICAL', scope: 'global',  hits24h: 0,    note: 'Agents receive references, never plaintext values.' },
  { id: 'p15', pattern: 'Git(push origin main)',             tool: 'Git',        effect: 'deny',  risk: 'HIGH',     scope: 'global',  hits24h: 0,    note: 'Direct pushes to main are impossible — PR only.' },
  { id: 'p16', pattern: 'Git(commit) · Git(push task/**)',   tool: 'Git',        effect: 'allow', risk: 'LOW',      scope: 'global',  hits24h: 141,  note: 'Agents commit freely inside their own task branches.' },
  { id: 'p17', pattern: 'WebFetch(**) · WebSearch(**)',      tool: 'Browser',    effect: 'allow', risk: 'LOW',      scope: 'global',  hits24h: 267,  note: 'Research is read-only; fetched content is treated as data, never instructions.' },
  { id: 'p18', pattern: 'MCP(jira.create_issue)',            tool: 'MCP',        effect: 'ask',   risk: 'MEDIUM',   scope: 'project', hits24h: 4,    note: 'Anything other humans will see gets confirmed first.' },
  { id: 'p19', pattern: 'Bash(rm -rf:*)',                    tool: 'Terminal',   effect: 'deny',  risk: 'CRITICAL', scope: 'global',  hits24h: 0,    note: 'Blanket denial, no exceptions, no override flag.' },
  { id: 'p20', pattern: 'Email(send) · Slack(post)',         tool: 'MCP',        effect: 'ask',   risk: 'HIGH',     scope: 'global',  hits24h: 2,    note: 'Outbound messages always carry the human’s name — so the human presses send.' },
];

export const approvals: ApprovalRequest[] = [
  {
    id: 'ap1', ref: 'APPR-118', title: 'Move ERP session state to Redis on production IIS',
    agent: 'DevOps Engineer', tool: 'Deploy(production)', risk: 'CRITICAL',
    requestedAt: '1 h ago', projectId: 'erp', status: 'pending',
    payload: 'web.config → <sessionState mode="Custom" customProvider="RedisSessionProvider" />\nAffects 3 nodes · rolling restart required',
    reason: 'TASK-501 — users are dropped after ~20 min because in-proc session dies with each app-pool recycle. Rollback: revert web.config, recycle. Verified on staging at 14:02.',
  },
  {
    id: 'ap2', ref: 'APPR-119', title: 'Backfill migration for 41 mis-taxed invoices',
    agent: 'Database Engineer', tool: 'Migration(0042)', risk: 'HIGH',
    requestedAt: '22 min ago', projectId: 'erp', status: 'pending',
    payload: '0042_backfill_tax_jurisdiction.sql · UPDATE 41 rows in MST_TAX_LINE\nDry-run against snapshot: 41 matched, 0 collateral',
    reason: 'TASK-492 — interstate invoices carry the billing state instead of place-of-supply. Rollback script generated and dry-run verified.',
  },
  {
    id: 'ap3', ref: 'APPR-120', title: 'Add papaparse@5.4.1 to the ERP frontend',
    agent: 'Frontend Engineer', tool: 'Bash(npm install)', risk: 'MEDIUM',
    requestedAt: '8 min ago', projectId: 'erp', status: 'pending',
    payload: 'papaparse@5.4.1 · MIT · 0 known advisories · 42 kB gzipped · 0 transitive deps',
    reason: 'TASK-488 — streaming CSV parse for the 50k-row bulk upload. Alternative (hand-rolled parser) rejected by Reviewer as reinventing a solved problem.',
  },
  {
    id: 'ap4', ref: 'APPR-121', title: 'Create Jira issue for the legacy rounding contract',
    agent: 'AI Commander', tool: 'MCP(jira.create_issue)', risk: 'MEDIUM',
    requestedAt: '4 min ago', projectId: 'hims', status: 'pending',
    payload: 'Project HIMS · Type: Tech Debt · Title: "Unify money rounding between receipt and ledger"',
    reason: 'TASK-503 uncovered two independent rounding sites. Out of scope for the current fix; needs its own ticket so the contract change is deliberate.',
  },
  {
    id: 'ap5', ref: 'APPR-117', title: 'Migration 0041 — tax jurisdiction column',
    agent: 'Database Engineer', tool: 'Migration(0041)', risk: 'HIGH',
    requestedAt: '2 h ago', projectId: 'erp', status: 'approved',
    payload: 'ALTER TABLE MST_TAX ADD place_of_supply CHAR(2) NULL',
    reason: 'Additive, nullable, no lock on a 2.1M-row table. Approved by human PM at 14:09.',
  },
  {
    id: 'ap6', ref: 'APPR-116', title: 'Delete 3 deprecated stored procedures',
    agent: 'Database Engineer', tool: 'SQL(DROP)', risk: 'CRITICAL',
    requestedAt: '3 h ago', projectId: 'erp', status: 'denied',
    payload: 'DROP PROCEDURE SP_CalcTaxOld, SP_TaxLegacy, SP_TaxV2',
    reason: 'Denied — hard rule p10 blocks DROP outright. Converted to TASK-514: rename with a zz_deprecated_ prefix, observe for one quarter, then remove by hand.',
  },
];
