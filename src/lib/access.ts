import { useSyncExternalStore } from 'react';
import { ApiError, api, type Catalogue } from '@/lib/api';

/* ═══════════════════════════════════════════════════════════════
   ACCESS — the product's catalogue, as the server keeps it: every
   permission with its label, the roles (built-in and custom) and
   the agent roster by name. GET /auth/catalogue answers anyone
   signed in, because a Viewer is exactly the person who reads
   "your role does not include …". Loaded once per sign-in and
   shared by every screen, so nothing here is written in the app.
   ═══════════════════════════════════════════════════════════════ */

export interface Access {
  /** Null until the first answer; a failed load keeps it null and sets `error`. */
  catalogue: Catalogue | null;
  error: string | null;
}

let state: Access = { catalogue: null, error: null };
let pending: Promise<void> | null = null;
/* Bumped by clearAccess: an answer that left before a sign-out belongs to the person who signed out,
   so it is dropped instead of published for whoever signs in next. */
let generation = 0;
const listeners = new Set<() => void>();

const publish = (next: Access) => {
  state = next;
  listeners.forEach((cb) => cb());
};

/**
 * Reads the catalogue once for this sign-in. Calling it again while it loads, or after it loaded,
 * does nothing; `force` reads it again, e.g. after an admin renamed a role.
 */
export function loadAccess(force = false): Promise<void> {
  if (pending) return pending;
  if (state.catalogue && !force) return Promise.resolve();
  const mine = generation;
  const read: Promise<void> = api.catalogue().then(
    (catalogue) => { if (mine === generation) publish({ catalogue, error: null }); },
    (e: unknown) => {
      if (mine !== generation) return;
      // Nothing is invented in its place: labels fall back to the raw ids, which are at least true.
      console.error('[NeuroCode] GET /auth/catalogue failed:', e);
      publish({ catalogue: null, error: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    },
  ).finally(() => { if (pending === read) pending = null; });
  pending = read;
  return read;
}

/** Forgets the catalogue when nobody is signed in, so the next person reads their own workspace's. */
export function clearAccess() {
  generation += 1;
  pending = null;
  if (state.catalogue || state.error) publish({ catalogue: null, error: null });
}

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
};
const snapshot = () => state;

/** The catalogue, and the lists in it; each list is empty until it has loaded. */
export function useAccess() {
  const { catalogue, error } = useSyncExternalStore(subscribe, snapshot, snapshot);
  return {
    catalogue,
    error,
    permissions: catalogue?.permissions ?? [],
    roles: catalogue?.roles ?? [],
    agents: catalogue?.agents ?? [],
    reload: () => loadAccess(true),
  };
}

/* Lookups that answer with the id itself for anything the catalogue does not hold. */
export const permissionLabel = (catalogue: Catalogue | null, id: string) =>
  catalogue?.permissions.find((p) => p.id === id)?.label ?? id;
export const roleName = (catalogue: Catalogue | null, id: string) =>
  catalogue?.roles.find((r) => r.id === id)?.name ?? id;
/** Runs name an agent by id or by name, so either finds it. */
export const agentName = (catalogue: Catalogue | null, id: string) =>
  catalogue?.agents.find((a) => a.id === id || a.name === id)?.name ?? id;
