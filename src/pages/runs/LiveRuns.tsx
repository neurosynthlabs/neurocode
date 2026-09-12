import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowDownToLine, FileDiff, FolderGit2, GitMerge, Loader2, Square, Trash2, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Ascii, Dot, Empty, KV, ListRow, Mono, Page, PageBody, PageHeader, Panel, Stat, StatGrid, Tag } from '@/components/os';
import { api, type MergeResult, type RunDoc, type RunLog, type RunStep } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { ago } from '@/pages/code/format';

/* Live Runs, for real: every run is a git worktree on a branch of its own, and this is what it did. */

const LEVEL_TONE: Record<RunLog['level'], string> = {
  info: 'text-ink-2', ok: 'text-ok', warn: 'text-warn', err: 'text-danger', tool: 'text-brand',
};
const LEVEL_MARK: Record<RunLog['level'], string> = { info: '·', ok: '✓', warn: '!', err: '✗', tool: '›' };
const KIND_LABEL: Record<RunStep['kind'], string> = {
  edit: 'writes code', merge: 'brings a branch in', test: 'runs the tests', review: 'reads the diff', handoff: 'your signature',
};
const SEVERITY_TONE: Record<string, 'danger' | 'warn' | 'neutral'> = { HIGH: 'danger', MEDIUM: 'warn', LOW: 'neutral' };
const done = (s: RunStep) => s.status === 'done' || s.status === 'skipped' || s.status === 'failed';
const progress = (r: RunDoc) => Math.round((100 * r.steps.filter(done).length) / Math.max(1, r.steps.length));

export function LiveRuns() {
  const { runs, onRunLog, cancelRun, discardRun, mergeRun } = useData();
  const { can } = useAuth();
  const linked = useSearchParams()[0].get('ref');
  const [picked, setPicked] = useState<{ link: string | null; ref: string } | null>(null);
  const selected = (picked && picked.link === linked ? picked.ref : null) ?? linked ?? runs[0]?.ref ?? null;
  const run = runs.find((r) => r.ref === selected) ?? runs[0] ?? null;

  const detail = useRemote(run ? `run:${run.ref}` : null, () => api.run(run?.ref ?? ''));
  const [streamed, setStreamed] = useState<Record<string, RunLog[]>>({});
  const [showDiff, setShowDiff] = useState(false);
  const [follow, setFollow] = useState(true);
  const [busy, setBusy] = useState(false);
  // Merging writes to your real repository, so it takes two clicks.
  const [armed, setArmed] = useState(false);
  const [mergeResult, setMergeResult] = useState<MergeResult | null>(null);
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
  const discard = async () => {
    if (!run) return;
    setBusy(true);
    const out = await discardRun(run.ref);
    setBusy(false);
    if (out) toast('Worktree removed', { description: `${out.branch} is gone. The commits stay until git prunes them.` });
  };
  const merge = async () => {
    if (!run) return;
    if (!armed) { setArmed(true); return; }
    setArmed(false);
    setBusy(true);
    const result = await mergeRun(run.ref);
    setBusy(false);
    if (!result) return;
    setMergeResult(result);
    if (result.merged) toast.success(`Merged into ${result.into}`, { description: `${result.commit} · undo with ${result.undo}` });
    else toast.error(`${result.conflicts.length} files collide with ${result.into}`, { description: 'Nothing was merged.' });
  };
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);

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
                    {r.status === 'waiting' ? `waiting for you · ${r.waitingOn ?? ''}` : `${progress(r)}% · ${r.diff.files} files`}
                  </p>
                </ListRow>
              ))}
            </div>
          </div>

          {/* the run */}
          <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto">
            <StatGrid cols={4}>
              <Stat label="Changed" value={`${stat.files} files`} sub={`+${stat.insertions} −${stat.deletions} · ${stat.commits} commits`} />
              <Stat label="Tests" value={run.tests.status} tone={run.tests.status === 'passed' ? 'ok' : run.tests.status === 'failed' ? 'danger' : 'neutral'}
                sub={run.tests.command ?? 'no test command found'} />
              <Stat label="Review" value={run.review.findings.length || '—'} tone={run.review.findings.some((f) => f.severity === 'HIGH') ? 'danger' : 'neutral'}
                sub={run.review.by ? `by ${run.review.by}` : 'not reviewed yet'} />
              <Stat label="Wrote with" value={run.model ?? 'no model'} tone={run.model ? 'brand' : 'warn'} sub={run.requestedBy} />
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
                        <span className="tnum shrink-0 text-[12.5px] text-soft">{child.diff.files} files</span>
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
                      <span className="mt-0.5 block truncate text-[12px] text-dim">{s.agent} · {KIND_LABEL[s.kind]}{s.detail ? ` · ${s.detail}` : ''}</span>
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
                    <span className="shrink-0 text-dim">{l.at.slice(11, 19)}</span>
                    <span className={cn('w-2.5 shrink-0 text-center', LEVEL_TONE[l.level])}>{LEVEL_MARK[l.level]}</span>
                    <span className={cn('min-w-0 break-words whitespace-pre-wrap', LEVEL_TONE[l.level])}>{l.line}</span>
                  </div>
                ))}
                {working && <div className="flex gap-2.5 text-dim"><span className="animate-pulse-dot">▊</span></div>}
              </div>
            </Panel>

            {run.review.findings.length > 0 && (
              <Panel flush title="Review" eyebrow={run.review.verdict || `read by ${run.review.by}`}>
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
