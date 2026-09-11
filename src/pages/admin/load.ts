import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, type RoleDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { demoUsers, roles as seedRoles } from '@/mock/rbac';

/* What the Admin screens share: loading from the API (or the demo data), making a change with the
   server's reason shown on failure, and a few formatters. */

/** The built-in roles as the API returns them, with the demo people counted in. */
export const demoRoles: RoleDoc[] = seedRoles.map((r) => ({
  ...r, builtin: true, members: demoUsers.filter((u) => u.roles.includes(r.id)).length,
}));

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
 * One admin screen's data. Signed in, it comes from the API; `load` must be a stable function (a
 * module-level one). In the demo it is the demo data, and the screen is read-only.
 */
export function useAdmin<T>(load: () => Promise<T>, demo: T) {
  const live = useAuth().state === 'signed-in';
  const [data, setData] = useState<T | null>(live ? null : demo);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!live) return;
    let current = true;
    load().then(
      (d) => { if (current) { setData(d); setError(null); } },
      (e: unknown) => { if (current) setError(reason(e)); },
    );
    return () => { current = false; };
  }, [live, load, version]);
  return { data, setData, error, live, reload: () => setVersion((v) => v + 1) };
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

/** A temporary password: 14 characters, none that read alike (no l/1, O/0). */
export function temporaryPassword() {
  const bytes = crypto.getRandomValues(new Uint8Array(14));
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join('');
}
