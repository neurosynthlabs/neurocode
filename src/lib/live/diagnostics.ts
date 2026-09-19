import { useSyncExternalStore } from 'react';
import { request } from '@/lib/api';

/* Problems and language servers for the Workbench (GET/POST /diagnostics/* and /lsp/*).

   A check runs the project's own checkers — its tsc, eslint or oxlint, ruff, mypy or pyright, go vet,
   cargo check, Gradle or Maven, dotnet build — only those it declares and this machine has, and reads what
   they print into problems. Paths come back absolute (`path`, what the editor opens) and as the Workbench
   names them (`file`: label-prefixed for a project's further source). Lines and columns are 1-based;
   columns count UTF-16 code units, as the editor does.

   The Problems tab, the status bar and the editor are three components the Workbench places apart; the small
   store at the bottom is how they hear each other — which check finished, how many problems there are, how
   to open a file at a line, and what the language server says about the file shown. */

export type Severity = 'error' | 'warning' | 'info';
export const SEVERITIES: Severity[] = ['error', 'warning', 'info'];

export interface Problem {
  /** The source's label, or the folder's name. */
  source: string;
  /** As the Workbench names it: relative, with a further source's label in front; absolute when outside the folder. */
  file: string;
  /** Absolute and real: what the editor opens. */
  path: string;
  line: number;
  col: number;
  endLine: number | null;
  endCol: number | null;
  severity: Severity;
  /** The tool's own code for it: TS2322, F401, no-undef, E0308. */
  code: string | null;
  message: string;
  /** Which checker named it: tsc, eslint, ruff, … */
  tool: string;
}

export interface Checker { tool: string; label: string; command: string; why: string }
/** A checker the project declares whose tool this machine does not have, with what installs it. */
export interface MissingChecker { tool: string; why: string }

export type ToolStatus = 'waiting' | 'running' | 'passed' | 'problems' | 'failed' | 'timeout' | 'cancelled' | 'error';
export interface CheckTool extends Checker {
  status: ToolStatus;
  exit: number | null;
  ms: number | null;
  /** Problems it named (counted before the check's ceiling). */
  problems: number;
  note: string;
  /** Its last lines, when it failed without naming a problem. */
  output: string[];
}

export interface CheckTarget {
  kind: 'project' | 'folder';
  folder: string;
  name: string;
  projectId: string | null;
  source: string | null;
  prefix: string;
}

export type CheckStatus = 'running' | 'done' | 'cancelled' | 'failed';
export type Counts = Record<Severity, number>;

export interface CheckDoc {
  id: string;
  target: CheckTarget;
  status: CheckStatus;
  startedAt: string | null;
  endedAt: string | null;
  ms: number | null;
  tools: CheckTool[];
  missing: MissingChecker[];
  counts: Counts;
  /** Every problem named; `kept` of them are held (at most 5 000), `capped` when that cut some. */
  total: number;
  kept: number;
  capped: boolean;
  /** Problems matching the filters asked for; `problems` is one page of them, errors first. */
  matching: number;
  offset: number;
  limit: number;
  problems: Problem[];
}

export interface Checkers { target: CheckTarget; checkers: Checker[]; missing: MissingChecker[] }

/** A project (its first source unless one is named), or a folder on this machine. */
export type CheckWhere = { projectId: string; source?: string | null } | { folder: string };

export interface FileProblems { path: string; checkId: string | null; checkedAt: string | null; problems: Problem[] }

export type LspState = 'none' | 'missing' | 'available' | 'starting' | 'ready' | 'failed';
export interface LspStatus {
  language: string | null;
  state: LspState;
  /** The server's program name: pyright-langserver, gopls, … */
  server: string | null;
  root: string | null;
  /** What the status bar says: "pyright-langserver ready", or what to install. */
  message: string;
}
/** Where a name is defined. `openable` is false outside the folders this server opens (a library's own file). */
export interface LspPlace { path: string; openable: boolean; line: number; col: number; endLine: number; endCol: number }
export interface LspHover { server: string; markdown: string | null; range: LspPlace | null }
export interface LspDefinition { server: string; locations: LspPlace[] }
export interface LspSymbol { name: string; detail: string | null; kind: string; depth: number; line: number; col: number }

const seg = encodeURIComponent;
const where = (w: CheckWhere) => ('folder' in w
  ? `folder=${seg(w.folder)}`
  : `projectId=${seg(w.projectId)}${w.source ? `&source=${seg(w.source)}` : ''}`);
// Detection reads a few files; a language server may take a while to start on its first question.
const slow = () => AbortSignal.timeout(20_000);
const starting = () => AbortSignal.timeout(60_000);

export const diagnosticsApi = {
  checkers: (w: CheckWhere) => request<Checkers>(`/diagnostics/checkers?${where(w)}`, { signal: slow() }),
  start: (w: CheckWhere, tools?: string[]) =>
    request<CheckDoc>('/diagnostics/checks', { method: 'POST', json: { ...w, ...(tools ? { tools } : {}) }, signal: slow() }),
  check: (id: string, opts: { offset?: number; limit?: number; severity?: Severity | null; tool?: string | null } = {}) => {
    const q = new URLSearchParams();
    if (opts.offset) q.set('offset', String(opts.offset));
    if (opts.limit !== undefined) q.set('limit', String(opts.limit));
    if (opts.severity) q.set('severity', opts.severity);
    if (opts.tool) q.set('tool', opts.tool);
    const s = q.toString();
    return request<CheckDoc>(`/diagnostics/checks/${seg(id)}${s ? `?${s}` : ''}`);
  },
  /** The newest check of each folder of a project, or of one folder. */
  recent: (w: CheckWhere) => request<CheckDoc[]>(`/diagnostics/checks?${'folder' in w ? `folder=${seg(w.folder)}` : `projectId=${seg(w.projectId)}`}`),
  cancel: (id: string) => request<CheckDoc>(`/diagnostics/checks/${seg(id)}/cancel`, { method: 'POST', json: {}, signal: slow() }),
  file: (path: string) => request<FileProblems>(`/diagnostics/file?path=${seg(path)}`),
};

export const lspApi = {
  status: (path: string) => request<LspStatus>(`/lsp/status?path=${seg(path)}`, { signal: slow() }),
  hover: (path: string, line: number, col: number, text?: string) =>
    request<LspHover>('/lsp/hover', { method: 'POST', json: { path, line, col, ...(text === undefined ? {} : { text }) }, signal: starting() }),
  definition: (path: string, line: number, col: number, text?: string) =>
    request<LspDefinition>('/lsp/definition', { method: 'POST', json: { path, line, col, ...(text === undefined ? {} : { text }) }, signal: starting() }),
  symbols: (path: string, text?: string) =>
    request<{ server: string; symbols: LspSymbol[]; capped: boolean }>('/lsp/symbols', { method: 'POST', json: { path, ...(text === undefined ? {} : { text }) }, signal: starting() }),
};

/* ── what the Problems tab, the status bar and the editor share ─────────────────────────────── */

interface Shared {
  /** Summed over the newest check of each folder of what the Workbench shows; null before any check. */
  counts: Counts | null;
  checking: boolean;
  /** Bumped whenever a check finishes: the editor reads its file's problems again. */
  version: number;
  /** Bumped to bring the Problems tab forward. */
  reveal: number;
  /** What the language server says about the file the editor shows. */
  lsp: { path: string; status: LspStatus } | null;
}

let shared: Shared = { counts: null, checking: false, version: 0, reveal: 0, lsp: null };
const listeners = new Set<() => void>();
let opener: ((path: string, line?: number) => void) | null = null;

function set(next: Partial<Shared>) {
  shared = { ...shared, ...next };
  for (const l of listeners) l();
}
function subscribe(l: () => void) {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

export function sumCounts(checks: CheckDoc[]): Counts | null {
  const done = checks.filter((c) => c.status !== 'running');
  if (!done.length) return null;
  const out: Counts = { error: 0, warning: 0, info: 0 };
  for (const c of done) for (const s of SEVERITIES) out[s] += c.counts[s] ?? 0;
  return out;
}

export const problemsStore = {
  /** A fresh summary of the newest checks; `finished` when one just ended, so the editor looks again. */
  summary(checks: CheckDoc[], finished = false) {
    set({ counts: sumCounts(checks), checking: checks.some((c) => c.status === 'running'),
      version: finished || (shared.version === 0 && checks.some((c) => c.status !== 'running')) ? shared.version + 1 : shared.version });
  },
  clear() { set({ counts: null, checking: false }); },
  /** The Workbench's way to open a file, handed over by its bottom panel. */
  setOpener(fn: ((path: string, line?: number) => void) | null) { opener = fn; },
  open(path: string, line?: number): boolean {
    if (!opener) return false;
    opener(path, line);
    return true;
  },
  showProblems() { set({ reveal: shared.reveal + 1 }); },
  setLsp(path: string, status: LspStatus | null) { set({ lsp: status ? { path, status } : null }); },
  get: () => shared,
};

export function useProblems(): Shared {
  return useSyncExternalStore(subscribe, () => shared, () => shared);
}

/** Read the newest checks of what the Workbench shows and tell the status bar and the editor. */
export async function announce(target: CheckWhere | null, finished: boolean) {
  if (!target) return;
  try {
    const recent = await diagnosticsApi.recent('folder' in target ? target : { projectId: target.projectId });
    problemsStore.summary(recent, finished);
  } catch (e) {
    console.warn('[NeuroCode] the problem counts were not read:', e);
  }
}
