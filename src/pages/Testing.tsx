import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FlaskConical, ShieldQuestion, RotateCcw, EyeOff, FileCode, Loader2, Play, ScrollText, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Segmented, DataTable, Row, Cell,
  Stat, StatGrid, Bar, MeterRow, SectionTitle, StatusText, Empty,
} from '@/components/os';
import { suites, failures, coverage, runs, visualDiffs } from '@/mock/testing';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { testing, type TestExpectationLive, type TestFailureLive, type TestHistoryLine, type TestingReport, type TestSuiteLive } from '@/lib/live/testing';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ago } from '@/pages/code/format';

/** Live: each onboarded project's own test command and what it printed. The demo keeps the worked example. */
export default function Testing() {
  const { mode } = useData();
  return mode === 'live' ? <LiveTesting /> : <SampleTesting />;
}

function SampleTesting() {
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

/* ── live ─────────────────────────────────────────────────────── */

type Tab = 'suites' | 'failures' | 'coverage' | 'history';

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const seconds = (ms: number | null) => (ms === null ? '—' : ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`);
const rate = (passed: number | null, total: number | null) => (passed !== null && total ? (100 * passed) / total : null);
const rateTone = (pct: number) => (pct >= 99 ? 'ok' : pct >= 95 ? 'warn' : 'danger');
const RATE_TEXT = { ok: 'text-ok', warn: 'text-warn', danger: 'text-danger' } as const;
const KIND_WORD: Record<TestExpectationLive['kind'], string> = { legacy: 'legacy', quarantine: 'quarantined' };

function LiveTesting() {
  const nav = useNavigate();
  const { can } = useAuth();
  const { runs } = useData();
  const { project, all, setProjectId } = useProject();
  const [tab, setTab] = useState<Tab>('suites');
  const [picked, setPicked] = useState<number | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const report = useRemote('testing', testing.report);

  // Runs stream in as they change: when one starts, pauses or finishes, read the report again.
  const signature = runs.map((r) => `${r.ref}:${r.status}:${r.tests.status}`).join('|');
  const { reload } = report;
  const seen = useRef(signature);
  useEffect(() => {
    if (seen.current === signature) return;
    seen.current = signature;
    reload();
  });

  const data = report.data;
  const start = async (projectId: string, name: string) => {
    setStarting(projectId);
    try {
      const run = await testing.run(projectId);
      toast(`Testing ${name}`, {
        description: `${run.ref} · a throwaway worktree at ${run.shortBase}. The first run in a project waits for your approval.`,
        action: { label: 'Open run', onClick: () => nav(`/runs?ref=${encodeURIComponent(run.ref)}`) },
      });
      reload();
    } catch (e) {
      toast.error('Tests not started', { description: reason(e) });
    } finally {
      setStarting(null);
    }
  };

  const mine = data?.suites.find((s) => s.projectId === project.id);
  const runBlocked = !project.source ? 'Onboard a repository first'
    : !can('runs:run') ? 'Needs the runs:run permission'
      : mine && !mine.command ? 'No test command was found in this project'
        : mine?.checking ? `${mine.checking} is testing it now` : null;

  return (
    <Page>
      <PageHeader
        title="Testing"
        subtitle="Each onboarded project's own test command, run in a throwaway worktree. The numbers are what the runner printed; a documented legacy failure is information, not a defect."
        actions={
          <Button size="sm" disabled={!!runBlocked || starting === project.id} title={runBlocked ?? undefined}
            onClick={() => void start(project.id, project.name)}>
            {starting === project.id ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />}Run {project.name}'s tests
          </Button>
        }
      >
        {data && data.suites.length > 0 && (
          <div className="pb-3">
            <Segmented
              options={[
                { id: 'suites', label: `Suites (${data.suites.length})` },
                { id: 'failures', label: `Failures (${data.failures.length})` },
                { id: 'coverage', label: 'Coverage' },
                { id: 'history', label: `History (${data.history.length})` },
              ]}
              value={tab} onChange={setTab} />
          </div>
        )}
      </PageHeader>

      {report.error && !data ? (
        <PageBody><Empty title="The test report did not load" hint={report.error} action={<Button size="sm" variant="outline" onClick={reload}>Try again</Button>} /></PageBody>
      ) : !data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the test runs…" /></PageBody>
      ) : data.suites.length === 0 ? (
        <PageBody>
          <Empty icon={<FlaskConical className="size-6" />} title="No project has code on this machine"
            hint="Tests are the project's own command — a Makefile test target, pytest, npm test, go test or dotnet test — so there is nothing to run until a repository is onboarded."
            action={<Button size="sm" variant="outline" onClick={() => nav('/projects')}>Open Projects</Button>} />
        </PageBody>
      ) : (
        <PageBody className="space-y-4">
          <Stats report={data} />
          {tab === 'suites' && (
            <Suites report={data} canRun={can('runs:run')} starting={starting} onRun={(s) => void start(s.projectId, s.projectName)}
              onOpenFailure={(id) => { setPicked(id); setTab('failures'); }} />
          )}
          {tab === 'failures' && (
            <Failures report={data} picked={picked} onPick={setPicked} onChanged={reload}
              canRun={can('runs:run')} canDecide={can('decisions:make')}
              indexed={(pid) => !!all.find((p) => p.id === pid)?.codeIndex}
              onOpenFile={(f) => { setProjectId(f.projectId); nav(`/code?path=${encodeURIComponent(f.file)}`); }}
              onOpenRun={(ref) => nav(`/runs?ref=${encodeURIComponent(ref)}`)} />
          )}
          {tab === 'coverage' && <Coverage report={data} />}
          {tab === 'history' && <History lines={data.history} onOpen={(ref) => nav(`/runs?ref=${encodeURIComponent(ref)}`)} />}
        </PageBody>
      )}
    </Page>
  );
}

function Stats({ report }: { report: TestingReport }) {
  const latest = report.suites.flatMap((s) => (s.latest ? [s.latest] : []));
  const counted = latest.filter((l) => l.total !== null);
  const cases = counted.reduce((n, l) => n + (l.total ?? 0), 0);
  const passing = counted.reduce((n, l) => n + (l.passed ?? 0), 0);
  const pct = rate(passing, cases);
  const expected = report.failures.filter((f) => f.expectation);
  const legacy = expected.filter((f) => f.expectation?.kind === 'legacy').length;
  // A suite that failed without naming its failures is unexplained too — never a quiet zero.
  const unnamed = latest.filter((l) => l.unnamed).length;
  const real = report.failures.length - expected.length;
  const wall = latest.reduce((n, l) => n + (l.ms ?? 0), 0);
  return (
    <StatGrid cols={5}>
      <Stat label="Cases" value={counted.length ? cases.toLocaleString() : '—'} icon={<FlaskConical className="size-3" />}
        sub={counted.length ? `${passing.toLocaleString()} passing` : latest.length ? 'the runner printed no totals' : 'no tests have run'} />
      <Stat label="Pass rate" value={pct === null ? '—' : `${pct.toFixed(1)}%`} tone={pct === null ? 'neutral' : rateTone(pct)}
        sub={pct === null ? (latest.length ? 'exit code only' : 'nothing measured yet') : `latest run of ${counted.length === 1 ? 'one suite' : `${counted.length} suites`}`} />
      <Stat label="Real failures" value={real + unnamed} tone={real + unnamed ? 'warn' : 'ok'}
        sub={unnamed ? `${unnamed} failed run${unnamed === 1 ? '' : 's'} with failures not named` : real ? 'need a decision' : 'none unexplained'} />
      <Stat label="Expected" value={expected.length} tone="neutral" icon={<ShieldQuestion className="size-3" />}
        sub={`${legacy} legacy · ${expected.length - legacy} quarantined`} />
      <Stat label="Wall clock" value={latest.length ? seconds(wall) : '—'} sub="latest run of each suite" />
    </StatGrid>
  );
}

function Suites({ report, canRun, starting, onRun, onOpenFailure }: {
  report: TestingReport; canRun: boolean; starting: string | null;
  onRun: (s: TestSuiteLive) => void; onOpenFailure: (id: number) => void;
}) {
  const legacy = report.failures.find((f) => f.expectation?.kind === 'legacy');
  return (
    <>
      <Panel flush>
        <DataTable head={['Project', 'Command', 'Status', 'Passed', 'Duration', 'Runner', '']}>
          {report.suites.map((s) => {
            const l = s.latest;
            const calm = l?.allExpected === true;
            const pct = l ? rate(l.passed, l.total) : null;
            const blocked = !canRun ? 'Needs the runs:run permission' : !s.command ? 'No test command was found'
              : s.allowed === 'refused' ? 'You chose not to run tests here' : s.checking ? `${s.checking} is testing it now` : null;
            return (
              <Row key={s.projectId}>
                <Cell className="font-medium text-ink">{s.projectName}</Cell>
                <Cell>
                  {s.command ? <Mono>{s.command}</Mono> : <span className="text-[12.5px] text-dim">no test command found</span>}
                  {s.allowed === 'refused' && <Tag tone="danger" className="ml-2">refused</Tag>}
                  {s.command && s.allowed === null && <span className="ml-2 text-[12px] text-dim">asks you first</span>}
                </Cell>
                <Cell>
                  {s.checking ? <StatusText state="running" label="testing" />
                    : !l ? <StatusText state="idle" label="not run" />
                      : <StatusText state={l.status === 'passed' ? 'pass' : calm ? 'warn' : 'fail'} label={l.status === 'passed' ? 'pass' : calm ? 'expected' : 'fail'} />}
                </Cell>
                <Cell>
                  {!l ? <span className="text-dim">—</span> : pct === null ? (
                    <span className="text-[12.5px] text-dim">exit code only</span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <span className="tnum w-20 text-[13px]">{l.passed}/{l.total}</span>
                      <Bar className="w-28" pct={pct} tone={l.status === 'passed' ? 'ok' : 'warn'} />
                    </span>
                  )}
                </Cell>
                <Cell className="tnum">{l ? seconds(l.ms) : '—'}</Cell>
                <Cell mono className="text-dim">{l?.runner || s.tool}</Cell>
                <Cell className="text-right">
                  <Button size="xs" variant="outline" disabled={!!blocked || starting === s.projectId} title={blocked ?? undefined} onClick={() => onRun(s)}>
                    {starting === s.projectId ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />}Run
                  </Button>
                </Cell>
              </Row>
            );
          })}
        </DataTable>
      </Panel>

      {legacy?.expectation && (
        <Panel className="border-warn/35 accent-left" eyebrow="Red on purpose — this is not a regression"
          title={<span className="flex items-center gap-2"><ShieldQuestion className="size-4 text-warn" />{legacy.name}</span>}
          actions={<Button size="xs" variant="ghost" onClick={() => onOpenFailure(legacy.id)}>Open</Button>}>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <SectionTitle>Why it is expected</SectionTitle>
              <p className="text-[13.5px] leading-relaxed text-ink-2">{legacy.expectation.reason}</p>
              <p className="mt-1.5 text-[12.5px] text-dim">{legacy.expectation.by ?? 'Someone'} · {ago(legacy.expectation.at)}</p>
            </div>
            <div>
              <SectionTitle>What the runner said</SectionTitle>
              <p className="text-[13.5px] leading-relaxed text-ink-2">{legacy.message || 'No message.'}</p>
              {legacy.file && <Mono className="mt-2 inline-block">{legacy.file}{legacy.line ? `:${legacy.line}` : ''}</Mono>}
              <p className="mt-1.5 text-[12.5px] text-dim">Failed in {legacy.failedIn} of the last {legacy.ofLast} tested runs, since {legacy.firstFailedRef}.</p>
            </div>
          </div>
        </Panel>
      )}
    </>
  );
}

function Failures({ report, picked, onPick, onChanged, canRun, canDecide, indexed, onOpenFile, onOpenRun }: {
  report: TestingReport; picked: number | null; onPick: (id: number) => void; onChanged: () => void;
  canRun: boolean; canDecide: boolean; indexed: (projectId: string) => boolean;
  onOpenFile: (f: TestFailureLive) => void; onOpenRun: (ref: string) => void;
}) {
  const list = report.failures;
  const f = list.find((x) => x.id === picked) ?? list[0] ?? null;
  const failing = useMemo(() => new Set(list.map((x) => `${x.projectId}:${x.name}`)), [list]);
  const idle = report.expectations.filter((e) => !failing.has(`${e.projectId}:${e.testName}`));
  const names = Object.fromEntries(report.suites.map((s) => [s.projectId, s.projectName]));

  if (!f) {
    const ran = report.suites.some((s) => s.latest);
    return (
      <div className="space-y-4">
        <Panel>
          <Empty icon={<FlaskConical className="size-6" />} title={ran ? 'Nothing failed in the latest run of any suite' : 'No tests have run yet'}
            hint={ran ? 'Or the runner printed its failures in a shape NeuroCode does not read — the run itself still says pass or fail.' : 'Run a project\'s tests from the Suites tab. Failures appear here with the lines the runner printed.'} />
        </Panel>
        {idle.length > 0 && <Standing items={idle} names={names} canDecide={canDecide} onChanged={onChanged} />}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex min-h-[440px] flex-col gap-3 md:flex-row">
        <div className="w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] overflow-y-auto rounded-md border border-line bg-surface">
          {list.map((x) => (
            <button key={x.id} onClick={() => onPick(x.id)}
              className={cn('w-full border-l-2 px-3 py-2.5 text-left transition-colors',
                x.id === f.id ? 'border-brand bg-brand/8' : 'border-transparent hover:bg-surface-2/70')}>
              <div className="flex items-center gap-2">
                <Dot state={x.expectation ? 'warn' : 'error'} />
                <span className="truncate text-[13px] font-medium text-ink">{x.projectName}</span>
                {x.expectation && <Tag tone="warn" className="ml-auto">{KIND_WORD[x.expectation.kind]}</Tag>}
              </div>
              <p className="mt-1 line-clamp-2 font-mono text-[11.5px] text-dim">{x.name}</p>
            </button>
          ))}
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <FailureDetail key={f.id} f={f} canRun={canRun} canDecide={canDecide} indexed={indexed(f.projectId)}
            onChanged={onChanged} onOpenFile={onOpenFile} onOpenRun={onOpenRun} />
        </div>
      </div>
      {idle.length > 0 && <Standing items={idle} names={names} canDecide={canDecide} onChanged={onChanged} />}
    </div>
  );
}

function FailureDetail({ f, canRun, canDecide, indexed, onChanged, onOpenFile, onOpenRun }: {
  f: TestFailureLive; canRun: boolean; canDecide: boolean; indexed: boolean; onChanged: () => void;
  onOpenFile: (f: TestFailureLive) => void; onOpenRun: (ref: string) => void;
}) {
  const [full, setFull] = useState(false);
  const [marking, setMarking] = useState<TestExpectationLive['kind'] | null>(null);
  const [busy, setBusy] = useState(false);
  const detail = useRemote(full ? `testing:failure:${f.id}` : null, () => testing.failure(f.id));
  const where = f.file ? `${f.file}${f.line ? `:${f.line}` : ''}` : 'no file named';
  const decideBlocked = canDecide ? undefined : 'Needs the decisions:make permission';

  const rerun = async () => {
    setBusy(true);
    try {
      const run = await testing.rerun(f.id);
      toast(`Re-running ${f.projectName}'s suite`, {
        description: `${run.ref} · the whole command, at ${run.shortBase}`,
        action: { label: 'Open run', onClick: () => onOpenRun(run.ref) },
      });
      onChanged();
    } catch (e) {
      toast.error('Not re-run', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await testing.unexpect(f.projectId, f.name);
      toast('It counts as a real failure again');
      onChanged();
    } catch (e) {
      toast.error('Not removed', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel eyebrow={`${f.projectName} · ${where}`} title={<span className="break-all font-mono text-[14px]">{f.name}</span>}
      actions={f.expectation ? <Tag tone="warn">expected · {KIND_WORD[f.expectation.kind]}</Tag> : <Tag tone="danger">real failure</Tag>}>
      <SectionTitle>What the runner said</SectionTitle>
      <p className="text-[13.5px] leading-relaxed text-ink-2">{f.message || 'The runner printed no message for it.'}</p>
      <p className="mt-2 text-[12.5px] text-dim">
        Failed in <span className="tnum text-ink-2">{f.failedIn}</span> of the last <span className="tnum">{f.ofLast}</span> tested runs of this project, since{' '}
        <button className="font-mono text-brand hover:underline" onClick={() => onOpenRun(f.firstFailedRef)}>{f.firstFailedRef}</button>
        {' '}· this one in <button className="font-mono text-brand hover:underline" onClick={() => onOpenRun(f.runRef)}>{f.runRef}</button>
      </p>

      {f.expectation && (
        <>
          <SectionTitle className="mt-3">Why it is expected</SectionTitle>
          <p className="rounded-sm border border-warn/30 bg-warn/8 p-2.5 text-[13.5px] leading-relaxed text-warn">{f.expectation.reason}</p>
          <p className="mt-1.5 text-[12.5px] text-dim">{f.expectation.by ?? 'Someone'} · {ago(f.expectation.at)} · still run and reported; it does not raise a run's gate on its own</p>
        </>
      )}

      {full ? (
        detail.error ? <p className="mt-3 text-[12.5px] text-danger">{detail.error}</p>
          : !detail.data ? <p className="mt-3 text-[12.5px] text-dim">Loading the step's output…</p>
            : <pre className="ascii mt-3 max-h-[420px] overflow-auto rounded-sm border border-line bg-base p-3">{detail.data.log.map((l) => l.line).join('\n') || 'The step kept no output.'}</pre>
      ) : f.excerpt && <pre className="ascii mt-3 overflow-x-auto rounded-sm border border-line bg-base p-3">{f.excerpt}</pre>}

      <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">
        <Button size="xs" variant="outline" disabled={!canRun || busy} title={canRun ? 'Runs the whole command again' : 'Needs the runs:run permission'} onClick={() => void rerun()}>
          <RotateCcw className="size-3" />Re-run suite
        </Button>
        {f.expectation ? (
          <Button size="xs" variant="outline" disabled={!canDecide || busy} title={decideBlocked} onClick={() => void remove()}>
            <X className="size-3" />Remove expectation
          </Button>
        ) : (
          <>
            <Button size="xs" variant="outline" disabled={!canDecide || busy} title={decideBlocked} onClick={() => setMarking('legacy')}>
              <ShieldQuestion className="size-3" />Mark legacy-expected
            </Button>
            <Button size="xs" variant="outline" disabled={!canDecide || busy} title={decideBlocked} onClick={() => setMarking('quarantine')}>
              <EyeOff className="size-3" />Quarantine
            </Button>
          </>
        )}
        <Button size="xs" variant="ghost" disabled={!f.file || !indexed} title={!f.file ? 'The runner named no file' : !indexed ? 'Index the project to open its files' : undefined}
          onClick={() => onOpenFile(f)}>
          <FileCode className="size-3" />Open file
        </Button>
        <Button size="xs" variant="ghost" className="sm:ml-auto" onClick={() => setFull((v) => !v)}>
          <ScrollText className="size-3" />{full ? 'Excerpt' : 'Full output'}
        </Button>
      </div>

      {marking && <ExpectDialog f={f} kind={marking} onClose={() => setMarking(null)} onSaved={() => { setMarking(null); onChanged(); }} />}
    </Panel>
  );
}

function ExpectDialog({ f, kind, onClose, onSaved }: {
  f: TestFailureLive; kind: TestExpectationLive['kind']; onClose: () => void; onSaved: () => void;
}) {
  const [why, setWhy] = useState('');
  const [saving, setSaving] = useState(false);
  const ready = why.trim().length >= 10;
  const save = async () => {
    setSaving(true);
    try {
      await testing.expect(f.projectId, { testName: f.name, kind, reason: why.trim() });
      toast(kind === 'legacy' ? 'Marked legacy-expected' : 'Quarantined', { description: 'Still run and reported. It no longer raises a run\'s gate on its own.' });
      onSaved();
    } catch (e) {
      toast.error('Not saved', { description: reason(e) });
      setSaving(false);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{kind === 'legacy' ? 'Red on purpose' : 'Quarantine this test'}</DialogTitle>
          <DialogDescription className="break-all font-mono text-[12px]">{f.name}</DialogDescription>
        </DialogHeader>
        <p className="text-[13px] leading-relaxed text-ink-2">
          {kind === 'legacy'
            ? 'The behaviour it checks is known to differ and is kept that way on purpose.'
            : 'It fails for reasons that are not the code under test.'} It keeps running and keeps being reported;
          a run whose every failure is expected does not raise its gate. The next person to see it red reads your reason.
        </p>
        <Textarea value={why} onChange={(e) => setWhy(e.target.value)} rows={4} aria-label="Why"
          placeholder={kind === 'legacy' ? 'Why is it red on purpose, and until when?' : 'What makes it flaky?'} />
        <p className="text-[12px] text-dim">{ready ? 'Recorded in the audit log with your name.' : 'A sentence, at least ten characters.'}</p>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!ready || saving} onClick={() => void save()}>{saving && <Loader2 className="size-3.5 animate-spin" />}Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Expectations on tests that are not failing right now: turned green, renamed, or not run lately. */
function Standing({ items, names, canDecide, onChanged }: {
  items: TestExpectationLive[]; names: Record<string, string>; canDecide: boolean; onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const remove = async (e: TestExpectationLive) => {
    setBusy(e.testName);
    try {
      await testing.unexpect(e.projectId, e.testName);
      onChanged();
    } catch (err) {
      toast.error('Not removed', { description: reason(err) });
    } finally {
      setBusy(null);
    }
  };
  return (
    <Panel flush eyebrow="Not failing in the latest run" title="Standing expectations">
      <div className="divide-y divide-line/60">
        {items.map((e) => (
          <div key={`${e.projectId}:${e.testName}`} className="flex items-center gap-3 px-5 py-2.5">
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[12.5px] text-ink">{e.testName}</span>
              <span className="mt-0.5 block truncate text-[12px] text-dim">{names[e.projectId] ?? e.projectId} · {KIND_WORD[e.kind]} · {e.reason}</span>
            </span>
            <Button size="xs" variant="ghost" disabled={!canDecide || busy === e.testName} title={canDecide ? undefined : 'Needs the decisions:make permission'}
              onClick={() => void remove(e)}>Remove</Button>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Coverage({ report }: { report: TestingReport }) {
  const byProject = report.suites
    .map((s) => ({ suite: s, rows: report.coverage.filter((c) => c.projectId === s.projectId) }))
    .filter((g) => g.rows.length > 0);
  if (byProject.length === 0) {
    return (
      <Panel>
        <Empty icon={<FlaskConical className="size-6" />} title="No coverage report was written"
          hint="Your test command wrote no coverage report (coverage.xml, lcov.info, coverage/coverage-summary.json, coverage.out). Turn one on in the project and the next run fills this." />
      </Panel>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      {byProject.map(({ suite, rows }) => {
        const covered = rows.reduce((n, r) => n + r.covered, 0);
        const total = rows.reduce((n, r) => n + r.total, 0);
        const sources = [...new Set(rows.map((r) => r.source))].join(', ');
        return (
          <Panel key={suite.projectId} title={suite.projectName}
            eyebrow={`${rows[0].runRef} · ${sources} · ${total ? ((100 * covered) / total).toFixed(1) : '0'}% of ${total.toLocaleString()} ${rows.some((r) => r.source === 'go') ? 'statements' : 'lines'}`}>
            {rows.map((r) => {
              const pct = r.total ? (100 * r.covered) / r.total : 0;
              return <MeterRow key={r.path} label={r.path || '(project root)'} pct={pct} right={`${pct.toFixed(0)}% · ${r.covered.toLocaleString()}/${r.total.toLocaleString()}`} />;
            })}
          </Panel>
        );
      })}
      <Panel eyebrow="Honesty" title="What this measures">
        <p className="text-[13.5px] leading-relaxed text-ink-2">
          These are the lines each project's own runner reported executing, read from the report it wrote during its latest
          tested run. NeuroCode instruments nothing, so a directory missing here was not measured — not untested.
        </p>
        <p className="mt-3 border-t border-line pt-2.5 text-[12.5px] text-dim">
          It is not the coverage bars on Projects: those say how much of the code the index could read, not how much of it the tests run.
        </p>
      </Panel>
    </div>
  );
}

function History({ lines, onOpen }: { lines: TestHistoryLine[]; onOpen: (ref: string) => void }) {
  if (lines.length === 0) {
    return <Panel><Empty title="No test run on record" hint="A run appears here once a project's test command has really run — from this screen, or as a step of an agent's run." /></Panel>;
  }
  return (
    <Panel flush>
      <DataTable head={['Run', 'Project', 'Triggered by', 'Branch', 'Duration', 'Pass rate', 'Commit', 'When']}>
        {lines.map((r) => {
          const pct = rate(r.passed, r.total);
          return (
            <Row key={r.runRef} onClick={() => onOpen(r.runRef)}>
              <Cell mono className="text-brand">{r.runRef}</Cell>
              <Cell className="text-[12.5px]">{r.projectName}</Cell>
              <Cell className="text-[12.5px]">{r.trigger}{r.role === 'check' ? <span className="text-dim"> · tests only</span> : null}</Cell>
              <Cell mono className="text-dim">{r.branch}</Cell>
              <Cell className="tnum">{seconds(r.ms)}</Cell>
              <Cell>
                {pct === null ? (
                  <span className="flex items-center gap-2">
                    <StatusText state={r.status === 'passed' ? 'pass' : 'fail'} label={r.status} />
                    <span className="text-[12px] text-dim">exit code only</span>
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    <Bar className="w-20" pct={pct} tone={rateTone(pct)} />
                    <span className={cn('tnum text-[12px]', RATE_TEXT[rateTone(pct)])}>{pct.toFixed(1)}%</span>
                  </span>
                )}
              </Cell>
              <Cell mono className="text-dim">{r.sha ? r.sha.slice(0, 7) : '—'}</Cell>
              <Cell className="text-dim">{ago(r.at)}</Cell>
            </Row>
          );
        })}
      </DataTable>
    </Panel>
  );
}
