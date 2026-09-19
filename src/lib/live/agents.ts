import { request, type LaneId, type RunStep, type SessionDoc } from '@/lib/api';

/* The Agents screen's live data: GET /agents. Everything on a card is derived on the server — status
   from the runs, lanes from the router, the record from the steps, spend from the ledger. What a person
   typed on the agent's row arrives only under `declared`, because nothing in the runtime reads it. */

export type LiveAgentStatus = 'running' | 'waiting' | 'idle' | 'disabled';

export interface LaneRef { lane: LaneId; model: string }

export interface AgentOutcome {
  kind: 'edit' | 'test' | 'review';
  /** What the verdict measures: "edits written", "project tests passed", "model-read reviews". */
  label: string;
  decided: number;
  good: number;
  /** Null until a step of this kind reached a verdict. */
  rate: number | null;
  avgMinutes: number | null;
}

export interface AgentCurrent {
  runRef: string;
  taskRef: string | null;
  status: 'running' | 'waiting';
  branch: string;
  worktree: string;
  startedAt: string;
  progress: number;
  step: string | null;
  stepKind: RunStep['kind'] | null;
  stepStatus: RunStep['status'] | null;
  filesChanged: number;
  /** Summed over the ledger lines that name this run, never the agent's other runs. */
  tokensIn: number;
  tokensOut: number;
}

export interface LiveAgent {
  id: string;
  name: string;
  role: string;
  icon: string;
  status: LiveAgentStatus;
  tasksDone: number;
  outcomes: AgentOutcome[];
  tokens24h: number;
  /** null when any of it went to a lane with no declared price. */
  cost24h: number | null;
  /** The role the runtime asks a lane for as this agent, or null when it never makes a model call. */
  callsAs: 'write' | 'review' | null;
  noCall: string | null;
  lanes: {
    /** What the router would try now. A snapshot: lanes rotate between runs. */
    primary: LaneRef | null;
    fallback: LaneRef | null;
    /** The last call a lane really answered for this agent, from the ledger. */
    lastAnswered: { lane: string; model: string; at: string } | null;
  };
  /** The prompt the runtime sends, with the per-run pieces in angle brackets. */
  prompt: { system: string; user: string } | null;
  current: AgentCurrent | null;
  declared: {
    autonomy: 'supervised' | 'semi' | 'autonomous';
    tools: string[];
    skills: string[];
    guardrails: string[];
    systemPrompt: string;
  };
}

export interface Roster {
  agents: LiveAgent[];
  /** Lanes that could answer right now: the real ceiling on agents working at once. */
  lanesOpen: number;
  /** Runs whose worktree nobody removed and whose directory is really there. */
  worktreesOnDisk: number;
  /** What the runtime makes sure of for every agent, whatever its row declares. */
  enforced: string[];
}

// The router is asked about its lanes on the way, which can wait on a local Ollama for a moment.
export const fetchRoster = () => request<Roster>('/agents', { signal: AbortSignal.timeout(10_000) });

/* ── Custom agents: the workspace's and a project's own, and those its repository declares ──────────
   GET /agents/custom?project=… lists them in the order a name resolves — the project's stored ones, its
   files (.neurocode/agents, then .claude/agents), then the workspace's — with the ones that lose their name
   marked `shadowedBy`. Usage is counted from sessions and run steps, never estimated. */

export type AgentSource = 'workspace' | 'project' | '.neurocode/agents' | '.claude/agents' | 'built-in';
export type AgentMode = 'primary' | 'subagent';

export interface CustomAgent {
  /** `custom:<id>`, `file:<name>`, or a roster agent's id — what a session is asked through. */
  key: string;
  /** The stored row's id; null for an agent read from a file. */
  id: string | null;
  name: string;
  role: string;
  prompt: string;
  /** The lane it prefers. The router still answers on another when that one cannot. */
  lane: LaneId | null;
  /** What a file's front matter said as its model, as written. */
  model: string | null;
  /** Empty: every tool NeuroCode has, each still under the tool rules. */
  tools: string[];
  /** Names a file listed that NeuroCode has no tool for (Bash, Task…). */
  ignored: string[];
  maxSteps: number;
  mode: AgentMode;
  source: AgentSource;
  projectId: string | null;
  /** The file it was read from, for a repository's agent. */
  path: string | null;
  editable: boolean;
  /** What reading its file could not take as written. */
  notes: string[];
  /** The key of the nearer agent that has its name, or "built-in" when it is named like a roster agent. */
  shadowedBy: string | null;
  truncated: boolean;
  /** Its instructions' size, at four characters a token. */
  tokens: number;
  createdBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  usage: { sessions: number; lastSession: string | null; steps: number; stepsDone: number };
}

export interface AgentTool { name: string; where: 'session' | 'run'; what: string }

export interface CustomAgentCatalogue {
  agents: CustomAgent[];
  /** The folders read in the project's checkout; empty with no project. */
  folders: { path: string; read: boolean }[];
  /** Whether the project's checkout is on this machine; null with no project. */
  checkout: boolean | null;
  /** Agent files whose front matter could not be read. */
  unreadable: string[];
  tools: AgentTool[];
  lanes: { id: LaneId; label: string; model: string }[];
  limits: { name: number; role: number; prompt: number; maxSteps: number; perScope: number };
}

export interface AgentInput {
  name: string;
  role: string;
  prompt: string;
  lane: LaneId | null;
  tools: string[];
  maxSteps: number;
  mode: AgentMode;
  /** Null: the whole workspace's. Kept as it was on a change. */
  projectId: string | null;
}

/** A dry run: one question, no tools, on the lane the agent prefers. */
export interface DryRun {
  agent: string;
  name: string;
  answer: string;
  lane: LaneId;
  model: string;
  ms: number;
  reasoning: string | null;
  preferred: LaneId | null;
  /** False when the lane it prefers could not answer and another did. */
  onPreferred: boolean;
  tokens: { in: number; out: number };
}

/** A session that is answered by an agent carries its key. */
export type AgentSession = SessionDoc & { agent?: string | null };

const seg = encodeURIComponent;
const POST = (json?: unknown, ms = 8000) => ({ method: 'POST', json, signal: AbortSignal.timeout(ms) });

export const agentsApi = {
  // Reading a project's agent files happens on the server, from its checkout.
  list: (projectId: string | null) =>
    request<CustomAgentCatalogue>(`/agents/custom${projectId ? `?project=${seg(projectId)}` : ''}`,
      { signal: AbortSignal.timeout(10_000) }),
  create: (input: AgentInput) => request<CustomAgent>('/agents/custom', POST(input)),
  update: (id: string, input: AgentInput) =>
    request<CustomAgent>(`/agents/custom/${seg(id)}`, { method: 'PATCH', json: input }),
  remove: (id: string) => request<{ ok: true; id: string }>(`/agents/custom/${seg(id)}`, { method: 'DELETE' }),
  /** A model answers, so it is given a model's time. */
  tryOut: (agent: string, question: string, projectId: string | null) =>
    request<DryRun>('/agents/try', POST({ agent, question, projectId }, 120_000)),
  /** "Ask <agent>": a new session on the project, answered by this agent. */
  ask: (projectId: string, agent: string) => request<AgentSession>('/sessions', POST({ projectId, agent })),
};

/** Where an agent came from, as a badge says it. */
export const SOURCE_LABEL: Record<AgentSource, string> = {
  workspace: 'Workspace', project: 'Project', '.neurocode/agents': '.neurocode/agents',
  '.claude/agents': '.claude/agents', 'built-in': 'Built in',
};
