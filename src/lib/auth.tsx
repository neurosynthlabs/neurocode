import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react';
import { toast } from 'sonner';
import { SIGNED_OUT, api, unreachable, type AuthUser, type SsoPublic, type Workspace } from '@/lib/api';
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
  /**
   * The same question, asked inside one project. A project is open to the whole workspace until
   * somebody restricts it, and an open one answers exactly as `can` does — which is every project
   * until someone restricts one. Inside a restricted project a person's rights are their own, cut
   * to what their grant there carries, so this can only ever say no where `can` said yes.
   *
   * The API is still the fence. This only spares the round trip and the flicker.
   */
  canIn: (projectId: string | null | undefined, ...perms: string[]) => boolean;
  /** Whether this person may use the machine the API runs on: the permission, and a server that opens it
   *  at all. A hosted server answers every machine route with 404, so a screen asks this before offering
   *  a folder, a terminal, a run or a debugger. */
  machine: boolean;
  /** "Owner", "Engineer, Viewer"… named by the workspace's own catalogue. */
  roleNames: string;
  /**
   * Single sign-on, as the sign-in screen needs it: whether there is a button, what it says, and
   * whether passwords are off for everyone but an Owner. Never on for an API that has none.
   */
  sso: SsoPublic;
  /** Throws an ApiError with the server's reason on a wrong password or a lockout. */
  login: (email: string, password: string) => Promise<void>;
  /**
   * Leave for the identity provider. The server mints the state and the nonce and sets them as a
   * cookie of its own; this only carries the browser there, so nothing in the page ever holds either.
   */
  signInWithSso: () => Promise<void>;
  logout: () => Promise<void>;
  /** Re-reads the session: once the setup wizard has finished, or as the retry when not connected. */
  refresh: () => Promise<void>;
}

const C = createContext<AuthCtx | null>(null);

interface Session {
  state: AuthState; user: AuthUser | null; workspace: Workspace | null; offlineReason: string | null;
  /** What the server said about opening its own machine; true until it says otherwise, as a local one does. */
  machineAccess: boolean;
  sso: SsoPublic;
}

/** What an API with no single sign-on amounts to: no button, and passwords as they always were. */
const NO_SSO: SsoPublic = { enabled: false, label: 'single sign-on', passwordsOff: false };


/** What the server says about this browser's session. */
async function readSession(): Promise<Session> {
  try {
    const s = await api.authStatus();
    return {
      state: s.needsSetup ? 'setup' : s.user ? 'signed-in' : 'signed-out',
      user: s.user, workspace: s.workspace, offlineReason: null, machineAccess: s.machineAccess !== false,
      sso: s.sso ?? NO_SSO,
    };
  } catch (e) {
    console.error('[NeuroCode] GET /auth/status failed:', e);
    return { state: 'offline', user: null, workspace: null, offlineReason: unreachable(e, '/auth/status'),
             machineAccess: true, sso: NO_SSO };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>({ state: 'loading', user: null, workspace: null,
    offlineReason: null, machineAccess: true, sso: NO_SSO });
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
    setSession((s) => ({ ...s, state: 'signed-in', user: r.user, workspace: r.workspace, offlineReason: null,
                         machineAccess: r.machineAccess !== false }));
  }, []);

  // The server answers with the address to go to; the browser goes there. A failure is thrown on,
  // so the sign-in screen can say what the server said rather than leaving a button that does nothing.
  const signInWithSso = useCallback(async () => {
    const { url } = await api.ssoStart();
    window.location.assign(url);
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
  // Only the restricted projects this person holds a grant in. A project that is not in here is open,
  // and an open project is the workspace set — which is why the absence means "yes, as usual".
  const projectRights = useMemo(() => {
    const held = (user as (AuthUser & { projectRights?: Record<string, string[]> }) | null)?.projectRights;
    return new Map(Object.entries(held ?? {}).map(([id, list]) => [id, new Set(list)]));
  }, [user]);
  const canIn = useCallback((projectId: string | null | undefined, ...p: string[]) => {
    const narrowed = projectId ? projectRights.get(projectId) : undefined;
    return narrowed ? p.every((x) => narrowed.has(x)) : p.every((x) => perms.has(x));
  }, [perms, projectRights]);
  const machine = can('machine:access') && session.machineAccess;
  const roleNames = useMemo(() => (user?.roles ?? []).map((id) => roleName(catalogue, id)).join(', '), [user, catalogue]);

  const value = useMemo<AuthCtx>(
    () => ({ state, user, workspace, offlineReason, can, canAny, canIn, machine, roleNames, sso: session.sso,
             login, signInWithSso, logout, refresh }),
    [state, user, workspace, offlineReason, can, canAny, canIn, machine, roleNames, session.sso, login,
     signInWithSso, logout, refresh],
  );
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useAuth() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
