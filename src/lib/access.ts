import { useSyncExternalStore } from 'react';
import { ApiError, api, type Catalogue, type PermissionDef } from '@/lib/api';
import { NAV, NAV_SECTIONS, type NavSection } from '@/lib/nav';

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

/* ═══════════════════════════════════════════════════════════════
   WHERE A RIGHT LIVES — every permission names the module and
   sub-module the sidebar shows it under, and a verb. That filing
   is the server's (server/app/data/catalogue.json); this is only
   how the Roles matrix reads it back, in the sidebar's own order,
   so the map a person navigates and the grid an admin grants in
   are provably the same shape.
   ═══════════════════════════════════════════════════════════════ */

/** What holding a right lets someone do. The four columns of the matrix. */
export const VERBS = ['use', 'write', 'decide', 'admin'] as const;
export type Verb = (typeof VERBS)[number];
export const VERB_LABELS: Record<Verb, string> = {
  use: 'Use', write: 'Write', decide: 'Decide', admin: 'Admin',
};

/** A permission with its filing. `module` is a NavSection; `sub` is one of that section's own. */
export interface FiledPermission extends PermissionDef {
  module: NavSection;
  sub: string | null;
  verb: Verb;
}

const isVerb = (v: unknown): v is Verb => VERBS.includes(v as Verb);
const isSection = (v: unknown): v is NavSection => NAV_SECTIONS.includes(v as NavSection);

/**
 * The catalogue's permissions with their filing read off. An older API sends only `group`, so the
 * module falls back to it and the verb to `use` — a right filed roughly is still a right you can
 * find, and nothing here is invented that the server did not send.
 */
export function filedPermissions(permissions: PermissionDef[]): FiledPermission[] {
  return permissions.map((p) => {
    const raw = p as PermissionDef & Partial<Pick<FiledPermission, 'module' | 'sub' | 'verb'>>;
    const module = isSection(raw.module) ? raw.module : isSection(p.group) ? p.group : 'Admin';
    return { ...p, module, sub: raw.sub ?? null, verb: isVerb(raw.verb) ? raw.verb : 'use' };
  });
}

/** One row of the matrix: a module, or one sub-module of it. */
export interface ModuleRow {
  key: string;
  module: NavSection;
  sub: string | null;
  /** "Build" or "Build → Execution": what the sidebar calls this place. */
  label: string;
}

/**
 * The rows, in sidebar order: each section, its own rights first, then each sub-section in the order
 * the sidebar lists it. A module with no rights at all has no row — there would be nothing in it.
 */
export function moduleRows(permissions: FiledPermission[]): ModuleRow[] {
  const rows: ModuleRow[] = [];
  for (const module of NAV_SECTIONS) {
    const here = permissions.filter((p) => p.module === module);
    if (here.some((p) => !p.sub)) rows.push({ key: module, module, sub: null, label: module });
    const order = [...new Set(NAV.filter((i) => i.section === module && i.sub).map((i) => i.sub as string))];
    const extra = [...new Set(here.map((p) => p.sub).filter((s): s is string => !!s))];
    for (const sub of [...order.filter((s) => extra.includes(s)), ...extra.filter((s) => !order.includes(s))]) {
      rows.push({ key: `${module}/${sub}`, module, sub, label: `${module} → ${sub}` });
    }
  }
  return rows;
}

/** The rights in one cell: this row, this verb. Empty means the cell draws an em dash, not a box. */
export const cellOf = (permissions: FiledPermission[], row: ModuleRow, verb: Verb) =>
  permissions.filter((p) => p.module === row.module && (p.sub ?? null) === row.sub && p.verb === verb);
