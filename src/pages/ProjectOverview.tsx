import { useMemo, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Code2, ExternalLink, FileText, FolderGit2, Globe, Loader2, RefreshCw, Scale, Search, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { SourcesPanel } from '@/components/projects/SourcesPanel';
import {
  Page, PageHeader, PageBody, Panel, Stat, StatGrid, Tag, RiskPill, Dot, Mono,
  DataTable, Row, Cell, MeterRow, Segmented, KV, BlockBar, Empty, SectionTitle, ListRow,
} from '@/components/os';
import { agentName, useAccess } from '@/lib/access';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useProject } from '@/lib/project-context';
import { instructionsApi, kb } from '@/lib/live/instructions';
import { useRemote } from '@/lib/remote';
import type { Project } from '@/types';
import { ago } from './code/format';

type Tab = 'modules' | 'instructions' | 'rules' | 'decisions' | 'hotspots';

export default function ProjectOverview() {
  const { projectId } = useParams();
  const nav = useNavigate();
  const { all: projects } = useProject();
  const p = projects.find((x) => x.id === projectId);
  if (!p) {
    return (
      <Page>
        <PageHeader title="Project" />
        <PageBody>
          <Empty icon={<FolderGit2 className="size-6" />} title="No such project"
            hint="It may have been removed, or the workspace was emptied. Pick one in Projects."
            action={<Button size="sm" variant="outline" onClick={() => nav('/projects')}>Open Projects</Button>} />
        </PageBody>
      </Page>
    );
  }
  return <Overview key={p.id} p={p} />;
}

function Overview({ p }: { p: Project }) {
  const nav = useNavigate();
  const { can } = useAuth();
  const { catalogue } = useAccess();
  const { tasks, memory } = useData();
  const { setProjectId } = useProject();
  const [tab, setTab] = useState<Tab>('modules');
  const [q, setQ] = useState('');
  const [asked, setAsked] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);

  // The index's finish time streams in on the project record: a new index reloads the summary.
  const stamp = p.codeIndex?.at ?? 'none';
  const s = useRemote(p.source ? `${p.id}:${stamp}:summary` : null, () => api.code.summary(p.id));
  const summary = s.data;
  const indexing = asked === stamp || !!summary?.indexing;

  const modules = useMemo(() => {
    const all = summary?.modules ?? [];
    const t = q.trim().toLowerCase();
    return t ? all.filter((m) => m.name.toLowerCase().includes(t)) : all;
  }, [summary, q]);
  // Read off the checkout on every request, so a new index — the likeliest moment a file changed — reads it again.
  const ins = useRemote(p.source ? `${p.id}:${stamp}:instructions` : null, () => instructionsApi.list(p.id));
  const told = ins.data;
  const hotspots = summary?.hotspots ?? [];
  const rules = p.rules ?? [];
  const decisions = useMemo(
    () => memory.filter((f) => f.category === 'decisions' && !f.archived && (f.projectId === p.id || f.projectId === null)),
    [memory, p.id],
  );
  const myTasks = useMemo(() => tasks.filter((t) => t.projectId === p.id), [tasks, p.id]);

  const reindex = async () => {
    setAsked(stamp);
    try {
      await api.code.reindex(p.id);
      toast('Reading the code again', { description: 'The modules and hotspots refresh on their own when the index is ready.' });
    } catch (e) {
      setAsked(null);
      toast.error('Not re-indexed', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    }
  };
  const openIn = (to: string) => { setProjectId(p.id); nav(to); };

  const draft = async () => {
    setDrafting(true);
    try {
      const plan = await instructionsApi.draft(p.id);
      toast.success(`${plan.ref} compiled`, { description: 'Dispatch it when its questions are settled; AGENTS.md is written in a worktree and lands with your signature.' });
      openIn(`/plans?ref=${encodeURIComponent(plan.ref)}`);
    } catch (e) {
      toast.error('No draft compiled', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    } finally {
      setDrafting(false);
    }
  };

  const notIndexed = (
    <Empty icon={indexing ? <Loader2 className="size-5 animate-spin" /> : undefined}
      title={indexing ? `Reading ${p.name}…` : `${p.name} has no index yet`}
      hint={!p.source ? 'Its code was not onboarded on this machine, so there is nothing to index.'
        : indexing ? 'This fills in the moment the index is ready.' : 'Index it to see its modules and the files most depended on.'}
      action={p.source && !indexing && can('projects:onboard') && summary?.canIndex
        ? <Button size="sm" onClick={() => void reindex()}>Index now</Button> : undefined} />
  );
  const indexBody = (ready: ReactNode) =>
    s.error ? <Empty title="The index did not load" hint={s.error} action={<Button size="sm" variant="outline" onClick={s.reload}>Try again</Button>} />
      : p.source && !summary ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the index…" />
        : !summary?.indexed ? notIndexed : ready;

  return (
    <Page>
      <PageHeader
        title={p.name}
        subtitle={p.description}
        actions={
          <>
            {p.source && can('machine:access') && (
              <Button size="sm" variant="outline" onClick={() => openIn(`/workbench?project=${encodeURIComponent(p.id)}`)}><Code2 className="size-3.5" />Open in Workbench</Button>
            )}
            <Button size="sm" variant="outline" onClick={() => openIn('/code')}><ExternalLink className="size-3.5" />Code intelligence</Button>
            {p.source && can('projects:onboard') && summary?.canIndex && (
              <Button size="sm" variant="outline" disabled={indexing} onClick={() => void reindex()}>
                {indexing ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}{indexing ? 'Indexing…' : 'Re-index'}
              </Button>
            )}
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-3 pb-3">
          <span className="flex items-center gap-1.5"><Dot state={p.status} pulse={p.status === 'active'} /><span className="text-[13px] text-ink-2 capitalize">{p.status}</span></span>
          <Mono>{p.codename}</Mono>
          {p.repo && <Mono>{p.repo}</Mono>}
          <div className="flex flex-wrap gap-1">
            {p.stack.map((x) => <span key={x} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[11.5px] text-ink-2">{x}</span>)}
          </div>
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        <div className="grid grid-cols-12 gap-3">
          <Panel eyebrow="Measured when the code was indexed" className="col-span-12 xl:col-span-5"
            title={p.understoodPct === null ? 'Not indexed yet' : `Project understood: ${p.understoodPct}%`}>
            {p.coverage.length === 0
              ? <p className="text-[13px] text-dim">Nothing has been measured yet.</p>
              : p.coverage.map((c) => <MeterRow key={c.label} label={c.label} pct={c.pct} />)}
            <p className="mt-2 border-t border-line pt-2 text-[12px] text-dim">
              Understood is the share of the files the onboarding scan found that the code index holds.
            </p>
          </Panel>

          <div className="col-span-12 space-y-3 xl:col-span-7">
            <StatGrid cols={4}>
              <Stat label="Tasks" value={p.work.tasks} sub={`${myTasks.filter((t) => t.status === 'done').length} done`} />
              <Stat label="Running" value={p.work.running} tone="ok" sub="in progress" />
              <Stat label="In review" value={p.work.review} tone="warn" sub="awaiting review" />
              <Stat label="Blocked" value={p.work.blocked} tone={p.work.blocked ? 'danger' : 'neutral'} sub="need a decision" />
            </StatGrid>
            <Panel eyebrow="Where the code came from" title="Source" flush>
              <div className="grid grid-cols-1 gap-x-6 px-3.5 py-1.5 md:grid-cols-2">
                {p.source ? (
                  <>
                    <KV k="Source" v={p.source.repo} mono />
                    <KV k={p.source.kind === 'git' ? 'Branch' : 'Kind'} v={p.source.kind === 'git' ? p.source.branch ?? '' : 'local folder'} mono={p.source.kind === 'git'} />
                    <KV k="Files measured" v={(p.files ?? 0).toLocaleString()} />
                    <KV k="Lines" v={p.lines} />
                    {p.languages && p.languages.length > 0 && <KV k="Languages" v={p.languages.map((l) => `${l.name} ${l.pct}%`).join(' · ')} />}
                    {p.codeIndex && <KV k="Indexed" v={ago(p.codeIndex.at)} />}
                  </>
                ) : (
                  <KV k="Repository" v={p.repo || 'not recorded'} mono />
                )}
              </div>
            </Panel>
          </div>
        </div>

        <SourcesPanel p={p} />

        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            options={[
              { id: 'modules', label: summary?.indexed ? `Modules (${summary.modules?.length ?? 0})` : 'Modules' },
              { id: 'instructions', label: told ? `Instructions (${told.files.length})` : 'Instructions' },
              { id: 'rules', label: `Rules (${rules.length})` },
              { id: 'decisions', label: `Decisions (${decisions.length})` },
              { id: 'hotspots', label: summary?.indexed ? `Most depended on (${hotspots.length})` : 'Most depended on' },
            ]}
            value={tab}
            onChange={setTab}
          />
          {tab === 'modules' && summary?.indexed && (
            <div className="flex h-9 w-72 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
              <Search className="size-3.5 shrink-0 text-dim" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter modules…" aria-label="Filter modules"
                className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none" />
            </div>
          )}
        </div>

        {tab === 'modules' && (
          <Panel flush eyebrow="From the code index" title="Modules">
            {indexBody(modules.length === 0 ? (
              <Empty title={q.trim() ? 'No module matches' : 'No modules found'} hint={q.trim() ? 'Try part of a module name.' : 'The index holds no source files grouped into modules.'} />
            ) : (
              <DataTable head={['Module', 'Files', 'Lines', 'Symbols', 'Complexity', 'Depended on by', 'Depends on']}>
                {modules.map((m) => (
                  <Row key={m.name} onClick={() => openIn(`/architecture?module=${encodeURIComponent(m.name)}`)}>
                    <Cell mono className="font-medium text-ink">{m.name}</Cell>
                    <Cell className="tnum">{m.files.toLocaleString()}</Cell>
                    <Cell className="tnum">{m.lines.toLocaleString()}</Cell>
                    <Cell className="tnum">{m.symbols.toLocaleString()}</Cell>
                    <Cell className="tnum">{m.complexity.toLocaleString()}</Cell>
                    <Cell className="tnum">{m.fanIn} {m.fanIn === 1 ? 'module' : 'modules'}</Cell>
                    <Cell className="tnum">{m.fanOut} {m.fanOut === 1 ? 'module' : 'modules'}</Cell>
                  </Row>
                ))}
              </DataTable>
            ))}
          </Panel>
        )}

        {tab === 'instructions' && (
          <Panel flush eyebrow="Read from the checkout root when a plan is compiled or a session answers"
            title={<span className="flex items-center gap-1.5"><FileText className="size-3.5 text-brand" />Instructions</span>}
            actions={told && told.files.length > 0 ? (
              <span className={told.capped ? 'text-[12px] text-warn' : 'text-[12px] text-dim'}>
                {kb(told.bytes)} of {kb(told.cap)} handed to the model{told.capped ? ' · cut at the cap' : ''}
              </span>
            ) : undefined}>
            {!p.source ? (
              <Empty title="No instruction files here" hint="Its code was not onboarded on this machine, so there is no checkout to read them from." />
            ) : ins.error ? (
              <Empty title="The instructions did not load" hint={ins.error} action={<Button size="sm" variant="outline" onClick={ins.reload}>Try again</Button>} />
            ) : !told ? (
              <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the instruction files…" />
            ) : told.files.length === 0 ? (
              <Empty icon={<FileText className="size-6" />} title={`${p.name} has no instruction files`}
                hint={summary?.indexed
                  ? 'AGENTS.md, CLAUDE.md and .claude/rules/*.md at the checkout root are handed to every plan and session. Draft an AGENTS.md from the code index: it compiles a plan you dispatch and sign like any other.'
                  : 'AGENTS.md, CLAUDE.md and .claude/rules/*.md at the checkout root are handed to every plan and session. Index the code to draft an AGENTS.md from it.'}
                action={summary?.indexed && can('plans:compile') ? (
                  <Button size="sm" disabled={drafting} onClick={() => void draft()}>
                    {drafting && <Loader2 className="size-3.5 animate-spin" />}{drafting ? 'Compiling…' : 'Draft AGENTS.md'}
                  </Button>
                ) : undefined} />
            ) : (
              <>
                <DataTable head={['File', 'Scope', 'Size', 'SHA-1', 'Applies']}>
                  {told.files.map((f) => (
                    <Row key={f.path}>
                      <Cell>
                        <span className="block font-mono text-[12.5px] text-ink">{f.path}</span>
                        {f.importedBy && <span className="block text-[11.5px] text-dim">imported by {f.importedBy}</span>}
                      </Cell>
                      <Cell><Tag tone={f.scope === 'rules' ? 'violet' : 'neutral'}>{f.scope === 'rules' ? 'rule' : 'project'}</Tag></Cell>
                      <Cell className="tnum">{kb(f.bytes)}</Cell>
                      <Cell mono className="text-dim">{f.sha1.slice(0, 7)}</Cell>
                      <Cell className="text-[12.5px]">
                        {f.paths.length === 0
                          ? <span className="text-ink-2">always</span>
                          : <span className={f.applied ? 'text-ink-2' : 'text-dim'} title={f.paths.join(', ')}>
                              {f.matched ? `matched ${f.matched}` : `only for ${f.paths.join(', ')}`}
                            </span>}
                        {f.cut && <span className="ml-1.5"><Tag tone="warn">cut at the cap</Tag></span>}
                      </Cell>
                    </Row>
                  ))}
                </DataTable>
                {told.refused.length > 0 && (
                  <div className="border-t border-line px-5 py-3">
                    <p className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium text-warn"><ShieldAlert className="size-3.5" />Imports not read</p>
                    {told.refused.map((r) => (
                      <p key={`${r.from}:${r.path}`} className="text-[12.5px] text-soft [overflow-wrap:anywhere]">
                        <span className="font-mono text-ink-2">@{r.path}</span> in {r.from || 'the checkout root'} — {r.why}
                      </p>
                    ))}
                  </div>
                )}
                <p className="border-t border-line px-5 py-2.5 text-[12px] text-dim">
                  A rule with <span className="font-mono">paths:</span> is handed over only when the plan's files fall under it. HTML comments are left out.
                </p>
              </>
            )}
          </Panel>
        )}

        {tab === 'rules' && (
          <Panel eyebrow="Chosen when the project was onboarded · recorded, not yet enforced" title="Project rules" flush>
            {rules.length === 0 ? (
              <Empty title="No rules recorded" hint="Rules are chosen in the onboarding wizard. Nothing checks a change against them yet." />
            ) : (
              <div className="divide-y divide-line">
                {rules.map((r) => (
                  <div key={r.id} className="px-3.5 py-3">
                    <span className="text-[13.5px] font-medium text-ink">{r.label}</span>
                    {r.note && <p className="mt-1 text-[12.5px] text-soft">{r.note}</p>}
                  </div>
                ))}
              </div>
            )}
          </Panel>
        )}

        {tab === 'decisions' && (
          <Panel eyebrow="Facts filed under Decisions, for this project and the workspace" title="Decisions" flush>
            {decisions.length === 0 ? (
              <Empty icon={<Scale className="size-6" />} title="No decisions recorded yet"
                hint="Facts filed under Decisions in Memory appear here — from Add from text, or from a plan's answered question."
                action={<Button size="sm" variant="outline" onClick={() => openIn('/memory')}>Open Memory</Button>} />
            ) : decisions.map((f) => (
              <ListRow key={f.id} onClick={() => openIn(`/memory?ref=${encodeURIComponent(f.ref)}`)}>
                <div className="flex items-center gap-2">
                  <Mono tone={f.pinned ? 'brand' : 'neutral'}>{f.ref}</Mono>
                  {f.projectId === null && <Tag tone="violet"><Globe className="size-3" />workspace</Tag>}
                  <span className="ml-auto text-[11.5px] text-dim">{ago(f.createdAt)}</span>
                </div>
                <p className="mt-1 text-[13.5px] font-medium text-ink">{f.title}</p>
                <p className="mt-0.5 line-clamp-2 text-[12.5px] text-soft">{f.body}</p>
              </ListRow>
            ))}
          </Panel>
        )}

        {tab === 'hotspots' && (
          <Panel eyebrow="Ranked by how many files reach each one, then by its complexity" title="Most depended on" flush>
            {indexBody(hotspots.length === 0 ? (
              <Empty title="Nothing depends on anything yet" hint="The index found no file that another file uses." />
            ) : (
              <DataTable head={['Path', 'Risk', 'Reached by', 'Complexity', 'Lines']}>
                {hotspots.map((h) => (
                  <Row key={h.path} onClick={() => openIn(`/code?path=${encodeURIComponent(h.path)}`)}>
                    <Cell mono className="text-ink-2">{h.path}</Cell>
                    <Cell><RiskPill risk={h.risk} bare /></Cell>
                    <Cell className="tnum">{h.fanIn} {h.fanIn === 1 ? 'file' : 'files'}</Cell>
                    <Cell className="tnum">{h.complexity}</Cell>
                    <Cell className="tnum">{h.lines.toLocaleString()}</Cell>
                  </Row>
                ))}
              </DataTable>
            ))}
          </Panel>
        )}

        <SectionTitle>Tasks in {p.name}</SectionTitle>
        <Panel flush>
          {myTasks.length === 0 ? (
            <Empty title={`No tasks in ${p.name} yet`} hint="A requirement compiled for this project becomes a task here."
              action={can('plans:compile') ? <Button size="sm" variant="outline" onClick={() => openIn('/')}>Compile a requirement</Button> : undefined} />
          ) : (
            <DataTable head={['Ref', 'Task', 'Status', 'Priority', 'Risk', 'Layers', 'Agents', 'Progress']}>
              {myTasks.map((t) => (
                <Row key={t.id} onClick={() => openIn('/tasks')}>
                  <Cell mono>{t.ref}</Cell>
                  <Cell className="font-medium text-ink">{t.title}</Cell>
                  <Cell><span className="flex items-center gap-1.5"><Dot state={t.status} pulse={t.status === 'in_progress'} /><span className="capitalize">{t.status.replace('_', ' ')}</span></span></Cell>
                  <Cell><Tag tone={t.priority === 'URGENT' ? 'danger' : t.priority === 'HIGH' ? 'warn' : 'neutral'}>{t.priority}</Tag></Cell>
                  <Cell><RiskPill risk={t.risk} bare /></Cell>
                  <Cell className="text-[12.5px] text-dim">{t.layers.join(' · ')}</Cell>
                  <Cell className="text-[12.5px]">{t.agents.map((a) => agentName(catalogue, a)).join(', ')}</Cell>
                  <Cell><span className="flex items-center gap-2"><BlockBar pct={t.progress} width={10} /><span className="tnum text-[12px]">{t.progress}%</span></span></Cell>
                </Row>
              ))}
            </DataTable>
          )}
        </Panel>
      </PageBody>
    </Page>
  );
}
