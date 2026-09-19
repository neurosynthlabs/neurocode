import { request } from '@/lib/api';
import type { RuleDecision } from '@/lib/live/work';

/* The web: whether search is configured (GET /web), setting its key (PUT /web, workspace:admin), and a
   person trying a search or reading one page (POST /web/search, /web/fetch, ai:use). The key is kept in
   the API's secrets file and only ever comes back masked, and only to an admin. A fetch never reaches a
   local or private address, and every one of them passes the tool rules first. */

export interface WebStatus {
  provider: 'brave';
  label: string;
  configured: boolean;
  /** Where a key is made. */
  keysAt: string;
  /** `••••1234`; present only for someone who may change it, null when none is set. */
  keyMask?: string | null;
  fetch: { timeoutS: number; maxBytes: number; maxHops: number };
}

export interface WebResult {
  title: string;
  url: string;
  snippet: string;
}

export interface WebSearch {
  query: string;
  results: WebResult[];
  decision: RuleDecision;
}

export interface WebPage {
  url: string;
  requested: string;
  status: number;
  title: string;
  text: string;
  bytes: number;
  truncated: boolean;
  contentType: string;
  hops: number;
  decision: RuleDecision;
}

/** Who may set the search key. */
export const WEB_KEY_PERMISSION = 'workspace:admin';

export const webApi = {
  status: () => request<WebStatus>('/web'),
  /** An empty key removes it. */
  setKey: (key: string) => request<WebStatus>('/web', { method: 'PUT', json: { key } }),
  search: (q: string, projectId: string | null = null) =>
    request<WebSearch>('/web/search', { method: 'POST', json: { q, projectId }, signal: AbortSignal.timeout(25_000) }),
  fetch: (url: string, projectId: string | null = null) =>
    request<WebPage>('/web/fetch', { method: 'POST', json: { url, projectId }, signal: AbortSignal.timeout(25_000) }),
};
