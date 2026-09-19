import { request } from '@/lib/api';

/* Taste: how this team likes the work done, learnt from what it did. Signals are the moments — a signature
   accepted or refused, a run sent back with notes, a plan's steps edited, a person's own commits on a run's
   branch. "Learn from my decisions" asks a model to propose rules from the signals nobody has read; a rule's
   support and contradictions are the signals it cites, counted by the server. Only an adopted rule is handed
   to the compiler, the agents and the reviewer. */

export type TasteKind = 'accept' | 'refuse' | 'rework_note' | 'edit_delta' | 'plan_edit';
export type RuleStatus = 'proposed' | 'active' | 'retired';

export interface TasteRule {
  id: number;
  /** TASTE-n: how a plan or a run names it where it records what it was handed. */
  ref: string;
  /** Null for a rule of the whole workspace. */
  projectId: string | null;
  text: string;
  status: RuleStatus;
  /** Signals cited for it, and against it — counted, never a model's number. */
  support: number;
  contradict: number;
  /** How many signals it cites. */
  evidence: number;
  /** The lane and model that proposed it. */
  proposedBy: string | null;
  adoptedBy: string | null;
  adoptedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TasteSignal {
  id: number;
  kind: TasteKind;
  projectId: string | null;
  /** What happened, in a line. */
  summary: string;
  /** What it was built from: the run, the notes, a commit's patch, a step before and after. */
  payload: Record<string, unknown>;
  /** Read by a distillation already. */
  distilled: boolean;
  at: string;
  by: string | null;
  /** On a rule's evidence: whether it was cited for it or against it. */
  stance?: 'for' | 'against';
}

export interface SignalCounts {
  total: number;
  unread: number;
  byKind: Record<TasteKind, number>;
}

export interface RuleList {
  items: TasteRule[];
  counts: Record<RuleStatus, number>;
  signals: SignalCounts;
}

export interface Learnt {
  /** Signals the model was handed. */
  read: number;
  /** Moments gathered into signals just before, from decisions the runtime had recorded. */
  harvested: number;
  model: string;
  proposed: TasteRule[];
  /** Rules already listed that the new signals bore on. */
  weighed: TasteRule[];
}

export const SIGNAL_LABEL: Record<TasteKind, string> = {
  accept: 'Accepted a run',
  refuse: 'Refused a run',
  rework_note: 'Sent a run back',
  edit_delta: 'Edited an agent’s work',
  plan_edit: 'Shaped a plan',
};

const seg = encodeURIComponent;
/** Learning asks a model, so it waits as long as a compile does. */
const modelTimeout = () => AbortSignal.timeout(180_000);
/** A project's own view with the workspace's, or "workspace" for the workspace's alone. */
const scope = (projectId: string | null) => `project=${seg(projectId ?? 'workspace')}`;

export const tasteApi = {
  rules: (projectId: string | null) => request<RuleList>(`/taste/rules?${scope(projectId)}&limit=200`),
  signals: (projectId: string | null, limit = 50) =>
    request<TasteSignal[]>(`/taste/signals?${scope(projectId)}&limit=${limit}`),
  evidence: (id: number) => request<{ rule: TasteRule; signals: TasteSignal[] }>(`/taste/rules/${id}/evidence`),
  learn: (projectId: string | null) =>
    request<Learnt>('/taste/learn', { method: 'POST', json: { projectId }, signal: modelTimeout() }),
  change: (id: number, change: { text?: string; status?: 'active' | 'retired' }) =>
    request<TasteRule>(`/taste/rules/${id}`, { method: 'PATCH', json: change }),
};

/** taste.md: the adopted rules as a file a repository can keep — written here, in the browser. */
export function tasteMarkdown(rules: TasteRule[], scopeName: string): string {
  const active = rules.filter((r) => r.status === 'active');
  const lines = [
    `# Taste — ${scopeName}`,
    '',
    'How this team likes the work done: rules adopted from its own decisions in NeuroCode.',
    `Exported ${new Date().toISOString().slice(0, 10)} · ${active.length} rule${active.length === 1 ? '' : 's'}.`,
    '',
    ...active.map((r) => `- ${r.text} _(${r.ref} · ${r.support} for, ${r.contradict} against${r.projectId ? '' : ' · workspace'})_`),
  ];
  return `${lines.join('\n')}\n`;
}
