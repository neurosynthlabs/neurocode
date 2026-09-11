import type { McpServer, MemoryFact, Plan, Project, SearchHit, Task } from '@/types';
import { NAV, allowed } from './nav';
import { agents } from '@/mock/agents';

/** Curated hits for what the index cannot read yet: code, database objects, bugs, commits, tests. */
const domainHits: SearchHit[] = [
  { id: 's-code-1',  group: 'Code',         title: 'InvoiceService.cs',            subtitle: 'src/Services/Billing · 412 LOC · churn HIGH', to: '/code',         icon: 'FileCode', meta: 'C#' },
  { id: 's-code-2',  group: 'Code',         title: 'TaxService.cs',                subtitle: 'src/Services/Billing · used by 4 modules',    to: '/code',         icon: 'FileCode', meta: 'C#' },
  { id: 's-code-3',  group: 'Code',         title: 'InvoiceTaxSummary.tsx',        subtitle: 'src/features/invoice · React component',      to: '/code',         icon: 'FileCode', meta: 'TSX' },
  { id: 's-db-1',    group: 'Database',     title: 'SP_CalculateTax',              subtitle: 'stored procedure · 412 lines · 11 callers',   to: '/code',         icon: 'Database', meta: 'SPROC' },
  { id: 's-db-2',    group: 'Database',     title: 'MST_TAX',                      subtitle: 'master table · 2.1M rows · 6 indexes',        to: '/code',         icon: 'Table2',   meta: 'TABLE' },
  { id: 's-db-3',    group: 'Database',     title: 'TRANS_INVOICE',                subtitle: 'append-only · never modify directly',         to: '/code',         icon: 'Table2',   meta: 'TABLE' },
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

const navIcon = (to: string, fallback: string) => NAV.find((n) => n.to === to)?.icon ?? fallback;
const sentence = (s: string) => s.split(/(?<=[.!?])\s/)[0].slice(0, 90);

/** The parts of the store that ⌘K searches. */
export interface Indexable { projects: Project[]; tasks: Task[]; plans: Plan[]; memory: MemoryFact[]; mcp: McpServer[] }

/**
 * Everything ⌘K can find. Built from the live store, so a plan compiled a minute ago or a project
 * onboarded just now is searchable at once. Hits that name one record open it through `?ref=`.
 */
export function buildIndex(
  { projects, tasks, plans, memory, mcp }: Indexable,
  /** `useAuth().canAny`: screens a role cannot open are left out. */
  canAny: (...perms: string[]) => boolean = () => true,
): SearchHit[] {
  return [
    ...NAV.filter((n) => allowed(n, canAny)).map<SearchHit>((n) => ({
      id: `nav-${n.to}`, group: 'Navigate', title: n.label,
      subtitle: n.keywords.split(' ').slice(0, 6).join(' · '), to: n.to, icon: n.icon,
    })),
    ...projects.map<SearchHit>((p) => ({
      id: `proj-${p.id}`, group: 'Projects', title: p.name,
      subtitle: `${p.stack.slice(0, 3).join(' · ') || p.status} · ${p.modules} modules`, to: `/projects/${p.id}`, icon: 'FolderKanban', meta: p.codename,
    })),
    ...tasks.map<SearchHit>((t) => ({
      id: `task-${t.id}`, group: 'Tasks', title: `${t.ref} · ${t.title}`,
      subtitle: `${t.status.replace('_', ' ')} · ${t.priority} · ${t.layers.join(', ')}`, to: `/tasks?ref=${t.ref}`, icon: 'ListChecks', meta: t.risk,
    })),
    ...plans.map<SearchHit>((p) => ({
      id: `plan-${p.id}`, group: 'Plans', title: `${p.ref} · ${sentence(p.rawRequirement)}`,
      subtitle: `${p.taskRef} · ${p.steps.length} steps · ${p.openQuestions.length} open questions`, to: `/plans?ref=${p.ref}`,
      icon: navIcon('/plans', 'ListChecks'), meta: p.risk,
    })),
    ...memory.map<SearchHit>((f) => ({
      id: `mem-${f.id}`, group: 'Memory', title: `${f.ref} · ${f.title}`,
      subtitle: `${f.category.replace('_', ' ')} · ${f.confidence.toLowerCase()} confidence · strength ${f.strength}`, to: `/memory?ref=${f.ref}`,
      icon: 'Brain', meta: f.pinned ? 'PINNED' : f.category.toUpperCase(),
    })),
    ...mcp.map<SearchHit>((m) => ({
      id: `mcp-${m.id}`, group: 'Tools', title: m.name,
      subtitle: `${m.transport} · ${m.tools.length} tools · ${m.status.replace('_', ' ')}`, to: '/mcp', icon: navIcon('/mcp', 'Server'), meta: m.scope.toUpperCase(),
    })),
    ...agents.map<SearchHit>((a) => ({
      id: `agent-${a.id}`, group: 'Agents', title: a.name,
      subtitle: `${a.role} · ${a.model}`, to: '/agents', icon: 'Bot', meta: a.status,
    })),
    ...domainHits,
  ];
}

export const SEARCH_GROUPS = [
  'Navigate', 'Tasks', 'Plans', 'Memory', 'Code', 'Database', 'Decisions',
  'Bugs', 'Requirements', 'Knowledge', 'Commits', 'Tests', 'Tools', 'Agents', 'Projects',
];
