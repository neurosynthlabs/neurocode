import { request } from '@/lib/api';
import { socketUrl } from '@/lib/live/terminal';

/* Jupyter notebooks in the Workbench (/notebooks/*): the .ipynb file read and saved as nbformat 4, and a real
   Jupyter kernel on the machine the API runs on — the project's own environment first. The file is named by
   the absolute path the Workbench opened. What a kernel prints streams over its socket; commands go over HTTP.
   Everything here needs machine:access (the Owner role) and a server with machine access on. */

export type CellType = 'code' | 'markdown' | 'raw';

/** Text is one string here: the server joins nbformat's lists of lines, and splits them again on save. */
export type NbOutput =
  | { output_type: 'stream'; name: 'stdout' | 'stderr'; text: string }
  | { output_type: 'display_data'; data: Record<string, unknown>; metadata: Record<string, unknown> }
  | { output_type: 'execute_result'; data: Record<string, unknown>; metadata: Record<string, unknown>; execution_count: number | null }
  | { output_type: 'error'; ename: string; evalue: string; traceback: string[] };

export interface NbCell {
  id: string;
  cell_type: CellType;
  source: string;
  metadata: Record<string, unknown>;
  execution_count?: number | null;
  outputs?: NbOutput[];
  attachments?: Record<string, Record<string, string>>;
}

export interface NbDocument {
  nbformat: 4;
  nbformat_minor: number;
  metadata: Record<string, unknown>;
  cells: NbCell[];
}

export interface NotebookFile {
  path: string;
  name: string;
  size: number;
  modified: string | null;
  /** What a save must send back as expectSha1. */
  sha1: string;
  /** From the notebook's metadata: the kernelspec's language, else language_info's, else python. */
  language: string;
  kernelName: string | null;
  /** The file was empty: this is a fresh notebook, not yet written. */
  new: boolean;
  notebook: NbDocument;
}

export interface NotebookSaved { path: string; size: number; modified: string | null; sha1: string }

/** A kernel that can run a notebook: the project's own interpreter, an installed kernelspec, or the machine's python3. */
export interface KernelChoice {
  name: string;
  displayName: string;
  language: string;
  source: 'project' | 'kernelspec' | 'machine';
  interpreter: string | null;
}

export interface KernelOptions {
  language: string;
  /**
   * What starting a kernel would use; null when nothing on this machine can run the notebook. Opening a
   * notebook only looks — nothing is run to find these — so an interpreter found beside the notebook has
   * no version in its displayName until the person starts it.
   */
  choice: KernelChoice | null;
  available: KernelChoice[];
  /** What to install, in words, when choice is null. */
  missing: string | null;
}

export type KernelStatus = 'starting' | 'idle' | 'busy' | 'restarting' | 'dead' | 'closed';
export type RunStatus = 'queued' | 'running' | 'ok' | 'error' | 'aborted';

export interface KernelDoc extends KernelChoice {
  id: string;
  path: string;
  notebook: string;
  status: KernelStatus;
  /** Why it is dead or closed, in the kernel's or the server's words. */
  note: string;
  executionCount: number;
  restarts: number;
  watching: number;
  startedAt: string | null;
  lastActiveAt: string | null;
  /** Only on a start: whether it was started now or was already running for this notebook. */
  new?: boolean;
}

export interface KernelRun {
  requestId: string;
  cellId: string;
  status: RunStatus;
  executionCount: number | null;
  outputs: NbOutput[];
  /** It printed more than the server keeps for one cell; the last output says so. */
  truncated: boolean;
  startedAt: string | null;
  endedAt: string | null;
}

/** What the kernel socket sends, as JSON text frames. Run events carry requestId and cellId. */
export type KernelEvent =
  | { type: 'hello'; kernel: KernelDoc; runs: KernelRun[] }
  | { type: 'state'; kernel: KernelDoc }
  | { type: 'queued'; requestId: string; cellId: string }
  | { type: 'started'; requestId: string; cellId: string; executionCount: number | null }
  | { type: 'stream'; requestId: string; cellId: string; name: 'stdout' | 'stderr'; text: string }
  | { type: 'output'; requestId: string; cellId: string; output: NbOutput; displayId: string | null }
  | { type: 'clear'; requestId: string; cellId: string }
  | { type: 'update'; displayId: string; output: NbOutput }
  | { type: 'done'; requestId: string; cellId: string; status: RunStatus; executionCount: number | null }
  | { type: 'resync' }
  | { type: 'closed' };

/** A socket closed with one of these will not open again by trying. */
export const KERNEL_CLOSE: Record<number, string> = {
  4401: 'Your session ended. Sign in again to run this notebook.',
  4403: 'This kernel cannot be reached from here.',
  4404: 'This kernel is shut down.',
};

const seg = encodeURIComponent;
const where = (path: string) => new URLSearchParams({ path }).toString();
// A kernel imports its environment before it answers; a large one (torch, tensorflow) takes a while.
const slow = () => AbortSignal.timeout(90_000);
// Reading or saving a notebook with many pictures moves megabytes.
const big = () => AbortSignal.timeout(60_000);

export const notebooks = {
  open: (path: string) => request<NotebookFile>(`/notebooks/file?${where(path)}`, { signal: big() }),
  save: (path: string, notebook: NbDocument, expectSha1: string) =>
    request<NotebookSaved>('/notebooks/file', { method: 'PUT', json: { path, notebook, expectSha1 }, signal: big() }),
  kernelOptions: (path: string) =>
    request<KernelOptions>(`/notebooks/kernelspecs?${where(path)}`, { signal: AbortSignal.timeout(45_000) }),
  start: (path: string, kernel?: string | null) =>
    request<KernelDoc>('/notebooks/kernels', { method: 'POST', json: { path, ...(kernel ? { kernel } : {}) }, signal: slow() }),
  /** Your kernels, oldest first — a handful at most. */
  kernels: () => request<KernelDoc[]>('/notebooks/kernels'),
  kernel: (id: string) => request<KernelDoc & { runs: KernelRun[] }>(`/notebooks/kernels/${seg(id)}?runs=true`, { signal: big() }),
  execute: (id: string, cellId: string, code: string) =>
    request<KernelRun>(`/notebooks/kernels/${seg(id)}/execute`, { method: 'POST', json: { cellId, code } }),
  interrupt: (id: string) => request<KernelDoc>(`/notebooks/kernels/${seg(id)}/interrupt`, { method: 'POST', signal: big() }),
  restart: (id: string) => request<KernelDoc>(`/notebooks/kernels/${seg(id)}/restart`, { method: 'POST', signal: slow() }),
  shutDown: (id: string) => request<{ ok: boolean; id: string }>(`/notebooks/kernels/${seg(id)}`, { method: 'DELETE', signal: big() }),
  socket: (id: string) => socketUrl(`/notebooks/kernels/${seg(id)}/ws`),
};

/** A fresh cell, with an id nbformat 4.5 accepts. */
export function newCell(cellType: CellType, source = ''): NbCell {
  const id = crypto.randomUUID().replace(/-/g, '').slice(0, 8);
  return cellType === 'code'
    ? { id, cell_type: 'code', source, metadata: {}, execution_count: null, outputs: [] }
    : { id, cell_type: cellType, source, metadata: {} };
}
