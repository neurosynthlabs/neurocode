import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Search, Plus, GitBranch, Database, FileCode, Boxes, Check, Brain, Lock, FolderGit2, FolderOpen, X, Layers, FileArchive,
  FolderPlus, Upload, Loader2, BookOpen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, BlockBar, Segmented, Mono,
  Empty, KV, Field, Wizard, More, About,
} from '@/components/os';
import { RolePick } from '@/components/projects/SourcesPanel';
import { FolderPicker } from '@/components/workbench/FolderPicker';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { categoryLabel } from '@/lib/live/knowledge';
import { ARCHIVES, bytes, joinPath, machineApi, type NewProjectOptions } from '@/lib/live/machine';
import { LABEL, SOURCE_DOT, labelFrom, readOnly, referencesApi, sourcesApi, sourcesOf, type SourceInput } from '@/lib/live/sources';
import { useRemote } from '@/lib/remote';
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

/* What unpacking an archive, and starting an empty project, add in front of the stages above. */
const ARCHIVE_STAGES = [
  { id: 'check', label: 'Check every entry', detail: 'Nothing leaves the folder, no links, at most 50,000 entries and 2 GB — or nothing is written', gitOnly: false },
  { id: 'unpack', label: 'Unpack', detail: 'Into a new folder, never an existing one; a single top folder is dropped', gitOnly: false },
];
const EMPTY_STAGES = [
  { id: 'create', label: 'Create', detail: 'A new folder, git init on main, and a README committed once', gitOnly: false },
];

/** The four ways a project begins. Importing and starting empty write on this machine, so they need machine:access. */
type Door = 'folder' | 'archive' | 'clone' | 'empty';
const DOORS: { id: Door; title: string; hint: string; icon: typeof FolderOpen; machine: boolean }[] = [
  { id: 'folder', title: 'Open a folder on this machine', hint: 'Read where it is. Nothing is copied.', icon: FolderOpen, machine: false },
  { id: 'archive', title: 'Import an archive', hint: 'A .zip, .tar.gz or .tgz, unpacked into a new folder', icon: FileArchive, machine: true },
  { id: 'clone', title: 'Clone a repository', hint: 'A shallow clone of one branch', icon: GitBranch, machine: false },
  { id: 'empty', title: 'Start an empty project', hint: 'A new folder with git and a README', icon: FolderPlus, machine: true },
];

/** What is wrong with a new folder's name, or null: one plain name that is not hidden. */
function nameProblem(name: string): string | null {
  const n = name.trim();
  if (!n) return 'Name its folder';
  if (n === '.' || n === '..' || /[/\\]/.test(n)) return 'Name its folder with one plain name, not a path';
  if (n.startsWith('.')) return 'A name starting with a dot would be a hidden folder';
  return null;
}

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');

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
  if (!label) return 'Give it a label: the folder its files appear under';
  if (!LABEL.test(label)) return 'A label is lower-case letters, digits, dots, dashes or underscores';
  if (taken.includes(label)) return `Another source is already called ${label}`;
  return repoProblem(draft.kind, draft.repo, draft.branch);
}

const NO_SOURCE: SourceInput = { label: '', kind: 'git', repo: '', branch: 'main', role: 'code' };

export default function Projects() {
  const nav = useNavigate();
  const { can, machine } = useAuth();
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
  const [door, setDoor] = useState<Door>('folder');
  const source: 'git' | 'local' = door === 'clone' ? 'git' : 'local';
  const [branch, setBranch] = useState('main');
  // An archive: one on this machine (surveyed before anything is written) or one this browser uploads.
  const [archiveFrom, setArchiveFrom] = useState<'machine' | 'upload'>('machine');
  const [archivePath, setArchivePath] = useState('');
  const [upload, setUpload] = useState<File | null>(null);
  const [into, setInto] = useState('');
  const [folderName, setFolderName] = useState('');
  const [progress, setProgress] = useState<number | null>(null);
  const [readsFrom, setReadsFrom] = useState<Set<string>>(() => new Set());
  const [excluded, setExcluded] = useState(DEFAULT_EXCLUDED);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  // Further sources the new project holds beside its first one, and the one being written.
  const [extras, setExtras] = useState<SourceInput[]>([]);
  const [draft, setDraft] = useState<SourceInput>(NO_SOURCE);
  const [browsing, setBrowsing] = useState<'first' | 'extra' | 'archive' | 'into' | null>(null);
  const browse = machine;
  // A door that needs this machine is closed for one of two reasons, and they are not the same thing.
  const offReason = can('machine:access')
    ? 'This server opens no folders (NEUROCODE_MACHINE_ACCESS=false)'
    : 'Needs the “Use this machine” permission';

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

  const onDisk = STAGES.filter((st) => !st.gitOnly || source === 'git');
  const stages = door === 'archive' ? [...ARCHIVE_STAGES, ...onDisk] : door === 'empty' ? [...EMPTY_STAGES, ...onDisk] : onDisk;
  const makes = door === 'archive' || door === 'empty';

  // An archive on this machine is read before anything is written: its entries, every one checked, and what
  // it unpacks to. A refusal (a path out of the folder, a link, a bomb) is said here, at the first step.
  const surveyPath = door === 'archive' && archiveFrom === 'machine' && ARCHIVES.some((a) => archivePath.trim().toLowerCase().endsWith(a))
    ? archivePath.trim() : null;
  const survey = useRemote(surveyPath ? `survey:${surveyPath}` : null, () => machineApi.archive(surveyPath ?? ''));
  // The folder name follows the archive until a person writes their own.
  const [namedFor, setNamedFor] = useState<string | null>(null);
  const suggested = door === 'archive'
    ? (archiveFrom === 'machine' ? survey.data?.suggestedName : upload?.name.replace(/\.(zip|tar\.gz|tgz)$/i, '')) ?? null : null;
  if (suggested && namedFor !== suggested && (!folderName || folderName === namedFor)) {
    setNamedFor(suggested);
    setFolderName(suggested);
  }
  const target = into.trim() && folderName.trim() ? joinPath(into.trim(), folderName.trim()) : null;

  const startProblem = (): string | null => {
    if (door === 'clone' || door === 'folder') return repoProblem(source, repo, branch);
    if (!browse) return 'This needs the “Use this machine” permission, which the Owner role holds';
    if (door === 'archive') {
      if (archiveFrom === 'machine') {
        if (!archivePath.trim()) return 'Choose an archive';
        if (!surveyPath) return 'Choose a .zip, .tar.gz or .tgz file';
        if (survey.error) return survey.error;
        if (!survey.data) return 'Reading the archive…';
      } else if (!upload) return 'Choose a file to upload';
      else if (!ARCHIVES.some((a) => upload.name.toLowerCase().endsWith(a))) return `${upload.name} is not a .zip, .tar.gz or .tgz file`;
    }
    if (!/^(\/|~\/)/.test(into.trim())) return 'Choose the folder to make it in';
    return nameProblem(folderName);
  };
  const problem = startProblem();
  const togglePick = (id: string) => setPicked((cur) => {
    const next = new Set(cur);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const resetDraft = () => {
    setRepo('');
    setDoor('folder');
    setBranch('main');
    setArchivePath('');
    setUpload(null);
    setFolderName('');
    setNamedFor(null);
    setReadsFrom(new Set());
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
    else if (browsing === 'archive') setArchivePath(path);
    else if (browsing === 'into') setInto(path);
    else setDraft((d) => ({ ...d, repo: path, label: d.label || labelFrom(path) }));
    setBrowsing(null);
  };
  const toggleRead = (id: string) => setReadsFrom((cur) => {
    const next = new Set(cur);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  /** Make the project through the door chosen. Null when it was refused — the toast says why. */
  const begin = async (): Promise<Project | null> => {
    const options: NewProjectOptions = {
      excluded: excluded.split(',').map((x) => x.trim()).filter(Boolean),
      rules: SUGGESTED_RULES.filter((r) => picked.has(r.id)),
    };
    if (door === 'clone' || door === 'folder') {
      return createProject({ source, repo: repo.trim(), branch: branch.trim(), ...options });
    }
    try {
      if (door === 'empty') return (await machineApi.emptyProject(into.trim(), folderName.trim(), options)).project;
      if (archiveFrom === 'upload' && upload) {
        setProgress(0);
        return (await machineApi.uploadArchive(upload, into.trim(), folderName.trim(), options, setProgress)).project;
      }
      return (await machineApi.importArchive(archivePath.trim(), into.trim(), folderName.trim(), options)).project;
    } catch (e) {
      toast.error(door === 'empty' ? 'No project was made' : 'Nothing was imported', { description: reason(e) });
      return null;
    } finally {
      setProgress(null);
    }
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
        subtitle="Each project keeps its own memory, rules and graph."
        actions={<Button size="sm" onClick={() => setNewOpen(true)}><Plus className="size-3.5" />New project</Button>}
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <div className="flex h-9 w-64 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
            <Search className="size-3.5 shrink-0 text-dim" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search projects…"
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
            hint="Open a folder, import an archive, clone a repository, or start empty."
            action={<Button size="sm" variant="outline" onClick={() => setNewOpen(true)}>New project</Button>} />
        ) : list.length === 0 ? (
          <Empty title="No project matches those filters" hint="Clear the search or filters." />
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
                      <span className="text-[11.5px] text-dim">{p.kind}</span>
                    </div>
                  </div>
                  <span className="shrink-0 text-right">
                    {p.understoodPct === null
                      ? <span className="block text-[12.5px] text-dim">{p.status === 'onboarding' ? 'reading…' : 'not indexed'}</span>
                      : <span className="tnum block text-[17px] leading-none font-semibold text-brand">{p.understoodPct}%</span>}
                    <span className="block text-[11px] text-dim">understood</span>
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
                        <div className="flex items-center gap-1 text-dim"><Icon className="size-3" /><span className="text-[11px]">{l}</span></div>
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
        {projects.length > 0 && <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          <Panel eyebrow="Shared across every project" title={<span className="flex items-center gap-1.5"><Brain className="size-3.5 text-brand" />Global AI Brain</span>} className="xl:col-span-2" flush>
            {brain.length === 0 ? (
              <Empty title="Nothing in the global brain yet"
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
              <Empty title="No project memory yet" />
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
        </div>}
      </PageBody>

      {/* Onboarding wizard */}
      <Wizard
        open={wizardOpen}
        onOpenChange={setWizard}
        title="New project"
        description="Nothing changes until the code is read, measured and indexed."
        finishLabel={door === 'archive' ? 'Import and onboard' : door === 'empty' ? 'Create and onboard' : 'Start onboarding'}
        busy={busy}
        onFinish={async () => {
          setBusy(true);
          const doc = await begin();
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
          for (const id of readsFrom) {
            try {
              await referencesApi.add(doc.id, id, '');
            } catch (e) {
              toast.error(`${projects.find((x) => x.id === id)?.name ?? id} was not referenced`, { description: reason(e) });
            }
          }
          setBusy(false);
          setWizard(false);
          const doing = door === 'clone' ? 'Cloning and measuring' : door === 'archive' ? 'Unpacking and measuring' : 'Measuring';
          toast.success(`${doc.name} is onboarding`, {
            description: `${doing} it now${added ? `, with ${added} more ${added === 1 ? 'source' : 'sources'}` : ''}. Each stage lands in Activity.`,
          });
          resetDraft();
        }}
        steps={[
          {
            id: 'repo', title: 'Start', hint: DOORS.find((d) => d.id === door)?.title.split(' ')[0] ?? 'where it comes from',
            valid: problem === null, blocker: problem ?? undefined,
            content: (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" role="group" aria-label="How the project begins">
                  {DOORS.map((d) => {
                    const off = d.machine && !browse;
                    const Icon = d.icon;
                    return (
                      <button key={d.id} type="button" aria-pressed={door === d.id} disabled={off} onClick={() => setDoor(d.id)}
                        className={cn('flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors',
                          door === d.id ? 'border-brand/60 bg-brand/8' : 'border-line hover:bg-surface-2', off && 'cursor-not-allowed opacity-50')}>
                        <Icon className={cn('mt-0.5 size-4 shrink-0', door === d.id ? 'text-brand' : 'text-dim')} />
                        <span className="min-w-0">
                          <span className="block text-[13.5px] font-medium text-ink">{d.title}</span>
                          <span className="block text-[12px] text-dim">{off ? offReason : d.hint}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>

                {(door === 'clone' || door === 'folder') && (
                  <>
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
                  </>
                )}

                {door === 'archive' && (
                  <div className="space-y-2.5">
                    <Segmented options={[{ id: 'machine', label: 'On this machine' }, { id: 'upload', label: 'Upload from this browser' }]}
                      value={archiveFrom} onChange={setArchiveFrom} />
                    {archiveFrom === 'machine' ? (
                      <div className="flex items-end gap-2">
                        <Field className="min-w-0 flex-1" label="Archive" value={archivePath} onChange={setArchivePath} mono
                          placeholder="/Users/you/Desktop/shop-main.zip" />
                        <Button type="button" variant="outline" size="sm" className="mb-px h-9" onClick={() => setBrowsing('archive')}>
                          <FileArchive className="size-3.5" />Choose…
                        </Button>
                      </div>
                    ) : (
                      <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-line-strong px-3 py-3 hover:bg-surface-2">
                        <Upload className="size-4 shrink-0 text-brand" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] text-ink">{upload ? upload.name : 'Choose a .zip, .tar.gz or .tgz file'}</span>
                          <span className="block text-[12px] text-dim">{upload ? bytes(upload.size) : 'Checked and unpacked; the upload is then deleted.'}</span>
                        </span>
                        <input type="file" accept={ARCHIVES.join(',')} className="sr-only"
                          onChange={(e) => setUpload(e.target.files?.[0] ?? null)} />
                      </label>
                    )}
                    {archiveFrom === 'machine' && surveyPath && (
                      survey.error ? (
                        <p className="rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-[12.5px] text-danger [overflow-wrap:anywhere]">{survey.error}</p>
                      ) : !survey.data ? (
                        <p className="flex items-center gap-2 text-[12.5px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the archive…</p>
                      ) : (
                        <div className="rounded-lg border border-line bg-base px-3 py-2 text-[12.5px]">
                          <p className="text-ink-2">
                            {survey.data.name} · {bytes(survey.data.bytes)} → {survey.data.files.toLocaleString()} {survey.data.files === 1 ? 'file' : 'files'}, {bytes(survey.data.total)} unpacked
                          </p>
                          {survey.data.top && <p className="text-dim">Its top folder <span className="font-mono">{survey.data.top}/</span> is dropped; files sit at the project root.</p>}
                          {survey.data.sample.length > 0 && (
                            <p className="mt-1 truncate font-mono text-[11.5px] text-dim" title={survey.data.sample.join('\n')}>{survey.data.sample.slice(0, 6).join('  ')}</p>
                          )}
                        </div>
                      )
                    )}
                  </div>
                )}

                {makes && (
                  <div className="space-y-2.5">
                    <div className="flex items-end gap-2">
                      <Field className="min-w-0 flex-1" label={door === 'archive' ? 'Unpack into' : 'Make it in'} value={into} onChange={setInto} mono
                        placeholder="/Users/you/code" />
                      {browse && (
                        <Button type="button" variant="outline" size="sm" className="mb-px h-9" onClick={() => setBrowsing('into')}>
                          <FolderOpen className="size-3.5" />Browse…
                        </Button>
                      )}
                    </div>
                    <Field label="Folder name" value={folderName} onChange={setFolderName} mono placeholder="shop"
                      hint={target ? `${target} — refused if it exists, never merged` : 'Refused if it exists, never merged.'} />
                  </div>
                )}

                <div className="rounded-sm border border-line bg-base px-3 py-1.5">
                  {door === 'clone' && <KV k="Access" v="cloned with your git credentials, read only" />}
                  {door === 'folder' && <KV k="Access" v="read in place, nothing copied" />}
                  {door === 'archive' && <KV k="Writes" v={`only ${target ?? 'the new folder'}, after every entry is checked`} />}
                  {door === 'empty' && <KV k="Writes" v={`${target ?? 'the new folder'}: git init, a README, one commit`} />}
                  <KV k="Changes" v="none to its code until you dispatch a plan" />
                </div>
              </div>
            ),
          },
          {
            id: 'scope', title: 'Scope', hint: 'what gets read',
            content: (
              <div className="space-y-3">
                <Field label="Excluded paths" value={excluded} onChange={setExcluded} mono
                  hint="Never parsed, embedded or shown to a model." />
                <div className="rounded-sm border border-line bg-base px-3 py-2 text-[12.5px] leading-relaxed text-soft">
                  Everything else is read: code, SQL, git history and docs.
                </div>
              </div>
            ),
          },
          {
            id: 'rules', title: 'Access & rules', hint: 'what it may do',
            content: (
              <div className="space-y-3">
                <p className="text-[13px] leading-relaxed text-soft">
                  Recorded, not yet enforced: nothing checks a change against them.
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
                  <p className="text-[12.5px] text-soft">This project's facts stay with it. Global facts are shared by all.</p>
                </div>
              </div>
            ),
          },
          {
            id: 'sources', title: 'More sources', hint: extras.length || readsFrom.size ? `${extras.length + readsFrom.size} added` : 'optional',
            content: (
              <div className="space-y-3">
                <p className="flex items-center gap-1 text-[13px] leading-relaxed text-soft">
                  Another folder or repository, such as an API or shared library.
                  <About>Each is onboarded the same way. Its files appear under its label, so search, impact and runs span all of them.</About>
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
                        {readOnly(x) && <Tag tone="violet">reference</Tag>}
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
                  <div className="flex flex-wrap items-center gap-2">
                    <Segmented options={[{ id: 'git', label: 'Git remote' }, { id: 'local', label: 'Local path' }]}
                      value={draft.kind} onChange={(kind) => setDraft((d) => ({ ...d, kind }))} />
                    <RolePick value={draft.role ?? 'code'} onChange={(role) => setDraft((d) => ({ ...d, role }))} label="What this source is" />
                  </div>
                  {draft.role === 'reference' && (
                    <p className="text-[12px] text-dim">Read for grounding, never written by an agent.</p>
                  )}
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
                      hint="The folder its files appear under" />
                    {draft.kind === 'git' && <Field label="Branch" mono value={draft.branch} onChange={(v) => setDraft((d) => ({ ...d, branch: v }))} />}
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[12px] text-dim">{draft.repo.trim() || draft.label.trim() ? draftProblem ?? 'Ready to add' : 'Optional: skip to onboard one source'}</span>
                    <Button type="button" size="sm" variant="outline" disabled={!!draftProblem} onClick={addDraft}>
                      <Plus className="size-3.5" />{draft.role === 'reference' ? 'Add a reference' : 'Add source'}
                    </Button>
                  </div>
                </div>
                {projects.length > 0 && (
                  <div className="space-y-2">
                    <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-soft"><BookOpen className="size-3.5" />Reads from other projects</p>
                    <p className="text-[12px] text-dim">Searched as references, never written.</p>
                    <div className="flex flex-wrap gap-1.5">
                      {projects.map((x) => {
                        const on = readsFrom.has(x.id);
                        return (
                          <button key={x.id} type="button" aria-pressed={on} onClick={() => toggleRead(x.id)}
                            className={cn('inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12.5px] transition-colors',
                              on ? 'border-brand/60 bg-brand/10 text-ink' : 'border-line text-soft hover:bg-surface-2')}>
                            {on ? <Check className="size-3 text-brand" /> : <Dot state={x.status} />}{x.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            ),
          },
          {
            id: 'review', title: 'Review', hint: `${stages.length} stages`,
            content: (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-x-6 rounded-sm border border-line bg-base px-3 py-1.5 sm:grid-cols-2">
                  <KV k="From" v={DOORS.find((d) => d.id === door)?.title ?? ''} />
                  {door === 'clone' || door === 'folder' ? (
                    <>
                      <KV k="Source" v={repo.trim() || 'not entered'} mono />
                      <KV k="Branch" v={source === 'git' ? branch : 'working tree'} mono />
                    </>
                  ) : (
                    <>
                      {door === 'archive' && <KV k="Archive" v={archiveFrom === 'upload' ? upload?.name ?? 'not chosen' : archivePath.trim() || 'not chosen'} mono />}
                      <KV k="New folder" v={target ?? 'not chosen'} mono />
                    </>
                  )}
                  <KV k="Excluded" v={`${excluded.split(',').map((x) => x.trim()).filter(Boolean).length} patterns`} />
                  <KV k="Rules recorded" v={`${picked.size} of ${SUGGESTED_RULES.length}`} />
                  <KV k="More sources" v={extras.length ? extras.map((x) => (readOnly(x) ? `${x.label} (reference)` : x.label)).join(', ') : 'none'} mono={extras.length > 0} />
                  <KV k="Reads from" v={readsFrom.size ? projects.filter((x) => readsFrom.has(x.id)).map((x) => x.name).join(', ') : 'no other project'} />
                </div>
                {progress !== null && (
                  <p className="flex items-center gap-2 text-[12.5px] text-soft"><Loader2 className="size-3.5 animate-spin" />Uploading the archive · {Math.round(progress * 100)}%</p>
                )}
                <More label="What onboarding does">
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
                </More>
              </div>
            ),
          },
        ]}
      />
      <FolderPicker
        open={browsing !== null}
        mode={browsing === 'archive' ? 'file' : 'folder'}
        accept={ARCHIVES}
        purpose={browsing === 'archive' ? 'import-archive' : browsing === 'into' ? 'project-parent' : 'project-folder'}
        title={browsing === 'first' ? 'Choose the project folder' : browsing === 'archive' ? 'Choose an archive to import'
          : browsing === 'into' ? (door === 'archive' ? 'Choose where to unpack it' : 'Choose where to make it') : 'Choose a folder to add'}
        confirmLabel={browsing === 'into' ? 'Make it here' : undefined}
        onPick={onFolder}
        onClose={() => setBrowsing(null)}
      />
    </Page>
  );
}
