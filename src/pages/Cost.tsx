import { useMemo, useState } from 'react';
import { Coins, TriangleAlert, HardDrive } from 'lucide-react';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Segmented, DataTable, Row, Cell,
  Stat, StatGrid, Bar, KV, Ring, SectionTitle,
} from '@/components/os';
import { costDays, byAgent, byModel, byProject, budget, expensiveOps, savings, savingsTotals } from '@/mock/cost';
import { cn } from '@/lib/utils';

const W = 560, H = 116;

export default function Cost() {
  const [split, setSplit] = useState<'agent' | 'model' | 'project'>('agent');
  const [hover, setHover] = useState<number | null>(null);

  const buckets = split === 'agent' ? byAgent : split === 'model' ? byModel : byProject;
  const max = Math.max(...costDays.map((d) => d.cost));
  const bw = (W - (costDays.length - 1) * 4) / costDays.length;
  const day = hover !== null ? costDays[hover] : null;
  const pctUsed = Math.round((budget.spent / budget.daily) * 100);

  const totalTokens = useMemo(() => buckets.reduce((n, b) => n + b.tokensIn + b.tokensOut, 0), [buckets]);

  return (
    <Page>
      <PageHeader
        title="Cost & Usage"
        subtitle="What the fleet actually costs, where it goes, and an honest account of what running models locally does and does not save."
      />

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Spent today" value={`$${budget.spent.toFixed(2)}`} tone={pctUsed > 80 ? 'warn' : 'brand'} sub={`${pctUsed}% of $${budget.daily.toFixed(2)}`} icon={<Coins className="size-3" />} />
          <Stat label="This month" value={`$${budget.monthSpent.toFixed(2)}`} sub={`${budget.monthDays} days elapsed`} />
          <Stat label="Projected" value={`$${budget.projectedMonth.toFixed(2)}`} sub={`last month $${budget.lastMonth.toFixed(2)}`} tone={budget.projectedMonth < budget.lastMonth ? 'ok' : 'warn'} />
          <Stat label="If all frontier" value={`$${savingsTotals.ifAllFrontier.toFixed(2)}`} tone="danger" sub="same work, one big model" />
          <Stat label="Local share" value="71%" tone="ok" sub="of all calls today" icon={<HardDrive className="size-3" />} />
        </StatGrid>

        <div className="grid grid-cols-12 gap-3">
          <Panel className="col-span-12 xl:col-span-8" eyebrow="14 days · local vs remote" title="Daily spend"
            actions={day ? <span className="text-[12.5px] text-soft"><Mono>{day.day}</Mono> ${day.cost.toFixed(2)} · remote ${day.remote.toFixed(2)}</span> : <span className="text-[12px] text-dim">hover a bar</span>}>
            <svg width={W} height={H} className="w-full" viewBox={`0 0 ${W} ${H}`} onMouseLeave={() => setHover(null)}>
              {[0.25, 0.5, 0.75, 1].map((f) => (
                <line key={f} x1="0" y1={H - 18 - f * (H - 26)} x2={W} y2={H - 18 - f * (H - 26)} stroke="var(--os-line)" strokeDasharray="2 4" />
              ))}
              {costDays.map((d, i) => {
                const x = i * (bw + 4);
                const total = (d.cost / max) * (H - 26);
                const local = (d.local / max) * (H - 26);
                const on = hover === i;
                return (
                  <g key={d.day} onMouseEnter={() => setHover(i)}>
                    <rect x={x} y={0} width={bw} height={H} fill="transparent" />
                    <rect x={x} y={H - 18 - total} width={bw} height={total - local} rx="2"
                      fill="var(--os-brand)" opacity={on ? 1 : 0.78} />
                    {local > 0 && <rect x={x} y={H - 18 - local} width={bw} height={local} rx="2" fill="var(--os-ok)" opacity={on ? 1 : 0.78} />}
                    <text x={x + bw / 2} y={H - 5} fontSize="8.5" textAnchor="middle" fill={on ? 'var(--os-ink)' : 'var(--os-dim)'}>
                      {d.day.split(' ')[1]}
                    </text>
                  </g>
                );
              })}
            </svg>
            <div className="mt-2 flex items-center gap-4 border-t border-line pt-2 text-[11.5px] text-dim">
              <span className="flex items-center gap-1.5"><span className="size-2 rounded-xs bg-brand" />remote API</span>
              <span className="flex items-center gap-1.5"><span className="size-2 rounded-xs bg-ok" />local inference</span>
              <span className="ml-auto">the drop on Sep 04 is the day the local coder model came online</span>
            </div>
          </Panel>

          <Panel className="col-span-12 xl:col-span-4" eyebrow="Guard rail" title="Daily budget">
            <div className="flex items-center gap-4">
              <Ring pct={pctUsed} size={64} tone={pctUsed > 80 ? 'warn' : 'brand'} />
              <div className="min-w-0 flex-1">
                <div className="figure text-[24px] text-ink">${budget.spent.toFixed(2)}</div>
                <div className="text-[12.5px] text-dim">of ${budget.daily.toFixed(2)} · ${(budget.daily - budget.spent).toFixed(2)} left</div>
              </div>
            </div>
            <Bar className="mt-3" pct={pctUsed} tone={pctUsed > 80 ? 'warn' : 'brand'} height="h-1.5" />
            <div className="mt-3 flex items-start gap-2 rounded-sm border border-warn/30 bg-warn/8 px-2.5 py-2">
              <TriangleAlert className="mt-px size-3.5 shrink-0 text-warn" />
              <p className="text-[12.5px] text-warn">
                At {budget.guard}% of the daily budget the router stops offering remote models entirely and serves
                everything locally. Quality drops on hard reasoning; nothing stops.
              </p>
            </div>
          </Panel>
        </div>

        <div className="flex items-center gap-2">
          <Segmented options={[{ id: 'agent', label: 'By agent' }, { id: 'model', label: 'By model' }, { id: 'project', label: 'By project' }]} value={split} onChange={setSplit} />
          <span className="ml-auto text-[12.5px] text-dim">{(totalTokens / 1_000_000).toFixed(1)}M tokens across {buckets.length} buckets</span>
        </div>

        <Panel flush>
          <DataTable head={[split === 'agent' ? 'Agent' : split === 'model' ? 'Model' : 'Project', 'Tokens in', 'Tokens out', 'Calls', 'Cost', 'Share']}>
            {buckets.map((b) => (
              <Row key={b.label}>
                <Cell className="font-medium text-ink">{b.label}</Cell>
                <Cell className="tnum">{(b.tokensIn / 1000).toFixed(0)}k</Cell>
                <Cell className="tnum">{(b.tokensOut / 1000).toFixed(0)}k</Cell>
                <Cell className="tnum">{b.calls.toLocaleString()}</Cell>
                <Cell className={cn('tnum', b.cost === 0 ? 'text-ok' : 'text-ink')}>{b.cost === 0 ? 'free' : `$${b.cost.toFixed(2)}`}</Cell>
                <Cell><span className="flex items-center gap-2"><Bar className="w-24" pct={b.share} /><span className="tnum text-[12px]">{b.share}%</span></span></Cell>
              </Row>
            ))}
          </DataTable>
        </Panel>

        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <Panel eyebrow="An honest comparison, not a marketing claim" title="What local inference saves" flush>
            <DataTable head={['Task class', 'Local calls', 'Local $', 'Frontier $', 'Quality Δ']}>
              {savings.map((s) => (
                <Row key={s.label}>
                  <Cell className="font-medium text-ink">{s.label}
                    <span className="mt-0.5 block max-w-[300px] text-[12px] font-normal text-dim">{s.honest}</span>
                  </Cell>
                  <Cell className="tnum">{s.localCalls.toLocaleString()}</Cell>
                  <Cell className="tnum text-ok">${s.localCost.toFixed(2)}</Cell>
                  <Cell className="tnum text-danger">${s.frontierCost.toFixed(2)}</Cell>
                  <Cell className="text-[12.5px] text-soft">{s.qualityDelta}</Cell>
                </Row>
              ))}
            </DataTable>
            <div className="border-t border-line px-3.5 py-2.5">
              <KV k="Actual today" v={<span className="text-ok">${savingsTotals.actualToday.toFixed(2)}</span>} />
              <KV k="If everything went frontier" v={<span className="text-danger">${savingsTotals.ifAllFrontier.toFixed(2)}</span>} />
              <p className="mt-2 text-[12px] text-dim">{savingsTotals.note}</p>
            </div>
          </Panel>

          <Panel eyebrow="Today's most expensive work" title="Top operations" flush>
            <DataTable head={['Operation', 'Task', 'Model', 'Tokens', 'Cost']}>
              {expensiveOps.map((o) => (
                <Row key={o.id}>
                  <Cell className="font-medium text-ink">{o.op}
                    <span className="mt-0.5 block text-[12px] font-normal text-dim">{o.agent} · {o.at}</span>
                  </Cell>
                  <Cell><Mono>{o.task}</Mono></Cell>
                  <Cell className="text-[12.5px]">{o.model}</Cell>
                  <Cell className="tnum">{((o.tokensIn + o.tokensOut) / 1000).toFixed(0)}k</Cell>
                  <Cell className={cn('tnum', o.cost === 0 ? 'text-ok' : 'text-ink')}>{o.cost === 0 ? 'free' : `$${o.cost.toFixed(2)}`}</Cell>
                </Row>
              ))}
            </DataTable>
          </Panel>
        </div>

        <SectionTitle>Where the money actually goes</SectionTitle>
        <Panel>
          <p className="max-w-4xl text-[13.5px] leading-relaxed text-ink-2">
            Reasoning and review are the only places a frontier model earns its price — architecture decisions, legacy risk
            calls and the final code review. Coding, extraction, summarising and embedding all sit within a point or two of
            frontier quality on local weights, and they are the overwhelming majority of calls. That is the whole trade:
            spend on judgement, never on typing.
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Tag tone="violet">reasoning → remote</Tag>
            <Tag tone="brand">review → remote</Tag>
            <Tag tone="ok">coding → local</Tag>
            <Tag tone="ok">extraction → local</Tag>
            <Tag tone="ok">embeddings → local</Tag>
            <Tag tone="ok">reranking → local</Tag>
          </div>
        </Panel>
      </PageBody>
    </Page>
  );
}
