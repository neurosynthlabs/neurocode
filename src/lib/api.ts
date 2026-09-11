import type { ActivityEvent, ApprovalRequest, MemoryFact, Task, TaskStatus } from '@/types';

/* Where the local API lives. In dev every call goes through Vite's /api proxy (scripts/dev.sh starts
   both servers). A production build talks to an API only when VITE_API_URL is set at build time — the
   public demo has none, never makes a request, and runs on the seed data. */
export const API_BASE: string = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? '/api' : '');

export interface Health { ok: boolean; db: string; counts: Record<string, number> }

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

interface RequestOpts { method?: string; json?: unknown; headers?: Record<string, string>; signal?: AbortSignal }

async function request<T>(path: string, { method = 'GET', json, headers, signal }: RequestOpts = {}): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method,
    headers: { ...(json === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
    body: json === undefined ? undefined : JSON.stringify(json),
    signal: signal ?? AbortSignal.timeout(5000),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') detail = body.detail;
    } catch { /* the body was not JSON — keep the status line */ }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

const seg = encodeURIComponent;

export const api = {
  health: () => request<Health>('/health', { signal: AbortSignal.timeout(2500) }),

  approvals: () => request<ApprovalRequest[]>('/approvals'),
  decide: (ref: string, decision: 'approve' | 'deny') =>
    request<ApprovalRequest>(`/approvals/${seg(ref)}/${decision}`, { method: 'POST' }),

  tasks: () => request<Task[]>('/tasks'),
  moveTask: (ref: string, status: TaskStatus) => request<Task>(`/tasks/${seg(ref)}`, { method: 'PATCH', json: { status } }),
  check: (ref: string, itemId: string, done: boolean) =>
    request<Task>(`/tasks/${seg(ref)}/checklist/${seg(itemId)}`, { method: 'POST', json: { done } }),

  /** Full-text search is FTS5 on the server: every word must match as a prefix, best match first. */
  memory: (q = '', signal?: AbortSignal) => request<MemoryFact[]>(`/memory?${new URLSearchParams({ q })}`, { signal }),
  pin: (ref: string, pinned: boolean) => request<MemoryFact>(`/memory/${seg(ref)}/pin`, { method: 'POST', json: { pinned } }),
  archive: (ref: string) => request<MemoryFact>(`/memory/${seg(ref)}/archive`, { method: 'POST' }),

  activity: () => request<ActivityEvent[]>('/activity?limit=1000'),
  reset: () => request<Health>('/admin/reset', { method: 'POST', headers: { 'X-Confirm': 'reset' } }),

  /** Server-Sent Events: every change the API records, pushed to this tab. Returns the unsubscribe. */
  stream(onEvent: (e: ActivityEvent) => void): () => void {
    const es = new EventSource(`${API_BASE}/activity/stream`);
    es.addEventListener('activity', (m) => onEvent(JSON.parse((m as MessageEvent<string>).data) as ActivityEvent));
    return () => es.close();
  },
};
