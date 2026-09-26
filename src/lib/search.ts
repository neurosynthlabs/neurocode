import type { BrainstormDoc, Catalogue, CodeHit, RunDoc } from '@/lib/api';
import { ICONS } from '@/lib/icons';
import type { McpServer, MemoryFact, Plan, Project, SearchHit, Task } from '@/types';
import { NAV, allowed } from './nav';

const navIcon = (to: string, fallback: string) => NAV.find((n) => n.to === to)?.icon ?? fallback;
const sentence = (s: string) => s.split(/(?<=[.!?])\s/)[0].slice(0, 90);

/** The parts of the store, and of the catalogue, that ⌘K searches. */
export interface Indexable {
  projects: Project[]; tasks: Task[]; plans: Plan[]; runs: RunDoc[]; memory: MemoryFact[]; brainstorms: BrainstormDoc[];
  mcp: McpServer[];
  /** The agent roster from GET /auth/catalogue: names and roles, nothing measured. */
  agents: Catalogue['agents'];
}

/**
 * Everything ⌘K can find without asking the server again. Built from the live store, so a plan compiled
 * a minute ago or a project onboarded just now is searchable at once. Hits that name one record open it
 * through `?ref=`. Symbols in the code index are a query of their own: see `codeHits`.
 */
export function buildIndex(
  { projects, tasks, plans, runs, memory, brainstorms, mcp, agents }: Indexable,
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
      subtitle: [p.stack.slice(0, 3).join(' · ') || p.status, p.repo].filter(Boolean).join(' · '),
      to: `/projects/${p.id}`, icon: 'FolderKanban', meta: p.codename,
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
    ...runs.map<SearchHit>((r) => ({
      id: `run-${r.id}`, group: 'Runs', title: `${r.ref} · ${sentence(r.requirement)}`,
      subtitle: `${r.projectName} · ${r.branch} · ${r.status}`, to: `/runs?ref=${r.ref}`,
      icon: navIcon('/runs', 'Activity'), meta: r.status,
    })),
    // An archived fact is out of recall and off the Memory screen, so a hit for it would open to nothing.
    ...memory.filter((f) => !f.archived).map<SearchHit>((f) => ({
      id: `mem-${f.id}`, group: 'Memory', title: `${f.ref} · ${f.title}`,
      subtitle: `${f.category.replace('_', ' ')} · ${f.confidence.toLowerCase()} confidence`, to: `/memory?ref=${f.ref}`,
      icon: 'Brain', meta: f.pinned ? 'pinned' : f.category,
    })),
    ...brainstorms.map<SearchHit>((b) => ({
      id: `brief-${b.id}`, group: 'Brainstorms', title: `${b.ref} · ${b.brief.title}`,
      subtitle: sentence(b.idea), to: `/brainstorm?ref=${b.ref}`, icon: navIcon('/brainstorm', 'Lightbulb'),
    })),
    ...mcp.map<SearchHit>((m) => ({
      id: `mcp-${m.id}`, group: 'Tools', title: m.name,
      subtitle: `${m.transport} · ${m.tools.length} tools · ${m.status.replace('_', ' ')}`, to: '/mcp', icon: navIcon('/mcp', 'Server'), meta: m.scope,
    })),
    ...agents.map<SearchHit>((a) => ({
      id: `agent-${a.id}`, group: 'Agents', title: a.name, subtitle: a.role, to: '/agents',
      icon: a.icon in ICONS ? a.icon : 'Bot',
    })),
  ];
}

/** Symbols the server found in a project's code index for one query, each opening its file. */
export function codeHits(projectName: string, hits: CodeHit[]): SearchHit[] {
  return hits.map((h) => ({
    id: `code-${h.path}:${h.line}:${h.name}`, group: 'Code', title: h.name,
    subtitle: `${h.path}:${h.line} · ${projectName}`, to: `/code?path=${encodeURIComponent(h.path)}`,
    icon: 'FileCode', meta: h.kind,
  }));
}

export const SEARCH_GROUPS = [
  'Navigate', 'Tasks', 'Plans', 'Runs', 'Memory', 'Code', 'Brainstorms', 'Tools', 'Agents', 'Projects',
];
