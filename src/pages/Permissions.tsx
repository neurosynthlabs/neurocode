import { useMemo, useState } from 'react';
import {
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  Search,
  Box,
  Globe,
  Loader2,
  Plus,
  Pencil,
  Trash2,
  FlaskConical,
} from 'lucide-react';
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
import { ApiError } from '@/lib/api';
import { GateActions } from '@/components/runs/GateActions';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { fetchRoster } from '@/lib/live/agents';
import { fetchModels } from '@/lib/live/models';
import {
  projectLabel,
  work,
  RULE_TOOLS,
  REFUSED_UNLESS_ALLOWED,
  RULES_PERMISSION,
  type RuleAction,
  type RuleTool,
  type RuleTrial,
  type ToolRule,
} from '@/lib/live/work';
import { gateKind } from '@/lib/live/runtime';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';

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
  const [tab, setTab] = useState<'inbox' | 'rules' | 'tools' | 'sandbox'>('inbox');
  const [answer, setAnswer] = useState('all');
  const [q, setQ] = useState('');
  const { approvals, projects } = useData();

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

  const lanes = (models.data?.lanes ?? []).filter((l) => l.ready);
  const calls = models.data?.totals24h.calls ?? 0;

  return (
    <Page>
      <PageHeader
        title="Permissions"
        actions={
          <Segmented
            options={[
              { id: 'inbox', label: `Inbox (${pending.length})` },
              {
                id: 'rules',
                label: rules.data ? `Rules (${rules.data.length})` : 'Rules',
              },
              { id: 'tools', label: 'Tool rules' },
              { id: 'sandbox', label: 'Sandbox' },
            ]}
            value={tab}
            onChange={setTab}
          />
        }
      />

      <PageBody className="space-y-4">
        {/* The gate model is the Inbox's subject, and the three cards are how it is explained. On the Rules,
            Tool rules and Sandbox tabs it is a hundred words of theory above a table that names its own
            rule, so they stay here — word for word — on the one tab they belong to. */}
        {tab === 'inbox' && (
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
              >
                <p className="text-[12.5px] text-ink-2">{t.what}</p>
                <p className="mt-2 border-t border-line pt-2 text-[12.5px] text-dim">{t.why}</p>
              </Panel>
            );
          })}
        </div>
        )}

        {tab === 'inbox' && (
          <>
            <StatGrid cols={3}>
              <Stat
                label="Waiting on you"
                value={pending.length}
                tone={pending.length ? 'warn' : 'ok'}
              />
              <Stat
                label="Approved 7d"
                value={week.approved}
                tone="ok"
                sub={week.median === null ? 'no decision this week' : `median decision ${span(week.median)}`}
              />
              <Stat label="Denied 7d" value={week.denied} tone="danger" />
            </StatGrid>

            {pending.length === 0 ? (
              <Empty
                icon={<ShieldCheck className="size-6" />}
                title="Nothing is waiting on you"
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
                        {gateKind(a) === 'question' && <Tag tone="info">Agent asks</Tag>}
                        <Tag tone="neutral">{projectLabel(projects, a.projectId)}</Tag>
                        <RiskPill risk={a.risk} />
                      </span>
                    }
                  >
                    <SectionTitle>{gateKind(a) === 'question' ? 'What the agent asks' : 'What it is about'}</SectionTitle>
                    <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">
                      {a.payload}
                    </pre>
                    <SectionTitle className="mt-3">Why it is asking</SectionTitle>
                    <p className="text-[13.5px] leading-relaxed text-ink-2">{a.reason}</p>
                    <GateActions approval={a} />
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

        {tab === 'tools' && <ToolRules projects={projects} />}

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

/* ── tool rules ─────────────────────────────────────────────────── */

const ANSWER_TONE: Record<RuleAction, 'ok' | 'warn' | 'danger'> = { allow: 'ok', ask: 'warn', deny: 'danger' };
const toolLabel = (id: RuleTool) => RULE_TOOLS.find((t) => t.id === id)?.label ?? id;
const toolOf = (id: RuleTool) => RULE_TOOLS.find((t) => t.id === id) ?? RULE_TOOLS[0];
const why = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

interface Draft {
  id: number | null;
  tool: RuleTool;
  scope: string;
  pattern: string;
  action: RuleAction;
  note: string;
}

const EMPTY: Draft = { id: null, tool: 'web_fetch', scope: 'workspace', pattern: '', action: 'deny', note: '' };

/** Allow, ask or deny, written ahead of time: the table, the form that writes one, and a box that says
    what a tool would do here, and which rule says so, without doing it. */
function ToolRules({ projects }: { projects: Project[] }) {
  const { can } = useAuth();
  const mayWrite = can(RULES_PERMISSION);
  const [scope, setScope] = useState('all');
  const [tool, setTool] = useState('all');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [armed, setArmed] = useState<number | null>(null);
  const [version, setVersion] = useState(0);
  const rules = useRemote(`tool-rules:${version}`, () => work.toolRules());

  const shown = useMemo(
    () =>
      (rules.data ?? []).filter(
        (r) =>
          (scope === 'all' || (scope === 'workspace' ? r.projectId === null : r.projectId === scope)) &&
          (tool === 'all' || r.tool === tool),
      ),
    [rules.data, scope, tool],
  );

  const scopes = [
    { value: 'workspace', label: 'Workspace — every project' },
    ...projects.map((p) => ({ value: p.id, label: `${p.name} only` })),
  ];

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const saved =
        draft.id === null
          ? await work.addToolRule({
              tool: draft.tool,
              pattern: draft.pattern.trim(),
              action: draft.action,
              note: draft.note.trim(),
              projectId: draft.scope === 'workspace' ? null : draft.scope,
            })
          : await work.changeToolRule(draft.id, {
              pattern: draft.pattern.trim(),
              action: draft.action,
              note: draft.note.trim(),
            });
      toast.success(draft.id === null ? `Rule #${saved.id} added` : `Rule #${saved.id} changed`, {
        description: `${saved.action} · ${toolLabel(saved.tool)} · ${saved.pattern}`,
      });
      setDraft(null);
      setVersion((v) => v + 1);
    } catch (e) {
      toast.error('Rule not saved', { description: why(e) });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (r: ToolRule) => {
    if (armed !== r.id) {
      setArmed(r.id);
      window.setTimeout(() => setArmed((a) => (a === r.id ? null : a)), 4000);
      return;
    }
    setArmed(null);
    try {
      await work.removeToolRule(r.id);
      toast(`Rule #${r.id} removed`, { description: `${toolLabel(r.tool)} · ${r.pattern} asks again.` });
      setVersion((v) => v + 1);
    } catch (e) {
      toast.error('Rule not removed', { description: why(e) });
    }
  };

  const edit = (r: ToolRule) =>
    setDraft({
      id: r.id,
      tool: r.tool,
      scope: r.projectId ?? 'workspace',
      pattern: r.pattern,
      action: r.action,
      note: r.note,
    });

  const draftTool = draft ? toolOf(draft.tool) : null;
  const blocker = !draft
    ? ''
    : !draft.pattern.trim()
      ? `Write a pattern over the ${draftTool?.subject}.`
      : draft.pattern.length > 300
        ? 'A pattern is at most 300 characters.'
        : '';

  return (
    <>
      <Toolbar>
        <SelectField
          className="w-52"
          value={scope}
          onChange={setScope}
          options={[{ value: 'all', label: 'Every scope' }, ...scopes]}
        />
        <SelectField
          className="w-44"
          value={tool}
          onChange={setTool}
          options={[{ value: 'all', label: 'Every tool' }, ...RULE_TOOLS.map((t) => ({ value: t.id, label: t.label }))]}
        />
        {rules.data && (
          <span className="text-[12.5px] text-dim">
            {shown.length} of {rules.data.length}
          </span>
        )}
        <Button
          size="sm"
          className="ml-auto"
          disabled={!mayWrite}
          title={mayWrite ? undefined : `Writing tool rules needs ${RULES_PERMISSION}.`}
          onClick={() => setDraft({ ...EMPTY, scope: scope === 'all' ? 'workspace' : scope })}
        >
          <Plus className="size-3.5" />
          Add rule
        </Button>
      </Toolbar>

      {draft && draftTool && (
        <Panel
          className="accent-left"
          eyebrow={draft.id === null ? 'New rule' : `Rule #${draft.id} · its tool and scope stay as they are`}
          title={draft.id === null ? 'Write a tool rule' : 'Change this rule'}
        >
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {draft.id === null ? (
              <SelectField
                label="Tool"
                value={draft.tool}
                onChange={(v) => setDraft({ ...draft, tool: v as RuleTool })}
                options={RULE_TOOLS.map((t) => ({ value: t.id, label: `${t.label} — matched on the ${t.subject}` }))}
              />
            ) : (
              <Field label="Tool" value={toolLabel(draft.tool)} onChange={() => {}} disabled />
            )}
            {draft.id === null ? (
              <SelectField
                label="Holds for"
                value={draft.scope}
                onChange={(v) => setDraft({ ...draft, scope: v })}
                options={scopes}
              />
            ) : (
              <Field
                label="Holds for"
                value={scopes.find((x) => x.value === draft.scope)?.label ?? draft.scope}
                onChange={() => {}}
                disabled
              />
            )}
            <Field
              label={`Pattern over the ${draftTool.subject}`}
              value={draft.pattern}
              onChange={(v) => setDraft({ ...draft, pattern: v })}
              placeholder={draftTool.example}
              mono
              hint="* matches anything, / included; ? one character. Case matters."
            />
            <Field
              label="Note (optional)"
              value={draft.note}
              onChange={(v) => setDraft({ ...draft, note: v })}
              placeholder="Why this rule exists"
            />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Segmented
              options={[
                { id: 'allow', label: 'Allow' },
                { id: 'ask', label: 'Ask' },
                { id: 'deny', label: 'Deny' },
              ]}
              value={draft.action}
              onChange={(v) => setDraft({ ...draft, action: v })}
            />
            <span className="text-[12px] text-dim">
              {draft.action === 'allow'
                ? 'Goes ahead without asking anyone.'
                : draft.action === 'ask'
                  ? 'A person answers first.'
                  : 'Never happens, whoever asks.'}
            </span>
          </div>
          {!draftTool.consultedBy && (
            <p className="mt-3 rounded-sm border border-warn/30 bg-warn/8 px-3 py-2 text-[12.5px] text-warn">
              Nothing consults {draftTool.label.toLowerCase()} rules yet. The rule is kept, and applies once runs and
              sessions read it.
            </p>
          )}
          <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
            <Button size="sm" disabled={!!blocker || saving} title={blocker || undefined} onClick={() => void save()}>
              {saving && <Loader2 className="size-3.5 animate-spin" />}
              {draft.id === null ? 'Add rule' : 'Save'}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <span className="ml-auto text-[12px] text-dim">Written to the audit log under your name.</span>
          </div>
        </Panel>
      )}

      <Panel flush>
        {rules.loading ? (
          <p className="px-3.5 py-3 text-[13px] text-dim">
            <Loader2 className="mr-2 inline size-3.5 animate-spin" />
            reading the tool rules…
          </p>
        ) : rules.error ? (
          <Empty
            title="The tool rules did not load"
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
            title="No tool rules yet"
            hint="With no rule every tool asks: a person answers first. Add one to allow what you trust or deny what must never happen."
          />
        ) : shown.length === 0 ? (
          <Empty title="No rule matches" hint="Choose another scope or tool." />
        ) : (
          <DataTable head={['#', 'Holds for', 'Tool', 'Pattern', 'Answer', 'Note', 'By', '']}>
            {shown.map((r) => {
              const t = toolOf(r.tool);
              return (
                <Row key={r.id} className={cn(r.action === 'deny' && 'bg-danger/4')}>
                  <Cell mono className="text-dim">
                    {r.id}
                  </Cell>
                  <Cell className="text-[12.5px]">{r.projectName ?? (r.projectId ? r.projectId : 'Workspace')}</Cell>
                  <Cell className="text-[12.5px]">
                    {t.label}
                    {!t.consultedBy && <span className="block text-[11.5px] text-dim">kept · not consulted yet</span>}
                  </Cell>
                  <Cell mono className="max-w-[280px] break-all text-ink">
                    {r.pattern}
                  </Cell>
                  <Cell>
                    <Tag tone={ANSWER_TONE[r.action]}>{r.action}</Tag>
                  </Cell>
                  <Cell className="max-w-[240px] text-[12.5px] text-soft">{r.note}</Cell>
                  <Cell className="text-[12.5px]">
                    {r.createdBy ?? <span className="text-dim">not recorded</span>}
                    {r.updatedAt && <span className="block text-[11.5px] text-dim">{ago(r.updatedAt)}</span>}
                  </Cell>
                  <Cell>
                    {mayWrite && (
                      <span className="flex items-center gap-1">
                        <Button size="xs" variant="ghost" aria-label={`Change rule ${r.id}`} onClick={() => edit(r)}>
                          <Pencil className="size-3" />
                        </Button>
                        <Button
                          size="xs"
                          variant={armed === r.id ? 'destructive' : 'ghost'}
                          aria-label={`Remove rule ${r.id}`}
                          onClick={() => void remove(r)}
                        >
                          <Trash2 className="size-3" />
                          {armed === r.id && 'Remove?'}
                        </Button>
                      </span>
                    )}
                  </Cell>
                </Row>
              );
            })}
          </DataTable>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <TryRule projects={projects} scopes={scopes} />
        <Panel eyebrow="The same order every time" title="How a rule is chosen">
          <ol className="list-decimal space-y-1.5 pl-4 text-[12.5px] text-ink-2">
            <li>A project's own rule beats the workspace's, whatever either says.</li>
            <li>Then the longer pattern, counted without its wildcards: src/api/* beats src/*.</li>
            <li>Then deny beats ask beats allow, when two rules are equally specific.</li>
            <li>No rule matches: it asks — except for custom tools and repository hooks, where no rule is a no.</li>
          </ol>
          <p className="mt-2 text-[12.5px] text-soft">
            Those two are the whole of the permission rather than a narrowing of it: a custom tool and a
            hook in somebody's settings file do nothing at all until a rule allows them, so silence about
            one is a refusal and nobody is asked.
          </p>
          <SectionTitle className="mt-3">Consulted today</SectionTitle>
          <div className="space-y-1">
            {RULE_TOOLS.map((t) => (
              <p key={t.id} className="text-[12.5px] text-ink-2">
                <span className="font-medium text-ink">{t.label}:</span>{' '}
                {t.consultedBy ?? <span className="text-dim">nothing yet — the rule is kept for when runs read it</span>}
                {REFUSED_UNLESS_ALLOWED.includes(t.id) && <span className="text-warn"> · no rule means no</span>}
              </p>
            ))}
          </div>
        </Panel>
      </div>
    </>
  );
}

function TryRule({ projects, scopes }: { projects: Project[]; scopes: { value: string; label: string }[] }) {
  const [tool, setTool] = useState<RuleTool>('web_fetch');
  const [subject, setSubject] = useState('');
  const [scope, setScope] = useState(projects[0]?.id ?? 'workspace');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<RuleTrial | null>(null);
  const t = toolOf(tool);

  const ask = async () => {
    setBusy(true);
    try {
      setAnswer(await work.tryToolRule(tool, subject.trim(), scope === 'workspace' ? null : scope));
    } catch (e) {
      setAnswer(null);
      toast.error('Not tried', { description: why(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      eyebrow="Nothing runs"
      title={
        <span className="flex items-center gap-1.5">
          <FlaskConical className="size-3.5 text-brand" />
          Try it
        </span>
      }
    >
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <SelectField
          label="Tool"
          value={tool}
          onChange={(v) => {
            setTool(v as RuleTool);
            setAnswer(null);
          }}
          options={RULE_TOOLS.map((x) => ({ value: x.id, label: x.label }))}
        />
        <SelectField label="In" value={scope} onChange={setScope} options={scopes} />
      </div>
      <div className="mt-2 flex items-end gap-2">
        <Field
          className="min-w-0 flex-1"
          label={`The ${t.subject}`}
          value={subject}
          onChange={setSubject}
          placeholder={t.example.replace(/\*/g, '') || t.example}
          mono
        />
        <Button size="sm" disabled={!subject.trim() || busy} onClick={() => void ask()}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
          Try
        </Button>
      </div>
      {answer && (
        <div className="mt-3 rounded-sm border border-line bg-base px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <Tag tone={ANSWER_TONE[answer.action]}>{answer.action}</Tag>
            <Mono className="min-w-0 truncate">{answer.subject}</Mono>
          </div>
          <p className="mt-1.5 text-[12.5px] text-ink-2">{answer.why}</p>
          {!toolOf(answer.tool).consultedBy && (
            <p className="mt-1 text-[12px] text-dim">Nothing consults {toolOf(answer.tool).label.toLowerCase()} rules yet.</p>
          )}
        </div>
      )}
    </Panel>
  );
}
