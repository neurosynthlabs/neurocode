import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Check,
  X,
  ScanEye,
  Quote,
  Undo2,
  Loader2,
  GitBranch,
  ListChecks,
  MessageSquarePlus,
  Plus,
  FileText,
} from 'lucide-react';
import { toast } from 'sonner';
import { useData } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { ApiError, type RunDoc } from '@/lib/api';
import { fetchModels } from '@/lib/live/models';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { workbenchLink } from '@/lib/live/sessions';
import {
  reviewsApi,
  type CodeReview,
  type ReviewFinding,
  type ReviewRequest,
  type ReviewTarget,
  type ReviewTargets,
} from '@/lib/live/reviews';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { SEVERITY_TONE, work } from '@/lib/live/work';
import { ago } from '@/lib/time';
import { Button } from '@/components/ui/button';
import {
  Page,
  PageHeader,
  PageBody,
  Panel,
  Tag,
  Mono,
  ListRow,
  DataTable,
  Row,
  Cell,
  Stat,
  StatGrid,
  Empty,
  StatusText,
  Segmented,
  Field,
  SelectField,
  KV,
  type Tone,
} from '@/components/os';
import type { ApprovalRequest } from '@/types';

/* Review: every run whose diff was read, across projects, and the signature it waits for. The findings
   are the review step's own (severity, file, note); accepting or refusing is the run's handoff approval,
   the same one the approvals inbox shows, so the two can never disagree. Requesting changes sends the
   run back: the same plan again, as a new run told your notes and these findings. */

type Outcome = { label: string; tone: Tone };

/** The REVIEW.md a run's reviewer was handed, when the repository has one. */
const briefOf = (run: RunDoc) => (run.review as { brief?: { path: string } }).brief?.path ?? null;

/** The run's signature gate: the approval raised at its handoff step, pending or decided. */
function gateOf(run: RunDoc, approvals: ApprovalRequest[]): ApprovalRequest | undefined {
  const handoff = run.steps.find((s) => s.kind === 'handoff');
  return handoff ? approvals.find((a) => a.runRef === run.ref && a.step === handoff.n) : undefined;
}

function outcomeOf(run: RunDoc, gate: ApprovalRequest | undefined): Outcome {
  if (run.merged) return { label: `merged into ${run.merged.into}`, tone: 'ok' };
  if (run.review.reworkedAs) return { label: `sent back · ${run.review.reworkedAs}`, tone: 'warn' };
  if (gate?.status === 'pending') return { label: 'waiting for you', tone: 'brand' };
  if (gate?.status === 'approved') return { label: 'accepted', tone: 'ok' };
  if (gate?.status === 'denied') return { label: 'refused', tone: 'danger' };
  return {
    label: run.status,
    tone: run.status === 'failed' ? 'danger' : 'neutral',
  };
}

type View = 'runs' | 'demand';

/** Two kinds of review share the screen: a run's own review step, and any diff read on demand. */
export default function Review() {
  const [params] = useSearchParams();
  const [view, setView] = useState<View>(params.get('review') ? 'demand' : 'runs');
  const tabs = (
    <Segmented<View>
      options={[
        { id: 'runs', label: 'Runs' },
        { id: 'demand', label: 'On demand' },
      ]}
      value={view}
      onChange={setView}
    />
  );
  return view === 'runs' ? <RunReviews tabs={tabs} /> : <OnDemand tabs={tabs} />;
}

function RunReviews({ tabs }: { tabs: ReactNode }) {
  const nav = useNavigate();
  const { runs, approvals, decide } = useData();
  const { can } = useAuth();
  // A link can name the run to open (/review?ref=RUN-7), the way Plans and Live Runs take one.
  // A link that changes while the screen is open takes over again: what was picked is kept with the
  // link it was picked under, the same way Plans, Tasks and Live Runs keep theirs.
  const linked = useSearchParams()[0].get('ref');
  const [picked, setPicked] = useState<{ link: string | null; ref: string } | null>(null);
  const sel = (picked && picked.link === linked ? picked.ref : null) ?? linked;
  // The send-back notes belong to the run they were written for, so a link to another run never
  // carries them over under its name.
  const [draft, setDraft] = useState<{ ref: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [reviewer, setReviewer] = useState<string | null>(null);

  // Who reads the next diff, as the router would choose now. Left out when the router does not answer.
  useEffect(() => {
    let live = true;
    fetchModels().then(
      (report) => {
        if (live) setReviewer(report.routes.find((r) => r.feature === 'review')?.chain[0]?.model ?? null);
      },
      (e: unknown) => {
        console.error('[NeuroCode] GET /models failed:', e);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  // A run that merges several agents carries the review; the agents' own runs have none.
  const reviewed = useMemo(() => runs.filter((r) => r.review.by && !r.parent), [runs]);
  const r = reviewed.find((x) => x.ref === sel) ?? reviewed[0] ?? null;
  const gate = r ? gateOf(r, approvals) : undefined;
  const notes = r && draft?.ref === r.ref ? draft.text : null;
  const setNotes = (text: string | null) => setDraft(text === null || !r ? null : { ref: r.ref, text });

  const stats = useMemo(() => {
    const gates = reviewed.map((x) => gateOf(x, approvals));
    return {
      accepted: reviewed.filter((x, i) => x.merged || gates[i]?.status === 'approved').length,
      refused: gates.filter((g) => g?.status === 'denied').length,
      sentBack: reviewed.filter((x) => x.review.reworkedAs).length,
      high: reviewed.reduce((n, x) => n + x.review.findings.filter((f) => f.severity === 'HIGH').length, 0),
    };
  }, [reviewed, approvals]);

  const pick = (ref: string) => {
    setPicked({ link: linked, ref });
    setNotes(null);
  };

  if (!r) {
    return (
      <Page>
        <PageHeader
          title="Review"
          subtitle="A different lane reads each run's diff and lists what it found; the run then waits for your signature."
          actions={tabs}
        />
        <PageBody>
          <Empty
            icon={<ScanEye className="size-6" />}
            title="No run has been reviewed yet"
            hint="When an agent run finishes its tests, its diff is read by a different model — or by rules, labelled as such — and the findings land here for your signature."
          />
        </PageBody>
      </Page>
    );
  }

  const outcome = outcomeOf(r, gate);
  const high = r.review.findings.filter((f) => f.severity === 'HIGH').length;
  const waiting = gate?.status === 'pending';
  const canSendBack =
    !r.merged &&
    !r.review.reworkedAs &&
    r.status !== 'queued' &&
    r.status !== 'running' &&
    r.role !== 'check' &&
    (r.status !== 'waiting' || waiting);

  const sign = async (decision: 'approve' | 'deny') => {
    if (!gate) return;
    setBusy(true);
    const ok = await decide(gate.ref, decision);
    setBusy(false);
    if (!ok) return;
    if (decision === 'approve')
      toast.success(`${r.ref} accepted`, {
        description: `The branch is yours to merge: ${r.branch}.`,
      });
    else
      toast(`${r.ref} refused`, {
        description: 'The branch and its worktree are removed.',
      });
  };

  const sendBack = async () => {
    const text = notes?.trim();
    if (!text) return;
    setBusy(true);
    try {
      const made = await work.rework(r.ref, text);
      setNotes(null);
      toast.success(`${r.ref} sent back`, {
        description: `${made.ref} does the work again with your notes and the review's findings.`,
        action: {
          label: 'Open run',
          onClick: () => nav(`/runs?ref=${made.ref}`),
        },
      });
    } catch (e) {
      toast.error('Not sent back', {
        description: e instanceof ApiError ? e.message : 'The local API did not answer.',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page>
      <PageHeader
        title="Review"
        subtitle="A different lane reads each run's diff and lists what it found; the run then waits for your signature."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {reviewer && (
              <Tag tone="brand">
                <ScanEye className="size-3" />
                next review · {reviewer}
              </Tag>
            )}
            {tabs}
          </div>
        }
      />

      <PageBody className="space-y-4">
        <StatGrid cols={5}>
          <Stat label="Reviewed runs" value={reviewed.length} />
          <Stat label="Accepted" value={stats.accepted} tone="ok" sub="signed, or merged" />
          <Stat label="Refused" value={stats.refused} tone="danger" sub="branch removed" />
          <Stat label="Sent back" value={stats.sentBack} tone="warn" sub="done again with notes" />
          <Stat label="High findings" value={stats.high} tone={stats.high ? 'danger' : 'neutral'} />
        </StatGrid>

        <div className="flex min-h-[560px] flex-col gap-3 md:flex-row">
          <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto rounded-md border border-line bg-surface">
            {reviewed.map((x) => {
              const o = outcomeOf(x, gateOf(x, approvals));
              return (
                <ListRow key={x.ref} active={x.ref === r.ref} onClick={() => pick(x.ref)}>
                  <div className="flex items-center gap-2">
                    <Mono tone={x.ref === r.ref ? 'brand' : 'neutral'}>{x.ref}</Mono>
                    <Tag tone={o.tone} className="ml-auto">
                      {o.label}
                    </Tag>
                  </div>
                  <p className="mt-1 truncate text-[12.5px] text-ink-2">
                    {x.taskRef ?? x.planRef} · {x.projectName}
                  </p>
                  <div className="mt-1 flex items-center gap-2 text-[11.5px] text-dim">
                    <span>{x.diff.files} files</span>
                    <span className="text-ok">+{x.diff.insertions}</span>
                    <span className="text-danger">−{x.diff.deletions}</span>
                    <span className="ml-auto">{ago(x.startedAt)}</span>
                  </div>
                </ListRow>
              );
            })}
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            <Panel
              eyebrow={`read by ${r.review.by}${briefOf(r) ? ` · briefed by ${briefOf(r)}` : ''} · ${r.projectName} · ${ago(r.startedAt)}`}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  <Mono tone="brand">{r.ref}</Mono>
                  {r.taskRef}
                  <Tag tone={outcome.tone}>{outcome.label}</Tag>
                </span>
              }
              actions={
                <span className="text-[12px] text-dim">
                  {r.diff.files} files · <span className="text-ok">+{r.diff.insertions}</span>{' '}
                  <span className="text-danger">−{r.diff.deletions}</span>
                </span>
              }
            >
              {r.review.verdict && (
                <div className="flex items-start gap-2.5 rounded-sm border border-line bg-base p-3">
                  <Quote className="mt-0.5 size-3.5 shrink-0 text-brand" />
                  <p className="text-[13.5px] leading-relaxed text-ink-2 italic">{r.review.verdict}</p>
                </div>
              )}

              {notes !== null ? (
                <form
                  className="mt-3 space-y-2 border-t border-line pt-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void sendBack();
                  }}
                >
                  <textarea
                    autoFocus
                    rows={3}
                    maxLength={4000}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    aria-label="What should change"
                    placeholder="What should change? The next run is told this, along with the findings below."
                    className="w-full resize-none rounded-sm border border-line bg-base px-2.5 py-1.5 text-[13px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none"
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" type="submit" disabled={!notes.trim() || busy}>
                      {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Undo2 className="size-3.5" />}
                      Send back
                    </Button>
                    <Button size="sm" type="button" variant="ghost" onClick={() => setNotes(null)}>
                      Cancel
                    </Button>
                    <span className="text-[12px] text-dim">
                      {waiting
                        ? `Refuses ${gate?.ref} and removes ${r.branch}; `
                        : r.removed
                          ? ''
                          : `Removes ${r.branch}; `}
                      the plan runs again as a new run.
                    </span>
                  </div>
                </form>
              ) : (
                (waiting || canSendBack) && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
                    {waiting && can('approvals:decide') && (
                      <>
                        <Button size="sm" disabled={busy} onClick={() => void sign('approve')}>
                          <Check className="size-3.5" />
                          Accept
                        </Button>
                        <Button size="sm" variant="destructive" disabled={busy} onClick={() => void sign('deny')}>
                          <X className="size-3.5" />
                          Refuse
                        </Button>
                      </>
                    )}
                    {canSendBack && can('runs:run') && (!waiting || can('approvals:decide')) && (
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => setNotes('')}>
                        <Undo2 className="size-3.5" />
                        Request changes
                      </Button>
                    )}
                    {waiting && high > 0 && (
                      <span className="text-[12.5px] text-danger">
                        {high} high finding{high > 1 ? 's' : ''} — read them before you sign.
                      </span>
                    )}
                  </div>
                )
              )}
            </Panel>

            <Panel
              eyebrow={`${r.steps.filter((s) => s.status === 'done').length} of ${r.steps.length} steps done`}
              title="What the run did"
              flush
            >
              <div className="divide-y divide-line">
                {r.steps.map((s) => (
                  <div key={s.n} className="flex items-start gap-3 px-3.5 py-2">
                    <span className="w-48 shrink-0">
                      <StatusText state={s.status === 'running' ? 'running' : s.status} label={s.label} />
                    </span>
                    <span className="min-w-0 flex-1 text-[12.5px] text-soft">{s.detail}</span>
                  </div>
                ))}
                {r.tests.command && (
                  <div className="flex items-start gap-3 px-3.5 py-2">
                    <span className="w-48 shrink-0 font-mono text-[12px] text-ink-2">{r.tests.command}</span>
                    <span className="min-w-0 flex-1 text-[12.5px] text-soft">
                      tests {r.tests.status}
                      {r.tests.summary ? ` · ${r.tests.summary}` : ''}
                    </span>
                  </div>
                )}
              </div>
            </Panel>

            <Panel eyebrow={`${r.review.findings.length} findings`} title="Findings" flush>
              {r.review.findings.length === 0 ? (
                <Empty
                  icon={<Check className="size-5" />}
                  title="Nothing to fix"
                  hint="The review raised no finding for this diff."
                />
              ) : (
                <DataTable head={['Severity', 'File', 'Note']}>
                  {r.review.findings.map((f, i) => (
                    <Row key={i}>
                      <Cell>
                        <Tag tone={SEVERITY_TONE[f.severity] ?? 'neutral'}>{f.severity}</Tag>
                      </Cell>
                      <Cell mono className="text-dim">
                        {f.file ? `${f.file}${(f as ReviewFinding).line ? `:${(f as ReviewFinding).line}` : ''}` : '—'}
                      </Cell>
                      <Cell className="max-w-[520px] text-[12.5px] text-ink-2">{f.note}</Cell>
                    </Row>
                  ))}
                </DataTable>
              )}
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}

/* ── On demand: any diff — a branch, the working tree, a range of commits — read the way a run's is ── */

const REVIEW_ACTIVITY = ['Review requested', 'Review done', 'Review failed'];
const STATUS_TONE: Record<CodeReview['status'], Tone> = { running: 'brand', done: 'ok', failed: 'danger' };
const reasonOf = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

function OnDemand({ tabs }: { tabs: ReactNode }) {
  const { project } = useProject();
  const { activity } = useData();
  const { can } = useAuth();
  const [params] = useSearchParams();
  const [picked, setPicked] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [more, setMore] = useState<CodeReview[]>([]);
  const pid = project?.id ?? null;
  const page = useRemote(pid ? `reviews:${pid}` : null, () => reviewsApi.list(pid ?? ''));

  // A review is read in the background; the feed says when one is asked for and when it is done.
  const tick = activity.find((e) => e.projectId === pid && REVIEW_ACTIVITY.includes(e.action))?.id ?? '';
  const { reload } = page;
  useEffect(() => {
    if (tick) reload();
  }, [tick, reload]);

  const reviews = useMemo(() => {
    const first = page.data?.reviews ?? [];
    const seen = new Set(first.map((x) => x.ref));
    return [...first, ...more.filter((x) => !seen.has(x.ref))];
  }, [page.data, more]);
  const sel = picked ?? params.get('review');
  const r = reviews.find((x) => x.ref === sel) ?? reviews[0] ?? null;
  const running = reviews.filter((x) => x.status === 'running').length;
  const high = reviews.reduce((n, x) => n + x.findings.filter((f) => f.severity === 'HIGH').length, 0);

  const loadMore = async () => {
    try {
      const next = await reviewsApi.list(pid ?? '', reviews.length);
      setMore((all) => [...all, ...next.reviews]);
    } catch (e) {
      toast.error('No more loaded', { description: reasonOf(e) });
    }
  };

  const header = (
    <PageHeader
      title="Review"
      subtitle="Any diff, read the way a run's is: a branch against its base, the working tree, or a range of commits — on a lane other than the writer's, with the repository's REVIEW.md as the brief."
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {pid && can('runs:run') && (
            <Button size="sm" onClick={() => setAsking(true)}>
              <Plus className="size-3.5" />
              New review
            </Button>
          )}
          {tabs}
        </div>
      }
    />
  );
  const dialog = asking && project && (
    <AskDialog
      projectId={project.id}
      projectName={project.name}
      onClose={() => setAsking(false)}
      onAsked={(made) => {
        setAsking(false);
        setPicked(made.ref);
        reload();
      }}
    />
  );

  if (!project) {
    return (
      <Page>
        {header}
        <PageBody>
          <Empty
            icon={<ScanEye className="size-6" />}
            title="No project yet"
            hint="Onboard a repository in Projects, and any branch, commit range or uncommitted change in it can be reviewed here."
          />
        </PageBody>
      </Page>
    );
  }

  return (
    <Page>
      {header}
      <PageBody className="space-y-4">
        {page.error ? (
          <Empty
            title="The reviews did not load"
            hint={page.error}
            action={
              <Button size="sm" variant="outline" onClick={reload}>
                Try again
              </Button>
            }
          />
        ) : !page.data ? (
          <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the reviews…" />
        ) : reviews.length === 0 ? (
          <Empty
            icon={<GitBranch className="size-6" />}
            title={`Nothing in ${project.name} has been reviewed on demand yet`}
            hint="Pick a branch — one of yours, or one someone pushed — the working tree, or a range of commits. A model on a lane other than the writer's reads it; with no model, rules read it and say so."
            action={
              can('runs:run') ? (
                <Button size="sm" onClick={() => setAsking(true)}>
                  <Plus className="size-3.5" />
                  New review
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <StatGrid cols={4}>
              <Stat label="Reviews" value={page.data.total} sub={project.name} />
              <Stat label="Being read" value={running} tone={running ? 'brand' : 'neutral'} />
              <Stat label="High findings" value={high} tone={high ? 'danger' : 'neutral'} sub="in the ones listed" />
              <Stat
                label="Read by rules"
                value={reviews.filter((x) => x.offline).length}
                sub="no model could answer"
              />
            </StatGrid>
            <div className="flex min-h-[560px] flex-col gap-3 md:flex-row">
              <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto rounded-md border border-line bg-surface">
                {reviews.map((x) => (
                  <ListRow key={x.ref} active={x.ref === r?.ref} onClick={() => setPicked(x.ref)}>
                    <div className="flex items-center gap-2">
                      <Mono tone={x.ref === r?.ref ? 'brand' : 'neutral'}>{x.ref}</Mono>
                      <Tag tone={STATUS_TONE[x.status]} className="ml-auto">
                        {x.status === 'done' ? `${x.findings.length} findings` : x.status}
                      </Tag>
                    </div>
                    <p className="mt-1 truncate text-[12.5px] text-ink-2">{x.title}</p>
                    <div className="mt-1 flex items-center gap-2 text-[11.5px] text-dim">
                      {x.stats.files !== undefined && <span>{x.stats.files} files</span>}
                      {x.stats.insertions !== undefined && <span className="text-ok">+{x.stats.insertions}</span>}
                      {x.stats.deletions !== undefined && <span className="text-danger">−{x.stats.deletions}</span>}
                      <span className="ml-auto">{x.createdAt ? ago(x.createdAt) : ''}</span>
                    </div>
                  </ListRow>
                ))}
                {reviews.length < page.data.total && (
                  <button
                    type="button"
                    className="w-full px-4 py-2.5 text-left text-[12.5px] text-brand hover:underline"
                    onClick={() => void loadMore()}
                  >
                    Show more ({page.data.total - reviews.length} older)
                  </button>
                )}
              </div>
              <div className="min-w-0 flex-1 space-y-3">{r && <DemandDetail key={r.ref} r={r} onChanged={reload} />}</div>
            </div>
          </>
        )}
        {dialog}
      </PageBody>
    </Page>
  );
}

function DemandDetail({ r, onChanged }: { r: CodeReview; onChanged: () => void }) {
  const nav = useNavigate();
  const { can } = useAuth();
  const [busy, setBusy] = useState<'session' | 'plan' | null>(null);
  const done = r.status === 'done';
  const findings = r.findings.length > 0;

  const toSession = async () => {
    setBusy('session');
    try {
      const made = await reviewsApi.toSession(r.ref);
      onChanged();
      toast.success(`${made.ref} started`, {
        description: 'It goes through the findings against the code.',
        action: { label: 'Open', onClick: () => nav(`/sessions?ref=${encodeURIComponent(made.ref)}`) },
      });
    } catch (e) {
      toast.error('Not sent', { description: reasonOf(e) });
    } finally {
      setBusy(null);
    }
  };
  const toPlan = async () => {
    setBusy('plan');
    try {
      const plan = await reviewsApi.toPlan(r.ref);
      onChanged();
      toast.success(`${plan.ref} written`, {
        description: 'It waits in Plans for you before anything runs.',
        action: { label: 'Open', onClick: () => nav(`/plans?ref=${encodeURIComponent(plan.ref)}`) },
      });
    } catch (e) {
      toast.error('No plan made', { description: reasonOf(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <Panel
        eyebrow={
          r.status === 'running'
            ? `asked by ${r.requestedBy ?? 'someone'} · ${r.createdAt ? ago(r.createdAt) : ''}`
            : `read by ${r.model ?? 'nobody'}${r.lane ? ` on ${r.lane}` : ''} · ${r.finishedAt ? ago(r.finishedAt) : ''}`
        }
        title={
          <span className="flex flex-wrap items-center gap-2">
            <Mono tone="brand">{r.ref}</Mono>
            {r.title}
            <Tag tone={STATUS_TONE[r.status]}>{r.status}</Tag>
          </span>
        }
        actions={
          r.stats.files !== undefined && (
            <span className="text-[12px] text-dim">
              {r.stats.files} files · <span className="text-ok">+{r.stats.insertions ?? 0}</span>{' '}
              <span className="text-danger">−{r.stats.deletions ?? 0}</span>
            </span>
          )
        }
      >
        {r.status === 'running' ? (
          <p className="flex items-center gap-2 text-[13px] text-soft">
            <Loader2 className="size-3.5 animate-spin" />
            Reading the diff. It lands here when it is done, and in Activity.
          </p>
        ) : r.verdict ? (
          <div
            className={`flex items-start gap-2.5 rounded-sm border p-3 ${r.status === 'failed' ? 'border-danger/30 bg-danger/5' : 'border-line bg-base'}`}
          >
            <Quote className={`mt-0.5 size-3.5 shrink-0 ${r.status === 'failed' ? 'text-danger' : 'text-brand'}`} />
            <p className="text-[13.5px] leading-relaxed text-ink-2 italic">{r.verdict}</p>
          </div>
        ) : null}
        {r.offline && (
          <p className="mt-2 text-[12.5px] text-warn">
            No model could answer, so rules read this diff: secrets, debugging left in, TODOs and untouched tests.
          </p>
        )}
        {done && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
            {can('sessions:chat') && (
              <Button size="sm" variant="outline" disabled={!findings || busy !== null} onClick={() => void toSession()}>
                {busy === 'session' ? <Loader2 className="size-3.5 animate-spin" /> : <MessageSquarePlus className="size-3.5" />}
                Send to a session
              </Button>
            )}
            {can('plans:compile') && (
              <Button size="sm" variant="outline" disabled={!findings || busy !== null} onClick={() => void toPlan()}>
                {busy === 'plan' ? <Loader2 className="size-3.5 animate-spin" /> : <ListChecks className="size-3.5" />}
                Make a plan from these findings
              </Button>
            )}
            {!findings && <span className="text-[12.5px] text-dim">Nothing was found, so there is nothing to send.</span>}
          </div>
        )}
        {r.sent.length > 0 && (
          <p className="mt-2.5 text-[12.5px] text-dim">
            Sent to{' '}
            {r.sent.map((s, i) => (
              <span key={`${s.kind}-${s.ref}`}>
                {i > 0 && ', '}
                <Link
                  to={s.kind === 'session' ? `/sessions?ref=${encodeURIComponent(s.ref)}` : `/plans?ref=${encodeURIComponent(s.ref)}`}
                  className="text-brand hover:underline"
                >
                  {s.ref}
                </Link>{' '}
                by {s.by}
              </span>
            ))}
          </p>
        )}
      </Panel>

      {r.status !== 'running' && (
        <Panel eyebrow="What the reviewer was handed" title="The diff and its brief">
          <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
            <KV k="Target" v={r.target === 'working-tree' ? 'the working tree' : r.target === 'branch' ? 'a branch' : 'a range of commits'} />
            <KV
              k="Patch"
              v={
                r.fingerprint ? (
                  <Mono>
                    {r.fingerprint.replace(/^sha256:/, '').slice(0, 12)}
                    {r.stats.bytes !== undefined ? ` · ${Math.max(1, Math.round(r.stats.bytes / 1024))} KB` : ''}
                    {r.stats.truncated ? ' · cut to fit' : ''}
                  </Mono>
                ) : (
                  'none'
                )
              }
            />
            {r.stats.commits !== undefined && r.target !== 'working-tree' && <KV k="Commits" v={r.stats.commits} />}
            {r.stats.untracked ? <KV k="New files read" v={r.stats.untracked} /> : null}
            <KV k="Brief" v={r.brief ? <Mono>{r.brief.path}</Mono> : <span className="text-dim">no REVIEW.md</span>} />
            <KV
              k="Writer's lane"
              v={r.writer ? `${r.writer.lane ?? 'unknown'} · ${r.writer.run} — the reviewer avoided it` : <span className="text-dim">not one of our runs</span>}
            />
            {r.stats.fetched && <KV k="Fetched" v={<Mono>{r.stats.fetched}</Mono>} />}
            {r.instructions.length > 0 && <KV k="Instructions" v={r.instructions.map((x) => x.path).join(', ')} />}
            {r.taste.length > 0 && <KV k="Taste rules" v={r.taste.join(', ')} />}
          </div>
          {!r.brief && (
            <p className="mt-2 text-[12.5px] text-dim">
              Add REVIEW.md (or .neurocode/REVIEW.md) to the repository to tell the reviewer what matters here and what to leave alone.
            </p>
          )}
        </Panel>
      )}

      {done && (
        <Panel eyebrow={`${r.findings.length} findings`} title="Findings" flush>
          {r.findings.length === 0 ? (
            <Empty icon={<Check className="size-5" />} title="Nothing to fix" hint="The review raised no finding for this diff." />
          ) : (
            <DataTable head={['Severity', 'Where', 'Note']}>
              {r.findings.map((f, i) => (
                <Row key={i}>
                  <Cell>
                    <Tag tone={SEVERITY_TONE[f.severity] ?? 'neutral'}>{f.severity}</Tag>
                  </Cell>
                  <Cell mono className="text-dim">
                    {f.file ? (
                      <Link to={workbenchLink(r.projectId, f.file, f.line)} className="hover:text-brand" title="Open in Workbench">
                        {f.file}
                        {f.line ? `:${f.line}` : ''}
                      </Link>
                    ) : (
                      '—'
                    )}
                  </Cell>
                  <Cell className="max-w-[520px] text-[12.5px] text-ink-2">{f.note}</Cell>
                </Row>
              ))}
            </DataTable>
          )}
        </Panel>
      )}

      {r.paths.length > 0 && (
        <Panel eyebrow={`${r.paths.length} files`} title="What changed" flush>
          <div className="divide-y divide-line">
            {r.paths.map((p) => (
              <div key={p.path} className="flex items-center gap-3 px-3.5 py-2 text-[12.5px]">
                <FileText className="size-3.5 shrink-0 text-dim" />
                <span className="min-w-0 flex-1 truncate font-mono text-ink-2">{p.path}</span>
                <span className="text-ok">+{p.insertions}</span>
                <span className="text-danger">−{p.deletions}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}

const TARGETS: { id: ReviewTarget; label: string }[] = [
  { id: 'branch', label: 'A branch' },
  { id: 'working-tree', label: 'Working tree' },
  { id: 'commit-range', label: 'Commits' },
];

function AskDialog({
  projectId,
  projectName,
  onClose,
  onAsked,
}: {
  projectId: string;
  projectName: string;
  onClose: () => void;
  onAsked: (made: CodeReview) => void;
}) {
  const [source, setSource] = useState('');
  const where = useRemote(`review-targets:${projectId}:${source}`, () => reviewsApi.targets(projectId, source));
  const t: ReviewTargets | null = where.data;
  const [target, setTarget] = useState<ReviewTarget>('branch');
  const [head, setHead] = useState('');
  const [base, setBase] = useState('');
  const [fetch, setFetch] = useState(false);
  const [busy, setBusy] = useState(false);
  const branches = t?.branches ?? [];
  const current = t?.current ?? '';
  const pickedHead = head || branches.find((b) => !b.current)?.name || '';
  const pickedBase = base || current;
  const remote = branches.find((b) => b.name === pickedHead)?.remote ?? false;
  const ready =
    t?.git &&
    (target === 'working-tree'
      ? (t.dirty ?? 0) > 0
      : target === 'branch'
        ? pickedHead && pickedBase && pickedHead !== pickedBase
        : head.trim() && base.trim());

  const ask = async () => {
    setBusy(true);
    const body: ReviewRequest =
      target === 'working-tree'
        ? { target, source }
        : target === 'branch'
          ? { target, source, head: pickedHead, base: pickedBase, fetch: remote && fetch }
          : { target, source, head: head.trim(), base: base.trim() };
    try {
      const made = await reviewsApi.ask(projectId, body);
      toast.success(`${made.ref} is being read`, { description: `${made.title}. It lands here when it is done.` });
      onAsked(made);
    } catch (e) {
      toast.error('Not asked', { description: reasonOf(e) });
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o && !busy) onClose();
      }}
    >
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Review a diff in {projectName}</DialogTitle>
          <DialogDescription>
            Read the way a run's diff is: on a lane other than the one that wrote it when that is known, held to the project's instructions and its REVIEW.md. Nothing is changed.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          {t && t.sources.length > 1 && (
            <SelectField
              label="Source"
              value={source}
              onChange={(v) => {
                setSource(v);
                setHead('');
                setBase('');
              }}
              options={t.sources.map((x) => ({ value: x.label, label: x.primary ? `${x.name} (first source)` : x.name }))}
            />
          )}
          <Segmented<ReviewTarget> options={TARGETS} value={target} onChange={setTarget} />
          {where.error ? (
            <p className="text-[13px] text-danger">{where.error}</p>
          ) : !t ? (
            <p className="flex items-center gap-2 text-[13px] text-dim">
              <Loader2 className="size-3.5 animate-spin" />
              Reading the checkout…
            </p>
          ) : !t.git ? (
            <p className="text-[13px] text-soft">This checkout is not a git repository, so there is no diff to read.</p>
          ) : target === 'working-tree' ? (
            <p className="text-[13px] text-soft">
              {t.dirty
                ? `${t.dirty} ${t.dirty === 1 ? 'path differs' : 'paths differ'} from the last commit, new files included. They are read as they are; nothing is staged.`
                : 'Nothing in the working tree differs from its last commit.'}
            </p>
          ) : target === 'branch' ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <SelectField
                  label="Branch to review"
                  value={pickedHead}
                  onChange={setHead}
                  options={branches.map((b) => ({ value: b.name, label: b.remote ? `${b.name} · pushed` : b.name }))}
                />
                <SelectField
                  label="Against"
                  value={pickedBase}
                  onChange={setBase}
                  options={branches.filter((b) => !b.remote).map((b) => ({ value: b.name, label: b.current ? `${b.name} · checked out` : b.name }))}
                />
              </div>
              {remote && (
                <label className="flex items-center gap-2 text-[13px] text-ink-2">
                  <input type="checkbox" className="accent-brand" checked={fetch} onChange={(e) => setFetch(e.target.checked)} />
                  Fetch {pickedHead} from its remote first, with your git credentials
                </label>
              )}
              <p className="text-[12.5px] text-dim">What the branch changed since it left the base — the diff a pull request shows.</p>
            </>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="From" value={base} onChange={setBase} placeholder="v1.4.0 or a commit" mono />
              <Field label="To" value={head} onChange={setHead} placeholder={current || 'main'} mono />
            </div>
          )}
          {t?.git && (
            <p className="text-[12.5px] text-dim">
              {t.brief ? (
                <>
                  The reviewer is briefed by <Mono>{t.brief.path}</Mono>.
                </>
              ) : (
                'No REVIEW.md in this repository: the reviewer reads with the project’s instructions alone.'
              )}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={() => void ask()} disabled={busy || !ready}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <ScanEye className="size-3.5" />}
            Review
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
