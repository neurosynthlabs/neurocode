import { request, type RunDoc, type RunStep } from '@/lib/api';
import type { ApprovalRequest, Plan } from '@/types';

/* The runtime's newer calls, and the parts of a run they add: pushing an accepted run's branch
   (POST /runs/{ref}/push), reading its review again for a fresh receipt (POST /runs/{ref}/review),
   dispatching a plan "until done" (POST /plans/{ref}/dispatch {goalBudget}), and the project's own
   checks and a goal run's completion check as the run document carries them. */

/** One of the project's own checks — its lint or typecheck — as it ran in the run's worktree. */
export interface RunCheck {
  /** The run step it ran as. */
  step: number;
  name: string;
  command: string;
  status: 'not run' | 'passed' | 'failed' | 'skipped';
  /** Null until it ran. */
  exit: number | null;
  summary: string;
  /** Its last lines, as printed. */
  output: string[];
}

export interface GoalCriterion {
  criterion: string;
  met: boolean;
  /** What the judge cited; empty when it cited nothing. */
  evidence: string;
  /** The file in the diff it cited; empty when none. */
  file: string;
  /** Why a "met" was not accepted, or why it was not judged. */
  why: string;
}

/** A goal run's completion check: the project's own word, and a judge on a lane that did not write the code. */
export interface RunGoal {
  step: number;
  verdict: 'not run' | 'met' | 'not met' | 'unjudged';
  why: string;
  tests?: string;
  checks?: { name: string; status: RunCheck['status']; summary: string }[];
  criteria: GoalCriterion[];
  /** The model that judged; empty when none could. */
  by: string;
  at: string | null;
  /** 'rework': it tried again on its own. 'sign': it went to your signature. */
  next?: 'rework' | 'sign';
}

/** The fingerprint of the exact patch the reviewer read. Merge and push refuse a branch whose patch is another. */
export interface ReviewReceipt { sha256: string; head: string; base: string; bytes: number; truncated: boolean; at: string; by: string }

/** What one agent step was handed: the project's instruction files for its targets, retrieval's pieces for its
    words, and the files it read — each with why it was chosen. */
export interface StepGrounding {
  instructions: { path: string; bytes: number; sha1: string; scope: 'project' | 'rules'; matched: string | null }[];
  /** True when the instructions were cut to fit. */
  capped: boolean;
  /** `project` is set when retrieval also searched the projects this one references: the piece's own project. */
  pieces: { kind: 'code' | 'doc'; ref: string; path: string; line: number | null; how: string; project?: string }[];
  files: { path: string; via: 'plan' | 'retrieval' | 'index'; readonly?: boolean }[];
  /** Refs of the taste rules a person adopted that the step was handed (Memory → Taste). */
  taste?: string[];
}

/** A step with what the governed runtime keeps on it. */
export type RuntimeStep = RunStep & {
  /** The commit the step left in the worktree; absent when it wrote nothing. */
  commitSha?: string;
  /** The question the agent asked instead of writing, and the answer it got (null until answered). */
  question?: string;
  answer?: string | null;
  grounding?: StepGrounding;
  /** Set while the step stands taken back by a revert. */
  takenBack?: { to: number; by: string; at: string };
};

/** What a person allowed beyond the tool rules: once at one step, or for the rest of the run. */
export interface RunGrant { tool: 'edit' | 'command'; subject: string; scope: 'once' | 'run'; step: number | null; by: string; at: string; used?: boolean | null }
export interface RunRevert { to: number; by: string; at: string; steps: number[]; redo: boolean }

/** A run as the runtime now serves it. */
export type RuntimeRun = Omit<RunDoc, 'steps'> & {
  steps: RuntimeStep[];
  /** Which try this is; each automatic or manual send-back adds one. */
  attempt: number;
  /** "Run until done": attempts allowed in all; null for an ordinary run. */
  goalBudget: number | null;
  checks: RunCheck[];
  goal: RunGoal | null;
  review: RunDoc['review'] & { receipt?: ReviewReceipt; instructions?: { path: string; bytes: number }[]; taste?: string[] };
  grants: RunGrant[];
  reverts: RunRevert[];
  /** Labels of the project's reference sources: read for grounding, never written. */
  references: string[];
};

/** Every run document the API sends carries these fields; older ones read as a first attempt with no checks. */
export function asRuntime(run: RunDoc): RuntimeRun {
  const r = run as Partial<RuntimeRun> & RunDoc;
  return {
    ...r, attempt: r.attempt ?? 1, goalBudget: r.goalBudget ?? null, checks: r.checks ?? [], goal: r.goal ?? null,
    grants: r.grants ?? [], reverts: r.reverts ?? [], references: r.references ?? [],
  };
}

/* ── the gates ─────────────────────────────────────────────────── */

/** What a gate is: the first run of a project's command, a tool rule asking about a command or files, an agent's
    question, a pause before a step, or the signature. The server reads it from the tool the gate names. */
export type GateKind = 'tests' | 'command' | 'edit' | 'question' | 'step' | 'signature' | 'other';
/** "Allow once", "Allow for this run", "Always allow in this project" (needs rules:manage). */
export type GateScope = 'once' | 'run' | 'project';
export type GateApproval = ApprovalRequest & { kind?: GateKind; options?: string[] };

/** The kind of a gate; one the server sent without a kind is approved or denied. */
export const gateKind = (a: ApprovalRequest): GateKind => (a as GateApproval).kind ?? 'other';

/** `sha256:ab12…` → `ab12cd34ef56`, the way the run screen shows a receipt. */
export const shortReceipt = (fp: string | undefined) => (fp ?? '').replace(/^sha256:/, '').slice(0, 12);

const seg = encodeURIComponent;
const POST = (json?: unknown) => ({ method: 'POST', json });
// A push waits on the remote, and git gives it two minutes before it gives up.
const remote = () => ({ signal: AbortSignal.timeout(150_000) });

export const runtimeApi = {
  /** Push an accepted run's own branch; the answer is the run, with `pushed.compareUrl` when the remote has one. */
  push: (ref: string, remoteName?: string) =>
    request<RunDoc>(`/runs/${seg(ref)}/push`, { ...POST(remoteName ? { remote: remoteName } : {}), ...remote() }),
  /** Read the run's diff again as the branch is now; the review step runs in the background. */
  reviewAgain: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/review`, POST()),
  /** Dispatch a plan; with `goalBudget` (1–5) it runs until its completion check passes or the attempts run out. */
  dispatch: (ref: string, opts: { goalBudget?: number } = {}) =>
    request<Plan & { runRef?: string }>(`/plans/${seg(ref)}/dispatch`, POST(opts.goalBudget ? { goalBudget: opts.goalBudget } : undefined)),
  /** Answer a gate beyond yes or no: a tool rule's ask with a scope, an agent's question with words. */
  decide: (ref: string, decision: 'approve' | 'deny', opts: { scope?: GateScope; answer?: string } = {}) =>
    request<GateApproval>(`/approvals/${seg(ref)}/${decision}`, POST(opts.scope || opts.answer !== undefined ? opts : undefined)),
  /** Take the run's worktree back to how it stood after step `n`; with `redo` the later steps run again. */
  revert: (ref: string, n: number, redo = false) => request<RunDoc>(`/runs/${seg(ref)}/steps/${n}/revert`, POST({ redo })),
};
