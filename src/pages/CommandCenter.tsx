import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowUp, Bug, ChevronRight, Cpu, Image, Loader2, Paperclip, ShieldAlert, ShieldCheck, Sparkles, TriangleAlert, WandSparkles,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Page, PageBody, Panel, RiskPill, Dot, Mono, Empty, BlockBar } from '@/components/os';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { agents } from '@/mock/agents';
import { runs } from '@/mock/runs';
import { budget } from '@/mock/cost';
import { timeOf } from '@/mock/activity-extra';
import { cn } from '@/lib/utils';

/* Home. One question in the middle of the screen, the way a good assistant opens, with the
   work that needs you laid out calmly underneath. */

const SUGGESTIONS = [
  { icon: Bug, label: 'Fix a bug', text: 'Invoice mein tax galat aa raha hai — CGST/SGST interstate orders pe reverse ho raha hai. Fix karo.' },
  { icon: Sparkles, label: 'Build a feature', text: 'Customers ko CSV se bulk upload karna hai, 50k rows tak, har galat row ka error ke saath.' },
  { icon: WandSparkles, label: 'Refactor safely', text: 'BillingService mein rounding do jagah ho rahi hai. Ek jagah lao, behaviour bilkul same rehna chahiye.' },
  { icon: ShieldCheck, label: 'Harden security', text: 'Login pe rate limiting aur account lockout lagao, har attempt audit log mein ho.' },
];

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

export default function CommandCenter() {
  const nav = useNavigate();
  const loc = useLocation();
  const { project } = useProject();
  const { tasks, approvals, activity, mode, health, compile: compileRequirement } = useData();
  const [req, setReq] = useState('');
  const [compiling, setCompiling] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);

  // "New requirement" in the sidebar lands here with the composer focused.
  const compose = (loc.state as { compose?: number } | null)?.compose;
  useEffect(() => { if (compose) box.current?.focus(); }, [compose]);

  const mine = useMemo(() => tasks.filter((t) => t.projectId === project.id), [tasks, project.id]);
  const active = useMemo(() => mine.filter((t) => ['in_progress', 'review', 'blocked', 'planning'].includes(t.status)), [mine]);
  const pending = useMemo(() => approvals.filter((a) => a.status === 'pending'), [approvals]);
  const live = useMemo(() => runs.filter((r) => r.status === 'running' || r.status === 'waiting'), []);
  const feed = useMemo(() => activity.slice(0, 6), [activity]);
  const running = mine.filter((t) => t.status === 'in_progress').length;
  const agentsLive = agents.filter((a) => a.status === 'running').length;

  const compile = async () => {
    const text = req.trim();
    if (!text || compiling) return;
    if (mode !== 'live') {
      // The public demo has no API: show where a compiled plan lands, using the sample plans.
      toast('Demo: showing a compiled sample plan', { description: 'Run npm run dev:start to compile your own requirement.' });
      nav('/plans');
      return;
    }
    setCompiling(true);
    const plan = await compileRequirement(text, project.id);
    setCompiling(false);
    if (!plan) return;
    setReq('');
    toast.success(`${plan.ref} compiled`, {
      description: `${plan.steps.length} steps · ${plan.openQuestions.length} open questions · ${plan.compiler?.provider === 'rules' ? 'offline planner' : plan.compiler?.model}`,
    });
    nav('/plans');
  };
  const compilerName = mode === 'live' && health?.compiler
    ? (health.compiler.provider === 'rules' ? 'Offline planner' : health.compiler.model)
    : 'DeepSeek-V3.2';
  const note = mode === 'live' ? health?.compiler?.note : undefined;

  return (
    <Page>
      <PageBody className="px-5 pt-10 pb-12 sm:px-10 sm:pt-16">
        {/* The question */}
        <section className="mx-auto w-full max-w-[760px]">
          <h1 className="text-center text-[30px] leading-tight font-semibold tracking-[-0.03em] text-ink sm:text-[38px]">
            {greeting()}, Rajat
          </h1>
          <p className="mt-2.5 text-center text-[15px] text-soft">
            What should we build in <span className="font-medium text-ink-2">{project.name}</span>?
          </p>

          <div className="composer mt-8 border border-line bg-surface p-2" style={{ borderRadius: 'calc(var(--radius) * 2.6)' }}>
            <textarea
              ref={box}
              value={req}
              onChange={(e) => setReq(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); compile(); } }}
              aria-label="Requirement"
              rows={3}
              placeholder="Describe the change in English or Hinglish…"
              className="w-full resize-none bg-transparent px-3 pt-2.5 text-[15.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:outline-none"
            />
            <div className="flex flex-wrap items-center gap-1 px-1 pb-0.5">
              <button onClick={() => toast('Attaching files arrives with the agent runtime')}
                className="flex h-8 items-center gap-1.5 rounded-full px-3 text-[13px] text-soft transition-colors hover:bg-surface-2 hover:text-ink">
                <Paperclip className="size-4" />Attach
              </button>
              <button onClick={() => toast('Screenshot → Vision Agent → component match')}
                className="flex h-8 items-center gap-1.5 rounded-full px-3 text-[13px] text-soft transition-colors hover:bg-surface-2 hover:text-ink">
                <Image className="size-4" />Screenshot
              </button>
              <span className="ml-1 hidden h-8 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[12.5px] text-soft sm:flex" title={note ?? 'The planner that compiles your requirement'}>
                <Cpu className="size-3.5" />{compilerName}
                {note && <TriangleAlert className="size-3.5 text-warn" aria-label={note} />}
              </span>
              <span className="ml-auto hidden pr-2 text-[12px] text-dim md:inline">⌘↵ to compile</span>
              <button
                onClick={compile}
                disabled={!req.trim() || compiling}
                aria-label="Compile Plan"
                title="Compile Plan (⌘↵)"
                className="ml-auto grid size-9 shrink-0 place-items-center rounded-full bg-brand text-brand-ink transition-[opacity,transform] hover:scale-105 disabled:scale-100 disabled:opacity-30 md:ml-0"
              >
                {compiling ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-[18px]" strokeWidth={2.4} />}
              </button>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {SUGGESTIONS.map(({ icon: I, label, text }) => (
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
            <span className="text-ink-2">{agentsLive}</span> agents live ·{' '}
            <span className={pending.length ? 'text-warn' : 'text-ink-2'}>{pending.length}</span> approvals waiting ·{' '}
            <span className="text-ink-2">${budget.spent.toFixed(2)}</span> spent today
          </p>
        </section>

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
                      <span className="mt-0.5 block truncate text-[12.5px] text-dim">{a.agent} · {a.requestedAt}</span>
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
                {active.slice(0, 5).map((t) => (
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
                    <span className="flex shrink-0 items-center gap-2.5">
                      <BlockBar pct={t.progress} width={12} />
                      <span className="tnum w-9 text-right text-[12.5px] text-soft">{t.progress}%</span>
                    </span>
                  </button>
                ))}
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
            <div className="divide-y divide-line/60">
              {live.map((r) => (
                <button key={r.id} onClick={() => nav('/runs')}
                  className="flex w-full items-center gap-3.5 px-5 py-3 text-left transition-colors hover:bg-surface-2/60">
                  <Dot state={r.status} pulse={r.status === 'running'} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-medium text-ink">{r.agentName}</span>
                    <span className="mt-0.5 block truncate text-[12.5px] text-dim">{r.step}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2.5">
                    <BlockBar pct={r.progress} width={10} />
                    <span className="tnum w-9 text-right text-[12.5px] text-soft">{r.progress}%</span>
                  </span>
                </button>
              ))}
            </div>
          </Panel>

          <Panel
            title="Recent activity"
            actions={<Button size="sm" variant="ghost" onClick={() => nav('/activity')}>All activity<ChevronRight className="size-3.5" /></Button>}
            flush
          >
            <div className="divide-y divide-line/60">
              {feed.map((e) => (
                <div key={e.id} className="flex items-start gap-3.5 px-5 py-3">
                  <span className="tnum mt-0.5 w-[62px] shrink-0 font-mono text-[12px] text-dim">{timeOf(e.t)}</span>
                  <Dot state={e.level === 'err' ? 'error' : e.level} className="mt-1.5" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium text-ink-2">{e.action}</span>
                    <span className="mt-0.5 block truncate text-[12.5px] text-dim">{e.detail}</span>
                  </span>
                </div>
              ))}
            </div>
          </Panel>
        </section>
      </PageBody>
    </Page>
  );
}
