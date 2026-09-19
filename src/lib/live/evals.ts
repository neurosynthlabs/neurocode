import { request } from "@/lib/api";

/* The Evals screen against the local API. Every score here is a run that happened: a case's answer came
   from a real call to the compiler, ask memory, retrieval or a lane, and its verdict from the checks the
   case states, a judge that is a labelled model call of its own, or a person who overrode it. */

export type EvalKind = "regression" | "capability" | "safety" | "cost";
export type EvalTarget = "compile" | "ask" | "retrieval" | "review" | "prompt";
export type EvalRunStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled";
export type EvalVerdict = "pass" | "fail" | "partial" | "error";
export type CheckKind =
  | "exact"
  | "contains"
  | "not_contains"
  | "regex"
  | "json_equals"
  | "json_contains"
  | "cites"
  | "retrieves"
  | "max_ms"
  | "free_lane"
  | "not_offline"
  | "judge";

/** The kinds a suite is filed under, in the order the screen offers them. */
export const EVAL_KINDS: { id: EvalKind; label: string }[] = [
  { id: "capability", label: "Capability" },
  { id: "regression", label: "Regression" },
  { id: "safety", label: "Safety" },
  { id: "cost", label: "Cost" },
];

/** What a suite can point at: each is a call this app really makes. */
export const EVAL_TARGETS: { value: EvalTarget; label: string }[] = [
  { value: "compile", label: "Requirement compiler" },
  { value: "ask", label: "Ask memory" },
  { value: "retrieval", label: "Retrieval" },
  { value: "review", label: "Reviewer prompt" },
  { value: "prompt", label: "Lane prompt" },
];

/** Every check the API scores an answer with, as a person reads it. */
export const CHECK_LABEL: Record<CheckKind, string> = {
  exact: "Exactly equals",
  contains: "Contains",
  not_contains: "Never contains",
  regex: "Matches a pattern",
  json_equals: "JSON path equals",
  json_contains: "JSON path has",
  cites: "Cites a fact",
  retrieves: "Retrieves a ref",
  max_ms: "Answers within",
  free_lane: "On a free lane",
  not_offline: "A model answered",
  judge: "LLM judge",
};

export interface EvalCheck {
  kind: CheckKind;
  value?: string | number | boolean;
  path?: string;
  ref?: string;
  k?: number;
  rubric?: string;
}

export interface LiveEvalSuite {
  id: string;
  name: string;
  target: string;
  targetKind: EvalTarget;
  kind: EvalKind;
  cases: number;
  passed: number;
  /** null until a run of the suite has finished. */
  score: number | null;
  /** null until there are two finished runs to compare. */
  delta: number | null;
  lastRun: string | null;
  lastRunRef: string | null;
  status: "pass" | "warn" | "fail" | "running" | "never";
  runningRef: string | null;
  threshold: number;
  lane: string | null;
  projectId: string | null;
  allowOffline: boolean;
}

export interface EvalLesson {
  ref: string;
  at: string | null;
  from: string;
  runRef: string;
  text: string;
  category: string;
}
export interface EvalLane {
  id: string;
  label: string;
  ready: boolean;
  blocked: string | null;
}

export interface EvalOverview {
  suites: LiveEvalSuite[];
  /** Finished scores per suite, oldest first, at most seven. */
  trend: Record<string, number[]>;
  lessons: EvalLesson[];
  lanes: EvalLane[];
}

export interface EvalRunDoc {
  ref: string;
  status: EvalRunStatus;
  lane: string | null;
  score: number | null;
  passed: number;
  failed: number;
  partial: number;
  errored: number;
  note: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface EvalCaseRow {
  id: string;
  suite: string;
  n: number;
  name: string;
  input: string;
  checks: EvalCheck[];
  weight: number;
  expected: string;
  sourcePlanId: string | null;
  judge: string;
  scoreDelta: number;
  /** null when the run on screen never reached this case. */
  resultId: number | null;
  status: EvalVerdict | null;
  machineStatus: EvalVerdict | null;
  got: string;
  output: string;
  score: number | null;
  lane: string;
  model: string;
  ms: number | null;
  offline: boolean;
  error: string;
  checkResults: { kind: CheckKind; ok: boolean | null; observed: string }[];
  judgeReason: string;
  overridden: boolean;
  overrideNote: string;
}

export interface EvalSuiteDetail extends LiveEvalSuite {
  description: string;
  systemPrompt: string;
  trend: number[];
  run: EvalRunDoc | null;
  comparedWith: string | null;
  runs: EvalRunDoc[];
  caseRows: EvalCaseRow[];
}

export interface SuiteInput {
  name: string;
  kind: EvalKind;
  targetKind: EvalTarget;
  projectId: string | null;
  lane: string | null;
  systemPrompt: string;
  threshold: number;
  allowOffline: boolean;
  description: string;
}

export interface CaseInput {
  name: string;
  input: string;
  checks: EvalCheck[];
  weight: number;
  sourcePlanId?: string | null;
}

const seg = encodeURIComponent;
const POST = (json?: unknown) => ({ method: "POST", json });

export const evalsApi = {
  overview: () =>
    request<EvalOverview>("/evals", { signal: AbortSignal.timeout(15_000) }),
  suite: (id: string, run?: string | null) =>
    request<EvalSuiteDetail>(
      `/evals/${seg(id)}${run ? `?run=${seg(run)}` : ""}`,
      { signal: AbortSignal.timeout(15_000) },
    ),
  createSuite: (body: SuiteInput) =>
    request<EvalSuiteDetail>("/evals/suites", POST(body)),
  deleteSuite: (id: string) =>
    request<{ ok: boolean }>(`/evals/suites/${seg(id)}`, { method: "DELETE" }),
  addCase: (suiteId: string, body: CaseInput) =>
    request<EvalCaseRow>(`/evals/suites/${seg(suiteId)}/cases`, POST(body)),
  caseFromPlan: (suiteId: string, planRef: string) =>
    request<CaseInput>(
      `/evals/suites/${seg(suiteId)}/cases/from-plan`,
      POST({ planRef }),
    ),
  deleteCase: (id: string) =>
    request<{ ok: boolean }>(`/evals/cases/${seg(id)}`, { method: "DELETE" }),
  run: (suiteId: string, lane: string | null) =>
    request<EvalRunDoc>(`/evals/suites/${seg(suiteId)}/run`, POST({ lane })),
  runAll: (kind: EvalKind | null) =>
    request<{ runRefs: string[] }>("/evals/run-all", POST({ kind })),
  cancel: (ref: string) =>
    request<EvalRunDoc>(`/evals/runs/${seg(ref)}/cancel`, POST()),
  override: (
    resultId: number,
    status: "pass" | "fail" | "partial" | null,
    note: string,
  ) =>
    request<EvalCaseRow>(
      `/evals/results/${resultId}/override`,
      POST({ status, note }),
    ),
  lesson: (resultId: number, text: string) =>
    request<{ ref: string }>(
      `/evals/results/${resultId}/lesson`,
      POST({ text }),
    ),
};
