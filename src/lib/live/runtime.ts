import { request, type RunDoc, type RunStep } from '@/lib/api';
import type { ApprovalRequest, Plan } from '@/types';

/* The runtime's newer calls, and the parts of a run they add: pushing an accepted run's branch
   (POST /runs/{ref}/push), opening its pull or merge request and reading that back (POST and GET
   /runs/{ref}/pr), reading its review again for a fresh receipt (POST /runs/{ref}/review),
   dispatching a plan "until done" (POST /plans/{ref}/dispatch {goalBudget}), and the project's own
   checks and a goal run's completion check as the run document carries them. */

/** One of the project's own checks — its lint or typecheck — as it ran in the run's worktree. */
export interface RunCheck {
  /** The run step it ran as. */
  step: number;
  name: string;
  command: string;
  status: 'not run' | 'passed' | 'failed' | 'skipped';
  /** Null until it ran. */
  exit: number | null;
  summary: string;
  /** Its last lines, as printed. */
  output: string[];
  /** What its output named, read into file:line:col — the first ones, in the run's worktree. */
  problems?: CheckProblem[];
  /** Every problem it named, by severity, and in all (more than `problems` may hold). */
  problemCounts?: Partial<Record<CheckProblem['severity'], number>>;
  problemTotal?: number;
}

export interface CheckProblem {
  file: string;
  line: number;
  col: number;
  severity: 'error' | 'warning' | 'info';
  code: string | null;
  message: string;
  tool: string;
}

export interface GoalCriterion {
  criterion: string;
  met: boolean;
  /** What the judge cited; empty when it cited nothing. */
  evidence: string;
  /** The file in the diff it cited; empty when none. */
  file: string;
  /** Why a "met" was not accepted, or why it was not judged. */
  why: string;
}

/** A goal run's completion check: the project's own word, and a judge on a lane that did not write the code. */
export interface RunGoal {
  step: number;
  verdict: 'not run' | 'met' | 'not met' | 'unjudged';
  why: string;
  tests?: string;
  checks?: { name: string; status: RunCheck['status']; summary: string }[];
  criteria: GoalCriterion[];
  /** The model that judged; empty when none could. */
  by: string;
  at: string | null;
  /** 'rework': it tried again on its own. 'sign': it went to your signature. */
  next?: 'rework' | 'sign';
}

/** The fingerprint of the exact patch the reviewer read. Merge and push refuse a branch whose patch is another. */
export interface ReviewReceipt { sha256: string; head: string; base: string; bytes: number; truncated: boolean; at: string; by: string }

/** What one agent step was handed: the project's instruction files for its targets, retrieval's pieces for its
    words, and the files it read — each with why it was chosen. */
export interface StepGrounding {
  instructions: { path: string; bytes: number; sha1: string; scope: 'project' | 'rules'; matched: string | null }[];
  /** True when the instructions were cut to fit. */
  capped: boolean;
  /** `project` is set when retrieval also searched the projects this one references: the piece's own project. */
  pieces: { kind: 'code' | 'doc'; ref: string; path: string; line: number | null; how: string; project?: string }[];
  files: { path: string; via: 'plan' | 'retrieval' | 'index'; readonly?: boolean }[];
  /** Refs of the taste rules a person adopted that the step was handed (Memory → Taste). */
  taste?: string[];
}

/** A step with what the governed runtime keeps on it. */
export type RuntimeStep = RunStep & {
  /** The commit the step left in the worktree; absent when it wrote nothing. */
  commitSha?: string;
  /** How many times this step has been tried, and how many times it may be. Both absent when there was
      only ever going to be one try — "attempt 1 of 1" on every step is noise, not information. */
  attempts?: number;
  maxAttempts?: number;
  /** The question the agent asked instead of writing, and the answer it got (null until answered). */
  question?: string;
  answer?: string | null;
  grounding?: StepGrounding;
  /** Set while the step stands taken back by a revert. */
  takenBack?: { to: number; by: string; at: string };
};

/** What a person allowed beyond the tool rules: once at one step, or for the rest of the run. */
export interface RunGrant { tool: 'edit' | 'command'; subject: string; scope: 'once' | 'run'; step: number | null; by: string; at: string; used?: boolean | null }
export interface RunRevert { to: number; by: string; at: string; steps: number[]; redo: boolean }
/** A time this run was carried on after being interrupted: where from, and what was kept. */
export interface RunResume {
  from: number;
  by: string;
  at: string;
  /** The step whose commit was adopted rather than written again; null when none was. */
  adopted: number | null;
  /** Source label → the commit the half-written files were kept as, on a ref of the run's own. */
  kept: Record<string, string>;
}

/** Where one of a run's worktrees stands, against where its own record says it should. */
export interface ResumePart {
  label: string;
  head: string;
  checkpoint: string;
  dirty: boolean;
  /** A commit this run made that resume would take as the interrupted step's. */
  adopts: string | null;
  /** A commit this run did not make, which is why it is refused. */
  foreign: string | null;
}

/** What carrying an interrupted run on would do. Read from git and the run's own record; nothing here
    is a guess, and `canResume: false` carries the reason, which is the whole answer. */
export interface ResumePlan {
  ref: string;
  canResume: boolean;
  /** Empty when it can be carried on; otherwise a sentence saying why not, in words you can act on. */
  reason: string;
  /** The step it would start from; null when it cannot. */
  from: number | null;
  lastGood: number;
  worktree: 'present' | 'missing' | 'gone';
  parts: ResumePart[];
  grants: { run: number; once: number };
  steps: { done: number; left: number };
  /** One sentence for the person: what runs again, what stands, and what was kept. */
  said: string;
}

/* ── the forge ─────────────────────────────────────────────────── */

/** The pull or merge request this run's branch has on its forge, as it was last read from there. */
export interface PullRequest {
  host: string;
  /** The project's path on the forge: `acme/shop`. */
  path: string;
  /** What that forge calls it, in its own words: "pull request" on GitHub, "merge request" on GitLab. */
  noun: string;
  number: number;
  url: string;
  /** "unknown" only when the forge answered with a word this product does not translate. */
  state: 'open' | 'merged' | 'closed' | 'unknown';
  draft: boolean;
  /** Why it went up as a draft, or why it went up ready — the same sentence the request itself carries. */
  draftBecause: string;
  /** What opened it: the person's own `gh` or `glab`, or a token they stored. Never a credential of ours. */
  via: 'gh' | 'glab' | 'token';
  /** The branch it goes into. */
  base: string;
  title: string;
  by: string;
  at: string;
  /** When the state above was last read from the forge. */
  checkedAt: string;
  /** Set when the last read could not reach the forge: what is shown is what was true then. */
  checkFailed?: string;
}

/** A run's branch as it stands on the project's own remote. */
export interface PushedBranch {
  remote: string;
  branch: string;
  sha: string;
  /** The branch a request would go into, measured when it was pushed. */
  baseBranch?: string;
  compareUrl: string | null;
  at: string;
  by: string;
  pullRequest?: PullRequest | null;
  /** One entry per source, for a project that has several; each with a request of its own. */
  sources?: { label: string; remote: string; branch: string; sha: string; compareUrl: string | null; pullRequest?: PullRequest | null }[];
}

/** How this machine can reach one forge right now. `reach: null` means it cannot, and `why` says so. */
export interface ForgeReach {
  host: string;
  path: string;
  noun: string;
  reach: 'gh' | 'glab' | 'token' | null;
  why: string;
}

/** GET /runs/{ref}/pr: the request as the forge has it now, kept on the run as it is read. */
export interface PullRequestView {
  ref: string;
  pullRequest: PullRequest | null;
  compareUrl: string | null;
  forge: ForgeReach | null;
  /** Empty when there is nothing to say; otherwise why there is no request, or why none could be read. */
  why: string;
}

/** A run as the runtime now serves it. */
export type RuntimeRun = Omit<RunDoc, 'steps'> & {
  steps: RuntimeStep[];
  /** Which try this is; each automatic or manual send-back adds one. */
  attempt: number;
  /** "Run until done": attempts allowed in all; null for an ordinary run. */
  goalBudget: number | null;
  checks: RunCheck[];
  goal: RunGoal | null;
  review: RunDoc['review'] & {
    receipt?: ReviewReceipt; instructions?: { path: string; bytes: number }[]; taste?: string[];
    /** The repository's REVIEW.md the reviewer was handed, read from the checkout the run branched from. */
    brief?: { path: string; bytes: number };
    /** Each finding may name the line in the new file it is about. */
    findings: { severity: string; file: string; line?: number; note: string }[];
  };
  grants: RunGrant[];
  reverts: RunRevert[];
  resumes: RunResume[];
  /** Labels of the project's reference sources: read for grounding, never written. */
  references: string[];
  /** The branch on the remote, and the request opened for it. Null while it lives only on this machine. */
  pushed: PushedBranch | null;
};

/** Every run document the API sends carries these fields; older ones read as a first attempt with no checks. */
export function asRuntime(run: RunDoc): RuntimeRun {
  const r = run as Partial<RuntimeRun> & RunDoc;
  return {
    ...r, attempt: r.attempt ?? 1, goalBudget: r.goalBudget ?? null, checks: r.checks ?? [], goal: r.goal ?? null,
    grants: r.grants ?? [], reverts: r.reverts ?? [], resumes: r.resumes ?? [], references: r.references ?? [],
  };
}

/* ── the gates ─────────────────────────────────────────────────── */

/** What a gate is: the first run of a project's command, a tool rule asking about a command or files, an agent's
    question, a pause before a step, or the signature. The server reads it from the tool the gate names. */
export type GateKind = 'tests' | 'command' | 'edit' | 'question' | 'step' | 'signature' | 'other';
/** "Allow once", "Allow for this run", "Always allow in this project" (needs rules:manage). */
export type GateScope = 'once' | 'run' | 'project';
export type GateApproval = ApprovalRequest & { kind?: GateKind; options?: string[] };

/** The kind of a gate; one the server sent without a kind is approved or denied. */
export const gateKind = (a: ApprovalRequest): GateKind => (a as GateApproval).kind ?? 'other';

/** `sha256:ab12…` → `ab12cd34ef56`, the way the run screen shows a receipt. */
export const shortReceipt = (fp: string | undefined) => (fp ?? '').replace(/^sha256:/, '').slice(0, 12);

const seg = encodeURIComponent;
const POST = (json?: unknown) => ({ method: 'POST', json });
// A push waits on the remote, and git gives it two minutes before it gives up.
const remote = () => ({ signal: AbortSignal.timeout(150_000) });

export const runtimeApi = {
  /** Push an accepted run's own branch; the answer is the run, with `pushed.compareUrl` when the remote has one. */
  push: (ref: string, remoteName?: string) =>
    request<RunDoc>(`/runs/${seg(ref)}/push`, { ...POST(remoteName ? { remote: remoteName } : {}), ...remote() }),
  /** Open the pull or merge request for a pushed branch, with the person's own `gh`, `glab` or token.
      The answer is the run, with what came back under `pushed.pullRequest`. */
  openPullRequest: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/pr`, { ...POST(), ...remote() }),
  /** Read that request's state back from the forge. Safe to ask of any run: a run with none says so.
      What this machine can open one *with* is GET /runs/forges, read by `nc forges`. */
  pullRequest: (ref: string) => request<PullRequestView>(`/runs/${seg(ref)}/pr`, remote()),
  /** Keep the workspace's token for a forge (github.com, gitlab.com): used to clone a private repository,
      push, and open requests. Needs workspace:admin; the token is never sent back. */
  setForgeToken: (host: string, token: string) =>
    request<unknown>(`/runs/forges/${seg(host)}/token`, { method: 'PUT', json: { token } }),
  /** Read the run's diff again as the branch is now; the review step runs in the background. */
  reviewAgain: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/review`, POST()),
  /** Dispatch a plan; with `goalBudget` (1–5) it runs until its completion check passes or the attempts run out. */
  dispatch: (ref: string, opts: { goalBudget?: number } = {}) =>
    request<Plan & { runRef?: string }>(`/plans/${seg(ref)}/dispatch`, POST(opts.goalBudget ? { goalBudget: opts.goalBudget } : undefined)),
  /** Answer a gate beyond yes or no: a tool rule's ask with a scope, an agent's question with words. */
  decide: (ref: string, decision: 'approve' | 'deny', opts: { scope?: GateScope; answer?: string } = {}) =>
    request<GateApproval>(`/approvals/${seg(ref)}/${decision}`, POST(opts.scope || opts.answer !== undefined ? opts : undefined)),
  /** Take the run's worktree back to how it stood after step `n`; with `redo` the later steps run again. */
  revert: (ref: string, n: number, redo = false) => request<RunDoc>(`/runs/${seg(ref)}/steps/${n}/revert`, POST({ redo })),
  /** What carrying an interrupted run on would do. Reads git and writes nothing, so it is safe to ask
      on every run the screen opens. */
  resumePlan: (ref: string) => request<ResumePlan>(`/runs/${seg(ref)}/resume`),
  /** Carry it on from its last finished step. The steps already done stand, with the commits they made. */
  resume: (ref: string) => request<RunDoc>(`/runs/${seg(ref)}/resume`, POST()),
};

/* ── a patch, read into its files ─────────────────────────────────
   The run's diff arrives as one patch, the way git printed it, because that is what was reviewed and
   what a merge would take. Splitting it here rather than on the server keeps that true — nothing is
   re-measured, only divided at the lines git itself writes to divide it. */

/** One file inside a patch: what git's header says happened to it, and the lines that say how. */
export interface PatchFile {
  /** The file as the patch names it: the b/ side, or the a/ side for a file that was deleted. */
  path: string;
  /** Added, modified, deleted, renamed — read from git's own header lines, never guessed from the counts. */
  change: 'A' | 'M' | 'D' | 'R';
  additions: number;
  deletions: number;
  /** git says "Binary files … differ" and prints no lines; there is nothing to colour. */
  binary: boolean;
  /** Where it came from, when git recorded a rename. */
  from: string | null;
  /** The section from its first hunk on, exactly as git printed it. Empty when only a mode changed. */
  body: string;
}

/** `"a/src/app.ts"` → `src/app.ts`, quoted or not, whichever side it is. */
function patchPath(line: string): string {
  const text = line.trim();
  const bare = text.startsWith('"') ? text.slice(1, text.endsWith('"') ? -1 : undefined) : text;
  return bare.replace(/^[ab]\//, '');
}

/**
 * Every file in one unified patch, in the order git wrote them. A patch that was cut short by the
 * server's ceiling ends in half a file; that half is kept and counted for what it holds, because
 * leaving it out would hide a change the screen was told about.
 */
export function splitPatch(patch: string): PatchFile[] {
  if (!patch.trim()) return [];
  const out: PatchFile[] = [];
  for (const section of patch.split(/^diff --git /m)) {
    if (!section.trim()) continue;
    const lines = section.split('\n');
    let change: PatchFile['change'] = 'M';
    let path = '';
    let from: string | null = null;
    let binary = false;
    let additions = 0;
    let deletions = 0;
    let start = lines.length;
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i];
      if (start === lines.length) {
        if (l.startsWith('new file mode')) change = 'A';
        else if (l.startsWith('deleted file mode')) change = 'D';
        else if (l.startsWith('rename from ')) { change = 'R'; from = patchPath(l.slice(12)); }
        else if (l.startsWith('rename to ')) { change = 'R'; path = patchPath(l.slice(10)); }
        else if (l.startsWith('--- ') && !l.startsWith('--- /dev/null') && !path) path = patchPath(l.slice(4));
        else if (l.startsWith('+++ ') && !l.startsWith('+++ /dev/null')) path = patchPath(l.slice(4));
        if (l.startsWith('@@') || l.startsWith('Binary files')) {
          start = i;
          binary = l.startsWith('Binary files');
        }
        continue;
      }
      if (l.startsWith('+') && !l.startsWith('+++')) additions++;
      else if (l.startsWith('-') && !l.startsWith('---')) deletions++;
    }
    // The header's own first line — `a/x b/x` — is the last word on a file whose ---/+++ lines were cut off.
    if (!path) path = patchPath((lines[0] ?? '').split(' b/').pop() ?? '');
    if (!path) continue;
    out.push({ path, change, additions, deletions, binary, from, body: lines.slice(start).join('\n').trimEnd() });
  }
  return out;
}
