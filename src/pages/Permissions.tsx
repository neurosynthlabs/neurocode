import { useMemo, useState } from 'react';
import { ShieldCheck, ShieldAlert, ShieldX, Check, X, Search, Box, Globe, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page,
  PageHeader,
  PageBody,
  Panel,
  Tag,
  RiskPill,
  Mono,
  Toolbar,
  Field,
  SelectField,
  DataTable,
  Row,
  Cell,
  Stat,
  StatGrid,
  Empty,
  SectionTitle,
  Segmented,
} from '@/components/os';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { fetchRoster } from '@/lib/live/agents';
import { fetchModels } from '@/lib/live/models';
import { projectLabel, work } from '@/lib/live/work';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { ApprovalRequest } from '@/types';

/* Permissions: the gates the runtime really has, and nothing it does not. An agent works alone inside its
   own worktree; the first run of a project's tests asks you once and the answer is kept; a run that
   changed a file stops at your signature. The inbox is those gates waiting, the rules are the answers
   kept, and the sandbox is what the runtime guarantees, read from the runtime itself. */

const TIERS = [
  {
    key: 'alone',
    label: 'Works alone',
    tone: 'ok' as const,
    icon: ShieldCheck,
    verdict: 'no gate',
    what: 'writes files in its own git worktree and branch · reads the files the plan names · has its diff read by a reviewer lane',
    why: 'None of it touches your checkout. What a model returns is written as files; nothing it says is executed.',
  },
  {
    key: 'once',
    label: 'Asks once per project',
    tone: 'warn' as const,
    icon: ShieldAlert,
    verdict: 'you, the first time',
    what: "runs the project's own test command inside the run's worktree",
    why: 'The first run in a project stops and asks. Your answer is kept as a standing rule, so it never asks again.',
  },
  {
    key: 'always',
    label: 'Always your signature',
    tone: 'danger' as const,
    icon: ShieldX,
    verdict: 'you, every time',
    what: 'accepting a run that changed a file · merging an accepted branch into your repository (needs runs:merge)',
    why: 'Refuse a run and its branch and worktree are removed. Nothing is merged unless someone allowed to merge does it.',
  },
];

const WEEK = 7 * 24 * 60 * 60 * 1000;

/** "6 min" · "2 h 10 min" · "40 s": how long a decision took. */
function span(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export default function Permissions() {
  const [tab, setTab] = useState<'inbox' | 'rules' | 'sandbox'>('inbox');
  const [answer, setAnswer] = useState('all');
  const [q, setQ] = useState('');
  const { approvals, runs, projects, decide: record } = useData();

  // "The last 7 days" counts back from when the page was opened.
  const [openedAt] = useState(() => Date.now());
  const pending = approvals.filter((a) => a.status === 'pending');
  const resolved = approvals.filter((a) => a.status !== 'pending');

  // A decision on a test gate writes a rule, so the rules are read again whenever a gate is decided.
  const rules = useRemote(`rules:${resolved.length}`, work.rules);
  const roster = useRemote(tab === 'sandbox' ? 'roster' : null, fetchRoster);
  const models = useRemote(tab === 'sandbox' ? 'models' : null, fetchModels);

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (rules.data ?? []).filter(
      (r) =>
        (answer === 'all' || r.answer === answer) &&
        (!t || `${r.projectName} ${r.command ?? ''} ${r.decidedBy ?? ''}`.toLowerCase().includes(t)),
    );
  }, [rules.data, answer, q]);

  const week = useMemo(() => {
    const since = openedAt - WEEK;
    const recent = resolved.filter((a) => a.decidedAt && new Date(a.decidedAt).getTime() >= since);
    return {
      approved: recent.filter((a) => a.status === 'approved').length,
      denied: recent.filter((a) => a.status === 'denied').length,
      median: median(
        recent
          .filter((a) => a.decidedBy)
          .map((a) => new Date(a.decidedAt ?? '').getTime() - new Date(a.requestedAt).getTime()),
      ),
    };
  }, [resolved, openedAt]);

  /** Which kind of step a gate stopped: what approving or refusing it actually does depends on it. */
  const kindOf = (a: ApprovalRequest) => runs.find((r) => r.ref === a.runRef)?.steps.find((s) => s.n === a.step)?.kind;

  const decide = async (a: ApprovalRequest, v: 'approve' | 'deny') => {
    if (!(await record(a.ref, v))) return;
    const kind = kindOf(a);
    if (v === 'approve') {
      toast.success(`${a.ref} approved`, {
        description:
          kind === 'test'
            ? 'The tests run now, and this project will not ask again.'
            : kind === 'handoff'
              ? 'The branch is yours to merge.'
              : 'Your signature is recorded against this action.',
      });
    } else {
      toast(`${a.ref} denied`, {
        description:
          kind === 'test'
            ? 'Tests will not run in this project; the run carries on without them.'
            : kind === 'handoff'
              ? 'The branch and its worktree are removed.'
              : 'Your answer is recorded.',
      });
    }
  };

  const lanes = (models.data?.lanes ?? []).filter((l) => l.ready);
  const calls = models.data?.totals24h.calls ?? 0;

  return (
    <Page>
      <PageHeader
        title="Permissions"
        subtitle="What an agent may do alone, what asks you once, and what always stops at your desk."
        actions={
          <Segmented
            options={[
              { id: 'inbox', label: `Inbox (${pending.length})` },
              {
                id: 'rules',
                label: rules.data ? `Rules (${rules.data.length})` : 'Rules',
              },
              { id: 'sandbox', label: 'Sandbox' },
            ]}
            value={tab}
            onChange={setTab}
          />
        }
      />

      <PageBody className="space-y-4">
        <div className="grid grid-cols-1 gap-3 stagger lg:grid-cols-3">
          {TIERS.map((t) => {
            const I = t.icon;
            return (
              <Panel
                key={t.key}
                className={cn(
                  'accent-top',
                  t.tone === 'danger' && 'border-danger/35',
                  t.tone === 'warn' && 'border-warn/30',
                )}
                eyebrow={t.key === 'once' && rules.data ? `${rules.data.length} projects answered` : t.verdict}
                title={
                  <span className="flex items-center gap-2">
                    <I className={cn('size-4', `text-${t.tone}`)} />
                    {t.label}
                  </span>
                }
                actions={<Tag tone={t.tone}>{t.verdict}</Tag>}
              >
                <p className="text-[12.5px] text-ink-2">{t.what}</p>
                <p className="mt-2 border-t border-line pt-2 text-[12.5px] text-dim">{t.why}</p>
              </Panel>
            );
          })}
        </div>

        {tab === 'inbox' && (
          <>
            <StatGrid cols={3}>
              <Stat
                label="Waiting on you"
                value={pending.length}
                tone={pending.length ? 'warn' : 'ok'}
                sub="nothing moves until you decide"
              />
              <Stat
                label="Approved 7d"
                value={week.approved}
                tone="ok"
                sub={week.median === null ? 'no decision this week' : `median decision ${span(week.median)}`}
              />
              <Stat label="Denied 7d" value={week.denied} tone="danger" sub="decided in the last 7 days" />
            </StatGrid>

            {pending.length === 0 ? (
              <Empty
                icon={<ShieldCheck className="size-6" />}
                title="Nothing is waiting on you"
                hint="Every gated action has been decided. Agents are free to keep working."
              />
            ) : (
              <div className="space-y-3 stagger">
                {pending.map((a) => (
                  <Panel
                    key={a.id}
                    className={cn(
                      'accent-left',
                      a.risk === 'CRITICAL' || a.risk === 'HIGH' ? 'border-danger/40' : 'border-warn/35',
                    )}
                    eyebrow={`${a.agent} · ${a.tool} · requested ${ago(a.requestedAt)}`}
                    title={
                      <span className="flex flex-wrap items-center gap-2">
                        <Mono tone="brand">{a.ref}</Mono>
                        {a.title}
                      </span>
                    }
                    actions={
                      <span className="flex items-center gap-2">
                        <Tag tone="neutral">{projectLabel(projects, a.projectId)}</Tag>
                        <RiskPill risk={a.risk} />
                      </span>
                    }
                  >
                    <SectionTitle>What it is about</SectionTitle>
                    <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">
                      {a.payload}
                    </pre>
                    <SectionTitle className="mt-3">Why it is asking</SectionTitle>
                    <p className="text-[13.5px] leading-relaxed text-ink-2">{a.reason}</p>
                    <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
                      <Button size="sm" onClick={() => void decide(a, 'approve')}>
                        <Check className="size-3.5" />
                        Approve
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => void decide(a, 'deny')}>
                        <X className="size-3.5" />
                        Deny
                      </Button>
                      <span className="ml-auto text-[12px] text-dim">
                        Your decision is recorded under your name, and it is final.
                      </span>
                    </div>
                  </Panel>
                ))}
              </div>
            )}

            {resolved.length > 0 && (
              <>
                <SectionTitle>Decided</SectionTitle>
                <Panel flush>
                  <DataTable head={['Ref', 'Title', 'Agent', 'Tool', 'Risk', 'Outcome', 'Decided']}>
                    {resolved.map((a) => (
                      <Row key={a.id}>
                        <Cell mono>{a.ref}</Cell>
                        <Cell className="max-w-[420px] text-ink">{a.title}</Cell>
                        <Cell className="text-[12.5px]">{a.agent}</Cell>
                        <Cell mono className="text-dim">
                          {a.tool}
                        </Cell>
                        <Cell>
                          <RiskPill risk={a.risk} bare />
                        </Cell>
                        <Cell>
                          <Tag tone={a.status === 'approved' ? 'ok' : 'danger'}>{a.status}</Tag>
                        </Cell>
                        <Cell className="text-dim">
                          {a.decidedAt ? ago(a.decidedAt) : ''}
                          {a.decidedBy ? '' : ' · closed with its run'}
                        </Cell>
                      </Row>
                    ))}
                  </DataTable>
                </Panel>
              </>
            )}
          </>
        )}

        {tab === 'rules' && (
          <>
            <Toolbar>
              <Field
                className="w-64"
                value={q}
                onChange={setQ}
                icon={<Search className="size-3.5" />}
                placeholder="Search projects, commands, people…"
                onClear={() => setQ('')}
              />
              <SelectField
                className="w-36"
                value={answer}
                onChange={setAnswer}
                options={[
                  { value: 'all', label: 'Any answer' },
                  { value: 'allowed', label: 'allowed' },
                  { value: 'refused', label: 'refused' },
                ]}
              />
              {rules.data && (
                <span className="ml-auto text-[12.5px] text-dim">
                  {shown.length} of {rules.data.length}
                </span>
              )}
            </Toolbar>
            <Panel flush>
              {rules.loading ? (
                <p className="px-3.5 py-3 text-[13px] text-dim">
                  <Loader2 className="mr-2 inline size-3.5 animate-spin" />
                  reading the rules…
                </p>
              ) : rules.error ? (
                <Empty
                  title="The rules did not load"
                  hint={rules.error}
                  action={
                    <Button size="sm" variant="outline" onClick={rules.reload}>
                      Try again
                    </Button>
                  }
                />
              ) : !rules.data?.length ? (
                <Empty
                  icon={<ShieldAlert className="size-6" />}
                  title="No standing rules yet"
                  hint="The first time a run wants to run a project's tests it asks you, and your answer is kept here per project."
                />
              ) : shown.length === 0 ? (
                <Empty title="No rule matches" />
              ) : (
                <DataTable head={['Project', 'Rule', 'Command', 'Answer', 'Decided', 'By']}>
                  {shown.map((r) => (
                    <Row key={r.projectId} className={cn(r.answer === 'refused' && 'bg-danger/4')}>
                      <Cell className="text-ink">{r.projectName}</Cell>
                      <Cell className="text-[12.5px] text-ink-2">Run the project's tests</Cell>
                      <Cell mono className="text-dim">
                        {r.command ?? 'none found on this machine'}
                      </Cell>
                      <Cell>
                        <Tag tone={r.answer === 'allowed' ? 'ok' : 'danger'}>{r.answer}</Tag>
                      </Cell>
                      <Cell className="text-dim">{ago(r.decidedAt)}</Cell>
                      <Cell className="text-[12.5px]">
                        {r.decidedBy ?? <span className="text-dim">not recorded</span>}
                      </Cell>
                    </Row>
                  ))}
                </DataTable>
              )}
            </Panel>
          </>
        )}

        {tab === 'sandbox' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel
              eyebrow="Enforced by the runtime"
              title={
                <span className="flex items-center gap-1.5">
                  <Box className="size-3.5 text-brand" />
                  What every run is held to
                </span>
              }
            >
              {roster.loading ? (
                <p className="text-[13px] text-dim">
                  <Loader2 className="mr-2 inline size-3.5 animate-spin" />
                  asking the runtime…
                </p>
              ) : roster.error ? (
                <p className="text-[13px] text-danger">{roster.error}</p>
              ) : (
                <div className="space-y-1.5">
                  {roster.data?.enforced.map((t) => (
                    <p key={t} className="flex items-start gap-1.5 text-[12.5px] text-ink-2">
                      <ShieldCheck className="mt-px size-3.5 shrink-0 text-ok" />
                      {t}
                    </p>
                  ))}
                </div>
              )}
            </Panel>
            <Panel
              eyebrow="What reaches a model"
              title={
                <span className="flex items-center gap-1.5">
                  <Globe className="size-3.5 text-brand" />
                  Where calls go
                </span>
              }
            >
              {models.loading ? (
                <p className="text-[13px] text-dim">
                  <Loader2 className="mr-2 inline size-3.5 animate-spin" />
                  asking the router…
                </p>
              ) : models.error ? (
                <p className="text-[13px] text-danger">{models.error}</p>
              ) : (
                models.data && (
                  <>
                    <p className="text-[12.5px] text-ink-2">
                      <span className="font-medium text-ink">Routing: {models.data.preference}.</span>{' '}
                      {models.data.preference in models.data.preferences
                        ? models.data.preferences[models.data.preference as keyof typeof models.data.preferences]
                        : `Only the ${models.data.preference} lane.`}
                    </p>
                    <p className="mt-2 text-[12.5px] text-dim">
                      An edit step sends the files it may change to the lane that writes; a review sends the diff.
                    </p>
                    <SectionTitle className="mt-3 mb-1.5">Lanes that can answer now</SectionTitle>
                    {lanes.length === 0 ? (
                      <p className="text-[12.5px] text-dim">None — no model is configured.</p>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {lanes.map((l) => (
                          <Tag key={l.id} tone={l.hosting === 'local' ? 'ok' : 'neutral'}>
                            {l.label} · {l.hosting}
                          </Tag>
                        ))}
                      </div>
                    )}
                    <p className="mt-3 border-t border-line pt-2.5 text-[12px] text-dim">
                      {calls === 0
                        ? 'No model call in the last 24 hours.'
                        : `Of ${calls} calls in the last 24 hours, ${models.data.totals24h.local} were answered on this machine, ${models.data.totals24h.remote} by a remote lane and ${models.data.totals24h.offline} without a model.`}
                    </p>
                  </>
                )
              )}
            </Panel>
          </div>
        )}
      </PageBody>
    </Page>
  );
}
