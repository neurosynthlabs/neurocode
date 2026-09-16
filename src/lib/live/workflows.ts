import { request } from "@/lib/api";

/* The Workflows screen against the local API. A workflow is steps a person wrote; running one writes an
   ordinary plan that the runtime executes, so every number here was counted from real runs. */

export type WorkflowResult = "success" | "failed" | "partial";
export type LiveState =
  | "todo"
  | "active"
  | "waiting"
  | "done"
  | "failed"
  | "skipped";

export interface LiveWorkflowPhase {
  id: string;
  title: string;
  detail: string;
  mode: "parallel" | "pipeline" | "single";
  /** null for the built-in's Write phase: how many agents is the compiler's call, run by run. */
  agents: number | null;
  state: "todo";
}

export interface LiveWorkflow {
  id: string;
  name: string;
  description: string;
  trigger: string;
  scope: "global" | "project";
  projectId: string | null;
  projectName: string | null;
  builtin: boolean;
  runs: number;
  /** Whether any plan came from it: removing it then archives rather than deletes. */
  referenced: boolean;
  /** null until a run of it exists. */
  avgAgents: number | null;
  avgMinutes: number | null;
  lastRun: string | null;
  lastResult: WorkflowResult | null;
  phases: LiveWorkflowPhase[];
  /** The test command found in the project the phases were drawn for, if any. */
  tests: { project: string | null; command: string | null };
}

export interface WorkflowStepDoc {
  n: number;
  label: string;
  agent: string;
  detail: string;
}

export interface LiveWorkflowDetail extends LiveWorkflow {
  requirementTemplate: string;
  steps: WorkflowStepDoc[];
  archived: boolean;
  /** The definition the runtime executes, in words. */
  definitionText: string;
}

export interface WorkflowLiveRow {
  label: string;
  runRef: string;
  /** write | merge | test | review | handoff */
  phase: string;
  state: LiveState;
  ms: number | null;
  /** null: no model call is on the ledger for it, so nothing was measured. */
  tokens: number | null;
}

export interface WorkflowLiveRun {
  ref: string;
  workflow: string;
  workflowId: string;
  status: "queued" | "running" | "waiting";
  startedAt: string;
  phase: string;
  waitingOn: string | null;
  completed: number;
  running: number;
  queued: number;
  /** Everything that was skipped or collided, so nothing is dropped silently. */
  skipped: string[];
  agents: WorkflowLiveRow[];
}

export interface WorkflowHistoryRow {
  id: string;
  runRef: string;
  workflowId: string;
  workflow: string;
  trigger: string;
  agents: number;
  durationS: number | null;
  tokens: number | null;
  cost: number | null;
  result: WorkflowResult;
  status: "done" | "failed" | "cancelled";
  at: string;
}

export interface WorkflowOverview {
  stats: {
    workflows: number;
    runsTotal: number;
    liveNow: number;
    agentsInFlight: number;
    lanesOpen: number;
    tokensToday: number;
    costToday: number;
  };
  live: WorkflowLiveRun | null;
  history: WorkflowHistoryRow[];
  /** Roster names a step may be given to. */
  writers: string[];
}

export interface WorkflowInput {
  name: string;
  description: string;
  projectId: string | null;
  requirementTemplate: string;
  steps: { label: string; agent: string; detail: string }[];
}

export interface WorkflowStarted {
  planRef: string;
  taskRef: string;
  runRef: string | null;
  agents: number;
  openQuestions: number;
  /** Why no run started, when none did. */
  note: string;
}

const seg = encodeURIComponent;
const query = (projectId: string | null) =>
  projectId ? `?project=${seg(projectId)}` : "";

export const workflowsApi = {
  list: (projectId: string | null) =>
    request<LiveWorkflow[]>(`/workflows${query(projectId)}`),
  detail: (id: string, projectId: string | null) =>
    request<LiveWorkflowDetail>(`/workflows/${seg(id)}${query(projectId)}`),
  overview: () =>
    request<WorkflowOverview>("/workflows/overview", {
      signal: AbortSignal.timeout(10_000),
    }),
  create: (body: WorkflowInput) =>
    request<LiveWorkflowDetail>("/workflows", { method: "POST", json: body }),
  update: (id: string, body: WorkflowInput) =>
    request<LiveWorkflowDetail>(`/workflows/${seg(id)}`, {
      method: "PATCH",
      json: body,
    }),
  remove: (id: string) =>
    request<{ ok: boolean; archived: boolean }>(`/workflows/${seg(id)}`, {
      method: "DELETE",
    }),
  // Dispatching reads git and writes worktree metadata before it answers; a compile may wait on a model.
  run: (id: string, projectId: string, input: string) =>
    request<WorkflowStarted>(`/workflows/${seg(id)}/run`, {
      method: "POST",
      json: { projectId, input },
      signal: AbortSignal.timeout(180_000),
    }),
};
