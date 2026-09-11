import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Plus, GitBranch, Database, FileCode, Boxes, Check, Brain, Lock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, BlockBar, Segmented, Mono,
  SectionTitle, Empty, KV, Field, Wizard,
} from '@/components/os';
import { projects } from '@/mock/projects';
import { onboardingSteps, globalBrain, isolatedMemory } from '@/mock/modules';
import { useProject } from '@/lib/project-context';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

type Sort = 'active' | 'understood' | 'size';

/* Rules a new project starts life with — each is enforced by a reviewer check or a hook. */
const SEED_RULES = [
  { id: 'solid', label: 'Strict SOLID', note: 'One reason to change per class — a blocker at review' },
  { id: 'naming', label: 'Class-object naming', note: '<Noun>Service / I<Noun>Service / <Noun>Repository — no Helper or Util' },
  { id: 'repo', label: 'Repository pattern', note: 'No SQL text inside a service file' },
  { id: 'trans', label: 'Protected tables are append-only', note: 'Direct writes to TRANS_* are refused by a PreToolUse hook' },
  { id: 'iface', label: 'Interface-first', note: 'Contract and registration before the implementation' },
  { id: 'deps', label: 'New dependencies need your approval', note: 'Supply chain is your signature, not the agent’s' },
];
const DB_STEPS = [5, 6, 7, 8];
const DEFAULT_EXCLUDED = 'node_modules, bin, obj, dist, **/*.designer.cs';
const estSeconds = (e: string) => (e.endsWith('m') ? parseFloat(e) * 60 : parseFloat(e));
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

export default function Projects() {
  const nav = useNavigate();
  const { projectId, setProjectId } = useProject();
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('all');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState<Sort>('active');
  const [newOpen, setNewOpen] = useState(false);
  const [repo, setRepo] = useState('');
  const [source, setSource] = useState<'git' | 'local'>('git');
  const [branch, setBranch] = useState('main');
  const [excluded, setExcluded] = useState(DEFAULT_EXCLUDED);
  const [connectDb, setConnectDb] = useState(true);
  const [mineGit, setMineGit] = useState(true);
  const [ingestDocs, setIngestDocs] = useState(true);
  const [seeded, setSeeded] = useState<Set<string>>(() => new Set(SEED_RULES.map((r) => r.id)));

  const scopeToggles = [
    { label: 'Connect database', note: 'Read-only, against the nightly snapshot — adds schema, procedures and code↔DB links', on: connectDb, set: setConnectDb },
    { label: 'Mine git history', note: 'Churn, hotspots and bug-fix density per file', on: mineGit, set: setMineGit },
    { label: 'Ingest docs & tickets', note: 'READMEs, ADRs, Jira exports and meeting notes', on: ingestDocs, set: setIngestDocs },
  ];
  const skippedSteps = useMemo(() => new Set([
    ...(connectDb ? [] : DB_STEPS), ...(mineGit ? [] : [12]), ...(ingestDocs ? [] : [14]),
  ]), [connectDb, mineGit, ingestDocs]);
  const activeSteps = onboardingSteps.filter((st) => !skippedSteps.has(st.n));
  const estMinutes = Math.max(1, Math.round(activeSteps.reduce((n, st) => n + estSeconds(st.est), 0) / 60));
  const toggleSeed = (id: string) => setSeeded((cur) => {
    const next = new Set(cur);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const resetDraft = () => {
    setRepo('');
    setSource('git');
    setBranch('main');
    setExcluded(DEFAULT_EXCLUDED);
    setConnectDb(true);
    setMineGit(true);
    setIngestDocs(true);
    setSeeded(new Set(SEED_RULES.map((r) => r.id)));
  };

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    const out = projects.filter((p) => {
      if (kind !== 'all' && p.kind !== kind) return false;
      if (status !== 'all' && p.status !== status) return false;
      if (!s) return true;
      return (p.name + p.codename + p.stack.join(' ') + p.description).toLowerCase().includes(s);
    });
    const order = ['just now', 'active'];
    return [...out].sort((a, b) => {
      if (sort === 'understood') return b.understoodPct - a.understoodPct;
      if (sort === 'size') return b.modules - a.modules;
      return (order.indexOf(b.status) - order.indexOf(a.status)) || b.memoryPct - a.memoryPct;
    });
  }, [q, kind, status, sort]);

  return (
    <Page>
      <PageHeader
        title="Projects"
        subtitle="Every project keeps its own memory, rules, architecture graph and agents. Context never leaks between them."
        actions={<Button size="sm" onClick={() => setNewOpen(true)}><Plus className="size-3.5" />New project</Button>}
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <div className="flex h-7 w-64 items-center gap-2 rounded-sm border border-line bg-surface-2 px-2.5 focus-within:border-brand">
            <Search className="size-3.5 shrink-0 text-dim" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search projects, stacks, codenames…"
              className="min-w-0 flex-1 bg-transparent text-[12px] text-ink placeholder:text-dim focus-visible:outline-none"
            />
          </div>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
            {['all', 'legacy', 'greenfield', 'platform'].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'All kinds' : k}</option>)}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-7 rounded-sm border border-line-strong bg-surface-2 px-2 text-[12px] text-ink-2">
            {['all', 'active', 'onboarding', 'paused', 'archived'].map((k) => <option key={k} value={k} className="bg-surface">{k === 'all' ? 'All statuses' : k}</option>)}
          </select>
          <Segmented
            options={[{ id: 'active', label: 'Last active' }, { id: 'understood', label: 'Understanding' }, { id: 'size', label: 'Size' }]}
            value={sort}
            onChange={setSort}
          />
          <span className="ml-auto text-[11.5px] text-dim">{list.length} of {projects.length}</span>
        </div>
      </PageHeader>

      <PageBody className="space-y-5">
        {list.length === 0 ? (
          <Empty title="No project matches those filters" hint="Clear the search or widen the kind/status filter." />
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
            {list.map((p) => (
              <section key={p.id} className={cn('hover-lift flex flex-col rounded-md border bg-surface', p.id === projectId ? 'border-brand/50' : 'border-line')}>
                <div className="flex items-start justify-between gap-3 border-b border-line px-3.5 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Dot state={p.status} pulse={p.status === 'active'} />
                      <h2 className="truncate text-[13.5px] font-semibold text-ink">{p.name}</h2>
                      {p.id === projectId && <Tag tone="brand">active</Tag>}
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <Mono>{p.codename}</Mono>
                      <span className="eyebrow">{p.kind}</span>
                    </div>
                  </div>
                  <span className="shrink-0 text-right">
                    <span className="tnum block text-[17px] leading-none font-semibold text-brand">{p.understoodPct}%</span>
                    <span className="eyebrow">understood</span>
                  </span>
                </div>

                <div className="flex-1 px-3.5 py-3">
                  <p className="mb-2.5 line-clamp-2 text-[11.5px] text-soft">{p.description}</p>
                  <div className="mb-3 flex flex-wrap gap-1">
                    {p.stack.map((s) => (
                      <span key={s} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px text-[10.5px] text-ink-2">{s}</span>
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
                        <div className="tnum mt-0.5 text-[13px] font-medium text-ink">{v}</div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-2.5 space-y-1.5">
                    {p.coverage.slice(0, 3).map((c) => (
                      <div key={c.label} className="flex items-center gap-2">
                        <span className="w-32 shrink-0 truncate text-[11px] text-soft">{c.label}</span>
                        <BlockBar pct={c.pct} width={12} />
                        <span className="tnum w-7 text-right text-[10.5px] text-dim">{c.pct}%</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="flex items-center justify-between gap-2 border-t border-line px-3.5 py-2.5">
                  <div className="flex items-center gap-2.5 text-[11px]">
                    <span className="text-ok">{p.work.running} running</span>
                    <span className="text-warn">{p.work.review} review</span>
                    {p.work.blocked > 0 && <span className="text-danger">{p.work.blocked} blocked</span>}
                    <span className="text-dim">· {p.lastActive}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {p.id !== projectId && (
                      <Button size="xs" variant="ghost" onClick={() => { setProjectId(p.id); toast.success(`Switched to ${p.name}`, { description: 'Memory, rules and agents swapped.' }); }}>
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
            <div className="divide-y divide-line">
              {globalBrain.map((b) => (
                <div key={b.id} className="px-3.5 py-2.5">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[12.5px] font-medium text-ink">{b.label}</span>
                    <span className="tnum text-[12px] text-brand">{b.count.toLocaleString()}</span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-dim">{b.note}</p>
                  <ul className="mt-1.5 space-y-0.5">
                    {b.examples.map((e) => (
                      <li key={e} className="flex items-start gap-1.5 text-[11px] text-soft">
                        <span className="mt-1.5 size-1 shrink-0 rounded-full bg-line-strong" />{e}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Panel>

          <Panel eyebrow="Never leaves its project" title={<span className="flex items-center gap-1.5"><Lock className="size-3.5 text-warn" />Isolated memory</span>} flush>
            <div className="divide-y divide-line">
              {isolatedMemory.map((m) => {
                const p = projects.find((x) => x.id === m.projectId);
                return (
                  <div key={m.projectId} className="px-3.5 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[12px] font-medium text-ink">{p?.name ?? m.projectId}</span>
                      <span className="tnum text-[11.5px] text-soft">{m.facts.toLocaleString()} facts</span>
                    </div>
                    <div className="mt-1 flex gap-3 text-[10.5px] text-dim">
                      <span>{m.rules} rules</span><span>{m.decisions} decisions</span><span>{m.legacy} legacy</span>
                    </div>
                    <p className="mt-1 text-[11px] text-soft">{m.note}</p>
                  </div>
                );
              })}
            </div>
          </Panel>
        </div>
      </PageBody>

      {/* Onboarding wizard */}
      <Wizard
        open={newOpen}
        onOpenChange={setNewOpen}
        title="Onboard a repository"
        description="The OS reads the codebase end to end before it is allowed to change anything. One pass produces the architecture graph, the project memory and the rule set."
        finishLabel="Start onboarding"
        onFinish={() => {
          setNewOpen(false);
          toast.success('Onboarding queued', { description: `${activeSteps.length} steps · est. ${estMinutes} min · the Architect reports back at 100%.` });
          resetDraft();
        }}
        steps={[
          {
            id: 'repo', title: 'Repository', hint: 'where the code lives',
            valid: repoProblem(source, repo, branch) === null, blocker: repoProblem(source, repo, branch) ?? undefined,
            content: (
              <div className="space-y-3">
                <Segmented options={[{ id: 'git', label: 'Git remote' }, { id: 'local', label: 'Local path' }]} value={source} onChange={setSource} />
                <Field
                  label={source === 'git' ? 'Clone URL' : 'Absolute path'}
                  value={repo} onChange={setRepo} mono
                  placeholder={source === 'git' ? 'git@github.com:sofscript/careworks-erp.git' : '/Users/rajat/work/careworks-erp'}
                />
                {source === 'git' && <Field label="Branch" value={branch} onChange={setBranch} mono />}
                <div className="rounded-sm border border-line bg-base px-3 py-1.5">
                  <KV k="Credentials" v="read-only, against a snapshot replica" />
                  <KV k="Write access" v="none until you approve the first plan" />
                </div>
              </div>
            ),
          },
          {
            id: 'scope', title: 'Scope', hint: 'what gets read',
            content: (
              <div className="space-y-3">
                <div>
                  <SectionTitle>Detected from the remote</SectionTitle>
                  <div className="flex flex-wrap gap-1">
                    {['C# · .NET 8', 'T-SQL', 'TypeScript · React', 'PowerShell', 'YAML'].map((l) => <Tag key={l} tone="neutral">{l}</Tag>)}
                  </div>
                  <p className="mt-1 text-[11px] text-dim">From the repository’s language stats — confirmed properly in step 1 of the pipeline.</p>
                </div>
                <Field label="Excluded paths — never parsed, embedded or shown to a model" value={excluded} onChange={setExcluded} mono />
                <div className="divide-y divide-line rounded-sm border border-line">
                  {scopeToggles.map((t) => (
                    <div key={t.label} className="flex items-center justify-between gap-4 px-3 py-2">
                      <span className="min-w-0">
                        <span className="block text-[12.5px] font-medium text-ink">{t.label}</span>
                        <span className="block text-[11px] text-dim">{t.note}</span>
                      </span>
                      <Switch aria-label={t.label} checked={t.on} onCheckedChange={t.set} />
                    </div>
                  ))}
                </div>
              </div>
            ),
          },
          {
            id: 'rules', title: 'Access & rules', hint: 'what it may do',
            content: (
              <div className="space-y-3">
                <p className="text-[12px] leading-relaxed text-soft">
                  These become the project’s rule set on day one. Each is enforced by a reviewer check or a hook — not by
                  asking a model nicely. Edit them any time after onboarding.
                </p>
                <div className="divide-y divide-line rounded-sm border border-line">
                  {SEED_RULES.map((r) => {
                    const on = seeded.has(r.id);
                    return (
                      <button key={r.id} type="button" onClick={() => toggleSeed(r.id)} aria-pressed={on}
                        className="flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-2">
                        <span className={cn('mt-0.5 grid size-4 shrink-0 place-items-center rounded-xs border transition-colors',
                          on ? 'border-brand bg-brand text-brand-ink' : 'border-line-strong bg-surface')}>
                          {on && <Check className="size-3" strokeWidth={2.6} />}
                        </span>
                        <span className="min-w-0">
                          <span className="block text-[12.5px] font-medium text-ink">{r.label}</span>
                          <span className="block text-[11px] text-dim">{r.note}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className="flex items-start gap-2 rounded-sm border border-line bg-base px-3 py-2">
                  <Lock className="mt-px size-3.5 shrink-0 text-warn" />
                  <p className="text-[11.5px] text-soft">
                    Memory for this project is isolated. Nothing it learns leaks into other projects — except through the
                    global brain, and only after you promote it.
                  </p>
                </div>
              </div>
            ),
          },
          {
            id: 'review', title: 'Review', hint: `${activeSteps.length} of ${onboardingSteps.length} steps`,
            content: (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-x-6 rounded-sm border border-line bg-base px-3 py-1.5 sm:grid-cols-2">
                  <KV k="Source" v={repo.trim() || '—'} mono />
                  <KV k="Branch" v={source === 'git' ? branch : 'working tree'} mono />
                  <KV k="Database" v={connectDb ? 'read-only snapshot' : 'skipped'} />
                  <KV k="Rules seeded" v={`${seeded.size} of ${SEED_RULES.length}`} />
                </div>
                <SectionTitle right={<span className="text-[11px] text-dim">est. {estMinutes} min · {activeSteps.length} of {onboardingSteps.length} steps</span>}>
                  Automatic pipeline
                </SectionTitle>
                <div className="max-h-[260px] overflow-y-auto rounded-sm border border-line bg-base">
                  {onboardingSteps.map((st) => {
                    const skip = skippedSteps.has(st.n);
                    return (
                      <div key={st.n} className={cn('flex items-center gap-2.5 border-b border-line/60 px-3 py-1.5 last:border-0', skip && 'opacity-45')}>
                        <span className="tnum w-5 shrink-0 text-right font-mono text-[10.5px] text-dim">{st.n}</span>
                        <span className="min-w-0 flex-1">
                          <span className={cn('block truncate text-[12px] text-ink-2', skip && 'line-through')}>{st.label}</span>
                          <span className="block truncate text-[10.5px] text-dim">{skip ? 'skipped — turned off in Scope' : st.detail}</span>
                        </span>
                        <span className="shrink-0 text-[10.5px] text-dim">{st.agent}</span>
                        <span className="tnum w-10 shrink-0 text-right font-mono text-[10.5px] text-soft">{st.est}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ),
          },
        ]}
      />
    </Page>
  );
}
