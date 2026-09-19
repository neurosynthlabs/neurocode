import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowUp, Bug, ChevronRight, Compass, Cpu, FolderGit2, GitBranchPlus, Lightbulb, Loader2, MessagesSquare, ShieldAlert, ShieldCheck,
  Sparkles, TriangleAlert, WandSparkles, X, type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Page, PageBody, Panel, RiskPill, Dot, Mono, Empty, BlockBar } from '@/components/os';
import type { AskAnswer, RunDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { fetchModels, type ModelsReport } from '@/lib/live/models';
import { clock } from '@/lib/live/work';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';

/* Home. One question in the middle of the screen, the way a good assistant opens, with the
   work that needs you laid out calmly underneath. The composer plans, asks memory or
   brainstorms. Planning and brainstorming need a model; asking memory answers without one, by
   quoting the matching facts, and says so. */

type Kind = 'plan' | 'ask' | 'idea';
interface Mode { id: Kind; label: string; icon: LucideIcon; placeholder: string; action: string; verb: string; perm: string }
interface Suggestion { icon: LucideIcon; label: string; text: string }

const MODES: Mode[] = [
  { id: 'plan', label: 'Plan', icon: GitBranchPlus, placeholder: 'Describe the change you want, in your own words…', action: 'Compile Plan', verb: 'compile', perm: 'plans:compile' },
  { id: 'ask', label: 'Ask', icon: MessagesSquare, placeholder: 'Ask what memory knows about this project…', action: 'Ask memory', verb: 'ask', perm: 'ai:use' },
  { id: 'idea', label: 'Brainstorm', icon: Lightbulb, placeholder: 'Pitch an idea. It comes back as a brief that argues against itself.', action: 'Brainstorm', verb: 'brainstorm', perm: 'ai:use' },
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
  const running = mine.filter((t) => t.status === 'in_progress').length;
  const working = agentRuns.filter((r) => r.status === 'running').length;
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
    setBusy(false);
    if (!plan) return;
    setReq('');
    toast.success(`${plan.ref} compiled`, {
      description: `${plan.steps.length} steps · ${plan.openQuestions.length} open questions${plan.compiler ? ` · ${plan.compiler.model}` : ''}`,
    });
    nav('/plans');
  };

  const submit = async () => {
    const text = req.trim();
    if (!text || busy) return;
    if (kind === 'plan') { await compile(text); return; }
    setBusy(true);
    if (kind === 'ask') {
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
                title={note ?? 'The model that answers. Choose it in Admin → AI providers.'}>
                <Cpu className="size-3.5" />{engine}
                {note && <TriangleAlert className="size-3.5 text-warn" aria-label={note} />}
              </span>
              <span className="ml-auto hidden pr-2 text-[12px] text-dim md:inline">⌘↵ to {m.verb}</span>
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

          {answer && (
            <div className="animate-slide-up mt-4 rounded-2xl border border-line bg-surface p-5">
              <div className="flex items-start gap-3">
                <span className="grid size-8 shrink-0 place-items-center rounded-full bg-brand/12 text-brand"><Sparkles className="size-4" /></span>
                <div className="min-w-0 flex-1">
                  <p className="text-[12.5px] text-dim">{answer.q}</p>
                  <p className="mt-1.5 text-[14.5px] leading-relaxed whitespace-pre-line text-ink">{answer.answer}</p>
                  {answer.citations.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {answer.citations.map((c) => (
                        <Link
                          key={c.ref} to={`/memory?ref=${c.ref}`} title={c.title}
                          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-surface-2/60 px-2.5 py-1 text-[12.5px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
                        >
                          <span className="font-mono text-[11.5px] text-brand">{c.ref}</span>
                          <span className="truncate">{c.title}</span>
                        </Link>
                      ))}
                    </div>
                  )}
                  <p className="mt-3 text-[12px] text-dim">
                    {answer.provider === 'rules'
                      ? 'Memory search, no model: the matching facts, quoted. Add a model in Admin → AI providers for a written answer.'
                      : `${answer.model} · ${(answer.ms / 1000).toFixed(1)} s`}
                  </p>
                </div>
                <button onClick={() => setAnswer(null)} aria-label="Close the answer"
                  className="grid size-7 shrink-0 place-items-center rounded-full text-dim transition-colors hover:bg-surface-2 hover:text-ink">
                  <X className="size-4" />
                </button>
              </div>
            </div>
          )}

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

          <p className="mt-7 text-center text-[13px] text-dim">
            <span className="text-ink-2">{running}</span> tasks running ·{' '}
            <span className="text-ink-2">{working}</span> runs working ·{' '}
            <span className={pending.length ? 'text-warn' : 'text-ink-2'}>{pending.length}</span> approvals waiting
            {cost && <>{' '}·{' '}{cost.costComplete ? '' : 'at least '}<span className="text-ink-2">${cost.costUsd.toFixed(2)}</span> spent in 24 h</>}
          </p>
        </section>

        {/* A workspace with no project yet: the two ways in. */}
        {all.length === 0 && (
          <section className="mx-auto mt-10 grid w-full max-w-[760px] grid-cols-1 gap-4 sm:grid-cols-2" aria-label="Get started">
            {[
              { icon: FolderGit2, title: 'Onboard an existing repository', text: 'Clone a repository or open a folder on this machine. It is measured and indexed, ready to plan against.', to: '/projects?new=1', perm: 'projects:onboard' },
              { icon: Compass, title: 'Design a new system', text: 'Answer a few questions, start from the architecture that fits, shape it, and scaffold it into a new repository.', to: '/blueprints?new=1', perm: 'plans:compile' },
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
              <Empty icon={<ShieldCheck className="size-6" />} title="You are all caught up" hint="Nothing is waiting for your signature." />
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
              <Empty title="Nothing in flight" hint="Describe a change above and it becomes a plan." />
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
              <Empty title="No agent is working" hint="Dispatch a plan and its run starts here, in a worktree of its own." />
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
              <Empty title="Nothing has happened yet" hint="Compiling a plan, onboarding a project or deciding an approval shows up here." />
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
