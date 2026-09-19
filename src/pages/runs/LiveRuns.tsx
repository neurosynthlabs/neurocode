import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowDownToLine, ExternalLink, FileDiff, FolderGit2, GitMerge, Loader2, RefreshCw, Square, Trash2, TriangleAlert, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { Button, buttonVariants } from '@/components/ui/button';
import { Ascii, Dot, Empty, KV, ListRow, Mono, Page, PageBody, PageHeader, Panel, Stat, StatGrid, Tag } from '@/components/os';
import { api, ApiError, type MergeResult, type RunDoc, type RunLog, type RunStep } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ago } from '@/pages/code/format';
import { SEVERITY_TONE, clock } from '@/lib/live/work';
import { asRuntime, runtimeApi, shortReceipt, type RunCheck, type RunGoal, type RuntimeRun } from '@/lib/live/runtime';
import { plural } from '@/lib/words';

/* Live Runs, for real: every run is a git worktree on a branch of its own, and this is what it did. */

const LEVEL_TONE: Record<RunLog['level'], string> = {
  info: 'text-ink-2', ok: 'text-ok', warn: 'text-warn', err: 'text-danger', tool: 'text-brand',
};
const LEVEL_MARK: Record<RunLog['level'], string> = { info: '·', ok: '✓', warn: '!', err: '✗', tool: '›' };
const KIND_LABEL: Record<RunStep['kind'], string> = {
  edit: 'writes code', merge: 'brings a branch in', test: 'runs the tests', review: 'reads the diff', handoff: 'your signature',
};
const CHECK_TONE: Record<RunCheck['status'], 'ok' | 'danger' | 'neutral'> = { passed: 'ok', failed: 'danger', skipped: 'neutral', 'not run': 'neutral' };
const GOAL_TONE: Record<RunGoal['verdict'], 'ok' | 'danger' | 'warn' | 'neutral'> = { met: 'ok', 'not met': 'danger', unjudged: 'warn', 'not run': 'neutral' };
const done = (s: RunStep) => s.status === 'done' || s.status === 'skipped' || s.status === 'failed';
/** What a step does, told apart where one kind does two jobs: a check runs as a test step, the completion check as a review. */
const stepKind = (r: RuntimeRun, s: RunStep) =>
  r.checks.some((c) => c.step === s.n) ? 'runs a check' : r.goal?.step === s.n ? 'judges the goal' : KIND_LABEL[s.kind];
/** A run a person accepted: it finished and its signature step was answered yes. */
const accepted = (r: RunDoc) => r.status === 'done' && r.steps.some((s) => s.kind === 'handoff' && s.status === 'done');
const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const progress = (r: RunDoc) => Math.round((100 * r.steps.filter(done).length) / Math.max(1, r.steps.length));

export function LiveRuns() {
  const { runs, onRunLog, cancelRun, discardRun, mergeRun } = useData();
  const { can } = useAuth();
  const linked = useSearchParams()[0].get('ref');
  const [picked, setPicked] = useState<{ link: string | null; ref: string } | null>(null);
  const selected = (picked && picked.link === linked ? picked.ref : null) ?? linked ?? runs[0]?.ref ?? null;
  const found = runs.find((r) => r.ref === selected) ?? runs[0] ?? null;
  const run = found ? asRuntime(found) : null;

  const detail = useRemote(run ? `run:${run.ref}` : null, () => api.run(run?.ref ?? ''));
  const [streamed, setStreamed] = useState<Record<string, RunLog[]>>({});
  const [showDiff, setShowDiff] = useState(false);
  const [follow, setFollow] = useState(true);
  const [busy, setBusy] = useState(false);
  // Merging writes to your real repository, so it takes two clicks. The first click and the merge's
  // answer each belong to one run: kept with its ref, so picking another run shows neither.
  const [armedRef, setArmedRef] = useState<string | null>(null);
  const [merged, setMerged] = useState<{ ref: string; result: MergeResult } | null>(null);
  const armed = run !== null && armedRef === run.ref;
  const mergeResult = run && merged?.ref === run.ref ? merged.result : null;
  const logBox = useRef<HTMLDivElement>(null);
  const diff = useRemote(showDiff && run ? `diff:${run.ref}:${run.diff.commits}` : null, () => api.runDiff(run?.ref ?? ''));

  useEffect(() => onRunLog((line) => setStreamed((m) => ({ ...m, [line.runRef]: [...(m[line.runRef] ?? []), line] }))), [onRunLog]);

  const logs = useMemo(() => {
    const seen = new Set<number>();
    return [...(detail.data?.logs ?? []), ...(streamed[run?.ref ?? ''] ?? [])].filter((l) => !seen.has(l.id) && seen.add(l.id));
  }, [detail.data, streamed, run?.ref]);

  useEffect(() => {
    if (follow && logBox.current) logBox.current.scrollTop = logBox.current.scrollHeight;
  }, [logs.length, follow]);

  const stop = async () => {
    if (!run) return;
    setBusy(true);
    const out = await cancelRun(run.ref);
    setBusy(false);
    if (out) toast('Stopped', { description: 'The worktree stays where it is, for you to look at.' });
  };
  const push = async () => {
    if (!run) return;
    setBusy(true);
    try {
      const out = asRuntime(await runtimeApi.push(run.ref));
      toast.success(`Pushed to ${out.pushed?.remote ?? 'the remote'}`, {
        description: out.pushed?.compareUrl ? 'Open the pull request from here, under your own account.' : `${out.branch} at ${out.pushed?.sha.slice(0, 7) ?? ''}.`,
      });
    } catch (e) {
      toast.error('Nothing was pushed', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };
  const reviewAgain = async () => {
    if (!run) return;
    setBusy(true);
    try {
      await runtimeApi.reviewAgain(run.ref);
      toast('Reading it again', { description: 'The review reads the branch as it is now, and keeps a new receipt.' });
    } catch (e) {
      toast.error('Not reviewed again', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };
  const discard = async () => {
    if (!run) return;
    setBusy(true);
    const out = await discardRun(run.ref);
    setBusy(false);
    if (out) toast('Worktree removed', { description: `${out.branch} is gone. The commits stay until git prunes them.` });
  };
  const merge = async () => {
    if (!run) return;
    const ref = run.ref;
    if (!armed) { setArmedRef(ref); return; }
    setArmedRef(null);
    setBusy(true);
    const result = await mergeRun(ref);
    setBusy(false);
    if (!result) return;
    setMerged({ ref, result });
    if (result.merged) toast.success(`Merged into ${result.into}`, { description: `${result.commit} · undo with ${result.undo}` });
    else toast.error(`${result.conflicts.length} files collide with ${result.into}`, { description: 'Nothing was merged.' });
  };
  useEffect(() => {
    if (!armedRef) return;
    const id = window.setTimeout(() => setArmedRef(null), 4000);
    return () => window.clearTimeout(id);
  }, [armedRef]);

  if (!run) {
    return (
      <Page>
        <PageHeader title="Live Runs" subtitle="Every run works in a git worktree of its own, on its own branch. Nothing runs on your working tree, and nothing is merged without you." />
        <PageBody>
          <Empty
            icon={<FolderGit2 className="size-6" />} title="No run yet"
            hint="Compile a requirement, settle its questions, and dispatch the plan. If that project's code is on this machine, the agents get a worktree and start there."
          />
        </PageBody>
      </Page>
    );
  }

  const working = run.status === 'running' || run.status === 'queued';
  const stat = run.diff;
  const reviewStep = run.steps.find((s) => s.kind === 'review' && s.n !== run.goal?.step);
  const gateStep = run.steps.find((s) => s.status === 'waiting');
  const canReread = can('runs:run') && !!reviewStep && reviewStep.status !== 'running' && !run.parent && run.role !== 'check'
    && !run.removed && !run.merged && (run.status === 'done' || (run.status === 'waiting' && gateStep?.kind === 'handoff'));
  const canPush = can('runs:merge') && accepted(run) && !run.removed && run.diff.files > 0 && !run.parent;
  const receipt = run.review.receipt;

  return (
    <Page>
      <PageHeader
        title="Live Runs"
        subtitle="Every run works in a git worktree of its own, on its own branch. The model proposes file contents; nothing it says is ever executed."
        actions={
          <>
            {can('runs:run') && (working || run.status === 'waiting') && (
              <Button size="sm" variant="outline" onClick={() => void stop()} disabled={busy}><Square className="size-3.5" />Stop</Button>
            )}
            {canReread && (
              <Button size="sm" variant="outline" onClick={() => void reviewAgain()} disabled={busy} title="Read the branch as it is now, and keep a new receipt">
                <RefreshCw className="size-3.5" />Review again
              </Button>
            )}
            {canPush && (
              <Button size="sm" variant="outline" onClick={() => void push()} disabled={busy} title="Push this run's branch with your own git credentials. Never forced.">
                {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}{run.pushed ? 'Push again' : 'Push branch'}
              </Button>
            )}
            {run.pushed?.compareUrl && (
              <a href={run.pushed.compareUrl} target="_blank" rel="noopener noreferrer" className={buttonVariants({ size: 'sm', variant: 'outline' })}
                title="Opens the remote's pull request page, under your own account">
                Open pull request<ExternalLink className="size-3.5" />
              </a>
            )}
            {can('runs:merge') && run.status === 'done' && !run.merged && !run.removed && run.diff.files > 0 && (
              <Button size="sm" onClick={() => void merge()} disabled={busy}>
                <GitMerge className="size-3.5" />{armed ? 'Click again to merge' : 'Merge'}
              </Button>
            )}
            {can('runs:run') && !working && run.status !== 'waiting' && !run.removed && (
              <Button size="sm" variant="destructive" onClick={() => void discard()} disabled={busy}><Trash2 className="size-3.5" />Discard worktree</Button>
            )}
            <Button size="sm" variant={showDiff ? 'default' : 'outline'} onClick={() => setShowDiff((v) => !v)}>
              <FileDiff className="size-3.5" />Diff
            </Button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3 text-[12.5px] text-dim">
          <Mono tone="brand">{run.ref}</Mono>
          {run.taskRef && <Tag tone="neutral">{run.taskRef}</Tag>}
          <span className="min-w-0 truncate text-[13px] text-ink-2">{run.requirement || run.planRef}</span>
          <span className="flex items-center gap-1.5 sm:ml-auto"><FolderGit2 className="size-3.5" /><Mono>{run.branch}</Mono>from {run.shortBase}</span>
        </div>
      </PageHeader>

      <PageBody className="flex h-full flex-col gap-3 p-0">
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pt-4 pb-4 sm:px-6 md:flex-row">
          {/* the runs */}
          <div className="flex w-full shrink-0 max-h-[36vh] md:max-h-none md:w-[300px] flex-col overflow-y-auto rounded-xl border border-line bg-surface">
            <div className="shrink-0 border-b border-line px-4 py-2.5 text-[12px] font-medium text-dim">{runs.length} runs</div>
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
              {runs.map((r) => (
                <ListRow key={r.ref} active={r.ref === run.ref} onClick={() => setPicked({ link: linked, ref: r.ref })}>
                  <div className="flex items-center gap-2">
                    <Dot state={r.status === 'running' ? 'running' : r.status} pulse={r.status === 'running'} />
                    <Mono>{r.ref}</Mono>
                    <span className="ml-auto text-[11.5px] text-dim">{ago(r.startedAt)}</span>
                  </div>
                  <p className="mt-1 truncate text-[13px] text-ink">{r.projectName} · {r.branch.replace('neurocode/', '')}</p>
                  <p className="mt-1 truncate text-[11.5px] text-dim">
                    {r.status === 'waiting' ? `waiting for you · ${r.waitingOn ?? ''}` : `${progress(r)}% · ${plural(r.diff.files, 'file')}`}
                  </p>
                </ListRow>
              ))}
            </div>
          </div>

          {/* the run */}
          <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto [&>*]:shrink-0">
            <StatGrid cols={run.goalBudget ? 5 : 4}>
              <Stat label="Changed" value={plural(stat.files, 'file')} sub={`+${stat.insertions} −${stat.deletions} · ${plural(stat.commits, 'commit')}`} />
              <Stat label="Tests" value={run.tests.status} tone={run.tests.status === 'passed' ? 'ok' : run.tests.status === 'failed' ? 'danger' : 'neutral'}
                sub={run.tests.command ?? 'no test command found'} />
              <Stat label="Review" value={run.review.by ? plural(run.review.findings.length, 'finding') : 'not yet'} tone={run.review.findings.some((f) => f.severity === 'HIGH') ? 'danger' : 'neutral'}
                sub={run.review.by ? `by ${run.review.by}` : 'not reviewed yet'} />
              <Stat label="Wrote with" value={run.model ?? 'no model'} tone={run.model ? 'brand' : 'warn'} sub={run.requestedBy} />
              {run.goalBudget ? (
                <Stat label="Until done" value={`attempt ${run.attempt} of ${run.goalBudget}`} tone={run.goal ? GOAL_TONE[run.goal.verdict] : 'neutral'}
                  sub={run.goal && run.goal.verdict !== 'not run' ? `goal ${run.goal.verdict}` : 'goal not checked yet'} />
              ) : null}
            </StatGrid>

            {run.status === 'waiting' && (
              <Panel className="border-warn/40" eyebrow="Nothing moves until you answer" title={<span className="flex items-center gap-2"><TriangleAlert className="size-4 text-warn" />Waiting for you · {run.waitingOn}</span>}>
                <p className="text-[13.5px] text-ink-2">{run.steps.find((s) => s.status === 'waiting')?.label}</p>
                <Link to="/permissions" className="mt-2 inline-block text-[13px] text-brand hover:underline">Open the approvals inbox →</Link>
              </Panel>
            )}
            {run.note && run.status !== 'waiting' && (
              <Panel className={run.status === 'done' ? 'accent-left' : 'border-warn/35'} eyebrow={`Run ${run.status}`}
                title={run.status === 'done' ? 'Finished' : 'Stopped'}>
                <p className="text-[13.5px] leading-relaxed text-ink-2">{run.note}</p>
                {run.review.reworkedAs && (
                  <button onClick={() => setPicked({ link: linked, ref: run.review.reworkedAs ?? '' })} className="mt-2 text-[13px] text-brand hover:underline">
                    Open {run.review.reworkedAs} →
                  </button>
                )}
              </Panel>
            )}

            {run.children.length > 0 && (
              <Panel flush title="Agents in parallel" eyebrow={`${run.children.length} worktrees, a branch each, merged here`}>
                <div className="divide-y divide-line/60">
                  {run.children.map((ref) => {
                    const child = runs.find((r) => r.ref === ref);
                    if (!child) return null;
                    return (
                      <button key={ref} onClick={() => setPicked({ link: linked, ref })}
                        className="flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-surface-2/60">
                        <Dot state={child.status === 'running' ? 'running' : child.status} pulse={child.status === 'running'} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13.5px] font-medium text-ink">{child.agent ?? child.ref}</span>
                          <span className="mt-0.5 block truncate font-mono text-[12px] text-dim">{child.branch}</span>
                        </span>
                        <span className="tnum shrink-0 text-[12.5px] text-soft">{plural(child.diff.files, 'file')}</span>
                        <Mono>{child.ref}</Mono>
                      </button>
                    );
                  })}
                </div>
              </Panel>
            )}

            {run.parent && (
              <Panel title="One of several agents" eyebrow="its branch is merged in the run below">
                <button onClick={() => setPicked({ link: linked, ref: run.parent ?? '' })} className="text-[13px] text-brand hover:underline">
                  Open {run.parent} →
                </button>
              </Panel>
            )}

            {run.conflicts.length > 0 && (
              <Panel flush className="border-danger/35" title="Collisions" eyebrow="found at the merge, never half-applied">
                <div className="divide-y divide-line/60">
                  {run.conflicts.map((x) => (
                    <div key={x.branch} className="px-5 py-2.5">
                      <div className="text-[13px] font-medium text-ink">{x.agent || x.branch}</div>
                      <div className="mt-1 flex flex-wrap gap-1">{x.files.map((f) => <Mono key={f}>{f}</Mono>)}</div>
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {mergeResult && !mergeResult.merged && (
              <Panel className="border-danger/35" eyebrow={`Nothing was merged into ${mergeResult.into}`} title="The merge collides">
                <div className="flex flex-wrap gap-1">{mergeResult.conflicts.map((f) => <Mono key={f}>{f}</Mono>)}</div>
                <p className="mt-2 text-[12.5px] text-dim">Your repository is untouched. Merge it by hand, or dispatch again from your current branch.</p>
              </Panel>
            )}

            <Panel flush title="Steps" eyebrow={`${run.steps.filter(done).length} of ${run.steps.length} finished`}>
              <div className="divide-y divide-line/60">
                {run.steps.map((s) => (
                  <div key={s.n} className="flex items-start gap-3 px-5 py-2.5">
                    <Dot state={s.status === 'running' ? 'running' : s.status} pulse={s.status === 'running'} className="mt-1.5" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] text-ink">{s.label}</span>
                      <span className="mt-0.5 block truncate text-[12px] text-dim">{s.agent} · {stepKind(run, s)}{s.detail ? ` · ${s.detail}` : ''}</span>
                    </span>
                    <span className="tnum shrink-0 text-[11.5px] text-dim">{s.ms ? `${(s.ms / 1000).toFixed(1)}s` : ''}</span>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel
              flush title="Output" eyebrow={`${logs.length} lines`}
              actions={
                <Button size="icon-xs" variant={follow ? 'default' : 'outline'} onClick={() => setFollow((f) => !f)} title="Follow the output">
                  <ArrowDownToLine className="size-3" />
                </Button>
              }
            >
              <div ref={logBox} className="max-h-[320px] min-h-[160px] overflow-y-auto bg-base px-4 py-2.5 font-mono text-[12.5px] leading-[1.6]">
                {detail.loading && logs.length === 0 && <span className="text-dim">loading…</span>}
                {logs.map((l) => (
                  <div key={l.id} className="flex gap-2.5">
                    <span className="shrink-0 text-dim">{clock(l.at)}</span>
                    <span className={cn('w-2.5 shrink-0 text-center', LEVEL_TONE[l.level])}>{LEVEL_MARK[l.level]}</span>
                    <span className={cn('min-w-0 break-words whitespace-pre-wrap', LEVEL_TONE[l.level])}>{l.line}</span>
                  </div>
                ))}
                {working && <div className="flex gap-2.5 text-dim"><span className="animate-pulse-dot">▊</span></div>}
              </div>
            </Panel>

            {run.checks.length > 0 && <ChecksPanel checks={run.checks} />}
            {run.goal && run.goalBudget ? <GoalPanel goal={run.goal} attempt={run.attempt} budget={run.goalBudget} /> : null}

            {run.review.findings.length > 0 && (
              <Panel flush title="Review" eyebrow={run.review.verdict || `read by ${run.review.by}`}
                actions={receipt ? <span className="text-[11.5px] text-dim">receipt <Mono>{shortReceipt(receipt.sha256)}</Mono></span> : undefined}>
                <div className="divide-y divide-line/60">
                  {run.review.findings.map((f, i) => (
                    <div key={i} className="flex items-start gap-3 px-5 py-2.5">
                      <Tag tone={SEVERITY_TONE[f.severity] ?? 'neutral'}>{f.severity}</Tag>
                      <span className="min-w-0 flex-1 text-[13px] text-ink-2">{f.note}</span>
                      {f.file && <Mono>{f.file}</Mono>}
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {showDiff && (
              <Panel flush title="Diff" eyebrow={`${stat.files} files · +${stat.insertions} −${stat.deletions}`}>
                {diff.loading ? <p className="px-5 py-3 text-[13px] text-dim"><Loader2 className="mr-2 inline size-3.5 animate-spin" />reading the worktree…</p>
                  : diff.data?.gone ? <p className="px-5 py-3 text-[13px] text-soft">The worktree was removed, so there is no diff to show.</p>
                    : !diff.data?.patch ? <p className="px-5 py-3 text-[13px] text-soft">Nothing changed yet.</p>
                      : <Ascii className="max-h-[420px] overflow-auto rounded-none text-[12px] ring-0">{diff.data.patch}</Ascii>}
              </Panel>
            )}

            <Panel title="Where it lives" eyebrow="Merge it yourself when you are ready">
              <KV k="Branch" v={run.branch} mono />
              <KV k="Worktree" v={run.removed ? 'removed' : run.worktree} mono />
              <KV k="Branched from" v={run.shortBase} mono />
              <KV k="Started" v={`${ago(run.startedAt)} by ${run.requestedBy}`} />
              {receipt && (
                <KV k="Reviewed diff" v={<span><Mono>{shortReceipt(receipt.sha256)}</Mono> at <Mono>{receipt.head.slice(0, 7) || '—'}</Mono>{receipt.by ? ` · ${receipt.by}` : ''}</span>} />
              )}
              {run.pushed && (
                <KV k="Pushed" v={<span>to {run.pushed.remote} at <Mono>{run.pushed.sha.slice(0, 7)}</Mono>, by {run.pushed.by}, {ago(run.pushed.at)}</span>} />
              )}
              {run.pushed && (
                <KV k="Pull request" v={run.pushed.compareUrl
                  ? <a href={run.pushed.compareUrl} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open it on the remote ↗</a>
                  : 'This remote has no pull request page NeuroCode knows how to open.'} />
              )}
              {run.merged ? (
                <>
                  <KV k="Merged" v={`into ${run.merged.into} as ${run.merged.commit}, by ${run.merged.by}`} />
                  <KV k="Undo" v={<Mono>{run.merged.undo}</Mono>} />
                </>
              ) : run.status === 'done' && !run.removed && run.diff.files > 0 ? (
                <KV k="To merge" v={<Mono>git merge {run.branch}</Mono>} />
              ) : null}
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}

/** The project's own lint and typecheck, as they ran in this run's worktree. Output folded, a click away. */
function ChecksPanel({ checks }: { checks: RunCheck[] }) {
  const failedN = checks.filter((c) => c.status === 'failed').length;
  return (
    <Panel flush title="Checks" eyebrow={failedN ? `${failedN} failed · the signature says so` : 'the project\'s own commands, allowed once per project'}>
      <div className="divide-y divide-line/60">
        {checks.map((c) => (
          <div key={c.step} className="px-5 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <Tag tone={CHECK_TONE[c.status]}>{c.status}</Tag>
              <span className="text-[13.5px] text-ink">{c.name}</span>
              <Mono className="min-w-0 truncate">{c.command}</Mono>
            </div>
            {c.summary && <p className="mt-1 text-[12.5px] break-words text-dim">{c.summary}</p>}
            {c.output.length > 0 && (
              <details className="mt-1.5">
                <summary className="cursor-pointer text-[12px] text-brand">Last {c.output.length} lines</summary>
                <Ascii className="mt-1.5 max-h-[240px] overflow-auto text-[12px]">{c.output.join('\n')}</Ascii>
              </details>
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}

/** A goal run's completion check: each criterion, what was cited for it, and what happened next. */
function GoalPanel({ goal, attempt, budget }: { goal: RunGoal; attempt: number; budget: number }) {
  const next = goal.next === 'rework' ? `attempt ${attempt + 1} of ${budget} does it again` : goal.verdict === 'not run' ? 'runs after the review' : 'your signature decides';
  return (
    <Panel flush title={<span className="flex items-center gap-2">Completion check<Tag tone={GOAL_TONE[goal.verdict]}>{goal.verdict}</Tag></span>}
      eyebrow={`attempt ${attempt} of ${budget} · ${next}`}>
      <div className="px-5 py-2.5 text-[13px] text-ink-2">
        {goal.verdict === 'not run'
          ? 'Once the tests, checks and review are done, a model on a lane that did not write this change judges each acceptance criterion against the diff, citing evidence.'
          : goal.why}
        {goal.by && <span className="text-dim"> · judged by {goal.by}</span>}
      </div>
      {goal.criteria.length > 0 && (
        <div className="divide-y divide-line/60 border-t border-line/60">
          {goal.criteria.map((c, i) => (
            <div key={i} className="flex items-start gap-3 px-5 py-2.5">
              <Tag tone={c.met ? 'ok' : 'danger'}>{c.met ? 'met' : 'not met'}</Tag>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] text-ink">{c.criterion}</span>
                {(c.evidence || c.why) && (
                  <span className="mt-0.5 block text-[12px] break-words text-dim">{[c.evidence, c.why].filter(Boolean).join(' · ')}</span>
                )}
              </span>
              {c.file && <Mono className="shrink-0">{c.file}</Mono>}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
