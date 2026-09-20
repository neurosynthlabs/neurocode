import { API_BASE, ApiError, SIGNED_OUT, request } from '@/lib/api';
import type { Project } from '@/types';

/* The Workbench's calls: folders, files and git on the machine the API runs on (GET/PUT/POST /machine/*).
   A project's folders come from its sources (src/lib/live/sources.ts). Every path goes out and comes back
   absolute and real — symlinks resolved — because that is the path the server checks against its roots.
   Everything here needs machine:access (the Owner role) and a server with machine access on. */

export interface MachineRoot { path: string; label: string }

export interface MachineEntry {
  name: string;
  path: string;
  kind: 'dir' | 'file' | 'link';
  /** Bytes, for a file or a link to one inside the roots; null for a folder. */
  size: number | null;
  modified: string | null;
  /** A folder holding a repository of its own. */
  git: boolean;
  /** What a link points at when that is inside the roots; null when it points outside, or nowhere. */
  linkTo: 'dir' | 'file' | null;
}

export interface MachineListing {
  path: string;
  /** Null at a root: the browser goes no higher. */
  parent: string | null;
  entries: MachineEntry[];
  /** More than the 2 000 entries a listing holds; `total` says how many. */
  capped: boolean;
  total: number;
  /** Dot files left out because hidden files were not asked for. */
  hidden: number;
}

export interface MachineFile {
  path: string;
  size: number;
  modified: string | null;
  binary: boolean;
  /** The text when the file is UTF-8 text; null for a binary file or another encoding. */
  text: string | null;
  /** A label for the status bar; the editor picks its highlighting from the file name. */
  language: string | null;
  /** What a save must send back as expectSha1. */
  sha1: string;
  reason: 'binary' | 'not UTF-8' | null;
}

export interface MachineSaved { path: string; size: number; modified: string | null; sha1: string }

/** One letter per file, git's: M modified, A added, D deleted, R renamed, C copied, T type changed, U conflicted, ? untracked. */
export type GitLetter = 'M' | 'A' | 'D' | 'R' | 'C' | 'T' | 'U' | '?';
export interface GitChange { path: string; status: GitLetter; staged: boolean; was?: string }

/** How git's letter reads on a screen: a colour, and the word that names it. The file tree and the
    Changes panel draw the same letters, so they read them from one place rather than two that drift. */
export const GIT_TONE: Record<GitLetter, { cls: string; word: string }> = {
  M: { cls: 'text-warn', word: 'modified' },
  T: { cls: 'text-warn', word: 'type changed' },
  A: { cls: 'text-ok', word: 'added' },
  '?': { cls: 'text-ok', word: 'untracked' },
  C: { cls: 'text-ok', word: 'copied' },
  R: { cls: 'text-info', word: 'renamed' },
  D: { cls: 'text-danger', word: 'deleted' },
  U: { cls: 'text-danger', word: 'conflicted' },
};

/** `git status` for the repository holding a path. Paths in `changed` are relative to `root`. */
export interface MachineGit {
  root: string;
  /** Null on a detached HEAD. */
  branch: string | null;
  head: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  changed: GitChange[];
  capped: boolean;
}

export interface MachineFiles {
  root: string;
  /** Relative to root, with '/' between folders. */
  files: string[];
  capped: boolean;
  /** 'git': the repository's own list, .gitignore respected. 'walk': the folder walked, hidden folders skipped. */
  source: 'git' | 'walk';
}

/** macOS guards Desktop, Documents, Downloads, iCloud Drive and external volumes per app; the API says which
    app was refused, and the switch that lets it in. `app` is null when the API could not tell. */
export interface OsPermission { code: 'needs_os_permission'; folder: string; place: string; app: string | null; settings: string }

/** An ApiError that may carry the macOS privacy answer, for a screen that shows what to do about it. */
export class MachineError extends ApiError {
  permission: OsPermission | null;
  constructor(message: string, status: number, permission: OsPermission | null) {
    super(message, status);
    this.name = 'MachineError';
    this.permission = permission;
  }
}

/** The macOS privacy answer an error carries, when it carries one. */
export const osPermission = (e: unknown): OsPermission | null => (e instanceof MachineError ? e.permission : null);

/* The machine's own calls read the error body whole: a folder macOS guards answers 403 with a stable code, the
   folder and the app, which the plain request() keeps only as words. Same cookie, CSRF header and timeout. */
async function call<T>(path: string, { method = 'GET', json, signal }: { method?: string; json?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method,
    credentials: 'include',
    headers: { 'X-NC-Client': 'web', ...(json === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: json === undefined ? undefined : JSON.stringify(json),
    signal: signal ?? AbortSignal.timeout(5000),
  });
  if (res.ok) return res.json() as Promise<T>;
  throw await failure(res.status, () => res.json());
}

async function failure(status: number, body: () => Promise<unknown>): Promise<MachineError> {
  let detail = `HTTP ${status}`;
  let permission: OsPermission | null = null;
  try {
    const got = await body();
    if (got && typeof got === 'object' && 'detail' in got) {
      const d = (got as { detail: unknown }).detail;
      if (typeof d === 'string') detail = d;
      else if (Array.isArray(d) && d[0] && typeof d[0] === 'object' && 'msg' in d[0]) detail = String(d[0].msg);
      if ((got as { code?: unknown }).code === 'needs_os_permission') permission = got as unknown as OsPermission;
    }
  } catch { /* the body was not JSON — keep the status line */ }
  if (status === 401) window.dispatchEvent(new Event(SIGNED_OUT));
  return new MachineError(detail, status, permission);
}

/** What an archive would become, read from its headers with every entry checked — nothing is written yet. */
export interface ArchiveSurvey {
  path: string;
  name: string;
  /** The folder name to offer: the archive's single top folder, or its own name without the suffix. */
  suggestedName: string;
  kind: 'zip' | 'tar';
  /** The archive's own size, and what it unpacks to. */
  bytes: number;
  total: number;
  entries: number;
  files: number;
  folders: number;
  /** The single folder at the top that is dropped, so the files sit at the project's root; null when there is none. */
  top: string | null;
  /** The first few paths as they will be in the project. */
  sample: string[];
}

/** What starting an import answers: the new project (onboarding), its new folder, and what will be unpacked there. */
export interface Imported extends Omit<ArchiveSurvey, 'path' | 'name' | 'suggestedName'> {
  project: Project;
  target: string;
}

export interface NewProjectOptions {
  excluded: string[];
  rules: { id: string; label: string; note: string }[];
}

/** The archives the importer unpacks, as a file picker's accept list. */
export const ARCHIVES = ['.zip', '.tar.gz', '.tgz'];

const q = encodeURIComponent;
/** git and a folder walk can take longer than the 5 s an ordinary call gets. */
const slow = () => AbortSignal.timeout(30000);

/** Upload an archive with the browser's progress: resolves with the import, or rejects with a MachineError. */
function upload(file: File, into: string, name: string, options: NewProjectOptions, onProgress?: (share: number) => void): Promise<Imported> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/machine/import/upload?into=${q(into)}&name=${q(name)}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader('X-NC-Client', 'web');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as Imported); } catch { reject(new MachineError('The API answered with something that is not JSON.', xhr.status, null)); }
        return;
      }
      void failure(xhr.status, async () => JSON.parse(xhr.responseText) as unknown).then(reject);
    };
    xhr.onerror = () => reject(new TypeError('The upload did not reach the API.'));
    const form = new FormData();
    form.append('options', JSON.stringify(options));
    form.append('archive', file, file.name);
    xhr.send(form);
  });
}

export const machineApi = {
  roots: () => request<MachineRoot[]>('/machine/roots'),
  /** Desktop, Downloads and Documents, when each is here and inside the roots. */
  places: () => request<MachineRoot[]>('/machine/places'),
  list: (path: string, hidden = false) => call<MachineListing>(`/machine/list?path=${q(path)}&hidden=${hidden}`),
  file: (path: string) => call<MachineFile>(`/machine/file?path=${q(path)}`, { signal: AbortSignal.timeout(20000) }),
  /** A tarball has no index: its headers are read by walking it, which takes a moment on a large one. */
  archive: (path: string) => call<ArchiveSurvey>(`/machine/archive?path=${q(path)}`, { signal: AbortSignal.timeout(120000) }),
  /** A new project from an archive on this machine: a new folder `name` in `into`, unpacked and onboarded in the background. */
  importArchive: (archive: string, into: string, name: string, options: NewProjectOptions) =>
    call<Imported>('/machine/import', { method: 'POST', json: { archive, into, name, ...options }, signal: AbortSignal.timeout(120000) }),
  /** The same, from a file the browser uploads — for a server this browser is not running on. */
  uploadArchive: upload,
  /** A new folder with a git repository and a README, committed once, then onboarded. */
  emptyProject: (into: string, name: string, options: NewProjectOptions) =>
    call<{ project: Project; target: string }>('/machine/empty-project', { method: 'POST', json: { into, name, ...options }, signal: AbortSignal.timeout(30000) }),
  save: (path: string, text: string, expectSha1: string) =>
    request<MachineSaved>('/machine/file', { method: 'PUT', json: { path, text, expectSha1 }, signal: AbortSignal.timeout(20000) }),
  mkdir: (path: string) => request<MachineEntry>('/machine/mkdir', { method: 'POST', json: { path } }),
  newFile: (path: string) => request<MachineEntry>('/machine/new-file', { method: 'POST', json: { path } }),
  git: (path: string) => request<MachineGit | null>(`/machine/git?path=${q(path)}`, { signal: slow() }),
  files: (path: string) => request<MachineFiles>(`/machine/files?path=${q(path)}`, { signal: slow() }),
};

/* ── the checkout's own changes ───────────────────────────────────
   The editor saves into the project's real working tree, and a working tree with changes that are not
   committed refuses every merge. These three are how a person deals with that without leaving for a
   terminal. They live here rather than with the Git screen's reads (lib/live/git.ts) because they are
   the machine's door — machine access on, `machine:access`, inside the machine's roots — and because
   the Workbench is the only screen that asks for them. Paths are relative to the repository's top,
   which is exactly what `MachineGit.changed` carries. */

/** What one file has that the last commit does not. `change` is git's letter; `?` is a file it has never seen. */
export interface FileDiff {
  path: string;
  change: string;
  tracked: boolean;
  additions: number;
  deletions: number;
  /** The patch as git printed it, cut at the server's ceiling. */
  patch: string;
  truncated: boolean;
}

export interface Committed {
  commit: string;
  sha: string;
  branch: string;
  files: number;
  message: string;
  /** The git identity on this machine that the commit was made with. */
  by: string;
}

export const checkoutApi = {
  fileDiff: (projectId: string, path: string, source?: string | null) =>
    request<FileDiff>(`/projects/${q(projectId)}/git/file-diff?path=${q(path)}${source ? `&source=${q(source)}` : ''}`,
      { signal: slow() }),
  commit: (projectId: string, paths: string[], message: string, source?: string | null) =>
    request<Committed>(`/projects/${q(projectId)}/git/commit`,
      { method: 'POST', json: { paths, message, ...(source ? { source } : {}) }, signal: slow() }),
  /** Puts these files back the way the last commit had them. One that commit does not hold is refused, never deleted. */
  discard: (projectId: string, paths: string[], source?: string | null) =>
    request<{ discarded: string[] }>(`/projects/${q(projectId)}/git/discard`,
      { method: 'POST', json: { paths, ...(source ? { source } : {}) }, signal: slow() }),
};

/** The last part of a path, the way a tab names a file. */
export const baseName = (path: string) => path.replace(/\/+$/, '').split('/').pop() || path;

/** The folder holding a path. */
export const dirName = (path: string) => {
  const cut = path.replace(/\/+$/, '').lastIndexOf('/');
  return cut <= 0 ? '/' : path.slice(0, cut);
};

/** A path joined under a folder, with exactly one '/' between. */
export const joinPath = (folder: string, name: string) => `${folder.replace(/\/+$/, '')}/${name.replace(/^\/+/, '')}`;

/** Whether `path` is `folder` or inside it. */
export const within = (path: string, folder: string) => path === folder || path.startsWith(`${folder.replace(/\/+$/, '')}/`);

/** Bytes as a person reads them. */
export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
