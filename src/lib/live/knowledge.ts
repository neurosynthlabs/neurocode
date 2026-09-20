import { request, type RetrievalHit, type RetrievalState } from '@/lib/api';
import type { KnowledgeDoc, MemoryCategory, MemoryConflict } from '@/types';

/* The Knowledge screen's live shapes: the repository's own writing, as retrieval holds it. The kind is
   the file's extension — nothing classifies documents — and the summary is the document's own first
   paragraph, never a written one. */

export type DocFormat = 'md' | 'mdx' | 'rst' | 'txt' | 'adoc';

export interface LiveDoc extends Omit<KnowledgeDoc, 'kind' | 'size' | 'addedAt'> {
  kind: DocFormat;
  /** ISO: the last commit that touched it, or the file's modified time when git has none. */
  addedAt: string | null;
  bytes: number;
  /** Changed on disk since the index was built, or gone from disk. */
  stale: boolean;
  embedded: number;
  /** Empty in the list; filled by GET …/retrieval/doc. */
  sections: { ref: string; title: string; embedded: boolean }[];
}

export interface LiveDocs {
  retrieval: Omit<RetrievalState, 'q' | 'results'>;
  docs: LiveDoc[];
  onDisk: number;
  notIndexed: number;
  stale: number;
  entitiesLinked: number;
  formats: string[];
  pipeline: { n: number; step: string; detail: string }[];
}

/** The retrieval summary, and whether a build is running right now — as the code summary reports `indexing`. */
export interface RetrievalStatus extends RetrievalState {
  building: boolean;
}

export interface CountedSearch extends RetrievalState {
  results: RetrievalHit[];
  /** Present when a question was asked: what the words matched, what meaning compared, what survived. */
  /** `lexicalCap` is where the word count stops counting; `k` is the reciprocal-rank constant. */
  /** `dropped` is what the near-duplicate rule skipped to fill these slots; `floored` is how many of the
      results shown are too far from the question to be handed to a model as an answer to it. */
  counts?: {
    lexical: number;
    semantic: number;
    fused: number;
    lexicalCap: number;
    k: number;
    dropped: number;
    floored: number;
  };
}

const seg = encodeURIComponent;

export const knowledgeApi = {
  docs: (pid: string) => request<LiveDocs>(`/projects/${seg(pid)}/code/retrieval/docs`, { signal: AbortSignal.timeout(30_000) }),
  doc: (pid: string, path: string) =>
    request<LiveDoc>(`/projects/${seg(pid)}/code/retrieval/doc?path=${seg(path)}`, { signal: AbortSignal.timeout(15_000) }),
  /** What retrieval holds, what it finds for `q`, and whether a build is running. */
  retrieval: (pid: string, q = '') =>
    request<RetrievalStatus>(`/projects/${seg(pid)}/code/retrieval?q=${seg(q)}`, { signal: AbortSignal.timeout(30_000) }),
  search: (pid: string, q: string, limit = 20) =>
    request<CountedSearch>(`/projects/${seg(pid)}/code/retrieval?q=${seg(q)}&limit=${limit}`, { signal: AbortSignal.timeout(30_000) }),
};

/* ── Memory ───────────────────────────────────────────────────────
   The categories are the server's own vocabulary (routes_knowledge.Category); only the words for them
   live here. A recall is one row the server wrote when a feature cited a fact or handed it to a model. */

export const MEMORY_CATEGORIES: { id: MemoryCategory; label: string }[] = [
  { id: 'human', label: 'Human' },
  { id: 'preferences', label: 'Preferences' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'business_rules', label: 'Business Rules' },
  { id: 'legacy', label: 'Legacy' },
  { id: 'database', label: 'Database' },
  { id: 'decisions', label: 'Decisions' },
  { id: 'incidents', label: 'Incidents' },
  { id: 'project', label: 'Project' },
  { id: 'bugs', label: 'Bugs' },
  { id: 'code', label: 'Code' },
];

export const categoryLabel = (id: MemoryCategory) => MEMORY_CATEGORIES.find((c) => c.id === id)?.label ?? id;

export type RecallFeature = 'ask' | 'chat' | 'retrieval' | 'research' | 'compile';

export const RECALLED_BY: Record<RecallFeature, string> = {
  ask: 'Ask memory', chat: 'Session tool', retrieval: 'Session grounding', research: 'Research', compile: 'Compiler',
};

export interface Recall {
  ref: string;
  title: string;
  feature: RecallFeature;
  /** The session, research or plan it was recalled for; null for a question asked of memory. */
  context: string | null;
  at: string;
}

export interface ConflictInput { a: string; b: string; topic: string; detail: string; severity: 'low' | 'medium' | 'high' }

/** One category's share of what memory holds, counted by the server. */
export interface CategoryCount { held: number; pinned: number; recalled24h: number; lastUsedAt: string | null }

/** Memory's figures, counted by the database — the fact list is a page, so counting it undercounts. */
export interface MemoryStats {
  held: number;
  pinned: number;
  /** Held facts that belong to no project: the global brain. */
  global: number;
  recalled24h: number;
  /** Archived within the last `retiredDays` days. */
  retired: number;
  retiredDays: number;
  byCategory: Partial<Record<MemoryCategory, CategoryCount>>;
}

export const memoryApi = {
  hits: (limit = 50) => request<Recall[]>(`/memory/hits?limit=${limit}`),
  /** Counted for a scope: 'global' is the workspace's own facts, a project id adds that project's; none is everything. */
  stats: (project?: string) =>
    request<MemoryStats>(`/memory/stats${project ? `?project=${seg(project)}` : ''}`, { signal: AbortSignal.timeout(15_000) }),
  fileConflict: (input: ConflictInput) => request<MemoryConflict>('/memory/conflicts', { method: 'POST', json: input }),
};
