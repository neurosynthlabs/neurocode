import { request } from '@/lib/api';
import type { Container, Deployment } from '@/types';
import type { HealthCheck, LogLine, PipelineStage } from '@/mock/devops';

/* The DevOps screen's live data: GET /ops/*. It shows this machine, because NeuroCode deploys nothing
   anywhere else — the API process, Postgres, the web dev server, Ollama; checks probed when asked; the
   runs the agents delivered; the CI workflow as git sees it; and logs the database already holds. */

export type ServiceStatus = 'ok' | 'warn' | 'down';
export interface Fact { k: string; v: string }

export interface LocalService {
  id: 'api' | 'postgres' | 'web' | 'ollama';
  name: string;
  status: ServiceStatus;
  url: string;
  version: string;
  startedAt: string | null;
  uptimeS: number | null;
  /** Measured by `ps` for this process only; null for every other service. */
  cpuPct: number | null;
  rssBytes: number | null;
  facts: Fact[];
}

export interface OpsGateItem {
  /** `handoff`: stopped at your signature. `ready`: accepted, not merged yet. */
  kind: 'handoff' | 'ready';
  runRef: string;
  approvalRef: string | null;
  project: string;
  projectId: string;
  branch: string;
  diff: { files: number; insertions: number; deletions: number; commits: number };
  tests: string;
  /** null when nothing was measured that would raise one — the pill is then not shown. */
  risk: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | null;
  ships: string[];
  dangers: string[];
  undo: string;
}

export type OpsCheck = Omit<HealthCheck, 'latencyMs'> & { latencyMs: number | null };

export interface OpsOverview {
  services: LocalService[];
  gate: OpsGateItem[];
  checks: OpsCheck[];
  runtime: Fact[];
  at: string;
}

export type OpsDelivery = Omit<Deployment, 'status'> & {
  status: Exclude<Deployment['status'], 'rolled_back'> | 'ready' | 'cancelled' | 'discarded';
  note: string;
  projectId: string;
  role: 'solo' | 'integration';
};
export interface OpsDeliveries {
  items: OpsDelivery[];
  stats: { mergedToday: number; merged: number; failed: number; discarded: number; blockedOnYou: number };
}

export interface OpsPipeline {
  run: { ref: string; status: string; trigger: string; by: string; runner: string; branch: string; startedAt: string; elapsedS: number } | null;
  stages: (Omit<PipelineStage, 'state'> & { state: PipelineStage['state'] | 'waiting'; kind: string })[];
  workflow: { path: string; present: boolean; steps: string[]; tracked: boolean; upstream: string | null; pushed: boolean | null; note: string };
}

/** `next` is an opaque cursor — the last line's exact time and id — to pass back as `before`. */
export interface OpsLogs { lines: LogLine[]; next: string | null }
export interface OpsContainers { available: boolean; reason: string; containers: Container[] }

export interface OpsSecret {
  id: string;
  /** The key's name in server/secrets.json, or the variable that stands in for it. Never a value. */
  name: string;
  env: string;
  store: string;
  set: boolean;
  rejected: boolean;
  lastSetAt: string | null;
  replaceableBy: string[];
  usedBy: string;
  note: string;
}

export type LogLevel = LogLine['level'];

export const fetchOverview = () => request<OpsOverview>('/ops/overview', { signal: AbortSignal.timeout(20_000) });
export const fetchDeliveries = () => request<OpsDeliveries>('/ops/deliveries?limit=50');
export const fetchPipeline = (run?: string) => request<OpsPipeline>(`/ops/pipeline${run ? `?run=${encodeURIComponent(run)}` : ''}`);
export const fetchLogs = (level: LogLevel | null, before: string | null) =>
  request<OpsLogs>(`/ops/logs?${new URLSearchParams({ limit: '200', ...(level ? { level } : {}), ...(before ? { before } : {}) })}`);
export const fetchContainers = () => request<OpsContainers>('/ops/containers', { signal: AbortSignal.timeout(20_000) });
export const fetchSecrets = () => request<OpsSecret[]>('/ops/secrets', { signal: AbortSignal.timeout(10_000) });
