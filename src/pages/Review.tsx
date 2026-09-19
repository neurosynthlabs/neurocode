import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Check, X, ScanEye, Quote, Undo2, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { useData } from '@/lib/data';
import { useAuth } from '@/lib/auth';
import { ApiError, type RunDoc } from '@/lib/api';
import { fetchModels } from '@/lib/live/models';
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
  type Tone,
} from '@/components/os';
import type { ApprovalRequest } from '@/types';

/* Review: every run whose diff was read, across projects, and the signature it waits for. The findings
   are the review step's own (severity, file, note); accepting or refusing is the run's handoff approval,
   the same one the approvals inbox shows, so the two can never disagree. Requesting changes sends the
   run back: the same plan again, as a new run told your notes and these findings. */

type Outcome = { label: string; tone: Tone };

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

export default function Review() {
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
          reviewer && (
            <Tag tone="brand">
              <ScanEye className="size-3" />
              next review · {reviewer}
            </Tag>
          )
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
              eyebrow={`read by ${r.review.by} · ${r.projectName} · ${ago(r.startedAt)}`}
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
                        {f.file || '—'}
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
