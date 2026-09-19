import { request } from '@/lib/api';
import type { Plan, Project } from '@/types';

/* The Blueprint wizard against the local API: the technology catalogue and template bank (shipped with
   NeuroCode, not sample data), a person's blueprints with their answers and architecture, a model's review
   that only proposes, finalizing into a document, a diagram and decisions in memory, and scaffolding into a
   new folder through the normal plan → run → review → signature path. */

export type TechCategory =
  | 'frontend' | 'mobile' | 'backend' | 'language' | 'database' | 'cache' | 'queue' | 'search' | 'auth'
  | 'ai-ml' | 'data' | 'infra' | 'ci-cd' | 'observability' | 'testing' | 'hosting';

export interface Tech {
  id: string;
  name: string;
  category: TechCategory;
  languages: string[];
  kind: string;
  maturity: string;
  license: string;
  pairsWith: string[];
  notes: string;
}

export interface Question {
  id: string;
  kind: 'text' | 'one' | 'many';
  question: string;
  hint?: string;
  options?: { id: string; label: string }[];
}

export interface LayerInfo { id: string; label: string; category: TechCategory }

export type Answers = Record<string, string | string[]>;

export interface Condition { answer: string; is: string[]; says: string }
export interface Fit {
  /** Conditions met in `fitsWhen`, minus those met in `avoidWhen`. */
  score: number;
  fits: { says: string; answer: string; values: string[] }[];
  against: { says: string; answer: string; values: string[] }[];
  /** How many of its conditions the answers spoke to at all. */
  considered: number;
  conditions: number;
}

export interface TemplateSummary {
  id: string;
  name: string;
  summary: string;
  source: 'catalogue' | 'mine';
  layers: Record<string, string | null>;
  services: number;
  repos: number;
  fitsWhen: Condition[];
  avoidWhen: Condition[];
  fit?: Fit;
  description?: string;
  createdBy?: string | null;
  createdById?: string | null;
  createdAt?: string | null;
}

export interface Layer { choice: string | null; alternatives: string[]; why: string }
export interface Service {
  name: string;
  kind: string;
  tech: string;
  responsibilities: string[];
  talksTo: string[];
}
export interface DataStore { name: string; tech: string; purpose: string }
export interface Environment { name: string; purpose: string; hosting: string }
export interface Adr { title: string; decision: string; why: string; alternatives: string[] }
export interface ScaffoldFile { path: string; template: string }
export interface ScaffoldRepo { label: string; layout: string; files: ScaffoldFile[] }

export interface Architecture {
  summary: string;
  layers: Record<string, Layer>;
  services: Service[];
  dataStores: DataStore[];
  environments: Environment[];
  ci: { tool: string; stages: { name: string; does: string }[] };
  infra: { kind: string; tools: string[]; notes: string[] };
  observability: { tools: string[]; notes: string[] };
  security: { auth: string; secrets: string; notes: string[] };
  folderLayout: string;
  conventions: string[];
  adrs: Adr[];
  scaffold: { repos: ScaffoldRepo[] };
}

export interface TemplateDoc {
  id: string;
  name: string;
  summary: string;
  source: 'catalogue' | 'mine';
  fitsWhen: Condition[];
  avoidWhen: Condition[];
  spec: Architecture;
  diagram: string;
  description: string | null;
  createdBy: string | null;
  createdById: string | null;
  createdAt: string | null;
}

export interface Change {
  id: string;
  /** A place in the architecture: `layers.backend.choice`, `services[api].talksTo`, `services[+]`. */
  path: string;
  /** What is there now, read by the server — not what the model quoted. null when the place is new. */
  from: unknown;
  /** null removes it. */
  to: unknown;
  why: string;
}

export interface Review {
  summary: string;
  changes: Change[];
  /** Proposals left out because they did not fit the blueprint, with why. */
  dropped: string[];
  revision: number;
  model: string;
  provider: string;
  ms: number;
  at: string;
  by: string;
  decided: Record<string, 'accepted' | 'rejected'>;
}

export interface Final {
  document: string;
  diagram: string;
  /** The memory facts (decisions) written for it. */
  facts: string[];
  revision: number;
  at: string;
  by: string;
  /** The architecture changed after the document was written. */
  stale: boolean;
}

export interface ScaffoldRecord {
  projectId: string;
  planRef: string;
  taskRef: string;
  folder: string;
  repos: { label: string; path: string }[];
  at: string;
  by: string;
}

export type BlueprintStatus = 'draft' | 'final' | 'scaffolded';

export interface BlueprintDoc {
  id: string;
  name: string;
  template: string | null;
  templateName: string | null;
  status: BlueprintStatus;
  revision: number;
  answers: Answers;
  spec: Architecture;
  diagram: string;
  checks: { path: string; problem: string }[];
  review: Review | null;
  final: Final | null;
  scaffolded: ScaffoldRecord | null;
  projectId: string | null;
  projectName: string | null;
  createdBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface BlueprintRow {
  id: string;
  name: string;
  template: string | null;
  templateName: string | null;
  status: BlueprintStatus;
  revision: number;
  summary: string;
  idea: string;
  layers: Record<string, string>;
  services: number;
  reviewed: boolean;
  projectId: string | null;
  projectName: string | null;
  createdBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface Catalogue {
  tech: Tech[];
  templates: TemplateSummary[];
  mine: TemplateSummary[];
  mineTotal: number;
  questions: Question[];
  layers: LayerInfo[];
}

export interface ExportDoc { filename: string; mime: string; text: string }

export interface Scaffolded {
  blueprint: BlueprintDoc;
  project: Project;
  plan: Plan;
  planRef: string;
  taskRef: string;
}

/** A model call or a scaffold can take a while; the default 5 s would cut it off. A fresh signal per call. */
const slow = () => ({ signal: AbortSignal.timeout(180_000) });

export const blueprintsApi = {
  catalogue: () => request<Catalogue>('/blueprints/catalogue'),
  rank: (answers: Answers) =>
    request<{ catalogue: TemplateSummary[]; mine: TemplateSummary[] }>('/blueprints/rank', { method: 'POST', json: { answers } }),
  list: (limit = 100, offset = 0) =>
    request<{ items: BlueprintRow[]; total: number; limit: number; offset: number }>(`/blueprints?limit=${limit}&offset=${offset}`),
  get: (id: string) => request<BlueprintDoc>(`/blueprints/${encodeURIComponent(id)}`),
  create: (name: string, templateId: string | null, answers: Answers) =>
    request<BlueprintDoc>('/blueprints', { method: 'POST', json: { name, templateId, answers } }),
  save: (id: string, expectRevision: number, patch: { name?: string; answers?: Answers; spec?: Architecture }) =>
    request<BlueprintDoc>(`/blueprints/${encodeURIComponent(id)}`, { method: 'PATCH', json: { expectRevision, ...patch } }),
  remove: (id: string) => request<{ ok: boolean }>(`/blueprints/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  suggest: (id: string, expectRevision: number) =>
    request<BlueprintDoc>(`/blueprints/${encodeURIComponent(id)}/suggest`, { method: 'POST', json: { expectRevision }, ...slow() }),
  apply: (id: string, expectRevision: number, changes: Change[], rejected: string[]) =>
    request<BlueprintDoc>(`/blueprints/${encodeURIComponent(id)}/apply`, { method: 'POST', json: { expectRevision, changes, rejected } }),
  finalize: (id: string, expectRevision: number) =>
    request<BlueprintDoc>(`/blueprints/${encodeURIComponent(id)}/finalize`, { method: 'POST', json: { expectRevision }, ...slow() }),
  exportDoc: (id: string, format: 'json' | 'yaml') =>
    request<ExportDoc>(`/blueprints/${encodeURIComponent(id)}/export?format=${format}`),
  importDoc: (text: string, kind?: 'blueprint' | 'template') =>
    request<{ kind: 'blueprint'; blueprint: BlueprintDoc } | { kind: 'template'; template: TemplateDoc }>(
      '/blueprints/import', { method: 'POST', json: { text, kind } }),
  template: (id: string) => request<TemplateDoc>(`/blueprints/templates/${encodeURIComponent(id)}`),
  exportTemplate: (id: string, format: 'json' | 'yaml') =>
    request<ExportDoc>(`/blueprints/templates/${encodeURIComponent(id)}/export?format=${format}`),
  saveTemplate: (blueprintId: string, name: string, description: string) =>
    request<TemplateDoc>('/blueprints/templates', { method: 'POST', json: { blueprintId, name, description } }),
  removeTemplate: (id: string) => request<{ ok: boolean }>(`/blueprints/templates/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  scaffold: (id: string, folder: string, repos?: string[]) =>
    request<Scaffolded>(`/blueprints/${encodeURIComponent(id)}/scaffold`, { method: 'POST', json: { folder, repos }, ...slow() }),
};

export function download(doc: ExportDoc): void {
  const url = URL.createObjectURL(new Blob([doc.text], { type: doc.mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = doc.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const STATUS_TONE = { draft: 'neutral', final: 'ok', scaffolded: 'brand' } as const;
export const STATUS_LABEL: Record<BlueprintStatus, string> = { draft: 'Draft', final: 'Finalized', scaffolded: 'Scaffolded' };

export const SERVICE_KINDS = ['web', 'api', 'worker', 'gateway', 'function', 'job', 'cli', 'library', 'service', 'pipeline',
  'desktop', 'mobile', 'static', 'realtime'] as const;
export const INFRA_KINDS = ['compose', 'kubernetes', 'serverless', 'paas', 'static', 'vm', 'desktop', 'package', 'none'] as const;

/** A technology's name, or the text a person typed when it is not in the catalogue. */
export function techName(byId: Map<string, { name: string }>, id: string | null | undefined): string {
  if (!id) return '—';
  return byId.get(id)?.name ?? id;
}

/** The Mermaid text for an architecture — the same drawing the server writes when it finalizes, made here
    so the editor's diagram follows every unsaved change. Kept in step with `diagram()` in
    server/app/services/blueprints.py. */
export function toMermaid(arch: Architecture, byId: Map<string, { name: string }>, layerLabel: (id: string) => string, layerOrder: string[]): string {
  const node = (prefix: string, name: string) => prefix + name.replace(/[^A-Za-z0-9_]/g, '_');
  const label = (t: string) => t.replace(/"/g, '#quot;');
  const lines = ['flowchart LR'];
  const services = arch.services ?? [];
  const stores = arch.dataStores ?? [];
  if (!services.length && !stores.length) {
    const chosen = Object.entries(arch.layers ?? {})
      .filter(([, v]) => v.choice)
      .sort(([a], [b]) => layerOrder.indexOf(a) - layerOrder.indexOf(b));
    if (!chosen.length) return 'flowchart LR\n  empty["Nothing chosen yet"]';
    lines.push('  subgraph layers ["Layers"]');
    chosen.forEach(([k, v]) => lines.push(`    ${node('l_', k)}["${label(layerLabel(k))} · ${label(techName(byId, v.choice))}"]`));
    lines.push('  end');
    return lines.join('\n');
  }
  const names = new Map<string, string>();
  services.forEach((s) => names.set(s.name, node('s_', s.name)));
  stores.forEach((d) => names.set(d.name, node('d_', d.name)));
  const groups: [string, string, string[]][] = [
    ['clients', 'Clients', ['web', 'mobile', 'desktop', 'static', 'cli']],
    ['edge', 'Edge', ['gateway']],
    ['services', 'Services', ['api', 'service', 'realtime', 'function', 'library']],
    ['workers', 'Workers and pipelines', ['worker', 'job', 'pipeline']],
  ];
  const placed = new Set<string>();
  for (const [key, title, kinds] of groups) {
    const members = services.filter((s) => kinds.includes(s.kind));
    if (!members.length) continue;
    lines.push(`  subgraph ${key} ["${title}"]`);
    members.forEach((s) => {
      lines.push(`    ${names.get(s.name)}["${label(s.name)} · ${label(techName(byId, s.tech))}"]`);
      placed.add(s.name);
    });
    lines.push('  end');
  }
  services.filter((s) => !placed.has(s.name)).forEach((s) => lines.push(`  ${names.get(s.name)}["${label(s.name)} · ${label(techName(byId, s.tech))}"]`));
  if (stores.length) {
    lines.push('  subgraph data ["Data"]');
    stores.forEach((d) => lines.push(`    ${names.get(d.name)}[("${label(d.name)} · ${label(techName(byId, d.tech))}")]`));
    lines.push('  end');
  }
  services.forEach((s) => (s.talksTo ?? []).forEach((t) => { if (names.has(t)) lines.push(`  ${names.get(s.name)} --> ${names.get(t)}`); }));
  return lines.join('\n');
}
