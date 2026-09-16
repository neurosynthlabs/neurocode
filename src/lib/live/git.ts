import { request, type RunDoc, type RunStatus } from '@/lib/api';
import type { Commit, Worktree } from '@/types';

/* The Git screen's live shapes, read from the repository on this machine by GET /projects/{pid}/git.
   Nothing here is stored: every number is git's answer at the moment the screen asked. */

/** A worktree beside the checkout: one a run made, or one somebody made by hand (`madeBy: 'git'`). */
export interface GitWorktree extends Omit<Worktree, 'lastCommitAt'> {
  /** ISO, or null when the branch has no commit git could read. */
  lastCommitAt: string | null;
  runRef: string | null;
  runStatus: RunStatus | null;
  madeBy: 'neurocode' | 'git';
  /** A run whose worktree is no longer on disk. Shown, never "fixed" by reading it. */
  gone: boolean;
  merged: RunDoc['merged'];
  /** Exactly when POST /runs/{runRef}/merge would accept it. */
  canMerge: boolean;
  /** Why it would be refused, in the words the merge itself refuses with. */
  mergeBlocked: string | null;
  base: string;
}

export interface GitMergePreview {
  worktreeId: string;
  branch: string;
  target: string;
  result: 'clean' | 'collides' | 'stale';
  files: number;
  note: string;
  /** Another open branch this one collides with, though each may merge into the checkout cleanly. */
  collidesWith?: string;
  onFile?: string;
}

export interface GitOverview {
  available: boolean;
  reason?: string;
  repo: string;
  head: { branch: string; sha: string; dirty: boolean };
  /** Onboarding clones one commit deep, so older history is not on this machine. */
  shallow: boolean;
  worktrees: GitWorktree[];
  mergePreview: GitMergePreview[];
  stats: {
    worktrees: number; dirty: number; clean: number; collisions: number;
    commitsToday: number; agentCommitsToday: number; awaitingYou: number;
  };
}

export interface GitChangedFile { path: string; change: 'M' | 'A' | 'D' | 'R'; additions: number; deletions: number; hunk: string }
export interface GitDiff { branch: string; against: string; files: GitChangedFile[]; truncated: boolean }

export interface GitConflictHunk { side: 'ours' | 'theirs'; branch: string; agent: string; commit: string; at: string | null; lines: string }
export interface GitConflict {
  id: string;
  file: string;
  taskRef: string;
  /** "L118 – L137", or empty when only the file list was recorded. */
  region: string;
  hunks: GitConflictHunk[];
  policy: string[];
  resolution: string;
  resolvedBy: string;
}

/** `at` is ISO. */
export interface GitCommits { shallow: boolean; commits: Commit[] }

const seg = encodeURIComponent;
// Git walks every worktree on a load; a large checkout can take longer than the default 5 s.
const slow = () => ({ signal: AbortSignal.timeout(30_000) });

export const gitApi = {
  overview: (pid: string) => request<GitOverview>(`/projects/${seg(pid)}/git`, slow()),
  diff: (pid: string, branch: string, against: 'base' | 'head') =>
    request<GitDiff>(`/projects/${seg(pid)}/git/diff?branch=${seg(branch)}&against=${against}`, slow()),
  conflicts: (pid: string) => request<GitConflict[]>(`/projects/${seg(pid)}/git/conflicts`, slow()),
  commits: (pid: string, limit = 200) => request<GitCommits>(`/projects/${seg(pid)}/git/commits?limit=${limit}`, slow()),
};
