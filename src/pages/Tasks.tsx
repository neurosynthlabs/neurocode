import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search, Play, Pause, CheckCheck, GitBranch, AlertOctagon, Boxes } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Dot, Mono, Ascii, Segmented,
  DataTable, Row, Cell, BlockBar, KV, Empty, SectionTitle, Avatar2, SelectField,
} from '@/components/os';
import { useData } from '@/lib/data';
import { agentName, agents } from '@/mock/agents';
import { projects } from '@/mock/projects';
import { useProject } from '@/lib/project-context';
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

const EPIC_TREE = `EPIC · TASK-488 — Customer bulk upload (CSV, 50k rows)
│
├── Architecture
│     └── staging-table + transaction boundary  (ADR-49)
│
├── Frontend                          Frontend Engineer
│     ├── drag-drop upload UI
│     ├── client-side column validation
│     └── chunked progress + row-level errors
│
├── Backend                           Backend Engineer
│     ├── POST /customers/bulk  (chunked, idempotency key)
│     ├── server validation + error envelope
│     └── CustomerImportService  (interface-first)
│
├── Database                          Database Engineer
│     ├── STG_CUSTOMER_IMPORT staging table
│     └── SP_CommitCustomerImport  (all-or-nothing)
│
├── Testing                           QA Engineer
│     ├── unit — validator matrix
│     ├── integration — partial failure rolls back
│     └── E2E — 50k row happy path under 90s
│
├── Security                          Security Engineer
│     └── CSV formula-injection + upload size cap
│
└── Documentation                     Documentation Agent
      └── import format + operator runbook`;

function Card({ t, onOpen }: { t: Task; onOpen: () => void }) {
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
      <p className="mt-1.5 line-clamp-2 text-[12.5px] font-medium text-ink">{t.title}</p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {t.layers.map((l) => <span key={l} className="rounded-xs border border-line bg-surface-2 px-1 py-px text-[10px] text-dim">{l}</span>)}
      </div>
      {t.blockedReason && (
        <p className="mt-1.5 flex items-start gap-1 text-[10.5px] text-danger"><AlertOctagon className="mt-px size-2.5 shrink-0" />{t.blockedReason}</p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <div className="flex -space-x-1">
          {t.agents.slice(0, 4).map((a) => <Avatar2 key={a} label={agentName(a)} />)}
        </div>
        <span className="ml-auto tnum text-[10.5px] text-dim">{t.files}f · {t.tests}t</span>
      </div>
      {t.progress > 0 && <div className="mt-1.5"><BlockBar pct={t.progress} width={16} /></div>}
    </button>
  );
}

export default function Tasks() {
  const { projectId } = useProject();
  const [view, setView] = useState<'board' | 'table' | 'epics'>('board');
  const [proj, setProj] = useState(projectId);
  const [prio, setPrio] = useState('all');
  const [risk, setRisk] = useState('all');
  const [agent, setAgent] = useState('all');
  const [q, setQ] = useState('');
  const { tasks, moveTask, toggleCheck } = useData();
  // Held by ref, not by object, so the sheet always shows the task as it is now.
  const wanted = useSearchParams()[0].get('ref');
  const [openRef, setOpenRef] = useState<string | null>(wanted);
  useEffect(() => { if (wanted) setOpenRef(wanted); }, [wanted]);
  const open = useMemo(() => tasks.find((t) => t.ref === openRef) ?? null, [tasks, openRef]);

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
      const k = t.epic ?? 'Unassigned';
      m.set(k, [...(m.get(k) ?? []), t]);
    });
    return [...m.entries()];
  }, [list]);

  const move = async (to: TaskStatus, note: string) => {
    if (!open) return;
    if (await moveTask(open.ref, to)) toast(`${open.ref} → ${COLUMNS.find((c) => c.id === to)?.label}`, { description: note });
  };

  return (
    <Page>
      <PageHeader
        title="Tasks"
        subtitle="A requirement becomes an epic, an epic becomes agent-sized tasks, each task becomes its own worktree."
        actions={
          <Segmented
            options={[{ id: 'board', label: 'Board' }, { id: 'table', label: 'Table' }, { id: 'epics', label: 'Epics' }]}
            value={view}
            onChange={setView}
          />
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <div className="flex h-7 w-64 items-center gap-2 rounded-sm border border-line bg-surface-2 px-2.5 focus-within:border-brand">
            <Search className="size-3.5 shrink-0 text-dim" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search tasks, refs, requirements…"
              className="min-w-0 flex-1 bg-transparent text-[12px] text-ink placeholder:text-dim focus-visible:outline-none" />
          </div>
          <select value={proj} onChange={(e) => setProj(e.target.value)} className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
            <option value="all" className="bg-surface">All projects</option>
            {projects.map((p) => <option key={p.id} value={p.id} className="bg-surface">{p.name}</option>)}
          </select>
          <select value={prio} onChange={(e) => setPrio(e.target.value)} className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
            {['all', 'URGENT', 'HIGH', 'NORMAL', 'LOW'].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'Any priority' : k}</option>)}
          </select>
          <select value={risk} onChange={(e) => setRisk(e.target.value)} className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
            {['all', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'Any risk' : k}</option>)}
          </select>
          <select value={agent} onChange={(e) => setAgent(e.target.value)} className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
            <option value="all" className="bg-surface">Any agent</option>
            {agents.map((a) => <option key={a.id} value={a.id} className="bg-surface">{a.name}</option>)}
          </select>
          <span className="ml-auto text-[11.5px] text-dim">{list.length} of {tasks.length}</span>
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        <Panel eyebrow="Automatic decomposition" title="One Hinglish sentence became 14 agent-sized units">
          <Ascii>{EPIC_TREE}</Ascii>
        </Panel>

        {view === 'board' && (
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
                    <span className="tnum text-[11px] text-dim">{items.length}</span>
                  </div>
                  <div className="space-y-2">
                    {items.length === 0
                      ? <div className="rounded-md border border-dashed border-line px-2 py-4 text-center text-[11px] text-dim">empty</div>
                      : items.map((t) => <Card key={t.id} t={t} onOpen={() => setOpenRef(t.ref)} />)}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {view === 'table' && (
          <Panel flush>
            {list.length === 0 ? <Empty title="No task matches" /> : (
              <DataTable head={['Ref', 'Task', 'Project', 'Status', 'Priority', 'Risk', 'Layers', 'Agents', 'Files', 'Tests', 'Progress', 'Updated']}>
                {list.map((t) => (
                  <Row key={t.id} onClick={() => setOpenRef(t.ref)}>
                    <Cell mono>{t.ref}</Cell>
                    <Cell className="font-medium text-ink">{t.title}</Cell>
                    <Cell className="text-dim">{projects.find((p) => p.id === t.projectId)?.name}</Cell>
                    <Cell><span className="flex items-center gap-1.5"><Dot state={t.status} /><span className="capitalize">{t.status.replace('_', ' ')}</span></span></Cell>
                    <Cell><Tag tone={PRIO_TONE[t.priority]}>{t.priority}</Tag></Cell>
                    <Cell><RiskPill risk={t.risk} bare /></Cell>
                    <Cell className="text-[11.5px] text-dim">{t.layers.join(' · ')}</Cell>
                    <Cell className="text-[11.5px]">{t.agents.length ? t.agents.map(agentName).join(', ') : '—'}</Cell>
                    <Cell className="tnum">{t.files}</Cell>
                    <Cell className="tnum">{t.tests}</Cell>
                    <Cell><span className="flex items-center gap-2"><BlockBar pct={t.progress} width={10} /><span className="tnum text-[11px]">{t.progress}%</span></span></Cell>
                    <Cell className="text-dim">{t.updatedAt}</Cell>
                  </Row>
                ))}
              </DataTable>
            )}
          </Panel>
        )}

        {view === 'epics' && (
          <div className="space-y-3">
            {epics.map(([epic, items]) => {
              const pct = Math.round(items.reduce((n, t) => n + t.progress, 0) / items.length);
              return (
                <Panel key={epic} eyebrow={`${items.length} tasks`} title={<span className="flex items-center gap-1.5"><Boxes className="size-3.5 text-brand" />{epic}</span>}
                  actions={<span className="flex items-center gap-2"><BlockBar pct={pct} width={14} /><span className="tnum text-[11.5px] text-soft">{pct}%</span></span>} flush>
                  <div className="divide-y divide-line">
                    {items.map((t) => (
                      <button key={t.id} onClick={() => setOpenRef(t.ref)} className="flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-surface-2">
                        <Dot state={t.status} />
                        <Mono>{t.ref}</Mono>
                        <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{t.title}</span>
                        <Tag tone={PRIO_TONE[t.priority]}>{t.priority}</Tag>
                        <RiskPill risk={t.risk} bare />
                        <span className="tnum w-10 text-right text-[11px] text-soft">{t.progress}%</span>
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
                <SheetDescription className="text-[12px] text-soft">
                  {open.epic ? `Epic · ${open.epic}` : 'No epic'} · {projects.find((p) => p.id === open.projectId)?.name}
                </SheetDescription>
              </SheetHeader>

              <div className="space-y-3 p-5">
                <Panel eyebrow="What you actually typed" title="Raw requirement">
                  <p className="text-[13px] leading-relaxed text-ink-2">{open.requirement}</p>
                </Panel>

                {open.blockedReason && (
                  <Panel eyebrow="Blocked" title="Waiting on a human" className="border-danger/35">
                    <p className="text-[12.5px] text-ink-2">{open.blockedReason}</p>
                  </Panel>
                )}

                <div className="grid grid-cols-2 gap-3">
                  <Panel eyebrow="Assignment" title="Agents">
                    {open.agents.length === 0 ? <p className="text-[12px] text-dim">Unassigned — still in the backlog.</p> : (
                      <div className="space-y-1.5">
                        {open.agents.map((a) => (
                          <div key={a} className="flex items-center gap-2">
                            <Avatar2 label={agentName(a)} />
                            <span className="text-[12px] text-ink-2">{agentName(a)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </Panel>
                  <Panel eyebrow="Scope" title="Surface">
                    <KV k="Layers" v={open.layers.join(' · ')} />
                    <KV k="Files" v={open.files} />
                    <KV k="Tests" v={open.tests} />
                    <KV k="Worktree" v={open.worktree ?? '—'} mono />
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
                            <span className={cn('text-[12px]', done ? 'text-dim line-through' : 'text-ink-2')}>{c.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </Panel>
                )}

                <SectionTitle>Linked</SectionTitle>
                <div className="flex flex-wrap gap-1.5">
                  <Mono tone="brand">PLAN-{open.ref.split('-')[1]}</Mono>
                  <Mono>REV-{Number(open.ref.split('-')[1]) * 5 + 151}</Mono>
                  {open.worktree && <Mono><GitBranch className="mr-1 inline size-2.5" />{open.worktree}</Mono>}
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                  <Button size="sm" disabled={open.status === 'in_progress' || open.status === 'done'} onClick={() => move('in_progress', 'Handed to the orchestrator.')}>
                    <Play className="size-3.5" />Run
                  </Button>
                  <Button size="sm" variant="outline" disabled={open.status === 'backlog' || open.status === 'done'} onClick={() => move('backlog', 'Paused. Its worktrees are kept.')}>
                    <Pause className="size-3.5" />Pause
                  </Button>
                  <Button size="sm" variant="outline" disabled={open.status !== 'review'} title={open.status === 'review' ? undefined : 'Only a task in review can be approved for merge'}
                    onClick={() => move('done', 'Approved for merge.')}>
                    <CheckCheck className="size-3.5" />Approve
                  </Button>
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
