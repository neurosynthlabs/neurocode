import {
  createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode,
} from 'react';
import { toast } from 'sonner';
import {
  API_BASE, ApiError, api, type Change, type Collection, type Health, type McpInput, type ProjectInput,
} from '@/lib/api';
import { approvals as seedApprovals } from '@/mock/permissions';
import { tasks as seedTasks } from '@/mock/tasks';
import { memoryFacts as seedMemory, memoryConflicts as seedConflicts, type MemoryConflict } from '@/mock/memory';
import { activity as seedActivity } from '@/mock/activity';
import { activityExtra } from '@/mock/activity-extra';
import { projects as seedProjects } from '@/mock/projects';
import { plans as seedPlans } from '@/mock/plans';
import { mcpServers as seedMcp } from '@/mock/mcp';
import type { ActivityEvent, ApprovalRequest, McpServer, MemoryFact, Plan, Project, Task, TaskStatus } from '@/types';

/* ═══════════════════════════════════════════════════════════════
   DATA — the one place screens read the operator's mutable state.
   It paints instantly from the seed, then, if the local API
   answers, swaps in the persisted SQLite state and subscribes to
   the stream: log lines, and every document that changes, so all
   open tabs stay in step. Toggles are optimistic; actions that
   create documents wait for the server. In demo mode every
   action still works, inside this tab.
   ═══════════════════════════════════════════════════════════════ */

export type DataMode = 'connecting' | 'live' | 'demo';
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
  resolveConflict: (id: string, keep: 'a' | 'b' | 'adr') => Promise<boolean>;
  createProject: (input: ProjectInput) => Promise<Project | null>;
  registerMcp: (input: McpInput) => Promise<McpServer | null>;
  /** `answer: null` defers the question instead. */
  settleQuestion: (ref: string, index: number, answer: string | null) => Promise<boolean>;
  dispatchPlan: (ref: string) => Promise<boolean>;
  /** Live only: the compiler runs inside the local API. */
  compile: (requirement: string, projectId: string) => Promise<Plan | null>;
  recompile: (ref: string) => Promise<Plan | null>;
  /** Refs ranked by the server's FTS5 index, best first. Live mode only. */
  searchMemory: (q: string, signal?: AbortSignal) => Promise<string[]>;
  reset: () => Promise<boolean>;
}

const C = createContext<DataCtx | null>(null);

const seed = (): Domain => ({
  approvals: seedApprovals,
  tasks: seedTasks,
  memory: seedMemory,
  projects: seedProjects,
  plans: seedPlans,
  mcp: seedMcp,
  conflicts: seedConflicts,
  activity: [...seedActivity].reverse().concat(activityExtra),
});

async function load(): Promise<Domain> {
  const [approvals, tasks, memory, projects, plans, mcp, conflicts, activity] = await Promise.all([
    api.approvals(), api.tasks(), api.memory(), api.projects(), api.plans(), api.mcp(), api.conflicts(), api.activity(),
  ]);
  return { approvals, tasks, memory, projects, plans, mcp, conflicts, activity };
}

/** One document into or out of a collection. The server streams these; demo actions make their own. */
function applyChange(d: Domain, c: Change): Domain {
  const list = d[c.collection] as { id: string }[];
  const next = c.op === 'drop'
    ? list.filter((x) => x.id !== c.id)
    : list.some((x) => x.id === c.doc.id) ? list.map((x) => (x.id === c.doc.id ? c.doc : x)) : [c.doc, ...list];
  return { ...d, [c.collection]: next } as Domain;
}

/** A plan that has started: dispatched from here, or seeded with steps already moving. */
export const inFlight = (p: Plan) => p.status === 'dispatched' || p.steps.some((s) => s.state !== 'todo');

const patch = <T extends { ref: string }>(list: T[], ref: string, fields: Partial<T>): T[] =>
  list.map((x) => (x.ref === ref ? { ...x, ...fields } : x));
const words = (s: TaskStatus) => s.replace('_', ' ');
/** Global facts belong to no project, so their events are filed under NeuroCode itself — as the API does. */
const home = (f?: MemoryFact) => (!f || f.projectId === 'global' ? 'aios' : f.projectId);
const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');
const clock = () => new Date().toTimeString().slice(0, 8);
const redact = (s: string) => s.replace(/(:\/\/)[^/@\s]+@/g, '$1***@');
const slug = (repo: string) =>
  (repo.trim().replace(/\/+$/, '').split(/[/:\\]/).pop() ?? '').replace(/\.git$/i, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'project';
const titleOf = (id: string) =>
  id.split('-').filter(Boolean).map((w) => (w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1))).join(' ');
const uniqueId = (list: { id: string }[], base: string) => {
  let n = 1;
  let id = base;
  while (list.some((x) => x.id === id)) id = `${base}-${++n}`;
  return id;
};
const nextNumber = (refs: string[]) => Math.max(0, ...refs.map((r) => Number(/\d+$/.exec(r)?.[0] ?? 0))) + 1;
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
        unsubscribe = api.stream({
          activity: (ev) => setDomain((d) => (d.activity.some((e) => e.id === ev.id) ? d : { ...d, activity: [ev, ...d.activity] })),
          change: (c) => setDomain((d) => applyChange(d, c)),
        });
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
  const put = useCallback(<T extends { id: string }>(collection: Collection, doc: T) =>
    setDomain((d) => applyChange(d, { op: 'put', collection, doc })), []);
  const drop = useCallback((collection: Collection, id: string) =>
    setDomain((d) => applyChange(d, { op: 'drop', collection, id })), []);

  /** For toggles: the change is already on screen. Live: persist it, and the server streams the event back. */
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

  /** For actions that create or reshape documents: nothing changes on screen until the server answers. */
  const ask = useCallback(async <T,>(call: () => Promise<T>, failure: string): Promise<T | null> => {
    try {
      return await call();
    } catch (e) {
      toast.error(failure, { description: reason(e) });
      return null;
    }
  }, []);

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

  const resolveConflict = useCallback(async (id: string, keep: 'a' | 'b' | 'adr') => {
    const c = now.current.conflicts.find((x) => x.id === id);
    if (!c) return false;
    if (live.current && !(await ask(() => api.resolveConflict(id, keep), 'Conflict not resolved'))) return false;
    const fact = (fid: string) => now.current.memory.find((f) => f.id === fid);
    const [winner, loser] = keep === 'b' ? [fact(c.b), fact(c.a)] : [fact(c.a), fact(c.b)];
    drop('conflicts', id);
    if (keep !== 'adr' && loser) drop('memory', loser.id);
    if (!live.current) {
      log(keep === 'adr'
        ? { action: 'Conflict escalated', detail: `${c.topic} · written up as an ADR. Both facts stay until it is decided.`, projectId: home(winner), level: 'warn' }
        : { action: 'Conflict resolved', detail: `${c.topic} · kept ${winner?.ref ?? c.a}, archived ${loser?.ref ?? c.b} as superseded`, projectId: home(winner), level: 'ok' });
    }
    return true;
  }, [ask, drop, log]);

  const createProject = useCallback(async (input: ProjectInput) => {
    if (live.current) {
      const doc = await ask(() => api.createProject(input), 'Onboarding did not start');
      if (doc) put('projects', doc);
      return doc;
    }
    const id = uniqueId(now.current.projects, slug(input.repo));
    const repo = redact(input.repo);
    const doc: Project = {
      id, name: titleOf(id), codename: id.toUpperCase(), stack: [], kind: 'greenfield', status: 'onboarding',
      memoryPct: 0, understoodPct: 0, lines: '—', modules: 0, dbTables: 0, storedProcs: 0, repo, lastActive: 'just now',
      coverage: [], work: { tasks: 0, running: 0, review: 0, blocked: 0 },
      description: 'Demo data: with the local API running, the repository is cloned and measured here.',
      source: { kind: input.source, repo, ...(input.source === 'git' ? { branch: input.branch } : {}) },
      rules: input.rules,
    };
    put('projects', doc);
    log({ action: 'Onboarding started', detail: `${doc.name} · ${repo}`, projectId: id, level: 'info' });
    return doc;
  }, [ask, put, log]);

  const registerMcp = useCallback(async (input: McpInput) => {
    if (live.current) {
      const doc = await ask(() => api.registerMcp(input), 'Server not registered');
      if (doc) put('mcp', doc);
      return doc;
    }
    const id = uniqueId(now.current.mcp, input.name);
    const doc: McpServer = {
      id, name: id, transport: input.transport, status: 'disconnected', scope: input.scope, command: input.command,
      tools: [], resources: 0, prompts: 0, latencyMs: 0, calls24h: 0, errorRate: 0,
      untrusted: true, defaultEffect: input.defaultEffect, config: input.config,
    };
    put('mcp', doc);
    log({ action: 'MCP server registered', detail: `${id} · ${input.transport} · ${input.scope} scope · tools default to ${input.defaultEffect}`, projectId: 'aios', level: 'info' });
    return doc;
  }, [ask, put, log]);

  const settleQuestion = useCallback(async (ref: string, index: number, answer: string | null) => {
    const p = now.current.plans.find((x) => x.ref === ref);
    const q = p?.openQuestions[index];
    if (!p || q === undefined) return false;
    if (live.current) {
      // the server also writes the answer into memory and streams that fact back
      const doc = await ask(() => api.settle(ref, index, answer === null ? { defer: true } : { answer }), 'Not saved');
      if (doc) put('plans', doc);
      return !!doc;
    }
    const openQuestions = p.openQuestions.filter((_, i) => i !== index);
    if (answer === null) {
      put('plans', { ...p, openQuestions, deferred: [...(p.deferred ?? []), q] } satisfies Plan);
      log({ action: 'Question deferred', detail: `${ref} · ${q}`, projectId: p.projectId, level: 'warn' });
      return true;
    }
    const n = nextNumber(now.current.memory.map((f) => f.ref));
    put('plans', { ...p, openQuestions, answered: [...(p.answered ?? []), { q, a: answer }] } satisfies Plan);
    put('memory', {
      id: `m${n}`, ref: `MEM-${n}`, category: 'business_rules', title: q, body: answer,
      reason: `You answered it while reviewing ${ref}.`, source: `${ref} · open question`, projectId: p.projectId,
      confidence: 'HIGH', strength: 100, hits: 0, createdAt: 'just now', lastUsed: 'just now', evidence: [ref],
      tags: ['answer', ref], pinned: false,
    } satisfies MemoryFact);
    log({ action: 'Business rule recorded', detail: `MEM-${n} · ${q}`, projectId: p.projectId, level: 'ok' });
    return true;
  }, [ask, put, log]);

  const dispatchPlan = useCallback(async (ref: string) => {
    const p = now.current.plans.find((x) => x.ref === ref);
    if (!p) return false;
    if (live.current) {
      const doc = await ask(() => api.dispatch(ref), 'Not dispatched');
      if (doc) put('plans', doc);
      return !!doc;
    }
    if (inFlight(p) || p.openQuestions.length) return false;
    const steps = p.steps.map((s, i) => (i === 0 ? { ...s, state: 'active' as const } : s));
    put('plans', { ...p, status: 'dispatched', steps } satisfies Plan);
    const t = now.current.tasks.find((x) => x.ref === p.taskRef);
    if (t && (t.status === 'backlog' || t.status === 'planning')) {
      setDomain((d) => ({ ...d, tasks: patch(d.tasks, t.ref, { status: 'in_progress', updatedAt: 'just now' }) }));
    }
    log({ action: 'Plan dispatched', detail: `${ref} → ${p.taskRef} · ${steps[0].agent} starts: ${steps[0].label}`, projectId: p.projectId, level: 'ok', taskRef: p.taskRef });
    return true;
  }, [ask, put, log]);

  const compile = useCallback(async (requirement: string, projectId: string) => {
    if (!live.current) return null;
    const doc = await ask(() => api.compile(requirement, projectId), 'The compiler did not answer');
    if (doc) put('plans', doc);
    return doc;
  }, [ask, put]);

  const recompile = useCallback(async (ref: string) => {
    if (!live.current) return null;
    const doc = await ask(() => api.recompile(ref), 'Not re-compiled');
    if (doc) put('plans', doc);
    return doc;
  }, [ask, put]);

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
    () => ({
      ...domain, mode, health, decide, moveTask, toggleCheck, setPinned, archive, resolveConflict, createProject,
      registerMcp, settleQuestion, dispatchPlan, compile, recompile, searchMemory, reset,
    }),
    [domain, mode, health, decide, moveTask, toggleCheck, setPinned, archive, resolveConflict, createProject,
      registerMcp, settleQuestion, dispatchPlan, compile, recompile, searchMemory, reset],
  );
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useData() {
  const ctx = useContext(C);
  if (!ctx) throw new Error('useData must be used inside <DataProvider>');
  return ctx;
}
