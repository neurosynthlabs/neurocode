import { API_BASE, request, type ChatAttachment, type ChatMessage, type SessionDoc } from '@/lib/api';
import type { Plan, Task } from '@/types';

/* Shaping a session, as the API does it: answering a permission card, editing a question and
   regenerating an answer (new turns; the old ones stay, replaced), forking, export and import, "Make
   this a plan", uploads and the composer's @ mentions. The answering itself arrives on the stream. */

/** What the composer's @ picker offers: a file or symbol from the code index, a memory fact, a plan. */
export interface Mention {
  kind: 'file' | 'symbol' | 'fact' | 'plan';
  ref: string;
  name: string;
  /** A line of context: size, `kind · path:line`, `ref · category`, `ref · status`. */
  detail: string;
}

export interface Upload {
  id: number;
  name: string;
  mime: string;
  bytes: number;
  sha1: string;
  /** A picture: it is sent only to a lane whose model reads images. */
  image: boolean;
  by: string;
  at: string | null;
}

/** An export, ready to become a download in the browser. */
export interface ExportDoc { filename: string; mime: string; text: string }

export type PermitDecision = 'once' | 'session' | 'refuse';

/** What an upload may be, and how big: the same ceilings the server keeps. */
export const UPLOAD_LIMITS = { text: 1024 * 1024, image: 5 * 1024 * 1024 };
export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

const seg = encodeURIComponent;
const POST = (json?: unknown, ms = 8000) => ({ method: 'POST', json, signal: AbortSignal.timeout(ms) });

export const sessionsApi = {
  permit: (ref: string, messageId: number, decision: PermitDecision) =>
    request<SessionDoc>(`/sessions/${seg(ref)}/permissions/${messageId}`, POST({ decision })),
  edit: (ref: string, messageId: number, text: string, attachments?: Pick<ChatAttachment, 'kind' | 'ref' | 'name'>[]) =>
    request<{ message: ChatMessage; session: SessionDoc }>(`/sessions/${seg(ref)}/messages/${messageId}/edit`,
      POST({ text, ...(attachments ? { attachments } : {}) })),
  regenerate: (ref: string, messageId: number, lane: string | null = null) =>
    request<{ message: ChatMessage; session: SessionDoc }>(`/sessions/${seg(ref)}/messages/${messageId}/regenerate`,
      POST(lane ? { lane } : {})),
  fork: (ref: string, at: number) => request<SessionDoc>(`/sessions/${seg(ref)}/fork`, POST({ at })),
  exportDoc: (ref: string, format: 'md' | 'json') =>
    request<ExportDoc>(`/sessions/${seg(ref)}/export?format=${format}`, { signal: AbortSignal.timeout(20_000) }),
  importDoc: (projectId: string, document: unknown) =>
    request<SessionDoc>('/sessions/import', POST({ projectId, document }, 30_000)),
  /** A compile: it waits on a model, so it is given a model's time. */
  toPlan: (ref: string) =>
    request<Plan & { task: Task }>(`/sessions/${seg(ref)}/to-plan`, POST(undefined, 120_000)),
  upload: (ref: string, name: string, mime: string, data: string) =>
    request<Upload>(`/sessions/${seg(ref)}/files`, POST({ name, mime, data }, 60_000)),
  mentions: (projectId: string, q: string) =>
    request<{ items: Mention[] }>(`/projects/${seg(projectId)}/mentions?q=${seg(q)}`),
  /** Where an upload's bytes are served, for its chip — same origin, with the session cookie. */
  fileUrl: (ref: string, id: number | string) => `${API_BASE}/sessions/${seg(ref)}/files/${seg(String(id))}`,
};

/** A file's bytes as base64, read in the browser. */
export function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? '');
      resolve(url.slice(url.indexOf(',') + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error('The file could not be read.'));
    reader.readAsDataURL(file);
  });
}

/** Save an export as a file: the Blob is built here, so nothing else is asked of the server. */
export function download(doc: ExportDoc): void {
  const url = URL.createObjectURL(new Blob([doc.text], { type: doc.mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = doc.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** The Workbench, opened on a file of a project — at a line, when there is one. */
export function workbenchLink(projectId: string, path: string, line?: number | null): string {
  const q = new URLSearchParams({ project: projectId, path });
  if (line) q.set('line', String(line));
  return `/workbench?${q.toString()}`;
}
