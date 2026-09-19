import { request } from '@/lib/api';

/* Personal access tokens: how the `nc` terminal client and scripts sign in as you (GET/POST /tokens,
   POST /tokens/{id}/revoke). Each is yours alone, acts as you within its scopes and never beyond what you
   hold, and is refused once revoked or expired. An empty scope list means everything you hold except
   `machine:access` — a shell on the API's machine is only carried by a token that names it. The token
   itself is in the answer that made it and nowhere else, ever again. */

export type TokenState = 'active' | 'expired' | 'revoked';

export interface AccessToken {
  id: string;
  name: string;
  /** The first characters, `nc_pat_ab12`, so two tokens can be told apart. */
  prefix: string;
  scopes: string[];
  /** True when `scopes` is empty: everything you hold except machine:access. */
  allScopes: boolean;
  createdAt: string | null;
  lastUsedAt: string | null;
  expiresAt: string | null;
  revokedAt: string | null;
  state: TokenState;
}

export interface TokenPage {
  items: AccessToken[];
  total: number;
  limit: number;
  offset: number;
  nextOffset: number | null;
}

/** A token as it was made: the one time its secret is sent. */
export interface MadeToken extends AccessToken {
  token: string;
}

export interface TokenInput {
  name: string;
  scopes: string[];
  /** Days until it stops working; null: until it is revoked. */
  expiresInDays: number | null;
}

/** The permission a token carries only when it names it. */
export const MACHINE_PERMISSION = 'machine:access';
/** The longest a token may be made to last, as the API allows. */
export const MAX_TOKEN_DAYS = 366;

export const tokensApi = {
  list: (offset = 0, limit = 50) => request<TokenPage>(`/tokens?limit=${limit}&offset=${offset}`),
  create: (body: TokenInput) => request<MadeToken>('/tokens', { method: 'POST', json: body }),
  revoke: (id: string) => request<AccessToken>(`/tokens/${encodeURIComponent(id)}/revoke`, { method: 'POST' }),
};
