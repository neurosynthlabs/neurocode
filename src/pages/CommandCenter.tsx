import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowUp, Bell, BellOff, Bug, Play, Check, ChevronRight, Compass, Cpu, FolderGit2, GitBranchPlus, Lightbulb, Loader2, MessagesSquare, ShieldAlert, ShieldCheck,
  Sparkles, TriangleAlert, WandSparkles, type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Page, PageBody, Panel, RiskPill, Dot, Mono, Empty, BlockBar } from '@/components/os';
import { api, ApiError, type AskAnswer, type RunDoc } from '@/lib/api';
import { plansApi } from '@/lib/live/plans';
import { AnswerCard } from '@/components/memory/AnswerCard';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { fetchModels, type ModelsReport } from '@/lib/live/models';
import { inboxApi, inboxTarget, type Inbox, type InboxItem } from '@/lib/live/inbox';
import { disableNotify, enableNotify, useNotifyState } from '@/lib/notify';
import { useRemote } from '@/lib/remote';
import { clock } from '@/lib/live/work';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';

/* Home. One question in the middle of the screen, the way a good assistant opens, with the
   work that needs you laid out calmly underneath. The composer plans, asks or brainstorms. A question
   opens a session on the project, which reads its code, documents and memory; with no model to answer,
   it is memory that answers, by quoting the matching facts, and says so. */

type Kind = 'plan' | 'ask' | 'idea';
interface Mode { id: Kind; label: string; icon: LucideIcon; placeholder: string; action: string; verb: string; perm: string }
interface Suggestion { icon: LucideIcon; label: string; text: string }

const MODES: Mode[] = [
  { id: 'plan', label: 'Plan', icon: GitBranchPlus, placeholder: 'Describe the change you want, in your own words…', action: 'Compile Plan', verb: 'compile', perm: 'plans:compile' },
  { id: 'ask', label: 'Ask', icon: MessagesSquare, placeholder: 'Ask about this project’s code, docs or decisions…', action: 'Ask', verb: 'ask', perm: 'ai:use' },
  { id: 'idea', label: 'Brainstorm', icon: Lightbulb, placeholder: 'Pitch an idea. The brief argues against itself.', action: 'Brainstorm', verb: 'brainstorm', perm: 'ai:use' },
];

/* Shapes of a request, to start from. They name nothing in any codebase: a suggestion that mentioned a
   class or a table would claim this workspace has it. What memory can be asked is built from memory. */
const STARTERS: Record<'plan' | 'idea', Suggestion[]> = {
  plan: [
    { icon: Bug, label: 'Fix a bug', text: 'Fix a bug: what happens, what should happen instead, and where it shows up — ' },
    { icon: Sparkles, label: 'Build a feature', text: 'Build a feature: who uses it, what they can do with it, and what must not change — ' },
    { icon: WandSparkles, label: 'Refactor safely', text: 'Refactor, keeping the behaviour exactly the same: what to move, and why — ' },
    { icon: ShieldCheck, label: 'Harden security', text: 'Harden security: what is exposed today, and what should be refused or logged — ' },
  ],
  idea: [
    { icon: Lightbulb, label: 'Vendor portal', text: 'A vendor portal where suppliers raise and track invoice disputes themselves.' },
    { icon: Lightbulb, label: 'Weekly digest', text: 'A weekly digest of what the agents shipped, for managers who never open the app.' },
    { icon: Lightbulb, label: 'Offline orders', text: 'Field staff create orders offline, and they sync when the phone is back online.' },
  ],
};

const AUTO_START = 'nc.composer.autoStart';

/** How many of a run's steps are behind it, as a share of all of them. */
const progress = (r: RunDoc) =>
  Math.round((100 * r.steps.filter((s) => ['done', 'skipped', 'failed'].includes(s.status)).length) / Math.max(1, r.steps.length));

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

export default function CommandCenter() {
  const nav = useNavigate();
  const loc = useLocation();
  const { project, all } = useProject();
  const { user, can } = useAuth();
  const { tasks, approvals, activity, memory, health, runs: agentRuns, compile: compileRequirement, ask, brainstorm } = useData();
  const [models, setModels] = useState<ModelsReport | null>(null);
  const [kind, setKind] = useState<Kind>('plan');
  // Brainstorm's "Plan the MVP" lands here with the requirement drafted.
  const [req, setReq] = useState(() => (loc.state as { draft?: string } | null)?.draft ?? '');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<(AskAnswer & { q: string }) | null>(null);
  // "Start right away": a prompt compiles and the agents begin, its open questions deferred on the record.
  // This person's habit, kept in this browser; the gates, the review and the signature still stand.
  const [autoStart, setAutoStart] = useState(() => {
    try { return localStorage.getItem(AUTO_START) !== 'off'; } catch { return true; }
  });
  const flipAutoStart = () => {
    const next = !autoStart;
    setAutoStart(next);
    try { localStorage.setItem(AUTO_START, next ? 'on' : 'off'); } catch { /* private window: this visit only */ }
  };
  const box = useRef<HTMLTextAreaElement>(null);
  const m = MODES.find((x) => x.id === kind) ?? MODES[0];
  const permitted = can(m.perm);

  // "New requirement" in the sidebar lands here with the composer focused.
  const compose = (loc.state as { compose?: number } | null)?.compose;
  useEffect(() => { if (compose) box.current?.focus(); }, [compose]);

  // The day's spend is the gateway's own ledger. Without an answer the figure is left out, not guessed.
  useEffect(() => {
    let live = true;
    fetchModels().then(
      (report) => { if (live) setModels(report); },
      (e: unknown) => { console.error('[NeuroCode] GET /models failed:', e); },
    );
    return () => { live = false; };
  }, []);

  const mine = useMemo(() => (project ? tasks.filter((t) => t.projectId === project.id) : []), [tasks, project]);
  const active = useMemo(() => mine.filter((t) => ['in_progress', 'review', 'blocked', 'planning'].includes(t.status)), [mine]);
  const pending = useMemo(() => approvals.filter((a) => a.status === 'pending'), [approvals]);
  const live = useMemo(() => agentRuns.filter((r) => ['running', 'queued', 'waiting'].includes(r.status)).map((r) => ({
    id: r.id, name: `${r.ref} · ${r.branch.replace('neurocode/', '')}`, state: r.status === 'waiting' ? 'waiting' : 'running',
    step: r.status === 'waiting' ? `waiting for you · ${r.waitingOn ?? ''}` : r.steps.find((s) => s.status === 'running')?.label ?? 'starting…',
    pct: progress(r),
    to: `/runs?ref=${r.ref}`,
  })), [agentRuns]);
  // A task's progress is how far its latest run got; a task no run has worked on shows no bar at all.
  const latestRun = useMemo(() => {
    const m = new Map<string, RunDoc>();
    agentRuns.forEach((r) => { if (r.taskRef && !r.parent && !m.has(r.taskRef)) m.set(r.taskRef, r); });
    return m;
  }, [agentRuns]);
  const feed = useMemo(() => activity.slice(0, 6), [activity]);
  const suggestions = useMemo<Suggestion[]>(() => {
    if (kind !== 'ask') return STARTERS[kind];
    const known = memory.filter((f) => !project || f.projectId === project.id || f.projectId === null || f.projectId === 'global');
    return [...known.filter((f) => f.pinned), ...known.filter((f) => !f.pinned)].slice(0, 3).map((f) => ({
      icon: MessagesSquare, label: f.title.length > 40 ? `${f.title.slice(0, 40)}…` : f.title, text: `What do we know about “${f.title}”?`,
    }));
  }, [kind, memory, project]);

  const compile = async (text: string) => {
    if (!project) return;
    setBusy(true);
    const plan = await compileRequirement(text, project.id);
    if (!plan) { setBusy(false); return; }
    setReq('');
    if (autoStart && can('plans:decide')) {
      try {
        const started = await plansApi.dispatch(plan.ref, { skipQuestions: true });
        setBusy(false);
        const skipped = plan.openQuestions.length;
        if (started.noRun) toast.warning(`${plan.ref} dispatched, no run started`, { description: started.noRun });
        else toast.success(`${plan.ref} started`, {
          description: `${plan.steps.length} steps${skipped ? ` · ${skipped} open questions deferred` : ''}${plan.compiler ? ` · ${plan.compiler.model}` : ''}`,
        });
        nav(started.runRef ? `/runs?ref=${started.runRef}` : `/plans?ref=${plan.ref}`);
        return;
      } catch (e) {
        toast.error(`${plan.ref} compiled, not started`, { description: e instanceof ApiError ? e.message : 'The API did not answer.' });
      }
    }
    setBusy(false);
    toast.success(`${plan.ref} compiled`, {
      description: `${plan.steps.length} steps · ${plan.openQuestions.length} open questions${plan.compiler ? ` · ${plan.compiler.model}` : ''}`,
    });
    nav(`/plans?ref=${plan.ref}`);
  };

  const submit = async () => {
    const text = req.trim();
    if (!text || busy) return;
    if (kind === 'plan') { await compile(text); return; }
    setBusy(true);
    if (kind === 'ask') {
      // A model and a project: a session, which reads the code as well as memory, and keeps the conversation.
      if (project && health?.compiler && health.compiler.provider !== 'rules' && can('sessions:chat')) {
        try {
          const session = await api.newSession(project.id);
          await api.askSession(session.ref, text);
          setReq('');
          nav(`/sessions?ref=${session.ref}`);
        } catch (e) {
          toast.error('The question was not asked', { description: e instanceof Error ? e.message : 'The local API did not answer.' });
        } finally {
          setBusy(false);
        }
        return;
      }
      const a = await ask(text, project?.id);
      setBusy(false);
      if (a) setAnswer({ ...a, q: text });
      return;
    }
    const doc = await brainstorm(text, project?.id);
    setBusy(false);
    if (!doc) return;
    setReq('');
    toast.success(`${doc.ref} · ${doc.brief.title}`, { description: `Brief written by ${doc.compiler.model}.` });
    nav(`/brainstorm?ref=${doc.ref}`);
  };

  const gateway = health?.compiler;
  const engine = !gateway ? 'Not connected' : gateway.provider !== 'rules' ? gateway.model : kind === 'ask' ? 'Memory search' : 'No model';
  // Compiling is for a project: with none onboarded there is nothing to plan against.
  const blocked = kind === 'plan' && !project;
  const cost = models?.totals24h;
  const note = gateway?.note;
  const first = user?.name.split(/\s+/)[0] || 'there';

  return (
    <Page>
      <PageBody className="px-5 pt-10 pb-12 sm:px-10 sm:pt-16">
        {/* The question */}
        <section className="mx-auto w-full max-w-[760px]">
          <h1 className="text-center text-[30px] leading-tight font-semibold tracking-[-0.03em] text-ink sm:text-[38px]">
            {greeting()}, {first}
          </h1>
          <p className="mt-2.5 text-center text-[15px] text-soft">
            {project
              ? <>What should we build in <span className="font-medium text-ink-2">{project.name}</span>?</>
              : <>Onboard a repository in <Link to="/projects" className="font-medium text-ink-2 underline-offset-4 hover:underline">Projects</Link> to start.</>}
          </p>

          <div className="composer mt-8 border border-line bg-surface p-2" style={{ borderRadius: 'calc(var(--radius) * 2.6)' }}>
            <textarea
              ref={box}
              value={req}
              onChange={(e) => setReq(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit(); } }}
              aria-label="Requirement"
              rows={3}
              placeholder={m.placeholder}
              className="w-full resize-none bg-transparent px-3 pt-2.5 text-[15.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:outline-none"
            />
            <div className="flex flex-wrap items-center gap-1 px-1 pb-0.5">
              <div role="radiogroup" aria-label="What to do with it" className="flex items-center gap-0.5 rounded-full bg-surface-2 p-0.5">
                {MODES.map((x) => (
                  <button
                    key={x.id} role="radio" aria-checked={kind === x.id}
                    onClick={() => { setKind(x.id); box.current?.focus(); }}
                    className={cn('flex h-7 items-center gap-1.5 rounded-full px-3 text-[13px] transition-colors',
                      kind === x.id ? 'seg-thumb font-medium text-ink' : 'text-soft hover:text-ink')}
                  >
                    <x.icon className="size-3.5" />{x.label}
                  </button>
                ))}
              </div>
              <span className="ml-1 hidden h-8 items-center gap-1.5 rounded-full px-2.5 text-[12.5px] text-soft sm:flex"
                title={note ?? 'The model that answers. Choose it in Models.'}>
                <Cpu className="size-3.5" />{engine}
                {note && <TriangleAlert className="size-3.5 text-warn" aria-label={note} />}
              </span>
              {kind === 'plan' && can('plans:decide') && (
                <button type="button" role="switch" aria-checked={autoStart} onClick={flipAutoStart}
                  title={autoStart ? 'Agents start as soon as the plan is compiled; open questions are deferred'
                    : 'The plan waits for you to answer its questions and dispatch it'}
                  className={cn('flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[12.5px] transition-colors',
                    autoStart ? 'bg-brand/12 text-brand' : 'text-soft hover:text-ink')}>
                  <Play className="size-3.5" />Start right away
                </button>
              )}
              <span className="ml-auto hidden pr-2 text-[12px] text-dim md:inline">⌘↵ to {autoStart && kind === 'plan' ? 'start' : m.verb}</span>
              <button
                onClick={() => void submit()}
                disabled={!req.trim() || busy || !permitted || blocked}
                aria-label={m.action}
                title={!permitted ? 'Your role cannot do this' : blocked ? 'Onboard a project first' : `${m.action} (⌘↵)`}
                className="ml-auto grid size-9 shrink-0 place-items-center rounded-full bg-brand text-brand-ink transition-[opacity,transform] hover:scale-105 disabled:scale-100 disabled:opacity-30 md:ml-0"
              >
                {busy ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-[18px]" strokeWidth={2.4} />}
              </button>
            </div>
          </div>

          {answer && <div className="mt-4"><AnswerCard answer={answer} onClose={() => setAnswer(null)} /></div>}

          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {suggestions.map(({ icon: I, label, text }) => (
              <button
                key={label}
                onClick={() => { setReq(text); box.current?.focus(); }}
                className="flex items-center gap-2 rounded-full border border-line bg-surface px-3.5 py-2 text-[13px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              >
                <I className="size-4 text-dim" />{label}
              </button>
            ))}
          </div>

          {/* The three counts that stood here are on the Inbox strip below and in the panel headers; only the
              spend is on this screen once, so only the spend is left. */}
          {cost && (
            <p className="mt-7 text-center text-[13px] text-dim">
              {cost.costComplete ? '' : 'at least '}<span className="text-ink-2">${cost.costUsd.toFixed(2)}</span> spent in 24 h
            </p>
          )}
        </section>

        {/* The inbox: what needs you, what is working, what finished since you last looked. */}
        <InboxStrip />

        {/* A workspace with no project yet: the two ways in. */}
        {all.length === 0 && (
          <section className="mx-auto mt-10 grid w-full max-w-[760px] grid-cols-1 gap-4 sm:grid-cols-2" aria-label="Get started">
            {[
              { icon: FolderGit2, title: 'Onboard an existing repository', text: 'Clone one, or open a folder on this machine.', to: '/projects?new=1', perm: 'projects:onboard' },
              { icon: Compass, title: 'Design a new system', text: 'Pick an architecture, shape it, scaffold it.', to: '/blueprints?new=1', perm: 'plans:compile' },
            ].map(({ icon: I, title, text, to, perm }) => (
              <button key={to} onClick={() => nav(to)} disabled={!can(perm)} title={can(perm) ? undefined : `Needs the ${perm} permission`}
                className="flex flex-col items-start gap-2 rounded-2xl border border-line bg-surface px-5 py-4 text-left transition-colors hover:bg-surface-2/60 disabled:opacity-50">
                <span className="grid size-9 place-items-center rounded-full bg-brand/12 text-brand"><I className="size-[18px]" /></span>
                <span className="text-[15px] font-semibold text-ink">{title}</span>
                <span className="text-[13px] leading-relaxed text-soft">{text}</span>
              </button>
            ))}
          </section>
        )}

        {/* The work, laid out calmly */}
        <section className="mx-auto mt-14 grid w-full max-w-[1120px] grid-cols-1 gap-5 lg:grid-cols-2">
          <Panel
            title={<span className="flex items-center gap-2">Needs your decision{pending.length > 0 && <span className="tnum text-dim">{pending.length}</span>}</span>}
            actions={<Button size="sm" variant="ghost" onClick={() => nav('/permissions')}>Review all<ChevronRight className="size-3.5" /></Button>}
            flush
          >
            {pending.length === 0 ? (
              <Empty icon={<ShieldCheck className="size-6" />} title="You are all caught up" />
            ) : (
              <div className="divide-y divide-line/60">
                {pending.map((a) => (
                  <button key={a.id} onClick={() => nav('/permissions')}
                    className="flex w-full items-center gap-3.5 px-5 py-3.5 text-left transition-colors hover:bg-surface-2/60">
                    <span className={cn('grid size-8 shrink-0 place-items-center rounded-full',
                      a.risk === 'CRITICAL' || a.risk === 'HIGH' ? 'bg-danger/12 text-danger' : 'bg-warn/12 text-warn')}>
                      <ShieldAlert className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[14px] font-medium text-ink">{a.title}</span>
                      <span className="mt-0.5 block truncate text-[12.5px] text-dim">{a.agent} · {ago(a.requestedAt)}</span>
                    </span>
                    <RiskPill risk={a.risk} bare />
                    <ChevronRight className="size-4 shrink-0 text-dim" />
                  </button>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title={<span className="flex items-center gap-2">In progress<span className="tnum text-dim">{active.length}</span></span>}
            actions={<Button size="sm" variant="ghost" onClick={() => nav('/tasks')}>Open board<ChevronRight className="size-3.5" /></Button>}
            flush
          >
            {active.length === 0 ? (
              <Empty title="Nothing in flight" hint="Describe a change above to plan it." />
            ) : (
              <div className="divide-y divide-line/60">
                {active.slice(0, 5).map((t) => {
                  const run = latestRun.get(t.ref);
                  return (
                  <button key={t.id} onClick={() => nav(`/tasks?ref=${t.ref}`)}
                    className="flex w-full items-center gap-3.5 px-5 py-3.5 text-left transition-colors hover:bg-surface-2/60">
                    <Dot state={t.status} pulse={t.status === 'in_progress'} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[14px] font-medium text-ink">{t.title}</span>
                      <span className="mt-0.5 flex items-center gap-2 text-[12.5px] text-dim">
                        <Mono>{t.ref}</Mono>
                        <span className="truncate capitalize">{t.status.replace('_', ' ')}</span>
                      </span>
                    </span>
                    {run && (
                      <span className="flex shrink-0 items-center gap-2.5" title={`${run.ref}: steps finished`}>
                        <BlockBar pct={progress(run)} width={12} />
                        <span className="tnum w-9 text-right text-[12.5px] text-soft">{progress(run)}%</span>
                      </span>
                    )}
                  </button>
                  );
                })}
                {active.length > 5 && (
                  <button onClick={() => nav('/tasks')} className="w-full px-5 py-3 text-left text-[13px] text-soft transition-colors hover:bg-surface-2/60 hover:text-ink">
                    {active.length - 5} more on the board →
                  </button>
                )}
              </div>
            )}
          </Panel>

          <Panel
            title={<span className="flex items-center gap-2">Agents at work<span className="tnum text-dim">{live.length}</span></span>}
            actions={<Button size="sm" variant="ghost" onClick={() => nav('/runs')}>Live runs<ChevronRight className="size-3.5" /></Button>}
            flush
          >
            {live.length === 0 ? (
              <Empty title="No agent is working" hint="Dispatch a plan to start one." />
            ) : (
              <div className="divide-y divide-line/60">
                {live.map((r) => (
                  <button key={r.id} onClick={() => nav(r.to)}
                    className="flex w-full items-center gap-3.5 px-5 py-3 text-left transition-colors hover:bg-surface-2/60">
                    <Dot state={r.state} pulse={r.state === 'running'} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[14px] font-medium text-ink">{r.name}</span>
                      <span className="mt-0.5 block truncate text-[12.5px] text-dim">{r.step}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2.5">
                      <BlockBar pct={r.pct} width={10} />
                      <span className="tnum w-9 text-right text-[12.5px] text-soft">{r.pct}%</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title="Recent activity"
            actions={<Button size="sm" variant="ghost" onClick={() => nav('/activity')}>All activity<ChevronRight className="size-3.5" /></Button>}
            flush
          >
            {feed.length === 0 ? (
              <Empty title="Nothing has happened yet" />
            ) : <div className="divide-y divide-line/60">
              {feed.map((e) => (
                <div key={e.id} className="flex items-start gap-3.5 px-5 py-3">
                  <span className="tnum mt-0.5 w-[62px] shrink-0 font-mono text-[12px] text-dim">{clock(e.at)}</span>
                  <Dot state={e.level === 'err' ? 'error' : e.level} className="mt-1.5" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium text-ink-2">{e.action}</span>
                    <span className="mt-0.5 block truncate text-[12.5px] text-dim">{e.detail}</span>
                  </span>
                </div>
              ))}
            </div>}
          </Panel>
        </section>
      </PageBody>
    </Page>
  );
}

/* ── The inbox strip ─────────────────────────────────────────────
   Three calm columns read from the work itself: nothing here is stored, so marking it seen only moves
   the moment "done since" counts from. It is read again whenever the stream says something changed. */

const NEEDS_WORD: Record<string, string> = { approval: 'Decision', question: 'Question', signature: 'Signature', permission: 'Permission' };
const WORKING_WORD: Record<string, string> = { run: 'Run', session: 'Session', routine: 'Routine' };
const DONE_WORD: Record<string, string> = { run: 'Run', plan: 'Plan', routine: 'Routine', review: 'Review', research: 'Research', eval: 'Eval' };
const BAD = new Set(['failed', 'cancelled', 'refused']);

function InboxStrip() {
  const nav = useNavigate();
  const { approvals, runs, activity, onChat } = useData();
  const notifyState = useNotifyState();
  const [chatTick, setChatTick] = useState(0);
  const [marking, setMarking] = useState(false);

  // A session's turn (a permission card, a finished answer) is not a document the store holds, so it
  // nudges a re-read here — once a second at most, however fast the turns arrive.
  useEffect(() => {
    let t: number | undefined;
    const off = onChat((m) => {
      if (m.stream || t !== undefined) return;
      t = window.setTimeout(() => { t = undefined; setChatTick((n) => n + 1); }, 1000);
    });
    return () => { off(); window.clearTimeout(t); };
  }, [onChat]);

  const stamp = useMemo(() => [
    approvals.filter((a) => a.status === 'pending').map((a) => a.ref).join(','),
    runs.slice(0, 30).map((r) => `${r.ref}:${r.status}`).join(','),
    activity[0]?.id ?? '',
    chatTick,
  ].join('|'), [approvals, runs, activity, chatTick]);
  const box = useRemote('inbox', () => inboxApi.read());
  const reload = useRef(() => {});
  useLayoutEffect(() => { reload.current = box.reload; });
  const seen = useRef(stamp);
  useEffect(() => {
    if (seen.current === stamp) return;
    seen.current = stamp;
    const t = window.setTimeout(() => reload.current(), 400);
    return () => window.clearTimeout(t);
  }, [stamp]);

  const markSeen = async () => {
    setMarking(true);
    try {
      await inboxApi.seen();
      box.reload();
    } catch (e) {
      toast.error('Not marked', { description: e instanceof Error ? e.message : 'The local API did not answer.' });
    } finally {
      setMarking(false);
    }
  };

  const toggleNotify = async () => {
    if (notifyState === 'on') {
      disableNotify();
      toast('Notifications off');
      return;
    }
    const now = await enableNotify();
    if (now === 'on') toast.success('Notifications on', { description: 'You hear when something needs you or finishes.' });
    else toast.warning('Notifications are blocked', { description: 'Allow them for this site in browser settings, then try again.' });
  };

  const data: Inbox | null = box.data;
  return (
    <section className="mx-auto mt-10 w-full max-w-[1120px]" aria-label="Inbox">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">Inbox</h2>
        <div className="flex items-center gap-1.5">
          {notifyState !== 'unsupported' && (
            <Button size="sm" variant="ghost" onClick={() => void toggleNotify()} aria-pressed={notifyState === 'on'}
              title={notifyState === 'blocked' ? 'This browser blocks notifications for this site' : 'Notify when something needs you or finishes, in the background'}>
              {notifyState === 'on' ? <Bell className="size-3.5" /> : <BellOff className="size-3.5" />}
              {notifyState === 'on' ? 'Notifying you' : 'Notify me'}
            </Button>
          )}
        </div>
      </div>
      {box.error ? (
        <div className="rounded-xl border border-line/70 bg-surface">
          <Empty title="The inbox did not load" hint={box.error} action={<Button size="sm" variant="outline" onClick={box.reload}>Try again</Button>} />
        </div>
      ) : !data ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {[0, 1, 2].map((i) => <div key={i} className="h-[132px] animate-pulse rounded-xl border border-line/60 bg-surface" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <InboxColumn title="Needs you" count={data.counts.needsYou} tone="warn" items={data.needsYou} words={NEEDS_WORD}
            empty="Nothing is waiting for you." onOpen={(i) => nav(inboxTarget(i))} />
          <InboxColumn title="Working" count={data.counts.working} tone="brand" items={data.working} words={WORKING_WORD}
            empty="No agent, session or routine is working." onOpen={(i) => nav(inboxTarget(i))} />
          <InboxColumn
            title={data.sinceVisit ? 'Done since your last visit' : 'Done in the last day'} count={data.counts.doneSince} tone="ok"
            items={data.doneSince} words={DONE_WORD} onOpen={(i) => nav(inboxTarget(i))}
            empty={data.sinceVisit ? `Nothing has finished since ${ago(data.since)}.` : 'Nothing has finished in the last day.'}
            action={data.counts.doneSince > 0 && (
              <Button size="xs" variant="ghost" onClick={() => void markSeen()} disabled={marking}>
                {marking ? <Loader2 className="size-3 animate-spin" /> : <Check className="size-3" />}Mark seen
              </Button>
            )} />
        </div>
      )}
    </section>
  );
}

function InboxColumn({ title, count, tone, items, words, empty, action, onOpen }: {
  title: string; count: number; tone: 'warn' | 'brand' | 'ok'; items: InboxItem[]; words: Record<string, string>;
  empty: string; action?: ReactNode; onOpen: (item: InboxItem) => void;
}) {
  const shown = items.slice(0, 4);
  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-line/70 bg-surface">
      <div className="flex items-center gap-2 px-4 pt-3.5 pb-2">
        <span className="text-[13.5px] font-semibold text-ink">{title}</span>
        <span className={cn('tnum text-[13px]', count ? { warn: 'text-warn', brand: 'text-brand', ok: 'text-ok' }[tone] : 'text-dim')}>{count}</span>
        <span className="ml-auto">{action}</span>
      </div>
      {shown.length === 0 ? (
        <p className="px-4 pb-4 text-[13px] text-dim">{empty}</p>
      ) : (
        <div className="pb-1.5">
          {shown.map((i) => (
            <button key={`${i.kind}:${i.ref}:${i.at ?? ''}`} onClick={() => onOpen(i)}
              className="flex w-full items-start gap-2.5 px-4 py-2 text-left transition-colors hover:bg-surface-2/60">
              <Dot state={i.status && BAD.has(i.status) ? 'error' : tone === 'warn' ? 'waiting' : tone === 'brand' ? 'running' : 'done'}
                pulse={tone === 'brand'} className="mt-1.5" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13.5px] text-ink-2">{i.title}</span>
                <span className="mt-0.5 block truncate text-[12px] text-dim">
                  {words[i.kind] ?? i.kind} · {i.kind !== 'routine' && i.ref !== i.title ? `${i.ref} · ` : ''}{i.detail ? `${i.detail} · ` : ''}{ago(i.at)}
                </span>
              </span>
            </button>
          ))}
          {count > shown.length && <p className="px-4 pt-0.5 pb-2 text-[12px] text-dim">and {count - shown.length} more</p>}
        </div>
      )}
    </div>
  );
}
