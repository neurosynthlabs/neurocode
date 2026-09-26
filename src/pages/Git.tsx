import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { GitMerge, GitPullRequest, TriangleAlert, Check, Search, FolderGit2, Loader2, RefreshCw, GitCompare, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Ascii, ListRow, Segmented, Field,
  DataTable, Row, Cell, Stat, StatGrid, KV, Empty, Toolbar, SectionTitle, More,
} from '@/components/os';
import type { RunDoc, RunStep } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import {
  gitApi, type GitChangedFile, type GitMergePreview, type GitOverview, type GitReview, type GitReviewCheck, type GitWorktree,
} from '@/lib/live/git';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';
import { ago } from './code/format';

const PR_TONE = { draft: 'neutral', open: 'info', awaiting_human: 'warn', merged: 'ok', blocked: 'danger' } as const;
const CHK_TONE = { pass: 'ok', fail: 'danger', running: 'info', skipped: 'neutral', warn: 'warn' } as const;

/** The active project's repository, read from git on this machine. A project with no code has nothing to read. */
export default function Git() {
  const { project, all } = useProject();
  const nav = useNavigate();
  if (project?.source) return <LiveGit project={project} />;
  return (
    <Page>
      <PageHeader title="Git & Worktrees" />
      <PageBody>
        <Empty
          icon={<FolderGit2 className="size-6" />}
          title={all.length === 0 ? 'No repository onboarded yet' : `${project?.name ?? 'This project'} has no code on this machine`}
          hint="Onboard a repository to see its branches, worktrees and commits."
          action={<Button size="sm" variant="outline" onClick={() => nav('/projects')}>Open Projects</Button>}
        />
      </PageBody>
    </Page>
  );
}

/* ═══════════════════════════════════════════════════════════════
   Live: the repository on this machine, read from git on every load.
   ═══════════════════════════════════════════════════════════════ */

type LiveTab = 'worktrees' | 'conflicts' | 'commits' | 'reviews';
const STATUS_TONE = { clean: 'ok', merged: 'ok', ahead: 'info', dirty: 'warn', conflict: 'danger' } as const;
const PREVIEW_TONE = { clean: 'ok', collides: 'danger', stale: 'warn' } as const;
const CHECK_OF: Record<RunStep['status'], GitReviewCheck['status']> = {
  done: 'pass', failed: 'fail', running: 'running', waiting: 'warn', skipped: 'skipped', todo: 'skipped',
};
const REVIEW_LIMIT = 20;
const sha7 = (sha: string) => sha.slice(0, 7);

/** Two clicks for anything that writes to a real repository or deletes a branch. The second must come within 4 s. */
function useArmed() {
  const [armed, setArmed] = useState<string | null>(null);
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(null), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);
  return { armed, arm: (key: string) => setArmed(key), disarm: () => setArmed(null) };
}

function LiveGit({ project }: { project: Project }) {
  const { runs } = useData();
  const [tab, setTab] = useState<LiveTab>('worktrees');
  // The overview reloads when one of this project's runs changes — not on every tick of the stream.
  const mine = useMemo(() => runs.filter((r) => r.projectId === project.id), [runs, project.id]);
  const stamp = mine.map((r) => `${r.ref}:${r.status}:${r.removed}:${r.merged?.commit ?? ''}:${r.diff.commits}`).join('|');
  const o = useRemote(`${project.id}:git:${stamp}`, () => gitApi.overview(project.id));
  const data = o.data;
  const reviewable = mine.filter((r) => (r.role === 'solo' || r.role === 'integration') && !r.removed);
  const reviews = reviewable.slice(0, REVIEW_LIMIT);

  return (
    <Page>
      <PageHeader
        title="Git & Worktrees"
        subtitle={`Read from ${project.name}'s repository on this machine.`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              options={[
                { id: 'worktrees', label: data ? `Worktrees (${data.stats.worktrees})` : 'Worktrees' },
                { id: 'conflicts', label: 'Conflicts' },
                { id: 'commits', label: 'Commits' },
                { id: 'reviews', label: reviewable.length > reviews.length ? `Reviews (${reviews.length} of ${reviewable.length})` : `Reviews (${reviews.length})` },
              ]}
              value={tab} onChange={setTab} />
            <Button size="sm" variant="outline" onClick={o.reload} disabled={o.loading} aria-label="Read the repository again">
              {o.loading ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}Refresh
            </Button>
          </div>
        }
      >
        {data?.available && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pb-3 text-[12.5px] text-dim">
            <span className="flex items-center gap-1.5"><FolderGit2 className="size-3.5" /><Mono>{data.repo}</Mono></span>
            <span>checked out <Mono tone="brand">{data.head.branch}</Mono> at <Mono>{sha7(data.head.sha)}</Mono></span>
            <Tag tone={data.head.dirty ? 'warn' : 'ok'}>{data.head.dirty ? 'uncommitted changes' : 'clean'}</Tag>
            {data.shallow && <span className="text-warn">shallow clone: older history is not on this machine</span>}
          </div>
        )}
      </PageHeader>

      {o.error && !data ? (
        <PageBody><Empty title="The repository did not load" hint={o.error} action={<Button size="sm" variant="outline" onClick={o.reload}>Try again</Button>} /></PageBody>
      ) : !data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the repository…" /></PageBody>
      ) : !data.available ? (
        <PageBody><Empty icon={<FolderGit2 className="size-6" />} title="No repository to read" hint={data.reason} /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          <StatGrid cols={5}>
            <Stat label="Worktrees" value={data.stats.worktrees} sub={`${data.stats.dirty} dirty`} />
            <Stat label="Clean merges" value={data.stats.clean} tone={data.stats.clean ? 'ok' : 'neutral'} sub="merge-tree says clean" />
            <Stat label="Collisions" value={data.stats.collisions} tone={data.stats.collisions ? 'danger' : 'neutral'} sub="with the checkout" />
            <Stat label="Commits today" value={data.stats.commitsToday} sub={`${data.stats.agentCommitsToday} by agents`} />
            <Stat label="Awaiting you" value={data.stats.awaitingYou} tone={data.stats.awaitingYou ? 'warn' : 'neutral'} sub="merge gates pending"
              onClick={() => setTab('reviews')} />
          </StatGrid>

          {tab === 'worktrees' && <LiveWorktrees project={project} data={data} stamp={stamp} />}
          {tab === 'conflicts' && <LiveConflicts pid={project.id} stamp={stamp} />}
          {tab === 'commits' && <LiveCommits pid={project.id} stamp={stamp} />}
          {tab === 'reviews' && <LiveReviews runs={reviews} data={data} />}
        </PageBody>
      )}
    </Page>
  );
}

/** Where a run's branch was pushed, and the page where its pull request is opened — or why there is none. */
function PushedLine({ pushed }: { pushed: NonNullable<RunDoc['pushed']> }) {
  return (
    <p className="flex flex-wrap items-center gap-x-1.5">
      <Upload className="size-3 text-brand" />Pushed to {pushed.remote} at <Mono>{sha7(pushed.sha)}</Mono> by {pushed.by}, {ago(pushed.at)} ·
      {pushed.compareUrl
        ? <a href={pushed.compareUrl} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">Open pull request ↗</a>
        : <span>no known pull request page for this remote</span>}
    </p>
  );
}

function LiveWorktrees({ project, data, stamp }: { project: Project; data: GitOverview; stamp: string }) {
  const { mergeRun, runs } = useData();
  const { can } = useAuth();
  const { armed, arm, disarm } = useArmed();
  const [picked, setPicked] = useState<string | null>(null);
  const [against, setAgainst] = useState<'base' | 'head'>('base');
  const [busy, setBusy] = useState(false);
  const wt = data.worktrees.find((w) => w.id === picked) ?? data.worktrees[0] ?? null;
  const pushed = wt?.runRef ? runs.find((r) => r.ref === wt.runRef)?.pushed ?? null : null;
  // Every branch git still has carries a last commit, so a gone run with none has no branch left to diff.
  const branchKnown = !!wt && wt.branch !== '(detached)' && !(wt.gone && wt.lastCommitAt === null);
  const diff = useRemote(wt && branchKnown ? `${project.id}:diff:${wt.branch}:${against}:${stamp}` : null,
    () => gitApi.diff(project.id, wt?.branch ?? '', against));

  const merge = async (w: GitWorktree) => {
    if (!w.runRef) return;
    if (armed !== w.id) { arm(w.id); return; }
    disarm();
    setBusy(true);
    const result = await mergeRun(w.runRef);
    setBusy(false);
    if (!result) return;
    if (result.merged) toast.success(`Merged into ${result.into}`, { description: `${result.commit} · undo with ${result.undo}` });
    else toast.error(`${result.conflicts.length} files collide with ${result.into}`, { description: 'Nothing was merged.' });
  };

  if (!wt) {
    return (
      <Empty icon={<FolderGit2 className="size-6" />} title="No worktree beside the checkout"
        hint="A dispatched plan gives each agent its own worktree here." />
    );
  }

  const glyph = (w: GitWorktree) => (w.gone ? '∅' : w.status === 'merged' ? '✓' : w.status === 'conflict' ? '✕' : w.status === 'dirty' ? '~' : w.status === 'ahead' ? '↑' : '·');
  const tree = [
    `${data.repo}   (${data.head.branch} @ ${sha7(data.head.sha)}${data.head.dirty ? ', uncommitted changes' : ''})`,
    ...data.worktrees.map((w, i) => `${i === data.worktrees.length - 1 ? '└──' : '├──'} ${w.branch}  ${glyph(w)} ${w.agent} · ${w.taskRef}  +${w.additions} −${w.deletions}${w.gone ? '  (worktree gone)' : ''}`),
  ].join('\n');

  return (
    <>
      <div className="flex min-h-[420px] flex-col gap-3 md:flex-row">
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[320px] overflow-y-auto rounded-xl border border-line bg-surface">
          {data.worktrees.map((w) => (
            <ListRow key={w.id} active={w.id === wt.id} onClick={() => setPicked(w.id)}>
              <div className="flex items-center gap-2">
                <Dot state={w.gone ? 'disconnected' : w.status} pulse={w.runStatus === 'running'} />
                <Mono className="truncate">{w.branch}</Mono>
                {w.gone ? <Tag tone="warn" className="ml-auto">gone</Tag>
                  : w.runRef && runs.some((r) => r.ref === w.runRef && r.pushed) ? <Tag tone="info" className="ml-auto">pushed</Tag> : null}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11.5px] text-dim">
                <span className="truncate">{w.madeBy === 'git' ? 'made outside NeuroCode' : w.agent}</span>
                <span className="ml-auto text-ok">+{w.additions}</span>
                <span className="text-danger">−{w.deletions}</span>
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-dim">
                <span>{w.taskRef}</span>
                <span className="ml-auto">{w.filesChanged} files · {w.lastCommitAt ? ago(w.lastCommitAt) : 'no commit'}</span>
              </div>
            </ListRow>
          ))}
        </div>

        <div className="min-w-0 flex-1 space-y-3">
          <Panel eyebrow={wt.taskRef}
            title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{wt.branch}</Mono><Tag tone={STATUS_TONE[wt.status]}>{wt.status}</Tag>{wt.gone && <Tag tone="warn">worktree gone</Tag>}</span>}
            actions={<div className="flex flex-wrap gap-1.5">
              <Button size="xs" variant={against === 'head' ? 'default' : 'outline'} disabled={!branchKnown}
                onClick={() => setAgainst((a) => (a === 'head' ? 'base' : 'head'))}>
                <GitCompare className="size-3" />{against === 'head' ? `Against ${data.head.branch}` : 'Compare'}
              </Button>
              {can('runs:merge') && (
                <Button size="xs" variant="outline" disabled={!wt.canMerge || busy} title={wt.mergeBlocked ?? `Merge into ${data.head.branch}`}
                  onClick={() => void merge(wt)}>
                  <GitMerge className="size-3" />{armed === wt.id ? `Click again to merge into ${data.head.branch}` : 'Merge'}
                </Button>
              )}
            </div>}>
            <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2 xl:grid-cols-4">
              <KV k="Owner" v={wt.madeBy === 'git' ? 'made outside NeuroCode' : wt.agent} />
              <KV k="Path" v={wt.path} mono />
              <KV k="Ahead / behind" v={`${wt.ahead} / ${wt.behind}`} />
              <KV k="Last commit" v={wt.lastCommit || '—'} mono />
            </div>
            <div className="mt-2 space-y-1 text-[12.5px] text-dim">
              {wt.runRef && <p>Run <Link to={`/runs?ref=${encodeURIComponent(wt.runRef)}`} className="font-mono text-brand hover:underline">{wt.runRef}</Link> · {wt.runStatus}{wt.base ? <> · from <Mono>{sha7(wt.base)}</Mono></> : null}</p>}
              {wt.merged && <p className="text-ok">Merged into {wt.merged.into} as <Mono>{wt.merged.commit}</Mono> · undo with <Mono>{wt.merged.undo}</Mono></p>}
              {wt.mergeBlocked && !wt.merged && <p>Merge: {wt.mergeBlocked}</p>}
              {pushed ? <PushedLine pushed={pushed} />
                : wt.runRef ? <p>Accept <Link to={`/runs?ref=${encodeURIComponent(wt.runRef)}`} className="text-brand hover:underline">the run</Link>, then push from there: your credentials, never forced.</p>
                  : <p>To share it for review, push it yourself: <Mono>git push origin {wt.branch}</Mono></p>}
            </div>
          </Panel>

          <Panel flush title="Diff"
            eyebrow={diff.data ? `${diff.data.files.length} files · ${against === 'head' ? `what merging into ${diff.data.against} would bring` : `since ${diff.data.against || 'its base'}`}` : against === 'head' ? `against ${data.head.branch}` : 'since its base'}>
            {!branchKnown ? <Empty title="No branch to read" hint="Its branch no longer exists." />
              : diff.error ? <Empty title="The diff did not load" hint={diff.error} />
                : !diff.data ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the diff…" />
                  : diff.data.files.length === 0 ? <Empty title="No changes" hint={against === 'head' ? `Merging it would bring nothing into ${data.head.branch}.` : 'No commits beyond its base.'} />
                    : <DiffFiles files={diff.data.files} truncated={diff.data.truncated} />}
          </Panel>

          <MergePreviewPanel data={data} />
        </div>
      </div>

      <More label="Worktree layout"><Ascii className="overflow-auto">{tree}</Ascii></More>
    </>
  );
}

function DiffFiles({ files, truncated }: { files: GitChangedFile[]; truncated: boolean }) {
  return (
    <div className="divide-y divide-line/60">
      {files.map((f) => (
        <div key={f.path} className="px-5 py-3">
          <div className="flex items-center gap-2">
            <Tag tone={f.change === 'A' ? 'ok' : f.change === 'D' ? 'danger' : f.change === 'R' ? 'info' : 'warn'}>{f.change}</Tag>
            <Mono className="truncate">{f.path}</Mono>
            <span className="ml-auto shrink-0 tnum text-[12px]"><span className="text-ok">+{f.additions}</span> <span className="text-danger">−{f.deletions}</span></span>
          </div>
          {f.hunk && (
            <pre className="ascii mt-1.5 overflow-x-auto rounded-lg bg-base p-2.5 ring-1 ring-line/60 ring-inset">
              {f.hunk.split('\n').map((l, i) => (
                <div key={i} className={l.startsWith('+') ? 'text-ok' : l.startsWith('-') ? 'text-danger' : l.startsWith('@') ? 'text-brand' : ''}>{l}</div>
              ))}
            </pre>
          )}
        </div>
      ))}
      {truncated && <p className="px-5 py-2.5 text-[12.5px] text-warn">Truncated. Read the rest with git in the worktree.</p>}
    </div>
  );
}

function MergePreviewPanel({ data }: { data: GitOverview }) {
  const rows: GitMergePreview[] = data.mergePreview;
  return (
    <Panel eyebrow={`If merged into ${data.head.branch}`} title="Merge preview" flush>
      {rows.length === 0 ? <Empty title="Nothing waiting to merge" hint={`No open branch has commits that ${data.head.branch} does not.`} /> : (
        <DataTable head={['Branch', 'Into', 'Result', 'Files', 'Note']}>
          {rows.map((m) => (
            <Row key={m.worktreeId}>
              <Cell mono>{m.branch}</Cell>
              <Cell mono className="text-dim">{m.target}</Cell>
              <Cell><Tag tone={PREVIEW_TONE[m.result]}>{m.result}</Tag></Cell>
              <Cell className="tnum">{m.files}</Cell>
              <Cell className="max-w-[420px] text-[12.5px] text-soft">
                {m.note}{m.onFile && <Mono className="ml-1">{m.onFile}</Mono>}
                {m.collidesWith && <span className="mt-0.5 block text-danger">also collides with <Mono>{m.collidesWith}</Mono></span>}
              </Cell>
            </Row>
          ))}
        </DataTable>
      )}
    </Panel>
  );
}

function LiveConflicts({ pid, stamp }: { pid: string; stamp: string }) {
  const c = useRemote(`${pid}:conflicts:${stamp}`, () => gitApi.conflicts(pid));
  if (c.error) return <Empty title="The collisions did not load" hint={c.error} />;
  if (!c.data) return <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Asking git where the branches collide…" />;
  if (c.data.length === 0) return <Empty icon={<Check className="size-6" />} title="No collisions" hint="Every open branch merges cleanly." />;
  return (
    <div className="space-y-3">
      {c.data.map((x) => (
        <Panel key={x.id} className="border-danger/35" eyebrow={x.region ? `${x.taskRef} · ${x.region}` : x.taskRef}
          title={<span className="flex items-center gap-2"><TriangleAlert className="size-3.5 text-danger" /><Mono>{x.file}</Mono></span>}>
          {x.hunks.length === 0 ? <p className="text-[13px] text-dim">Only the file list was recorded, not the lines.</p> : (
            <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2">
              {x.hunks.map((h) => (
                <div key={h.side} className="rounded-lg bg-base p-3 ring-1 ring-line/60 ring-inset">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2">
                    <Tag tone={h.side === 'ours' ? 'info' : 'violet'}>{h.side}</Tag>
                    <Mono>{h.branch}</Mono>
                    <span className="ml-auto text-[11.5px] text-dim">{h.agent} · <span className="font-mono">{h.commit}</span>{h.at ? ` · ${ago(h.at)}` : ''}</span>
                  </div>
                  <pre className="ascii overflow-x-auto">{h.lines}</pre>
                </div>
              ))}
            </div>
          )}
          <SectionTitle className="mt-3 mb-1.5">What the runtime enforces</SectionTitle>
          <ul className="space-y-1">
            {x.policy.map((p) => (
              <li key={p} className="flex items-start gap-1.5 text-[12.5px] text-soft"><Check className="mt-px size-3 shrink-0 text-brand" />{p}</li>
            ))}
          </ul>
          <div className="mt-2.5 flex items-center justify-between gap-3 border-t border-line/60 pt-2.5">
            <p className="text-[13px] text-ink-2">{x.resolution}</p>
            <Tag tone="neutral">{x.resolvedBy}</Tag>
          </div>
        </Panel>
      ))}
    </div>
  );
}

function LiveCommits({ pid, stamp }: { pid: string; stamp: string }) {
  const [q, setQ] = useState('');
  const c = useRemote(`${pid}:commits:${stamp}`, () => gitApi.commits(pid));
  const all = useMemo(() => c.data?.commits ?? [], [c.data]);
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? all.filter((x) => (x.message + x.author + x.sha + x.branch).toLowerCase().includes(s)) : all;
  }, [q, all]);
  return (
    <>
      <Toolbar>
        <Field className="w-72" value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search commits, authors, branches…" onClear={() => setQ('')} />
        <span className="ml-auto text-[12.5px] text-dim">{shown.length} of {all.length}</span>
      </Toolbar>
      {c.data?.shallow && <p className="pb-3 text-[12.5px] text-warn">Shallow clone: older commits are not on this machine.</p>}
      <Panel flush>
        {c.error ? <Empty title="The history did not load" hint={c.error} />
          : !c.data ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the history…" />
            : shown.length === 0 ? <Empty title={all.length ? 'No commit matches' : 'No commits'} /> : (
              <DataTable head={['SHA', 'Message', 'Author', 'Branch', 'Files', 'When']}>
                {shown.map((x) => (
                  <Row key={x.sha}>
                    <Cell mono className="text-brand">{sha7(x.sha)}</Cell>
                    <Cell className="max-w-[560px] text-ink">{x.message}</Cell>
                    <Cell className="text-[12.5px]">{x.author}</Cell>
                    <Cell mono className="text-dim">{x.branch}</Cell>
                    <Cell className="tnum">{x.files}</Cell>
                    <Cell className="text-dim">{ago(x.at)}</Cell>
                  </Row>
                ))}
              </DataTable>
            )}
      </Panel>
    </>
  );
}

/** A run at its gate, read as a review: the runs and approvals the store already holds, plus git's merge preview. */
function reviewOf(run: RunDoc, data: GitOverview, gateTool: string | undefined): GitReview {
  const waitingOnMerge = run.status === 'waiting' && !!gateTool?.startsWith('Merge(');
  const state: GitReview['state'] =
    run.status === 'failed' || run.status === 'cancelled' || run.conflicts.length > 0 ? 'blocked'
      : waitingOnMerge ? 'awaiting_human'
        : run.status === 'done' ? (run.merged ? 'merged' : 'open')
          : 'draft';
  const preview = data.mergePreview.find((p) => p.worktreeId === run.ref);
  const checks: GitReviewCheck[] = run.steps.map((s) => ({
    id: `${run.ref}:${s.n}`, name: `${s.kind} / ${s.label}`, status: CHECK_OF[s.status], detail: s.detail, durationS: Math.round((s.ms ?? 0) / 1000),
  }));
  if (preview) {
    checks.push({
      id: `${run.ref}:merge-tree`, name: 'merge / conflict scan', durationS: 0, detail: preview.note,
      status: preview.result === 'clean' ? 'pass' : preview.result === 'collides' ? 'fail' : 'warn',
    });
  }
  // Who wrote it is whoever the run's own steps name for the work — an agent, or the Orchestrator for a merge —
  // never a name made up for a run that recorded none.
  const writers = run.agent ? [run.agent] : [...new Set(run.steps.filter((s) => s.kind === 'edit' || s.kind === 'merge').map((s) => s.agent).filter(Boolean))];
  return {
    id: run.id, ref: run.ref, title: (run.requirement.split('\n')[0] || run.planRef || run.ref).slice(0, 120),
    branch: run.branch, base: data.head.branch, author: writers.length ? `by ${writers.join(', ')} · for ${run.requestedBy}` : `for ${run.requestedBy}`,
    state, files: run.diff.files, additions: run.diff.insertions, deletions: run.diff.deletions,
    commits: run.diff.commits, openedAt: run.startedAt, reviewers: run.review.by ? [run.review.by] : [], checks,
    body: [run.review.verdict, run.tests.summary].filter(Boolean).join(' '),
  };
}

function LiveReviews({ runs, data }: { runs: RunDoc[]; data: GitOverview }) {
  const { approvals, decide, mergeRun } = useData();
  const { can } = useAuth();
  const { armed, arm, disarm } = useArmed();
  const [busy, setBusy] = useState<string | null>(null);

  if (runs.length === 0) {
    return <Empty icon={<GitPullRequest className="size-6" />} title="Nothing to review" hint="A finished run waits here for your signature." />;
  }

  const approve = async (run: RunDoc) => {
    if (!run.waitingOn) return;
    setBusy(run.ref);
    const ok = await decide(run.waitingOn, 'approve');
    setBusy(null);
    if (ok) toast('Approved', { description: `${run.ref} finishes in the background; merge it here after.` });
  };
  const refuse = async (run: RunDoc) => {
    if (!run.waitingOn) return;
    const key = `refuse:${run.ref}`;
    if (armed !== key) { arm(key); return; }
    disarm();
    setBusy(run.ref);
    const ok = await decide(run.waitingOn, 'deny');
    setBusy(null);
    if (ok) toast('Refused', { description: `${run.branch} and its worktree are being removed.` });
  };
  const merge = async (run: RunDoc) => {
    const key = `merge:${run.ref}`;
    if (armed !== key) { arm(key); return; }
    disarm();
    setBusy(run.ref);
    const result = await mergeRun(run.ref);
    setBusy(null);
    if (!result) return;
    if (result.merged) toast.success(`Merged into ${result.into}`, { description: `${result.commit} · undo with ${result.undo}` });
    else toast.error(`${result.conflicts.length} files collide with ${result.into}`, { description: 'Nothing was merged.' });
  };

  return (
    <div className="space-y-3">
      {runs.map((run) => {
        const gate = run.waitingOn ? approvals.find((a) => a.ref === run.waitingOn) : undefined;
        const p = reviewOf(run, data, gate?.tool);
        const wt = data.worktrees.find((w) => w.runRef === run.ref);
        // The overview knows what the row cannot — a dirty checkout, a branch git already has — so its answer wins.
        const canMerge = wt ? wt.canMerge : run.status === 'done' && !run.merged && !run.removed && run.diff.files > 0;
        const pending = gate?.status === 'pending';
        return (
          <Panel key={run.id} eyebrow={`${p.branch} → ${p.base} · opened ${ago(p.openedAt)}`}
            title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{p.ref}</Mono>{p.title}<Tag tone={PR_TONE[p.state]}>{p.state.replace('_', ' ')}</Tag>{run.pushed && <Tag tone="info">pushed</Tag>}</span>}
            actions={<span className="text-[12px] text-dim">{p.files} files · <span className="text-ok">+{p.additions}</span> <span className="text-danger">−{p.deletions}</span> · {p.commits} commits</span>}>
            {p.body && <p className="max-w-4xl text-[13px] leading-relaxed text-ink-2">{p.body}</p>}
            <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div>
                <SectionTitle>Checks</SectionTitle>
                {p.checks.length === 0 ? <p className="text-[12.5px] text-dim">No steps recorded.</p> : (
                  <div className="divide-y divide-line/60 rounded-lg ring-1 ring-line/60 ring-inset">
                    {p.checks.map((ch) => (
                      <div key={ch.id} className="flex items-center gap-2 px-3 py-1.5">
                        <Dot state={ch.status} pulse={ch.status === 'running'} />
                        <span className="min-w-0 truncate text-[13px] text-ink-2">{ch.name}</span>
                        <span className="ml-auto truncate text-[12px] text-dim">{ch.detail}</span>
                        <Tag tone={CHK_TONE[ch.status]}>{ch.status}</Tag>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <SectionTitle>Gate</SectionTitle>
                <div className={cn('rounded-lg p-3 ring-1 ring-inset', pending ? 'bg-warn/8 ring-warn/30' : 'ring-line/60')}>
                  <p className={cn('text-[13px]', pending ? 'text-warn' : 'text-ink-2')}>
                    {gate && pending ? `${gate.ref} · ${gate.risk} risk · ${gate.reason}` : run.note || (run.merged ? `Merged into ${run.merged.into} as ${run.merged.commit}.` : 'No gate is open for this run.')}
                  </p>
                  <p className="mt-1 text-[12px] text-dim">{p.author}{p.reviewers.length ? ` · reviewed by ${p.reviewers.join(', ')}` : ''}</p>
                  {run.pushed && <div className="mt-1 text-[12px] text-dim"><PushedLine pushed={run.pushed} /></div>}
                  <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    {p.state === 'awaiting_human' && pending && can('approvals:decide') && (
                      <>
                        <Button size="xs" disabled={busy === run.ref} onClick={() => void approve(run)}><Check className="size-3" />Approve</Button>
                        <Button size="xs" variant="destructive" disabled={busy === run.ref} onClick={() => void refuse(run)}>
                          {armed === `refuse:${run.ref}` ? 'Click again: the branch is deleted' : 'Refuse & discard'}
                        </Button>
                      </>
                    )}
                    {p.state === 'open' && can('runs:merge') && (
                      <Button size="xs" disabled={!canMerge || busy === run.ref} title={wt?.mergeBlocked ?? undefined} onClick={() => void merge(run)}>
                        <GitMerge className="size-3" />{armed === `merge:${run.ref}` ? `Click again to merge into ${data.head.branch}` : 'Merge'}
                      </Button>
                    )}
                    {p.state === 'open' && !canMerge && wt?.mergeBlocked && <span className="text-[12px] text-dim">{wt.mergeBlocked}</span>}
                    <Link to={`/runs?ref=${encodeURIComponent(run.ref)}`} className="ml-auto text-[12.5px] text-brand hover:underline">Open run →</Link>
                  </div>
                </div>
              </div>
            </div>
          </Panel>
        );
      })}
    </div>
  );
}
