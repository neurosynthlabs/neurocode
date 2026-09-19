import { request } from '@/lib/api';
import type { GroundedPlan } from '@/lib/live/instructions';

/* A plan shaped before it is dispatched: its steps edited by hand, comments left on it, and the revision the
   compiler writes from them — plus the dispatch options that go with it. Every call answers with the plan
   as it now stands; the same document also reaches every open tab on the change stream. */

export interface PlanStepDoc {
  n: number;
  label: string;
  agent: string;
  detail: string;
}

/** One step's fate between two revisions, matched by its label. `n` is its number now (null when removed), `was` before. */
export interface StepChange extends Omit<PlanStepDoc, 'n'> {
  op: 'same' | 'moved' | 'changed' | 'added' | 'removed';
  n: number | null;
  was: number | null;
  /** For `changed`: what it said before. */
  before?: { label: string; agent: string; detail: string };
}

export type CommentKind = 'comment' | 'split' | 'remove' | 'why' | 'risky';

/** A comment as a revision was handed it, with the compiler's reply. */
export interface HandedComment {
  id: number;
  kind: CommentKind;
  body: string;
  step: { n: number; label: string } | null;
  reply: string;
}

/** An earlier revision, kept: its steps, the comments that replaced it, and what changed into the next. */
export interface PlanRevision {
  revision: number;
  at: string;
  by: string;
  summary: string;
  model: string;
  steps: PlanStepDoc[];
  comments: HandedComment[];
  changes: StepChange[];
}

/** A plan as the API sends it since plans could be shaped. */
export type ShapedPlan = GroundedPlan & {
  revision?: number;
  stepGate?: boolean;
  revisions?: PlanRevision[];
  /** To the microsecond: which of two copies of one plan is newer. */
  updatedAt?: string | null;
  fileCheck?: (GroundedPlan['fileCheck'] & { readOnly?: Record<string, string> }) | null;
};

export interface PlanComment {
  id: number;
  planRef: string;
  /** Null for a comment on the whole plan, or once a revision replaced its step. */
  stepId: string | null;
  step: { n: number; label: string } | null;
  kind: CommentKind;
  body: string;
  /** The revision it was written on. */
  revision: number;
  resolved: boolean;
  by: string | null;
  createdAt: string;
  /** What the compiler answered when a revision took it up. */
  reply: string | null;
}

export interface CommentList {
  items: PlanComment[];
  open: number;
  revision: number;
}

export const COMMENT_KINDS: { id: CommentKind; label: string; hint: string }[] = [
  { id: 'comment', label: 'Comment', hint: 'Take this into account' },
  { id: 'split', label: 'Split', hint: 'This step is too big' },
  { id: 'remove', label: 'Remove', hint: 'This step should not be done' },
  { id: 'why', label: 'Why?', hint: 'Explain this step' },
  { id: 'risky', label: 'Risky', hint: 'Make this step safer' },
];

const seg = encodeURIComponent;
/** A revision is a compile, so it waits as long as one. */
const modelTimeout = () => AbortSignal.timeout(180_000);

export const plansApi = {
  editStep: (ref: string, stepId: string, patch: Partial<Pick<PlanStepDoc, 'label' | 'agent' | 'detail'>>) =>
    request<ShapedPlan>(`/plans/${seg(ref)}/steps/${seg(stepId)}`, { method: 'PATCH', json: patch }),
  /** `at` is where it goes, 1 for first; left out, last. */
  addStep: (ref: string, step: { label: string; agent: string; detail?: string; at?: number }) =>
    request<ShapedPlan>(`/plans/${seg(ref)}/steps`, { method: 'POST', json: step }),
  removeStep: (ref: string, stepId: string) =>
    request<ShapedPlan>(`/plans/${seg(ref)}/steps/${seg(stepId)}`, { method: 'DELETE' }),
  /** Every step's id, each once, in the order wanted. */
  reorder: (ref: string, order: string[]) =>
    request<ShapedPlan>(`/plans/${seg(ref)}/steps`, { method: 'PATCH', json: { order } }),
  comments: (ref: string) => request<CommentList>(`/plans/${seg(ref)}/comments`),
  comment: (ref: string, c: { kind: CommentKind; body: string; stepId?: string | null }) =>
    request<PlanComment>(`/plans/${seg(ref)}/comments`, { method: 'POST', json: c }),
  resolve: (ref: string, id: number, resolved = true) =>
    request<PlanComment>(`/plans/${seg(ref)}/comments/${id}/resolve`, { method: 'POST', json: { resolved } }),
  revise: (ref: string) =>
    request<ShapedPlan & { changes: StepChange[] }>(`/plans/${seg(ref)}/revise`, { method: 'POST', signal: modelTimeout() }),
  /** Dispatch with its options: "run until done" with a budget, and "pause before each step". */
  dispatch: (ref: string, opts: { goalBudget?: number; stepGate?: boolean }) =>
    request<ShapedPlan & { runRef?: string }>(`/plans/${seg(ref)}/dispatch`, {
      method: 'POST',
      json: { ...(opts.goalBudget ? { goalBudget: opts.goalBudget } : {}), stepGate: !!opts.stepGate },
    }),
};

/** The newer of two copies of one plan: the store's (from the change stream) or the one a call answered with. */
export function newer<T extends ShapedPlan>(held: T, answered: ShapedPlan | null): T {
  if (!answered || answered.ref !== held.ref) return held;
  return (answered.updatedAt ?? '') >= (held.updatedAt ?? '') ? ({ ...held, ...answered } as T) : held;
}
