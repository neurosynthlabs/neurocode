import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { toast } from 'sonner';
import {
  ApiError,
  api,
  type AskAnswer,
  type BrainstormDoc,
  type Change,
  type ChatEvent,
  type Collection,
  type DecisionDoc,
  type Extracted,
  type FactCandidate,
  type Health,
  type Loaded,
  type McpInput,
  type MergeResult,
  type Pref,
  type ProjectInput,
  type ResetResult,
  type RunDoc,
  type RunLogEvent,
  unreachable,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { addEvent } from '@/lib/feed';
import { permissionLabel, useAccess } from '@/lib/access';
import { memoryApi, type ConflictInput } from '@/lib/live/knowledge';
import { LAUNCH_PERMISSION, mcpApi } from '@/lib/live/mcp';
import type {
  ActivityEvent,
  ApprovalRequest,
  McpServer,
  MemoryConflict,
  MemoryFact,
  Plan,
  Project,
  Task,
  TaskStatus,
} from '@/types';

/* ═══════════════════════════════════════════════════════════════
   DATA — the one place screens read the operator's mutable state.
   It loads the workspace from the API, which keeps it in Postgres,
   and subscribes to the stream: log lines, and every document that
   changes, so all open tabs stay in step. Toggles are optimistic
   and roll back when the server refuses; actions that create
   documents wait for the server. Every action first checks the
   signed-in role, so a Viewer is told what is missing instead of
   watching a change bounce back. Nothing here is ever made up in
   the tab: what the server has not said, the store does not hold.
   ═══════════════════════════════════════════════════════════════ */

/** `connecting`: the first load has not settled. `offline`: the API could not be reached, so nothing is shown. */
export type DataMode = 'connecting' | 'live' | 'offline';
type Decision = 'approve' | 'deny';
type Entry = Pick<ActivityEvent, 'action' | 'detail' | 'projectId' | 'level' | 'taskRef'>;

interface Domain {
  approvals: ApprovalRequest[];
  tasks: Task[];
  memory: MemoryFact[];
  projects: Project[];
  plans: Plan[];
  mcp: McpServer[];
  conflicts: MemoryConflict[];
  /** Screen settings that must survive a reload (see usePref). */
  prefs: Pref[];
  /** Final decisions made outside the approvals inbox (see useDecision). */
  decisions: DecisionDoc[];
  /** Briefs made by Brainstorm, newest first. */
  brainstorms: BrainstormDoc[];
  /** Agent runs, newest first: each one a git worktree of its own. */
  runs: RunDoc[];
  /** Newest first, the order every feed renders in. */
  activity: ActivityEvent[];
}

/** Per collection: true when its load stopped at the server's ceiling, so older rows may exist that the store does not hold. */
export type Capped = Readonly<Record<keyof Domain, boolean>>;

/** What the store holds: the workspace itself, and how the connection to it is doing. */
export interface DataState extends Domain {
  /** `live` once the workspace has loaded; `offline` when the API could not be reached. */
  mode: DataMode;
  /**
   * While `live`: the change stream dropped and has not caught up yet. The screens keep working on what
   * they hold, but it may be stale until the stream is back and the workspace has been loaded again.
   */
  reconnecting: boolean;
  /** Which collections reached the server's ceiling. A screen that counts one says "at least" rather than a total. */
  capped: Capped;
  /** What went wrong reaching the API, in words, while `mode` is `offline`. */
  offlineReason: string | null;
  health: Health | null;
}

/**
 * What can be done to it. Every one of these keeps the same identity for the life of the provider, which
 * is why they are handed out on their own context: a dialog or a button that only acts has nothing to
 * re-render for when a run writes a log line somewhere else.
 */
export interface DataActions {
  /** Loads the workspace again from the start: the retry on the not-connected screen. */
  reconnect: () => void;
  decide: (ref: string, decision: Decision) => Promise<boolean>;
  moveTask: (ref: string, status: TaskStatus) => Promise<boolean>;
  toggleCheck: (ref: string, itemId: string) => Promise<boolean>;
  setPinned: (ref: string, pinned: boolean) => Promise<boolean>;
  archive: (ref: string) => Promise<boolean>;
  resolveConflict: (id: string, keep: 'a' | 'b') => Promise<boolean>;
  /** A person marks two facts as contradicting each other; nothing detects it on its own. */
  fileConflict: (input: ConflictInput) => Promise<MemoryConflict | null>;
  createProject: (input: ProjectInput) => Promise<Project | null>;
  registerMcp: (input: McpInput) => Promise<McpServer | null>;
  /** Connects once and records what happened. A stdio server must be trusted first. */
  checkMcp: (id: string) => Promise<McpServer | null>;
  trustMcp: (id: string, trusted: boolean) => Promise<McpServer | null>;
  /** `answer: null` defers the question instead. */
  settleQuestion: (ref: string, index: number, answer: string | null) => Promise<boolean>;
  dispatchPlan: (ref: string) => Promise<boolean>;
  /** Needs a model: with none configured the server refuses, and the toast carries its words. */
  compile: (requirement: string, projectId: string) => Promise<Plan | null>;
  recompile: (ref: string) => Promise<Plan | null>;
  /**
   * The facts the server's full-text search found, ranked best first — whole documents, not refs.
   * Search reads the database, which holds every fact; the store holds only the newest page of them,
   * so a match older than that page has nothing here to be looked up in. Scoping them to a project
   * and a category is the screen's own work.
   */
  searchMemory: (q: string, signal?: AbortSignal) => Promise<MemoryFact[]>;
  /** Empties the workspace (the server takes a backup first). People, roles and keys stay. */
  /** The backup and what was removed, or null when nothing was emptied. */
  reset: () => Promise<ResetResult | null>;
  /** Saves a screen setting. A `detail` also writes an audit line. */
  setPref: (key: string, value: unknown, detail?: string) => Promise<boolean>;
  /** A final decision: once made for a key, it cannot be made again. */
  recordDecision: (key: string, value: string, entry: Omit<Entry, 'taskRef'>) => Promise<boolean>;
  /** Answers from memory: a model's when one is set, otherwise the matching facts, labelled as such. */
  ask: (question: string, projectId?: string) => Promise<AskAnswer | null>;
  /** Needs a model, like compile. */
  brainstorm: (idea: string, projectId?: string) => Promise<BrainstormDoc | null>;
  /** Candidate facts found in pasted text. Nothing is stored until addFacts. */
  extract: (text: string, projectId?: string) => Promise<Extracted | null>;
  /** `projectId: null` files the facts under the workspace. */
  addFacts: (projectId: string | null, facts: FactCandidate[]) => Promise<MemoryFact[] | null>;
  /** Stop a run. Its worktree stays for you to look at. */
  cancelRun: (ref: string) => Promise<RunDoc | null>;
  /** Remove a finished run's worktree and branch. */
  discardRun: (ref: string) => Promise<RunDoc | null>;
  /** Merge an accepted run into the branch the repository has checked out. */
  mergeRun: (ref: string) => Promise<MergeResult | null>;
  /** Each line a run writes, as it writes it. Returns the unsubscribe. */
  onRunLog: (listener: (line: RunLogEvent) => void) => () => void;
  /** Each turn a session writes — your question, a tool call, the answer. Returns the unsubscribe. */
  onChat: (listener: (message: ChatEvent) => void) => () => void;
}

/** What `useData()` hands back: the workspace and everything that can be done to it, as one object. */
export type DataCtx = DataState & DataActions;

const StateC = createContext<DataState | null>(null);
const ActionsC = createContext<DataActions | null>(null);

/** What the store holds before the server has said anything: nothing. */
const EMPTY: Domain = {
  approvals: [],
  tasks: [],
  memory: [],
  projects: [],
  plans: [],
  mcp: [],
  conflicts: [],
  prefs: [],
  decisions: [],
  brainstorms: [],
  runs: [],
  activity: [],
};

const UNCAPPED = Object.fromEntries(Object.keys(EMPTY).map((k) => [k, false])) as unknown as Capped;

/* The inbox must hold every gate still waiting, however old: the newest pages alone left an older
   pending gate out, so it was never shown and could not be decided. Both lists are newest first, and
   a pending gate missing from the newest ones is older than all of them, so it goes after. */
async function approvals(): Promise<Loaded<ApprovalRequest>> {
  const [recent, pending] = await Promise.all([api.approvals(), api.approvals('pending')]);
  const seen = new Set(recent.items.map((a) => a.id));
  return {
    items: [...recent.items, ...pending.items.filter((a) => !seen.has(a.id))],
    capped: recent.capped || pending.capped,
  };
}

/* Every collection the workspace opens with, and where each one comes from — each whole, up to the
   server's ceiling (see `Loaded`). Fetched together but settled apart: one endpoint being down should
   not blank the whole app. A collection that cannot be fetched is the only thing that is empty, and the
   app says which ones. */
const COLLECTIONS: { [K in keyof Domain]: () => Promise<Loaded<Domain[K][number]>> } = {
  approvals,
  tasks: api.tasks,
  memory: api.facts,
  projects: api.projects,
  plans: api.plans,
  mcp: api.mcp,
  conflicts: api.conflicts,
  prefs: api.prefs,
  decisions: api.decisions,
  brainstorms: api.brainstorms,
  runs: api.runs,
  activity: api.activity,
};

type LoadResult = { domain: Domain; capped: Capped; missing: string[] };

async function load(): Promise<LoadResult> {
  const names = Object.keys(COLLECTIONS) as (keyof typeof COLLECTIONS)[];
  const settled = await Promise.allSettled(names.map((name) => COLLECTIONS[name]()));

  // A 401 is not a collection being unavailable — it is nobody being signed in, and the whole load
  // has to fail so the caller shows the sign-in screen rather than an empty workspace.
  const unauthorized = settled.find(
    (r) => r.status === 'rejected' && r.reason instanceof ApiError && r.reason.status === 401,
  );
  if (unauthorized && unauthorized.status === 'rejected') throw unauthorized.reason;

  const missing = names.filter((_, i) => settled[i].status === 'rejected');
  settled.forEach((r, i) => {
    if (r.status === 'rejected') console.error(`[NeuroCode] loading ${names[i]} failed:`, r.reason);
  });
  const domain = Object.fromEntries(
    names.map((name, i) => {
      const result = settled[i];
      return [name, result.status === 'fulfilled' ? result.value.items : []];
    }),
  ) as unknown as Domain;
  const capped = Object.fromEntries(
    names.map((name, i) => {
      const result = settled[i];
      return [name, result.status === 'fulfilled' && result.value.capped];
    }),
  ) as unknown as Capped;
  return { domain, capped, missing };
}

/* Two collections keep rows the store does not hold. A resolved conflict and an archived fact stay in
   the database as evidence, so a change to one still streams as a put — and applied as a put, a
   conflict someone had just resolved came back as open, with its Keep buttons and a sidebar alert, in
   every tab. The store holds what its GET answers (open conflicts, live facts), so such a put is a drop. */
function leaves(c: Change): boolean {
  if (c.op !== 'put') return false;
  if (c.collection === 'conflicts') return (c.doc as Partial<MemoryConflict>).status !== 'open';
  if (c.collection === 'memory') return (c.doc as Partial<MemoryFact>).archived === true;
  return false;
}

/** One document into or out of a collection, as the server streams it or an action's answer returns it. */
function applyChange(d: Domain, c: Change): Domain {
  const list = d[c.collection] as { id: string }[];
  const next =
    c.op === 'drop' || leaves(c)
      ? list.filter((x) => x.id !== (c.op === 'drop' ? c.id : c.doc.id))
      : list.some((x) => x.id === c.doc.id)
        ? list.map((x) => (x.id === c.doc.id ? c.doc : x))
        : [c.doc, ...list];
  return { ...d, [c.collection]: next } as Domain;
}

/** A plan that has started: dispatched, or with a step already past `todo`. */
export const inFlight = (p: Plan) => p.status === 'dispatched' || p.steps.some((s) => s.state !== 'todo');

/** An activity line into the feed, once and under its ceiling (see `@/lib/feed`). */
const withEvent = (d: Domain, ev: ActivityEvent): Domain => {
  const activity = addEvent(d.activity, ev);
  return activity === d.activity ? d : { ...d, activity };
};

const patch = <T extends { ref: string }>(list: T[], ref: string, fields: Partial<T>): T[] =>
  list.map((x) => (x.ref === ref ? { ...x, ...fields } : x));
class DatabaseDown extends Error {}
const reason = (e: unknown) =>
  e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?';

export function DataProvider({ children }: { children: ReactNode }) {
  const { user, can, roleNames } = useAuth();
  const { catalogue } = useAccess();
  const [domain, setDomain] = useState<Domain>(EMPTY);
  const [mode, setMode] = useState<DataMode>('connecting');
  const [capped, setCapped] = useState<Capped>(UNCAPPED);
  const [reconnecting, setReconnecting] = useState(false);
  const [offlineReason, setOfflineReason] = useState<string | null>(null);
  const [tries, setTries] = useState(0);
  const [health, setHealth] = useState<Health | null>(null);
  // Actions read the latest state through refs. A layout effect syncs them before the browser can
  // deliver the next click, so two quick clicks never act on the same stale snapshot.
  const now = useRef(domain);
  const listeners = useRef(new Set<(line: RunLogEvent) => void>());
  const chatters = useRef(new Set<(message: ChatEvent) => void>());
  const who = useRef({ can, roleNames, catalogue, name: user?.name ?? 'You' });
  useLayoutEffect(() => {
    now.current = domain;
  }, [domain]);
  useLayoutEffect(() => {
    who.current = { can, roleNames, catalogue, name: user?.name ?? 'You' };
  }, [can, roleNames, catalogue, user]);

  // The provider only mounts for someone signed in, so this runs once per person, and again on a retry.
  useEffect(() => {
    let cancelled = false;
    let unsubscribe: (() => void) | undefined;
    (async () => {
      /* Subscribed before the first load, so nothing that changes while the workspace loads is missed:
         what arrives in the meantime waits here and is applied over the loaded workspace, in order. */
      let early: { changes: Change[]; activity: ActivityEvent[] } | null = { changes: [], activity: [] };
      /* Loads the workspace again after the stream missed something. One at a time: a reload asked
         for while one is running runs once more after it, so the last one always sees the latest. */
      let reloading = false;
      let again = false;
      const resync = async () => {
        if (reloading) {
          again = true;
          return;
        }
        reloading = true;
        try {
          do {
            again = false;
            const [h2, fresh] = await Promise.all([api.health(), load()]);
            if (cancelled) return;
            if (!h2.ok) throw new DatabaseDown(`The database (${h2.db}) is not answering.`);
            setDomain(fresh.domain);
            setCapped(fresh.capped);
            setHealth(h2);
          } while (again);
          setReconnecting(false);
        } catch (e) {
          if (cancelled || (e instanceof ApiError && e.status === 401)) return;
          // Back but unable to catch up: the full retry says what is wrong, rather than stale screens saying nothing.
          console.error('[NeuroCode] reloading after the stream came back failed:', e);
          reconnectRef.current();
        } finally {
          reloading = false;
        }
      };
      unsubscribe = api.stream({
        activity: (ev) => {
          if (early) early.activity.push(ev);
          else setDomain((d) => withEvent(d, ev));
        },
        change: (c) => {
          if (early) early.changes.push(c);
          else setDomain((d) => applyChange(d, c));
        },
        log: (line) => listeners.current.forEach((cb) => cb(line)),
        chat: (message) => chatters.current.forEach((cb) => cb(message)),
        state: (s) => {
          if (!cancelled && s === 'reconnecting') setReconnecting(true);
        },
        // Cleared by the reload, not by the connection opening: until then the screens may still be stale.
        resync: () => void resync(),
      });
      try {
        const [h, loaded] = await Promise.all([api.health(), load()]);
        if (cancelled) return;
        // The API answered but cannot reach its database: nothing it said about the workspace is true.
        if (!h.ok)
          throw new DatabaseDown(
            `The local API is up, but its database (${h.db}) is not answering. Is Postgres running?`,
          );
        const caught = early;
        early = null;
        setDomain(caught.changes.reduce(applyChange, caught.activity.reduce(withEvent, loaded.domain)));
        setCapped(loaded.capped);
        setHealth(h);
        setOfflineReason(null);
        setReconnecting(false);
        setMode('live');
        if (loaded.missing.length) {
          toast.warning('Some of the workspace did not load', {
            description: `${loaded.missing.join(', ')} came back empty. Everything else is live.`,
          });
        }
      } catch (e) {
        if (cancelled) return;
        // Nothing loaded, so there is nothing for the stream to keep current; the retry subscribes again.
        unsubscribe?.();
        unsubscribe = undefined;
        // A 401 has already signed this person out (see SIGNED_OUT), which unmounts the provider.
        if (e instanceof ApiError && e.status === 401) return;
        console.error('[NeuroCode] the workspace did not load:', e);
        setDomain(EMPTY);
        setCapped(UNCAPPED);
        setReconnecting(false);
        setHealth(null);
        setOfflineReason(e instanceof DatabaseDown ? e.message : unreachable(e, '/health'));
        setMode('offline');
      }
    })();
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [tries]);

  const reconnect = useCallback(() => {
    setMode('connecting');
    setTries((n) => n + 1);
  }, []);
  // The load effect is declared first and reaches `reconnect` through this, so it stays a one-time effect.
  const reconnectRef = useRef(reconnect);

  /** The API refuses what a role cannot do; saying so here first spares the round trip and the flicker. */
  const permitted = useCallback((perm: string) => {
    if (who.current.can(perm)) return true;
    toast.error('Your role cannot do that', {
      description: `${who.current.roleNames || 'This account'} does not include “${permissionLabel(who.current.catalogue, perm)}”. An Owner or Admin can grant it in Admin → Roles & permissions.`,
    });
    return false;
  }, []);

  const put = useCallback(
    <T extends { id: string }>(collection: Collection, doc: T) =>
      setDomain((d) => applyChange(d, { op: 'put', collection, doc })),
    [],
  );
  const drop = useCallback(
    (collection: Collection, id: string) => setDomain((d) => applyChange(d, { op: 'drop', collection, id })),
    [],
  );

  /** For toggles: the change is already on screen. Persist it; the server streams the activity line back. */
  const commit = useCallback(async <T,>(call: () => Promise<T>, revert: () => void): Promise<T | null> => {
    try {
      return await call();
    } catch (e) {
      revert();
      toast.error('Not saved', { description: reason(e) });
      return null;
    }
  }, []);

  /** For actions that create or reshape documents: nothing changes on screen until the server answers. */
  const attempt = useCallback(async <T,>(call: () => Promise<T>, failure: string): Promise<T | null> => {
    try {
      return await call();
    } catch (e) {
      toast.error(failure, { description: reason(e) });
      return null;
    }
  }, []);

  /* A feature only a model can do. With no lane able to answer, the server refuses (409) with a sentence
     that already says what to do, so that sentence is the whole message; a provider's own failure (502)
     arrives with its reason, under the feature's name. */
  const needsModel = useCallback(async <T,>(call: () => Promise<T>, failure: string): Promise<T | null> => {
    try {
      return await call();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) toast.error(e.message);
      else toast.error(failure, { description: reason(e) });
      return null;
    }
  }, []);

  const decide = useCallback(
    async (ref: string, decision: Decision) => {
      const a = now.current.approvals.find((x) => x.ref === ref);
      if (!a || a.status !== 'pending' || !permitted('approvals:decide')) return false;
      const set = (status: ApprovalRequest['status']) =>
        setDomain((d) => ({
          ...d,
          approvals: patch(d.approvals, ref, { status }),
        }));
      set(decision === 'approve' ? 'approved' : 'denied');
      const doc = await commit(
        () => api.decide(ref, decision),
        () => set('pending'),
      );
      if (doc) put('approvals', doc);
      return !!doc;
    },
    [commit, put, permitted],
  );

  const moveTask = useCallback(
    async (ref: string, to: TaskStatus) => {
      const t = now.current.tasks.find((x) => x.ref === ref);
      if (!t || t.status === to || !permitted('tasks:write')) return false;
      const set = (status: TaskStatus) => setDomain((d) => ({ ...d, tasks: patch(d.tasks, ref, { status }) }));
      set(to);
      // The answer carries the time the server recorded the move, so the card shows that, not a guess.
      const doc = await commit(
        () => api.moveTask(ref, to),
        () => set(t.status),
      );
      if (doc) put('tasks', doc);
      return !!doc;
    },
    [commit, put, permitted],
  );

  const toggleCheck = useCallback(
    async (ref: string, itemId: string) => {
      const t = now.current.tasks.find((x) => x.ref === ref);
      const item = t?.checklist.find((c) => c.id === itemId);
      if (!t || !item || !permitted('tasks:write')) return false;
      const done = !item.done;
      const set = (v: boolean) =>
        setDomain((d) => ({
          ...d,
          tasks: d.tasks.map((x) =>
            x.ref === ref
              ? {
                  ...x,
                  checklist: x.checklist.map((c) => (c.id === itemId ? { ...c, done: v } : c)),
                }
              : x,
          ),
        }));
      set(done);
      const doc = await commit(
        () => api.check(ref, itemId, done),
        () => set(!done),
      );
      if (doc) put('tasks', doc);
      return !!doc;
    },
    [commit, put, permitted],
  );

  const setPinned = useCallback(
    async (ref: string, pinned: boolean) => {
      const f = now.current.memory.find((x) => x.ref === ref);
      if (!f || f.pinned === pinned || !permitted('memory:write')) return false;
      const set = (v: boolean) =>
        setDomain((d) => ({
          ...d,
          memory: patch(d.memory, ref, { pinned: v }),
        }));
      set(pinned);
      return !!(await commit(
        () => api.pin(ref, pinned),
        () => set(!pinned),
      ));
    },
    [commit, permitted],
  );

  const archive = useCallback(
    async (ref: string) => {
      const at = now.current.memory.findIndex((x) => x.ref === ref);
      if (at < 0 || !permitted('memory:write')) return false;
      const f = now.current.memory[at];
      setDomain((d) => ({
        ...d,
        memory: d.memory.filter((x) => x.ref !== ref),
      }));
      const restore = () =>
        setDomain((d) => ({
          ...d,
          memory: [...d.memory.slice(0, at), f, ...d.memory.slice(at)],
        }));
      return !!(await commit(() => api.archive(ref), restore));
    },
    [commit, permitted],
  );

  const resolveConflict = useCallback(
    async (id: string, keep: 'a' | 'b') => {
      const c = now.current.conflicts.find((x) => x.id === id);
      if (!c || !permitted('memory:write')) return false;
      // The answer is only {id, status, resolution}; the stream's put of the resolved conflict is dropped too (see `leaves`).
      if (!(await attempt(() => api.resolveConflict(id, keep), 'Conflict not resolved'))) return false;
      drop('conflicts', id);
      // Settling on one fact archives the other on the server.
      drop('memory', keep === 'b' ? c.a : c.b);
      return true;
    },
    [attempt, drop, permitted],
  );

  const fileConflict = useCallback(
    async (input: ConflictInput) => {
      if (!permitted('memory:write')) return null;
      const doc = await attempt(() => memoryApi.fileConflict(input), 'Contradiction not recorded');
      if (doc) put('conflicts', doc);
      return doc;
    },
    [attempt, put, permitted],
  );

  const createProject = useCallback(
    async (input: ProjectInput) => {
      if (!permitted('projects:onboard')) return null;
      const doc = await attempt(() => api.createProject(input), 'Onboarding did not start');
      if (doc) put('projects', doc);
      return doc;
    },
    [attempt, put, permitted],
  );

  const registerMcp = useCallback(
    async (input: McpInput) => {
      if (!permitted('mcp:manage')) return null;
      const doc = await attempt(() => api.registerMcp(input), 'Server not registered');
      if (doc) put('mcp', doc);
      return doc;
    },
    [attempt, put, permitted],
  );

  const checkMcp = useCallback(
    async (id: string) => {
      const s = now.current.mcp.find((x) => x.id === id);
      if (!s || !permitted('mcp:manage') || (s.transport === 'stdio' && !permitted(LAUNCH_PERMISSION))) return null;
      const doc = await attempt(() => mcpApi.check(id), 'Server not checked');
      if (doc) put('mcp', doc);
      return doc;
    },
    [attempt, put, permitted],
  );

  const trustMcp = useCallback(
    async (id: string, trusted: boolean) => {
      if (!permitted('mcp:manage') || !permitted(LAUNCH_PERMISSION)) return null;
      const doc = await attempt(() => mcpApi.trust(id, trusted), trusted ? 'Server not trusted' : 'Trust not withdrawn');
      if (doc) put('mcp', doc);
      return doc;
    },
    [attempt, put, permitted],
  );

  const settleQuestion = useCallback(
    async (ref: string, index: number, answer: string | null) => {
      const p = now.current.plans.find((x) => x.ref === ref);
      if (!p || p.openQuestions[index] === undefined || !permitted('plans:decide')) return false;
      // the server also writes the answer into memory and streams that fact back
      const doc = await attempt(
        () => api.settle(ref, index, answer === null ? { defer: true } : { answer }),
        'Not saved',
      );
      if (doc) put('plans', doc);
      return !!doc;
    },
    [attempt, put, permitted],
  );

  const dispatchPlan = useCallback(
    async (ref: string) => {
      const p = now.current.plans.find((x) => x.ref === ref);
      if (!p || !permitted('plans:decide')) return false;
      const doc = await attempt(() => api.dispatch(ref), 'Not dispatched');
      if (doc) put('plans', doc);
      return !!doc;
    },
    [attempt, put, permitted],
  );

  const compile = useCallback(
    async (requirement: string, projectId: string) => {
      if (!permitted('plans:compile')) return null;
      const doc = await needsModel(() => api.compile(requirement, projectId), 'The compiler did not answer');
      if (doc) put('plans', doc);
      return doc;
    },
    [needsModel, put, permitted],
  );

  const recompile = useCallback(
    async (ref: string) => {
      if (!permitted('plans:decide')) return null;
      const doc = await needsModel(() => api.recompile(ref), 'Not re-compiled');
      if (doc) put('plans', doc);
      return doc;
    },
    [needsModel, put, permitted],
  );

  const setPref = useCallback(
    async (key: string, value: unknown, detail?: string) => {
      if (!permitted('settings:write')) return false;
      const before = now.current.prefs.find((p) => p.id === key);
      put('prefs', { id: key, value });
      try {
        await api.setPref(key, value, detail);
        return true;
      } catch (e) {
        if (before) put('prefs', before);
        else drop('prefs', key);
        toast.error('Setting not saved', { description: reason(e) });
        return false;
      }
    },
    [put, drop, permitted],
  );

  const recordDecision = useCallback(
    async (key: string, value: string, entry: Omit<Entry, 'taskRef'>) => {
      if (now.current.decisions.some((d) => d.id === key) || !permitted('decisions:make')) return false;
      put('decisions', {
        id: key,
        value,
        decidedAt: new Date().toISOString(),
        decidedBy: who.current.name,
      });
      const doc = await commit(
        () => api.recordDecision(key, { value, ...entry }),
        () => drop('decisions', key),
      );
      // The server's copy has the real time and the real name; the placeholder above only held the click.
      if (doc) put('decisions', doc);
      return !!doc;
    },
    [put, drop, commit, permitted],
  );

  const searchMemory = useCallback((q: string, signal?: AbortSignal) => api.memory(q, signal), []);

  const ask = useCallback(
    async (question: string, projectId?: string) => {
      if (!permitted('ai:use')) return null;
      return attempt(() => api.ask(question, projectId), 'Memory did not answer');
    },
    [attempt, permitted],
  );

  const brainstorm = useCallback(
    async (idea: string, projectId?: string) => {
      if (!permitted('ai:use')) return null;
      const doc = await needsModel(() => api.brainstorm(idea, projectId), 'The brainstorm did not run');
      if (doc) put('brainstorms', doc);
      return doc;
    },
    [needsModel, put, permitted],
  );

  const extract = useCallback(
    async (text: string, projectId?: string) => {
      if (!permitted('ai:use')) return null;
      return attempt(() => api.extract(text, projectId), 'No facts were extracted');
    },
    [attempt, permitted],
  );

  const addFacts = useCallback(
    async (projectId: string | null, facts: FactCandidate[]) => {
      if (!facts.length || !permitted('memory:write')) return null;
      const docs = await attempt(() => api.addFacts(projectId, facts), 'Facts not added');
      docs?.forEach((d) => put('memory', d));
      return docs;
    },
    [attempt, put, permitted],
  );

  const cancelRun = useCallback(
    async (ref: string) => {
      if (!permitted('runs:run')) return null;
      const doc = await attempt(() => api.cancelRun(ref), 'The run did not stop');
      if (doc) put('runs', doc);
      return doc;
    },
    [attempt, put, permitted],
  );

  const discardRun = useCallback(
    async (ref: string) => {
      if (!permitted('runs:run')) return null;
      const doc = await attempt(() => api.discardRun(ref), 'The worktree was not removed');
      if (doc) put('runs', doc);
      return doc;
    },
    [attempt, put, permitted],
  );

  const mergeRun = useCallback(
    async (ref: string) => {
      if (!permitted('runs:merge')) return null;
      const result = await attempt(() => api.mergeRun(ref), 'Nothing was merged');
      if (result) put('runs', result.run);
      return result;
    },
    [attempt, put, permitted],
  );

  const onRunLog = useCallback((listener: (line: RunLogEvent) => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  const onChat = useCallback((listener: (message: ChatEvent) => void) => {
    chatters.current.add(listener);
    return () => {
      chatters.current.delete(listener);
    };
  }, []);

  const reset = useCallback(async () => {
    if (!permitted('workspace:admin')) return null;
    let done: ResetResult;
    try {
      done = await api.reset();
    } catch (e) {
      toast.error('The workspace was not emptied', { description: reason(e) });
      return null;
    }
    // Emptied is emptied: a reload that fails afterwards is its own problem, and says so.
    try {
      const [h, loaded] = await Promise.all([api.health(), load()]);
      setDomain(loaded.domain);
      setCapped(loaded.capped);
      setHealth(h);
    } catch (e) {
      toast.warning('Emptied, but the screens did not reload', {
        description: reason(e),
      });
      reconnect();
    }
    return done;
  }, [permitted, reconnect]);

  /* Two contexts, not one. The workspace changes on every streamed line, and one context carrying both
     the workspace and the thirty actions on it re-rendered every reader of either — the sidebar, the top
     bar, the palette and whatever screen was open — for a fact written on a screen nobody had open. The
     actions never change, so a component that only acts sits on a value that never moves. */
  const state = useMemo<DataState>(
    () => ({ ...domain, mode, reconnecting, capped, offlineReason, health }),
    [domain, mode, reconnecting, capped, offlineReason, health],
  );

  const actions = useMemo<DataActions>(
    () => ({
      reconnect,
      decide,
      moveTask,
      toggleCheck,
      setPinned,
      archive,
      resolveConflict,
      fileConflict,
      createProject,
      registerMcp,
      checkMcp,
      trustMcp,
      settleQuestion,
      dispatchPlan,
      compile,
      recompile,
      searchMemory,
      reset,
      setPref,
      recordDecision,
      ask,
      brainstorm,
      extract,
      addFacts,
      cancelRun,
      discardRun,
      mergeRun,
      onRunLog,
      onChat,
    }),
    [
      reconnect,
      decide,
      moveTask,
      toggleCheck,
      setPinned,
      archive,
      resolveConflict,
      fileConflict,
      createProject,
      registerMcp,
      checkMcp,
      trustMcp,
      settleQuestion,
      dispatchPlan,
      compile,
      recompile,
      searchMemory,
      reset,
      setPref,
      recordDecision,
      ask,
      brainstorm,
      extract,
      addFacts,
      cancelRun,
      discardRun,
      mergeRun,
      onRunLog,
      onChat,
    ],
  );

  return (
    <ActionsC.Provider value={actions}>
      <StateC.Provider value={state}>{children}</StateC.Provider>
    </ActionsC.Provider>
  );
}

/** The workspace and every action on it. A component that reads none of the workspace should use
    `useDataActions` instead, so a change on another screen does not re-render it. */
export function useData(): DataCtx {
  const state = useContext(StateC);
  const actions = useDataActions();
  if (!state) throw new Error('useData must be used inside <DataProvider>');
  return useMemo(() => ({ ...state, ...actions }), [state, actions]);
}

/** Only what can be done to the workspace. This value never changes, so reading it costs no renders. */
export function useDataActions(): DataActions {
  const actions = useContext(ActionsC);
  if (!actions) throw new Error('useDataActions must be used inside <DataProvider>');
  return actions;
}

const isMap = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

/**
 * Screen state that should survive a reload and leave an audit line: a skill switched off, a model
 * disabled. Saved by the API. Pass a module-level `initial`: it stands until a value is saved. Maps
 * merge over it, so a key added to `initial` later still shows up beside the saved ones.
 */
export function usePref<T>(key: string, initial: T): [T, (next: T, detail?: string) => void] {
  const { prefs, setPref } = useData();
  const stored = prefs.find((p) => p.id === key);
  const value = useMemo(
    () =>
      (stored === undefined
        ? initial
        : isMap(initial) && isMap(stored.value)
          ? { ...initial, ...stored.value }
          : stored.value) as T,
    [stored, initial],
  );
  const set = useCallback(
    (next: T, detail?: string) => {
      void setPref(key, next, detail);
    },
    [key, setPref],
  );
  return [value, set];
}

/** A decision that is final once made, such as a production gate. `undefined` until it is made. */
export function useDecision(
  key: string,
): [string | undefined, (value: string, entry: Omit<Entry, 'taskRef'>) => Promise<boolean>] {
  const { decisions, recordDecision } = useData();
  const decide = useCallback(
    (value: string, entry: Omit<Entry, 'taskRef'>) => recordDecision(key, value, entry),
    [key, recordDecision],
  );
  return [decisions.find((d) => d.id === key)?.value, decide];
}
