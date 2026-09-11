import type { MemoryConflict } from '@/mock/memory';
import type { ActivityEvent, ApprovalRequest, McpServer, MemoryFact, Plan, Project, Task, TaskStatus } from '@/types';

/* Where the local API lives. In dev every call goes through Vite's /api proxy (scripts/dev.sh starts
   both servers). A production build talks to an API only when VITE_API_URL is set at build time — the
   public demo has none, never makes a request, and runs on the seed data. */
export const API_BASE: string = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? '/api' : '');

export interface Health {
  ok: boolean;
  db: string;
  counts: Record<string, number>;
  /** `note` explains why a configured provider is being skipped, e.g. a rejected key. */
  compiler?: { provider: 'deepseek' | 'ollama' | 'rules'; model: string; note?: string };
}

/** The collections the server streams changes for. Every document in them has an `id`. */
export type Collection = 'approvals' | 'tasks' | 'memory' | 'projects' | 'plans' | 'mcp' | 'conflicts';
export type Change =
  | { op: 'put'; collection: Collection; doc: { id: string } }
  | { op: 'drop'; collection: Collection; id: string };

export interface ProjectInput {
  source: 'git' | 'local';
  repo: string;
  branch: string;
  excluded: string[];
  connectDb: boolean;
  mineGit: boolean;
  ingestDocs: boolean;
  rules: { id: string; label: string; note: string }[];
}

export interface McpInput {
  name: string;
  transport: McpServer['transport'];
  command: string;
  scope: McpServer['scope'];
  defaultEffect: NonNullable<McpServer['defaultEffect']>;
  config: string;
}

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
      if (body && typeof body === 'object' && 'detail' in body) {
        const d = body.detail;
        // FastAPI sends a string for our own errors and a list for validation errors
        if (typeof d === 'string') detail = d;
        else if (Array.isArray(d) && d[0] && typeof d[0] === 'object' && 'msg' in d[0]) detail = String(d[0].msg);
      }
    } catch { /* the body was not JSON — keep the status line */ }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

const seg = encodeURIComponent;
// A model can take a while to answer; the server gives it up to two minutes.
const compileTimeout = () => AbortSignal.timeout(180_000);

export const api = {
  health: () => request<Health>('/health', { signal: AbortSignal.timeout(2500) }),

  projects: () => request<Project[]>('/projects'),
  createProject: (input: ProjectInput) => request<Project>('/projects', { method: 'POST', json: input }),

  approvals: () => request<ApprovalRequest[]>('/approvals'),
  decide: (ref: string, decision: 'approve' | 'deny') =>
    request<ApprovalRequest>(`/approvals/${seg(ref)}/${decision}`, { method: 'POST' }),

  tasks: () => request<Task[]>('/tasks'),
  moveTask: (ref: string, status: TaskStatus) => request<Task>(`/tasks/${seg(ref)}`, { method: 'PATCH', json: { status } }),
  check: (ref: string, itemId: string, done: boolean) =>
    request<Task>(`/tasks/${seg(ref)}/checklist/${seg(itemId)}`, { method: 'POST', json: { done } }),

  plans: () => request<Plan[]>('/plans'),
  compile: (requirement: string, projectId: string) =>
    request<Plan>('/plans/compile', { method: 'POST', json: { requirement, projectId }, signal: compileTimeout() }),
  recompile: (ref: string) => request<Plan>(`/plans/${seg(ref)}/recompile`, { method: 'POST', signal: compileTimeout() }),
  settle: (ref: string, index: number, body: { answer: string } | { defer: true }) =>
    request<Plan>(`/plans/${seg(ref)}/questions/${index}`, { method: 'POST', json: body }),
  dispatch: (ref: string) => request<Plan>(`/plans/${seg(ref)}/dispatch`, { method: 'POST' }),

  /** Full-text search is FTS5 on the server: every word must match as a prefix, best match first. */
  memory: (q = '', signal?: AbortSignal) => request<MemoryFact[]>(`/memory?${new URLSearchParams({ q })}`, { signal }),
  pin: (ref: string, pinned: boolean) => request<MemoryFact>(`/memory/${seg(ref)}/pin`, { method: 'POST', json: { pinned } }),
  archive: (ref: string) => request<MemoryFact>(`/memory/${seg(ref)}/archive`, { method: 'POST' }),
  conflicts: () => request<MemoryConflict[]>('/memory/conflicts'),
  resolveConflict: (id: string, keep: 'a' | 'b' | 'adr') =>
    request<MemoryConflict>(`/memory/conflicts/${seg(id)}/resolve`, { method: 'POST', json: { keep } }),

  mcp: () => request<McpServer[]>('/mcp/servers'),
  registerMcp: (input: McpInput) => request<McpServer>('/mcp/servers', { method: 'POST', json: input }),

  activity: () => request<ActivityEvent[]>('/activity?limit=1000'),
  reset: () => request<Health>('/admin/reset', { method: 'POST', headers: { 'X-Confirm': 'reset' } }),

  /** Server-Sent Events: each log line, and each document that changed. Returns the unsubscribe. */
  stream(on: { activity: (e: ActivityEvent) => void; change: (c: Change) => void }): () => void {
    const es = new EventSource(`${API_BASE}/activity/stream`);
    es.addEventListener('activity', (m) => on.activity(JSON.parse((m as MessageEvent<string>).data) as ActivityEvent));
    es.addEventListener('change', (m) => on.change(JSON.parse((m as MessageEvent<string>).data) as Change));
    return () => es.close();
  },
};
