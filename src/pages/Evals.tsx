import { useEffect, useMemo, useState } from 'react';
import { BookmarkPlus, FileInput, Gauge, Loader2, Play, Plus, Scale, Square, Trash2, TrendingDown, TrendingUp, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  More,
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, ListRow, DataTable, Row, Cell,
  Stat, StatGrid, Trend, Sparkline, Ring, Empty, Segmented, Field, SelectField,
} from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import {
  CHECK_LABEL, EVAL_KINDS, EVAL_TARGETS as TARGETS, evalsApi, type CaseInput, type CheckKind, type EvalCaseRow, type EvalCheck, type EvalKind, type EvalOverview,
  type EvalSuiteDetail, type EvalTarget, type LiveEvalSuite, type SuiteInput,
} from '@/lib/live/evals';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';

const KIND_TONE = { regression: 'warn', capability: 'brand', safety: 'ok', cost: 'violet' } as const;

/** Suites a person wrote, scored by runs that happened. */
export default function Evals() {
  return <LiveEvals />;
}

/* ── Live: suites a person wrote, scored by runs that happened ──────────────── */

const TRUE_LOOP = `  CASE ──▶ TARGET (compiler · ask · retrieval · reviewer · lane prompt) ──▶ CHECKS ──▶ STORED RESULT
                                                                                     │
      compile and ask read memory next time ◀── MEMORY ◀── a person saves a lesson ◀──┘`;

const KINDS: { id: 'all' | EvalKind; label: string }[] = [{ id: 'all', label: 'All' }, ...EVAL_KINDS];
const VERDICT_TONE = { pass: 'ok', fail: 'danger', partial: 'warn', error: 'danger' } as const;
const RUN_TONE = { queued: 'info', running: 'info', done: 'ok', failed: 'danger', cancelled: 'neutral' } as const;
const SUITE_DOT = { pass: 'pass', warn: 'warn', fail: 'fail', running: 'queued', never: 'idle' } as const;

function reason(e: unknown) {
  return e instanceof ApiError ? e.message : 'The local API did not answer.';
}

/** The checks that mean something for a target — the API refuses the rest with the same rule. */
function kindsFor(target: EvalTarget): CheckKind[] {
  return (Object.keys(CHECK_LABEL) as CheckKind[]).filter((k) => {
    if (k === 'cites') return target === 'ask';
    if (k === 'retrieves') return target === 'retrieval';
    if (target === 'retrieval') return !['free_lane', 'not_offline', 'judge'].includes(k);
    return true;
  });
}

function LiveEvals() {
  const { can } = useAuth();
  const [kind, setKind] = useState<'all' | EvalKind>('all');
  const [picked, setPicked] = useState<string | null>(null);
  const [runRef, setRunRef] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  const overview = useRemote('evals', () => evalsApi.overview());
  const suites = useMemo(() => overview.data?.suites ?? [], [overview.data]);
  const list = useMemo(() => suites.filter((x) => kind === 'all' || x.kind === kind), [suites, kind]);
  const sel = list.find((x) => x.id === picked) ?? list[0] ?? null;
  const detail = useRemote(sel ? `${sel.id}:${runRef ?? 'latest'}` : null, () => evalsApi.suite(sel?.id ?? '', runRef));

  // A run is answered in the background, one case after another: while one is in flight, look again.
  const running = suites.some((x) => x.status === 'running');
  const { reload: reloadOverview } = overview;
  const { reload: reloadDetail } = detail;
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => { reloadOverview(); reloadDetail(); }, 3000);
    return () => window.clearInterval(id);
  }, [running, reloadOverview, reloadDetail]);
  const refresh = () => { overview.reload(); detail.reload(); };

  const runAll = async () => {
    setBusy(true);
    try {
      const { runRefs } = await evalsApi.runAll(kind === 'all' ? null : kind);
      toast(`${runRefs.length} suite${runRefs.length === 1 ? '' : 's'} queued`, { description: 'They run one after another, so a free lane is not spent in a burst.' });
      refresh();
    } catch (e) {
      toast.error('Nothing queued', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  const scored = suites.filter((x) => x.score !== null);
  const mean = scored.length ? Math.round(scored.reduce((n, x) => n + (x.score ?? 0), 0) / scored.length) : null;
  const regressions = suites.filter((x) => (x.delta ?? 0) < 0);
  const improved = suites.filter((x) => (x.delta ?? 0) > 0);

  return (
    <Page>
      <PageHeader
        title="Evals"
        subtitle="Check that each feature still answers the way it should."
        about={<>
          <p>A suite runs when someone runs it, against the real compiler, memory, retrieval or a lane.</p>
          <p>Answers are checked by stated rules, or by a judge that is a model call of its own. A regression is reported as loudly as a win.</p>
        </>}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {can('evals:write') && <Button size="sm" variant="outline" onClick={() => setCreating(true)}><Plus className="size-3.5" />New suite</Button>}
            {can('ai:use') && suites.length > 0 && (
              <Button size="sm" onClick={() => void runAll()} disabled={busy}>
                {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}Run {kind === 'all' ? 'all suites' : `${kind} suites`}
              </Button>
            )}
          </div>
        }
      >
        <div className="pb-3"><Segmented options={KINDS} value={kind} onChange={(v) => { setKind(v); setRunRef(null); }} /></div>
      </PageHeader>

      {overview.error ? (
        <PageBody><Empty title="The evals did not load" hint={overview.error} action={<Button size="sm" variant="outline" onClick={overview.reload}>Try again</Button>} /></PageBody>
      ) : !overview.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the suites…" /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          {suites.length > 0 && <StatGrid cols={5}>
            <Stat label="Suites" value={suites.length} icon={<Gauge className="size-3" />} sub={`${scored.length} scored`} />
            <Stat label="Cases" value={suites.reduce((n, x) => n + x.cases, 0)} sub={scored.length ? `${scored.reduce((n, x) => n + x.passed, 0)} passing in the last runs` : 'none run yet'} />
            <Stat label="Mean score" value={mean ?? '—'} tone="brand" sub={mean === null ? 'no finished run' : `across ${scored.length} scored suite${scored.length === 1 ? '' : 's'}`} />
            <Stat label="Regressions" value={regressions.length} tone={regressions.length ? 'danger' : undefined} sub={regressions.map((r) => r.name).join(' · ') || 'vs the run before'} icon={<TrendingDown className="size-3" />} />
            <Stat label="Improved" value={improved.length} tone={improved.length ? 'ok' : undefined} sub="vs the run before" icon={<TrendingUp className="size-3" />} />
          </StatGrid>}

          {suites.length === 0 ? (
            <Panel>
              <Empty
                icon={<Gauge className="size-6" />}
                title="No eval suites yet"
                hint="Point one at the compiler, memory, retrieval or a lane."
                action={can('evals:write') && <Button size="sm" onClick={() => setCreating(true)}><Plus className="size-3.5" />New suite</Button>}
              />
            </Panel>
          ) : (
            <div className="flex min-h-[520px] flex-col gap-3 md:flex-row">
              <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[330px] overflow-y-auto rounded-md border border-line bg-surface">
                {list.length === 0 && <p className="px-4 py-3 text-[12.5px] text-dim">No {kind} suite.</p>}
                {list.map((x) => (
                  <ListRow key={x.id} active={x.id === sel?.id} onClick={() => { setPicked(x.id); setRunRef(null); }}>
                    <div className="flex items-center gap-2">
                      <Dot state={SUITE_DOT[x.status]} pulse={x.status === 'running'} />
                      <Mono tone={x.id === sel?.id ? 'brand' : 'neutral'}>{x.name}</Mono>
                    </div>
                    <div className="mt-1.5 flex items-center gap-3">
                      <span className="figure text-[17px] text-ink">{x.score ?? '—'}</span>
                      {x.delta !== null && <Trend value={x.delta} />}
                      <span className="ml-auto"><Sparkline points={overview.data?.trend[x.id] ?? []} tone={(x.delta ?? 0) < 0 ? 'danger' : 'ok'} /></span>
                    </div>
                    <p className="mt-1 text-[11.5px] text-dim">
                      {x.target} · {x.status === 'never' ? `${x.cases} cases · not run yet` : `${x.passed}/${x.cases} passed`}
                    </p>
                  </ListRow>
                ))}
              </div>
              <div className="min-w-0 flex-1 space-y-3">
                {sel && <SuitePanel suite={sel} detail={detail.data} error={detail.error} lanes={overview.data.lanes}
                  runRef={runRef} onRun={setRunRef} onChanged={refresh}
                  onDeleted={() => { setPicked(null); setRunRef(null); overview.reload(); }} />}
              </div>
            </div>
          )}

          {suites.length > 0 && <Panel title="Lessons learned" flush
            about="A lesson is a memory fact a person saves from a result. The compiler and Ask memory read it on their next call; nothing rewrites a rule on its own.">
            {overview.data.lessons.length === 0 ? (
              <p className="px-5 py-4 text-[13px] text-dim">No lessons yet. Save one from a failed case.</p>
            ) : (
              <div className="divide-y divide-line/60">
                {overview.data.lessons.map((l) => (
                  <div key={l.ref} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-5 py-3">
                    <span className="w-20 shrink-0 text-[12px] text-dim">{ago(l.at)}</span>
                    <Mono className="shrink-0">{l.from}</Mono>
                    <span className="min-w-0 flex-1 text-[13px] text-ink-2">{l.text}</span>
                    <span className="shrink-0 font-mono text-[11.5px] text-dim">{l.ref} · {l.runRef}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>}

          <More label="How a failure reaches the next run"><Ascii>{TRUE_LOOP}</Ascii></More>
        </PageBody>
      )}

      <SuiteDialog open={creating} onOpenChange={setCreating} onCreated={(id) => { setPicked(id); setKind('all'); overview.reload(); }} />
    </Page>
  );
}

function SuitePanel({ suite, detail, error, lanes, runRef, onRun, onChanged, onDeleted }: {
  suite: LiveEvalSuite; detail: EvalSuiteDetail | null; error: string | null; lanes: EvalOverview['lanes'];
  runRef: string | null; onRun: (ref: string | null) => void; onChanged: () => void; onDeleted: () => void;
}) {
  const { can } = useAuth();
  const [lane, setLane] = useState('');
  const [acting, setActing] = useState(false);
  const [adding, setAdding] = useState<CaseInput | null>(null);
  const [fromPlan, setFromPlan] = useState(false);
  const [overriding, setOverriding] = useState<EvalCaseRow | null>(null);
  const [learning, setLearning] = useState<EvalCaseRow | null>(null);

  const act = async (what: () => Promise<unknown>, done: string, failed: string) => {
    setActing(true);
    try {
      await what();
      toast(done);
      onChanged();
    } catch (e) {
      toast.error(failed, { description: reason(e) });
    } finally {
      setActing(false);
    }
  };

  const tone = suite.status === 'fail' ? 'danger' : suite.status === 'warn' ? 'warn' : 'ok';
  const run = detail?.run ?? null;
  const cases = detail?.caseRows ?? [];
  const regressed = (suite.delta ?? 0) < 0;
  const ready = lanes.filter((l) => l.ready);

  return (
    <>
      <Panel className={cn(regressed && 'border-danger/35 accent-left')}
        eyebrow={`${suite.target} · ${suite.lastRun ? `last finished ${ago(suite.lastRun)}` : 'never finished a run'} · pass at ${suite.threshold}`}
        title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{suite.name}</Mono><Tag tone={KIND_TONE[suite.kind]}>{suite.kind}</Tag>{suite.allowOffline && <Tag>offline counts</Tag>}</span>}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {suite.delta !== null && <Trend value={suite.delta} suffix=" pts" />}
            {suite.runningRef ? (
              can('ai:use') && <Button size="xs" variant="outline" disabled={acting}
                onClick={() => void act(() => evalsApi.cancel(suite.runningRef ?? ''), `${suite.runningRef} stopped`, 'Not stopped')}>
                <Square className="size-3" />Stop {suite.runningRef}
              </Button>
            ) : can('ai:use') && (
              <>
                {suite.targetKind !== 'retrieval' && (
                  <select value={lane} onChange={(e) => setLane(e.target.value)} aria-label="Lane"
                    className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
                    <option value="" className="bg-surface">{suite.lane ? `pinned · ${suite.lane}` : 'auto routing'}</option>
                    {ready.map((l) => <option key={l.id} value={l.id} className="bg-surface">{l.label}</option>)}
                  </select>
                )}
                <Button size="xs" disabled={acting || suite.cases === 0}
                  onClick={() => void act(async () => { const r = await evalsApi.run(suite.id, lane || null); onRun(r.ref); }, `${suite.name} queued`, 'Not started')}>
                  <Play className="size-3" />Run
                </Button>
              </>
            )}
            {can('evals:write') && !suite.runningRef && (
              <Button size="icon-xs" variant="ghost" aria-label={`Delete ${suite.name}`} disabled={acting}
                onClick={() => { if (window.confirm(`Delete ${suite.name}, its cases, runs and results?`)) void act(async () => { await evalsApi.deleteSuite(suite.id); onDeleted(); }, `${suite.name} deleted`, 'Not deleted'); }}>
                <Trash2 className="size-3.5" />
              </Button>
            )}
          </div>
        }>
        <div className="flex flex-wrap items-center gap-5">
          {suite.score === null
            ? <div className="grid size-16 place-items-center rounded-full ring-1 ring-line ring-inset text-[11.5px] text-dim">not run</div>
            : <Ring pct={suite.score} size={64} tone={tone} />}
          <div className="grid min-w-0 flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
            <div><div className="figure text-[20px] text-ink">{run ? run.passed : '—'}</div><div className="mt-1 text-[12px] text-dim">passed</div></div>
            <div><div className="figure text-[20px] text-ink">{run ? run.failed + run.partial : '—'}</div><div className="mt-1 text-[12px] text-dim">failed · partial</div></div>
            <div><div className="figure text-[20px] text-ink">{run ? run.errored : '—'}</div><div className="mt-1 text-[12px] text-dim">errored</div></div>
            <div>
              {(detail?.trend.length ?? 0) > 1
                ? <Sparkline points={detail?.trend ?? []} width={120} height={32} tone={regressed ? 'danger' : 'ok'} />
                : <div className="text-[12.5px] text-dim">{detail?.trend.length ? 'one finished run' : 'no finished run'}</div>}
              <div className="mt-1 text-[12px] text-dim">trend</div>
            </div>
          </div>
        </div>
        {regressed && (
          <p className="mt-3 border-t border-line pt-2.5 text-[12.5px] text-danger">
            Down {Math.abs(suite.delta ?? 0)} points against the run before {suite.lastRunRef}; the cases show which moved.
          </p>
        )}
        {detail?.description && <p className="mt-3 text-[13px] text-soft">{detail.description}</p>}
      </Panel>

      <Panel flush
        eyebrow={run ? `${run.ref} · ${run.status}${run.lane ? ` · asked ${run.lane} first` : ''}${detail?.comparedWith ? ` · Δ against ${detail.comparedWith}` : ''}` : 'No run yet'}
        title="Cases"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {detail && detail.runs.length > 0 && (
              <select value={runRef ?? ''} onChange={(e) => onRun(e.target.value || null)} aria-label="Run"
                className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
                <option value="" className="bg-surface">Latest run</option>
                {detail.runs.map((r) => (
                  <option key={r.ref} value={r.ref} className="bg-surface">{r.ref} · {r.status}{r.score !== null ? ` · ${r.score}` : ''}</option>
                ))}
              </select>
            )}
            {can('evals:write') && suite.targetKind === 'compile' && (
              <Button size="xs" variant="outline" onClick={() => setFromPlan(true)}><FileInput className="size-3" />From a plan</Button>
            )}
            {can('evals:write') && (
              <Button size="xs" variant="outline" onClick={() => setAdding({ name: '', input: '', checks: [{ kind: 'contains', value: '' }], weight: 1 })}>
                <Plus className="size-3" />Add case
              </Button>
            )}
          </div>
        }>
        {run && (run.note || run.status === 'running' || run.status === 'queued') && (
          <p className={cn('flex items-center gap-2 border-b border-line/60 px-5 py-2.5 text-[12.5px]', run.status === 'failed' ? 'text-danger' : 'text-soft')}>
            {(run.status === 'running' || run.status === 'queued') && <Loader2 className="size-3.5 animate-spin" />}
            {run.status === 'running' ? `Answering case ${run.passed + run.failed + run.partial + run.errored + 1} of ${suite.cases}…` : run.status === 'queued' ? 'Waiting its turn…' : run.note}
            <Tag tone={RUN_TONE[run.status]} className="ml-auto">{run.status}</Tag>
          </p>
        )}
        {error ? (
          <Empty title="The suite did not load" hint={error} />
        ) : !detail ? (
          <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the cases…" />
        ) : cases.length === 0 ? (
          <Empty title="No cases yet" hint="Add an input and the checks its answer must pass." />
        ) : (
          <DataTable head={['Case', 'Expected', 'Got', 'Status', 'Δ', 'Judge', '']}>
            {cases.map((c) => (
              <Row key={c.id} className={cn(c.overridden && 'bg-warn/5')}>
                <Cell className="max-w-[240px] text-ink">
                  {c.name}
                  {c.ms !== null && c.status && <div className="mt-0.5 font-mono text-[11px] text-dim">{c.offline ? 'offline rules' : c.model || c.lane} · {c.ms} ms</div>}
                </Cell>
                <Cell className="max-w-[260px] text-[12.5px] text-soft">{c.expected}</Cell>
                <Cell className={cn('max-w-[260px] text-[12.5px] break-words', c.status === 'pass' ? 'text-ok' : c.status === 'partial' ? 'text-warn' : c.status ? 'text-danger' : 'text-dim')}>
                  {c.status ? c.got || '—' : 'not run yet'}
                </Cell>
                <Cell>{c.status ? <Tag tone={VERDICT_TONE[c.status]}>{c.status}</Tag> : <span className="text-[12px] text-dim">—</span>}</Cell>
                <Cell className="tnum">{c.scoreDelta === 0 ? '—' : c.scoreDelta > 0 ? `+${c.scoreDelta}` : c.scoreDelta}</Cell>
                <Cell className="max-w-[260px] text-[12px] text-dim">
                  {c.overridden ? <span className="flex items-start gap-1 text-warn"><Scale className="mt-px size-3 shrink-0" />{c.judge}</span> : c.judge}
                </Cell>
                <Cell className="whitespace-nowrap">
                  {c.resultId !== null && (
                    <span className="flex justify-end gap-1">
                      {can('evals:write') && <Button size="xs" variant="ghost" onClick={() => setOverriding(c)}><Scale className="size-3" />{c.overridden ? 'Verdict' : 'Override'}</Button>}
                      {can('memory:write') && c.status !== 'pass' && <Button size="xs" variant="ghost" onClick={() => setLearning(c)}><BookmarkPlus className="size-3" />Lesson</Button>}
                    </span>
                  )}
                  {can('evals:write') && !suite.runningRef && (
                    <Button size="icon-xs" variant="ghost" aria-label={`Remove ${c.name}`}
                      onClick={() => void act(() => evalsApi.deleteCase(c.id), 'Case removed', 'Not removed')}>
                      <X className="size-3" />
                    </Button>
                  )}
                </Cell>
              </Row>
            ))}
          </DataTable>
        )}
      </Panel>

      {adding && <CaseDialog suite={suite} draft={adding} onClose={() => setAdding(null)} onSaved={onChanged} />}
      <FromPlanDialog open={fromPlan} suite={suite} onOpenChange={setFromPlan} onDraft={(d) => { setFromPlan(false); setAdding(d); }} />
      {overriding && <OverrideDialog row={overriding} onClose={() => setOverriding(null)} onSaved={onChanged} />}
      {learning && <LessonDialog row={learning} runRef={run?.ref ?? ''} onClose={() => setLearning(null)} onSaved={onChanged} />}
    </>
  );
}

function SuiteDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (id: string) => void }) {
  const { all: projects } = useProject();
  const blank: SuiteInput = { name: '', kind: 'capability', targetKind: 'compile', projectId: projects[0]?.id ?? null, lane: null, systemPrompt: '', threshold: 90, allowOffline: false, description: '' };
  const [form, setForm] = useState<SuiteInput>(blank);
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<SuiteInput>) => setForm((f) => ({ ...f, ...patch }));
  const needsProject = ['compile', 'ask', 'retrieval'].includes(form.targetKind);
  const valid = form.name.trim().length >= 2 && (!needsProject || !!form.projectId) && (form.targetKind !== 'prompt' || !!form.systemPrompt.trim());

  const save = async () => {
    setSaving(true);
    try {
      const made = await evalsApi.createSuite({ ...form, projectId: needsProject ? form.projectId : null });
      toast(`${made.name} created`, { description: 'Add a case, then run it.' });
      onOpenChange(false);
      setForm(blank);
      onCreated(made.id);
    } catch (e) {
      toast.error('Not created', { description: reason(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New eval suite</DialogTitle>
          <DialogDescription>One real target, and the score its cases must reach.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="Name" value={form.name} onChange={(v) => set({ name: v })} placeholder="compiler-risk-grading" mono autoFocus />
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField label="Target" value={form.targetKind} onChange={(v) => set({ targetKind: v as EvalTarget })} options={TARGETS} />
            <SelectField label="Kind" value={form.kind} onChange={(v) => set({ kind: v as EvalKind })} options={KINDS.filter((k) => k.id !== 'all').map((k) => ({ value: k.id, label: k.label }))} />
          </div>
          {needsProject && (
            projects.length
              ? <SelectField label="Project it reads" value={form.projectId ?? ''} onChange={(v) => set({ projectId: v })} options={projects.map((p) => ({ value: p.id, label: p.name }))} />
              : <p className="text-[12.5px] text-warn">This target needs a project, and there is none yet.</p>
          )}
          {form.targetKind === 'prompt' && (
            <label className="block">
              <span className="mb-1.5 block text-[12.5px] font-medium text-soft">System prompt</span>
              <Textarea value={form.systemPrompt} onChange={(e) => set({ systemPrompt: e.target.value })} rows={4}
                placeholder='Answer as one JSON object: {"answer": "..."}' className="font-mono text-[12.5px]" />
              <span className="mt-1 block text-[12px] text-dim">Lanes answer in JSON; text checks read that JSON as text.</span>
            </label>
          )}
          <Field label="Pass at (0–100)" type="number" value={String(form.threshold)} onChange={(v) => set({ threshold: Math.max(0, Math.min(100, Number(v) || 0)) })}
            className="sm:w-1/2" />
          <More label="More options">
            <div className="space-y-3">
              <label className="flex items-center justify-between gap-3 text-[13px] text-ink-2">
                Count offline answers
                <Switch checked={form.allowOffline} onCheckedChange={(v) => set({ allowOffline: v })} />
              </label>
              <Field label="Description" value={form.description} onChange={(v) => set({ description: v })} placeholder="What this suite protects" />
            </div>
          </More>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => void save()} disabled={!valid || saving}>{saving && <Loader2 className="size-3.5 animate-spin" />}Create suite</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CheckRow({ target, check, onChange, onRemove }: {
  target: EvalTarget; check: EvalCheck; onChange: (c: EvalCheck) => void; onRemove?: () => void;
}) {
  const text = (key: 'value' | 'path' | 'ref' | 'rubric', placeholder: string, mono = true) => (
    <input value={String(check[key] ?? '')} onChange={(e) => onChange({ ...check, [key]: e.target.value })} placeholder={placeholder}
      aria-label={placeholder}
      className={cn('h-8 min-w-0 flex-1 rounded-lg border border-line bg-surface-2/60 px-2.5 text-[12.5px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none', mono && 'font-mono')} />
  );
  const number = (key: 'value' | 'k', placeholder: string) => (
    <input type="number" min={1} value={check[key] === undefined ? '' : String(check[key])} placeholder={placeholder} aria-label={placeholder}
      onChange={(e) => onChange({ ...check, [key]: e.target.value === '' ? undefined : Number(e.target.value) })}
      className="h-8 w-24 rounded-lg border border-line bg-surface-2/60 px-2.5 text-[12.5px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none" />
  );
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={check.kind} aria-label="Check"
        onChange={(e) => onChange({ kind: e.target.value as CheckKind })}
        className="h-8 rounded-lg border border-line bg-surface-2/60 px-2 text-[12.5px] text-ink-2">
        {kindsFor(target).map((k) => <option key={k} value={k} className="bg-surface">{CHECK_LABEL[k]}</option>)}
      </select>
      {['exact', 'contains', 'not_contains'].includes(check.kind) && text('value', 'text')}
      {check.kind === 'regex' && text('value', 'pattern')}
      {(check.kind === 'json_equals' || check.kind === 'json_contains') && <>{text('path', '$.risk')}{text('value', 'value')}</>}
      {check.kind === 'cites' && text('ref', 'MEM-142')}
      {check.kind === 'retrieves' && <>{text('ref', 'MEM-142')}{number('k', 'top k')}</>}
      {check.kind === 'max_ms' && number('value', 'ms')}
      {check.kind === 'judge' && text('rubric', 'What a passing answer does', false)}
      {onRemove && <Button size="icon-xs" variant="ghost" aria-label="Remove check" onClick={onRemove}><X className="size-3" /></Button>}
    </div>
  );
}

function CaseDialog({ suite, draft, onClose, onSaved }: { suite: LiveEvalSuite; draft: CaseInput; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<CaseInput>(draft);
  const [saving, setSaving] = useState(false);
  const valid = !!form.name.trim() && !!form.input.trim() && form.checks.length > 0;

  const save = async () => {
    setSaving(true);
    try {
      await evalsApi.addCase(suite.id, form);
      toast('Case added', { description: `${suite.name} · ${form.name}` });
      onSaved();
      onClose();
    } catch (e) {
      toast.error('Not added', { description: reason(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add a case to {suite.name}</DialogTitle>
          <DialogDescription>Sent to the {suite.target.toLowerCase()} as written; each check scores the answer.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="Name" value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="Interstate invoice is HIGH risk" autoFocus />
          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-medium text-soft">{suite.targetKind === 'review' ? 'Diff' : suite.targetKind === 'retrieval' ? 'Query' : 'Input'}</span>
            <Textarea value={form.input} onChange={(e) => setForm({ ...form, input: e.target.value })} rows={4} className="text-[13px]" />
          </label>
          <div>
            <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Checks</span>
            <div className="space-y-2">
              {form.checks.map((c, i) => (
                <CheckRow key={i} target={suite.targetKind} check={c}
                  onChange={(next) => setForm({ ...form, checks: form.checks.map((x, j) => (j === i ? next : x)) })}
                  onRemove={form.checks.length > 1 ? () => setForm({ ...form, checks: form.checks.filter((_, j) => j !== i) }) : undefined} />
              ))}
            </div>
            <Button size="xs" variant="ghost" className="mt-2" onClick={() => setForm({ ...form, checks: [...form.checks, { kind: 'contains', value: '' }] })}>
              <Plus className="size-3" />Another check
            </Button>
          </div>
          <Field label="Weight" type="number" value={String(form.weight)} onChange={(v) => setForm({ ...form, weight: Math.max(1, Math.min(100, Number(v) || 1)) })} className="w-32" />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} disabled={!valid || saving}>{saving && <Loader2 className="size-3.5 animate-spin" />}Add case</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FromPlanDialog({ open, suite, onOpenChange, onDraft }: {
  open: boolean; suite: LiveEvalSuite; onOpenChange: (o: boolean) => void; onDraft: (d: CaseInput) => void;
}) {
  const { plans } = useData();
  const mine = plans.filter((p) => p.projectId === suite.projectId);
  const [ref, setRef] = useState('');
  const [loading, setLoading] = useState(false);
  const chosen = ref || mine[0]?.ref || '';

  const draft = async () => {
    setLoading(true);
    try {
      onDraft(await evalsApi.caseFromPlan(suite.id, chosen));
    } catch (e) {
      toast.error('No draft', { description: reason(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>A case from a plan</DialogTitle>
          <DialogDescription>Its requirement becomes the input; its risk and owners, the checks.</DialogDescription>
        </DialogHeader>
        {mine.length === 0
          ? <p className="text-[13px] text-dim">This suite's project has no plan yet.</p>
          : <SelectField label="Plan" value={chosen} onChange={setRef} options={mine.map((p) => ({ value: p.ref, label: `${p.ref} · ${p.rawRequirement.slice(0, 60)}` }))} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => void draft()} disabled={!chosen || loading}>{loading && <Loader2 className="size-3.5 animate-spin" />}Draft case</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OverrideDialog({ row, onClose, onSaved }: { row: EvalCaseRow; onClose: () => void; onSaved: () => void }) {
  const [status, setStatus] = useState<'pass' | 'fail' | 'partial'>(row.status === 'pass' ? 'fail' : 'pass');
  const [note, setNote] = useState(row.overrideNote);
  const [saving, setSaving] = useState(false);

  const save = async (verdict: 'pass' | 'fail' | 'partial' | null) => {
    if (row.resultId === null) return;
    setSaving(true);
    try {
      await evalsApi.override(row.resultId, verdict, note);
      toast(verdict ? 'Your verdict is recorded beside the machine’s' : 'Override taken back');
      onSaved();
      onClose();
    } catch (e) {
      toast.error('Not recorded', { description: reason(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Override {row.name}</DialogTitle>
          <DialogDescription>The checks said {row.machineStatus}. Your verdict shows beside it; the score is unchanged.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {row.output && <pre className="max-h-40 overflow-auto rounded-lg bg-base p-3 font-mono text-[12px] whitespace-pre-wrap text-ink-2 ring-1 ring-line/60 ring-inset">{row.output}</pre>}
          {row.judgeReason && <p className="text-[12.5px] text-soft">Judge: {row.judgeReason}</p>}
          <Segmented options={[{ id: 'pass', label: 'Pass' }, { id: 'partial', label: 'Partial' }, { id: 'fail', label: 'Fail' }]} value={status} onChange={setStatus} />
          <Field label="Why" value={note} onChange={setNote} placeholder="What the checks could not see" />
        </div>
        <DialogFooter>
          {row.overridden && <Button variant="ghost" onClick={() => void save(null)} disabled={saving}>Take back</Button>}
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save(status)} disabled={!note.trim() || saving}>Record verdict</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function LessonDialog({ row, runRef, onClose, onSaved }: { row: EvalCaseRow; runRef: string; onClose: () => void; onSaved: () => void }) {
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (row.resultId === null) return;
    setSaving(true);
    try {
      const { ref } = await evalsApi.lesson(row.resultId, text);
      toast(`${ref} saved to memory`, { description: 'The compiler and ask memory read it on their next call.' });
      onSaved();
      onClose();
    } catch (e) {
      toast.error('Not saved', { description: reason(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>A lesson from {row.name}</DialogTitle>
          <DialogDescription>Saved to memory with {runRef} as its source.</DialogDescription>
        </DialogHeader>
        <p className="text-[12.5px] text-soft">Expected {row.expected}; got {row.got || '—'}.</p>
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} autoFocus
          placeholder="What should be true next time, in a sentence or two." className="text-[13px]" />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} disabled={text.trim().length < 10 || saving}>{saving && <Loader2 className="size-3.5 animate-spin" />}Save lesson</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
