/* ═══════════════════════════════════════════════════════════════
   NEUROCODE — domain types
   Single source of truth for every screen's mock data.
   ═══════════════════════════════════════════════════════════════ */

export type Health = 'ok' | 'warn' | 'danger' | 'info' | 'idle';
export type Risk = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type Confidence = 'LOW' | 'MEDIUM' | 'HIGH';

/* ── Projects ─────────────────────────────────────────────────── */
export interface ProjectStat { label: string; value: string }
export interface Coverage { label: string; pct: number }

export interface Project {
  id: string;
  name: string;
  codename: string;
  stack: string[];
  kind: 'legacy' | 'greenfield' | 'platform';
  status: 'active' | 'onboarding' | 'paused' | 'archived';
  memoryPct: number;
  understoodPct: number;
  lines: string;
  modules: number;
  dbTables: number;
  storedProcs: number;
  repo: string;
  lastActive: string;
  coverage: Coverage[];
  work: { tasks: number; running: number; review: number; blocked: number };
  description: string;
  /** Set on projects onboarded from this app; the seeded ones have none. */
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

/* ── Agents ───────────────────────────────────────────────────── */
export type AgentStatus = 'running' | 'idle' | 'waiting' | 'blocked' | 'error' | 'disabled';

export interface Agent {
  id: string;
  name: string;
  role: string;
  icon: string;
  model: string;
  fallbackModel?: string;
  status: AgentStatus;
  tools: string[];
  skills: string[];
  autonomy: 'supervised' | 'semi' | 'autonomous';
  tasksDone: number;
  successRate: number;
  avgMinutes: number;
  tokens24h: number;
  cost24h: number;
  systemPrompt: string;
  guardrails: string[];
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
  confidence: number;
  createdAt: string;
  steps: PlanStep[];
  testPlan: string[];
  openQuestions: string[];
  /** Compiled plans: `draft` until dispatched. Seeded plans are read from their steps instead. */
  status?: 'draft' | 'dispatched';
  answered?: { q: string; a: string }[];
  deferred?: string[];
  /** The memory facts the compiler was given. */
  cited?: string[];
  compiler?: { provider: 'deepseek' | 'ollama' | 'rules'; model: string; ms: number };
}

/* ── Live runs (parallel agents) ──────────────────────────────── */
export interface RunLogLine { t: string; level: 'info' | 'ok' | 'warn' | 'err' | 'tool'; text: string }
export interface AgentRun {
  id: string;
  agentId: string;
  agentName: string;
  taskRef: string;
  projectId: string;
  status: AgentStatus;
  progress: number;
  step: string;
  worktree: string;
  model: string;
  startedAt: string;
  elapsed: string;
  tokensIn: number;
  tokensOut: number;
  cost: number;
  filesTouched: string[];
  log: RunLogLine[];
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
  projectId: string | 'global';
  confidence: Confidence;
  strength: number;
  hits: number;
  createdAt: string;
  lastUsed: string;
  evidence: string[];
  tags: string[];
  pinned: boolean;
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

/* ── Code intelligence ────────────────────────────────────────── */
export interface CodeNode {
  id: string;
  name: string;
  path: string;
  kind: 'folder' | 'file' | 'class' | 'sproc' | 'table' | 'component';
  lang?: string;
  children?: CodeNode[];
  loc?: number;
  risk?: Risk;
  lastChanged?: string;
  owner?: string;
}

export interface SymbolDetail {
  id: string;
  name: string;
  path: string;
  kind: string;
  loc: number;
  complexity: number;
  churn: number;
  risk: Risk;
  lastChanged: string;
  summary: string;
  dependencies: string[];
  usedBy: string[];
  database: string[];
  storedProcedures: string[];
  tests: string[];
  knownBugs: string[];
  decisions: string[];
}

/* ── Architecture / impact ────────────────────────────────────── */
export interface GraphNode {
  id: string;
  label: string;
  layer: 'ui' | 'api' | 'service' | 'repo' | 'sproc' | 'table' | 'external';
  risk: Risk;
  x: number;
  y: number;
}
export interface GraphEdge { from: string; to: string; kind: 'calls' | 'reads' | 'writes' | 'depends' }

export interface ImpactReport {
  target: string;
  risk: Risk;
  confidence: number;
  modules: number;
  apis: number;
  tests: number;
  legacyDeps: number;
  blastRadius: { label: string; items: string[] }[];
  warnings: string[];
  recommendation: string;
}

/* ── Testing ──────────────────────────────────────────────────── */
export interface TestSuite {
  id: string;
  name: string;
  kind: 'build' | 'lint' | 'unit' | 'integration' | 'api' | 'e2e' | 'security' | 'regression' | 'visual';
  status: 'pass' | 'fail' | 'warn' | 'running' | 'skipped';
  passed: number;
  total: number;
  durationS: number;
  runner: string;
}
export interface FailedTest {
  id: string;
  name: string;
  suite: string;
  reason: string;
  legacyExpected: boolean;
  aiRecommendation: string;
  file: string;
  line: number;
  diff?: string;
}

/* ── Review ───────────────────────────────────────────────────── */
export interface ReviewCheck { id: string; label: string; status: 'pass' | 'fail' | 'warn'; note: string }
export interface ReviewFinding {
  id: string;
  severity: 'blocker' | 'major' | 'minor' | 'nit';
  file: string;
  line: number;
  rule: string;
  message: string;
  suggestion: string;
  agent: string;
}
export interface Review {
  id: string;
  ref: string;
  taskRef: string;
  projectId: string;
  reviewer: string;
  verdict: 'approved' | 'changes_requested' | 'pending';
  round: number;
  createdAt: string;
  checks: ReviewCheck[];
  findings: ReviewFinding[];
  reviewerNote: string;
  filesChanged: number;
  additions: number;
  deletions: number;
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

/* ── MCP / tools / ACP ────────────────────────────────────────── */
export interface McpServer {
  id: string;
  name: string;
  transport: 'stdio' | 'http' | 'sse';
  status: 'connected' | 'disconnected' | 'error' | 'auth_required';
  scope: 'global' | 'project' | 'local';
  command: string;
  tools: { name: string; description: string; risk: Risk }[];
  resources: number;
  prompts: number;
  latencyMs: number;
  calls24h: number;
  errorRate: number;
  /** Registered from this app: its output stays untrusted until you promote it. */
  untrusted?: boolean;
  defaultEffect?: 'ask' | 'allow-read' | 'deny';
  /** The config exactly as it was reviewed in the wizard. */
  config?: string;
}

export interface AcpClient {
  id: string;
  name: string;
  editor: string;
  version: string;
  status: 'connected' | 'idle' | 'disconnected';
  sessionId: string;
  capabilities: string[];
  permissionMode: string;
  lastPing: string;
  messages: number;
}

/* ── Models ───────────────────────────────────────────────────── */
export interface ModelEntry {
  id: string;
  name: string;
  vendor: string;
  kind: 'reasoning' | 'coding' | 'vision' | 'small' | 'embedding' | 'reranker';
  hosting: 'local' | 'remote';
  contextK: number;
  inPer1M: number;
  outPer1M: number;
  status: 'ready' | 'loading' | 'offline';
  latencyMs: number;
  quality: number;
  calls24h: number;
  cost24h: number;
  notes: string;
}
export interface RoutingRule {
  id: string;
  when: string;
  route: string;
  fallback: string;
  enabled: boolean;
  hits24h: number;
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

export type HookEvent =
  | 'SessionStart' | 'UserPromptSubmit' | 'PreToolUse' | 'PostToolUse'
  | 'Stop' | 'SubagentStop' | 'PreCompact' | 'Notification' | 'PreCommit' | 'PostDeploy';

export interface Hook {
  id: string;
  event: HookEvent;
  matcher: string;
  command: string;
  scope: 'global' | 'project';
  enabled: boolean;
  blocking: boolean;
  fires24h: number;
  lastFired: string;
  lastResult: 'ok' | 'blocked' | 'error';
  description: string;
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

/* ── Workflows (deterministic orchestration) ──────────────────── */
export interface WorkflowPhase {
  id: string;
  title: string;
  detail: string;
  mode: 'parallel' | 'pipeline' | 'single' | 'loop';
  agents: number;
  state: PlanStepState;
}
export interface WorkflowDef {
  id: string;
  name: string;
  description: string;
  trigger: string;
  phases: WorkflowPhase[];
  runs: number;
  avgAgents: number;
  avgMinutes: number;
  lastRun: string;
  lastResult: 'success' | 'failed' | 'partial';
  scope: 'global' | 'project';
}

/* ── DevOps ───────────────────────────────────────────────────── */
export interface Environment {
  id: string;
  name: string;
  kind: 'local' | 'staging' | 'production';
  status: Health;
  url: string;
  version: string;
  deployedAt: string;
  uptime: string;
  cpu: number;
  mem: number;
  requests: string;
  errorRate: number;
  requiresApproval: boolean;
}
export interface Deployment {
  id: string;
  env: string;
  version: string;
  status: 'success' | 'failed' | 'running' | 'rolled_back' | 'awaiting_approval';
  by: string;
  at: string;
  durationS: number;
  commit: string;
}
export interface Container { id: string; name: string; image: string; status: 'up' | 'down' | 'restarting'; cpu: number; mem: string; ports: string }

/* ── Research / brainstorm ────────────────────────────────────── */
export interface ResearchReport {
  id: string;
  ref: string;
  question: string;
  status: 'complete' | 'running' | 'queued';
  createdAt: string;
  durationS: number;
  agents: number;
  sources: { kind: string; count: number }[];
  summary: string;
  findings: string[];
  alternatives: { name: string; pros: string[]; cons: string[]; verdict: string }[];
  risks: string[];
  recommendation: string;
  citations: { label: string; url: string }[];
  confidence: number;
}

export interface BrainstormNode { id: string; stage: string; title: string; items: string[] }
export interface BrainstormSession {
  id: string;
  ref: string;
  idea: string;
  createdAt: string;
  status: 'complete' | 'running';
  nodes: BrainstormNode[];
  devilsAdvocate: string[];
  mvp: string[];
  roadmap: { phase: string; weeks: string; items: string[] }[];
  verdict: string;
  score: number;
}

/* ── Activity ─────────────────────────────────────────────────── */
export interface ActivityEvent {
  id: string;
  t: string;
  actor: string;
  actorKind: 'human' | 'agent' | 'system' | 'hook' | 'tool';
  action: string;
  detail: string;
  projectId: string;
  level: 'info' | 'ok' | 'warn' | 'err';
  taskRef?: string;
}

/* ── Sessions / checkpoints ───────────────────────────────────── */
export interface Checkpoint {
  id: string;
  label: string;
  at: string;
  files: number;
  tokens: number;
  restorable: boolean;
}
export interface Session {
  id: string;
  ref: string;
  title: string;
  projectId: string;
  startedAt: string;
  duration: string;
  status: 'active' | 'ended' | 'forked';
  messages: number;
  tokens: number;
  cost: number;
  agents: string[];
  checkpoints: Checkpoint[];
  summary: string;
}

/* ── Permissions ──────────────────────────────────────────────── */
export interface PermissionRule {
  id: string;
  pattern: string;
  tool: string;
  effect: 'allow' | 'ask' | 'deny';
  risk: Risk;
  scope: 'global' | 'project';
  hits24h: number;
  note: string;
}
export interface ApprovalRequest {
  id: string;
  ref: string;
  title: string;
  agent: string;
  tool: string;
  risk: Risk;
  requestedAt: string;
  projectId: string;
  payload: string;
  reason: string;
  status: 'pending' | 'approved' | 'denied';
}

/* ── Cost ─────────────────────────────────────────────────────── */
export interface CostBucket { label: string; tokensIn: number; tokensOut: number; cost: number; calls: number; share: number }
export interface CostDay { day: string; cost: number; local: number; remote: number }

/* ── Evals ────────────────────────────────────────────────────── */
export interface EvalSuite {
  id: string;
  name: string;
  target: string;
  kind: 'regression' | 'capability' | 'safety' | 'cost';
  cases: number;
  passed: number;
  score: number;
  delta: number;
  lastRun: string;
  status: 'pass' | 'fail' | 'warn' | 'running';
}
export interface EvalCase {
  id: string;
  suite: string;
  name: string;
  expected: string;
  got: string;
  status: 'pass' | 'fail' | 'partial';
  scoreDelta: number;
  judge: string;
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

/* ── Settings ─────────────────────────────────────────────────── */
export interface SettingGroup {
  id: string;
  name: string;
  items: { id: string; label: string; description: string; kind: 'toggle' | 'select' | 'text' | 'secret'; value: string | boolean; options?: string[] }[];
}
