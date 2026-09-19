import { API_BASE, request } from '@/lib/api';

/* The Workbench's terminals and run configurations, on the machine the API runs on. Every call needs the
   machine:access permission (an Owner's) and a server that allows machine access; a server that does not
   answers 404 "Machine access is off on this server". */

export type TerminalKind = 'shell' | 'run';
export type TerminalStatus = 'starting' | 'running' | 'exited';

/** A pseudo-terminal the API holds: a shell, or a run configuration's command. */
export interface TerminalDoc {
  id: string;
  kind: TerminalKind;
  /** The folder's name for a shell, the configuration's name for a run. */
  title: string;
  cwd: string;
  /** The command line a run executes; empty for a shell. */
  command: string;
  shell: string;
  pid: number | null;
  cols: number;
  rows: number;
  status: TerminalStatus;
  exitCode: number | null;
  startedAt: string | null;
  endedAt: string | null;
  /** Sockets watching it right now, this tab's included. */
  attached: number;
  /** Bytes of recent output kept to repaint a reconnecting tab. */
  bytes: number;
  projectId: string | null;
  runConfigId: number | null;
  /** How many times it has been started: 1, then one more per restart. */
  runs: number;
}

/** What the server says over a terminal socket, as JSON text frames. Output itself arrives as binary frames. */
export type TerminalMessage =
  | { type: 'hello'; terminal: TerminalDoc }
  | { type: 'state'; terminal: TerminalDoc }
  | { type: 'exit'; code: number | null }
  | { type: 'repaint' }
  | { type: 'closed' }
  | { type: 'error'; message: string };

export type RunConfigKind = 'run' | 'debug';
export type RunLanguage = 'python' | 'node' | 'shell';

/** How a person runs (or debugs) a project. Environment values never leave the server: `env` is their names. */
export interface RunConfig {
  id: number;
  projectId: string;
  name: string;
  kind: RunConfigKind;
  language: RunLanguage;
  /** For `run`, the command line. For `debug`, the program (a file of the checkout) or `-m module`. */
  command: string;
  args: string[];
  /** A folder of the checkout, relative to its root; empty for the root. */
  cwd: string;
  env: string[];
  createdBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface RunConfigInput {
  name: string;
  kind: RunConfigKind;
  language: RunLanguage;
  command: string;
  args: string[];
  cwd: string;
  env: Record<string, string>;
}

/** A change. `env` is a patch: a value sets a variable, null removes it, and one not named keeps its value. */
export interface RunConfigPatch {
  name?: string;
  language?: RunLanguage;
  command?: string;
  args?: string[];
  cwd?: string;
  env?: Record<string, string | null>;
}

/** A configuration read from the checkout's files — never run until a person saves and starts it. */
export interface RunSuggestion {
  name: string;
  kind: RunConfigKind;
  language: RunLanguage;
  command: string;
  args: string[];
  cwd: string;
  /** Where it was read from: "package.json script `dev`". */
  why: string;
  /** The project already has a configuration with this command in this folder. */
  saved: boolean;
}

export interface Detected {
  /** The first source's checkout. */
  checkout: string;
  /** Every checkout that was read; a further source's suggestions carry its label in their name and folder. */
  sources: { label: string; root: string; primary: boolean }[];
  suggestions: RunSuggestion[];
  /** The list stopped at the server's ceiling. */
  capped: boolean;
}

const seg = encodeURIComponent;
// Stopping waits for the program to leave (a hang-up, then a kill), and a restart starts it again after.
const slow = () => AbortSignal.timeout(20_000);

/** A WebSocket address for an API path: this page's own origin unless VITE_API_URL names another. */
export function socketUrl(path: string): string {
  const url = new URL(API_BASE + path, window.location.href);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

/** A socket closed with one of these will not open again by trying: signed out, not allowed, or gone. */
export const FINAL_CLOSE: Record<number, string> = {
  4401: 'Your session ended. Sign in again to reach this terminal.',
  4403: 'This terminal cannot be opened from here.',
  4404: 'This terminal is closed.',
};

export const terminals = {
  list: () => request<TerminalDoc[]>('/machine/terminals'),
  one: (id: string) => request<TerminalDoc>(`/machine/terminals/${seg(id)}`),
  open: (input: { cwd?: string | null; projectId?: string | null; cols: number; rows: number }) =>
    request<TerminalDoc>('/machine/terminals', { method: 'POST', json: input, signal: slow() }),
  stop: (id: string) => request<TerminalDoc>(`/machine/terminals/${seg(id)}/stop`, { method: 'POST', signal: slow() }),
  restart: (id: string) => request<TerminalDoc>(`/machine/terminals/${seg(id)}/restart`, { method: 'POST', signal: slow() }),
  close: (id: string) => request<{ ok: boolean; id: string }>(`/machine/terminals/${seg(id)}`, { method: 'DELETE', signal: slow() }),
  socket: (id: string) => socketUrl(`/machine/terminals/${seg(id)}/ws`),
};

export const runConfigs = {
  list: (projectId: string, kind?: RunConfigKind) =>
    request<RunConfig[]>(`/projects/${seg(projectId)}/run-configs?${new URLSearchParams({ ...(kind ? { kind } : {}), limit: '200' })}`),
  add: (projectId: string, input: RunConfigInput) =>
    request<RunConfig>(`/projects/${seg(projectId)}/run-configs`, { method: 'POST', json: input }),
  change: (projectId: string, id: number, patch: RunConfigPatch) =>
    request<RunConfig>(`/projects/${seg(projectId)}/run-configs/${id}`, { method: 'PATCH', json: patch }),
  remove: (projectId: string, id: number) =>
    request<{ ok: boolean; id: number }>(`/projects/${seg(projectId)}/run-configs/${id}`, { method: 'DELETE' }),
  detect: (projectId: string) =>
    request<Detected>(`/projects/${seg(projectId)}/run-configs/detect`, { signal: AbortSignal.timeout(20_000) }),
  /** Opens a terminal running the configuration; its output streams through that terminal's socket. */
  start: (id: number, size: { cols: number; rows: number }) =>
    request<TerminalDoc>(`/run-configs/${id}/start`, { method: 'POST', json: size, signal: slow() }),
};
