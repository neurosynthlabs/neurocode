import { useMemo, useState } from 'react';
import { Gauge, TrendingDown, TrendingUp, Play, Scale } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, ListRow, DataTable, Row, Cell,
  Stat, StatGrid, Trend, Sparkline, Ring, Empty, Segmented,
} from '@/components/os';
import { evalSuites, evalCases, evalTrend, lessons } from '@/mock/workflows';
import { cn } from '@/lib/utils';

const KIND_TONE = { regression: 'warn', capability: 'brand', safety: 'ok', cost: 'violet' } as const;
const CASE_TONE = { pass: 'ok', fail: 'danger', partial: 'warn' } as const;

const LOOP = `  TASK ──▶ RESULT ──▶ EVALUATION ──▶ FAILURE ANALYSIS ──▶ MEMORY ──▶ FUTURE IMPROVEMENT
                          │                                        │
                          └─────────── judge + human ◀─────────────┘
   every failure becomes a lesson · every lesson becomes a rule or a skill change`;

export default function Evals() {
  const [sel, setSel] = useState('e1');
  const [kind, setKind] = useState<'all' | 'regression' | 'capability' | 'safety' | 'cost'>('all');

  const list = useMemo(() => evalSuites.filter((s) => kind === 'all' || s.kind === kind), [kind]);
  const s = useMemo(() => evalSuites.find((x) => x.id === sel) ?? evalSuites[0], [sel]);
  const cases = evalCases[s.id] ?? [];
  const avg = Math.round(evalSuites.reduce((n, x) => n + x.score, 0) / evalSuites.length);
  const regressions = evalSuites.filter((x) => x.delta < 0);

  return (
    <Page>
      <PageHeader
        title="Evals"
        subtitle="Does the OS actually get better? Every agent is scored against fixed cases nightly — and regressions are reported as loudly as wins."
        actions={<Button size="sm" onClick={() => toast('All 10 suites queued · ~14 min')}><Play className="size-3.5" />Run all suites</Button>}
      >
        <div className="pb-3">
          <Segmented
            options={[{ id: 'all', label: 'All' }, { id: 'capability', label: 'Capability' }, { id: 'regression', label: 'Regression' }, { id: 'safety', label: 'Safety' }, { id: 'cost', label: 'Cost' }]}
            value={kind} onChange={setKind} />
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Suites" value={evalSuites.length} icon={<Gauge className="size-3" />} />
          <Stat label="Cases" value={evalSuites.reduce((n, x) => n + x.cases, 0)} sub={`${evalSuites.reduce((n, x) => n + x.passed, 0)} passing`} />
          <Stat label="Mean score" value={avg} tone="brand" sub="across all suites" />
          <Stat label="Regressions" value={regressions.length} tone="danger" sub={regressions.map((r) => r.name.split('-')[0]).join(' · ')} icon={<TrendingDown className="size-3" />} />
          <Stat label="Improved" value={evalSuites.filter((x) => x.delta > 0).length} tone="ok" sub="vs last run" icon={<TrendingUp className="size-3" />} />
        </StatGrid>

        <Panel eyebrow="How a failure turns into a better agent" title="Self-improvement loop">
          <Ascii>{LOOP}</Ascii>
        </Panel>

        <div className="flex min-h-[520px] gap-3">
          <div className="no-scrollbar w-[330px] shrink-0 overflow-y-auto rounded-md border border-line bg-surface">
            {list.map((x) => (
              <ListRow key={x.id} active={x.id === s.id} onClick={() => setSel(x.id)}>
                <div className="flex items-center gap-2">
                  <Dot state={x.status} />
                  <Mono tone={x.id === s.id ? 'brand' : 'neutral'}>{x.name}</Mono>
                </div>
                <div className="mt-1.5 flex items-center gap-3">
                  <span className="figure text-[17px] text-ink">{x.score}</span>
                  <Trend value={x.delta} />
                  <span className="ml-auto"><Sparkline points={evalTrend[x.id] ?? []} tone={x.delta < 0 ? 'danger' : 'ok'} /></span>
                </div>
                <p className="mt-1 text-[10.5px] text-dim">{x.target} · {x.passed}/{x.cases}</p>
              </ListRow>
            ))}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel className={cn(s.delta < 0 && 'border-danger/35 accent-left')}
              eyebrow={`${s.target} · last run ${s.lastRun}`}
              title={<span className="flex items-center gap-2"><Mono tone="brand">{s.name}</Mono><Tag tone={KIND_TONE[s.kind]}>{s.kind}</Tag></span>}
              actions={<Trend value={s.delta} suffix=" pts" />}>
              <div className="flex items-center gap-5">
                <Ring pct={s.score} size={64} tone={s.status === 'fail' ? 'danger' : s.status === 'warn' ? 'warn' : 'ok'} />
                <div className="grid flex-1 grid-cols-3 gap-3">
                  <div><div className="figure text-[20px] text-ink">{s.passed}</div><div className="eyebrow mt-1">passed</div></div>
                  <div><div className="figure text-[20px] text-ink">{s.cases - s.passed}</div><div className="eyebrow mt-1">failed</div></div>
                  <div><Sparkline points={evalTrend[s.id] ?? []} width={120} height={32} tone={s.delta < 0 ? 'danger' : 'ok'} /><div className="eyebrow mt-1">7-run trend</div></div>
                </div>
              </div>
              {s.delta < 0 && (
                <p className="mt-3 border-t border-line pt-2.5 text-[11.5px] text-danger">
                  Regressed {Math.abs(s.delta)} points since the last run. Reported, not hidden — the failure analysis below is what feeds the next lesson.
                </p>
              )}
            </Panel>

            <Panel eyebrow={`${cases.length} cases shown`} title="Cases" flush>
              {cases.length === 0 ? (
                <Empty title="Case detail not captured for this suite" hint="Only suites with a recent regression keep per-case traces. Pick reviewer-strictness or test-generation-quality." />
              ) : (
                <DataTable head={['Case', 'Expected', 'Got', 'Status', 'Δ', 'Judge']}>
                  {cases.map((c) => (
                    <Row key={c.id} className={cn(c.judge.startsWith('Human') && 'bg-warn/5')}>
                      <Cell className="max-w-[240px] text-ink">{c.name}</Cell>
                      <Cell className="text-[11.5px] text-soft">{c.expected}</Cell>
                      <Cell className={cn('text-[11.5px]', c.status === 'pass' ? 'text-ok' : c.status === 'fail' ? 'text-danger' : 'text-warn')}>{c.got}</Cell>
                      <Cell><Tag tone={CASE_TONE[c.status]}>{c.status}</Tag></Cell>
                      <Cell className="tnum">{c.scoreDelta === 0 ? '—' : c.scoreDelta}</Cell>
                      <Cell className="max-w-[260px] text-[11px] text-dim">
                        {c.judge.startsWith('Human') ? <span className="flex items-start gap-1 text-warn"><Scale className="mt-px size-3 shrink-0" />{c.judge}</span> : c.judge}
                      </Cell>
                    </Row>
                  ))}
                </DataTable>
              )}
            </Panel>
          </div>
        </div>

        <Panel eyebrow="Written back into global memory after a failure" title="Lessons learned" flush>
          <div className="divide-y divide-line">
            {lessons.map((l, i) => (
              <div key={i} className="flex items-start gap-3 px-3.5 py-2.5">
                <span className="w-20 shrink-0 text-[11px] text-dim">{l.at}</span>
                <Mono className="shrink-0">{l.from}</Mono>
                <span className="min-w-0 flex-1 text-[12px] text-ink-2">{l.text}</span>
              </div>
            ))}
          </div>
        </Panel>
      </PageBody>
    </Page>
  );
}
