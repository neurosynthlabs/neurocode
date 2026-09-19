import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react';
import { toast } from 'sonner';
import { SIGNED_OUT, api, unreachable, type AuthUser, type Workspace } from '@/lib/api';
import { clearAccess, loadAccess, roleName, useAccess } from '@/lib/access';

/* ═══════════════════════════════════════════════════════════════
   WHO IS HERE — the session and what it may do. The API is what
   enforces permissions; the app reads them to hide what a role
   cannot open and to explain what it cannot change. When the API
   cannot be reached there is nobody to be: the app says it is not
   connected and offers a retry.
   ═══════════════════════════════════════════════════════════════ */

/** `setup`: the workspace has no Owner yet. `offline`: the API could not be reached, so nothing can be shown. */
export type AuthState = 'loading' | 'offline' | 'setup' | 'signed-out' | 'signed-in';

interface AuthCtx {
  state: AuthState;
  user: AuthUser | null;
  workspace: Workspace | null;
  /** What went wrong reaching the API, in words, while `state` is `offline`. */
  offlineReason: string | null;
  /** True when the user holds every one of the permissions. */
  can: (...perms: string[]) => boolean;
  /** True when the user holds at least one of them. */
  canAny: (...perms: string[]) => boolean;
  /** "Owner", "Engineer, Viewer"… named by the workspace's own catalogue. */
  roleNames: string;
  /** Throws an ApiError with the server's reason on a wrong password or a lockout. */
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Re-reads the session: once the setup wizard has finished, or as the retry when not connected. */
  refresh: () => Promise<void>;
}

const C = createContext<AuthCtx | null>(null);

interface Session { state: AuthState; user: AuthUser | null; workspace: Workspace | null; offlineReason: string | null }


/** What the server says about this browser's session. */
async function readSession(): Promise<Session> {
  try {
    const s = await api.authStatus();
    return {
      state: s.needsSetup ? 'setup' : s.user ? 'signed-in' : 'signed-out',
      user: s.user, workspace: s.workspace, offlineReason: null,
    };
  } catch (e) {
    console.error('[NeuroCode] GET /auth/status failed:', e);
    return { state: 'offline', user: null, workspace: null, offlineReason: unreachable(e, '/auth/status') };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>({ state: 'loading', user: null, workspace: null, offlineReason: null });
  const { state, user, workspace, offlineReason } = session;
  const { catalogue } = useAccess();
  const current = useRef(state);
  useEffect(() => { current.current = state; }, [state]);

  const refresh = useCallback(async () => { setSession(await readSession()); }, []);

  useEffect(() => {
    let live = true;
    void readSession().then((s) => { if (live) setSession(s); });
    return () => { live = false; };
  }, []);

  // The catalogue belongs to whoever is signed in: read it when someone is, forget it when nobody is.
  useEffect(() => {
    if (state === 'signed-in') void loadAccess();
    else if (state !== 'loading') clearAccess();
  }, [state]);

  // A request answered 401: the session expired, was ended elsewhere, or the account was disabled.
  useEffect(() => {
    const ended = () => {
      if (current.current !== 'signed-in') return;  // many requests can fail at once; say it once
      current.current = 'signed-out';
      setSession((s) => ({ ...s, state: 'signed-out', user: null }));
      toast('You were signed out', { description: 'Your session ended. Sign in again to carry on.' });
    };
    window.addEventListener(SIGNED_OUT, ended);
    return () => window.removeEventListener(SIGNED_OUT, ended);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const r = await api.login(email, password);
    setSession({ state: 'signed-in', user: r.user, workspace: r.workspace, offlineReason: null });
  }, []);

  const logout = useCallback(async () => {
    if (current.current !== 'signed-in') return;
    try {
      await api.logout();
    } catch (e) {
      // This tab drops the session either way; the server's copy runs out on its own.
      console.warn('[NeuroCode] sign-out did not reach the API:', e);
    }
    setSession((s) => ({ ...s, state: 'signed-out', user: null }));
  }, []);

  const perms = useMemo(() => new Set(user?.permissions ?? []), [user]);
  const can = useCallback((...p: string[]) => p.every((x) => perms.has(x)), [perms]);
  const canAny = useCallback((...p: string[]) => p.some((x) => perms.has(x)), [perms]);
  const roleNames = useMemo(() => (user?.roles ?? []).map((id) => roleName(catalogue, id)).join(', '), [user, catalogue]);

  const value = useMemo<AuthCtx>(
    () => ({ state, user, workspace, offlineReason, can, canAny, roleNames, login, logout, refresh }),
    [state, user, workspace, offlineReason, can, canAny, roleNames, login, logout, refresh],
  );
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useAuth() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
