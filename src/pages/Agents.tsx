import { useMemo, useState } from 'react';
import { Bot, Check, ShieldCheck } from 'lucide-react';
import { ICONS } from '@/lib/icons';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, ListRow, Stat, StatGrid,
  KV, MeterRow, SectionTitle, Empty, Segmented,
} from '@/components/os';
import { agents } from '@/mock/agents';
import { runs } from '@/mock/runs';
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

export default function Agents() {
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
          <span className="ml-2 text-[11.5px] text-dim">
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
                  <p className="text-[12px] text-soft">{e.what}</p>
                  <div className="mt-2 border-t border-line pt-2">
                    <Tag tone={e.tone}>{e.who}</Tag>
                  </div>
                </Panel>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1">
            {/* Roster */}
            <div className="no-scrollbar w-[300px] shrink-0 overflow-y-auto border-r border-line">
              {agents.map((x) => (
                <ListRow key={x.id} active={x.id === sel} onClick={() => setSel(x.id)}>
                  <div className="flex items-center gap-2.5">
                    <span className={cn('grid size-6 shrink-0 place-items-center rounded-sm',
                      x.status === 'running' ? 'bg-brand/15 text-brand' : 'bg-surface-3 text-soft')}>
                      <Icon name={x.icon} className="size-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-[12.5px] font-medium text-ink">{x.name}</span>
                        <Dot state={x.status} pulse={x.status === 'running'} />
                      </span>
                      <span className="block truncate text-[10.5px] text-dim">{x.role}</span>
                    </span>
                  </div>
                  <div className="mt-1.5 flex items-center gap-2">
                    <Mono>{x.model}</Mono>
                    <span className="ml-auto tnum text-[10.5px] text-dim">{x.successRate}%</span>
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
                    <p className="text-[12.5px] text-soft">{a.role}</p>
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
                  <p className="mt-2 text-[11.5px] text-dim">{AUTONOMY[a.autonomy].note}</p>
                </Panel>

                <Panel eyebrow="Capabilities" title="Tools granted">
                  <div className="flex flex-wrap gap-1.5">
                    {a.tools.map((t) => (
                      <span key={t} className="inline-flex items-center gap-1 rounded-xs border border-ok/25 bg-ok/8 px-1.5 py-px text-[11px] text-ok">
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
                        <span className="text-[12px] text-ink-2">{g}</span>
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
