import type { SearchHit } from '@/types';
import { NAV } from './nav';
import { projects } from '@/mock/projects';
import { agents } from '@/mock/agents';
import { tasks } from '@/mock/tasks';

/** Curated cross-domain hits — the "one search box for everything" promise. */
const domainHits: SearchHit[] = [
  { id: 's-code-1',  group: 'Code',         title: 'InvoiceService.cs',            subtitle: 'src/Services/Billing · 412 LOC · churn HIGH', to: '/code',         icon: 'FileCode', meta: 'C#' },
  { id: 's-code-2',  group: 'Code',         title: 'TaxService.cs',                subtitle: 'src/Services/Billing · used by 4 modules',    to: '/code',         icon: 'FileCode', meta: 'C#' },
  { id: 's-code-3',  group: 'Code',         title: 'InvoiceTaxSummary.tsx',        subtitle: 'src/features/invoice · React component',      to: '/code',         icon: 'FileCode', meta: 'TSX' },
  { id: 's-db-1',    group: 'Database',     title: 'SP_CalculateTax',              subtitle: 'stored procedure · 412 lines · 11 callers',   to: '/code',         icon: 'Database', meta: 'SPROC' },
  { id: 's-db-2',    group: 'Database',     title: 'MST_TAX',                      subtitle: 'master table · 2.1M rows · 6 indexes',        to: '/code',         icon: 'Table2',   meta: 'TABLE' },
  { id: 's-db-3',    group: 'Database',     title: 'TRANS_INVOICE',                subtitle: 'append-only · never modify directly',         to: '/code',         icon: 'Table2',   meta: 'TABLE' },
  { id: 's-mem-1',   group: 'Memory',       title: 'MEM-142 · TRANS tables are append-only', subtitle: 'Architecture decision · HIGH confidence', to: '/memory', icon: 'Brain',   meta: 'DECISION' },
  { id: 's-mem-2',   group: 'Memory',       title: 'MEM-311 · Interstate tax uses place-of-supply', subtitle: 'Business rule · learned 14:07 today', to: '/memory', icon: 'Brain', meta: 'RULE' },
  { id: 's-mem-3',   group: 'Memory',       title: 'MEM-088 · PM writes requirements in Hinglish', subtitle: 'Human preference · always parse both', to: '/memory', icon: 'Brain', meta: 'HUMAN' },
  { id: 's-bug-1',   group: 'Bugs',         title: 'BUG-883 · CGST/SGST reversed interstate', subtitle: 'open · 41 invoices affected',       to: '/tasks',        icon: 'Bug',      meta: 'OPEN' },
  { id: 's-bug-2',   group: 'Bugs',         title: 'BUG-991 · Excel export times out >100k', subtitle: 'triaged · TASK-513',                 to: '/tasks',        icon: 'Bug',      meta: 'TRIAGED' },
  { id: 's-req-1',   group: 'Requirements', title: 'REQ-291 · Customer bulk upload',   subtitle: 'Knowledge · linked to TASK-488',           to: '/knowledge',    icon: 'FileText', meta: 'REQ' },
  { id: 's-req-2',   group: 'Requirements', title: 'REQ-238 · Invoice tax correctness', subtitle: 'Knowledge · linked to TASK-492',          to: '/knowledge',    icon: 'FileText', meta: 'REQ' },
  { id: 's-adr-1',   group: 'Decisions',    title: 'ADR-42 · One repository per aggregate', subtitle: 'accepted · 2026-05-11',               to: '/memory',       icon: 'Scale',    meta: 'ADR' },
  { id: 's-adr-2',   group: 'Decisions',    title: 'ADR-52 · Single jurisdiction resolver', subtitle: 'draft · Documentation Agent',         to: '/memory',       icon: 'Scale',    meta: 'ADR' },
  { id: 's-com-1',   group: 'Commits',      title: 'a82f91c · fix(tax): resolve jurisdiction by place of supply', subtitle: 'Backend Engineer · 12 min ago', to: '/git', icon: 'GitCommit', meta: 'COMMIT' },
  { id: 's-com-2',   group: 'Commits',      title: '4d0e17b · test(tax): interstate matrix',  subtitle: 'QA Engineer · 8 min ago',           to: '/git',          icon: 'GitCommit', meta: 'COMMIT' },
  { id: 's-test-1',  group: 'Tests',        title: 'InvoiceTaxTest',                 subtitle: '18 cases · all passing',                     to: '/testing',      icon: 'FlaskConical', meta: 'PASS' },
  { id: 's-test-2',  group: 'Tests',        title: 'InvoiceTaxLegacyTest',           subtitle: 'failing · legacy-expected, do not "fix"',     to: '/testing',      icon: 'FlaskConical', meta: 'LEGACY' },
  { id: 's-mtg-1',   group: 'Knowledge',    title: 'Meeting 2026-08-21 · Billing review', subtitle: 'transcript · 38 min · 7 facts extracted', to: '/knowledge',   icon: 'Mic',      meta: 'MEETING' },
];

export function buildIndex(): SearchHit[] {
  return [
    ...NAV.map<SearchHit>((n) => ({
      id: `nav-${n.to}`, group: 'Navigate', title: n.label,
      subtitle: n.keywords.split(' ').slice(0, 6).join(' · '), to: n.to, icon: n.icon,
    })),
    ...projects.map<SearchHit>((p) => ({
      id: `proj-${p.id}`, group: 'Projects', title: p.name,
      subtitle: `${p.stack.slice(0, 3).join(' · ')} · ${p.modules} modules`, to: `/projects`, icon: 'FolderKanban', meta: p.codename,
    })),
    ...tasks.map<SearchHit>((t) => ({
      id: `task-${t.id}`, group: 'Tasks', title: `${t.ref} · ${t.title}`,
      subtitle: `${t.status.replace('_', ' ')} · ${t.priority} · ${t.layers.join(', ')}`, to: '/tasks', icon: 'ListChecks', meta: t.risk,
    })),
    ...agents.map<SearchHit>((a) => ({
      id: `agent-${a.id}`, group: 'Agents', title: a.name,
      subtitle: `${a.role} · ${a.model}`, to: '/agents', icon: 'Bot', meta: a.status,
    })),
    ...domainHits,
  ];
}

export const SEARCH_GROUPS = [
  'Navigate', 'Tasks', 'Code', 'Database', 'Memory', 'Decisions',
  'Bugs', 'Requirements', 'Knowledge', 'Commits', 'Tests', 'Agents', 'Projects',
];
