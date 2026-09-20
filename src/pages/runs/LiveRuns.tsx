import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowDownToLine, ChevronRight, ExternalLink, FileDiff, FolderGit2, GitMerge, GitPullRequest, Loader2, PlayCircle, RefreshCw, Square, Trash2, TriangleAlert, Undo2, Upload } from 'lucide-react';
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
import { asRuntime, gateKind, runtimeApi, shortReceipt, type PullRequest, type RunCheck, type RunGoal, type RuntimeRun, type RuntimeStep } from '@/lib/live/runtime';
import { GateActions } from '@/components/runs/GateActions';
import { PatchFiles } from '@/components/workbench/DiffView';
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
/* A request on the forge: merged is done, closed is done another way, open is still waiting on somebody.
   "unknown" is what a forge said in a word this product does not translate — shown as itself, not guessed. */
const PR_TONE: Record<PullRequest['state'], 'ok' | 'danger' | 'brand' | 'neutral'> = { merged: 'ok', closed: 'danger', open: 'brand', unknown: 'neutral' };
const Noun = (pr: PullRequest | null) => (pr?.noun ?? 'pull request');
const sentence = (words: string) => words.charAt(0).toUpperCase() + words.slice(1);
const done = (s: RunStep) => s.status === 'done' || s.status === 'skipped' || s.status === 'failed';
/** What a step does, told apart where one kind does two jobs: a check runs as a test step, the completion check as a review. */
const stepKind = (r: RuntimeRun, s: RunStep) =>
  r.checks.some((c) => c.step === s.n) ? 'runs a check' : r.goal?.step === s.n ? 'judges the goal' : KIND_LABEL[s.kind];
/** A run a person accepted: it finished and its signature step was answered yes. */
const accepted = (r: RunDoc) => r.status === 'done' && r.steps.some((s) => s.kind === 'handoff' && s.status === 'done');
const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
const progress = (r: RunDoc) => Math.round((100 * r.steps.filter(done).length) / Math.max(1, r.steps.length));
/* The ceiling on the live output. A run that talks for an hour used to stream into a list with no
   ceiling at all — every line copied the whole array, and every run watched in the session stayed in
   memory until the tab was closed. What falls off the top is not lost: it is in the run's own record,
   which this screen reads again whenever the run is opened. */
const LIVE_LINES = 2000;

export function LiveRuns() {
  const { runs, approvals, onRunLog, cancelRun, discardRun, mergeRun } = useData();
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
  // Which steps are unfolded, and which one is asking whether to be reverted — both kept with the run's ref.
  const [openSteps, setOpenSteps] = useState<{ ref: string; open: number[] }>({ ref: '', open: [] });
  const [reverting, setReverting] = useState<{ ref: string; n: number } | null>(null);
  // Merging writes to your real repository, so it takes two clicks. The first click and the merge's
  // answer each belong to one run: kept with its ref, so picking another run shows neither.
  const [armedRef, setArmedRef] = useState<string | null>(null);
  const [merged, setMerged] = useState<{ ref: string; result: MergeResult } | null>(null);
  const armed = run !== null && armedRef === run.ref;
  const mergeResult = run && merged?.ref === run.ref ? merged.result : null;
  const logBox = useRef<HTMLDivElement>(null);
  const diff = useRemote(showDiff && run ? `diff:${run.ref}:${run.diff.commits}` : null, () => api.runDiff(run?.ref ?? ''));
  /* Whether this run can be carried on, and from where. Asked only of a run that stopped with steps it
     never reached — the answer reads git, so there is no reason to ask it of a run that finished. The
     key carries the status and the step count, so answering a gate or carrying it on asks again. */
  const stranded = !!run && (run.status === 'failed' || run.status === 'cancelled')
    && !run.removed && !run.merged && !run.parent && run.role !== 'check'
    && run.steps.some((st) => st.status === 'todo');
  const carryOn = useRemote(stranded && run ? `resume:${run.ref}:${run.status}:${run.steps.filter(done).length}` : null,
    () => runtimeApi.resumePlan(run?.ref ?? ''));
  /* The request's state, read back from the forge so this screen says "merged" without anybody going to
     look. Asked only of a run that has one — a run with no request has nothing to read, and asking would
     shell out to `gh` for an answer that is already known. */
  const held = run?.pushed?.pullRequest ?? null;
  const prState = useRemote(held && run ? `pr:${run.ref}:${held.number}` : null, () => runtimeApi.pullRequest(run?.ref ?? ''));
  const request = prState.data?.pullRequest ?? held;

  useEffect(
    () => onRunLog((line) => setStreamed((m) => ({ ...m, [line.runRef]: [...(m[line.runRef] ?? []), line].slice(-LIVE_LINES) }))),
    [onRunLog],
  );

  // Lines arrive for every run that is working, not only the one on screen, and every run watched in a
  // session used to stay in memory until the tab was closed. Only the open run's buffer is kept; the
  // others' output is on the server, and this screen reads it back when that run is opened.
  // Adjusted during render rather than in an effect, so the dropped lines are never painted.
  const openRef = run?.ref ?? null;
  const [watching, setWatching] = useState<string | null>(null);
  if (openRef && watching !== openRef) {
    setWatching(openRef);
    setStreamed((m) => (m[openRef] ? { [openRef]: m[openRef] } : {}));
  }

  const logs = useMemo(() => {
    const seen = new Set<number>();
    return [...(detail.data?.logs ?? []), ...(streamed[run?.ref ?? ''] ?? [])].filter((l) => !seen.has(l.id) && seen.add(l.id));
  }, [detail.data, streamed, run?.ref]);
  // What is actually put in the DOM. The count in the panel's eyebrow stays the whole run's.
  const shown = logs.length > LIVE_LINES ? logs.slice(-LIVE_LINES) : logs;

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
  const openPullRequest = async () => {
    if (!run) return;
    setBusy(true);
    try {
      const out = asRuntime(await runtimeApi.openPullRequest(run.ref));
      const made = out.pushed?.pullRequest ?? null;
      toast.success(`${sentence(Noun(made))} #${made?.number ?? ''} opened`.trim(), { description: made?.draftBecause ?? 'It is open on the remote.' });
      prState.reload();
    } catch (e) {
      // The refusal says what is missing and keeps the compare link, which is what there was before.
      toast.error('Nothing was opened', { description: failed(e) });
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
  const revert = async (n: number, redo: boolean) => {
    if (!run) return;
    setBusy(true);
    try {
      await runtimeApi.revert(run.ref, n, redo);
      setReverting(null);
      toast.success(`Reverted to step ${n}`, {
        description: redo ? 'The worktree is back where that step left it, and the later steps run again.'
          : 'The worktree is back where that step left it. The later steps are marked taken back.',
      });
    } catch (e) {
      toast.error('Not reverted', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };
  const resume = async () => {
    if (!run) return;
    setBusy(true);
    try {
      const from = carryOn.data?.from;
      await runtimeApi.resume(run.ref);
      toast.success(`Carrying on from step ${from ?? ''}`.trim(), {
        description: 'The steps already done stand, with the commits they made. Nothing is written again.',
      });
    } catch (e) {
      toast.error('Not carried on', { description: failed(e) });
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
        <PageHeader title="Live Runs" subtitle="One git worktree and branch per run." />
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
  const gate = run.waitingOn ? approvals.find((a) => a.ref === run.waitingOn && a.status === 'pending') ?? null : null;
  const unfolded = openSteps.ref === run.ref ? openSteps.open : [];
  const toggleStep = (n: number) => setOpenSteps({ ref: run.ref, open: unfolded.includes(n) ? unfolded.filter((x) => x !== n) : [...unfolded, n] });
  const lastStep = run.steps.at(-1)?.n ?? 0;
  // A run is taken back step by step only when one agent wrote it, it has stopped, and its worktree is still there.
  const canRevert = can('runs:run') && run.role === 'solo' && !working && !run.removed && !run.merged;
  const asking = reverting?.ref === run.ref ? reverting.n : null;
  const runGrants = run.grants.filter((g) => g.scope === 'run');
  const lastRevert = run.reverts.at(-1);
  const lastResume = run.resumes.at(-1);

  return (
    <Page>
      <PageHeader
        title="Live Runs"
        subtitle="One git worktree and branch per run."
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
            {/* One control for the forge, never three: the request when there is one, the button that opens
                it when there is not, and — for somebody who may not push — the compare link as before. */}
            {request ? (
              <a href={request.url} target="_blank" rel="noopener noreferrer" className={buttonVariants({ size: 'sm', variant: 'outline' })}
                title={`${sentence(request.noun)} #${request.number} on ${request.host} — ${request.draft ? 'a draft' : 'ready'}, ${request.state}`}>
                <GitPullRequest className="size-3.5" />#{request.number} · {request.draft ? `draft, ${request.state}` : request.state}<ExternalLink className="size-3.5" />
              </a>
            ) : canPush && run.pushed ? (
              <Button size="sm" variant="outline" onClick={() => void openPullRequest()} disabled={busy}
                title="Opens it on the forge with your own gh or glab — a draft while findings stand unanswered, ready once it is signed.">
                {busy ? <Loader2 className="size-3.5 animate-spin" /> : <GitPullRequest className="size-3.5" />}Open pull request
              </Button>
            ) : run.pushed?.compareUrl ? (
              <a href={run.pushed.compareUrl} target="_blank" rel="noopener noreferrer" className={buttonVariants({ size: 'sm', variant: 'outline' })}
                title="Opens the remote's pull request page, under your own account">
                Open pull request<ExternalLink className="size-3.5" />
              </a>
            ) : null}
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
              <Panel className="border-warn/40" eyebrow={gate && gateKind(gate) === 'question' ? 'The agent asks rather than guesses' : 'Nothing moves until you answer'}
                title={<span className="flex items-center gap-2"><TriangleAlert className="size-4 text-warn" />{gate?.title ?? `Waiting for you · ${run.waitingOn ?? ''}`}</span>}>
                <p className="text-[13.5px] text-ink-2">{run.steps.find((s) => s.status === 'waiting')?.label}</p>
                {gate && gate.payload && gateKind(gate) !== 'signature' && (
                  <p className="mt-2 text-[13.5px] leading-relaxed break-words whitespace-pre-wrap text-ink">{gate.payload}</p>
                )}
                {gate && <p className="mt-2 text-[12.5px] leading-relaxed text-dim">{gate.reason}</p>}
                {gate ? <GateActions approval={gate} /> : null}
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
                {carryOn.data?.canResume && carryOn.data.from !== null && (
                  <div className="mt-3 border-t border-line/60 pt-3">
                    <p className="text-[13px] leading-relaxed text-ink-2">{carryOn.data.said}</p>
                    {can('runs:run') && (
                      <Button size="sm" className="mt-2.5" onClick={() => void resume()} disabled={busy}>
                        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <PlayCircle className="size-3.5" />}
                        Carry on from step {carryOn.data.from}
                      </Button>
                    )}
                  </div>
                )}
                {carryOn.data && !carryOn.data.canResume && carryOn.data.reason && (
                  <p className="mt-3 border-t border-line/60 pt-3 text-[13px] leading-relaxed text-soft">{carryOn.data.reason}</p>
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
                {run.steps.map((s) => {
                  const more = hasMore(s) || (canRevert && s.n < lastStep);
                  const open = unfolded.includes(s.n);
                  return (
                    <div key={s.n} className="px-5 py-2.5">
                      <button type="button" onClick={() => more && toggleStep(s.n)} aria-expanded={more ? open : undefined}
                        className={cn('flex w-full items-start gap-3 text-left', more && 'cursor-pointer')}>
                        <Dot state={s.status === 'running' ? 'running' : s.status} pulse={s.status === 'running'} className="mt-1.5" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13.5px] text-ink">{s.label}</span>
                          <span className="mt-0.5 block truncate text-[12px] text-dim">
                            {s.agent} · {stepKind(run, s)}{s.takenBack ? ` · taken back to step ${s.takenBack.to}` : s.detail ? ` · ${s.detail}` : ''}
                          </span>
                        </span>
                        {s.question && <Tag tone={s.answer ? 'neutral' : 'warn'}>{s.answer ? 'asked' : 'asks you'}</Tag>}
                        <span className="tnum shrink-0 text-[11.5px] text-dim">{s.ms ? `${(s.ms / 1000).toFixed(1)}s` : ''}</span>
                        {more && <ChevronRight className={cn('mt-0.5 size-3.5 shrink-0 text-dim transition-transform', open && 'rotate-90')} />}
                      </button>
                      {open && (
                        <StepDetail step={s} projectId={run.projectId} last={lastStep} canRevert={canRevert && s.n < lastStep} asking={asking === s.n} busy={busy}
                          onAsk={() => setReverting({ ref: run.ref, n: s.n })} onCancel={() => setReverting(null)}
                          onRevert={(redo) => void revert(s.n, redo)} />
                      )}
                    </div>
                  );
                })}
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
                {shown.length < logs.length && (
                  <div className="pb-1 text-dim">Earlier output is in the run&rsquo;s record.</div>
                )}
                {shown.map((l) => (
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
              <Panel flush title="Diff" eyebrow={`${stat.files} files · +${stat.insertions} −${stat.deletions}${diff.data?.truncated ? ' · cut at 200 kB' : ''}`}>
                {diff.loading ? <p className="px-5 py-3 text-[13px] text-dim"><Loader2 className="mr-2 inline size-3.5 animate-spin" />reading the worktree…</p>
                  : diff.error ? <p className="px-5 py-3 text-[13px] text-soft">{diff.error}</p>
                    : diff.data?.gone ? <p className="px-5 py-3 text-[13px] text-soft">The worktree was removed, so there is no diff to show.</p>
                      : !diff.data?.patch ? <p className="px-5 py-3 text-[13px] text-soft">Nothing changed yet.</p>
                        : <PatchFiles patch={diff.data.patch} truncated={diff.data.truncated} />}
              </Panel>
            )}

            <Panel title="Where it lives" eyebrow="Merge it yourself when you are ready">
              <KV k="Branch" v={run.branch} mono />
              <KV k="Worktree" v={run.removed ? 'removed' : run.worktree} mono />
              <KV k="Branched from" v={run.shortBase} mono />
              <KV k="Started" v={`${ago(run.startedAt)} by ${run.requestedBy}`} />
              {run.references.length > 0 && <KV k="Read only" v={`${run.references.join(', ')} — ${run.references.length === 1 ? 'a reference source' : 'reference sources'}: read for grounding, never written`} />}
              {run.review.instructions && run.review.instructions.length > 0 && (
                <KV k="Reviewer was given" v={<span className="break-words">{run.review.instructions.map((f) => f.path).join(', ')}</span>} />
              )}
              {run.review.taste && run.review.taste.length > 0 && <KV k="Taste applied" v={run.review.taste.join(', ')} mono />}
              {runGrants.length > 0 && (
                <KV k="Allowed for this run" v={<span className="break-words">{runGrants.map((g) => `${g.tool} ${g.subject}`).join(' · ')} — by {runGrants[0].by}</span>} />
              )}
              {lastRevert && (
                <KV k="Reverted" v={`to step ${lastRevert.to} by ${lastRevert.by}, ${ago(lastRevert.at)}${lastRevert.redo ? ' · later steps ran again' : ''}`} />
              )}
              {lastResume && (
                <KV k="Carried on" v={`from step ${lastResume.from} by ${lastResume.by}, ${ago(lastResume.at)}${lastResume.adopted ? ` · step ${lastResume.adopted} had already committed` : ''}${Object.keys(lastResume.kept ?? {}).length > 0 ? ' · what was half written is kept on a ref of its own' : ''}`} />
              )}
              {receipt && (
                <KV k="Reviewed diff" v={<span><Mono>{shortReceipt(receipt.sha256)}</Mono> at <Mono>{receipt.head.slice(0, 7) || '—'}</Mono>{receipt.by ? ` · ${receipt.by}` : ''}</span>} />
              )}
              {run.pushed && (
                <KV k="Pushed" v={<span>to {run.pushed.remote} at <Mono>{run.pushed.sha.slice(0, 7)}</Mono>, by {run.pushed.by}, {ago(run.pushed.at)}</span>} />
              )}
              {run.pushed && (
                <KV k={request ? sentence(request.noun) : 'Pull request'} v={request ? (
                  <span className="flex flex-col gap-0.5">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <a href={request.url} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">#{request.number} on {request.host} ↗</a>
                      <Tag tone={PR_TONE[request.state]}>{request.draft ? `draft · ${request.state}` : request.state}</Tag>
                    </span>
                    <span className="text-dim">{request.draftBecause}</span>
                    <span className="text-dim">Opened by {request.by} with {request.via === 'token' ? 'a stored token' : `your own ${request.via}`}, {ago(request.at)} · into <Mono>{request.base}</Mono></span>
                    {/* What is on screen is what the forge last said, and when it said it. */}
                    <span className="text-dim">{request.checkFailed ? `Last read ${ago(request.checkedAt)}; it could not be read again just now.` : `Read from ${request.host} ${ago(request.checkedAt)}.`}</span>
                  </span>
                ) : run.pushed.compareUrl ? (
                  <span className="flex flex-col gap-0.5">
                    <span>{canPush ? 'Not opened yet — “Open pull request” above does it from here.' : 'Not opened yet.'}</span>
                    <a href={run.pushed.compareUrl} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open it on the remote yourself ↗</a>
                  </span>
                ) : 'This remote has no pull request page NeuroCode knows how to open.'} />
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

/** Whether a step has more to show than its row: what it was handed, a question, a commit, a revert. */
const hasMore = (s: RuntimeStep) => !!(s.grounding || s.question || s.commitSha || s.takenBack || (s.attempts ?? 0) > 1);
const VIA: Record<'plan' | 'retrieval' | 'index', string> = { plan: 'the plan named it', retrieval: 'retrieval found it', index: 'the index matched its words' };

/** One step unfolded: the question and its answer, what it was handed, the commit it left — and the revert to here. */
function StepDetail({ step: s, projectId, last, canRevert, asking, busy, onAsk, onCancel, onRevert }: {
  step: RuntimeStep; projectId: string; last: number; canRevert: boolean; asking: boolean; busy: boolean;
  onAsk: () => void; onCancel: () => void; onRevert: (redo: boolean) => void;
}) {
  const g = s.grounding;
  return (
    <div className="mt-2 ml-5 space-y-2.5 text-[12.5px] text-ink-2">
      {s.question && (
        <div>
          <p><span className="text-dim">Asked · </span>{s.question}</p>
          <p className="mt-0.5">{s.answer ? <><span className="text-dim">Answered · </span>{s.answer}</> : <span className="text-warn">Waiting for your answer.</span>}</p>
        </div>
      )}
      {g && (
        <details>
          <summary className="cursor-pointer text-brand">Instructions given to this step · {plural(g.instructions.length, 'file')}{g.capped ? ' · cut to fit' : ''}</summary>
          {g.instructions.length === 0 ? (
            <p className="mt-1 text-dim">None: the project has no AGENTS.md, CLAUDE.md or rule file that applies to these files.</p>
          ) : (
            <ul className="mt-1 space-y-0.5">
              {g.instructions.map((f) => (
                <li key={f.path} className="flex flex-wrap items-center gap-x-2">
                  <Mono>{f.path}</Mono><span className="text-dim">{f.bytes.toLocaleString()} bytes · {f.scope === 'rules' ? 'rule' : 'project file'}</span>
                  {f.matched && <span className="text-dim">· applies to <Mono>{f.matched}</Mono></span>}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
      {g && (
        <details>
          <summary className="cursor-pointer text-brand">Read from retrieval · {plural(g.pieces.length, 'piece')}</summary>
          {g.pieces.length === 0 ? (
            <p className="mt-1 text-dim">Nothing: retrieval found no code or document for this step’s words, or the project is not indexed yet.</p>
          ) : (
            <ul className="mt-1 space-y-0.5">
              {g.pieces.map((p) => (
                <li key={p.ref} className="flex flex-wrap items-center gap-x-2">
                  <Tag tone="neutral">{p.kind}</Tag><Mono>{p.path}{p.line ? `:${p.line}` : ''}</Mono><span className="text-dim">found by {p.how === 'both' ? 'words and meaning' : p.how === 'semantic' ? 'meaning' : 'words'}</span>
                  {p.project && p.project !== projectId && <Tag tone="info">{p.project} · reference</Tag>}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
      {g && g.files.length > 0 && (
        <details>
          <summary className="cursor-pointer text-brand">Files it was handed · {g.files.length}</summary>
          <ul className="mt-1 space-y-0.5">
            {g.files.map((f) => (
              <li key={f.path} className="flex flex-wrap items-center gap-x-2">
                <Mono>{f.path}</Mono><span className="text-dim">{VIA[f.via]}</span>{f.readonly && <Tag tone="info">read only</Tag>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {g?.taste && g.taste.length > 0 && (
        <p className="flex flex-wrap items-center gap-x-2"><span className="text-dim">Taste applied</span>{g.taste.map((t) => <Mono key={t}>{t}</Mono>)}</p>
      )}
      {(s.attempts ?? 0) > 1 && (
        <p><span className="text-dim">Tried </span>{s.attempts} times of {s.maxAttempts} allowed<span className="text-dim"> — the first answer was one the runtime could not use.</span></p>
      )}
      {s.commitSha && <p><span className="text-dim">Left commit </span><Mono>{s.commitSha.slice(0, 7)}</Mono></p>}
      {s.takenBack && <p className="text-dim">Taken back when the run was reverted to step {s.takenBack.to} by {s.takenBack.by}, {ago(s.takenBack.at)}.</p>}
      {canRevert && !asking && (
        <Button size="xs" variant="outline" onClick={onAsk} disabled={busy} title="Take the run's worktree back to how it stood after this step">
          <Undo2 className="size-3" />Revert to here
        </Button>
      )}
      {canRevert && asking && (
        <div className="rounded-lg bg-surface-2/70 px-3 py-2.5">
          <p className="text-ink">Take the worktree back to how it stood after step {s.n}?</p>
          <p className="mt-0.5 text-dim">Steps {s.n + 1}–{last} are taken back. Only the run’s own worktree moves; your checkout is untouched.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="xs" onClick={() => onRevert(true)} disabled={busy}>{busy ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}Revert and redo steps {s.n + 1}–{last}</Button>
            <Button size="xs" variant="outline" onClick={() => onRevert(false)} disabled={busy}><Undo2 className="size-3" />Revert and stop</Button>
            <Button size="xs" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
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
            {(c.problems?.length ?? 0) > 0 && <CheckProblems check={c} />}
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

const PROBLEM_TONE = { error: 'danger', warning: 'warn', info: 'neutral' } as const;

/** What a check's output named, read into file:line:col — in the run's worktree, as the agent left it. */
function CheckProblems({ check }: { check: RunCheck }) {
  const shown = check.problems ?? [];
  const counts = check.problemCounts ?? {};
  const total = check.problemTotal ?? shown.length;
  const said = (['error', 'warning', 'info'] as const).filter((k) => counts[k]).map((k) => plural(counts[k] ?? 0, k)).join(' · ');
  return (
    <details className="mt-1.5" open={check.status === 'failed'}>
      <summary className="cursor-pointer text-[12px] text-brand">{plural(total, 'problem')}{said ? ` · ${said}` : ''}</summary>
      <ul className="mt-1.5 space-y-1">
        {shown.map((p, i) => (
          <li key={`${p.file}:${p.line}:${p.col}:${i}`} className="flex min-w-0 items-start gap-2 text-[12.5px]">
            <Tag tone={PROBLEM_TONE[p.severity]}>{p.severity}</Tag>
            <span className="min-w-0 break-words text-ink-2">
              <Mono className="[overflow-wrap:anywhere]">{p.file}:{p.line}:{p.col}</Mono> {p.message}
              {p.code && <span className="text-dim"> · {p.tool} {p.code}</span>}
            </span>
          </li>
        ))}
      </ul>
      {total > shown.length && <p className="mt-1 text-[12px] text-dim">and {total - shown.length} more — the full output is in the run's log.</p>}
    </details>
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
