import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Workflow, Play, Search, Layers, Plus, Pencil, Archive, Square, Loader2, FolderGit2, Coins, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, ListRow, Field, Segmented, SelectField,
  DataTable, Row, Cell, Stat, StatGrid, Ascii, KV, Empty, SectionTitle, More,
} from '@/components/os';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import {
  workflowsApi, type LiveState, type LiveWorkflow, type LiveWorkflowDetail, type LiveWorkflowPhase,
  type WorkflowInput, type WorkflowLiveRun,
} from '@/lib/live/workflows';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';
import { ago, tokens as fmtTokens } from './code/format';

/** Workflows a person wrote, run as ordinary plans. */
export default function Workflows() {
  return <LiveWorkflows />;
}

const RES_TONE = { success: 'ok', failed: 'danger', partial: 'warn' } as const;

/* ── Live: workflows a person wrote, run as ordinary plans ─────────────── */

const LIVE_MODE_TONE = { parallel: 'brand', pipeline: 'violet', single: 'neutral' } as const;
const LIVE_DOT: Record<LiveState, string> = { todo: 'todo', active: 'running', waiting: 'waiting', done: 'done', failed: 'failed', skipped: 'todo' };
const NO_WRITE = 'Writing a workflow needs the workflows:write permission.';

/** What the runtime guarantees, from docs/ARCHITECTURE.md. These are true of every run, so they are stated, not measured. */
const GUARANTEES = [
  { name: 'Nothing touches your working tree', note: 'Every agent writes in its own git worktree and branch.' },
  { name: 'Nothing a model says is executed', note: 'A model only proposes file contents. Absolute paths, .. and .git are refused.' },
  { name: 'One command, behind your approval', note: 'Only the project’s own test command runs. The first time, the run waits for you.' },
  { name: 'Collisions are caught at the merge', note: 'Branches come in one by one; a collision is named and undone, never half-applied.' },
  { name: 'The review prefers another lane', note: 'Another model reads the diff when one is free; rules when none answers.' },
];

const minutes = (s: number | null) => (s === null ? '—' : `${Math.floor(s / 60)}m ${s % 60}s`);
const elapsed = (ms: number | null) => (ms === null ? '—' : ms < 60_000 ? `${Math.round(ms / 1000)}s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`);
const money = (c: number | null) => (c === null ? '—' : c === 0 ? 'free' : `$${c.toFixed(2)}`);
const reason = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** The phase graph, drawn from derived phases. A Write phase whose agent count is the compiler's call draws as "n". */
function liveShape(phases: LiveWorkflowPhase[]) {
  const lines: string[] = [];
  phases.forEach((p, i) => {
    const count = p.agents === null ? 'n' : String(p.agents);
    const tag = `${String(i + 1).padStart(2)} ${p.title.toUpperCase()}  ·  ${p.mode}${p.agents === 0 ? ' · code' : ` × ${count}`}`;
    if (p.mode === 'parallel') {
      const n = p.agents === null ? 3 : Math.max(p.agents, 1);
      const cells = Array.from({ length: n }, (_, k) => (p.agents === null && k === n - 1 ? '│ …  ' : '│ ●  ')).join('');
      lines.push(`   ┌${'─'.repeat(n * 6 - 1)}┐  ${tag}`);
      lines.push(`   ${cells}│`);
      lines.push(`   └${'─'.repeat(Math.floor((n * 6 - 1) / 2))}┬${'─'.repeat(Math.ceil((n * 6 - 1) / 2) - 1)}┘`);
    } else if (p.mode === 'pipeline') {
      lines.push(`   ● ─▶ ● ─▶ …   ${tag}`);
      lines.push('       │');
    } else {
      lines.push(`   ●             ${tag}`);
      lines.push('   │');
    }
  });
  lines.push('   ▼');
  lines.push('   DONE');
  return lines.join('\n');
}

function LiveWorkflows() {
  const nav = useNavigate();
  const { can } = useAuth();
  const { runs, cancelRun } = useData();
  const { project, all } = useProject();
  const withCode = useMemo(() => all.filter((p) => p.source), [all]);
  const pid = project?.id ?? null;

  const overview = useRemote('workflows:overview', () => workflowsApi.overview());
  const library = useRemote(`workflows:list:${pid ?? ''}`, () => workflowsApi.list(pid));

  // A run changing anywhere streams into the store; the counts here are re-read when one does.
  const stamp = useMemo(() => runs.slice(0, 30).map((r) => `${r.ref}:${r.status}`).join(','), [runs]);
  const reloads = useRef(() => {});
  useLayoutEffect(() => { reloads.current = () => { overview.reload(); library.reload(); }; });
  const seen = useRef(stamp);
  useEffect(() => {
    if (seen.current === stamp) return;
    seen.current = stamp;
    reloads.current();
  }, [stamp]);

  const [q, setQ] = useState('');
  const [scope, setScope] = useState<'all' | 'global' | 'project'>('all');
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState<'graph' | 'definition'>('graph');
  const [running, setRunning] = useState(false);
  const [editing, setEditing] = useState<LiveWorkflowDetail | 'new' | null>(null);

  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (library.data ?? []).filter((x) =>
      (scope === 'all' || x.scope === scope) && (!t || (x.name + x.description + x.trigger).toLowerCase().includes(t)));
  }, [library.data, q, scope]);
  const w: LiveWorkflow | null = list.find((x) => x.id === sel) ?? list[0] ?? null;
  const detail = useRemote(w ? `workflows:detail:${w.id}:${pid ?? ''}:${w.runs}:${w.lastRun ?? ''}` : null, () => workflowsApi.detail(w!.id, pid));

  const refresh = () => reloads.current();
  const mayRun = can('plans:compile', 'plans:decide');
  const mayWrite = can('workflows:write');
  const o = overview.data;

  const archive = async (target: LiveWorkflow) => {
    try {
      const done = await workflowsApi.remove(target.id);
      toast.success(done.archived ? `${target.name} archived` : `${target.name} deleted`, {
        description: done.archived ? 'Its runs keep its name in the history.' : 'It had never run, so nothing referred to it.',
      });
      setSel(null);
      refresh();
    } catch (e) {
      toast.error('Not removed', { description: reason(e, 'The local API did not answer.') });
    }
  };

  const stop = async (live: WorkflowLiveRun) => {
    if (await cancelRun(live.ref)) {
      toast(`${live.ref} is stopping`, { description: 'Its agents stop with it. Worktrees are kept.' });
      refresh();
    }
  };

  return (
    <Page>
      <PageHeader
        title="Workflows"
        subtitle="Write the steps once — which agent does what."
        actions={(
          <div className="flex flex-wrap items-center gap-2">
            {mayWrite && <Button size="sm" variant="outline" onClick={() => setEditing('new')}><Plus className="size-3.5" />New workflow</Button>}
            {mayRun && w && (
              <Button size="sm" onClick={() => setRunning(true)} disabled={!withCode.length}
                title={withCode.length ? undefined : 'No project has code on this machine yet'}>
                <Play className="size-3.5" />Run workflow
              </Button>
            )}
          </div>
        )}
      />

      <PageBody className="space-y-4">
        {overview.error || library.error ? (
          <Empty title="Workflows did not load" hint={overview.error ?? library.error ?? undefined}
            action={<Button size="sm" variant="outline" onClick={refresh}>Try again</Button>} />
        ) : !o || !library.data ? (
          <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading workflows…" />
        ) : (
          <>
            {(o.stats.runsTotal > 0 || o.stats.liveNow > 0) && <StatGrid cols={5}>
              <Stat label="Workflows" value={o.stats.workflows} icon={<Workflow className="size-3" />} sub="including the built-in" />
              <Stat label="Runs total" value={o.stats.runsTotal} />
              <Stat label="Live now" value={o.stats.liveNow} tone={o.stats.liveNow ? 'ok' : 'neutral'} sub={o.live ? o.live.workflow : 'nothing running'} />
              <Stat label="Agents in flight" value={o.stats.agentsInFlight} tone={o.stats.agentsInFlight ? 'brand' : 'neutral'} sub={`${o.stats.lanesOpen} lane${o.stats.lanesOpen === 1 ? '' : 's'} open now`} icon={<Layers className="size-3" />} />
              <Stat label="Tokens today" value={fmtTokens(o.stats.tokensToday)} sub={o.stats.tokensToday ? `agents and reviews · ${o.stats.costToday === null ? 'cost unknown: unpriced model' : money(o.stats.costToday)}` : 'no model call yet today'} icon={<Coins className="size-3" />} />
            </StatGrid>}

            {!withCode.length && (
              <Panel>
                <Empty icon={<FolderGit2 className="size-6" />}
                  title={all.length === 0 ? 'No project has been onboarded yet' : 'None of your projects has code on this machine'}
                  hint={all.length === 0
                    ? 'A run branches from a real repository. Onboard one first.'
                    : 'A run branches from a real repository; onboard one with its code.'}
                  action={<Button size="sm" variant="outline" onClick={() => nav('/projects')}>Open Projects</Button>} />
              </Panel>
            )}

            {o.live ? (
              <LivePanel live={o.live} mayStop={can('runs:run')} onStop={() => void stop(o.live!)} onOpen={() => nav('/runs')} />
            ) : (
              <p className="text-[12.5px] text-dim">No workflow is running.</p>
            )}

            <div className="flex min-h-[560px] flex-col gap-3 md:flex-row">
              <div className="flex w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[310px] flex-col overflow-hidden rounded-md border border-line bg-surface">
                <div className="space-y-2 border-b border-line p-2.5">
                  <Field value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Search workflows…" onClear={() => setQ('')} />
                  <Segmented className="w-full" options={[{ id: 'all', label: 'All' }, { id: 'global', label: 'Global' }, { id: 'project', label: 'Project' }]} value={scope} onChange={setScope} />
                </div>
                <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
                  {list.length === 0 ? <Empty title="No workflow matches" /> : list.map((x) => (
                    <ListRow key={x.id} active={x.id === w?.id} onClick={() => setSel(x.id)}>
                      <div className="flex items-center gap-2">
                        <Dot state={x.lastResult === 'success' ? 'ok' : x.lastResult === 'partial' ? 'warn' : x.lastResult === 'failed' ? 'error' : 'todo'} />
                        <Mono tone={x.id === w?.id ? 'brand' : 'neutral'}>{x.name}</Mono>
                      </div>
                      <p className="mt-1 truncate text-[11.5px] text-dim">{x.trigger}</p>
                      <div className="mt-1 flex items-center gap-3 text-[11.5px] text-dim">
                        <span>{x.runs} run{x.runs === 1 ? '' : 's'}</span>
                        {x.avgAgents !== null && <span>~{x.avgAgents} agents</span>}
                        {x.avgMinutes !== null && <span>~{x.avgMinutes}m</span>}
                        <span className="ml-auto">{x.lastRun ? ago(x.lastRun) : 'never run'}</span>
                      </div>
                    </ListRow>
                  ))}
                </div>
              </div>

              <div className="min-w-0 flex-1 space-y-3">
                {!w ? <Panel><Empty title="No workflow selected" /></Panel> : (
                  <>
                    <Panel eyebrow={`${w.scope}${w.projectName ? ` · ${w.projectName}` : ''} · ${w.trigger}`}
                      title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{w.name}</Mono>{w.lastResult ? <Tag tone={RES_TONE[w.lastResult]}>last: {w.lastResult}</Tag> : <Tag>never finished a run</Tag>}</span>}
                      actions={(
                        <div className="flex flex-wrap items-center gap-2">
                          {mayWrite && !w.builtin && detail.data && (
                            <>
                              <Button size="xs" variant="ghost" onClick={() => setEditing(detail.data)}><Pencil className="size-3" />Edit</Button>
                              <Button size="xs" variant="ghost" onClick={() => void archive(w)}>{w.referenced ? <Archive className="size-3" /> : <Trash2 className="size-3" />}{w.referenced ? 'Archive' : 'Delete'}</Button>
                            </>
                          )}
                          <Segmented options={[{ id: 'graph', label: 'Phase graph' }, { id: 'definition', label: 'Definition' }]} value={tab} onChange={setTab} />
                        </div>
                      )}>
                      <p className="text-[13.5px] leading-relaxed text-ink-2">{w.description || 'No description.'}</p>
                      {!mayWrite && !w.builtin && <p className="mt-2 text-[12px] text-dim">{NO_WRITE}</p>}
                      <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 border-t border-line pt-2.5 xl:grid-cols-4">
                        <KV k="Runs" v={w.runs} />
                        <KV k="Avg agents" v={w.avgAgents ?? '—'} />
                        <KV k="Avg duration" v={w.avgMinutes === null ? '—' : `${w.avgMinutes}m`} />
                        <KV k="Last run" v={w.lastRun ? ago(w.lastRun) : 'never'} />
                      </div>
                    </Panel>

                    {tab === 'graph' ? (
                      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                        <Panel eyebrow={w.tests.project ? `Drawn for ${w.tests.project}` : 'Pick a project to see its Test phase'} title="Fan-out">
                          <Ascii className="overflow-auto">{liveShape(w.phases)}</Ascii>
                        </Panel>
                        <Panel eyebrow={`${w.phases.length} phases`} title="Phases" flush>
                          <div className="divide-y divide-line">
                            {w.phases.map((p, i) => (
                              <div key={p.id} className="flex items-start gap-2.5 px-3.5 py-2.5">
                                <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{i + 1}</span>
                                <span className="min-w-0 flex-1">
                                  <span className="flex flex-wrap items-center gap-1.5">
                                    <span className="text-[13.5px] font-medium text-ink">{p.title}</span>
                                    <Tag tone={LIVE_MODE_TONE[p.mode]}>{p.mode}</Tag>
                                    <span className="text-[11.5px] text-dim">{p.agents === null ? 'agents set by the plan' : p.agents ? `${p.agents} agent${p.agents > 1 ? 's' : ''}` : 'no agent'}</span>
                                  </span>
                                  <span className="mt-0.5 block text-[12.5px] text-soft">{p.detail}</span>
                                </span>
                              </div>
                            ))}
                          </div>
                        </Panel>
                      </div>
                    ) : (
                      <Panel eyebrow="Read-only, as executed" title={w.name}>
                        {detail.error ? <Empty title="The definition did not load" hint={detail.error} />
                          : !detail.data ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading…" />
                            : <pre className="ascii max-h-[440px] overflow-auto rounded-sm border border-line bg-base p-3.5">{detail.data.definitionText}</pre>}
                      </Panel>
                    )}
                  </>
                )}
              </div>
            </div>

            <div className="space-y-3">
              <Panel eyebrow={o.history.length ? `Last ${o.history.length} ${o.history.length === 1 ? 'run' : 'runs'} that finished` : 'Recent runs'} title="History" flush>
                {o.history.length === 0 ? (
                  <Empty title="No workflow has finished a run yet" hint="Finished runs show here, with duration and tokens." />
                ) : (
                  <DataTable head={['Workflow', 'Trigger', 'Agents', 'Duration', 'Tokens', 'Cost', 'Result', 'When']}>
                    {o.history.map((h) => (
                      <Row key={h.id} onClick={() => nav('/runs')}>
                        <Cell mono className="text-ink">{h.workflow}</Cell>
                        <Cell className="text-[12.5px]">{h.trigger || h.runRef}</Cell>
                        <Cell className="tnum">{h.agents}</Cell>
                        <Cell className="tnum">{minutes(h.durationS)}</Cell>
                        <Cell className="tnum">{h.tokens === null ? '—' : fmtTokens(h.tokens)}</Cell>
                        <Cell className={cn('tnum', h.cost === 0 ? 'text-ok' : 'text-ink')}>{money(h.cost)}</Cell>
                        <Cell><Tag tone={RES_TONE[h.result]}>{h.status === 'cancelled' ? 'stopped' : h.result}</Tag></Cell>
                        <Cell className="text-dim">{ago(h.at)}</Cell>
                      </Row>
                    ))}
                  </DataTable>
                )}
              </Panel>
              <More label="What every run guarantees">
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {GUARANTEES.map((p) => (
                    <Panel key={p.name} title={p.name}>
                      <p className="text-[12.5px] leading-relaxed text-soft">{p.note}</p>
                    </Panel>
                  ))}
                </div>
              </More>
            </div>
          </>
        )}
      </PageBody>

      {running && w && (
        <RunDialog workflow={w} projects={withCode} active={project} onClose={() => setRunning(false)}
          onStarted={() => { setRunning(false); refresh(); }} />
      )}
      {editing && o && (
        <EditorDialog initial={editing === 'new' ? null : editing} writers={o.writers} projects={all}
          onClose={() => setEditing(null)}
          onSaved={(saved) => { setEditing(null); setSel(saved.id); refresh(); }} />
      )}
    </Page>
  );
}

function LivePanel({ live, mayStop, onStop, onOpen }: { live: WorkflowLiveRun; mayStop: boolean; onStop: () => void; onOpen: () => void }) {
  return (
    <Panel className="accent-top" eyebrow={`${live.ref} · started ${ago(live.startedAt)}${live.waitingOn ? ` · waiting on ${live.waitingOn}` : ''}`}
      title={<span className="flex flex-wrap items-center gap-2"><Dot state={live.status === 'waiting' ? 'waiting' : 'running'} pulse={live.status === 'running'} />{live.workflow}<Tag tone="brand">phase · {live.phase}</Tag></span>}
      actions={(
        <span className="flex flex-wrap items-center gap-2 text-[12px] text-dim">
          {live.completed} done · {live.running} running · {live.queued} queued
          <Button size="xs" variant="ghost" onClick={onOpen}>Open in Live Runs</Button>
          {mayStop && <Button size="xs" variant="outline" onClick={onStop}><Square className="size-3" />Stop</Button>}
        </span>
      )} flush>
      <DataTable head={['Agent', 'Phase', 'State', 'Elapsed', 'Tokens']}>
        {live.agents.map((a, i) => (
          <Row key={`${a.runRef}:${a.label}:${i}`} className={cn(a.state === 'active' && 'sweep')}>
            <Cell mono className="text-ink">{a.label}</Cell>
            <Cell><Tag tone="neutral">{a.phase}</Tag></Cell>
            <Cell><span className="flex items-center gap-1.5"><Dot state={LIVE_DOT[a.state]} pulse={a.state === 'active'} />{a.state}</span></Cell>
            <Cell className="tnum">{elapsed(a.ms)}</Cell>
            <Cell className="tnum">{a.tokens === null ? '—' : fmtTokens(a.tokens)}</Cell>
          </Row>
        ))}
      </DataTable>
      <div className="border-t border-line px-3.5 py-2.5 text-[12px] text-dim">
        <span className="text-ink-2">Nothing dropped silently.</span>{' '}
        {live.skipped.length ? live.skipped.join(' · ') : 'Nothing skipped or collided so far.'}
      </div>
    </Panel>
  );
}

function RunDialog({ workflow, projects, active, onClose, onStarted }: {
  workflow: LiveWorkflow; projects: Project[]; active: Project | null; onClose: () => void; onStarted: () => void;
}) {
  const nav = useNavigate();
  const allowed = workflow.projectId ? projects.filter((p) => p.id === workflow.projectId) : projects;
  const [projectId, setProjectId] = useState(allowed.find((p) => p.id === active?.id)?.id ?? allowed[0]?.id ?? '');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    try {
      const out = await workflowsApi.run(workflow.id, projectId, input.trim());
      if (out.runRef) {
        toast.success(`${out.runRef} started`, {
          description: `${out.planRef} · ${out.agents} agent${out.agents === 1 ? '' : 's'} in worktrees of their own`,
          action: { label: 'Live Runs', onClick: () => nav('/runs') },
        });
      } else if (out.openQuestions) {
        toast(`${out.planRef} is waiting on you`, { description: out.note, action: { label: 'Plans', onClick: () => nav('/plans') } });
      } else {
        toast.warning(`${out.planRef} dispatched, no run started`, { description: out.note });
      }
      onStarted();
    } catch (e) {
      toast.error('Not started', { description: reason(e, 'The local API did not answer.') });
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Run {workflow.name}</DialogTitle>
          <DialogDescription>
            {workflow.builtin
              ? 'Compiled into a plan; an open question waits in Plans.'
              : 'Its steps become a plan, dispatched into worktrees.'}
          </DialogDescription>
        </DialogHeader>
        {allowed.length === 0 ? (
          <Empty icon={<FolderGit2 className="size-6" />} title="No project here can run it"
            hint={workflow.projectId ? `${workflow.projectName ?? 'Its project'} has no code on this machine.` : 'Onboard a repository in Projects first.'} />
        ) : (
          <div className="grid gap-3">
            <SelectField label="Project" value={projectId} onChange={setProjectId} options={allowed.map((p) => ({ value: p.id, label: p.name }))} />
            <label className="block">
              <span className="mb-1.5 block text-[12.5px] font-medium text-soft">{workflow.builtin ? 'Requirement' : 'Input'}</span>
              <textarea
                value={input} onChange={(e) => setInput(e.target.value)} rows={5} autoFocus
                placeholder={workflow.builtin ? 'What should change, e.g. round totals per invoice' : 'What this run is about, e.g. the invoice tax report'}
                className="focus-brand w-full resize-none rounded-lg border border-line bg-surface-2/60 p-3 text-[13.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:outline-none"
              />
            </label>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => void start()} disabled={busy || !projectId || input.trim().length < 3}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}Run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const BLANK_STEP = { label: '', agent: '', detail: '' };

function EditorDialog({ initial, writers, projects, onClose, onSaved }: {
  initial: LiveWorkflowDetail | null; writers: string[]; projects: Project[];
  onClose: () => void; onSaved: (saved: LiveWorkflowDetail) => void;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [projectId, setProjectId] = useState(initial?.projectId ?? '');
  const [template, setTemplate] = useState(initial?.requirementTemplate ?? '{input}');
  const [steps, setSteps] = useState<WorkflowInput['steps']>(
    initial?.steps.length ? initial.steps.map((s) => ({ label: s.label, agent: s.agent, detail: s.detail })) : [{ ...BLANK_STEP, agent: writers[0] ?? '' }]);
  const [busy, setBusy] = useState(false);

  const put = (i: number, patch: Partial<WorkflowInput['steps'][number]>) =>
    setSteps((all) => all.map((s, k) => (k === i ? { ...s, ...patch } : s)));
  const valid = name.trim() && template.includes('{input}') && steps.length > 0 && steps.every((s) => s.label.trim() && s.agent);

  const save = async () => {
    setBusy(true);
    const body: WorkflowInput = { name: name.trim(), description: description.trim(), projectId: projectId || null, requirementTemplate: template.trim(), steps };
    try {
      const saved = initial ? await workflowsApi.update(initial.id, body) : await workflowsApi.create(body);
      toast.success(initial ? `${saved.name} saved` : `${saved.name} created`, { description: 'Nothing runs until someone runs it.' });
      onSaved(saved);
    } catch (e) {
      toast.error('Not saved', { description: reason(e, 'The local API did not answer.') });
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <DialogContent className="sm:max-w-[680px]">
        <DialogHeader>
          <DialogTitle>{initial ? `Edit ${initial.name}` : 'New workflow'}</DialogTitle>
          <DialogDescription>
            A workflow is its write steps. Merge, approved tests, review and your signature always follow.
          </DialogDescription>
        </DialogHeader>
        <div className="grid max-h-[62vh] gap-3 overflow-y-auto pr-1">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" value={name} onChange={setName} placeholder="api-and-screen" mono />
            <SelectField label="Scope" value={projectId} onChange={setProjectId}
              options={[{ value: '', label: 'Global · any project' }, ...projects.map((p) => ({ value: p.id, label: `Only ${p.name}` }))]} />
          </div>
          <Field label="Description" value={description} onChange={setDescription} placeholder="What it is for, in a sentence" />
          <Field label="Requirement" value={template} onChange={setTemplate} mono
            hint={template.includes('{input}') ? 'The run’s input goes where {input} is.' : 'Put {input} where the run’s input goes.'} />
          <div>
            <SectionTitle right={steps.length < 20 && <Button size="xs" variant="ghost" onClick={() => setSteps((all) => [...all, { ...BLANK_STEP, agent: writers[0] ?? '' }])}><Plus className="size-3" />Add step</Button>}>
              Steps
            </SectionTitle>
            <div className="divide-y divide-line/60 rounded-xl border border-line/70">
              {steps.map((s, i) => (
                <div key={i} className="grid gap-2 px-3.5 py-3">
                  <div className="flex items-center gap-2">
                    <span className="tnum w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{i + 1}</span>
                    <Field className="min-w-0 flex-1" value={s.label} onChange={(v) => put(i, { label: v })} placeholder="Add the endpoint" />
                    {steps.length > 1 && (
                      <Button size="icon-xs" variant="ghost" aria-label={`Remove step ${i + 1}`} onClick={() => setSteps((all) => all.filter((_, k) => k !== i))}>
                        <Trash2 className="size-3" />
                      </Button>
                    )}
                  </div>
                  <div className="grid gap-2 pl-6 sm:grid-cols-2">
                    <SelectField value={s.agent} onChange={(v) => put(i, { agent: v })} options={writers} />
                    <Field value={s.detail} onChange={(v) => put(i, { detail: v })} placeholder="Detail (optional)" />
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[12px] text-dim">
              Agents write at once, in their own worktrees. Approval steps are refused; the gate is yours.
            </p>
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
