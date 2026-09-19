import { request, type UsageReport } from '@/lib/api';

/* The Cost & Usage screen's live data: GET /usage. The AI gateway's ledger, with every dollar worked out
   by the database at each lane's declared price. A lane that declares no price leaves its calls
   unpriced, so a sum over them is a floor (`costComplete` false) — and null when nothing in it was
   priced at all, which is not the same as free. */

export interface Priced {
  /** Null when every call in the line ran on a lane with no price. */
  costUsd: number | null;
  /** False when any call in the line ran on a lane with no price: costUsd is then a floor. */
  costComplete: boolean;
}

export interface SpendDay extends Priced { day: string; calls: number; model: number; offline: number; tokens: number }

export interface AgentSpend extends Priced {
  /** The roster id, or the name the ledger wrote when the roster no longer has that agent. */
  agent: string;
  name: string;
  calls: number;
  tokensIn: number;
  tokensOut: number;
}

export interface ProjectSpend extends Priced {
  /** Null: calls that belonged to the workspace rather than to one project. */
  projectId: string | null;
  projectName: string | null;
  calls: number;
  tokensIn: number;
  tokensOut: number;
}

export interface CostlyCall {
  at: string;
  lane: string;
  model: string;
  feature: string;
  /** Roster id, or the ledger's own spelling; null when a person made the call. */
  agent: string | null;
  tokensIn: number;
  tokensOut: number;
  /** Null when the lane declares no price. */
  costUsd: number | null;
  runRef: string | null;
  taskRef: string | null;
}

export interface SpendReport extends Omit<UsageReport, 'totals' | 'byDay'> {
  totals: UsageReport['totals'] & Priced;
  byDay: SpendDay[];
  byAgent: AgentSpend[];
  byProject: ProjectSpend[];
  /** The window's ten costliest calls; unpriced calls come after every priced one. */
  costliest: CostlyCall[];
}

export const fetchUsage = (days: number) => request<SpendReport>(`/usage?days=${days}`);

/** What each feature the ledger records is called on screen. An id not here is shown as it is. */
export const FEATURE_LABEL: Record<string, string> = {
  compile: 'Requirement compiler', agent: 'Agent step', review: 'Code review', chat: 'Session', ask: 'Ask memory',
  brainstorm: 'Brainstorm', extract: 'Add from text', embed: 'Embedding', test: 'Connection test',
  research: 'Research', eval: 'Eval', 'eval-judge': 'Eval judge',
};
