import { request, type AiLane, type AiPreference, type CompilerInfo, type LaneId, type ThinkingLevel } from '@/lib/api';

/* The Models screen's live data: GET /models. The gateway's own view of its lanes, what each
   feature asks it for, and a day of its ledger. Key material is removed on the server. */

export type LaneRole = 'write' | 'review' | 'plan' | 'chat';

export interface FleetLane extends Omit<AiLane, 'keyMask' | 'keySource' | 'baseUrl' | 'signup'> {
  hosting: 'local' | 'remote';
  /** The embedding model the lane serves, if it serves one. */
  embed: string | null;
  /** Whether the cost is known: a free lane's is zero; a paid lane that declares no price has none. */
  priced: boolean;
  usdPerMIn: number;
  usdPerMOut: number;
  calls24h: number;
  failures24h: number;
  avgMs24h: number;
  tokensIn24h: number;
  tokensOut24h: number;
  /** Of the input tokens, how many the provider served from its prompt cache (as it reported them). */
  tokensCached24h: number;
  /** Of the output tokens, how many were reasoning rather than the answer. */
  tokensReasoning24h: number;
  /** null when the lane is not priced — unknown, which is not the same as free. */
  cost24h: number | null;
  /** What the prompt cache took off the bill; null when the cost is unknown. */
  saved24h: number | null;
}

export interface RouteLine {
  feature: 'compile' | 'agent' | 'review' | 'chat' | 'compact' | 'ask' | 'brainstorm' | 'extract' | 'embed' | 'test';
  role: LaneRole | null;
  /** Whether the feature has an answer of its own when no lane does. */
  offline: boolean;
  /** A review moves the lane that wrote the code to the end of its chain. */
  avoidsWriter: boolean;
  how: string;
  /** The lanes it would try now, in order. */
  chain: { lane: LaneId; model: string }[];
  calls24h: number;
  failures24h: number;
  offline24h: number;
  /** How hard it asks a model to think; null where there is nothing to set (embeddings, the admin test). */
  thinking: ThinkingLevel | null;
  thinkingDefault: ThinkingLevel | null;
}

export interface ModelsReport {
  preference: AiPreference;
  preferenceLocked: boolean;
  active: CompilerInfo;
  /** How the gateway orders lanes for a call, in words taken from its code. */
  ordering: string;
  /** What each of auto, free, local and rules lets the router use. */
  preferences: Record<'auto' | 'free' | 'local' | 'rules', string>;
  lanes: FleetLane[];
  totals24h: {
    calls: number; local: number; remote: number; offline: number; failures: number;
    tokensIn: number; tokensOut: number; costUsd: number;
    /** False when a lane with no declared price answered: costUsd is then a floor. */
    costComplete: boolean;
  };
  routes: RouteLine[];
}

export const fetchModels = () => request<ModelsReport>('/models', { signal: AbortSignal.timeout(10_000) });
