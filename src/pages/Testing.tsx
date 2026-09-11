import { useMemo, useState } from 'react';
import { FlaskConical, ShieldQuestion, RotateCcw, EyeOff, FileCode } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Segmented, DataTable, Row, Cell,
  Stat, StatGrid, Bar, MeterRow, SectionTitle, StatusText,
} from '@/components/os';
import { suites, failures, coverage, runs, visualDiffs } from '@/mock/testing';
import { cn } from '@/lib/utils';

export default function Testing() {
  const [tab, setTab] = useState<'suites' | 'failures' | 'coverage' | 'history'>('suites');
  const [sel, setSel] = useState(failures[0].id);
  const f = useMemo(() => failures.find((x) => x.id === sel) ?? failures[0], [sel]);

  const totalPassed = suites.reduce((n, s) => n + s.passed, 0);
  const totalCases = suites.reduce((n, s) => n + s.total, 0);
  const duration = suites.reduce((n, s) => n + s.durationS, 0);
  const legacy = failures.filter((x) => x.legacyExpected).length;

  return (
    <Page>
      <PageHeader
        title="Testing"
        subtitle="No agent may claim a test passes. It runs the suite and attaches the runner output — and a documented legacy failure is information, not a defect."
        actions={<Button size="sm" onClick={() => toast('Full suite queued on the self-hosted runner')}><RotateCcw className="size-3.5" />Run all</Button>}
      >
        <div className="pb-3">
          <Segmented
            options={[
              { id: 'suites', label: `Suites (${suites.length})` },
              { id: 'failures', label: `Failures (${failures.length})` },
              { id: 'coverage', label: 'Coverage' },
              { id: 'history', label: `History (${runs.length})` },
            ]}
            value={tab} onChange={setTab} />
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Cases" value={totalCases.toLocaleString()} sub={`${totalPassed.toLocaleString()} passing`} icon={<FlaskConical className="size-3" />} />
          <Stat label="Pass rate" value={`${((totalPassed / totalCases) * 100).toFixed(1)}%`} tone="ok" sub="across every suite" />
          <Stat label="Real failures" value={failures.length - legacy} tone={failures.length - legacy ? 'warn' : 'ok'} sub="need a decision" />
          <Stat label="Legacy-expected" value={legacy} tone="neutral" sub="red on purpose" icon={<ShieldQuestion className="size-3" />} />
          <Stat label="Wall clock" value={`${Math.floor(duration / 60)}m ${duration % 60}s`} sub="full pipeline" />
        </StatGrid>

        {tab === 'suites' && (
          <>
            <Panel flush>
              <DataTable head={['Suite', 'Status', 'Passed', 'Duration', 'Runner']}>
                {suites.map((s) => (
                  <Row key={s.id}>
                    <Cell className="font-medium text-ink">{s.name}</Cell>
                    <Cell><StatusText state={s.status} /></Cell>
                    <Cell>
                      <span className="flex items-center gap-2">
                        <span className="tnum w-20 text-[13px]">{s.passed}/{s.total}</span>
                        <Bar className="w-28" pct={(s.passed / s.total) * 100} tone={s.status === 'pass' ? 'ok' : 'warn'} />
                      </span>
                    </Cell>
                    <Cell className="tnum">{s.durationS}s</Cell>
                    <Cell mono className="text-dim">{s.runner}</Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>

            {/* The signature panel */}
            <Panel className="border-warn/35 accent-left" eyebrow="Red on purpose — this is not a regression"
              title={<span className="flex items-center gap-2"><ShieldQuestion className="size-4 text-warn" />{failures[0].name}</span>}>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <div>
                  <SectionTitle>Reason</SectionTitle>
                  <p className="text-[13.5px] leading-relaxed text-ink-2">{failures[0].reason}</p>
                  <Mono className="mt-2 inline-block">{failures[0].file}:{failures[0].line}</Mono>
                </div>
                <div>
                  <SectionTitle>AI recommendation</SectionTitle>
                  <p className="rounded-sm border border-warn/30 bg-warn/8 p-2.5 text-[13.5px] leading-relaxed text-warn">
                    {failures[0].aiRecommendation}
                  </p>
                </div>
              </div>
              {failures[0].diff && <pre className="ascii mt-3 overflow-x-auto rounded-sm border border-line bg-base p-3">{failures[0].diff}</pre>}
            </Panel>

            <Panel eyebrow="Screenshot comparison against the stored baseline" title="Visual regression" flush>
              <div className="grid grid-cols-1 gap-3 p-3.5 md:grid-cols-2 xl:grid-cols-4">
                {visualDiffs.map((v) => (
                  <div key={v.id} className="rounded-sm border border-line bg-base p-2.5">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="truncate text-[12.5px] font-medium text-ink">{v.name}</span>
                      <span className={cn('tnum shrink-0 text-[12px]', v.diff === 0 ? 'text-ok' : v.diff > 10 ? 'text-danger' : 'text-warn')}>{v.diff}%</span>
                    </div>
                    <div className="grid grid-cols-2 gap-1.5">
                      {[['baseline', v.baseline], ['current', v.current]].map(([k, val]) => (
                        <div key={k} className="rounded-xs border border-line-strong">
                          <div className="grid-lines flex h-16 items-center justify-center px-1.5 text-center text-[11px] text-dim">{val}</div>
                          <div className="eyebrow border-t border-line px-1.5 py-1">{k}</div>
                        </div>
                      ))}
                    </div>
                    <p className="mt-1.5 truncate text-[11.5px] text-soft">{v.verdict}</p>
                  </div>
                ))}
              </div>
            </Panel>
          </>
        )}

        {tab === 'failures' && (
          <div className="flex min-h-[440px] flex-col gap-3 md:flex-row">
            <div className="w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] overflow-y-auto rounded-md border border-line bg-surface">
              {failures.map((x) => (
                <button key={x.id} onClick={() => setSel(x.id)}
                  className={cn('w-full border-l-2 px-3 py-2.5 text-left transition-colors',
                    x.id === f.id ? 'border-brand bg-brand/8' : 'border-transparent hover:bg-surface-2/70')}>
                  <div className="flex items-center gap-2">
                    <Dot state={x.legacyExpected ? 'warn' : 'error'} />
                    <span className="truncate text-[13px] font-medium text-ink">{x.suite}</span>
                    {x.legacyExpected && <Tag tone="warn" className="ml-auto">legacy</Tag>}
                  </div>
                  <p className="mt-1 line-clamp-2 font-mono text-[11.5px] text-dim">{x.name}</p>
                </button>
              ))}
            </div>
            <div className="min-w-0 flex-1 space-y-3">
              <Panel eyebrow={`${f.suite} · ${f.file}:${f.line}`} title={f.name}
                actions={f.legacyExpected ? <Tag tone="warn">expected</Tag> : <Tag tone="danger">real failure</Tag>}>
                <SectionTitle>Why it fails</SectionTitle>
                <p className="text-[13.5px] leading-relaxed text-ink-2">{f.reason}</p>
                <SectionTitle className="mt-3">Recommendation</SectionTitle>
                <p className={cn('rounded-sm border p-2.5 text-[13.5px] leading-relaxed',
                  f.legacyExpected ? 'border-warn/30 bg-warn/8 text-warn' : 'border-line bg-base text-ink-2')}>
                  {f.aiRecommendation}
                </p>
                {f.diff && <pre className="ascii mt-3 overflow-x-auto rounded-sm border border-line bg-base p-3">{f.diff}</pre>}
                <div className="mt-3 flex gap-1.5 border-t border-line pt-3">
                  <Button size="xs" variant="outline" onClick={() => toast(`Re-running ${f.name}`)}><RotateCcw className="size-3" />Re-run</Button>
                  <Button size="xs" variant="outline" onClick={() => toast('Quarantined — excluded from the merge gate, still reported')}><EyeOff className="size-3" />Quarantine</Button>
                  <Button size="xs" variant="ghost" onClick={() => toast(`Opening ${f.file}`)}><FileCode className="size-3" />Open file</Button>
                </div>
              </Panel>
            </div>
          </div>
        )}

        {tab === 'coverage' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel eyebrow="By layer · lines executed under test" title="Coverage">
              {coverage.map((c) => <MeterRow key={c.layer} label={c.layer} pct={c.pct} right={`${c.pct}% · ${c.lines}`} />)}
            </Panel>
            <Panel eyebrow="Honesty" title="What coverage does not tell you">
              <p className="text-[13.5px] leading-relaxed text-ink-2">
                Database procedures sit at 41% because most of them have never had a test written — not because the
                untested 59% is safe. <span className="text-ink">SP_CalculateTax is 412 lines with nine branches and
                twelve covering assertions.</span> Infrastructure at 22% is deliberate: the deploy scripts are verified by
                running them against staging, not by unit tests.
              </p>
              <p className="mt-3 border-t border-line pt-2.5 text-[12.5px] text-dim">
                The number the OS actually gates on is not coverage — it is whether the specific lines a change touched
                are covered. For TASK-492 that figure is 100%.
              </p>
            </Panel>
          </div>
        )}

        {tab === 'history' && (
          <Panel flush>
            <DataTable head={['Run', 'Triggered by', 'Branch', 'Duration', 'Pass rate', 'Commit', 'When']}>
              {runs.map((r) => (
                <Row key={r.id}>
                  <Cell mono className="text-brand">{r.ref}</Cell>
                  <Cell className="text-[12.5px]">{r.trigger}</Cell>
                  <Cell mono className="text-dim">{r.branch}</Cell>
                  <Cell className="tnum">{Math.floor(r.durationS / 60)}m {r.durationS % 60}s</Cell>
                  <Cell>
                    <span className="flex items-center gap-2">
                      <Bar className="w-20" pct={r.pass} tone={r.pass >= 99 ? 'ok' : r.pass >= 95 ? 'warn' : 'danger'} />
                      <span className={cn('tnum text-[12px]', r.pass >= 99 ? 'text-ok' : r.pass >= 95 ? 'text-warn' : 'text-danger')}>{r.pass}%</span>
                    </span>
                  </Cell>
                  <Cell mono className="text-dim">{r.commit}</Cell>
                  <Cell className="text-dim">{r.at}</Cell>
                </Row>
              ))}
            </DataTable>
          </Panel>
        )}
      </PageBody>
    </Page>
  );
}
