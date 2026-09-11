import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react';
import { toast } from 'sonner';
import { API_BASE, SIGNED_OUT, api, type AuthUser, type Workspace } from '@/lib/api';
import { permissions as CATALOGUE, roles as ROLES } from '@/mock/rbac';

/* ═══════════════════════════════════════════════════════════════
   WHO IS HERE — the session and what it may do. The API is what
   enforces permissions; the app reads them to hide what a role
   cannot open and to explain what it cannot change. With no API
   (the public demo), the visitor is the demo Owner.
   ═══════════════════════════════════════════════════════════════ */

/** `setup`: the workspace has no Owner yet. `demo`: no API answered, so the seed data opens. */
export type AuthState = 'loading' | 'setup' | 'signed-out' | 'signed-in' | 'demo';

interface AuthCtx {
  state: AuthState;
  user: AuthUser | null;
  workspace: Workspace | null;
  /** True when the user holds every one of the permissions. */
  can: (...perms: string[]) => boolean;
  /** True when the user holds at least one of them. */
  canAny: (...perms: string[]) => boolean;
  /** "Owner", "Engineer, Viewer"… for the account menu. */
  roleNames: string;
  /** Throws an ApiError with the server's reason on a wrong password or a lockout. */
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Re-reads the session, e.g. once the setup wizard has finished. */
  refresh: () => Promise<void>;
}

const C = createContext<AuthCtx | null>(null);

const DEMO_USER: AuthUser = {
  id: 'u_rajat', email: 'rajat@neurocode.local', name: 'Rajat', status: 'active', roles: ['owner'],
  permissions: CATALOGUE.map((p) => p.id),
};
const DEMO_WORKSPACE: Workspace = { name: 'NeuroCode demo' };

export const roleName = (id: string) => ROLES.find((r) => r.id === id)?.name ?? id;

interface Session { state: AuthState; user: AuthUser | null; workspace: Workspace | null }

/** What the server says about this browser's session. No API answering means the demo. */
async function readSession(): Promise<Session> {
  try {
    const s = await api.authStatus();
    return { state: s.needsSetup ? 'setup' : s.user ? 'signed-in' : 'signed-out', user: s.user, workspace: s.workspace };
  } catch {
    console.info(`[NeuroCode] no local API at ${API_BASE}. Running on seed data; npm run dev:start starts it.`);
    return { state: 'demo', user: DEMO_USER, workspace: DEMO_WORKSPACE };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(API_BASE ? 'loading' : 'demo');
  const [user, setUser] = useState<AuthUser | null>(API_BASE ? null : DEMO_USER);
  const [workspace, setWorkspace] = useState<Workspace | null>(API_BASE ? null : DEMO_WORKSPACE);
  const current = useRef(state);
  useEffect(() => { current.current = state; }, [state]);

  const apply = useCallback((s: Session) => {
    setUser(s.user);
    setWorkspace(s.workspace);
    setState(s.state);
  }, []);
  const refresh = useCallback(async () => { if (API_BASE) apply(await readSession()); }, [apply]);

  useEffect(() => {
    if (!API_BASE) return;
    let current = true;
    void readSession().then((s) => { if (current) apply(s); });
    return () => { current = false; };
  }, [apply]);

  // A request answered 401: the session expired, was ended elsewhere, or the account was disabled.
  useEffect(() => {
    const ended = () => {
      if (current.current !== 'signed-in') return;  // many requests can fail at once; say it once
      current.current = 'signed-out';
      setUser(null);
      setState('signed-out');
      toast('You were signed out', { description: 'Your session ended. Sign in again to carry on.' });
    };
    window.addEventListener(SIGNED_OUT, ended);
    return () => window.removeEventListener(SIGNED_OUT, ended);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const r = await api.login(email, password);
    setUser(r.user);
    setWorkspace(r.workspace);
    setState('signed-in');
  }, []);

  const logout = useCallback(async () => {
    if (current.current !== 'signed-in') return;
    try { await api.logout(); } catch { /* the session is dropped here either way */ }
    setUser(null);
    setState('signed-out');
  }, []);

  const perms = useMemo(() => new Set(user?.permissions ?? []), [user]);
  const can = useCallback((...p: string[]) => p.every((x) => perms.has(x)), [perms]);
  const canAny = useCallback((...p: string[]) => p.some((x) => perms.has(x)), [perms]);
  const roleNames = useMemo(() => (user?.roles ?? []).map(roleName).join(', '), [user]);

  const value = useMemo<AuthCtx>(
    () => ({ state, user, workspace, can, canAny, roleNames, login, logout, refresh }),
    [state, user, workspace, can, canAny, roleNames, login, logout, refresh],
  );
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useAuth() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
