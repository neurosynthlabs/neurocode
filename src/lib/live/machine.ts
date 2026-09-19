import { request } from '@/lib/api';

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

const q = encodeURIComponent;
/** git and a folder walk can take longer than the 5 s an ordinary call gets. */
const slow = () => AbortSignal.timeout(30000);

export const machineApi = {
  roots: () => request<MachineRoot[]>('/machine/roots'),
  list: (path: string, hidden = false) => request<MachineListing>(`/machine/list?path=${q(path)}&hidden=${hidden}`),
  file: (path: string) => request<MachineFile>(`/machine/file?path=${q(path)}`, { signal: AbortSignal.timeout(20000) }),
  save: (path: string, text: string, expectSha1: string) =>
    request<MachineSaved>('/machine/file', { method: 'PUT', json: { path, text, expectSha1 }, signal: AbortSignal.timeout(20000) }),
  mkdir: (path: string) => request<MachineEntry>('/machine/mkdir', { method: 'POST', json: { path } }),
  newFile: (path: string) => request<MachineEntry>('/machine/new-file', { method: 'POST', json: { path } }),
  git: (path: string) => request<MachineGit | null>(`/machine/git?path=${q(path)}`, { signal: slow() }),
  files: (path: string) => request<MachineFiles>(`/machine/files?path=${q(path)}`, { signal: slow() }),
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
