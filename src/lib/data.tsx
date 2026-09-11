import {
  createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode,
} from 'react';
import { toast } from 'sonner';
import { API_BASE, ApiError, api, type Health } from '@/lib/api';
import { approvals as seedApprovals } from '@/mock/permissions';
import { tasks as seedTasks } from '@/mock/tasks';
import { memoryFacts as seedMemory } from '@/mock/memory';
import { activity as seedActivity } from '@/mock/activity';
import { activityExtra } from '@/mock/activity-extra';
import type { ActivityEvent, ApprovalRequest, MemoryFact, Task, TaskStatus } from '@/types';

/* ═══════════════════════════════════════════════════════════════
   DATA — the one place screens read the operator's mutable state.
   It paints instantly from the seed, then, if the local API
   answers, swaps in the persisted SQLite state and subscribes to
   the activity stream. Every action is optimistic and works the
   same way in both modes; in demo mode changes live in this tab.
   ═══════════════════════════════════════════════════════════════ */

export type DataMode = 'connecting' | 'live' | 'demo';
type Decision = 'approve' | 'deny';
type Entry = Pick<ActivityEvent, 'action' | 'detail' | 'projectId' | 'level' | 'taskRef'>;

interface Domain {
  approvals: ApprovalRequest[];
  tasks: Task[];
  memory: MemoryFact[];
  /** Newest first, the order every feed renders in. */
  activity: ActivityEvent[];
}

export interface DataCtx extends Domain {
  /** `live`: persisted by the local API. `demo`: seed data, changes last until reload. */
  mode: DataMode;
  health: Health | null;
  decide: (ref: string, decision: Decision) => Promise<boolean>;
  moveTask: (ref: string, status: TaskStatus) => Promise<boolean>;
  toggleCheck: (ref: string, itemId: string) => Promise<boolean>;
  setPinned: (ref: string, pinned: boolean) => Promise<boolean>;
  archive: (ref: string) => Promise<boolean>;
  /** Refs ranked by the server's FTS5 index, best first. Live mode only. */
  searchMemory: (q: string, signal?: AbortSignal) => Promise<string[]>;
  reset: () => Promise<boolean>;
}

const C = createContext<DataCtx | null>(null);

const seed = (): Domain => ({
  approvals: seedApprovals,
  tasks: seedTasks,
  memory: seedMemory,
  activity: [...seedActivity].reverse().concat(activityExtra),
});

async function load(): Promise<Domain> {
  const [approvals, tasks, memory, activity] = await Promise.all([api.approvals(), api.tasks(), api.memory(), api.activity()]);
  return { approvals, tasks, memory, activity };
}

const patch = <T extends { ref: string }>(list: T[], ref: string, fields: Partial<T>): T[] =>
  list.map((x) => (x.ref === ref ? { ...x, ...fields } : x));
const words = (s: TaskStatus) => s.replace('_', ' ');
/** Global facts belong to no project, so their events are filed under NeuroCode itself — as the API does. */
const home = (f: MemoryFact) => (f.projectId === 'global' ? 'aios' : f.projectId);
const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');
const clock = () => new Date().toTimeString().slice(0, 8);
let seq = 0;

export function DataProvider({ children }: { children: ReactNode }) {
  const [domain, setDomain] = useState<Domain>(seed);
  const [mode, setMode] = useState<DataMode>(API_BASE ? 'connecting' : 'demo');
  const [health, setHealth] = useState<Health | null>(null);
  const live = useRef(false);
  // Actions read the latest state through this ref. A layout effect syncs it before the browser can
  // deliver the next click, so two quick clicks never act on the same stale snapshot.
  const now = useRef(domain);
  useLayoutEffect(() => { now.current = domain; }, [domain]);

  useEffect(() => {
    if (!API_BASE) return;
    let cancelled = false;
    let unsubscribe: (() => void) | undefined;
    (async () => {
      try {
        const h = await api.health();
        if (!h.ok) throw new Error('the API reported itself unhealthy');
        const data = await load();
        if (cancelled) return;
        setDomain(data);
        setHealth(h);
        live.current = true;
        setMode('live');
        unsubscribe = api.stream((ev) =>
          setDomain((d) => (d.activity.some((e) => e.id === ev.id) ? d : { ...d, activity: [ev, ...d.activity] })));
      } catch {
        if (cancelled) return;
        setMode('demo');
        console.info(`[NeuroCode] no local API at ${API_BASE}. Running on seed data; npm run dev:start starts it.`);
      }
    })();
    return () => { cancelled = true; unsubscribe?.(); };
  }, []);

  const log = useCallback((e: Entry) => {
    const ev: ActivityEvent = { id: `local-${++seq}`, t: clock(), actor: 'You', actorKind: 'human', ...e };
    setDomain((d) => ({ ...d, activity: [ev, ...d.activity] }));
  }, []);

  /** The change is already on screen. Live: persist it, and the server streams the event back. Demo: log it here. */
  const commit = useCallback(async (call: () => Promise<unknown>, revert: () => void, entry: Entry) => {
    if (!live.current) { log(entry); return true; }
    try {
      await call();
      return true;
    } catch (e) {
      revert();
      toast.error('Not saved', { description: reason(e) });
      return false;
    }
  }, [log]);

  const decide = useCallback(async (ref: string, decision: Decision) => {
    const a = now.current.approvals.find((x) => x.ref === ref);
    if (!a || a.status !== 'pending') return false;
    const set = (status: ApprovalRequest['status']) => setDomain((d) => ({ ...d, approvals: patch(d.approvals, ref, { status }) }));
    set(decision === 'approve' ? 'approved' : 'denied');
    return commit(() => api.decide(ref, decision), () => set('pending'), {
      action: decision === 'approve' ? 'Approved' : 'Denied', detail: `${ref} · ${a.title}`,
      projectId: a.projectId, level: decision === 'approve' ? 'ok' : 'warn',
    });
  }, [commit]);

  const moveTask = useCallback(async (ref: string, to: TaskStatus) => {
    const t = now.current.tasks.find((x) => x.ref === ref);
    if (!t || t.status === to) return false;
    const set = (fields: Partial<Task>) => setDomain((d) => ({ ...d, tasks: patch(d.tasks, ref, fields) }));
    set({ status: to, updatedAt: 'just now' });
    return commit(() => api.moveTask(ref, to), () => set({ status: t.status, updatedAt: t.updatedAt }), {
      action: 'Task moved', detail: `${ref} · ${words(t.status)} → ${words(to)}`, projectId: t.projectId, level: 'info', taskRef: ref,
    });
  }, [commit]);

  const toggleCheck = useCallback(async (ref: string, itemId: string) => {
    const t = now.current.tasks.find((x) => x.ref === ref);
    const item = t?.checklist.find((c) => c.id === itemId);
    if (!t || !item) return false;
    const done = !item.done;
    const set = (v: boolean) => setDomain((d) => ({
      ...d,
      tasks: d.tasks.map((x) => (x.ref === ref ? { ...x, checklist: x.checklist.map((c) => (c.id === itemId ? { ...c, done: v } : c)) } : x)),
    }));
    set(done);
    return commit(() => api.check(ref, itemId, done), () => set(!done), {
      action: 'Checklist updated', detail: `${ref} · ${done ? '✓' : '○'} ${item.label}`,
      projectId: t.projectId, level: done ? 'ok' : 'info', taskRef: ref,
    });
  }, [commit]);

  const setPinned = useCallback(async (ref: string, pinned: boolean) => {
    const f = now.current.memory.find((x) => x.ref === ref);
    if (!f || f.pinned === pinned) return false;
    const set = (v: boolean) => setDomain((d) => ({ ...d, memory: patch(d.memory, ref, { pinned: v }) }));
    set(pinned);
    return commit(() => api.pin(ref, pinned), () => set(!pinned), {
      action: pinned ? 'Memory pinned' : 'Memory unpinned', detail: `${ref} · ${f.title}`, projectId: home(f), level: 'info',
    });
  }, [commit]);

  const archive = useCallback(async (ref: string) => {
    const at = now.current.memory.findIndex((x) => x.ref === ref);
    if (at < 0) return false;
    const f = now.current.memory[at];
    setDomain((d) => ({ ...d, memory: d.memory.filter((x) => x.ref !== ref) }));
    const restore = () => setDomain((d) => ({ ...d, memory: [...d.memory.slice(0, at), f, ...d.memory.slice(at)] }));
    return commit(() => api.archive(ref), restore, {
      action: 'Memory archived', detail: `${ref} · ${f.title} — recoverable, never deleted`, projectId: home(f), level: 'warn',
    });
  }, [commit]);

  const searchMemory = useCallback(
    async (q: string, signal?: AbortSignal) => (await api.memory(q, signal)).map((f) => f.ref),
    [],
  );

  const reset = useCallback(async () => {
    if (!live.current) { setDomain(seed()); return true; }
    try {
      const h = await api.reset();
      setDomain(await load());
      setHealth(h);
      return true;
    } catch (e) {
      toast.error('Reset failed', { description: reason(e) });
      return false;
    }
  }, []);

  const value = useMemo<DataCtx>(
    () => ({ ...domain, mode, health, decide, moveTask, toggleCheck, setPinned, archive, searchMemory, reset }),
    [domain, mode, health, decide, moveTask, toggleCheck, setPinned, archive, searchMemory, reset],
  );
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useData() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useData must be used inside <DataProvider>');
  return ctx;
}
