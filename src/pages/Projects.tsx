import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Search, Plus, GitBranch, Database, FileCode, Boxes, Check, Brain, Lock, FolderGit2, FolderOpen, X, Layers } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, BlockBar, Segmented, Mono,
  SectionTitle, Empty, KV, Field, Wizard,
} from '@/components/os';
import { FolderPicker } from '@/components/workbench/FolderPicker';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { categoryLabel } from '@/lib/live/knowledge';
import { LABEL, SOURCE_DOT, labelFrom, sourcesApi, sourcesOf, type SourceInput } from '@/lib/live/sources';
import { cn } from '@/lib/utils';
import { ago } from '@/lib/time';
import type { MemoryFact, Project } from '@/types';
import { toast } from 'sonner';

type Sort = 'active' | 'understood' | 'size';

const KINDS: Project['kind'][] = ['legacy', 'greenfield', 'platform'];
const STATUSES: Project['status'][] = ['active', 'onboarding', 'paused', 'archived'];

/* Rules a person may record for a new project. Suggestions only: nothing is ticked until someone ticks
   it, and nothing enforces a recorded rule yet — the wizard says so. */
const SUGGESTED_RULES = [
  { id: 'solid', label: 'Strict SOLID', note: 'One reason to change per class' },
  { id: 'naming', label: 'Consistent class naming', note: '<Noun>Service / <Noun>Repository — no Helper or Util' },
  { id: 'repo', label: 'Repository pattern', note: 'No SQL text inside a service file' },
  { id: 'iface', label: 'Interface-first', note: 'The contract before the implementation' },
  { id: 'deps', label: 'New dependencies need approval', note: 'A person signs off on every new package' },
];

/* What onboarding really does, in order (server/app/services/onboarding.py). */
const STAGES = [
  { id: 'clone', label: 'Clone', detail: 'A shallow clone of the branch, onto this machine', gitOnly: true },
  { id: 'measure', label: 'Measure', detail: 'Files, lines and languages; tables and procedures declared in its SQL files' },
  { id: 'index', label: 'Index', detail: 'Files, symbols and the dependencies between them, for Code Intelligence and Architecture' },
  { id: 'retrieval', label: 'Retrieval', detail: 'Code, documents and memory split into pieces, embedded when a lane is configured' },
];

const DEFAULT_EXCLUDED = 'node_modules, bin, obj, dist, **/*.designer.cs';
const GIT_URL = /^(git@[\w.-]+:[\w.~/-]+?(\.git)?|(https?|ssh):\/\/[^\s/]+\/[\w.~/-]+?(\.git)?)\/?$/i;
const GIT_REF = /^(?!-)(?!.*\.\.)(?!.*\/$)[\w./-]+$/;

/** What is wrong with the repository step, or null when it is ready to continue. */
function repoProblem(source: 'git' | 'local', repo: string, branch: string): string | null {
  const r = repo.trim();
  if (!r) return 'Enter a repository to continue';
  if (source === 'local') return /^(\/|~\/)/.test(r) ? null : 'Use an absolute path — it starts with / or ~/';
  if (!GIT_URL.test(r)) return 'Not a clone URL — e.g. git@github.com:org/repo.git';
  return GIT_REF.test(branch.trim()) ? null : 'Branch name is empty or invalid';
}

/** What is wrong with a further source, or null when it can be added: the repository rules, and a label
    that is a folder name no other source of the new project has taken. */
function sourceProblem(draft: SourceInput, taken: string[]): string | null {
  const label = draft.label.trim();
  if (!label) return 'Give it a label — the folder its files appear under in the project';
  if (!LABEL.test(label)) return 'A label is lower-case letters, digits, dots, dashes or underscores';
  if (taken.includes(label)) return `Another source is already called ${label}`;
  return repoProblem(draft.kind, draft.repo, draft.branch);
}

const NO_SOURCE: SourceInput = { label: '', kind: 'git', repo: '', branch: 'main' };

export default function Projects() {
  const nav = useNavigate();
  const { can } = useAuth();
  const { projectId, setProjectId, all: projects } = useProject();
  const { createProject, memory } = useData();
  const [params, setParams] = useSearchParams();
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('all');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState<Sort>('active');
  const [newOpen, setNewOpen] = useState(false);
  const [repo, setRepo] = useState('');
  const [source, setSource] = useState<'git' | 'local'>('git');
  const [branch, setBranch] = useState('main');
  const [excluded, setExcluded] = useState(DEFAULT_EXCLUDED);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  // Further sources the new project holds beside its first one, and the one being written.
  const [extras, setExtras] = useState<SourceInput[]>([]);
  const [draft, setDraft] = useState<SourceInput>(NO_SOURCE);
  const [browsing, setBrowsing] = useState<'first' | 'extra' | null>(null);
  const browse = can('machine:access');

  // The project navigator's "New project" lands here with ?new=1: the wizard is open until it is closed,
  // and closing it tidies the address.
  const askedNew = params.get('new') === '1';
  const wizardOpen = newOpen || askedNew;
  const setWizard = (open: boolean) => {
    setNewOpen(open);
    if (!open && askedNew) {
      const next = new URLSearchParams(params);
      next.delete('new');
      setParams(next, { replace: true });
    }
  };

  const stages = STAGES.filter((st) => !st.gitOnly || source === 'git');
  const togglePick = (id: string) => setPicked((cur) => {
    const next = new Set(cur);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const resetDraft = () => {
    setRepo('');
    setSource('git');
    setBranch('main');
    setExcluded(DEFAULT_EXCLUDED);
    setPicked(new Set());
    setExtras([]);
    setDraft(NO_SOURCE);
  };
  const draftProblem = sourceProblem(draft, extras.map((x) => x.label.trim()));
  const addDraft = () => {
    if (draftProblem) return;
    setExtras((cur) => [...cur, { ...draft, label: draft.label.trim(), repo: draft.repo.trim(), branch: draft.branch.trim() }]);
    setDraft({ ...NO_SOURCE, kind: draft.kind });
  };
  /** A picked folder fills the path, and a label from its name when none was written. */
  const onFolder = (path: string) => {
    if (browsing === 'first') setRepo(path);
    else setDraft((d) => ({ ...d, repo: path, label: d.label || labelFrom(path) }));
    setBrowsing(null);
  };

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    const out = projects.filter((p) => {
      if (kind !== 'all' && p.kind !== kind) return false;
      if (status !== 'all' && p.status !== status) return false;
      if (!s) return true;
      return (p.name + p.codename + p.stack.join(' ') + p.description).toLowerCase().includes(s);
    });
    return [...out].sort((a, b) => {
      if (sort === 'understood') return (b.understoodPct ?? -1) - (a.understoodPct ?? -1);
      if (sort === 'size') return b.modules - a.modules;
      return (b.lastActive ?? '').localeCompare(a.lastActive ?? '');
    });
  }, [projects, q, kind, status, sort]);

  // What memory really holds: the workspace's own facts by category, and each project's by what they are.
  const live = useMemo(() => memory.filter((f) => !f.archived), [memory]);
  const brain = useMemo(() => {
    const by = new Map<MemoryFact['category'], MemoryFact[]>();
    live.filter((f) => f.projectId === null).forEach((f) => by.set(f.category, [...(by.get(f.category) ?? []), f]));
    return [...by.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [live]);
  const isolated = useMemo(() => {
    const by = new Map<string, MemoryFact[]>();
    live.forEach((f) => { if (f.projectId !== null) by.set(f.projectId, [...(by.get(f.projectId) ?? []), f]); });
    return [...by.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [live]);
  const count = (facts: MemoryFact[], category: MemoryFact['category']) => facts.filter((f) => f.category === category).length;

  return (
    <Page>
      <PageHeader
        title="Projects"
        subtitle="Every project keeps its own memory, rules and architecture graph. Context never leaks between them."
        actions={<Button size="sm" onClick={() => setNewOpen(true)}><Plus className="size-3.5" />New project</Button>}
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <div className="flex h-9 w-64 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
            <Search className="size-3.5 shrink-0 text-dim" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search projects, stacks, codenames…"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none"
            />
          </div>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] text-ink-2">
            {['all', ...KINDS].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'All kinds' : k}</option>)}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-lg border border-line-strong bg-surface-2 px-2.5 text-[13px] text-ink-2">
            {['all', ...STATUSES].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'All statuses' : k}</option>)}
          </select>
          <Segmented
            options={[{ id: 'active', label: 'Last active' }, { id: 'understood', label: 'Understanding' }, { id: 'size', label: 'Size' }]}
            value={sort}
            onChange={setSort}
          />
          <span className="ml-auto text-[12.5px] text-dim">{list.length} of {projects.length}</span>
        </div>
      </PageHeader>

      <PageBody className="space-y-5">
        {projects.length === 0 ? (
          <Empty icon={<FolderGit2 className="size-6" />} title="No projects yet"
            hint="Onboard a repository to start. NeuroCode reads it before it changes anything."
            action={<Button size="sm" variant="outline" onClick={() => setNewOpen(true)}>Onboard a repository</Button>} />
        ) : list.length === 0 ? (
          <Empty title="No project matches those filters" hint="Clear the search or widen the kind/status filter." />
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
            {list.map((p) => (
              <section key={p.id} className={cn('hover-lift flex flex-col rounded-md border bg-surface', p.id === projectId ? 'border-brand/50' : 'border-line')}>
                <div className="flex items-start justify-between gap-3 border-b border-line px-3.5 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Dot state={p.status} pulse={p.status === 'active'} />
                      <h2 className="truncate text-[14.5px] font-semibold text-ink">{p.name}</h2>
                      {p.id === projectId && <Tag tone="brand">active</Tag>}
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <Mono>{p.codename}</Mono>
                      <span className="eyebrow">{p.kind}</span>
                    </div>
                  </div>
                  <span className="shrink-0 text-right">
                    {p.understoodPct === null
                      ? <span className="block text-[12.5px] text-dim">{p.status === 'onboarding' ? 'reading…' : 'not indexed'}</span>
                      : <span className="tnum block text-[17px] leading-none font-semibold text-brand">{p.understoodPct}%</span>}
                    <span className="eyebrow">understood</span>
                  </span>
                </div>

                <div className="flex-1 px-3.5 py-3">
                  <p className="mb-2.5 line-clamp-2 text-[12.5px] text-soft">{p.description}</p>
                  {sourcesOf(p).length > 1 && (
                    <div className="mb-2.5 flex flex-wrap items-center gap-1.5" aria-label="Sources">
                      {sourcesOf(p).map((x) => (
                        <span key={x.label} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2 py-px font-mono text-[11.5px] text-ink-2"
                          title={x.status === 'active' ? undefined : x.status}>
                          <Dot state={SOURCE_DOT[x.status]} />{x.id === null ? 'first source' : `${x.label}/`}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="mb-3 flex flex-wrap gap-1">
                    {p.stack.map((s) => (
                      <span key={s} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[11.5px] text-ink-2">{s}</span>
                    ))}
                  </div>

                  <div className="grid grid-cols-4 gap-2 border-y border-line py-2.5">
                    {[
                      { icon: FileCode, v: p.lines, l: 'lines' },
                      { icon: Boxes, v: p.modules, l: 'modules' },
                      { icon: Database, v: p.dbTables, l: 'tables' },
                      { icon: GitBranch, v: p.storedProcs, l: 'sprocs' },
                    ].map(({ icon: Icon, v, l }) => (
                      <div key={l}>
                        <div className="flex items-center gap-1 text-dim"><Icon className="size-3" /><span className="eyebrow">{l}</span></div>
                        <div className="tnum mt-0.5 text-[14px] font-medium text-ink">{v}</div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-2.5 space-y-1.5">
                    {p.coverage.slice(0, 3).map((c) => (
                      <div key={c.label} className="flex items-center gap-2">
                        <span className="w-32 shrink-0 truncate text-[12px] text-soft">{c.label}</span>
                        <BlockBar pct={c.pct} width={12} />
                        <span className="tnum w-7 text-right text-[11.5px] text-dim">{c.pct}%</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="flex items-center justify-between gap-2 border-t border-line px-3.5 py-2.5">
                  <div className="flex items-center gap-2.5 text-[12px]">
                    <span className="text-ok">{p.work.running} running</span>
                    <span className="text-warn">{p.work.review} review</span>
                    {p.work.blocked > 0 && <span className="text-danger">{p.work.blocked} blocked</span>}
                    <span className="text-dim">· active {ago(p.lastActive)}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {p.id !== projectId && (
                      <Button size="xs" variant="ghost" onClick={() => { setProjectId(p.id); toast.success(`Switched to ${p.name}`, { description: 'The screens now read this project.' }); }}>
                        Switch
                      </Button>
                    )}
                    <Button size="xs" variant="outline" onClick={() => nav(`/projects/${p.id}`)}>Open</Button>
                  </div>
                </div>
              </section>
            ))}
          </div>
        )}

        {/* Global brain vs isolated memory */}
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          <Panel eyebrow="Shared across every project" title={<span className="flex items-center gap-1.5"><Brain className="size-3.5 text-brand" />Global AI Brain</span>} className="xl:col-span-2" flush>
            {brain.length === 0 ? (
              <Empty title="Nothing in the global brain yet" hint="Facts saved as Global in Memory are shared by every project."
                action={<Button size="sm" variant="outline" onClick={() => nav('/memory')}>Open Memory</Button>} />
            ) : (
              <div className="divide-y divide-line">
                {brain.map(([category, facts]) => (
                  <div key={category} className="px-3.5 py-2.5">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-[13.5px] font-medium text-ink">{categoryLabel(category)}</span>
                      <span className="tnum text-[13px] text-brand">{facts.length.toLocaleString()}</span>
                    </div>
                    <ul className="mt-1.5 space-y-0.5">
                      {facts.slice(0, 3).map((f) => (
                        <li key={f.id}>
                          <button onClick={() => nav(`/memory?ref=${encodeURIComponent(f.ref)}`)}
                            className="flex items-start gap-1.5 text-left text-[12px] text-soft hover:text-ink">
                            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-line-strong" />{f.title}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel eyebrow="Never leaves its project" title={<span className="flex items-center gap-1.5"><Lock className="size-3.5 text-warn" />Isolated memory</span>} flush>
            {isolated.length === 0 ? (
              <Empty title="No project memory yet" hint="Facts remembered for one project are listed here, by project." />
            ) : (
              <div className="divide-y divide-line">
                {isolated.map(([pid, facts]) => (
                  <div key={pid} className="px-3.5 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[13px] font-medium text-ink">{projects.find((x) => x.id === pid)?.name ?? pid}</span>
                      <span className="tnum text-[12.5px] text-soft">{facts.length.toLocaleString()} {facts.length === 1 ? 'fact' : 'facts'}</span>
                    </div>
                    <div className="mt-1 flex gap-3 text-[11.5px] text-dim">
                      <span>{count(facts, 'business_rules')} business rules</span>
                      <span>{count(facts, 'decisions')} decisions</span>
                      <span>{count(facts, 'legacy')} legacy</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </PageBody>

      {/* Onboarding wizard */}
      <Wizard
        open={wizardOpen}
        onOpenChange={setWizard}
        title="Onboard a repository"
        description="NeuroCode reads the codebase before it is allowed to change anything: it measures it, indexes its code and builds retrieval over its code and documents."
        finishLabel="Start onboarding"
        busy={busy}
        onFinish={async () => {
          setBusy(true);
          const doc = await createProject({
            source, repo: repo.trim(), branch: branch.trim(),
            excluded: excluded.split(',').map((s) => s.trim()).filter(Boolean),
            rules: SUGGESTED_RULES.filter((r) => picked.has(r.id)),
          });
          if (!doc) {
            setBusy(false);
            return;
          }
          // Each further source is added once the project exists; one the API refuses is said, and the
          // rest still go in — they can be added again from the project's Sources section.
          let added = 0;
          for (const extra of extras) {
            try {
              await sourcesApi.add(doc.id, extra);
              added += 1;
            } catch (e) {
              toast.error(`${extra.label} was not added`, { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
            }
          }
          setBusy(false);
          setWizard(false);
          toast.success(`${doc.name} is onboarding`, {
            description: `${source === 'git' ? 'Cloning and measuring' : 'Measuring'} it now${added ? `, with ${added} more ${added === 1 ? 'source' : 'sources'}` : ''}. Each stage lands in Activity.`,
          });
          resetDraft();
        }}
        steps={[
          {
            id: 'repo', title: 'Repository', hint: 'where the code lives',
            valid: repoProblem(source, repo, branch) === null, blocker: repoProblem(source, repo, branch) ?? undefined,
            content: (
              <div className="space-y-3">
                <Segmented options={[{ id: 'git', label: 'Git remote' }, { id: 'local', label: 'Local path' }]} value={source} onChange={setSource} />
                <div className="flex items-end gap-2">
                  <Field
                    className="min-w-0 flex-1"
                    label={source === 'git' ? 'Clone URL' : 'Absolute path'}
                    value={repo} onChange={setRepo} mono
                    placeholder={source === 'git' ? 'git@github.com:org/repo.git' : '/Users/you/code/repo'}
                  />
                  {source === 'local' && browse && (
                    <Button type="button" variant="outline" size="sm" className="mb-px h-9" onClick={() => setBrowsing('first')}>
                      <FolderOpen className="size-3.5" />Browse…
                    </Button>
                  )}
                </div>
                {source === 'git' && <Field label="Branch" value={branch} onChange={setBranch} mono />}
                <div className="rounded-sm border border-line bg-base px-3 py-1.5">
                  <KV k="Access" v={source === 'git' ? 'cloned with your git credentials, read only' : 'read in place'} />
                  <KV k="Changes" v="none until you dispatch a plan" />
                </div>
              </div>
            ),
          },
          {
            id: 'scope', title: 'Scope', hint: 'what gets read',
            content: (
              <div className="space-y-3">
                <Field label="Excluded paths — never parsed, embedded or shown to a model" value={excluded} onChange={setExcluded} mono />
                <div className="rounded-sm border border-line bg-base px-3 py-2 text-[12.5px] leading-relaxed text-soft">
                  Everything else in the repository is read: its source files, the tables and procedures its SQL files
                  declare, its git history when it has one, and its README, docs and notes. Languages are measured once
                  the files are read.
                </div>
              </div>
            ),
          },
          {
            id: 'rules', title: 'Access & rules', hint: 'what it may do',
            content: (
              <div className="space-y-3">
                <p className="text-[13px] leading-relaxed text-soft">
                  Tick the ones this project should follow, and they are recorded as its rules. They are recorded, not
                  yet enforced: nothing checks a change against them today.
                </p>
                <div className="divide-y divide-line rounded-sm border border-line">
                  {SUGGESTED_RULES.map((r) => {
                    const on = picked.has(r.id);
                    return (
                      <button key={r.id} type="button" onClick={() => togglePick(r.id)} aria-pressed={on}
                        className="flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-2">
                        <span className={cn('mt-0.5 grid size-4 shrink-0 place-items-center rounded-xs border transition-colors',
                          on ? 'border-brand bg-brand text-brand-ink' : 'border-line-strong bg-surface')}>
                          {on && <Check className="size-3" strokeWidth={2.6} />}
                        </span>
                        <span className="min-w-0">
                          <span className="block text-[13.5px] font-medium text-ink">{r.label}</span>
                          <span className="block text-[12px] text-dim">{r.note}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className="flex items-start gap-2 rounded-sm border border-line bg-base px-3 py-2">
                  <Lock className="mt-px size-3.5 shrink-0 text-warn" />
                  <p className="text-[12.5px] text-soft">
                    Facts remembered for this project stay with it. A fact saved as Global in Memory is shared by every
                    project.
                  </p>
                </div>
              </div>
            ),
          },
          {
            id: 'sources', title: 'More sources', hint: extras.length ? `${extras.length} added` : 'optional',
            content: (
              <div className="space-y-3">
                <p className="text-[13px] leading-relaxed text-soft">
                  Add another folder or repository that belongs to this project — the API beside the web app, a shared
                  library, a data repository. Each is onboarded the same way, and its files appear in the project under
                  its label, so search, impact and runs span all of them.
                </p>
                {extras.length > 0 && (
                  <div className="divide-y divide-line/60 rounded-lg border border-line">
                    {extras.map((x) => (
                      <div key={x.label} className="flex items-center gap-2.5 px-3 py-2">
                        <Layers className="size-3.5 shrink-0 text-dim" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-mono text-[12.5px] text-ink">{x.label}/</span>
                          <span className="block truncate font-mono text-[11.5px] text-dim">{x.repo}{x.kind === 'git' ? ` @ ${x.branch}` : ''}</span>
                        </span>
                        <Tag>{x.kind === 'git' ? 'git' : 'folder'}</Tag>
                        <button type="button" aria-label={`Remove ${x.label}`} onClick={() => setExtras((cur) => cur.filter((y) => y.label !== x.label))}
                          className="grid size-7 place-items-center rounded-md text-dim transition-colors hover:bg-surface-2 hover:text-ink">
                          <X className="size-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="space-y-2.5 rounded-lg border border-line bg-base px-3 py-3">
                  <Segmented options={[{ id: 'git', label: 'Git remote' }, { id: 'local', label: 'Local path' }]}
                    value={draft.kind} onChange={(kind) => setDraft((d) => ({ ...d, kind }))} />
                  <div className="flex items-end gap-2">
                    <Field className="min-w-0 flex-1" mono value={draft.repo}
                      label={draft.kind === 'git' ? 'Clone URL' : 'Absolute path'}
                      placeholder={draft.kind === 'git' ? 'git@github.com:org/api.git' : '/Users/you/code/api'}
                      onChange={(v) => setDraft((d) => ({ ...d, repo: v, label: d.label && d.label !== labelFrom(d.repo) ? d.label : labelFrom(v) }))} />
                    {draft.kind === 'local' && browse && (
                      <Button type="button" variant="outline" size="sm" className="mb-px h-9" onClick={() => setBrowsing('extra')}>
                        <FolderOpen className="size-3.5" />Browse…
                      </Button>
                    )}
                  </div>
                  <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                    <Field label="Label" mono value={draft.label} placeholder="api" onChange={(v) => setDraft((d) => ({ ...d, label: v }))}
                      hint="The folder its files appear under in the project" />
                    {draft.kind === 'git' && <Field label="Branch" mono value={draft.branch} onChange={(v) => setDraft((d) => ({ ...d, branch: v }))} />}
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[12px] text-dim">{draft.repo.trim() || draft.label.trim() ? draftProblem ?? 'Ready to add' : 'Optional — skip this step to onboard one source'}</span>
                    <Button type="button" size="sm" variant="outline" disabled={!!draftProblem} onClick={addDraft}>
                      <Plus className="size-3.5" />Add another folder or repository
                    </Button>
                  </div>
                </div>
              </div>
            ),
          },
          {
            id: 'review', title: 'Review', hint: `${stages.length} stages`,
            content: (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-x-6 rounded-sm border border-line bg-base px-3 py-1.5 sm:grid-cols-2">
                  <KV k="Source" v={repo.trim() || 'not entered'} mono />
                  <KV k="Branch" v={source === 'git' ? branch : 'working tree'} mono />
                  <KV k="Excluded" v={`${excluded.split(',').map((x) => x.trim()).filter(Boolean).length} patterns`} />
                  <KV k="Rules recorded" v={`${picked.size} of ${SUGGESTED_RULES.length}`} />
                  <KV k="More sources" v={extras.length ? extras.map((x) => x.label).join(', ') : 'none'} mono={extras.length > 0} />
                </div>
                <SectionTitle>What onboarding does</SectionTitle>
                <div className="rounded-sm border border-line bg-base">
                  {stages.map((st, n) => (
                    <div key={st.id} className="flex items-center gap-2.5 border-b border-line/60 px-3 py-1.5 last:border-0">
                      <span className="tnum w-5 shrink-0 text-right font-mono text-[11.5px] text-dim">{n + 1}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-ink-2">{st.label}</span>
                        <span className="block truncate text-[11.5px] text-dim">{st.detail}</span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ),
          },
        ]}
      />
      <FolderPicker
        open={browsing !== null}
        title={browsing === 'first' ? 'Choose the project folder' : 'Choose a folder to add'}
        onPick={onFolder}
        onClose={() => setBrowsing(null)}
      />
    </Page>
  );
}
