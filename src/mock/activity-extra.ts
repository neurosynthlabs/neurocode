import type { ActivityEvent } from '@/types';

/* Earlier today + the two previous days. `activity` in ./activity.ts holds
   the live tail; this is the scrollback behind it. */
type Row = [string, string, ActivityEvent['actorKind'], string, string, string, ActivityEvent['level'], string?];

const rows: Row[] = [
  // ── today, morning ──────────────────────────────────────────────
  ['07:02:11', 'System',              'system', 'Session started',        'Nightly index complete · 2.4M lines · 12 min', 'erp',  'info'],
  ['07:02:14', 'Hook',                'hook',   'SessionStart fired',     'load-project-rules.sh → 10 rules attached',    'erp',  'ok'],
  ['07:04:40', 'Memory Brain',        'system', 'Decay pass',             '31 facts weakened · 6 retired · 14 pinned skipped', 'erp', 'info'],
  ['07:30:12', 'You',                 'human',  'Requirement submitted',  '"Memory ko human ki tarah decay hona chahiye"', 'aios', 'info', 'TASK-509'],
  ['07:31:02', 'Architect',           'agent',  'Half-life model drafted','Per-category decay, incidents never fully forgotten', 'aios', 'ok', 'TASK-509'],
  ['08:02:03', 'You',                 'human',  'Requirement submitted',  '"Users randomly logout ho rahe hain 20 min baad"', 'erp', 'warn', 'TASK-501'],
  ['08:02:40', 'AI Commander',        'agent',  'Escalated to URGENT',    'Production impact detected in the requirement text', 'erp', 'warn', 'TASK-501'],
  ['08:06:18', 'DevOps Engineer',     'agent',  'Correlation found',      '214 forced logouts, all within 4s of an app-pool recycle', 'erp', 'ok', 'TASK-501'],
  ['08:19:55', 'Backend Engineer',    'agent',  'Config read',            'web.config mode="InProc" on 3 of 3 nodes, no F5 affinity', 'erp', 'warn', 'TASK-501'],
  ['08:44:02', 'DevOps Engineer',     'agent',  'Staging validated',      '400 sessions survived a forced recycle · zero drops', 'erp', 'ok', 'TASK-501'],
  ['08:45:10', 'Security Engineer',   'agent',  'Blocked merge',          'Production session config needs APPR-118',     'erp',  'err',  'TASK-501'],
  ['09:12:04', 'Backend Engineer',    'agent',  'Agent started',          'Qwen3-Coder-Next · worktree task/invoice-tax-backend', 'erp', 'ok', 'TASK-492'],
  ['09:26:31', 'Database Engineer',   'agent',  'Stored proc mapped',     'SP_CalculateTax — jurisdiction derived twice (L288, L341)', 'erp', 'warn', 'TASK-492'],
  ['09:48:12', 'MCP: sqlserver',      'tool',   'Query executed',         'SELECT COUNT(*) FROM TRANS_INVOICE WHERE ... → 41 rows', 'erp', 'info'],
  ['10:05:22', 'You',                 'human',  'Requirement submitted',  '"OPD bill mein 1 rupee ka difference aa raha hai"', 'hims', 'info', 'TASK-503'],
  ['10:09:48', 'Architect',           'agent',  'Root cause found',       'Two rounding sites: MidpointRounding.ToEven vs T-SQL ROUND()', 'hims', 'ok', 'TASK-503'],
  ['10:40:03', 'Documentation Agent', 'agent',  'ADR drafted',            'ADR-51 — stored procedures are renamed, never dropped', 'erp', 'info'],
  ['11:02:19', 'Eval Runner',         'system', 'Nightly evals',          '10 suites · 8 pass · 2 regressions flagged',   'aios', 'warn'],
  ['11:14:50', 'Self-Improvement',    'system', 'Lesson learned',         'Reviewer missed a repository-pattern break → rule added to solid-enforcer', 'aios', 'ok'],
  ['11:40:07', 'You',                 'human',  'Requirement submitted',  '"Surge pricing chahiye, per zone, 2.5x cap"',  'taxi', 'info', 'TASK-506'],
  ['11:41:30', 'Researcher',          'agent',  'Research started',       'RES-77 · 4 parallel search angles',            'taxi', 'info'],
  ['12:15:44', 'AI Commander',        'agent',  'Task created',           'TASK-512 — inventory reorder point recalculation', 'erp', 'info'],
  ['12:20:02', 'QA Engineer',         'agent',  'Bug triaged',            'BUG-991 Excel export times out past ~100k rows → TASK-513', 'erp', 'warn'],
  ['12:41:18', 'Architect',           'agent',  'Deprecation queued',     '3 dead tax procedures → TASK-514 per ADR-51',  'erp',  'info'],
  ['12:55:31', 'You',                 'human',  'Requirement submitted',  '"Zed se direct agents chalane hain"',          'aios', 'info', 'TASK-521'],
  ['13:02:44', 'AI Commander',        'agent',  'Task created',           'TASK-517 — pathology report auto-attach to IPD file', 'hims', 'info'],
  ['13:20:09', 'Cost Guard',          'system', 'Route forced local',     'Simple extraction → Qwen3-4B · saved $0.18',   'erp',  'ok'],
  ['13:38:52', 'Frontend Engineer',   'agent',  'Blocked',                'papaparse@5.4.1 needs APPR-120 before the upload UI can proceed', 'erp', 'warn', 'TASK-488'],

  // ── yesterday ───────────────────────────────────────────────────
  ['y 09:14:02', 'You',               'human',  'Requirement submitted',  '"Customer bulk upload chahiye, CSV se, rollback ke saath"', 'erp', 'info', 'TASK-488'],
  ['y 09:18:40', 'Researcher',        'agent',  'Import paths surveyed',  '3 ad-hoc importers found, none transactional',  'erp',  'warn', 'TASK-488'],
  ['y 09:45:11', 'You',               'human',  'Requirement submitted',  '"Discharge summary ke department-wise templates"', 'hims', 'info', 'TASK-519'],
  ['y 10:31:27', 'Architect',         'agent',  'ADR accepted',           'ADR-49 — staging table for all bulk imports',   'erp',  'ok'],
  ['y 11:02:55', 'Database Engineer', 'agent',  'Migration written',      '0043_stg_customer_import.sql + rollback verified', 'erp', 'ok', 'TASK-488'],
  ['y 13:40:18', 'Backend Engineer',  'agent',  'Endpoint shipped',       'POST /customers/bulk — chunked, idempotent, resumable', 'erp', 'ok', 'TASK-488'],
  ['y 15:22:06', 'Code Reviewer',     'agent',  'Changes requested',      'Hand-rolled CSV parser rejected — reinventing a solved problem', 'erp', 'warn', 'TASK-488'],
  ['y 16:04:33', 'QA Engineer',       'agent',  'Regression detected',    'Reports grid export lost the XLSX sheet name',  'erp',  'err',  'TASK-479'],
  ['y 16:44:12', 'Frontend Engineer', 'agent',  'Regression fixed',       'Sheet name restored, visual diff back to 0.0%', 'erp',  'ok',   'TASK-479'],
  ['y 17:40:50', 'Architect',         'agent',  'ADR accepted',           'ADR-51 — deprecation policy for stored procedures', 'erp', 'ok'],
  ['y 18:12:04', 'DevOps Engineer',   'agent',  'Deployed to staging',    'v3.14.1 · 88s · health green',                  'taxi', 'ok'],
  ['y 19:30:41', 'Memory Brain',      'system', 'Conflict detected',      'Two facts disagree on SEZ jurisdiction source',  'erp',  'warn'],

  // ── two days ago ────────────────────────────────────────────────
  ['d2 09:02:10', 'You',              'human',  'Project switched',       'HIMS v3 — memory, rules and agents swapped',    'hims', 'info'],
  ['d2 10:14:55', 'Architect',        'agent',  'Onboarding complete',    'HIMS v3 understood 74% · 31 modules · 214 tables', 'hims', 'ok'],
  ['d2 11:48:22', 'Security Engineer','agent',  'Secret scan clean',      '0 findings across 1.1M lines',                   'hims', 'ok'],
  ['d2 14:20:36', 'You',              'human',  'Approved',               'APPR-113 — HIMS staging deploy',                 'hims', 'ok'],
  ['d2 15:02:19', 'DevOps Engineer',  'agent',  'Deployed to staging',    'v2.9.4 · 104s · 1 health check flapped, recovered', 'hims', 'warn'],
  ['d2 16:41:07', 'Documentation Agent','agent','Changelog generated',    '38 commits summarised into 9 operator-facing lines', 'hims', 'info'],
  ['d2 17:55:44', 'Workflow',         'system', 'Workflow complete',      'wf-db-contract-sweep · 9 agents · 214 tables · 12 dead procedures', 'hims', 'ok'],
  ['d2 18:30:12', 'Eval Runner',      'system', 'Eval improved',          'hinglish-parsing 88 → 93 after 6 new labelled cases', 'aios', 'ok'],
];

export const activityExtra: ActivityEvent[] = rows.map(([t, actor, actorKind, action, detail, projectId, level, taskRef], i) => ({
  id: `x${i}`, t, actor, actorKind, action, detail, projectId, level, taskRef,
}));

export const dayOf = (t: string) => (t.startsWith('d2 ') ? '2 days ago' : t.startsWith('y ') ? 'Yesterday' : 'Today');
export const timeOf = (t: string) => t.replace(/^(y|d2) /, '');
