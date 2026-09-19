/* ═══════════════════════════════════════════════════════════════
   NEUROCODE — domain types
   The shapes the screens read. Where a document comes from the
   API, its type here is what the API sends.
   ═══════════════════════════════════════════════════════════════ */

export type Risk = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type Confidence = 'LOW' | 'MEDIUM' | 'HIGH';

/* ── Projects ─────────────────────────────────────────────────── */
export interface Coverage { label: string; pct: number }

export interface Project {
  id: string;
  name: string;
  codename: string;
  stack: string[];
  kind: 'legacy' | 'greenfield' | 'platform';
  status: 'active' | 'onboarding' | 'paused' | 'archived';
  /** The share of the source files the onboarding scan found that the code index holds. Null until indexed. */
  understoodPct: number | null;
  lines: string;
  modules: number;
  dbTables: number;
  storedProcs: number;
  repo: string;
  lastActive: string;
  coverage: Coverage[];
  work: { tasks: number; running: number; review: number; blocked: number };
  description: string;
  /** Where the code was onboarded from. */
  source?: { kind: 'git' | 'local'; repo: string; branch?: string };
  /** Rules chosen in the onboarding wizard. */
  rules?: { id: string; label: string; note: string }[];
  /** Measured by the onboarding scan. */
  languages?: { name: string; pct: number }[];
  files?: number;
  /** The latest code index, when the project's code is on this machine. `at` changes with every index. */
  codeIndex?: { files: number; symbols: number; edges: number; unresolved: number; ms: number; at: string };
  /** Glob patterns the onboarding wizard left out; re-indexing keeps leaving them out. */
  excluded?: string[];
}

/* ── Tasks / Plans ────────────────────────────────────────────── */
export type TaskStatus = 'backlog' | 'planning' | 'in_progress' | 'review' | 'blocked' | 'done';
export type Priority = 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT';

export interface TaskChecklistItem { id: string; label: string; done: boolean }

export interface Task {
  id: string;
  ref: string;
  title: string;
  projectId: string;
  status: TaskStatus;
  priority: Priority;
  risk: Risk;
  epic?: string;
  layers: string[];
  agents: string[];
  files: number;
  tests: number;
  progress: number;
  createdAt: string;
  updatedAt: string;
  requirement: string;
  worktree?: string;
  checklist: TaskChecklistItem[];
  blockedReason?: string;
}

export type PlanStepState = 'done' | 'active' | 'todo' | 'failed' | 'skipped';
export interface PlanStep {
  id: string;
  n: number;
  label: string;
  agent: string;
  state: PlanStepState;
  detail: string;
  durationS?: number;
}

export interface Plan {
  id: string;
  ref: string;
  taskRef: string;
  projectId: string;
  rawRequirement: string;
  businessRequirement: string;
  technicalRequirement: string;
  affectedModules: string[];
  affectedFiles: string[];
  affectedDb: string[];
  architectureImpact: string;
  risk: Risk;
  /** What the compiler said of itself; null for a plan it did not compile (a workflow's). */
  confidence: number | null;
  createdAt: string;
  steps: PlanStep[];
  testPlan: string[];
  openQuestions: string[];
  /** `draft` until dispatched. */
  status?: 'draft' | 'dispatched';
  /** The workflow that ran the plan, once one has. */
  workflowId: string | null;
  answered?: { q: string; a: string }[];
  deferred?: string[];
  /** The memory facts the compiler was given. */
  cited?: string[];
  /** The code and documents retrieval handed the compiler; empty for a plan compiled before that, or from a workflow. */
  grounding?: { kind: string; ref: string; path: string }[];
  /** Null for a plan a workflow wrote rather than the compiler. */
  compiler?: { provider: string; model: string; ms: number } | null;
}

/* ── Memory ───────────────────────────────────────────────────── */
export type MemoryCategory =
  | 'human' | 'project' | 'architecture' | 'business_rules'
  | 'legacy' | 'database' | 'bugs' | 'decisions' | 'incidents' | 'preferences' | 'code';

export interface MemoryFact {
  id: string;
  ref: string;
  category: MemoryCategory;
  title: string;
  body: string;
  reason: string;
  source: string;
  /** Null for a fact that belongs to the workspace, and so to every project. */
  projectId: string | null;
  confidence: Confidence;
  /** Recalls in the last 24 hours: each time a feature cited the fact or handed it to a model. */
  hits24h: number;
  createdAt: string;
  /** The latest recall; null when nothing has used it yet. */
  lastUsedAt: string | null;
  evidence: string[];
  tags: string[];
  pinned: boolean;
  archived: boolean;
  archivedAt: string | null;
}

/** Two facts that cannot both be true. `a` and `b` are the facts' ids. */
export interface MemoryConflict {
  id: string;
  topic: string;
  a: string;
  b: string;
  detected: string;
  detail: string;
  suggestion: string;
  severity: 'HIGH' | 'MEDIUM' | 'LOW';
  /** A resolved conflict is kept, not deleted, so it still streams as a change — as `resolved`. */
  status: 'open' | 'resolved';
  /** Which side was kept, and by whom. Only once resolved. */
  resolution?: ConflictResolution['resolution'];
}

/** What resolving a conflict answers: the conflict's id and its new state, not the whole document. */
export interface ConflictResolution {
  id: string;
  status: 'resolved';
  resolution: { keep: 'a' | 'b'; by: string };
}

/* ── Knowledge ────────────────────────────────────────────────── */
export type DocKind =
  | 'requirement' | 'meeting' | 'screenshot' | 'pdf' | 'spec'
  | 'ticket' | 'email' | 'git' | 'schema' | 'transcript' | 'url';

export interface KnowledgeDoc {
  id: string;
  ref: string;
  kind: DocKind;
  title: string;
  projectId: string;
  source: string;
  addedAt: string;
  size: string;
  chunks: number;
  indexed: boolean;
  entities: string[];
  summary: string;
  linkedTo: string[];
}

/* ── Git / worktrees ──────────────────────────────────────────── */
export interface Worktree {
  id: string;
  branch: string;
  path: string;
  agent: string;
  taskRef: string;
  status: 'clean' | 'dirty' | 'conflict' | 'merged' | 'ahead';
  filesChanged: number;
  additions: number;
  deletions: number;
  ahead: number;
  behind: number;
  lastCommit: string;
  lastCommitAt: string;
}
export interface Commit { sha: string; message: string; author: string; at: string; files: number; branch: string }

/* ── MCP ──────────────────────────────────────────────────────── */
export interface McpServer {
  id: string;
  name: string;
  transport: 'stdio' | 'http' | 'sse';
  status: 'connected' | 'disconnected' | 'error' | 'auth_required';
  scope: 'global' | 'project' | 'local';
  command: string;
  /** What the last check found. Empty, and the numbers null, until a check has run. */
  tools: { name: string; description: string; risk: Risk }[];
  resources: number | null;
  prompts: number | null;
  /** The round trip of the check's tools/list request. */
  latencyMs: number | null;
  checkedAt: string | null;
  /** Why the last check did not connect; empty when it did. */
  lastError: string;
  /** Registered from this app: untrusted until someone trusts it, and a stdio server's command is never launched until then. */
  untrusted?: boolean;
  defaultEffect?: 'ask' | 'allow-read' | 'deny';
  /** The config exactly as it was reviewed in the wizard. */
  config?: string;
}

/* ── Skills / commands / hooks / plugins ──────────────────────── */
export interface Skill {
  id: string;
  name: string;
  slug: string;
  description: string;
  scope: 'global' | 'project' | 'plugin';
  source: string;
  enabled: boolean;
  triggers: string[];
  tools: string[];
  usedBy: string[];
  invocations: number;
  lastUsed: string;
  version: string;
}

export interface SlashCommand {
  id: string;
  name: string;
  description: string;
  scope: 'global' | 'project' | 'plugin';
  args: string;
  agent: string;
  model?: string;
  runs: number;
  lastRun: string;
  body: string;
  enabled: boolean;
}

export interface Plugin {
  id: string;
  name: string;
  publisher: string;
  version: string;
  installed: boolean;
  description: string;
  provides: { agents: number; skills: number; commands: number; hooks: number; mcp: number };
  stars: number;
  category: string;
  updatedAt: string;
}

/* ── Activity ─────────────────────────────────────────────────── */
export interface ActivityEvent {
  id: string;
  /** The time of day, HH:MM:SS, for a feed row. */
  t: string;
  /** When it happened, ISO 8601 at full precision: what grouping by day and ordering read. */
  at: string;
  actor: string;
  actorKind: 'human' | 'agent' | 'system' | 'hook' | 'tool';
  action: string;
  detail: string;
  /** Null for what happened to the workspace rather than to one project. */
  projectId: string | null;
  level: 'info' | 'ok' | 'warn' | 'err';
  taskRef?: string;
}

/* ── Permissions ──────────────────────────────────────────────── */
export interface ApprovalRequest {
  id: string;
  ref: string;
  title: string;
  agent: string;
  tool: string;
  risk: Risk;
  requestedAt: string;
  projectId: string | null;
  payload: string;
  reason: string;
  status: 'pending' | 'approved' | 'denied';
  /** Set once decided. `decidedBy` is the person's id, and absent when no person closed it. */
  decidedAt?: string;
  decidedBy?: string;
  /** The run stopped at this gate, and which of its steps. */
  runRef?: string;
  step?: number;
}

/* ── Global search ────────────────────────────────────────────── */
export interface SearchHit {
  id: string;
  group: string;
  title: string;
  subtitle: string;
  to: string;
  icon: string;
  meta?: string;
}

