import { request } from '@/lib/api';

/* The inbox against the local API: what needs you, what is working, and what finished since you last
   marked it seen. Nothing in it is stored — each entry is read from the work itself — so "seen" only moves
   the moment "done since" counts from. */

export type NeedsKind = 'approval' | 'question' | 'signature' | 'permission';
export type WorkingKind = 'run' | 'session' | 'routine';
export type DoneKind = 'run' | 'plan' | 'routine' | 'review' | 'research' | 'eval';

export interface InboxItem<K extends string = string> {
  kind: K;
  ref: string;
  title: string;
  detail: string;
  projectId: string | null;
  at: string | null;
  runRef?: string;
  sessionRef?: string;
  scheduleId?: string;
  risk?: string;
  /** How it ended, for what is done: done · failed · cancelled · fired · refused… */
  status?: string;
}

export interface Inbox {
  needsYou: InboxItem<NeedsKind>[];
  working: InboxItem<WorkingKind>[];
  doneSince: InboxItem<DoneKind>[];
  /** The true totals; each list holds at most twenty. */
  counts: { needsYou: number; working: number; doneSince: number };
  /** Where "done since" counts from. */
  since: string;
  /** False when this person never marked the inbox seen: `since` is then the last day. */
  sinceVisit: boolean;
}

export const inboxApi = {
  read: () => request<Inbox>('/inbox'),
  seen: () => request<{ since: string }>('/inbox/seen', { method: 'POST' }),
};

/** Where an entry is dealt with. */
export function inboxTarget(item: InboxItem): string {
  switch (item.kind) {
    case 'approval': case 'question': case 'signature': return '/permissions';
    case 'permission': case 'session': return item.sessionRef ? `/sessions?ref=${encodeURIComponent(item.sessionRef)}` : '/sessions';
    case 'run': return item.runRef ? `/runs?ref=${encodeURIComponent(item.runRef)}` : '/runs';
    case 'routine': return item.scheduleId ? `/routines?id=${encodeURIComponent(item.scheduleId)}` : '/routines';
    case 'plan': return `/plans?ref=${encodeURIComponent(item.ref)}`;
    case 'review': return '/review';
    case 'research': return '/research';
    case 'eval': return '/evals';
    default: return '/';
  }
}
