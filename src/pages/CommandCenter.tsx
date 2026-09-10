import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Paperclip, Image, Sparkles, ArrowRight, Play, Lightbulb, Microscope,
  FolderPlus, Workflow, ShieldAlert, Cpu, Coins, Bot, ListChecks, Clock,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Stat, StatGrid, Tag, RiskPill, Dot,
  BlockBar, Ascii, Mono, Kbd, SectionTitle, Empty,
} from '@/components/os';
import { useProject } from '@/lib/project-context';
import { tasks } from '@/mock/tasks';
import { agents, agentName } from '@/mock/agents';
import { activity } from '@/mock/activity';
import { approvals } from '@/mock/permissions';
import { runs } from '@/mock/runs';
import { budget } from '@/mock/cost';
import { cn } from '@/lib/utils';

const LAYERS = ['Frontend', 'Backend', 'Database', 'Testing', 'Review'] as const;

const COMPILER = `Requirement  ──▶  Research  ──▶  Architecture  ──▶  Impact  ──▶  Tasks
   Hinglish        4 sources        call graph        blast radius     agents + worktrees`;

export default function CommandCenter() {
  const nav = useNavigate();
  const { project } = useProject();
  const [req, setReq] = useState('');
  const [compiling, setCompiling] = useState(false);

  const mine = useMemo(() => tasks.filter((t) => t.projectId === project.id), [project.id]);
  const active = useMemo(
    () => mine.filter((t) => ['in_progress', 'review', 'blocked', 'planning'].includes(t.status)),
    [mine],
  );
  const live = useMemo(() => runs.filter((r) => r.status === 'running' || r.status === 'waiting'), []);
  const pending = useMemo(() => approvals.filter((a) => a.status === 'pending'), []);
  const feed = useMemo(() => activity.slice(-11).reverse(), []);

  const compile = () => {
    if (!req.trim()) return;
    setCompiling(true);
    toast.success('Requirement queued for the Commander', {
      description: 'Compiling → research → impact → task breakdown.',
    });
    window.setTimeout(() => {
      setCompiling(false);
      setReq('');
      nav('/plans');
    }, 1400);
  };

  return (
    <Page>
      <PageHeader
        title="Command Center"
        subtitle={`You are the AI Project Manager and the final approver. Everything below is running against ${project.name}.`}
        actions={
          <>
            <Button size="sm" variant="outline" onClick={() => nav('/workflows')}>
              <Workflow className="size-3.5" />Run workflow
            </Button>
            <Button size="sm" onClick={() => nav('/tasks')}>
              <ListChecks className="size-3.5" />Open board
            </Button>
          </>
        }
      />

      <PageBody>
        <div className="grid grid-cols-12 gap-4">
          {/* ── Left column ─────────────────────────────────────── */}
          <div className="col-span-12 space-y-4 xl:col-span-8">
            {/* Composer */}
            <Panel
              eyebrow="Requirement compiler"
              title="What should we work on?"
              actions={<Tag tone="brand"><Sparkles className="size-3" />Hinglish accepted</Tag>}
            >
              <textarea
                value={req}
                onChange={(e) => setReq(e.target.value)}
                rows={3}
                placeholder="Invoice mein tax galat aa raha hai — CGST/SGST interstate orders pe reverse ho raha hai. Fix karo."
                className="w-full resize-none rounded-sm border border-line bg-base px-3 py-2.5 text-[13px] leading-relaxed text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none"
              />
              <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  <Button size="xs" variant="ghost" onClick={() => toast('Attach files — static prototype')}>
                    <Paperclip className="size-3" />Files
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => toast('Screenshot → Vision Agent → component match')}>
                    <Image className="size-3" />Screenshot
                  </Button>
                  <span className="ml-1 hidden items-center gap-1.5 text-[11px] text-dim sm:flex">
                    <Cpu className="size-3" />routes to <Mono tone="brand">DeepSeek-V3.2</Mono> for planning
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="hidden text-[11px] text-dim md:inline">
                    <Kbd>⌘</Kbd> <Kbd>↵</Kbd> to compile
                  </span>
                  <Button size="sm" disabled={!req.trim() || compiling} onClick={compile}>
                    {compiling ? 'Compiling…' : <>Compile Plan<ArrowRight className="size-3.5" /></>}
                  </Button>
                </div>
              </div>
              <Ascii className="mt-3">{COMPILER}</Ascii>
            </Panel>

            {/* Needs you */}
            {pending.length > 0 && (
              <Panel
                eyebrow="Blocked on you"
                title={`${pending.length} approvals waiting`}
                className="border-warn/35"
                actions={<Button size="xs" variant="outline" onClick={() => nav('/permissions')}>Review all</Button>}
                flush
              >
                <div className="divide-y divide-line">
                  {pending.map((a) => (
                    <button
                      key={a.id}
                      onClick={() => nav('/permissions')}
                      className="flex w-full items-start gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-surface-2"
                    >
                      <ShieldAlert className={cn('mt-0.5 size-3.5 shrink-0', a.risk === 'CRITICAL' ? 'text-danger' : 'text-warn')} />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <Mono>{a.ref}</Mono>
                          <span className="text-[12.5px] font-medium text-ink">{a.title}</span>
                          <RiskPill risk={a.risk} />
                        </span>
                        <span className="mt-0.5 block truncate text-[11.5px] text-dim">
                          {a.agent} · {a.tool} · {a.requestedAt}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </Panel>
            )}

            {/* Active work */}
            <Panel
              eyebrow="Active work"
              title={`${active.length} tasks moving in ${project.name}`}
              actions={<Button size="xs" variant="ghost" onClick={() => nav('/tasks')}>Board →</Button>}
              flush
            >
              {active.length === 0 ? (
                <Empty title="Nothing in flight" hint="Compile a requirement above to put agents to work." />
              ) : (
                <div className="divide-y divide-line">
                  {active.map((t) => (
                    <button
                      key={t.id}
                      onClick={() => nav('/tasks')}
                      className="flex w-full items-center gap-4 px-3.5 py-3 text-left transition-colors hover:bg-surface-2"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Mono>{t.ref}</Mono>
                          <span className="truncate text-[12.5px] font-medium text-ink">{t.title}</span>
                          {t.status === 'blocked' && <Tag tone="danger">blocked</Tag>}
                          {t.priority === 'URGENT' && <Tag tone="danger">urgent</Tag>}
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          {LAYERS.map((l) => {
                            const on = t.layers.includes(l) || (l === 'Testing' && t.tests > 0) || (l === 'Review' && t.status === 'review');
                            return (
                              <span
                                key={l}
                                className={cn(
                                  'inline-flex items-center gap-1 rounded-xs border px-1.5 py-px text-[10.5px]',
                                  on ? 'border-brand/30 bg-brand/10 text-brand' : 'border-line text-dim',
                                )}
                              >
                                <Dot state={on ? 'ok' : 'idle'} />{l}
                              </span>
                            );
                          })}
                          <span className="ml-1 text-[11px] text-dim">
                            {t.agents.length ? t.agents.map(agentName).join(' · ') : 'unassigned'}
                          </span>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <RiskPill risk={t.risk} />
                        <div className="w-24">
                          <div className="tnum mb-1 text-right text-[11px] text-soft">{t.progress}%</div>
                          <BlockBar pct={t.progress} width={12} />
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </Panel>

            {/* Quick actions */}
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-5">
              {[
                { icon: Play, label: 'New Task', to: '/tasks' },
                { icon: Lightbulb, label: 'Brainstorm', to: '/brainstorm' },
                { icon: Microscope, label: 'Research', to: '/research' },
                { icon: FolderPlus, label: 'Onboard Repo', to: '/projects' },
                { icon: Workflow, label: 'Run Workflow', to: '/workflows' },
              ].map(({ icon: Icon, label, to }) => (
                <button
                  key={label}
                  onClick={() => nav(to)}
                  className="hover-lift flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2.5 text-[12px] text-ink-2"
                >
                  <Icon className="size-3.5 text-brand" />{label}
                </button>
              ))}
            </div>
          </div>

          {/* ── Right rail ──────────────────────────────────────── */}
          <div className="col-span-12 space-y-4 xl:col-span-4">
            <StatGrid cols={2}>
              <Stat label="Tasks running" value={mine.filter((t) => t.status === 'in_progress').length} sub={`${mine.length} total in project`} icon={<ListChecks className="size-3" />} />
              <Stat label="Agents live" value={agents.filter((a) => a.status === 'running').length} sub={`of ${agents.length} in the org`} tone="ok" icon={<Bot className="size-3" />} />
              <Stat label="Approvals" value={pending.length} sub="median decision 6m 12s" tone={pending.length ? 'warn' : 'neutral'} icon={<ShieldAlert className="size-3" />} />
              <Stat label="Spend today" value={`$${budget.spent.toFixed(2)}`} sub={`of $${budget.daily.toFixed(2)} · 71% local`} tone="brand" icon={<Coins className="size-3" />} />
            </StatGrid>

            <Panel eyebrow="Live agents" title="Parallel execution" actions={<Button size="xs" variant="ghost" onClick={() => nav('/runs')}>Terminal →</Button>} flush>
              <div className="divide-y divide-line">
                {live.map((r) => (
                  <button key={r.id} onClick={() => nav('/runs')} className="flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-surface-2">
                    <Dot state={r.status} pulse={r.status === 'running'} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] text-ink">{r.agentName}</span>
                      <span className="block truncate text-[10.5px] text-dim">{r.step}</span>
                    </span>
                    <BlockBar pct={r.progress} width={8} />
                    <span className="tnum w-8 shrink-0 text-right text-[11px] text-soft">{r.progress}%</span>
                  </button>
                ))}
              </div>
            </Panel>

            <Panel eyebrow="Brain log" title="What just happened" actions={<Button size="xs" variant="ghost" onClick={() => nav('/activity')}>All →</Button>} flush>
              <div className="divide-y divide-line/60">
                {feed.map((e) => (
                  <div key={e.id} className="flex items-start gap-2.5 px-3.5 py-1.5">
                    <span className="tnum mt-px shrink-0 font-mono text-[10.5px] text-dim">{e.t}</span>
                    <Dot state={e.level === 'err' ? 'error' : e.level} className="mt-1.5" />
                    <span className="min-w-0 flex-1">
                      <span className="text-[11.5px] text-ink-2">{e.action}</span>
                      <span className="block truncate text-[10.5px] text-dim">{e.detail}</span>
                    </span>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel eyebrow="Understanding" title={project.name} flush>
              <div className="space-y-2 p-3.5">
                {project.coverage.slice(0, 4).map((c) => (
                  <div key={c.label} className="flex items-center gap-2.5">
                    <span className="w-32 shrink-0 truncate text-[11.5px] text-ink-2">{c.label}</span>
                    <BlockBar pct={c.pct} width={14} />
                    <span className="tnum w-8 text-right text-[11px] text-soft">{c.pct}%</span>
                  </div>
                ))}
                <div className="flex items-center justify-between border-t border-line pt-2.5">
                  <SectionTitle className="mb-0">Project understood</SectionTitle>
                  <span className="tnum text-[15px] font-semibold text-brand">{project.understoodPct}%</span>
                </div>
                <p className="flex items-start gap-1.5 text-[11px] text-dim">
                  <Clock className="mt-px size-3 shrink-0" />
                  Last full index {project.lastActive}. Memory retains {project.memoryPct}% of what it has seen.
                </p>
              </div>
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
