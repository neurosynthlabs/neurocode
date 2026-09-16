import { request, type RetrievalHit, type RetrievalState } from '@/lib/api';
import type { KnowledgeDoc } from '@/types';

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

export interface CountedSearch extends RetrievalState {
  results: RetrievalHit[];
  /** Present when a question was asked: what the words matched, what meaning compared, what survived. */
  /** `lexicalCap` is where the word count stops counting; `k` is the reciprocal-rank constant. */
  counts?: { lexical: number; semantic: number; fused: number; lexicalCap: number; k: number };
}

const seg = encodeURIComponent;

export const knowledgeApi = {
  docs: (pid: string) => request<LiveDocs>(`/projects/${seg(pid)}/code/retrieval/docs`, { signal: AbortSignal.timeout(30_000) }),
  doc: (pid: string, path: string) =>
    request<LiveDoc>(`/projects/${seg(pid)}/code/retrieval/doc?path=${seg(path)}`, { signal: AbortSignal.timeout(15_000) }),
  search: (pid: string, q: string, limit = 20) =>
    request<CountedSearch>(`/projects/${seg(pid)}/code/retrieval?q=${seg(q)}&limit=${limit}`, { signal: AbortSignal.timeout(30_000) }),
};
