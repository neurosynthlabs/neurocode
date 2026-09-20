import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Search, Play, Square, CheckCheck, GitBranch, AlertOctagon, Boxes, ListTodo } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Dot, Mono, Segmented,
  DataTable, Row, Cell, BlockBar, KV, Empty, SectionTitle, Avatar2, SelectField,
} from '@/components/os';
import { useData } from '@/lib/data';
import { agentName, useAccess } from '@/lib/access';
import type { RunDoc } from '@/lib/api';
import { projectLabel } from '@/lib/live/work';
import { useProject } from '@/lib/project-context';
import { ago } from '@/lib/time';
import { cn } from '@/lib/utils';
import type { Task, TaskStatus } from '@/types';

const COLUMNS: { id: TaskStatus; label: string }[] = [
  { id: 'backlog', label: 'Backlog' },
  { id: 'planning', label: 'Planning' },
  { id: 'in_progress', label: 'In progress' },
  { id: 'review', label: 'Review' },
  { id: 'blocked', label: 'Blocked' },
  { id: 'done', label: 'Done' },
];

const PRIO_TONE = { URGENT: 'danger', HIGH: 'warn', NORMAL: 'neutral', LOW: 'neutral' } as const;

/** How far a run got: its finished steps, as a share of all of them. */
const progress = (r: RunDoc) =>
  Math.round((100 * r.steps.filter((x) => ['done', 'skipped', 'failed'].includes(x.status)).length) / Math.max(1, r.steps.length));
const WORKING: RunDoc['status'][] = ['queued', 'running', 'waiting'];

function Card({ t, run, onOpen }: { t: Task; run: RunDoc | undefined; onOpen: () => void }) {
  const { catalogue } = useAccess();
  return (
    <button
      onClick={onOpen}
      className="hover-lift w-full rounded-md border border-line bg-surface p-2.5 text-left"
    >
      <div className="flex items-center gap-1.5">
        <Mono>{t.ref}</Mono>
        <Tag tone={PRIO_TONE[t.priority]}>{t.priority}</Tag>
        <span className="ml-auto"><RiskPill risk={t.risk} bare /></span>
      </div>
      <p className="mt-1.5 line-clamp-2 text-[13.5px] font-medium text-ink">{t.title}</p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {t.layers.map((l) => <span key={l} className="rounded-xs border border-line bg-surface-2 px-1 py-px text-[11px] text-dim">{l}</span>)}
      </div>
      {t.blockedReason && (
        <p className="mt-1.5 flex items-start gap-1 text-[11.5px] text-danger"><AlertOctagon className="mt-px size-2.5 shrink-0" />{t.blockedReason}</p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <div className="flex -space-x-1">
          {t.agents.slice(0, 4).map((a) => <Avatar2 key={a} label={agentName(catalogue, a)} />)}
        </div>
        {t.files > 0 && <span className="ml-auto tnum text-[11.5px] text-dim">{t.files} files</span>}
      </div>
      {run && <div className="mt-1.5" title={`${run.ref}: steps finished`}><BlockBar pct={progress(run)} width={16} /></div>}
    </button>
  );
}

export default function Tasks() {
  const nav = useNavigate();
  const { projectId } = useProject();
  const { catalogue } = useAccess();
  const [view, setView] = useState<'board' | 'table' | 'epics'>('board');
  const [proj, setProj] = useState(projectId ?? 'all');
  const [prio, setPrio] = useState('all');
  const [risk, setRisk] = useState('all');
  const [agent, setAgent] = useState('all');
  const [q, setQ] = useState('');
  const { tasks, projects, plans, runs, moveTask, toggleCheck, dispatchPlan, cancelRun } = useData();
  // Held by ref, not by object, so the sheet always shows the task as it is now.
  const wanted = useSearchParams()[0].get('ref');
  // ?ref= opens a task; a click opens another (or closes it) until the link changes again.
  const [picked, setPicked] = useState<{ link: string | null; ref: string | null } | null>(null);
  const openRef = picked && picked.link === wanted ? picked.ref : wanted;
  const setOpenRef = (ref: string | null) => setPicked({ link: wanted, ref });
  const open = tasks.find((t) => t.ref === openRef) ?? null;
  // A task's work is its runs. The newest one that leads (not one agent's part of it) is how far it got.
  const latestRun = useMemo(() => {
    const m = new Map<string, RunDoc>();
    runs.forEach((r) => { if (r.taskRef && !r.parent && !m.has(r.taskRef)) m.set(r.taskRef, r); });
    return m;
  }, [runs]);
  // The agents a plan named, as the tasks carry them: the only names this filter can match.
  const agentNames = useMemo(() => [...new Set(tasks.flatMap((t) => t.agents))].sort(), [tasks]);
  const openPlan = open ? plans.find((p) => p.taskRef === open.ref) : undefined;
  const openRuns = open ? runs.filter((r) => r.taskRef === open.ref && !r.parent) : [];
  const openRun = open ? latestRun.get(open.ref) : undefined;

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return tasks.filter((t) => {
      if (proj !== 'all' && t.projectId !== proj) return false;
      if (prio !== 'all' && t.priority !== prio) return false;
      if (risk !== 'all' && t.risk !== risk) return false;
      if (agent !== 'all' && !t.agents.includes(agent)) return false;
      if (!s) return true;
      return (t.ref + t.title + t.requirement + (t.epic ?? '')).toLowerCase().includes(s);
    });
  }, [tasks, proj, prio, risk, agent, q]);

  const epics = useMemo(() => {
    const m = new Map<string, Task[]>();
    list.forEach((t) => {
      const k = t.epic || 'No epic';
      m.set(k, [...(m.get(k) ?? []), t]);
    });
    return [...m.entries()];
  }, [list]);

  const move = async (to: TaskStatus, note: string) => {
    if (!open) return;
    if (await moveTask(open.ref, to)) toast(`${open.ref} → ${COLUMNS.find((c) => c.id === to)?.label}`, { description: note });
  };
  const dispatch = async () => {
    if (!openPlan) return;
    if (await dispatchPlan(openPlan.ref)) toast.success(`${openPlan.ref} dispatched`, { description: 'Its run, once one starts, is in Live runs.' });
  };
  const stop = async () => {
    if (!openRun) return;
    if (await cancelRun(openRun.ref)) toast(`${openRun.ref} stopped`, { description: 'The worktree stays for you to look at.' });
  };

  return (
    <Page>
      <PageHeader
        title="Tasks"
        subtitle="A compiled requirement becomes a task. Once its plan is dispatched, the work runs in worktrees of its own — see Live runs."
        actions={
          <Segmented
            options={[{ id: 'board', label: 'Board' }, { id: 'table', label: 'Table' }, { id: 'epics', label: 'Epics' }]}
            value={view}
            onChange={setView}
          />
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <div className="flex h-9 w-64 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
            <Search className="size-3.5 shrink-0 text-dim" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search tasks, refs, requirements…"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none" />
          </div>
          <select value={proj} onChange={(e) => setProj(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] text-ink-2">
            <option value="all" className="bg-surface">All projects</option>
            {projects.map((p) => <option key={p.id} value={p.id} className="bg-surface">{p.name}</option>)}
          </select>
          <select value={prio} onChange={(e) => setPrio(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] text-ink-2">
            {['all', 'URGENT', 'HIGH', 'NORMAL', 'LOW'].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'Any priority' : k}</option>)}
          </select>
          <select value={risk} onChange={(e) => setRisk(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] text-ink-2">
            {['all', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'Any risk' : k}</option>)}
          </select>
          <select value={agent} onChange={(e) => setAgent(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] text-ink-2">
            <option value="all" className="bg-surface">Any agent</option>
            {agentNames.map((a) => <option key={a} value={a} className="bg-surface">{agentName(catalogue, a)}</option>)}
          </select>
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {tasks.length}</span>
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        {tasks.length === 0 && (
          <Empty icon={<ListTodo className="size-6" />} title="No tasks yet"
            hint="A task is created when you compile a requirement or start a workflow."
            action={<Button size="sm" onClick={() => nav('/')}>Compile a requirement</Button>} />
        )}

        {tasks.length > 0 && view === 'board' && (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 2xl:grid-cols-6">
            {COLUMNS.map((c) => {
              const items = list.filter((t) => t.status === c.id);
              return (
                <div key={c.id} className="flex min-w-0 flex-col">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5">
                      <Dot state={c.id} pulse={c.id === 'in_progress'} />
                      <span className="eyebrow">{c.label}</span>
                    </span>
                    <span className="tnum text-[12px] text-dim">{items.length}</span>
                  </div>
                  <div className="space-y-2">
                    {items.length === 0
                      ? <div className="rounded-md border border-dashed border-line px-2 py-4 text-center text-[12px] text-dim">empty</div>
                      : items.map((t) => <Card key={t.id} t={t} run={latestRun.get(t.ref)} onOpen={() => setOpenRef(t.ref)} />)}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {tasks.length > 0 && view === 'table' && (
          <Panel flush>
            {list.length === 0 ? <Empty title="No task matches" /> : (
              // Eleven columns do not fit a phone: below each width the ones that are not what the screen is
              // for step out, and the row still opens the task with all of them.
              <DataTable head={['Ref', 'Task', { label: 'Project', hideBelow: 'md' }, 'Status',
                { label: 'Priority', hideBelow: 'sm' }, { label: 'Risk', hideBelow: 'md' },
                { label: 'Layers', hideBelow: 'lg' }, { label: 'Agents', hideBelow: 'lg' },
                { label: 'Files', hideBelow: 'xl' }, { label: 'Run', hideBelow: 'md' },
                { label: 'Updated', hideBelow: 'sm' }]}>
                {list.map((t) => {
                  const run = latestRun.get(t.ref);
                  return (
                  <Row key={t.id} onClick={() => setOpenRef(t.ref)}>
                    <Cell mono>{t.ref}</Cell>
                    <Cell className="font-medium text-ink">{t.title}</Cell>
                    <Cell hideBelow="md" className="text-dim">{projectLabel(projects, t.projectId)}</Cell>
                    <Cell><span className="flex items-center gap-1.5"><Dot state={t.status} /><span className="capitalize">{t.status.replace('_', ' ')}</span></span></Cell>
                    <Cell hideBelow="sm"><Tag tone={PRIO_TONE[t.priority]}>{t.priority}</Tag></Cell>
                    <Cell hideBelow="md"><RiskPill risk={t.risk} bare /></Cell>
                    <Cell hideBelow="lg" className="text-[12.5px] text-dim">{t.layers.join(' · ')}</Cell>
                    <Cell hideBelow="lg" className="text-[12.5px]">{t.agents.length ? t.agents.map((a) => agentName(catalogue, a)).join(', ') : <span className="text-dim">none named</span>}</Cell>
                    <Cell hideBelow="xl" className="tnum">{t.files}</Cell>
                    <Cell hideBelow="md">
                      {run
                        ? <span className="flex items-center gap-2"><BlockBar pct={progress(run)} width={10} /><span className="tnum text-[12px]">{progress(run)}%</span></span>
                        : <span className="text-[12px] text-dim">not dispatched</span>}
                    </Cell>
                    <Cell hideBelow="sm" className="text-dim">{ago(t.updatedAt)}</Cell>
                  </Row>
                  );
                })}
              </DataTable>
            )}
          </Panel>
        )}

        {tasks.length > 0 && view === 'epics' && (
          <div className="space-y-3">
            <p className="text-[12.5px] text-dim">{epics.length} epic{epics.length === 1 ? '' : 's'} · {list.length} task{list.length === 1 ? '' : 's'}</p>
            {epics.length === 0 && <Empty title="No task matches" />}
            {epics.map(([epic, items]) => {
              const finished = items.filter((t) => t.status === 'done').length;
              return (
                <Panel key={epic} eyebrow={`${items.length} tasks`} title={<span className="flex items-center gap-1.5"><Boxes className="size-3.5 text-brand" />{epic}</span>}
                  actions={<span className="flex items-center gap-2"><BlockBar pct={(100 * finished) / items.length} width={14} /><span className="tnum text-[12.5px] text-soft">{finished} of {items.length} done</span></span>} flush>
                  <div className="divide-y divide-line">
                    {items.map((t) => (
                      <button key={t.id} onClick={() => setOpenRef(t.ref)} className="flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-surface-2">
                        <Dot state={t.status} />
                        <Mono>{t.ref}</Mono>
                        <span className="min-w-0 flex-1 truncate text-[13.5px] text-ink">{t.title}</span>
                        <Tag tone={PRIO_TONE[t.priority]}>{t.priority}</Tag>
                        <RiskPill risk={t.risk} bare />
                        <span className="w-24 text-right text-[12px] text-soft capitalize">{t.status.replace('_', ' ')}</span>
                      </button>
                    ))}
                  </div>
                </Panel>
              );
            })}
          </div>
        )}
      </PageBody>

      {/* Detail sheet */}
      <Sheet open={!!open} onOpenChange={(o) => !o && setOpenRef(null)}>
        <SheetContent side="right" className="w-[620px] gap-0 overflow-y-auto p-0 sm:max-w-none">
          {open && (
            <>
              <SheetHeader className="border-b border-line px-5 pt-5 pb-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Mono tone="brand">{open.ref}</Mono>
                  <Tag tone={PRIO_TONE[open.priority]}>{open.priority}</Tag>
                  <RiskPill risk={open.risk} />
                  <Tag tone="neutral"><Dot state={open.status} />{open.status.replace('_', ' ')}</Tag>
                </div>
                <SheetTitle className="mt-2 text-[16px] text-ink">{open.title}</SheetTitle>
                <SheetDescription className="text-[13px] text-soft">
                  {open.epic ? `Epic · ${open.epic}` : 'No epic'} · {projectLabel(projects, open.projectId)}
                </SheetDescription>
              </SheetHeader>

              <div className="space-y-3 p-5">
                <Panel eyebrow="What you actually typed" title="Raw requirement">
                  <p className="text-[14px] leading-relaxed text-ink-2">{open.requirement}</p>
                </Panel>

                {open.blockedReason && (
                  <Panel eyebrow="Blocked" title="Waiting on a human" className="border-danger/35">
                    <p className="text-[13.5px] text-ink-2">{open.blockedReason}</p>
                  </Panel>
                )}

                <div className="grid grid-cols-2 gap-3">
                  <Panel eyebrow="Assignment" title="Agents">
                    {open.agents.length === 0 ? <p className="text-[13px] text-dim">The plan named no agent.</p> : (
                      <div className="space-y-1.5">
                        {open.agents.map((a) => (
                          <div key={a} className="flex items-center gap-2">
                            <Avatar2 label={agentName(catalogue, a)} />
                            <span className="text-[13px] text-ink-2">{agentName(catalogue, a)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </Panel>
                  <Panel eyebrow="Scope" title="Surface">
                    {open.layers.length > 0 && <KV k="Layers" v={open.layers.join(' · ')} />}
                    <KV k="Files named" v={open.files} />
                    {openRun && <KV k="Branch" v={openRun.removed ? `${openRun.branch} (removed)` : openRun.branch} mono />}
                    {openRun && <KV k="Run" v={`${openRun.ref} · ${openRun.status} · ${progress(openRun)}%`} />}
                  </Panel>
                </div>

                {open.checklist.length > 0 && (
                  <Panel eyebrow="Progress" title="Checklist" flush>
                    <div className="divide-y divide-line">
                      {open.checklist.map((c) => {
                        const done = c.done;
                        return (
                          <button key={c.id} onClick={() => toggleCheck(open.ref, c.id)} role="checkbox" aria-checked={done} className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left hover:bg-surface-2">
                            <span className={cn('grid size-3.5 shrink-0 place-items-center rounded-xs border',
                              done ? 'border-ok bg-ok/15 text-ok' : 'border-line-strong text-transparent')}>
                              <CheckCheck className="size-2.5" />
                            </span>
                            <span className={cn('text-[13px]', done ? 'text-dim line-through' : 'text-ink-2')}>{c.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </Panel>
                )}

                <SectionTitle>Linked</SectionTitle>
                <div className="flex flex-wrap gap-1.5">
                  {openPlan && <Link to={`/plans?ref=${openPlan.ref}`}><Mono tone="brand">{openPlan.ref}</Mono></Link>}
                  {openRuns.map((r) => (
                    <Link key={r.ref} to={`/runs?ref=${r.ref}`}><Mono><GitBranch className="mr-1 inline size-2.5" />{r.ref}</Mono></Link>
                  ))}
                  {!openPlan && openRuns.length === 0 && <span className="text-[12.5px] text-dim">Nothing is linked to this task yet.</span>}
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                  {openPlan && openPlan.status !== 'dispatched' && (
                    <Button size="sm" disabled={openPlan.openQuestions.length > 0} onClick={() => void dispatch()}
                      title={openPlan.openQuestions.length > 0 ? 'Answer or defer the plan\'s open questions first' : undefined}>
                      <Play className="size-3.5" />Dispatch plan
                    </Button>
                  )}
                  {openRun && WORKING.includes(openRun.status) && (
                    <Button size="sm" variant="outline" onClick={() => void stop()}><Square className="size-3.5" />Stop run</Button>
                  )}
                  {open.status === 'review' && (
                    <Button size="sm" variant="outline" onClick={() => move('done', 'Marked done.')}>
                      <CheckCheck className="size-3.5" />Mark done
                    </Button>
                  )}
                  <SelectField className="ml-auto w-36" value={open.status} onChange={(v) => move(v as TaskStatus, 'Moved by hand.')}
                    options={COLUMNS.map((c) => ({ value: c.id, label: c.label }))} />
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </Page>
  );
}
