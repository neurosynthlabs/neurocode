import { request, type RunDoc, type RunLog } from "@/lib/api";

/* The Testing screen's live shapes, read by GET /testing. Every number is what a project's own test
   command printed the last time it really ran; a count is null when the runner's output was in no shape
   the parser knows, and the screen then says "exit code only" rather than drawing a rate. */

export interface TestCounts {
  passed: number | null;
  failed: number | null;
  skipped: number | null;
  total: number | null;
}

/** Which parser read a run's output; '' when none did. */
export type TestRunner = "pytest" | "jest" | "vitest" | "go" | "dotnet" | "";

/** One onboarded project's test command — the runtime runs exactly one per project. */
export interface TestSuiteLive {
  projectId: string;
  projectName: string;
  /** The command detected in the checkout now, or null when there is none. */
  command: string | null;
  tool: "pytest" | "make" | "npm" | "go" | "dotnet" | "unknown";
  /** The answer to the first-run approval in this project, or null when nobody has been asked. */
  allowed: "allowed" | "refused" | null;
  /** A test-only run still in flight here. */
  checking: string | null;
  latest:
    | (TestCounts & {
        runRef: string;
        status: "passed" | "failed";
        ms: number | null;
        sha: string;
        branch: string;
        runner: TestRunner;
        /** Every failure is expected by the same rule the signature gate uses. */
        allExpected: boolean;
        /** Failed, but not every failure was named — a crash, a build error, unreadable output. */
        unnamed: boolean;
        at: string;
      })
    | null;
}

export interface TestExpectationLive {
  projectId: string;
  testName: string;
  kind: "legacy" | "quarantine";
  reason: string;
  by: string | null;
  at: string;
}

export interface TestFailureLive {
  id: number;
  runRef: string;
  projectId: string;
  projectName: string;
  name: string;
  file: string;
  line: number | null;
  message: string;
  excerpt: string;
  at: string;
  expectation: TestExpectationLive | null;
  /** Measured over the project's last `ofLast` tested runs. */
  failedIn: number;
  ofLast: number;
  firstFailedRef: string;
}

export interface TestFailureDetail extends TestFailureLive {
  log: RunLog[];
}

export interface TestHistoryLine extends TestCounts {
  runRef: string;
  projectId: string;
  projectName: string;
  role: RunDoc["role"];
  trigger: string;
  branch: string;
  ms: number | null;
  sha: string;
  status: "passed" | "failed";
  runner: TestRunner;
  at: string;
}

export interface CoverageLine {
  runRef: string;
  projectId: string;
  /** A top-level directory of the project; '' for files at its root. */
  path: string;
  covered: number;
  total: number;
  source: "cobertura" | "lcov" | "istanbul" | "go";
}

export interface TestingReport {
  suites: TestSuiteLive[];
  failures: TestFailureLive[];
  history: TestHistoryLine[];
  coverage: CoverageLine[];
  expectations: TestExpectationLive[];
  /** How many recent tested runs "failed in N of the last M" looks back over. */
  window: number;
}

/** A run's test step as GET /runs carries it: the command and status, and the parsed totals beside them. */
export type RunTests = RunDoc["tests"] & TestCounts & { sha: string; runner: TestRunner };

const seg = encodeURIComponent;

export const testing = {
  report: () =>
    request<TestingReport>("/testing", { signal: AbortSignal.timeout(15_000) }),
  failure: (id: number) =>
    request<TestFailureDetail>(`/testing/failures/${id}`),
  run: (projectId: string) =>
    request<RunDoc>(`/projects/${seg(projectId)}/tests`, {
      method: "POST",
      signal: AbortSignal.timeout(30_000),
    }),
  rerun: (id: number) =>
    request<RunDoc>(`/testing/failures/${id}/rerun`, {
      method: "POST",
      signal: AbortSignal.timeout(30_000),
    }),
  expect: (
    projectId: string,
    body: {
      testName: string;
      kind: TestExpectationLive["kind"];
      reason: string;
    },
  ) =>
    request<TestExpectationLive>(
      `/projects/${seg(projectId)}/tests/expectations`,
      { method: "PUT", json: body },
    ),
  unexpect: (projectId: string, testName: string) =>
    request<{ ok: boolean }>(`/projects/${seg(projectId)}/tests/expectations?${new URLSearchParams({ testName })}`, { method: 'DELETE' }),
};
