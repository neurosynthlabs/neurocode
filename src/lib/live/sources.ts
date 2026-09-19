import { request } from '@/lib/api';
import type { Project } from '@/types';

/* A project's sources: the folder or repository it was onboarded from, and every further one added to it
   — the API beside the web app, a data repository — worked on as one project. A further source's files
   appear inside the project under its label (`api/app/main.py`); the first source's keep their own paths. */

export type SourceStatus = 'onboarding' | 'active' | 'failed';

/** What a project document carries of each source, the first one first (its `id` is null). */
export interface SourceSummary {
  id: number | null;
  label: string;
  kind: 'git' | 'local';
  status: SourceStatus;
}

/** One source as GET /projects/{pid}/sources lists it. */
export interface ProjectSource extends SourceSummary {
  /** A folder on this machine, or a clone URL with any credentials removed. */
  repo: string;
  /** Empty for a local folder. */
  branch: string;
  /** Its place among the further sources; the first source is always 0. */
  position: number;
  /** Why it failed, when it did. */
  note: string;
  primary: boolean;
  createdAt: string | null;
  /** Where it is on this machine — sent only to someone who may browse the machine; null when it is not here. */
  root?: string | null;
}

export interface SourceInput {
  label: string;
  kind: 'git' | 'local';
  repo: string;
  branch: string;
}

/** A project as the API sends it since projects hold sources. Absent on a document streamed before. */
export type ProjectWithSources = Project & { sources?: SourceSummary[] };

/** The sources a project document names, or just its first one when it names none. */
export function sourcesOf(p: Project): SourceSummary[] {
  const listed = (p as ProjectWithSources).sources;
  if (listed) return listed;
  return p.source ? [{ id: null, label: p.id, kind: p.source.kind, status: p.status === 'onboarding' ? 'onboarding' : 'active' }] : [];
}

/** The same rule the API holds a label to: it is a folder name inside the project. */
export const LABEL = /^[a-z0-9][a-z0-9._-]{0,59}$/;

/** A label from a folder or a clone URL: its last part, lower-case, as a folder name. */
export function labelFrom(repo: string): string {
  const tail = repo.trim().replace(/\/+$/, '').split(/[/:\\]/).pop() ?? '';
  return tail.replace(/\.git$/i, '').toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[^a-z0-9]+/, '').slice(0, 60);
}

/** What the tone of a source's state is on screen. */
export const SOURCE_DOT: Record<SourceStatus, string> = { active: 'active', onboarding: 'loading', failed: 'failed' };

const seg = encodeURIComponent;

export const sourcesApi = {
  list: (projectId: string) => request<ProjectSource[]>(`/projects/${seg(projectId)}/sources`),
  /** Onboards it after answering: cloned or read, then the whole project measured and indexed again (projects:onboard). */
  add: (projectId: string, input: SourceInput) =>
    request<ProjectSource>(`/projects/${seg(projectId)}/sources`, { method: 'POST', json: input }),
  /** A rename indexes the project again under the new label; a move only reorders. */
  edit: (projectId: string, id: number, change: { label?: string; position?: number }) =>
    request<ProjectSource>(`/projects/${seg(projectId)}/sources/${id}`, { method: 'PATCH', json: change }),
  /** Takes its files out of the project's index. Its folder, and a clone of it, stay on disk. */
  remove: (projectId: string, id: number) =>
    request<{ ok: boolean; id: number; label: string }>(`/projects/${seg(projectId)}/sources/${id}`, { method: 'DELETE' }),
  /** Reads it again; a source that failed is onboarded again from the start. */
  reindex: (projectId: string, id: number) =>
    request<{ ok: boolean; onboarding: boolean }>(`/projects/${seg(projectId)}/sources/${id}/reindex`, { method: 'POST' }),
};
