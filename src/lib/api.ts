import type { MemoryConflict } from '@/mock/memory';
import type {
  ActivityEvent, ApprovalRequest, Confidence, McpServer, MemoryCategory, MemoryFact, Plan, Project, Risk, Task, TaskStatus,
} from '@/types';

/* Where the local API lives. In dev every call goes through Vite's /api proxy (scripts/dev.sh starts
   both servers). A production build talks to an API only when VITE_API_URL is set at build time — the
   public demo has none, never makes a request, and runs on the seed data. */
export const API_BASE: string = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? '/api' : '');

/** Fired on window when the server says the session is gone: expired, signed out elsewhere, disabled. */
export const SIGNED_OUT = 'nc:signed-out';

export interface CompilerInfo {
  provider: LaneId | 'rules';
  /** How many lanes could answer right now. */
  lanes?: number;
  model: string;
  /** Why a configured provider is being skipped, e.g. a rejected key. */
  note?: string;
}

export interface Health {
  ok: boolean;
  db: string;
  counts: Record<string, number>;
  needsSetup?: boolean;
  compiler?: CompilerInfo;
}

/** The collections the server streams changes for. Every document in them has an `id`. */
export type Collection =
  | 'approvals' | 'tasks' | 'memory' | 'projects' | 'plans' | 'mcp' | 'conflicts' | 'prefs' | 'decisions'
  | 'brainstorms' | 'runs';

/** A saved screen setting. */
export interface Pref { id: string; value: unknown }
/** A final operator decision made outside the approvals inbox. */
export interface DecisionDoc { id: string; value: string; decidedAt: string; decidedBy?: string }
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

/* ── people and access ────────────────────────────────────────── */
export interface AuthUser {
  id: string; email: string; name: string; status: 'active' | 'disabled'; roles: string[]; permissions: string[];
}
export interface Workspace { name: string }
export interface AuthStatus { needsSetup: boolean; user: AuthUser | null; workspace: Workspace | null }
export interface SignedIn { user: AuthUser; workspace: Workspace | null }
export interface SetupInput { workspace: string; name: string; email: string; password: string }

export interface Person {
  id: string; email: string; name: string; status: 'active' | 'disabled'; roles: string[]; teams: string[];
  lastLoginAt: string | null; createdAt: string;
}
export interface PermissionDef { id: string; label: string; group: string; description: string }
export interface RoleDoc { id: string; name: string; description: string; builtin: boolean; permissions: string[]; members: number }
export interface TeamDoc { id: string; name: string; description: string; members: string[]; createdAt: string }
export interface AuditEntry {
  seq: number; at: string; user: string | null; action: string; target: string; detail: Record<string, unknown>; ip?: string;
}
export interface WorkspaceInfo { name: string; createdAt: string | null; people: number; roles: number; teams: number }

/** A lane is a provider and a model together. Several answer at once; free ones come first. */
export type LaneId = 'groq' | 'cerebras' | 'gemini' | 'mistral' | 'openrouter' | 'github' | 'deepseek' | 'ollama';
export type AiPreference = 'auto' | 'free' | 'local' | 'rules' | LaneId;
export interface AiLane {
  id: LaneId; label: string; model: string; baseUrl: string; api: 'openai' | 'ollama';
  /** What its free tier allows — the router's own cap, editable. 0 means unmetered. */
  free: boolean; rpm: number; rpd: number; goodAt: ('write' | 'review' | 'plan' | 'chat')[];
  needsKey: boolean; signup: string; note: string;
  enabled: boolean; hasKey: boolean; keyMask: string | null; keySource: 'workspace' | 'environment' | null;
  rejected: boolean;
  /** Can it take the next call? If not, `blocked` says why in plain words. */
  ready: boolean; blocked: string | null; allowed: boolean;
  spent: { minute: number; today: number };
}
export interface AiConfig {
  preference: AiPreference;
  /** NEUROCODE_COMPILER is set on the server, and it wins over the workspace setting. */
  preferenceLocked: boolean;
  active: CompilerInfo;
  deepseek: {
    hasKey: boolean; keyMask: string | null; keySource: 'workspace' | 'environment' | null;
    model: string; baseUrl: string; rejected: boolean;
  };
  ollama: { url: string; model: string; ready: boolean };
  lanes: AiLane[];
}
export interface AiPatch {
  preference?: AiPreference;
  /** An empty string removes the key. */
  deepseekKey?: string;
  deepseekModel?: string;
  deepseekUrl?: string;
  ollamaUrl?: string;
  ollamaModel?: string;
  /** The lane the fields below are about. */
  lane?: LaneId;
  key?: string;
  model?: string;
  baseUrl?: string;
  rpm?: number;
  rpd?: number;
  enabled?: boolean;
}
export interface AiTestResult { ok: boolean; ms: number; detail: string }

/* ── AI features ──────────────────────────────────────────────── */
export interface AiMeta { provider: CompilerInfo['provider']; model: string; ms: number }
export interface AskAnswer extends AiMeta { answer: string; citations: { ref: string; title: string }[] }
export interface Brief {
  title: string; problem: string; audience: string; value: string;
  mvp: string[]; risks: string[]; metrics: string[]; questions: string[];
  roadmap: { phase: string; items: string[] }[];
}
export interface BrainstormDoc {
  id: string; ref: string; idea: string; projectId: string | null; brief: Brief; compiler: AiMeta; by: string; createdAt: string;
}
export interface FactCandidate { title: string; body: string; category: MemoryCategory; confidence: Confidence; reason: string }
export interface Extracted extends AiMeta { facts: FactCandidate[] }

/* ── the code index ───────────────────────────────────────────── */
export interface CodeModule { name: string; files: number; lines: number; complexity: number; symbols: number; fanIn: number; fanOut: number }
export interface DbObject { name: string; kind: string; path: string; readers: number; writers: number; callers: number }
export interface CodeSummary {
  indexed: boolean;
  /** An index is being built right now. */
  indexing: boolean;
  /** The project's code is on this machine (onboarded, not a sample). */
  canIndex: boolean;
  run?: { files: number; symbols: number; edges: number; unresolved: number; ms: number; finishedAt: string; parsers: Record<string, string> };
  languages?: { name: string; files: number; lines: number }[];
  modules?: CodeModule[];
  hotspots?: { path: string; lines: number; complexity: number; churn: number; fanIn: number; risk: Risk }[];
  database?: { objects: number; top: DbObject[] };
}
export interface CodeChildren {
  dir: string;
  dirs: { name: string; path: string; files: number; lines: number }[];
  files: { id: number; name: string; path: string; lang: string; lines: number; complexity: number; fanIn: number }[];
}
export interface CodeHit { name: string; path: string; kind: string; line: number }
export interface Impact {
  target: string;
  kind: 'file' | 'module' | 'object';
  risk: Risk;
  confidence: number;
  counts: { direct: number; dependents: number; modules: number; tests: number; data: number };
  blastRadius: { label: string; items: string[] }[];
  modules: { name: string; files: number }[];
  warnings: string[];
  recommendation: string;
}
export interface CodeFile {
  file: {
    path: string; lang: string; module: string; lines: number; bytes: number; complexity: number; churn: number;
    changedAt: string | null; fanIn: number; fanOut: number; test: boolean;
  };
  symbols: { name: string; kind: string; line: number; exported: boolean }[];
  /** `path` is null for a package outside the repository. */
  dependsOn: { path: string | null; target: string; kind: string }[];
  database: { object: string; kind: string; path: string | null }[];
  usedBy: { path: string; kinds: string[]; targets: string[] }[];
  impact: Impact | null;
}
export interface CodeGraph {
  nodes: { id: string; label: string; kind: string; files: number; lines: number; risk: Risk }[];
  edges: { from: string; to: string; kind: string; weight: number }[];
  modules: number;
  truncated: boolean;
}
export type ImpactTarget = { path: string } | { module: string } | { object: string };

/* ── agent runs ───────────────────────────────────────────────── */
export type RunStatus = 'queued' | 'running' | 'waiting' | 'done' | 'failed' | 'cancelled';
export interface RunStep {
  n: number;
  kind: 'edit' | 'merge' | 'test' | 'review' | 'handoff';
  /** For a merge step: the agent run whose branch it brings in. */
  child?: string;
  label: string;
  agent: string;
  status: 'todo' | 'running' | 'waiting' | 'done' | 'skipped' | 'failed';
  detail: string;
  ms: number;
  startedAt: string | null;
  finishedAt: string | null;
}
export interface RunLog { id: number; at: string; step: number | null; level: 'info' | 'ok' | 'warn' | 'err' | 'tool'; line: string }
/** A log line as it arrives on the stream. */
export interface RunLogEvent extends RunLog { runRef: string }
export interface RunDoc {
  id: string; ref: string; projectId: string; projectName: string; taskRef: string | null; planRef: string;
  requirement: string; status: RunStatus; branch: string; worktree: string; repo: string; prefix: string;
  base: string; shortBase: string; startedAt: string; finishedAt: string | null; requestedBy: string;
  targets: string[];
  steps: RunStep[];
  tests: { command: string | null; argv: string[] | null; status: string; summary: string };
  review: { findings: { severity: string; file: string; note: string }[]; verdict: string; by: string };
  diff: { files: number; insertions: number; deletions: number; commits: number };
  model: string | null;
  note: string;
  removed: boolean;
  /** `solo`: one agent. `agent`: one of several working in parallel. `integration`: the run that merges them. */
  role: 'solo' | 'agent' | 'integration';
  /** Which agent this run belongs to, when it is one of several. */
  agent: string | null;
  /** The task the batch shares. */
  group: string;
  parent: string | null;
  children: string[];
  /** Branches that could not be merged, with the files that collided. */
  conflicts: { branch: string; agent: string; files: string[] }[];
  /** Set once the branch is merged into the repository on this machine. */
  merged: { into: string; commit: string; at: string; by: string; undo: string } | null;
  /** The approval this run is stopped at. */
  waitingOn?: string;
}
export interface MergeResult {
  merged: boolean;
  into: string;
  conflicts: string[];
  commit: string | null;
  undo: string | null;
  run: RunDoc;
}
export interface RunDetail extends RunDoc { logs: RunLog[] }
export interface RunDiff { patch: string; truncated: boolean; stat: RunDoc['diff']; gone: boolean }

/* ── sessions: a conversation that can read the code ───────────── */
export type SessionStatus = 'idle' | 'thinking' | 'failed';
export interface SessionDoc {
  id: string; ref: string; projectId: string; projectName: string; title: string; status: SessionStatus;
  startedAt: string; lastAt: string; startedBy: string; turns: number; toolCalls: number;
  model: string | null; lane: LaneId | null; note: string;
}
/** One turn: your question, a tool call with what it found, an answer, or a note from the system. */
export interface ChatMessage {
  id: number; at: string; role: 'you' | 'assistant' | 'tool' | 'note'; text: string;
  by?: string; model?: string; lane?: LaneId; ms?: number;
  tool?: string; arguments?: Record<string, unknown>; why?: string; detail?: string; ok?: boolean;
}
export interface SessionDetail extends SessionDoc { messages: ChatMessage[] }
export interface ChatEvent extends ChatMessage { sessionRef: string }

/* ── usage and the database ───────────────────────────────────── */
export interface UsageReport {
  days: number;
  totals: { calls: number; modelCalls: number; offline: number; failures: number; tokensIn: number; tokensOut: number; avgMs: number };
  byDay: { day: string; calls: number; model: number; offline: number; tokens: number }[];
  byFeature: { feature: string; calls: number; model: number; offline: number; failures: number; tokensIn: number; tokensOut: number; avgMs: number }[];
  byProvider: { provider: string; model: string; calls: number; failures: number; tokensIn: number; tokensOut: number; avgMs: number }[];
  /** `by` is filled in for admins only. */
  recent: { at: string; feature: string; provider: string; model: string; ok: boolean; ms: number; tokensIn: number; tokensOut: number; error: string; by: string | null }[];
  byPerson?: { name: string; calls: number; tokens: number }[];
}
export interface BackupInfo { name: string; bytes: number; at: string }
export interface DatabaseInfo {
  path: string; sqlite: string; pageSize: number; pages: number; freePages: number; journalMode: string;
  sizeBytes: number; walBytes: number; tables: { name: string; rows: number }[];
  /** Full-text search indexes. */
  indexes: number;
  migrations: { version: number; name: string; appliedAt: string }[];
  backups: BackupInfo[];
  backupDir: string | null;
}
export interface DatabaseCheck { ok: boolean; integrity: string[]; foreignKeyProblems: number; at: string }
export interface DatabaseOptimized { beforeBytes: number; afterBytes: number; ms: number }

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
    // The session is an HttpOnly cookie. X-NC-Client is the CSRF guard: the API refuses a change that
    // carries the cookie without it, and another site cannot add it.
    credentials: 'include',
    headers: { 'X-NC-Client': 'web', ...(json === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
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
    // The /auth routes answer 401 as part of their job (a wrong password); anywhere else it means the session ended.
    if (res.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event(SIGNED_OUT));
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

const seg = encodeURIComponent;
// A model can take a while to answer; the server gives it up to two minutes.
const modelTimeout = () => AbortSignal.timeout(180_000);
const POST = (json?: unknown) => ({ method: 'POST', json });
const PATCH = (json: unknown) => ({ method: 'PATCH', json });

export const api = {
  health: () => request<Health>('/health', { signal: AbortSignal.timeout(2500) }),

  /* signing in */
  authStatus: () => request<AuthStatus>('/auth/status', { signal: AbortSignal.timeout(2500) }),
  setup: (input: SetupInput) => request<SignedIn>('/auth/setup', { ...POST(input), signal: AbortSignal.timeout(10_000) }),
  login: (email: string, password: string) =>
    request<SignedIn>('/auth/login', { ...POST({ email, password }), signal: AbortSignal.timeout(10_000) }),
  logout: () => request<{ ok: boolean }>('/auth/logout', POST()),
  changePassword: (current: string, next: string) => request<{ ok: boolean }>('/auth/password', POST({ current, new: next })),

  /* administration */
  admin: {
    users: () => request<Person[]>('/admin/users'),
    createUser: (body: { email: string; name: string; password: string; roles: string[] }) => request<Person>('/admin/users', POST(body)),
    updateUser: (id: string, body: { name?: string; status?: Person['status']; roles?: string[] }) =>
      request<Person>(`/admin/users/${seg(id)}`, PATCH(body)),
    resetPassword: (id: string, password: string) => request<{ ok: boolean }>(`/admin/users/${seg(id)}/password`, POST({ password })),
    permissions: () => request<PermissionDef[]>('/admin/permissions'),
    roles: () => request<RoleDoc[]>('/admin/roles'),
    createRole: (body: { name: string; description: string; permissions: string[] }) => request<RoleDoc>('/admin/roles', POST(body)),
    updateRole: (id: string, body: { name?: string; description?: string; permissions?: string[] }) =>
      request<RoleDoc>(`/admin/roles/${seg(id)}`, PATCH(body)),
    deleteRole: (id: string) => request<RoleDoc>(`/admin/roles/${seg(id)}`, { method: 'DELETE' }),
    teams: () => request<TeamDoc[]>('/admin/teams'),
    createTeam: (body: { name: string; description: string; members: string[] }) => request<TeamDoc>('/admin/teams', POST(body)),
    updateTeam: (id: string, body: { name?: string; description?: string; members?: string[] }) =>
      request<TeamDoc>(`/admin/teams/${seg(id)}`, PATCH(body)),
    deleteTeam: (id: string) => request<TeamDoc>(`/admin/teams/${seg(id)}`, { method: 'DELETE' }),
    audit: (before?: number) =>
      request<AuditEntry[]>(`/admin/audit?${new URLSearchParams({ limit: '100', ...(before ? { before: String(before) } : {}) })}`),
    workspace: () => request<WorkspaceInfo>('/admin/workspace'),
    updateWorkspace: (name: string) => request<WorkspaceInfo>('/admin/workspace', PATCH({ name })),
    ai: () => request<AiConfig>('/admin/ai', { signal: AbortSignal.timeout(8000) }),
    updateAi: (patch: AiPatch) => request<AiConfig>('/admin/ai', { method: 'PUT', json: patch, signal: AbortSignal.timeout(8000) }),
    testAi: (provider: CompilerInfo['provider']) => request<AiTestResult>('/admin/ai/test', { ...POST({ provider }), signal: modelTimeout() }),
    database: () => request<DatabaseInfo>('/admin/database', { signal: AbortSignal.timeout(15_000) }),
    backupDatabase: () => request<BackupInfo>('/admin/database/backup', { ...POST(), signal: AbortSignal.timeout(120_000) }),
    checkDatabase: () => request<DatabaseCheck>('/admin/database/check', { ...POST(), signal: AbortSignal.timeout(120_000) }),
    optimizeDatabase: () => request<DatabaseOptimized>('/admin/database/optimize', { ...POST(), signal: AbortSignal.timeout(300_000) }),
  },

  /* the code index of an onboarded project */
  code: {
    summary: (pid: string) => request<CodeSummary>(`/projects/${seg(pid)}/code`, { signal: AbortSignal.timeout(15_000) }),
    files: (pid: string, dir = '') => request<CodeChildren>(`/projects/${seg(pid)}/code/files?${new URLSearchParams({ dir })}`),
    search: (pid: string, q: string) => request<CodeHit[]>(`/projects/${seg(pid)}/code/search?${new URLSearchParams({ q })}`),
    file: (pid: string, path: string) =>
      request<CodeFile>(`/projects/${seg(pid)}/code/file?${new URLSearchParams({ path })}`, { signal: AbortSignal.timeout(15_000) }),
    impact: (pid: string, target: ImpactTarget) =>
      request<Impact>(`/projects/${seg(pid)}/code/impact?${new URLSearchParams(target)}`, { signal: AbortSignal.timeout(15_000) }),
    graph: (pid: string) => request<CodeGraph>(`/projects/${seg(pid)}/code/graph`, { signal: AbortSignal.timeout(15_000) }),
    reindex: (pid: string) => request<{ ok: boolean }>(`/projects/${seg(pid)}/code/reindex`, POST()),
  },

  /* agent runs: a worktree of their own, and everything they did in it */
  runs: () => request<RunDoc[]>('/runs'),
  run: (ref: string, after = 0) => request<RunDetail>(`/runs/${seg(ref)}?after=${after}`),
  runDiff: (ref: string) => request<RunDiff>(`/runs/${seg(ref)}/diff`, { signal: AbortSignal.timeout(15_000) }),
  cancelRun: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/cancel`, POST()),
  mergeRun: (ref: string) => request<MergeResult>(`/runs/${seg(ref)}/merge`, { ...POST(), signal: AbortSignal.timeout(120_000) }),
  discardRun: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/discard`, POST()),

  sessions: (project?: string) => request<SessionDoc[]>(`/sessions${project ? `?project=${encodeURIComponent(project)}` : ''}`),
  session: (ref: string, after = 0) => request<SessionDetail>(`/sessions/${seg(ref)}?after=${after}`),
  newSession: (projectId: string, title = '') => request<SessionDoc>('/sessions', POST({ projectId, title })),
  /** Ask, and let it think in the background: the turns arrive on the stream. */
  askSession: (ref: string, text: string) =>
    request<{ message: ChatMessage; session: SessionDoc }>(`/sessions/${seg(ref)}/messages`, POST({ text })),
  cancelSession: (ref: string) => request<SessionDoc>(`/sessions/${seg(ref)}/cancel`, POST()),

  /** The AI gateway's ledger: every model call and every offline answer. */
  usage: (days = 30) => request<UsageReport>(`/usage?days=${days}`),

  /* AI features */
  ask: (question: string, projectId?: string) => request<AskAnswer>('/ai/ask', { ...POST({ question, projectId }), signal: modelTimeout() }),
  brainstorms: () => request<BrainstormDoc[]>('/ai/brainstorms'),
  brainstorm: (idea: string, projectId?: string) =>
    request<BrainstormDoc>('/ai/brainstorm', { ...POST({ idea, projectId }), signal: modelTimeout() }),
  extract: (text: string, projectId?: string) => request<Extracted>('/ai/extract', { ...POST({ text, projectId }), signal: modelTimeout() }),

  /* the work */
  projects: () => request<Project[]>('/projects'),
  createProject: (input: ProjectInput) => request<Project>('/projects', POST(input)),

  approvals: () => request<ApprovalRequest[]>('/approvals'),
  decide: (ref: string, decision: 'approve' | 'deny') => request<ApprovalRequest>(`/approvals/${seg(ref)}/${decision}`, POST()),

  tasks: () => request<Task[]>('/tasks'),
  moveTask: (ref: string, status: TaskStatus) => request<Task>(`/tasks/${seg(ref)}`, PATCH({ status })),
  check: (ref: string, itemId: string, done: boolean) =>
    request<Task>(`/tasks/${seg(ref)}/checklist/${seg(itemId)}`, POST({ done })),

  plans: () => request<Plan[]>('/plans'),
  compile: (requirement: string, projectId: string) =>
    request<Plan>('/plans/compile', { ...POST({ requirement, projectId }), signal: modelTimeout() }),
  recompile: (ref: string) => request<Plan>(`/plans/${seg(ref)}/recompile`, { ...POST(), signal: modelTimeout() }),
  settle: (ref: string, index: number, body: { answer: string } | { defer: true }) =>
    request<Plan>(`/plans/${seg(ref)}/questions/${index}`, POST(body)),
  dispatch: (ref: string) => request<Plan>(`/plans/${seg(ref)}/dispatch`, POST()),

  /** Full-text search is FTS5 on the server: every word must match as a prefix, best match first. */
  memory: (q = '', signal?: AbortSignal) => request<MemoryFact[]>(`/memory?${new URLSearchParams({ q })}`, { signal }),
  addFacts: (projectId: string, facts: FactCandidate[]) => request<MemoryFact[]>('/memory/facts', POST({ projectId, facts })),
  pin: (ref: string, pinned: boolean) => request<MemoryFact>(`/memory/${seg(ref)}/pin`, POST({ pinned })),
  archive: (ref: string) => request<MemoryFact>(`/memory/${seg(ref)}/archive`, POST()),
  conflicts: () => request<MemoryConflict[]>('/memory/conflicts'),
  resolveConflict: (id: string, keep: 'a' | 'b' | 'adr') =>
    request<MemoryConflict>(`/memory/conflicts/${seg(id)}/resolve`, POST({ keep })),

  mcp: () => request<McpServer[]>('/mcp/servers'),
  registerMcp: (input: McpInput) => request<McpServer>('/mcp/servers', POST(input)),

  prefs: () => request<Pref[]>('/prefs'),
  setPref: (key: string, value: unknown, detail?: string) =>
    request<Pref>(`/prefs/${seg(key)}`, { method: 'PUT', json: { value, detail: detail ?? '' } }),
  decisions: () => request<DecisionDoc[]>('/decisions'),
  recordDecision: (key: string, body: { value: string; action: string; detail: string; projectId: string; level: string }) =>
    request<DecisionDoc>(`/decisions/${seg(key)}`, POST(body)),

  activity: () => request<ActivityEvent[]>('/activity?limit=1000'),
  reset: () => request<Health>('/admin/reset', { method: 'POST', headers: { 'X-Confirm': 'reset' } }),

  /** Server-Sent Events: each log line, each document that changed, and an agent run's output. */
  stream(on: {
    activity: (e: ActivityEvent) => void;
    change: (c: Change) => void;
    log?: (l: RunLogEvent) => void;
    chat?: (m: ChatEvent) => void;
  }): () => void {
    const es = new EventSource(`${API_BASE}/activity/stream`, { withCredentials: true });
    es.addEventListener('activity', (m) => on.activity(JSON.parse((m as MessageEvent<string>).data) as ActivityEvent));
    es.addEventListener('change', (m) => on.change(JSON.parse((m as MessageEvent<string>).data) as Change));
    es.addEventListener('run', (m) => on.log?.(JSON.parse((m as MessageEvent<string>).data) as RunLogEvent));
    es.addEventListener('chat', (m) => on.chat?.(JSON.parse((m as MessageEvent<string>).data) as ChatEvent));
    return () => es.close();
  },
};
