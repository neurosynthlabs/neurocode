import { request } from '@/lib/api';
import { socketUrl } from '@/lib/live/terminal';

/* Debugging a project's program: Python through debugpy (the Debug Adapter Protocol), Node through its own
   inspector. Both arrive in one shape. Paths are absolute, the same paths the Workbench's editor opens, so
   a frame opens its file and a breakpoint in the editor is a breakpoint here. */

export type DebugStatus = 'starting' | 'running' | 'paused' | 'ended' | 'failed';
export type DebugLanguage = 'python' | 'node';

export interface DebugFrame {
  id: number;
  name: string;
  /** Absolute; null for code with no file (a REPL line, node's internals). */
  path: string | null;
  line: number | null;
  column: number | null;
  /** The adapter's hint: 'subtle' for library frames, 'label' for separators. */
  hint: string | null;
}

export interface DebugBreakpoint {
  line: number;
  /** The debugger bound it to code. False until it has, or when it never could. */
  verified: boolean;
  message: string | null;
}

export interface DebugOutput { category: string; text: string }

export interface DebugSessionDoc {
  id: string;
  projectId: string;
  name: string;
  language: DebugLanguage;
  program: string | null;
  module: string | null;
  args: string[];
  cwd: string;
  /** The python (or node) the program runs with — the project's own .venv when it has one. */
  interpreter: string;
  runConfigId: number | null;
  status: DebugStatus;
  /** Why it failed, in the debugger's words. */
  note: string;
  stopped: { reason: string; threadId: number | null; description: string } | null;
  /** The stopped thread's frames, top first; empty unless paused. */
  frames: DebugFrame[];
  exitCode: number | null;
  breakpoints: Record<string, DebugBreakpoint[]>;
  output: DebugOutput[];
  startedAt: string | null;
  endedAt: string | null;
  restarts: number;
}

export interface DebugScope { name: string; ref: number; expensive: boolean }
export interface DebugVariable {
  name: string;
  value: string;
  type: string | null;
  /** Non-zero when it has children to list. */
  ref: number;
  named: number | null;
  indexed: number | null;
}
export interface Evaluated { result: string; type: string | null; ref: number }

/** What the debug socket sends, as JSON text frames. */
export type DebugEvent =
  | { type: 'state'; session: DebugSessionDoc }
  | ({ type: 'output' } & DebugOutput)
  | { type: 'resync' };

export interface DebugStart {
  runConfigId?: number;
  /** A file of the checkout, relative to `cwd` or absolute inside the checkout. */
  program?: string;
  module?: string;
  args?: string[];
  cwd?: string;
  language?: DebugLanguage;
  /** The Workbench's breakpoints, by absolute path, in place before the first line runs. */
  breakpoints: Record<string, number[]>;
}

export type StepCommand = 'continue' | 'next' | 'stepIn' | 'stepOut' | 'pause';

const seg = encodeURIComponent;
// Starting a debugger launches two processes and waits for the program to be ready.
const startTimeout = () => AbortSignal.timeout(60_000);
const commandTimeout = () => AbortSignal.timeout(20_000);
const command = <T,>(id: string, name: string, args: Record<string, unknown> = {}) =>
  request<T>(`/debug/${seg(id)}/${name}`, { method: 'POST', json: args, signal: commandTimeout() });

export const debug = {
  start: (projectId: string, input: DebugStart) =>
    request<DebugSessionDoc>(`/projects/${seg(projectId)}/debug`, { method: 'POST', json: input, signal: startTimeout() }),
  list: (projectId: string) => request<DebugSessionDoc[]>(`/projects/${seg(projectId)}/debug`),
  get: (id: string) => request<DebugSessionDoc>(`/debug/${seg(id)}`),
  step: (id: string, name: StepCommand, threadId?: number | null) =>
    command<{ ok: boolean }>(id, name, threadId == null ? {} : { threadId }),
  restart: (id: string) => request<{ ok: boolean }>(`/debug/${seg(id)}/restart`, { method: 'POST', json: {}, signal: startTimeout() }),
  terminate: (id: string) => command<{ ok: boolean }>(id, 'terminate'),
  scopes: (id: string, frameId: number) => command<{ scopes: DebugScope[] }>(id, 'scopes', { frameId }),
  variables: (id: string, ref: number) => command<{ variables: DebugVariable[] }>(id, 'variables', { ref }),
  evaluate: (id: string, expression: string, frameId: number | null, context: 'watch' | 'repl') =>
    command<Evaluated>(id, 'evaluate', { expression, context, ...(frameId === null ? {} : { frameId }) }),
  setBreakpoints: (id: string, path: string, lines: number[]) =>
    command<{ path: string; breakpoints: DebugBreakpoint[] }>(id, 'setBreakpoints', { path, lines }),
  end: (id: string) => request<{ ok: boolean }>(`/debug/${seg(id)}`, { method: 'DELETE', signal: commandTimeout() }),
  socket: (id: string) => socketUrl(`/debug/${seg(id)}/ws`),
};
