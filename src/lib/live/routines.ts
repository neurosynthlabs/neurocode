import { request } from '@/lib/api';

/* Routines against the local API. A routine is a workflow or a requirement put on a cadence (five-field
   cron, always UTC), a webhook, or "Run now". Each fire compiles and dispatches a plan exactly as a person
   would, so every run it starts stops at the same gates and the same signature. */

export type FireTrigger = 'schedule' | 'manual' | 'webhook';
/** `firing` while it is compiled and dispatched; `skipped` when the last fire's work was still unfinished. */
export type FireOutcome = 'firing' | 'fired' | 'refused' | 'failed' | 'skipped';

export interface RoutineFire {
  id: number;
  scheduleId: string;
  at: string;
  trigger: FireTrigger;
  planRef: string | null;
  runRef: string | null;
  /** The run's status now, when the fire started one. */
  runStatus: string | null;
  outcome: FireOutcome;
  detail: string;
  /** What a webhook sent, quoted as data. */
  payloadExcerpt: string;
}

export interface Routine {
  id: string;
  name: string;
  projectId: string;
  projectName: string | null;
  workflowId: string | null;
  workflowName: string | null;
  what: 'workflow' | 'requirement';
  /** The requirement each fire compiles, or the input a workflow's {input} takes. */
  requirement: string;
  /** Five-field cron in UTC; empty when it fires only on demand. */
  cadence: string;
  cadenceLabel: string;
  enabled: boolean;
  nextAt: string | null;
  lastFiredAt: string | null;
  /** Whether a webhook token exists. The token itself is shown once, when it is made. */
  webhook: boolean;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
  last: RoutineFire | null;
  /** Why it would not fire now: the last fire's work is unfinished. Null when it would. */
  waiting: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  nextOffset: number | null;
}

export interface RoutineInput {
  name: string;
  projectId: string;
  workflowId: string | null;
  requirement: string;
  cadence: string;
  enabled: boolean;
}

export interface CadencePreview {
  cron: string;
  label: string;
  /** The next three minutes it fires, in UTC. Empty for a routine that fires only on demand. */
  next: string[];
}

export interface WebhookToken {
  scheduleId: string;
  /** Shown once. Only its hash is kept. */
  token: string;
  path: string;
}

const seg = encodeURIComponent;

export const routinesApi = {
  list: (projectId: string | null) =>
    request<Paged<Routine>>(`/schedules?limit=200${projectId ? `&project=${seg(projectId)}` : ''}`),
  get: (id: string) => request<Routine>(`/schedules/${seg(id)}`),
  fires: (id: string, offset = 0) => request<Paged<RoutineFire>>(`/schedules/${seg(id)}/fires?limit=20&offset=${offset}`),
  cadence: (cron: string) => request<CadencePreview>(`/schedules/cadence?cron=${seg(cron)}`),
  create: (body: RoutineInput) => request<Routine>('/schedules', { method: 'POST', json: body }),
  update: (id: string, body: Partial<RoutineInput>) => request<Routine>(`/schedules/${seg(id)}`, { method: 'PATCH', json: body }),
  remove: (id: string) => request<{ ok: boolean }>(`/schedules/${seg(id)}`, { method: 'DELETE' }),
  runNow: (id: string) => request<RoutineFire>(`/schedules/${seg(id)}/run`, { method: 'POST' }),
  issueToken: (id: string) => request<WebhookToken>(`/schedules/${seg(id)}/webhook/token`, { method: 'POST' }),
  revokeToken: (id: string) => request<{ ok: boolean }>(`/schedules/${seg(id)}/webhook/token`, { method: 'DELETE' }),
};

/* ── cadence presets ────────────────────────────────────────────
   What the editor offers, and the cron each writes. The server words them back (cadenceLabel) and
   reads any other expression as it is. */

export type PresetId = 'none' | 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'custom';

export interface Preset {
  id: PresetId;
  minute: number;
  hour: number;
  /** 0 is Sunday, as in cron. */
  weekday: number;
}

export const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

export function presetCron(p: Preset): string {
  switch (p.id) {
    case 'none': return '';
    case 'hourly': return `${p.minute} * * * *`;
    case 'daily': return `${p.minute} ${p.hour} * * *`;
    case 'weekdays': return `${p.minute} ${p.hour} * * 1-5`;
    case 'weekly': return `${p.minute} ${p.hour} * * ${p.weekday}`;
    default: return '';
  }
}

const whole = (s: string, max: number) => (/^\d+$/.test(s) && Number(s) <= max ? Number(s) : null);

/** Which preset wrote this cron, so the editor opens on it; anything else opens as a raw cron field. */
export function readPreset(cron: string): Preset {
  const base: Preset = { id: 'daily', minute: 0, hour: 9, weekday: 1 };
  const parts = cron.trim().split(/\s+/);
  if (!cron.trim()) return { ...base, id: 'none' };
  if (parts.length !== 5) return { ...base, id: 'custom' };
  const [mi, h, dom, mon, dow] = parts;
  const minute = whole(mi, 59);
  if (minute === null || dom !== '*' || mon !== '*') return { ...base, id: 'custom' };
  if (h === '*' && dow === '*') return { ...base, id: 'hourly', minute };
  const hour = whole(h, 23);
  if (hour === null) return { ...base, id: 'custom' };
  if (dow === '*') return { ...base, id: 'daily', minute, hour };
  if (dow === '1-5') return { ...base, id: 'weekdays', minute, hour };
  const day = whole(dow, 7);
  return day === null ? { ...base, id: 'custom' } : { ...base, id: 'weekly', minute, hour, weekday: day % 7 };
}
