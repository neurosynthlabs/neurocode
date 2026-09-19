import { request } from '@/lib/api';
import type { Project } from '@/types';

/* A project's sources: the folder or repository it was onboarded from, and every further one added to it
   — the API beside the web app, a data repository — worked on as one project. A further source's files
   appear inside the project under its label (`api/app/main.py`); the first source's keep their own paths. */

export type SourceStatus = 'onboarding' | 'active' | 'failed';

/** `code` is worked on; `reference` is indexed and read for grounding, and never written to — documents, a
    design system, another team's repository. The first source is always code. */
export type SourceRole = 'code' | 'reference';

/** What a project document carries of each source, the first one first (its `id` is null). */
export interface SourceSummary {
  id: number | null;
  label: string;
  kind: 'git' | 'local';
  status: SourceStatus;
  /** Absent on a document from before sources had roles: code. */
  role?: SourceRole;
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
  role?: SourceRole;
}

/** A project as the API sends it since projects hold sources and reference other projects. Absent on a document streamed before. */
export type ProjectWithSources = Project & { sources?: SourceSummary[]; references?: string[] };

/** The ids of the projects a project reads from, in the order they were added. */
export const referencesOf = (p: Project): string[] => (p as ProjectWithSources).references ?? [];

/** Whether a source is only read: a reference is never written by an agent. */
export const readOnly = (s: { role?: SourceRole }) => s.role === 'reference';

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
  /** A rename indexes the project again under the new label; a move only reorders; a role changes what agents may do there. */
  edit: (projectId: string, id: number, change: { label?: string; position?: number; role?: SourceRole }) =>
    request<ProjectSource>(`/projects/${seg(projectId)}/sources/${id}`, { method: 'PATCH', json: change }),
  /** Takes its files out of the project's index. Its folder, and a clone of it, stay on disk. */
  remove: (projectId: string, id: number) =>
    request<{ ok: boolean; id: number; label: string }>(`/projects/${seg(projectId)}/sources/${id}`, { method: 'DELETE' }),
  /** Reads it again; a source that failed is onboarded again from the start. */
  reindex: (projectId: string, id: number) =>
    request<{ ok: boolean; onboarding: boolean }>(`/projects/${seg(projectId)}/sources/${id}/reindex`, { method: 'POST' }),
};

/* Another project this one reads from: the service it calls, the library it uses, the system it replaces. Its
   code, documents and memory are searched and handed to models beside this project's own, labelled as a
   reference, and nothing there is ever written from here. */

/** One reference, in either direction: the other project, and why, in a person's words. */
export interface ProjectReference {
  id: number;
  project: { id: string; name: string; status: Project['status']; understoodPct: number | null };
  note: string;
  createdAt: string | null;
}

export interface References {
  /** The projects this one reads from, the first added first. */
  references: ProjectReference[];
  /** The projects that read from this one. */
  referencedBy: ProjectReference[];
  /** Retrieval and grounding search the first this many beside the project's own pieces. */
  readAtMost: number;
  /** The most one project may reference. */
  max: number;
}

export const referencesApi = {
  list: (projectId: string) => request<References>(`/projects/${seg(projectId)}/references`),
  /** Needs projects:onboard. Refused for the project itself, one already referenced, and a project that does not exist. */
  add: (projectId: string, referencedId: string, note: string) =>
    request<ProjectReference>(`/projects/${seg(projectId)}/references`, { method: 'POST', json: { referencedId, note } }),
  note: (projectId: string, id: number, note: string) =>
    request<ProjectReference>(`/projects/${seg(projectId)}/references/${id}`, { method: 'PATCH', json: { note } }),
  remove: (projectId: string, id: number) =>
    request<{ ok: boolean; id: number; referencedId: string }>(`/projects/${seg(projectId)}/references/${id}`, { method: 'DELETE' }),
};
