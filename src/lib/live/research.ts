import { request } from '@/lib/api';

/* The Research screen's live data: GET/POST /research. A report is written as it runs, so the screen
   reads it again while it is queued or running. "confidence" is cited coverage — the share of angles
   whose finding cites a piece it was handed — never a model's opinion of itself. */

export type ResearchStatus = 'queued' | 'running' | 'complete' | 'failed' | 'cancelled';
/** The chunk kinds retrieval holds. The web and GitHub have no fetcher, so they are not among them. */
export type ResearchKind = 'code' | 'doc' | 'memory';

export interface ResearchListItem {
  id: string;
  ref: string;
  question: string;
  status: ResearchStatus;
  createdAt: string;
  /** Seconds from start to finish; 0 until it has finished. */
  durationS: number;
  /** How many angles the question was split into. */
  agents: number;
  /** Cited coverage, 0–100; 0 until complete. */
  confidence: number;
  projectId: string;
  requestedBy: string;
}

export interface ResearchCitation {
  label: string;
  /** The piece's ref: `pkg/tax.py#apply_gst:118`, `README.md#2`, `MEM-512`. */
  url: string;
  via: 'internal' | 'docs' | 'memory';
  kind: ResearchKind;
  path: string;
  line: number;
  excerpt: string;
}

export interface ResearchAngle {
  n: number;
  question: string;
  status: 'todo' | 'running' | 'waiting' | 'done' | 'failed' | 'skipped';
  hits: number;
  lexical: number;
  semantic: number;
  finding: string;
  lane: string | null;
  model: string | null;
  ms: number | null;
  error: string;
  cited: number;
}

export interface ResearchDoc extends ResearchListItem {
  startedAt: string | null;
  finishedAt: string | null;
  projectHint: string;
  kinds: ResearchKind[];
  sources: { kind: 'code' | 'docs' | 'memory'; count: number }[];
  summary: string;
  recommendation: string;
  architecture: string;
  risks: string[];
  alternatives: { name: string; pros: string[]; cons: string[]; verdict: string }[];
  findings: string[];
  citations: ResearchCitation[];
  sweep: { angle: string; hits: number }[];
  angles: ResearchAngle[];
  gaps: string[];
  /** The lane that wrote the synthesis, or `rules`. */
  provider: string;
  model: string;
  fallback: string;
  /** Why it stopped, when it failed or was stopped. */
  note: string;
}

const seg = encodeURIComponent;

export const fetchResearch = (project: string) =>
  request<ResearchListItem[]>(`/research?${new URLSearchParams({ project, limit: '50' })}`);
export const fetchReport = (ref: string) => request<ResearchDoc>(`/research/${seg(ref)}`);
export const startResearch = (question: string, projectId: string, kinds: ResearchKind[]) =>
  request<ResearchListItem>('/research', { method: 'POST', json: { question, projectId, kinds } });
export const stopResearch = (ref: string) => request<ResearchListItem>(`/research/${seg(ref)}/cancel`, { method: 'POST' });
