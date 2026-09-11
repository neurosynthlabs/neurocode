import { useMemo, useState } from 'react';
import { Workflow, Play, Search, Layers, Gauge } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, ListRow, Field, Segmented,
  DataTable, Row, Cell, Stat, StatGrid, Bar, Ascii, KV, Empty, SectionTitle,
} from '@/components/os';
import { workflows, liveRun, wfHistory, patterns, scripts } from '@/mock/workflows';
import { cn } from '@/lib/utils';
import type { WorkflowDef } from '@/types';

const MODE_TONE = { parallel: 'brand', pipeline: 'violet', single: 'neutral', loop: 'warn' } as const;
const RES_TONE = { success: 'ok', failed: 'danger', partial: 'warn' } as const;

/** Draw the phase graph so the fan-out shape is obvious at a glance. */
function shape(w: WorkflowDef) {
  const lines: string[] = [];
  w.phases.forEach((p, i) => {
    const n = Math.max(p.agents, 1);
    const tag = `${String(i + 1).padStart(2)} ${p.title.toUpperCase()}  ·  ${p.mode}${p.agents ? ` × ${p.agents}` : ' · code'}`;
    if (p.mode === 'parallel' && n > 1) {
      lines.push(`   ┌${'─'.repeat(n * 6 - 1)}┐  ${tag}`);
      lines.push(`   ${Array.from({ length: n }, () => '│ ●  ').join('')}│`);
      lines.push(`   └${'─'.repeat(Math.floor((n * 6 - 1) / 2))}┬${'─'.repeat(Math.ceil((n * 6 - 1) / 2) - 1)}┘`);
    } else if (p.mode === 'loop') {
      lines.push(`   ┌── ● ──┐ ↺   ${tag}`);
      lines.push(`   └───┬───┘`);
    } else if (p.mode === 'pipeline') {
      lines.push(`   ${Array.from({ length: n }, () => '●').join(' ─▶ ')}      ${tag}`);
      lines.push(`       │`);
    } else {
      lines.push(`   ●             ${tag}`);
      lines.push(`   │`);
    }
  });
  lines.push('   ▼');
  lines.push('   DONE');
  return lines.join('\n');
}

export default function Workflows() {
  const [q, setQ] = useState('');
  const [scope, setScope] = useState<'all' | 'global' | 'project'>('all');
  const [sel, setSel] = useState(workflows[1].id);
  const [tab, setTab] = useState<'graph' | 'script'>('graph');

  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return workflows.filter((w) =>
      (scope === 'all' || w.scope === scope) &&
      (!t || (w.name + w.description + w.trigger).toLowerCase().includes(t)));
  }, [q, scope]);

  const w = useMemo(() => list.find((x) => x.id === sel) ?? list[0] ?? workflows[0], [list, sel]);
  const budgetPct = Math.round((liveRun.tokensSpent / liveRun.tokensBudget) * 100);

  return (
    <Page>
      <PageHeader
        title="Workflows"
        subtitle="Deterministic multi-agent orchestration. The control flow is code — fan out, verify, synthesise — so the shape of the work does not depend on a model's mood."
        actions={<Button size="sm" onClick={() => toast.success(`${w.name} launched`, { description: `${w.phases.length} phases · ~${w.avgAgents} agents` })}><Play className="size-3.5" />Run {w.name}</Button>}
      />

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Workflows" value={workflows.length} icon={<Workflow className="size-3" />} />
          <Stat label="Runs total" value={workflows.reduce((n, x) => n + x.runs, 0)} />
          <Stat label="Live now" value={1} tone="ok" sub={liveRun.workflow} />
          <Stat label="Agents in flight" value={liveRun.running} tone="brand" sub={`cap ${liveRun.concurrencyCap} · ${liveRun.queued} queued`} icon={<Layers className="size-3" />} />
          <Stat label="Budget used" value={`${budgetPct}%`} tone={budgetPct > 80 ? 'warn' : 'neutral'} sub={`${(liveRun.tokensSpent / 1e6).toFixed(2)}M of ${(liveRun.tokensBudget / 1e6).toFixed(1)}M`} icon={<Gauge className="size-3" />} />
        </StatGrid>

        {/* Live run */}
        <Panel className="accent-top" eyebrow={`${liveRun.ref} · running ${liveRun.elapsed}`}
          title={<span className="flex items-center gap-2"><Dot state="running" pulse />{liveRun.workflow}<Tag tone="brand">phase · {liveRun.phase}</Tag></span>}
          actions={<span className="text-[11px] text-dim">{liveRun.completed} done · {liveRun.running} running · {liveRun.queued} queued</span>} flush>
          <DataTable head={['Agent', 'Phase', 'State', 'Elapsed', 'Tokens']}>
            {liveRun.agents.map((a) => (
              <Row key={a.label} className={cn(a.state === 'active' && 'sweep')}>
                <Cell mono className="text-ink">{a.label}</Cell>
                <Cell><Tag tone="neutral">{a.phase}</Tag></Cell>
                <Cell><span className="flex items-center gap-1.5"><Dot state={a.state === 'active' ? 'running' : a.state === 'done' ? 'done' : 'todo'} pulse={a.state === 'active'} />{a.state}</span></Cell>
                <Cell className="tnum">{a.elapsed}</Cell>
                <Cell className="tnum">{a.tokens ? `${(a.tokens / 1000).toFixed(0)}k` : '—'}</Cell>
              </Row>
            ))}
          </DataTable>
          <div className="grid grid-cols-1 gap-3 border-t border-line px-3.5 py-2.5 md:grid-cols-2">
            <div>
              <div className="mb-1 flex justify-between text-[11px]"><span className="text-dim">token budget</span><span className="tnum text-soft">{budgetPct}%</span></div>
              <Bar pct={budgetPct} tone={budgetPct > 80 ? 'warn' : 'brand'} />
            </div>
            <p className="text-[11px] text-dim"><span className="text-ink-2">No silent caps.</span> {liveRun.dropped}</p>
          </div>
        </Panel>

        <div className="flex min-h-[560px] gap-3">
          {/* Library */}
          <div className="flex w-[310px] shrink-0 flex-col overflow-hidden rounded-md border border-line bg-surface">
            <div className="space-y-2 border-b border-line p-2.5">
              <Field value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search workflows…" onClear={() => setQ('')} />
              <Segmented className="w-full" options={[{ id: 'all', label: 'All' }, { id: 'global', label: 'Global' }, { id: 'project', label: 'Project' }]} value={scope} onChange={setScope} />
            </div>
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              {list.length === 0 ? <Empty title="No workflow matches" /> : list.map((x) => (
                <ListRow key={x.id} active={x.id === w.id} onClick={() => setSel(x.id)}>
                  <div className="flex items-center gap-2">
                    <Dot state={x.lastResult === 'success' ? 'ok' : x.lastResult === 'partial' ? 'warn' : 'error'} />
                    <Mono tone={x.id === w.id ? 'brand' : 'neutral'}>{x.name}</Mono>
                  </div>
                  <p className="mt-1 truncate text-[10.5px] text-dim">{x.trigger}</p>
                  <div className="mt-1 flex items-center gap-3 text-[10.5px] text-dim">
                    <span>{x.runs} runs</span><span>~{x.avgAgents} agents</span><span>~{x.avgMinutes}m</span>
                    <span className="ml-auto">{x.lastRun}</span>
                  </div>
                </ListRow>
              ))}
            </div>
          </div>

          {/* Detail */}
          <div className="min-w-0 flex-1 space-y-3">
            <Panel eyebrow={`${w.scope} · ${w.trigger}`}
              title={<span className="flex items-center gap-2"><Mono tone="brand">{w.name}</Mono><Tag tone={RES_TONE[w.lastResult]}>last: {w.lastResult}</Tag></span>}
              actions={<Segmented options={[{ id: 'graph', label: 'Phase graph' }, { id: 'script', label: 'Script' }]} value={tab} onChange={setTab} />}>
              <p className="text-[12.5px] leading-relaxed text-ink-2">{w.description}</p>
              <div className="mt-3 grid grid-cols-2 gap-x-6 border-t border-line pt-2.5 md:grid-cols-4">
                <KV k="Runs" v={w.runs} />
                <KV k="Avg agents" v={w.avgAgents} />
                <KV k="Avg duration" v={`${w.avgMinutes}m`} />
                <KV k="Last run" v={w.lastRun} />
              </div>
            </Panel>

            {tab === 'graph' ? (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                <Panel eyebrow="The shape of the work" title="Fan-out">
                  <Ascii className="overflow-auto">{shape(w)}</Ascii>
                </Panel>
                <Panel eyebrow={`${w.phases.length} phases`} title="Phases" flush>
                  <div className="divide-y divide-line">
                    {w.phases.map((p, i) => (
                      <div key={p.id} className="flex items-start gap-2.5 px-3.5 py-2.5">
                        <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[10.5px] text-dim">{i + 1}</span>
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <span className="text-[12.5px] font-medium text-ink">{p.title}</span>
                            <Tag tone={MODE_TONE[p.mode]}>{p.mode}</Tag>
                            <span className="text-[10.5px] text-dim">{p.agents ? `${p.agents} agent${p.agents > 1 ? 's' : ''}` : 'plain code'}</span>
                          </span>
                          <span className="mt-0.5 block text-[11.5px] text-soft">{p.detail}</span>
                        </span>
                        <Dot state={p.state === 'active' ? 'running' : p.state} pulse={p.state === 'active'} className="mt-1.5" />
                      </div>
                    ))}
                  </div>
                </Panel>
              </div>
            ) : (
              <Panel eyebrow="Read-only · the control flow is code, not a prompt" title={`${w.name}.js`}>
                <pre className="ascii max-h-[440px] overflow-auto rounded-sm border border-line bg-base p-3.5">
                  {scripts[w.id] ?? `// ${w.name}\n${w.phases.map((p) => `phase('${p.title}')\n// ${p.mode}${p.agents ? ` · ${p.agents} agent(s)` : ''} — ${p.detail}`).join('\n\n')}`}
                </pre>
              </Panel>
            )}
          </div>
        </div>

        <div className="grid grid-cols-12 gap-3">
          <Panel className="col-span-12 xl:col-span-8" eyebrow="Last 12 runs" title="History" flush>
            <DataTable head={['Workflow', 'Trigger', 'Agents', 'Duration', 'Tokens', 'Cost', 'Result', 'When']}>
              {wfHistory.map((h) => (
                <Row key={h.id}>
                  <Cell mono className="text-ink">{h.workflow}</Cell>
                  <Cell className="text-[11.5px]">{h.trigger}</Cell>
                  <Cell className="tnum">{h.agents}</Cell>
                  <Cell className="tnum">{Math.floor(h.durationS / 60)}m {h.durationS % 60}s</Cell>
                  <Cell className="tnum">{(h.tokens / 1e6).toFixed(2)}M</Cell>
                  <Cell className={cn('tnum', h.cost === 0 ? 'text-ok' : 'text-ink')}>{h.cost === 0 ? 'free' : `$${h.cost.toFixed(2)}`}</Cell>
                  <Cell><Tag tone={RES_TONE[h.result as keyof typeof RES_TONE]}>{h.result}</Tag></Cell>
                  <Cell className="text-dim">{h.at}</Cell>
                </Row>
              ))}
            </DataTable>
          </Panel>
          <div className="col-span-12 space-y-2 xl:col-span-4">
            <SectionTitle>Quality patterns</SectionTitle>
            {patterns.map((p) => (
              <Panel key={p.name} className="hover-lift" title={p.name}>
                <p className="text-[11.5px] leading-relaxed text-soft">{p.note}</p>
              </Panel>
            ))}
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
