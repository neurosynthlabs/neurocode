import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowUpRight, Bot, FileText, FlaskConical, Loader2, MessageSquarePlus, Pencil, Plus, ShieldCheck, Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ICONS } from '@/lib/icons';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, ListRow, Stat, StatGrid, About,
  KV, MeterRow, SectionTitle, Empty, Segmented, Field, SelectField,
} from '@/components/os';
import { ApiError, type LaneId } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import {
  agentsApi, fetchRoster, SOURCE_LABEL, type AgentInput, type CustomAgent, type CustomAgentCatalogue, type DryRun,
  type LiveAgent, type LiveAgentStatus,
} from '@/lib/live/agents';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

function Icon({ name, className }: { name: string; className?: string }) {
  const C = ICONS[name] ?? Bot;
  return <C className={className} />;
}

/** The roster the runtime really has: derived from the runs, the router and the ledger. */
export default function Agents() {
  return <LiveAgents />;
}

/* ── Live: derived from the runs, the router and the ledger ───── */

/** What a plan becomes, in the order services/runs.py does it. Static, and true. */
const CHAIN = [
  { what: 'A plan is split by the agent that owns each step', note: 'A run per agent, each in its own worktree and branch.' },
  { what: 'Each agent writes its steps', note: 'On a writing lane. With no lane open the step is skipped.' },
  { what: 'An integration run merges the branches', note: 'Only when several agents worked. Collisions are reported, never half-applied.' },
  { what: 'The project\'s own tests run', note: 'The first time, only once you approve the command. Nothing else runs.' },
  { what: 'The diff is reviewed', note: 'On another lane when one is open; by rules when none answers.' },
  { what: 'You sign', note: 'High risk after a high finding, failed tests or a collision. Refusing removes the worktree.' },
  { what: 'You merge', note: 'Into your checkout\'s branch, with runs:merge. An undo command is kept.' },
];

const CALLS_AS = { write: 'writes code', review: 'reviews diffs' } as const;
const finished = (s: string) => s === 'done' || s === 'skipped' || s === 'failed';

function LiveAgents() {
  const { runs: live } = useData();
  const roster = useRemote('agents', fetchRoster);
  const [sel, setSel] = useState<string | null>(null);
  const [view, setView] = useState<'roster' | 'custom' | 'org'>('roster');

  // Status and the current run follow the run stream: when any run or step moves, ask again. `reload`
  // keeps the last answer on screen, so a busy run does not blank the page.
  const moved = live.map((r) => `${r.ref}:${r.status}:${r.steps.filter((x) => finished(x.status)).length}`).join('|');
  const seen = useRef(moved);
  const { reload } = roster;
  useEffect(() => {
    if (seen.current === moved) return;
    seen.current = moved;
    reload();
  }, [moved, reload]);

  const list = roster.data?.agents ?? [];
  const a = list.find((x) => x.id === sel) ?? list[0];
  const count = (s: LiveAgentStatus) => list.filter((x) => x.status === s).length;

  return (
    <Page>
      <PageHeader
        title="Agents"
        subtitle="Who works on a run, on which lane, and what they did."
        about={<><p>The router gives each agent a lane; lanes rotate so agents working together start apart.</p><p>Each run gets its own worktree, never shared. Only you can approve.</p></>}
        actions={<Segmented options={[{ id: 'roster', label: 'Roster' }, { id: 'custom', label: 'Custom' }, { id: 'org', label: 'Run flow' }]} value={view} onChange={setView} />}
      >
        {roster.data && view !== 'custom' && (
          <div className="flex flex-wrap items-center gap-2 pb-3">
            <Tag tone="ok"><Dot state="running" pulse={count('running') > 0} />{count('running')} running</Tag>
            <Tag tone="neutral">{count('idle')} idle</Tag>
            <Tag tone="warn">{count('waiting')} waiting</Tag>
            <span className="ml-2 text-[12.5px] text-dim">
              {roster.data.lanesOpen} {roster.data.lanesOpen === 1 ? 'lane' : 'lanes'} open now · {roster.data.worktreesOnDisk} {roster.data.worktreesOnDisk === 1 ? 'worktree' : 'worktrees'} on disk
            </span>
          </div>
        )}
      </PageHeader>

      {view === 'custom' ? (
        <CustomAgents />
      ) : roster.error ? (
        <PageBody><Empty title="The roster did not load" hint={roster.error} action={<Button size="sm" variant="outline" onClick={roster.reload}>Try again</Button>} /></PageBody>
      ) : !roster.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the roster…" /></PageBody>
      ) : view === 'org' ? (
        <PageBody>
          <div className="space-y-4">
            <Panel title="Every change ends at your signature" flush>
              <ol className="divide-y divide-line/60">
                {CHAIN.map((c, i) => (
                  <li key={c.what} className="flex items-start gap-3 px-5 py-3">
                    <span className="tnum mt-px grid size-5 shrink-0 place-items-center rounded-full bg-surface-3 text-[11.5px] text-soft">{i + 1}</span>
                    <span className="min-w-0">
                      <span className="block text-[13.5px] font-medium text-ink">{c.what}</span>
                      <span className="block text-[13px] text-soft">{c.note}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </Panel>
            <Enforced rules={roster.data.enforced} />
          </div>
        </PageBody>
      ) : !a ? (
        <PageBody><Empty title="The agent roster is missing" hint="Restart the API to install it again." /></PageBody>
      ) : (
        <PageBody className="flex h-full flex-col p-0">
          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
              {list.map((x) => {
                const lead = x.outcomes.find((o) => o.rate !== null);
                return (
                  <ListRow key={x.id} active={x.id === a.id} onClick={() => setSel(x.id)}>
                    <div className="flex items-center gap-2.5">
                      <span className={cn('grid size-6 shrink-0 place-items-center rounded-sm',
                        x.status === 'running' ? 'bg-brand/15 text-brand' : 'bg-surface-3 text-soft')}>
                        <Icon name={x.icon} className="size-3.5" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-[13.5px] font-medium text-ink">{x.name}</span>
                          <Dot state={x.status} pulse={x.status === 'running'} />
                        </span>
                        <span className="block truncate text-[11.5px] text-dim">{x.role}</span>
                      </span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      {x.lanes.primary ? <Mono>{x.lanes.primary.model}</Mono>
                        : <span className="text-[11.5px] text-dim">{x.callsAs ? 'no lane open' : 'no model call'}</span>}
                      {lead && <span className="ml-auto tnum text-[11.5px] text-dim">{lead.rate}%</span>}
                    </div>
                  </ListRow>
                );
              })}
            </div>
            <div className="min-w-0 flex-1 overflow-y-auto p-5">
              <AgentDetail a={a} enforced={roster.data.enforced} />
            </div>
          </div>
        </PageBody>
      )}
    </Page>
  );
}

function AgentDetail({ a, enforced }: { a: LiveAgent; enforced: string[] }) {
  const { lanes, current } = a;
  const ask = useAsk();
  return (
    <>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand/12 text-brand">
            <Icon name={a.icon} className="size-4.5" />
          </span>
          <div>
            <h2 className="text-[17px] font-semibold text-ink">{a.name}</h2>
            <p className="text-[13.5px] text-soft">{a.role}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {ask.can && a.declared.systemPrompt && (
            <Button size="xs" variant="outline" disabled={ask.busy} onClick={() => void ask.start(a.id, a.name)}
              title={`A session on ${ask.projectName}, with this prompt`}>
              {ask.busy ? <Loader2 className="size-3 animate-spin" /> : <MessageSquarePlus className="size-3" />}Ask {a.name}
            </Button>
          )}
          {a.callsAs && <Tag tone="brand">{CALLS_AS[a.callsAs]}</Tag>}
          <Tag tone={a.status === 'running' ? 'ok' : a.status === 'waiting' ? 'warn' : 'neutral'}><Dot state={a.status} pulse={a.status === 'running'} />{a.status}</Tag>
        </div>
      </div>

      <StatGrid cols={a.outcomes.length + 3} className="mb-4">
        <Stat label="Tasks done" value={a.tasksDone} />
        {a.outcomes.map((o) => (
          <Stat
            key={o.kind} label={o.label[0].toUpperCase() + o.label.slice(1)}
            value={o.rate === null ? '—' : `${o.rate}%`}
            tone={o.rate === null ? undefined : o.rate >= 90 ? 'ok' : 'warn'}
            sub={o.decided ? `${o.good} of ${o.decided}${o.avgMinutes === null ? '' : ` · ${o.avgMinutes}m each`}` : 'no step finished yet'}
          />
        ))}
        <Stat label="Tokens 24h" value={a.tokens24h.toLocaleString()} />
        <Stat label="Cost 24h" value={a.cost24h === null ? 'unpriced' : a.cost24h === 0 ? 'free' : `$${a.cost24h.toFixed(2)}`} tone={a.cost24h === 0 ? 'ok' : a.cost24h === null ? undefined : 'brand'} sub={a.cost24h === null ? 'some lanes have no price' : undefined} />
      </StatGrid>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {current ? (
          <Panel
            eyebrow={current.status === 'waiting' ? 'Waiting for you' : 'Right now'}
            title={`${current.taskRef ?? current.runRef}${current.step ? ` · ${current.step}` : ''}`}
            actions={<Link to={`/runs?ref=${encodeURIComponent(current.runRef)}`} className="inline-flex items-center gap-1 text-[12.5px] text-brand hover:underline">Open {current.runRef}<ArrowUpRight className="size-3.5" /></Link>}
            className="lg:col-span-2"
          >
            <MeterRow label={`Branch ${current.branch}`} pct={current.progress} hint={current.worktree} />
            <div className="mt-2 grid grid-cols-1 gap-x-6 sm:grid-cols-2">
              <KV k="Started" v={ago(current.startedAt)} />
              <KV k="Files changed" v={current.filesChanged} />
              <KV k="Tokens in, this run" v={current.tokensIn.toLocaleString()} />
              <KV k="Tokens out, this run" v={current.tokensOut.toLocaleString()} />
            </div>
          </Panel>
        ) : (
          <Panel className="lg:col-span-2"><Empty title={`${a.name} is ${a.status}`} hint="It works when a plan gives it a step." /></Panel>
        )}

        <Panel title="Lanes" about={a.callsAs ? 'A snapshot: lanes rotate so agents working together start apart. Last answered comes from the ledger.' : undefined}>
          {a.callsAs ? (
            <>
              <KV k="Would try now" v={lanes.primary ? <Mono tone="brand">{lanes.primary.lane} · {lanes.primary.model}</Mono> : <span className="text-warn">no lane open</span>} />
              <KV k="Then" v={lanes.fallback ? <Mono>{lanes.fallback.lane} · {lanes.fallback.model}</Mono> : <span className="text-dim">no second lane open</span>} />
            </>
          ) : (
            <p className="py-2 text-[13px] text-soft">{a.noCall ?? 'The runtime makes no model call as this agent.'}</p>
          )}
          <KV k="Last answered" v={lanes.lastAnswered ? <span className="flex items-center gap-2"><Mono>{lanes.lastAnswered.lane} · {lanes.lastAnswered.model}</Mono><span className="text-[12px] text-dim">{ago(lanes.lastAnswered.at)}</span></span> : 'never'} />
        </Panel>

        <Enforced rules={enforced} />

        <Panel title={a.prompt ? `What every ${a.callsAs === 'review' ? 'review' : 'edit'} step is told` : 'No prompt'} className="lg:col-span-2">
          {a.prompt ? (
            <div className="space-y-3">
              <div>
                <SectionTitle className="mb-1.5">System</SectionTitle>
                <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">{a.prompt.system}</pre>
              </div>
              <div>
                <SectionTitle className="mb-1.5">Per step</SectionTitle>
                <pre className="ascii rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">{a.prompt.user}</pre>
              </div>
            </div>
          ) : (
            <p className="text-[13px] text-soft">{a.noCall ?? 'Nothing is sent.'}</p>
          )}
        </Panel>

        <Panel title="As declared" about="Runs do not read these. A session asked through this agent is given its prompt." className="lg:col-span-2">
          <KV k="Autonomy" wrap v={<span className="text-soft">{a.declared.autonomy} <span className="text-dim">· gated like every agent</span></span>} />
          {a.declared.tools.length > 0 && (
            <>
              <SectionTitle className="mt-3 mb-1.5">Tools</SectionTitle>
              <div className="flex flex-wrap gap-1">{a.declared.tools.map((t) => <Mono key={t}>{t}</Mono>)}</div>
            </>
          )}
          {a.declared.skills.length > 0 && (
            <>
              <SectionTitle className="mt-3 mb-1.5">Skills</SectionTitle>
              <div className="flex flex-wrap gap-1">{a.declared.skills.map((s) => <Mono key={s}>{s}</Mono>)}</div>
            </>
          )}
          {a.declared.guardrails.length > 0 && (
            <>
              <SectionTitle className="mt-3 mb-1.5">Guardrails</SectionTitle>
              <ul className="space-y-1">{a.declared.guardrails.map((g) => <li key={g} className="text-[13px] text-soft">{g}</li>)}</ul>
            </>
          )}
          {a.declared.systemPrompt && (
            <>
              <SectionTitle className="mt-3 mb-1.5">System prompt</SectionTitle>
              <p className="text-[13px] whitespace-pre-wrap text-soft">{a.declared.systemPrompt}</p>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}

function Enforced({ rules }: { rules: string[] }) {
  return (
    <Panel title="Enforced for every agent" flush>
      <div className="divide-y divide-line/60">
        {rules.map((g) => (
          <div key={g} className="flex items-start gap-2 px-5 py-2.5">
            <ShieldCheck className="mt-px size-3.5 shrink-0 text-ok" />
            <span className="text-[13px] text-ink-2">{g}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ── Custom: the workspace's and a project's own agents, and those its repository declares ─────── */

/** "Ask <agent>": a session on the active project answered by an agent, opened on Sessions. */
function useAsk() {
  const { project } = useProject();
  const { can } = useAuth();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const start = async (key: string, name: string) => {
    if (!project) return;
    setBusy(true);
    try {
      const made = await agentsApi.ask(project.id, key);
      toast.success(`${made.ref} started`, { description: `${name} answers in it.` });
      nav(`/sessions?ref=${encodeURIComponent(made.ref)}`);
    } catch (e) {
      toast.error('Session not started', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };
  return { can: can('sessions:chat') && project !== null, busy, start, projectName: project?.name ?? '' };
}

const MODE_LABEL = { primary: 'talks in sessions', subagent: 'takes plan steps' } as const;
const GROUPS: { title: string; has: (a: CustomAgent) => boolean }[] = [
  { title: 'This project', has: (a) => a.source === 'project' },
  { title: 'In the repository', has: (a) => a.source === '.neurocode/agents' || a.source === '.claude/agents' },
  { title: 'Workspace', has: (a) => a.source === 'workspace' },
];

function CustomAgents() {
  const { project } = useProject();
  const { can } = useAuth();
  const cat = useRemote(`custom-agents:${project?.id ?? ''}`, () => agentsApi.list(project?.id ?? null));
  const [sel, setSel] = useState<string | null>(null);
  const [editing, setEditing] = useState<CustomAgent | 'new' | null>(null);
  const mayManage = can('agents:manage');
  const list = cat.data?.agents ?? [];
  const a = list.find((x) => x.key === sel) ?? list.find((x) => !x.shadowedBy) ?? list[0] ?? null;

  if (cat.error) {
    return <PageBody><Empty title="The custom agents did not load" hint={cat.error} action={<Button size="sm" variant="outline" onClick={cat.reload}>Try again</Button>} /></PageBody>;
  }
  if (!cat.data) return <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the agents…" /></PageBody>;
  const data = cat.data;
  const editor = editing && (
    <AgentEditor initial={editing === 'new' ? null : editing} cat={data} projectId={project?.id ?? null} projectName={project?.name ?? null}
      onClose={() => setEditing(null)}
      onSaved={(saved) => { setEditing(null); setSel(saved.key); cat.reload(); }} />
  );
  const unread = data.checkout === false
    ? `Agent files not read: ${project?.name ?? 'this project'}'s code is not on this machine.`
    : null;

  if (list.length === 0) {
    return (
      <PageBody>
        <Empty icon={<Bot className="size-6" />} title="No custom agents yet"
          hint={`Write one, or add a file to .neurocode/agents or .claude/agents.${unread ? ` ${unread}` : ''}`}
          action={mayManage ? <Button size="sm" onClick={() => setEditing('new')}><Plus className="size-3.5" />New agent</Button> : undefined} />
        {editor}
      </PageBody>
    );
  }

  return (
    <PageBody className="flex h-full flex-col p-0">
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
          <div className="flex items-center gap-2 border-b border-line/60 px-4 py-2.5">
            <span className="flex-1 text-[12px] text-dim">{list.length} {list.length === 1 ? 'agent' : 'agents'}{project ? ` · ${project.name} and the workspace` : ' · the workspace'}</span>
            {mayManage && <Button size="xs" onClick={() => setEditing('new')}><Plus className="size-3" />New agent</Button>}
          </div>
          {GROUPS.map((g) => {
            const mine = list.filter(g.has);
            if (mine.length === 0) return null;
            return (
              <div key={g.title}>
                <p className="px-4 pt-3 pb-1 text-[12px] font-medium text-dim">{g.title}</p>
                {mine.map((x) => (
                  <ListRow key={x.key} active={a?.key === x.key} onClick={() => setSel(x.key)}>
                    <div className="flex items-center gap-2">
                      <span className={cn('truncate text-[13.5px] font-medium', x.shadowedBy ? 'text-dim line-through' : 'text-ink')}>{x.name}</span>
                      <Tag tone={x.editable ? 'neutral' : 'violet'} className="ml-auto shrink-0">{SOURCE_LABEL[x.source]}</Tag>
                    </div>
                    <p className="mt-1 truncate text-[11.5px] text-dim">
                      {x.shadowedBy ? `shadowed by ${x.shadowedBy === 'built-in' ? 'a built-in agent' : x.shadowedBy.replace(/^(custom|file):/, '')}` : x.role || MODE_LABEL[x.mode]}
                    </p>
                  </ListRow>
                ))}
              </div>
            );
          })}
          {(unread || data.unreadable.length > 0) && (
            <div className="space-y-1 border-t border-line/60 px-4 py-3 text-[12px] text-dim">
              {unread && <p>{unread}</p>}
              {data.unreadable.length > 0 && <p>Left out, front matter unreadable: {data.unreadable.join(', ')}</p>}
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1 overflow-y-auto p-5">
          {a && <CustomDetail key={a.key} a={a} cat={data} mayManage={mayManage} projectId={project?.id ?? null}
            onEdit={() => setEditing(a)} onRemoved={() => { setSel(null); cat.reload(); }} />}
        </div>
      </div>
      {editor}
    </PageBody>
  );
}

function CustomDetail({ a, cat, mayManage, projectId, onEdit, onRemoved }: {
  a: CustomAgent; cat: CustomAgentCatalogue; mayManage: boolean; projectId: string | null;
  onEdit: () => void; onRemoved: () => void;
}) {
  const ask = useAsk();
  const [confirm, setConfirm] = useState(false);
  const [removing, setRemoving] = useState(false);
  const lane = cat.lanes.find((l) => l.id === a.lane);
  const writes = a.tools.length === 0 || a.tools.includes('edit');
  const tools = a.tools.length ? cat.tools.filter((t) => a.tools.includes(t.name)) : [];
  const remove = async () => {
    if (!a.id) return;
    setRemoving(true);
    try {
      await agentsApi.remove(a.id);
      toast(`${a.name} removed`, { description: 'Past runs and sessions keep its name.' });
      onRemoved();
    } catch (e) {
      toast.error('Not removed', { description: reason(e) });
      setRemoving(false);
    }
  };

  return (
    <>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand/12 text-brand">
            {a.editable ? <Bot className="size-4.5" /> : <FileText className="size-4.5" />}
          </span>
          <div className="min-w-0">
            <h2 className="text-[17px] font-semibold text-ink">{a.name}</h2>
            <p className="text-[13.5px] text-soft">{a.role || 'No role written.'}</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <Tag tone={a.editable ? 'neutral' : 'violet'}>{SOURCE_LABEL[a.source]}</Tag>
              <Tag tone="brand">{MODE_LABEL[a.mode]}</Tag>
              {a.shadowedBy && <Tag tone="warn">shadowed</Tag>}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {ask.can && !a.shadowedBy && (
            <Button size="xs" variant="outline" disabled={ask.busy} onClick={() => void ask.start(a.key, a.name)} title={`A session on ${ask.projectName}`}>
              {ask.busy ? <Loader2 className="size-3 animate-spin" /> : <MessageSquarePlus className="size-3" />}Ask {a.name}
            </Button>
          )}
          {a.editable && mayManage && (
            <>
              <Button size="xs" variant="outline" onClick={onEdit}><Pencil className="size-3" />Edit</Button>
              {confirm ? (
                <>
                  <Button size="xs" variant="destructive" disabled={removing} onClick={() => void remove()}>
                    {removing ? <Loader2 className="size-3 animate-spin" /> : <Trash2 className="size-3" />}Remove
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => setConfirm(false)}>Keep</Button>
                </>
              ) : (
                <Button size="xs" variant="ghost" aria-label={`Remove ${a.name}`} onClick={() => setConfirm(true)}><Trash2 className="size-3" /></Button>
              )}
            </>
          )}
        </div>
      </div>

      {a.shadowedBy && (
        <p className="mb-4 rounded-md border border-warn/30 bg-warn/8 px-3 py-2 text-[13px] text-ink-2">
          {a.shadowedBy === 'built-in'
            ? `A built-in agent has this name and wins. Rename the file to use it.`
            : `A nearer ${a.name} (${a.shadowedBy.replace(/^(custom|file):/, '')}) wins. Project beats repository beats workspace.`}
        </p>
      )}

      <StatGrid cols={4} className="mb-4">
        <Stat label="Sessions asked" value={a.usage.sessions} sub={a.usage.lastSession ? `last ${ago(a.usage.lastSession)}` : 'none yet'} />
        <Stat label="Plan steps done" value={a.usage.steps ? `${a.usage.stepsDone} of ${a.usage.steps}` : '0'} />
        <Stat label="Lane it prefers" value={lane ? lane.label : 'router'} sub={lane ? lane.model : 'the router chooses'} />
        <Stat label="Tool calls per answer" value={a.maxSteps} />
      </StatGrid>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Panel eyebrow={`~${a.tokens.toLocaleString()} tokens${a.truncated ? ' · cut to fit' : ''}`} title="Instructions" className="lg:col-span-2"
          about="Given first, before the runtime's own rules, which hold whatever it says.">
          <pre className="ascii max-h-[320px] overflow-y-auto rounded-sm border border-line bg-base p-3 whitespace-pre-wrap">{a.prompt || 'No instructions: the file has no body.'}</pre>
        </Panel>

        <Panel eyebrow="Capped by tool rules" title="Tools">
          {a.tools.length === 0 ? (
            <p className="text-[13px] text-soft">Every tool. Web and MCP tools ask first unless a rule allows them.</p>
          ) : (
            <ul className="space-y-1.5">
              {tools.map((t) => (
                <li key={t.name} className="flex items-baseline gap-2 text-[13px]">
                  <Mono>{t.name}</Mono><span className="text-soft">{t.what}</span>
                </li>
              ))}
            </ul>
          )}
          {a.ignored.length > 0 && (
            <p className="mt-2.5 text-[12.5px] text-dim">
              Unknown, never offered: {a.ignored.map((t) => <Mono key={t} className="mr-1 line-through">{t}</Mono>)}
            </p>
          )}
          <p className={cn('mt-2.5 text-[12.5px]', writes ? 'text-dim' : 'text-warn')}>
            {writes ? 'Writes files in its run’s worktree, under the edit rules.' : 'Cannot write files: a plan step given to it fails.'}
          </p>
        </Panel>

        <Panel title={a.path ?? (a.source === 'project' ? 'Written for this project' : 'Written for the workspace')}>
          <KV k="Source" v={SOURCE_LABEL[a.source]} />
          {a.model && <KV k="Model, as written" v={<Mono>{a.model}</Mono>} />}
          {a.createdBy && <KV k="Written by" v={a.createdBy} />}
          {a.updatedAt && <KV k="Last changed" v={ago(a.updatedAt)} />}
          {!a.editable && <p className="mt-2 text-[12.5px] text-dim">Read from the repository. Edit the file to change it.</p>}
          {a.notes.map((n) => <p key={n} className="mt-1.5 text-[12.5px] text-warn">{n}</p>)}
        </Panel>

        <TryPanel a={a} projectId={projectId} />
      </div>
    </>
  );
}

function TryPanel({ a, projectId }: { a: CustomAgent; projectId: string | null }) {
  const { can } = useAuth();
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<DryRun | null>(null);
  const may = can('sessions:chat');
  const tryIt = async () => {
    setBusy(true);
    try {
      setAnswer(await agentsApi.tryOut(a.key, question.trim(), projectId));
    } catch (e) {
      toast.error('No answer', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel eyebrow="No tools, nothing written" title={`Try ${a.name}`} className="lg:col-span-2">
      <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (question.trim() && may) void tryIt(); }}>
        <textarea rows={2} maxLength={4000} value={question} onChange={(e) => setQuestion(e.target.value)} disabled={!may || busy}
          aria-label={`A question for ${a.name}`} placeholder={may ? 'Ask it something' : 'Needs the sessions:chat permission'}
          className="w-full resize-none rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[13.5px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none" />
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" type="submit" disabled={!may || busy || !question.trim()}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <FlaskConical className="size-3.5" />}Try it
          </Button>
          <span className="text-[12px] text-dim">One model call on {a.lane ? `the ${a.lane} lane if open` : 'the router’s lane'}, counted in usage.</span>
        </div>
      </form>
      {answer && (
        <div className="mt-3 space-y-1.5 border-t border-line pt-3">
          <p className="text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink">{answer.answer}</p>
          <p className="text-[12px] text-dim">
            {answer.lane} · {answer.model} · {(answer.ms / 1000).toFixed(1)} s · {answer.tokens.in.toLocaleString()} in, {answer.tokens.out.toLocaleString()} out
            {!answer.onPreferred && answer.preferred ? ` · ${answer.preferred} did not answer; another lane did` : ''}
          </p>
        </div>
      )}
    </Panel>
  );
}

const BLANK: AgentInput = { name: '', role: '', prompt: '', lane: null, tools: [], maxSteps: 8, mode: 'subagent', projectId: null };

function AgentEditor({ initial, cat, projectId, projectName, onClose, onSaved }: {
  initial: CustomAgent | null; cat: CustomAgentCatalogue; projectId: string | null; projectName: string | null;
  onClose: () => void; onSaved: (saved: CustomAgent) => void;
}) {
  const [form, setForm] = useState<AgentInput>(() => initial
    ? { name: initial.name, role: initial.role, prompt: initial.prompt, lane: initial.lane, tools: initial.tools, maxSteps: initial.maxSteps, mode: initial.mode, projectId: initial.projectId }
    : { ...BLANK, projectId });
  const [busy, setBusy] = useState(false);
  const put = (patch: Partial<AgentInput>) => setForm((f) => ({ ...f, ...patch }));
  const every = form.tools.length === 0;
  const toggle = (name: string, on: boolean) => put({
    tools: (every ? cat.tools.map((t) => t.name) : form.tools).filter((t) => t !== name).concat(on ? [name] : []),
  });
  const steps = Number.isFinite(form.maxSteps) && form.maxSteps >= 1 && form.maxSteps <= cat.limits.maxSteps;
  const valid = form.name.trim() && form.prompt.trim() && steps && form.prompt.length <= cat.limits.prompt;

  const save = async () => {
    setBusy(true);
    const body = { ...form, name: form.name.trim(), role: form.role.trim(), prompt: form.prompt.trim() };
    try {
      const saved = initial?.id ? await agentsApi.update(initial.id, body) : await agentsApi.create(body);
      toast.success(initial ? `${saved.name} saved` : `${saved.name} created`, { description: 'Used from the next step or answer.' });
      onSaved(saved);
    } catch (e) {
      toast.error('Not saved', { description: reason(e) });
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <DialogContent className="sm:max-w-[680px]">
        <DialogHeader>
          <DialogTitle>{initial ? `Edit ${initial.name}` : 'New agent'}</DialogTitle>
          <DialogDescription className="flex items-center gap-1">
            Its instructions, lane and tools.
            <About>Never more than the tool rules allow. Every run it works on still stops at your signature.</About>
          </DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[62vh] gap-3 overflow-y-auto pr-1">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" value={form.name} onChange={(v) => put({ name: v })} placeholder="Ledger Auditor" autoFocus />
            {initial ? (
              <Field label="Scope" value={initial.projectId ? `Only ${projectName ?? initial.projectId}` : 'The whole workspace'} onChange={() => undefined} disabled />
            ) : (
              <SelectField label="Scope" value={form.projectId ?? ''} onChange={(v) => put({ projectId: v || null })}
                options={[{ value: '', label: 'The whole workspace' }, ...(projectId ? [{ value: projectId, label: `Only ${projectName ?? projectId}` }] : [])]} />
            )}
          </div>
          <Field label="Role" value={form.role} onChange={(v) => put({ role: v })} placeholder="Reads money code for rounding and tax mistakes"
            hint="One line; the compiler uses it to assign steps." />
          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-medium text-soft">Instructions</span>
            <textarea rows={7} value={form.prompt} onChange={(e) => put({ prompt: e.target.value })} maxLength={cat.limits.prompt}
              placeholder="What it does, and must never do"
              className="w-full rounded-lg border border-line bg-surface-2/60 px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none" />
            <span className="mt-1 block text-[12px] text-dim">{form.prompt.length.toLocaleString()} of {cat.limits.prompt.toLocaleString()} characters</span>
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <SelectField label="Lane it prefers" value={form.lane ?? ''} onChange={(v) => put({ lane: (v || null) as LaneId | null })}
              options={[{ value: '', label: 'The router chooses' }, ...cat.lanes.map((l) => ({ value: l.id, label: `${l.label} · ${l.model}` }))]} />
            <SelectField label="Works as" value={form.mode} onChange={(v) => put({ mode: v as AgentInput['mode'] })}
              options={[{ value: 'subagent', label: 'Subagent · takes plan steps' }, { value: 'primary', label: 'Primary · talks in sessions' }]} />
            <Field label="Tool calls per answer" type="number" value={String(form.maxSteps)} onChange={(v) => put({ maxSteps: Number(v) })}
              hint={steps ? undefined : `1 to ${cat.limits.maxSteps}`} />
          </div>
          <div>
            <SectionTitle right={!every && <Button size="xs" variant="ghost" onClick={() => put({ tools: [] })}>Allow every tool</Button>}>Tools</SectionTitle>
            <p className="mb-2 text-[12px] text-dim">{every ? 'Every tool. Untick one to narrow.' : `${form.tools.length} of ${cat.tools.length}`}</p>
            <div className="grid gap-1 sm:grid-cols-2">
              {cat.tools.map((t) => (
                <label key={t.name} className="flex items-start gap-2 rounded-md px-1.5 py-1 hover:bg-surface-2/60">
                  <input type="checkbox" className="mt-0.5 size-4 shrink-0 accent-[var(--os-brand)]" checked={every || form.tools.includes(t.name)}
                    onChange={(e) => toggle(t.name, e.target.checked)} />
                  <span className="min-w-0">
                    <span className="block font-mono text-[12.5px] text-ink">{t.name}{t.where === 'run' ? ' · runs' : ''}</span>
                    <span className="block text-[12px] text-dim">{t.what}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => void save()} disabled={busy || !valid}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}{initial ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
