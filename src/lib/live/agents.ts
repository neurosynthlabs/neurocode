import { request, type LaneId, type RunStep } from '@/lib/api';

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
