import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, type RoleDoc } from '@/lib/api';

/* What the Admin screens share: loading from the API, making a change with the server's reason shown on
   failure, and a few formatters. */

export const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');

/** A change through the API. On failure the server's reason is shown and null comes back. */
export async function attempt<T>(call: () => Promise<T>, failure: string): Promise<T | null> {
  try {
    return await call();
  } catch (e) {
    toast.error(failure, { description: reason(e) });
    return null;
  }
}

/**
 * One admin screen's data, from the API. `load` must be a stable function (a module-level one). The
 * shell only draws these screens for someone signed in, so there is nothing else to show while it loads.
 */
export function useAdmin<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let current = true;
    load().then(
      (d) => { if (current) { setData(d); setError(null); } },
      (e: unknown) => { if (current) setError(reason(e)); },
    );
    return () => { current = false; };
  }, [load, version]);
  return { data, setData, error, reload: () => setVersion((v) => v + 1) };
}

/** The built-in role a new person or a new role starts from: Engineer when the catalogue has it, else the
 *  first built-in role that is not Owner — never a role id the workspace does not hold. */
export function startingRole(roles: RoleDoc[]): string {
  return (roles.find((r) => r.builtin && r.id === 'engineer') ?? roles.find((r) => r.builtin && r.id !== 'owner') ?? roles[0])?.id ?? '';
}

/** "11 Sep, 18:40" — or "never". */
export function when(iso: string | null | undefined) {
  if (!iso) return 'never';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getDate()} ${d.toLocaleString('en', { month: 'short' })}, ${d.toTimeString().slice(0, 5)}`;
}

export const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';

const ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';

/** A temporary password, none of whose characters read alike (no l/1, O/0): 14 long, or the workspace's
 *  minimum when that is longer. */
export function temporaryPassword(minimum = 0) {
  const bytes = crypto.getRandomValues(new Uint8Array(Math.max(14, minimum)));
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join('');
}
