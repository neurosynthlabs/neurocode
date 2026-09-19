import { request, type LaneId } from '@/lib/api';
import type { AgentSession } from '@/lib/live/agents';
import type { Plan, Task } from '@/types';

/* Reviews on demand: any diff read the way a run's review step reads a run — a branch against its base
   (fetched first when it was pushed elsewhere), the working tree, or a range of commits. The reading
   happens on the server in the background; the finished review lands in the activity feed and on the
   stream, and GET /reviews/{ref} reads it back. */

export type ReviewTarget = 'branch' | 'working-tree' | 'commit-range';
export type ReviewStatus = 'running' | 'done' | 'failed';

export interface ReviewFinding {
  severity: 'HIGH' | 'MEDIUM' | 'LOW';
  /** As the diff names it; empty when the finding is about the change as a whole. */
  file: string;
  /** In the new version of the file; absent when the reviewer named no line. */
  line?: number;
  note: string;
}

export interface CodeReview {
  id: string;
  ref: string;
  projectId: string;
  projectName: string;
  /** A further source's label; empty for the project's first. */
  source: string;
  target: ReviewTarget;
  base: string;
  head: string;
  /** "feature against main", "the working tree (changes not committed yet)", "commits a..b". */
  title: string;
  status: ReviewStatus;
  /** The reviewer's one sentence — or, for a review that failed, why. */
  verdict: string;
  findings: ReviewFinding[];
  /** `sha256:<hex>` of the exact patch that was read. */
  fingerprint: string | null;
  stats: {
    files?: number; insertions?: number; deletions?: number; commits?: number; untracked?: number;
    bytes?: number; truncated?: boolean; baseSha?: string; headSha?: string | null; fetched?: string | null;
  };
  paths: { path: string; insertions: number; deletions: number }[];
  /** The repository's REVIEW.md the reviewer was handed. */
  brief: { path: string; bytes: number } | null;
  instructions: { path: string; bytes: number }[];
  taste: string[];
  /** The run whose commits these are, when one of ours wrote them — its lane is the one the reviewer avoided. */
  writer: { run: string; lane: LaneId | null } | null;
  /** The sessions and plans made from it. */
  sent: { kind: 'session' | 'plan'; ref: string; by: string; at: string }[];
  lane: LaneId | null;
  model: string | null;
  /** Read by rules, because no model could answer. */
  offline: boolean;
  requestedBy: string | null;
  createdAt: string | null;
  finishedAt: string | null;
}

export interface ReviewTargets {
  projectId: string;
  source: string;
  sources: { label: string; name: string; primary: boolean; ready: boolean }[];
  /** False when the checkout is not a git repository. */
  git: boolean;
  branches?: { name: string; remote: boolean; sha: string; at: string | null; subject: string; current: boolean }[];
  current?: string | null;
  /** Paths that differ from the last commit, new files included. */
  dirty?: number;
  remotes?: string[];
  brief?: { path: string; bytes: number } | null;
}

export interface ReviewRequest {
  target: ReviewTarget;
  base?: string;
  head?: string;
  source?: string;
  /** Fetch `head` from its remote first: a branch someone else pushed. */
  fetch?: boolean;
}

export interface ReviewPage { reviews: CodeReview[]; total: number; more: boolean; nextOffset: number | null }

const seg = encodeURIComponent;

export const reviewsApi = {
  targets: (projectId: string, source = '') =>
    request<ReviewTargets>(`/projects/${seg(projectId)}/review/targets${source ? `?source=${seg(source)}` : ''}`,
      { signal: AbortSignal.timeout(15_000) }),
  list: (projectId: string, offset = 0) =>
    request<ReviewPage>(`/projects/${seg(projectId)}/reviews?limit=50&offset=${offset}`),
  one: (ref: string) => request<CodeReview>(`/reviews/${seg(ref)}`),
  // Both ends are checked with git before the answer; a fetch, when asked for, happens with the reading.
  ask: (projectId: string, body: ReviewRequest) =>
    request<CodeReview>(`/projects/${seg(projectId)}/review`, { method: 'POST', json: body, signal: AbortSignal.timeout(30_000) }),
  toSession: (ref: string) => request<AgentSession>(`/reviews/${seg(ref)}/session`, { method: 'POST' }),
  /** A compile: it waits on a model, so it is given a model's time. */
  toPlan: (ref: string) =>
    request<Plan & { task: Task }>(`/reviews/${seg(ref)}/plan`, { method: 'POST', signal: AbortSignal.timeout(120_000) }),
};
