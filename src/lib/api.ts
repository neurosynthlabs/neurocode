import { walkPages } from '@/lib/paging';
// The sidebar's own sections: a right names the one it is filed under, in these exact words.
import type { NavSection } from '@/lib/nav';
import type {
  ActivityEvent, ApprovalRequest, Confidence, ConflictResolution, McpServer, MemoryCategory, MemoryConflict, MemoryFact, Plan,
  Project, Risk, Task, TaskStatus,
} from '@/types';

/* Where the API lives. The same in every build: /api on this origin (Vite proxies it in dev, and a
   deployment serves the API beside the app), unless VITE_API_URL names another place at build time.
   There is no build without an API — when it cannot be reached, the app says so and offers a retry. */
export const API_BASE: string = import.meta.env.VITE_API_URL ?? '/api';

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
  /** Which database is answering, as `host:port/name`. */
  db: string;
  /** Row counts per table — the planner's estimates, so a status panel need not scan anything. */
  counts: Record<string, number>;
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
  /**
   * What this person holds *inside* one project, where a project narrows their workspace rights:
   * project id → the rights left to them there. A project that is not listed narrows nothing, so
   * everything in `permissions` applies. It can only ever take rights away, never add one.
   *
   * Optional because an API older than per-project rights does not send it, and "not sent" must read
   * as "nothing is narrowed" rather than as "everything is".
   */
  projectRights?: Record<string, string[]>;
}
export interface Workspace { name: string }
/**
 * What the sign-in screen may know about single sign-on before anyone has signed in: whether there
 * is a button and what it says. The issuer and the client id are an admin’s business and never
 * reach a stranger, so they are deliberately not here.
 */
export interface SsoPublic {
  enabled: boolean;
  /** What the button says after “Continue with”. */
  label: string;
  /** The workspace requires SSO: a password only works for an Owner. */
  passwordsOff: boolean;
}
export interface AuthStatus {
  needsSetup: boolean;
  /** A server on the internet asks for its setup token before the first Owner can be made. */
  setupNeedsToken?: boolean;
  user: AuthUser | null;
  workspace: Workspace | null;
  /** Whether this server opens the machine it runs on at all: false on a hosted one. */
  machineAccess?: boolean;
  /** Absent on an API older than single sign-on, which reads as “there is no button”. */
  sso?: SsoPublic;
}
export interface SignedIn { user: AuthUser; workspace: Workspace | null; machineAccess?: boolean }
export interface SetupInput { workspace: string; name: string; email: string; password: string; setupToken?: string }

export interface Person {
  id: string; email: string; name: string; status: 'active' | 'disabled'; roles: string[]; teams: string[];
  lastLoginAt: string | null; createdAt: string;
}
/** What holding a right lets someone do — the four columns of the Roles matrix, as the server files
    them (server/app/data/catalogue.py names the same four). */
export type PermissionVerb = 'use' | 'write' | 'decide' | 'admin';
/**
 * A right as the catalogue declares it, filed where the product itself files it: the sidebar module
 * and sub-module it lives under, and the verb that says what it lets someone do. `group` repeats the
 * module and is kept for one release, for a browser holding an older bundle.
 */
export interface PermissionDef {
  id: string; label: string; group: string; description: string;
  module: NavSection; sub: string | null; verb: PermissionVerb;
}
/** The product's own catalogue: what exists before anyone has made anything. */
export interface Catalogue {
  permissions: PermissionDef[];
  roles: { id: string; name: string; description: string; builtin: boolean; permissions: string[] }[];
  /** The agent roster's identity only: status, record and model are derived, and come from GET /agents. */
  agents: { id: string; name: string; role: string; icon: string }[];
}
export interface RoleDoc { id: string; name: string; description: string; builtin: boolean; permissions: string[]; members: number }
export interface TeamDoc { id: string; name: string; description: string; members: string[]; createdAt: string }
export interface AuditEntry {
  seq: number; at: string; user: string | null; action: string; target: string; detail: Record<string, unknown>; ip?: string;
}
export interface WorkspaceInfo {
  name: string; createdAt: string | null; people: number; roles: number; builtinRoles: number; teams: number;
  /** The sign-in rules the API enforces, read from its settings rather than restated. */
  security: { sessionDays: number; minPassword: number; loginAttempts: number; lockoutSeconds: number };
}

/** Single sign-on as an admin sees it. The client secret is never sent back — only whether one is
    held and its last four characters, exactly as a model key is reported. */
export interface SsoConfig {
  enabled: boolean;
  issuer: string;
  clientId: string;
  label: string;
  roleClaim: string;
  /** A claim value → the role it carries here. Never the Owner role: the API refuses that outright. */
  roleMap: Record<string, string>;
  defaultRoles: string[];
  createUsers: boolean;
  requireSso: boolean;
  redirectUri: string;
  hasSecret: boolean;
  secretMask: string | null;
  /** Whether a sign-in through it could actually happen: on, with an issuer, a client id and an address. */
  ready: boolean;
  /** What to add to this app’s own address to get the address to register at the provider. */
  callbackPath: string;
}
export interface SsoPatch {
  enabled?: boolean; issuer?: string; clientId?: string; clientSecret?: string; label?: string;
  roleClaim?: string; roleMap?: Record<string, string>; defaultRoles?: string[]; createUsers?: boolean;
  requireSso?: boolean; redirectUri?: string;
}

/** The fence around every command the runtime runs, as the machine actually offers it. */
export interface SandboxInfo {
  enabled: boolean;
  network: boolean;
  /** False when the server itself was started with the sandbox off, which a screen cannot undo. */
  serverAllows: boolean;
  detected: { kind: string; name: string; why: string };
  /** What is in force right now, including the one sentence a run’s log prints. */
  inForce: {
    kind: string; name: string; network: boolean; confinesWrites: boolean; confinesNetwork: boolean;
    writable: string[]; why: string; words: string;
  };
}

/** A lane is a provider and a model together. Several answer at once; free ones come first. */
export type LaneId = 'groq' | 'cloudflare' | 'gemini' | 'zai' | 'mistral' | 'openrouter' | 'deepseek' | 'cerebras' | 'ollama';
export type AiPreference = 'auto' | 'free' | 'local' | 'rules' | LaneId;
export interface AiLane {
  id: LaneId; label: string; model: string; baseUrl: string; api: 'openai' | 'ollama';
  /** Whether a call here costs no money. What the provider asks for instead is `gate`. */
  free: boolean;
  /** What its free tier asks for besides an account: '' (its documents name nothing), 'card', 'phone', 'identity'. */
  gate: '' | 'card' | 'phone' | 'identity';
  /** The whole of it in one line — the three meanings of "free", told apart, in the gateway's words. */
  freedom: string;
  /** When what is free here runs out for good (trial credit), or null when it does not. */
  expires: string | null;
  /** Calls a minute and a day the router allows itself. 0 means it sets no cap of its own. */
  rpm: number; rpd: number;
  /** Where those two came from: 'published' (the provider's, taken down) or 'ours' (it publishes none). */
  caps: 'published' | 'ours' | '';
  /** What the provider actually meters, in its own units and words. */
  allowance: string;
  /** Tokens a minute and a day as the provider publishes them — what a 2026 free tier really ends on. 0: unpublished. */
  tpm: number; tpd: number;
  /** True when the lane cannot be called until someone gives it a base URL of its own (an account id is in it). */
  needsBaseUrl: boolean;
  /** The most one call may ask for here, where the lane's own minute is smaller than a thinking call
      (Groq's free minute holds 8,000 tokens). 0 when the lane sets no such ceiling. */
  maxRequestTokens: number;
  goodAt: ('write' | 'review' | 'plan' | 'chat')[];
  needsKey: boolean; signup: string; note: string;
  enabled: boolean; hasKey: boolean; keyMask: string | null; keySource: 'workspace' | 'environment' | null;
  rejected: boolean;
  /** Can it take the next call? If not, `blocked` says why in plain words. */
  ready: boolean; blocked: string | null; allowed: boolean;
  /** Against its allowance: calls this minute and today, and tokens likewise (tokensToday only where a day's token budget is published). */
  spent: { minute: number; today: number; tokensMinute: number; tokensToday: number };
  /** Why the model it is set to no longer answers — the provider withdrew it — or null. */
  retired: string | null;
  /** The model's context window in tokens, from its provider's documents; null when none is published. */
  window: number | null;
  /** How the lane is told to think: DeepSeek's own fields, OpenAI-style effort, or null (it takes none). */
  thinks: 'deepseek' | 'effort' | null;
  /** The model names offered for this lane. */
  models: string[];
  /** Per model, per million tokens at the full rate. A cached input token has its own price. */
  prices: { model: string; usdPerMIn: number; usdPerMCached: number; usdPerMOut: number }[];
  /** A time-of-day discount: the factor off-peak, and the provider's rule in words. */
  offPeak: { factor: number; words: string } | null;
}
export type ThinkingLevel = 'off' | 'low' | 'high' | 'max';
export interface AiConfig {
  preference: AiPreference;
  /** NEUROCODE_COMPILER is set on the server, and it wins over the workspace setting. */
  preferenceLocked: boolean;
  active: CompilerInfo;
  deepseek: {
    hasKey: boolean; keyMask: string | null; keySource: 'workspace' | 'environment' | null;
    model: string; baseUrl: string; rejected: boolean;
    /** Set when the saved model is one DeepSeek has retired. */
    retired: string | null;
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
  /** feature → how hard it asks a model to think; "default" puts it back. */
  thinking?: Record<string, ThinkingLevel | 'default'>;
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
/** One piece retrieval found, and how it found it. */
export interface RetrievalHit {
  ref: string; kind: 'code' | 'doc' | 'memory'; path: string; title: string; line: number;
  text: string; score: number; how: 'both' | 'lexical' | 'semantic';
}
export interface RetrievalState {
  built: boolean; chunks: number; byKind: Partial<Record<'code' | 'doc' | 'memory', number>>;
  /** True once a lane has embedded the chunks: search is then by meaning as well as by words. */
  semantic: boolean;
  at?: string; ms?: number; embedded?: number; model?: string; lane?: string; note?: string;
  q: string; results: RetrievalHit[];
}
export interface CodeSummary {
  indexed: boolean;
  /** An index is being built right now. */
  indexing: boolean;
  /** The project's code is on this machine, so it can be indexed. */
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
  /** `reworkedAs`: the run that does this one's work again, once it was sent back for changes. */
  review: { findings: { severity: string; file: string; note: string }[]; verdict: string; by: string; reworkedAs?: string };
  diff: { files: number; insertions: number; deletions: number; commits: number };
  model: string | null;
  note: string;
  removed: boolean;
  /** `solo`: one agent. `agent`: one of several working in parallel. `integration`: the run that merges them. */
  role: 'solo' | 'agent' | 'integration' | 'check';
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
  /** The branch as last pushed to the project's own remote; null while it lives only on this machine. */
  pushed: { remote: string; branch: string; sha: string; at: string; by: string; compareUrl: string | null } | null;
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
  /** Prompt tokens the provider counted on the last call, and the answering model's window (null: unknown). */
  contextTokens: number | null; contextWindow: number | null;
  /** The share of the window at which older turns are folded before the next answer. */
  autoCompactAt: number;
  /** The project's instruction files its model is handed; absent when there was nothing to read. */
  instructions?: { path: string; bytes: number }[] | null;
  /** Forked from another session: its id, and the turn it was forked at. */
  parentId: string | null; forkedAt: number | null;
  /** What "Allow for this session" has allowed here: a tool, what it covers, and who allowed it. */
  grants: { tool: string; subject: string; covers: string | null; by: string; at: string }[];
  /** The permission card the session waits on — nothing moves until a person answers it. */
  waitingOn: { messageId: number; tool: string; subject: string } | null;
}
/** Something attached to a question: from the composer's @ picker, or an upload. */
export interface ChatAttachment {
  kind: 'file' | 'symbol' | 'fact' | 'plan' | 'upload'; ref: string; name: string;
  /** Characters of it the model was handed, and whether the cap cut it. */
  chars?: number; cut?: boolean;
  /** A symbol's file and line. */
  path?: string; line?: number;
  /** An upload: a picture goes only to a lane that reads images. */
  image?: boolean; mime?: string; bytes?: number;
  /** Imported from an export, which carries no uploaded bytes. */
  missing?: boolean;
}
/** A tool call that waits for a person — or waited, and what they said. */
export interface ChatPermission {
  tool: string; subject: string; why: string; ruleId: number | null; covers: string;
  state: 'pending' | 'allowed' | 'refused' | 'lapsed';
  scope?: 'once' | 'session' | 'refuse'; decidedBy?: string; decidedAt?: string;
}
/** One turn: your question, a tool call with what it found, an answer, a note, or a summary of folded turns. */
export interface ChatMessage {
  id: number; at: string; role: 'you' | 'assistant' | 'tool' | 'note' | 'summary'; text: string;
  by?: string; model?: string; lane?: LaneId; ms?: number;
  tool?: string; arguments?: Record<string, unknown>; why?: string; detail?: string; ok?: boolean;
  /** What the model reasoned before this turn, when the lane returned it. */
  reasoning?: string;
  /** How long (when it streamed) and how many tokens it reasoned. */
  thought?: { ms: number | null; tokens: number | null };
  /** On a summary: how many turns it folded, and their first and last ids. */
  folded?: { turns: number; from: number | null; to: number | null };
  /** A turn folded into a summary: still here to read, no longer sent to the model. */
  compacted?: boolean;
  attachments?: ChatAttachment[];
  /** A permission card (tool = 'permission'). */
  permission?: ChatPermission;
  /** Replaced by an edit or a regeneration: the id of the question that replaced it. */
  supersededBy?: number;
  /** A question that replaced another: by an edit, or asked again to regenerate its answer (and on which lane). */
  edited?: number; regenerated?: number;
}
export interface SessionDetail extends SessionDoc { messages: ChatMessage[]; parentRef: string | null }
/** The words of an answer being written, appended by position; never stored — the finished turn replaces them. */
export interface ChatStream {
  step: number; ms?: number;
  answer?: string; answerAt?: number; reasoning?: string; reasoningAt?: number;
  /** A lane failed halfway and the next one starts afresh. */
  restart?: boolean; lane?: string;
}
export type ChatEvent = (ChatMessage & { sessionRef: string; stream?: undefined }) | { sessionRef: string; stream: ChatStream };

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
/** `unused` and `invalid` name at most twenty indexes; the counts cover all of them. */
export interface IndexHealth {
  count: number; bytes: number; scans: number; statsSince: string | null;
  unused: string[]; unusedCount: number; invalid: string[]; invalidCount: number;
}
export interface DatabaseInfo {
  /** Which database is answering, as `host:port/name`. */
  path: string;
  /** "PostgreSQL", and the server version it reports. */
  engine: string; version: string;
  pageSize: number; pages: number; freePages: number;
  /** Postgres' `wal_level`: minimal · replica · logical. */
  walLevel: string;
  sizeBytes: number; walBytes: number; tables: { name: string; rows: number }[];
  /** Every index in the schema, and how many are unused or invalid. */
  indexes: number;
  indexHealth: IndexHealth;
  migrations: { version: number; name: string; appliedAt: string | null; revision: string }[];
  backups: BackupInfo[];
  backupDir: string | null;
}
/** What emptying the workspace did: the copy taken first (null where pg_dump is missing), and what each collection holds afterwards. */
export interface ResetResult { ok: boolean; backup: string | null; counts: Record<string, number>; compiler: CompilerInfo }
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

/** Why a request never got a usable answer, said so a person knows where to look. */
export function unreachable(e: unknown, path: string): string {
  if (e instanceof ApiError) {
    // In development a stopped API still answers — Vite's proxy replies 5xx with no body of its own.
    const words = e.message === `HTTP ${e.status}` ? '' : `: ${e.message}`;
    return `${API_BASE}${path} answered HTTP ${e.status}${words}.`;
  }
  if (e instanceof DOMException && e.name === 'TimeoutError') return `${API_BASE}${path} did not answer in time.`;
  return `Nothing answered at ${API_BASE}${path}.`;
}

export interface RequestOpts { method?: string; json?: unknown; headers?: Record<string, string>; signal?: AbortSignal }

/** One call to the API: the session cookie, the CSRF header, a 5 s timeout, and FastAPI's error words as an ApiError. */
export async function request<T>(path: string, { method = 'GET', json, headers, signal }: RequestOpts = {}): Promise<T> {
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

/** A collection as the store loads it: the rows, and whether the load stopped at a ceiling rather than at the end. */
export interface Loaded<T> {
  items: T[];
  /** True when the server's ceiling was reached, so there may be rows the store does not hold. No total is guessed. */
  capped: boolean;
}

/* The server's ceilings, as server/app/repositories/base.py sets them. A paged list answers at most
   MAX_LIMIT rows a page, and the most a whole walk goes to is EVERYTHING_CAP — the same line the server
   draws for its own admin lists. A list asked for with no limit answers only the first 100, newest
   first, and nothing in the answer says it stopped: that silently hid every older task, plan and gate. */
export const PAGE_MAX = 500;
export const LOAD_CAP = 5_000;

/** How many pages are asked for at a time once the first one comes back full (see walkPages). */
const PAGES_AT_ONCE = 3;

/** Every page of a paged list. The walk itself is in `@/lib/paging`, where it can be tested on its own. */
const everyPage = <T extends { id: string }>(path: string, params: Record<string, string> = {}): Promise<Loaded<T>> =>
  walkPages<T>(
    (offset, limit) =>
      request<T[]>(`${path}?${new URLSearchParams({ ...params, limit: String(limit), offset: String(offset) })}`,
        { signal: AbortSignal.timeout(15_000) }),
    { pageMax: PAGE_MAX, loadCap: LOAD_CAP, pagesAtOnce: PAGES_AT_ONCE },
  );

/** A list the server answers in one go, up to a fixed ceiling of its own: reaching it may mean more rows exist. */
const upTo = (ceiling: number) => <T,>(items: T[]): Loaded<T> => ({ items, capped: items.length >= ceiling });

/** How the change stream is doing. `reconnecting`: it dropped, and what changed meanwhile is not replayed. */
export type StreamState = 'open' | 'reconnecting';
/**
 * Why the workspace has to be loaded again. All three mean the same thing to the caller — what is on
 * screen may no longer be true — and differ only in what happened:
 * `reopened` the stream dropped and came back, `reset` the workspace was emptied, `missed` this reader
 * was too slow and the server dropped events on the way to it.
 */
export type ResyncReason = 'reopened' | 'reset' | 'missed';
/* After the connection closes for good (the stream answered with an error status, which EventSource
   hides), it is opened again after this long — unless the session turns out to be gone. */
const REOPEN_MS = 5_000;
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
  /** Where to send the browser to sign in at the identity provider. The state and nonce that must
      come back with it are set as a cookie by this call, and never touched by any script here. */
  ssoStart: () => request<{ url: string }>('/auth/sso/start', { ...POST(), signal: AbortSignal.timeout(15_000) }),
  changePassword: (current: string, next: string) => request<{ ok: boolean }>('/auth/password', POST({ current, new: next })),
  /** Permissions, roles and agents by name, for anyone signed in: a Viewer's screens need the labels too. */
  catalogue: () => request<Catalogue>('/auth/catalogue'),

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
    sso: () => request<SsoConfig>('/admin/sso'),
    updateSso: (patch: SsoPatch) => request<SsoConfig>('/admin/sso', { method: 'PUT', json: patch, signal: AbortSignal.timeout(15_000) }),
    sandbox: () => request<SandboxInfo>('/admin/sandbox'),
    updateSandbox: (body: { enabled: boolean; network: boolean }) =>
      request<SandboxInfo>('/admin/sandbox', { method: 'PUT', json: body }),
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
    retrieval: (pid: string, q = '') => request<RetrievalState>(
      `/projects/${seg(pid)}/code/retrieval?q=${encodeURIComponent(q)}`, { signal: AbortSignal.timeout(30_000) }),
    buildRetrieval: (pid: string) => request<{ ok: boolean }>(`/projects/${seg(pid)}/code/retrieval/build`, POST()),
  },

  /* agent runs: a worktree of their own, and everything they did in it */
  runs: () => everyPage<RunDoc>('/runs'),
  run: (ref: string, after = 0) => request<RunDetail>(`/runs/${seg(ref)}?after=${after}`),
  runDiff: (ref: string) => request<RunDiff>(`/runs/${seg(ref)}/diff`, { signal: AbortSignal.timeout(15_000) }),
  cancelRun: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/cancel`, POST()),
  mergeRun: (ref: string) => request<MergeResult>(`/runs/${seg(ref)}/merge`, { ...POST(), signal: AbortSignal.timeout(120_000) }),
  discardRun: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/discard`, POST()),
  unmergeRun: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/unmerge`, { ...POST(), signal: AbortSignal.timeout(60_000) }),

  sessions: (project?: string) => request<SessionDoc[]>(`/sessions${project ? `?project=${encodeURIComponent(project)}` : ''}`),
  session: (ref: string, after = 0) => request<SessionDetail>(`/sessions/${seg(ref)}?after=${after}`),
  newSession: (projectId: string, title = '') => request<SessionDoc>('/sessions', POST({ projectId, title })),
  /** Ask, and let it think in the background: the turns arrive on the stream. */
  askSession: (ref: string, text: string, attachments: Pick<ChatAttachment, 'kind' | 'ref' | 'name'>[] = []) =>
    request<{ message: ChatMessage; session: SessionDoc }>(`/sessions/${seg(ref)}/messages`, POST({ text, attachments })),
  cancelSession: (ref: string) => request<SessionDoc>(`/sessions/${seg(ref)}/cancel`, POST()),
  compactSession: (ref: string) =>
    request<{ summary: ChatMessage; session: SessionDoc }>(`/sessions/${seg(ref)}/compact`, { ...POST(), signal: modelTimeout() }),

  /** The AI gateway's ledger: every model call and every offline answer. */
  usage: (days = 30) => request<UsageReport>(`/usage?days=${days}`),

  /* AI features */
  ask: (question: string, projectId?: string) => request<AskAnswer>('/ai/ask', { ...POST({ question, projectId }), signal: modelTimeout() }),
  brainstorms: () => everyPage<BrainstormDoc>('/ai/brainstorms'),
  brainstorm: (idea: string, projectId?: string) =>
    request<BrainstormDoc>('/ai/brainstorm', { ...POST({ idea, projectId }), signal: modelTimeout() }),
  extract: (text: string, projectId?: string) => request<Extracted>('/ai/extract', { ...POST({ text, projectId }), signal: modelTimeout() }),

  /* the work */
  /** The server lists at most 200 projects (ProjectRepository.all_ordered). */
  projects: () => request<Project[]>('/projects').then(upTo(200)),
  createProject: (input: ProjectInput) => request<Project>('/projects', POST(input)),

  /** Newest first. `pending` asks for only the gates still waiting, however old. */
  approvals: (status?: 'pending') => everyPage<ApprovalRequest>('/approvals', status ? { status } : {}),
  decide: (ref: string, decision: 'approve' | 'deny') => request<ApprovalRequest>(`/approvals/${seg(ref)}/${decision}`, POST()),

  tasks: () => everyPage<Task>('/tasks'),
  moveTask: (ref: string, status: TaskStatus) => request<Task>(`/tasks/${seg(ref)}`, PATCH({ status })),
  check: (ref: string, itemId: string, done: boolean) =>
    request<Task>(`/tasks/${seg(ref)}/checklist/${seg(itemId)}`, POST({ done })),

  plans: () => everyPage<Plan>('/plans'),
  compile: (requirement: string, projectId: string) =>
    request<Plan>('/plans/compile', { ...POST({ requirement, projectId }), signal: modelTimeout() }),
  recompile: (ref: string) => request<Plan>(`/plans/${seg(ref)}/recompile`, { ...POST(), signal: modelTimeout() }),
  settle: (ref: string, index: number, body: { answer: string } | { defer: true }) =>
    request<Plan>(`/plans/${seg(ref)}/questions/${index}`, POST(body)),
  dispatch: (ref: string) => request<Plan>(`/plans/${seg(ref)}/dispatch`, POST()),

  /** Postgres full-text search on the server, best match first. */
  memory: (q = '', signal?: AbortSignal) => request<MemoryFact[]>(`/memory?${new URLSearchParams({ q })}`, { signal }),
  /** Every live fact, for the store: the list has no paging, and stops at 200 (MemoryFactRepository.search). */
  facts: () => request<MemoryFact[]>('/memory', { signal: AbortSignal.timeout(15_000) }).then(upTo(200)),
  /** `projectId: null` files the facts under the workspace rather than one project. */
  addFacts: (projectId: string | null, facts: FactCandidate[]) =>
    request<MemoryFact[]>('/memory/facts', POST({ ...(projectId ? { projectId } : {}), facts })),
  pin: (ref: string, pinned: boolean) => request<MemoryFact>(`/memory/${seg(ref)}/pin`, POST({ pinned })),
  archive: (ref: string) => request<MemoryFact>(`/memory/${seg(ref)}/archive`, POST()),
  /** Only the open ones, oldest first, and no more than the server's default page of 100. */
  conflicts: () => request<MemoryConflict[]>('/memory/conflicts').then(upTo(100)),
  /** The answer is the conflict's new state only; the whole document arrives on the stream. */
  resolveConflict: (id: string, keep: 'a' | 'b') =>
    request<ConflictResolution>(`/memory/conflicts/${seg(id)}/resolve`, POST({ keep })),

  /** At most 200 (McpRepository.all_ordered). */
  mcp: () => request<McpServer[]>('/mcp/servers').then(upTo(200)),
  registerMcp: (input: McpInput) => request<McpServer>('/mcp/servers', POST(input)),

  /** At most 500 (PrefRepository.all_ordered). */
  prefs: () => request<Pref[]>('/prefs').then(upTo(500)),
  setPref: (key: string, value: unknown, detail?: string) =>
    request<Pref>(`/prefs/${seg(key)}`, { method: 'PUT', json: { value, detail: detail ?? '' } }),
  /** At most 200 (DecisionRepository.all_ordered). */
  decisions: () => request<DecisionDoc[]>('/decisions').then(upTo(200)),
  /** A null projectId leaves the field out: the decision belongs to the workspace. */
  recordDecision: (key: string, { projectId, ...body }: { value: string; action: string; detail: string; projectId: string | null; level: string }) =>
    request<DecisionDoc>(`/decisions/${seg(key)}`, POST({ ...body, ...(projectId ? { projectId } : {}) })),

  /** The newest 500 lines: the feed's own ceiling (FEED_CAP), and a feed never needed the whole history. */
  activity: () => request<ActivityEvent[]>(`/activity?limit=${PAGE_MAX}`).then(upTo(PAGE_MAX)),
  /** Empties the workspace: every project and what it holds. People, roles, the roster, keys and the audit log stay. */
  reset: () => request<ResetResult>('/admin/reset', { method: 'POST', headers: { 'X-Confirm': 'reset' }, signal: AbortSignal.timeout(120_000) }),

  /**
   * Server-Sent Events: each log line, each document that changed, and an agent run's output.
   *
   * The server keeps no replay of changes, so a stream that drops has missed whatever changed while it
   * was away. EventSource reconnects on its own after a network drop; this says so through `state`, and
   * when it is back, asks for a `resync` — the caller loads the workspace again. A stream refused
   * outright (an error status, most often a 401 once the session has ended) is closed for good by the
   * browser, which never says why: the session is asked about, and a person no longer signed in is
   * signed out; otherwise the stream is opened again a little later. The server's `reset` event (the
   * workspace was emptied, which no per-document change can describe) is a resync too, and so is its
   * `resync` event, which is how a reader that fell too far behind is told that events went past it.
   */
  stream(on: {
    activity: (e: ActivityEvent) => void;
    change: (c: Change) => void;
    log?: (l: RunLogEvent) => void;
    chat?: (m: ChatEvent) => void;
    state?: (s: StreamState) => void;
    resync?: (why: ResyncReason) => void;
  }): () => void {
    let es: EventSource | null = null;
    let stopped = false;
    let dropped = false;
    let reopen: number | undefined;
    const data = <T,>(m: Event) => JSON.parse((m as MessageEvent<string>).data) as T;

    const later = () => {
      if (!stopped) reopen = window.setTimeout(connect, REOPEN_MS);
    };
    function connect() {
      const source = new EventSource(`${API_BASE}/activity/stream`, { withCredentials: true });
      es = source;
      source.addEventListener('activity', (m) => on.activity(data<ActivityEvent>(m)));
      source.addEventListener('change', (m) => on.change(data<Change>(m)));
      source.addEventListener('run', (m) => on.log?.(data<RunLogEvent>(m)));
      source.addEventListener('chat', (m) => on.chat?.(data<ChatEvent>(m)));
      source.addEventListener('reset', () => on.resync?.('reset'));
      // The server's own resync frame. It sends one when this reader fell behind — its queue filled and
      // events went past it — which nothing about the connection would ever have told us: the socket is
      // healthy and the screens are quietly stale. A reason this bundle does not know is read the same
      // way, because that is exactly what it means: something happened that we did not see.
      source.addEventListener('resync', (m) => {
        on.resync?.(data<{ why?: string }>(m).why === 'reset' ? 'reset' : 'missed');
      });
      source.addEventListener('open', () => {
        on.state?.('open');
        if (dropped) {
          dropped = false;
          on.resync?.('reopened');
        }
      });
      source.addEventListener('error', () => {
        if (stopped) return;
        dropped = true;
        on.state?.('reconnecting');
        // Still CONNECTING: the browser is retrying by itself, at the pace the server asked for.
        if (source.readyState !== EventSource.CLOSED) return;
        source.close();
        // /auth/status answers even with no session (user: null), so it can tell a 401 from an API that is down.
        request<AuthStatus>('/auth/status', { signal: AbortSignal.timeout(5000) }).then(
          (s) => {
            if (stopped) return;
            if (!s.user) window.dispatchEvent(new Event(SIGNED_OUT));
            else later();
          },
          later,
        );
      });
    }

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(reopen);
      es?.close();
    };
  },
};
