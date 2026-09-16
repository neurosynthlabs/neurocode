import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, Bot, Check, Loader2, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ICONS } from '@/lib/icons';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, ListRow, Stat, StatGrid,
  KV, MeterRow, SectionTitle, Empty, Segmented,
} from '@/components/os';
import { agents } from '@/mock/agents';
import { runs } from '@/mock/runs';
import { useData } from '@/lib/data';
import { fetchRoster, type LiveAgent, type LiveAgentStatus } from '@/lib/live/agents';
import { useRemote } from '@/lib/remote';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { AgentStatus } from '@/types';

const ORG = `                        ┌──────────────────────┐
                        │         YOU          │
                        │  AI PROJECT MANAGER  │
                        │    FINAL APPROVAL    │
                        └──────────┬───────────┘
                                   ▼
                       ┌───────────────────────┐
                       │     AI COMMANDER      │
                       │  Project Manager · CEO │
                       └───────────┬───────────┘
               ┌───────────────────┼───────────────────┐
               ▼                   ▼                   ▼
        MEMORY BRAIN        KNOWLEDGE BRAIN       TASK ENGINE
               └───────────────────┼───────────────────┘
                                   ▼
                            ORCHESTRATOR
       ┌──────────────┬────────────┼────────────┬──────────────┐
       ▼              ▼            ▼            ▼              ▼
   ARCHITECT      FRONTEND      BACKEND        DB           DEVOPS
       └──────────────┴────────────┼────────────┴──────────────┘
                        ┌──────────┴──────────┐
                        ▼                     ▼
                    TEST AGENT          SECURITY AGENT
                        └──────────┬──────────┘
                                   ▼
                             REVIEW AGENT
                                   ▼
                              YOU APPROVE
                                   ▼
                               GIT / PR`;

const AUTONOMY: Record<string, { tone: 'ok' | 'warn' | 'danger'; note: string }> = {
  supervised: { tone: 'warn', note: 'Every write is proposed, never applied without a gate.' },
  semi: { tone: 'ok', note: 'Acts alone inside its worktree; escalates on MEDIUM and above.' },
  autonomous: { tone: 'ok', note: 'Runs end to end unsupervised — read-only or non-destructive by construction.' },
};

const ESCALATION = [
  { tier: 'LOW', what: 'read files · search · run tests · write documentation', who: 'automatic', tone: 'ok' as const },
  { tier: 'MEDIUM', what: 'code changes · dependency changes · staging deploy', who: 'reviewer gate', tone: 'warn' as const },
  { tier: 'HIGH', what: 'database migration · production · delete data · security config', who: 'YOU must approve', tone: 'danger' as const },
];

function Icon({ name, className }: { name: string; className?: string }) {
  const C = ICONS[name] ?? Bot;
  return <C className={className} />;
}

/** The roster the runtime really has when the local API is up; the worked example in the demo. */
export default function Agents() {
  const { mode } = useData();
  return mode === 'live' ? <LiveAgents /> : <SampleAgents />;
}

function SampleAgents() {
  const [sel, setSel] = useState(agents[0].id);
  const [view, setView] = useState<'roster' | 'org'>('roster');
  const a = useMemo(() => agents.find((x) => x.id === sel) ?? agents[0], [sel]);
  const byStatus = (s: AgentStatus) => agents.filter((x) => x.status === s).length;
  const activeRun = useMemo(() => runs.find((r) => r.agentId === a.id), [a.id]);

  return (
    <Page>
      <PageHeader
        title="Agents"
        subtitle="Twelve specialists with their own model, tools, skills and guardrails. You are the only one who can approve."
        actions={<Segmented options={[{ id: 'roster', label: 'Roster' }, { id: 'org', label: 'Org chart' }]} value={view} onChange={setView} />}
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <Tag tone="ok"><Dot state="running" pulse />{byStatus('running')} running</Tag>
          <Tag tone="neutral">{byStatus('idle')} idle</Tag>
          <Tag tone="warn">{byStatus('waiting')} waiting</Tag>
          <span className="ml-2 text-[12.5px] text-dim">
            concurrency cap 8 · max parallel worktrees 6 · one agent per worktree, never shared
          </span>
        </div>
      </PageHeader>

      <PageBody className={cn(view === 'roster' && 'flex h-full flex-col p-0')}>
        {view === 'org' ? (
          <div className="space-y-4">
            <Panel eyebrow="Chain of command" title="Every change ends at your signature">
              <Ascii>{ORG}</Ascii>
            </Panel>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
              {ESCALATION.map((e) => (
                <Panel key={e.tier} eyebrow={`${e.tier} risk`} title={e.who} className={e.tone === 'danger' ? 'border-danger/35' : undefined}>
                  <p className="text-[13px] text-soft">{e.what}</p>
                  <div className="mt-2 border-t border-line pt-2">
                    <Tag tone={e.tone}>{e.who}</Tag>
                  </div>
                </Panel>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            {/* Roster */}
            <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
              {agents.map((x) => (
                <ListRow key={x.id} active={x.id === sel} onClick={() => setSel(x.id)}>
                  <div className="flex items-center gap-2.5">
                    <span className={cn('grid size-6 shrink-0 place-items-center rounded-sm',
                      x.status === 'running' ? 'bg-brand/15 text-brand' : 'bg-surface-3 text-soft')}>
                      <Icon name={x.icon} className="size-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-[13.5px] font-medium text-ink">{x.name}</span>
                        <Dot state={x.status} pulse={x.status === 'running'} />
                      </span>
                      <span className="block truncate text-[11.5px] text-dim">{x.role}</span>
                    </span>
                  </div>
                  <div className="mt-1.5 flex items-center gap-2">
                    <Mono>{x.model}</Mono>
                    <span className="ml-auto tnum text-[11.5px] text-dim">{x.successRate}%</span>
                  </div>
                </ListRow>
              ))}
            </div>

            {/* Detail */}
            <div className="min-w-0 flex-1 overflow-y-auto p-5">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand/12 text-brand">
                    <Icon name={a.icon} className="size-4.5" />
                  </span>
                  <div>
                    <h2 className="text-[17px] font-semibold text-ink">{a.name}</h2>
                    <p className="text-[13.5px] text-soft">{a.role}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Tag tone={AUTONOMY[a.autonomy].tone}>{a.autonomy}</Tag>
                  <Tag tone={a.status === 'running' ? 'ok' : 'neutral'}><Dot state={a.status} pulse={a.status === 'running'} />{a.status}</Tag>
                </div>
              </div>

              <StatGrid cols={5} className="mb-4">
                <Stat label="Tasks done" value={a.tasksDone} />
                <Stat label="Success rate" value={`${a.successRate}%`} tone={a.successRate >= 93 ? 'ok' : 'warn'} />
                <Stat label="Avg duration" value={`${a.avgMinutes}m`} />
                <Stat label="Tokens 24h" value={`${(a.tokens24h / 1_000_000).toFixed(2)}M`} />
                <Stat label="Cost 24h" value={a.cost24h === 0 ? 'free' : `$${a.cost24h.toFixed(2)}`} tone={a.cost24h === 0 ? 'ok' : 'brand'} sub={a.cost24h === 0 ? 'local model' : 'remote API'} />
              </StatGrid>

              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <Panel eyebrow="Routing" title="Models">
                  <KV k="Primary" v={<Mono tone="brand">{a.model}</Mono>} />
                  <KV k="Fallback" v={<Mono>{a.fallbackModel ?? '—'}</Mono>} />
                  <KV k="Autonomy" v={a.autonomy} />
                  <p className="mt-2 text-[12.5px] text-dim">{AUTONOMY[a.autonomy].note}</p>
                </Panel>

                <Panel eyebrow="Capabilities" title="Tools granted">
                  <div className="flex flex-wrap gap-1.5">
                    {a.tools.map((t) => (
                      <span key={t} className="inline-flex items-center gap-1 rounded-xs border border-ok/25 bg-ok/8 px-1.5 py-px text-[12px] text-ok">
                        <Check className="size-2.5" />{t}
                      </span>
                    ))}
                  </div>
                  <SectionTitle className="mt-3 mb-1.5">Skills loaded on trigger</SectionTitle>
                  <div className="flex flex-wrap gap-1">
                    {a.skills.map((s) => <Mono key={s}>{s}</Mono>)}
                  </div>
                </Panel>

                <Panel eyebrow="System prompt" title="How it is instructed" className="lg:col-span-2">
                  <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">{a.systemPrompt}</pre>
                </Panel>

                <Panel eyebrow="Guardrails" title="What it structurally cannot do" className="lg:col-span-2" flush>
                  <div className="divide-y divide-line">
                    {a.guardrails.map((g) => (
                      <div key={g} className="flex items-start gap-2 px-3.5 py-2">
                        <ShieldCheck className="mt-px size-3.5 shrink-0 text-warn" />
                        <span className="text-[13px] text-ink-2">{g}</span>
                      </div>
                    ))}
                  </div>
                </Panel>

                {activeRun ? (
                  <Panel eyebrow="Right now" title={`${activeRun.taskRef} · ${activeRun.step}`} className="lg:col-span-2">
                    <MeterRow label={`Worktree ${activeRun.worktree}`} pct={activeRun.progress} />
                    <div className="mt-2 grid grid-cols-2 gap-x-6">
                      <KV k="Elapsed" v={activeRun.elapsed} />
                      <KV k="Files touched" v={activeRun.filesTouched.length} />
                      <KV k="Tokens in" v={activeRun.tokensIn.toLocaleString()} />
                      <KV k="Tokens out" v={activeRun.tokensOut.toLocaleString()} />
                    </div>
                  </Panel>
                ) : (
                  <Panel className="lg:col-span-2"><Empty title={`${a.name} is ${a.status}`} hint="It will pick up the next task the orchestrator assigns to its speciality." /></Panel>
                )}
              </div>
            </div>
          </div>
        )}
      </PageBody>
    </Page>
  );
}

/* ── Live: derived from the runs, the router and the ledger ───── */

/** What a plan becomes, in the order services/runs.py does it. Static, and true. */
const CHAIN = [
  { what: 'A plan is split by the agent that owns each step', note: 'One agent: one run. Several: a run each, in a worktree and branch of its own, on lanes spread so they answer at the same time.' },
  { what: 'Each agent writes its steps', note: 'Through a lane good at writing. With no lane the step is skipped: no code is invented.' },
  { what: 'An integration run merges the branches', note: 'Only when several agents worked. Files that collide are reported, never half-applied.' },
  { what: 'The project\'s own tests run', note: 'The first time in a project, only after you approve the command (medium risk). Nothing else is ever executed.' },
  { what: 'The diff is reviewed', note: 'On a different lane from the one that wrote it when another is open; by rules when no lane answers, and the review says so.' },
  { what: 'You sign', note: 'High risk when the review found something high, the tests failed or branches collided; medium otherwise. Refuse and the branch and worktree are removed.' },
  { what: 'You merge', note: 'Into the branch your checkout is on, with the runs:merge permission, and an undo command recorded.' },
];

const CALLS_AS = { write: 'writes code', review: 'reviews diffs' } as const;
const finished = (s: string) => s === 'done' || s === 'skipped' || s === 'failed';

function LiveAgents() {
  const { runs: live } = useData();
  const roster = useRemote('agents', fetchRoster);
  const [sel, setSel] = useState<string | null>(null);
  const [view, setView] = useState<'roster' | 'org'>('roster');

  // Status and the current run follow the run stream: when any run or step moves, ask again. `reload`
  // keeps the last answer on screen, so a busy run does not blank the page.
  const moved = live.map((r) => `${r.ref}:${r.status}:${r.steps.filter((x) => finished(x.status)).length}`).join('|');
  const seen = useRef(moved);
  const { reload } = roster;
  useEffect(() => {
    if (seen.current === moved) return;
    seen.current = moved;
    reload();
  }, [moved, reload]);

  const list = roster.data?.agents ?? [];
  const a = list.find((x) => x.id === sel) ?? list[0];
  const count = (s: LiveAgentStatus) => list.filter((x) => x.status === s).length;

  return (
    <Page>
      <PageHeader
        title="Agents"
        subtitle="Who works on a run, which lane the router would give them now, and what their steps have really done. You are the only one who can approve."
        actions={<Segmented options={[{ id: 'roster', label: 'Roster' }, { id: 'org', label: 'How a run flows' }]} value={view} onChange={setView} />}
      >
        {roster.data && (
          <div className="flex flex-wrap items-center gap-2 pb-3">
            <Tag tone="ok"><Dot state="running" pulse={count('running') > 0} />{count('running')} running</Tag>
            <Tag tone="neutral">{count('idle')} idle</Tag>
            <Tag tone="warn">{count('waiting')} waiting</Tag>
            <span className="ml-2 text-[12.5px] text-dim">
              {roster.data.lanesOpen} {roster.data.lanesOpen === 1 ? 'lane' : 'lanes'} open now · {roster.data.worktreesOnDisk} {roster.data.worktreesOnDisk === 1 ? 'worktree' : 'worktrees'} on disk · one worktree per run, never shared
            </span>
          </div>
        )}
      </PageHeader>

      {roster.error ? (
        <PageBody><Empty title="The roster did not load" hint={roster.error} action={<Button size="sm" variant="outline" onClick={roster.reload}>Try again</Button>} /></PageBody>
      ) : !roster.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the roster…" /></PageBody>
      ) : view === 'org' ? (
        <PageBody>
          <div className="space-y-4">
            <Panel eyebrow="What the runtime does with a plan" title="Every change ends at your signature" flush>
              <ol className="divide-y divide-line/60">
                {CHAIN.map((c, i) => (
                  <li key={c.what} className="flex items-start gap-3 px-5 py-3">
                    <span className="tnum mt-px grid size-5 shrink-0 place-items-center rounded-full bg-surface-3 text-[11.5px] text-soft">{i + 1}</span>
                    <span className="min-w-0">
                      <span className="block text-[13.5px] font-medium text-ink">{c.what}</span>
                      <span className="block text-[13px] text-soft">{c.note}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </Panel>
            <Enforced rules={roster.data.enforced} />
          </div>
        </PageBody>
      ) : !a ? (
        <PageBody><Empty title="No agents in this workspace" hint="The roster is empty, so no plan step has anyone to name." /></PageBody>
      ) : (
        <PageBody className="flex h-full flex-col p-0">
          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
              {list.map((x) => {
                const lead = x.outcomes.find((o) => o.rate !== null);
                return (
                  <ListRow key={x.id} active={x.id === a.id} onClick={() => setSel(x.id)}>
                    <div className="flex items-center gap-2.5">
                      <span className={cn('grid size-6 shrink-0 place-items-center rounded-sm',
                        x.status === 'running' ? 'bg-brand/15 text-brand' : 'bg-surface-3 text-soft')}>
                        <Icon name={x.icon} className="size-3.5" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-[13.5px] font-medium text-ink">{x.name}</span>
                          <Dot state={x.status} pulse={x.status === 'running'} />
                        </span>
                        <span className="block truncate text-[11.5px] text-dim">{x.role}</span>
                      </span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      {x.lanes.primary ? <Mono>{x.lanes.primary.model}</Mono>
                        : <span className="text-[11.5px] text-dim">{x.callsAs ? 'no lane open' : 'no model call'}</span>}
                      {lead && <span className="ml-auto tnum text-[11.5px] text-dim">{lead.rate}%</span>}
                    </div>
                  </ListRow>
                );
              })}
            </div>
            <div className="min-w-0 flex-1 overflow-y-auto p-5">
              <AgentDetail a={a} enforced={roster.data.enforced} />
            </div>
          </div>
        </PageBody>
      )}
    </Page>
  );
}

function AgentDetail({ a, enforced }: { a: LiveAgent; enforced: string[] }) {
  const { lanes, current } = a;
  return (
    <>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand/12 text-brand">
            <Icon name={a.icon} className="size-4.5" />
          </span>
          <div>
            <h2 className="text-[17px] font-semibold text-ink">{a.name}</h2>
            <p className="text-[13.5px] text-soft">{a.role}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {a.callsAs && <Tag tone="brand">{CALLS_AS[a.callsAs]}</Tag>}
          <Tag tone={a.status === 'running' ? 'ok' : a.status === 'waiting' ? 'warn' : 'neutral'}><Dot state={a.status} pulse={a.status === 'running'} />{a.status}</Tag>
        </div>
      </div>

      <StatGrid cols={a.outcomes.length + 3} className="mb-4">
        <Stat label="Tasks done" value={a.tasksDone} sub="finished tasks that name it" />
        {a.outcomes.map((o) => (
          <Stat
            key={o.kind} label={o.label[0].toUpperCase() + o.label.slice(1)}
            value={o.rate === null ? '—' : `${o.rate}%`}
            tone={o.rate === null ? undefined : o.rate >= 90 ? 'ok' : 'warn'}
            sub={o.decided ? `${o.good} of ${o.decided}${o.avgMinutes === null ? '' : ` · ${o.avgMinutes}m each`}` : 'no step finished yet'}
          />
        ))}
        <Stat label="Tokens 24h" value={a.tokens24h.toLocaleString()} sub="from the usage ledger" />
        <Stat label="Cost 24h" value={a.cost24h === null ? 'unpriced' : a.cost24h === 0 ? 'free' : `$${a.cost24h.toFixed(2)}`} tone={a.cost24h === 0 ? 'ok' : a.cost24h === null ? undefined : 'brand'} sub={a.cost24h === null ? 'part of it ran on a lane with no declared price' : "at each lane's declared price"} />
      </StatGrid>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {current ? (
          <Panel
            eyebrow={current.status === 'waiting' ? 'Waiting for you' : 'Right now'}
            title={`${current.taskRef ?? current.runRef}${current.step ? ` · ${current.step}` : ''}`}
            actions={<Link to={`/runs?ref=${encodeURIComponent(current.runRef)}`} className="inline-flex items-center gap-1 text-[12.5px] text-brand hover:underline">Open {current.runRef}<ArrowUpRight className="size-3.5" /></Link>}
            className="lg:col-span-2"
          >
            <MeterRow label={`Branch ${current.branch}`} pct={current.progress} hint={current.worktree} />
            <div className="mt-2 grid grid-cols-1 gap-x-6 sm:grid-cols-2">
              <KV k="Started" v={ago(current.startedAt)} />
              <KV k="Files changed" v={current.filesChanged} />
              <KV k="Tokens in, this run" v={current.tokensIn.toLocaleString()} />
              <KV k="Tokens out, this run" v={current.tokensOut.toLocaleString()} />
            </div>
          </Panel>
        ) : (
          <Panel className="lg:col-span-2"><Empty title={`${a.name} is ${a.status}`} hint="It works when a dispatched plan gives it a step. Nothing here is started or stopped: runs live on Live Runs." /></Panel>
        )}

        <Panel eyebrow="Routing" title="Lanes">
          {a.callsAs ? (
            <>
              <KV k="Would try now" v={lanes.primary ? <Mono tone="brand">{lanes.primary.lane} · {lanes.primary.model}</Mono> : <span className="text-warn">no lane open</span>} />
              <KV k="Then" v={lanes.fallback ? <Mono>{lanes.fallback.lane} · {lanes.fallback.model}</Mono> : '—'} />
            </>
          ) : (
            <p className="py-2 text-[13px] text-soft">{a.noCall ?? 'The runtime makes no model call as this agent.'}</p>
          )}
          <KV k="Last answered" v={lanes.lastAnswered ? <span className="flex items-center gap-2"><Mono>{lanes.lastAnswered.lane} · {lanes.lastAnswered.model}</Mono><span className="text-[12px] text-dim">{ago(lanes.lastAnswered.at)}</span></span> : 'never'} />
          {a.callsAs && <p className="mt-2 text-[12.5px] text-dim">A snapshot: lanes rotate so agents working together start on different ones. Last answered is what really happened, from the ledger.</p>}
        </Panel>

        <Enforced rules={enforced} />

        <Panel eyebrow="Sent by the runtime" title={a.prompt ? `What every ${a.callsAs === 'review' ? 'review' : 'edit'} step is told` : 'No prompt'} className="lg:col-span-2">
          {a.prompt ? (
            <div className="space-y-3">
              <div>
                <SectionTitle className="mb-1.5">System</SectionTitle>
                <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">{a.prompt.system}</pre>
              </div>
              <div>
                <SectionTitle className="mb-1.5">Then, filled in for each step</SectionTitle>
                <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">{a.prompt.user}</pre>
              </div>
            </div>
          ) : (
            <p className="text-[13px] text-soft">{a.noCall ?? 'No model call is made as this agent, so nothing is sent.'}</p>
          )}
        </Panel>

        <Panel eyebrow="Declared on the agent · nothing in the runtime reads these yet" title="As written" className="lg:col-span-2">
          <KV k="Autonomy" v={<span className="text-soft">{a.declared.autonomy} <span className="text-dim">· every agent is gated the same way</span></span>} />
          {a.declared.tools.length > 0 && (
            <>
              <SectionTitle className="mt-3 mb-1.5">Tools</SectionTitle>
              <div className="flex flex-wrap gap-1">{a.declared.tools.map((t) => <Mono key={t}>{t}</Mono>)}</div>
            </>
          )}
          {a.declared.skills.length > 0 && (
            <>
              <SectionTitle className="mt-3 mb-1.5">Skills</SectionTitle>
              <div className="flex flex-wrap gap-1">{a.declared.skills.map((s) => <Mono key={s}>{s}</Mono>)}</div>
            </>
          )}
          {a.declared.guardrails.length > 0 && (
            <>
              <SectionTitle className="mt-3 mb-1.5">Guardrails</SectionTitle>
              <ul className="space-y-1">{a.declared.guardrails.map((g) => <li key={g} className="text-[13px] text-soft">{g}</li>)}</ul>
            </>
          )}
          {a.declared.systemPrompt && (
            <>
              <SectionTitle className="mt-3 mb-1.5">System prompt</SectionTitle>
              <p className="text-[13px] whitespace-pre-wrap text-soft">{a.declared.systemPrompt}</p>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}

function Enforced({ rules }: { rules: string[] }) {
  return (
    <Panel eyebrow="Enforced for every agent" title="What the runtime makes sure of" flush>
      <div className="divide-y divide-line/60">
        {rules.map((g) => (
          <div key={g} className="flex items-start gap-2 px-5 py-2.5">
            <ShieldCheck className="mt-px size-3.5 shrink-0 text-ok" />
            <span className="text-[13px] text-ink-2">{g}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
