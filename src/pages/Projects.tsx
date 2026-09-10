import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Plus, GitBranch, Database, FileCode, Boxes, Check, Brain, Lock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, BlockBar, Segmented, Mono,
  SectionTitle, Empty, KV,
} from '@/components/os';
import { projects } from '@/mock/projects';
import { onboardingSteps, globalBrain, isolatedMemory } from '@/mock/modules';
import { useProject } from '@/lib/project-context';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';

type Sort = 'active' | 'understood' | 'size';

export default function Projects() {
  const nav = useNavigate();
  const { projectId, setProjectId } = useProject();
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('all');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState<Sort>('active');
  const [newOpen, setNewOpen] = useState(false);
  const [repo, setRepo] = useState('');

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

      {/* Onboarding dialog */}
      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Onboard a repository</DialogTitle>
            <DialogDescription>
              The OS reads the codebase end to end before it is allowed to change anything. This runs once per project
              and produces the architecture graph, the project memory and the rule set.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <SectionTitle>Repository</SectionTitle>
              <input
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="git@github.com:sofscript/careworks-erp.git  ·  or a local path"
                className="h-8 w-full rounded-sm border border-line bg-base px-2.5 font-mono text-[11.5px] text-ink placeholder:text-dim focus-visible:border-brand focus-visible:outline-none"
              />
            </div>
            <div>
              <SectionTitle right={<span className="text-[11px] text-dim">est. 24 min for 2.4M lines</span>}>
                Automatic pipeline · {onboardingSteps.length} steps
              </SectionTitle>
              <div className="max-h-[300px] overflow-y-auto rounded-sm border border-line bg-base">
                {onboardingSteps.map((s) => (
                  <div key={s.n} className="flex items-center gap-2.5 border-b border-line/60 px-3 py-1.5 last:border-0">
                    <span className="tnum w-5 shrink-0 text-right font-mono text-[10.5px] text-dim">{s.n}</span>
                    <Check className="size-3 shrink-0 text-line-strong" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] text-ink-2">{s.label}</span>
                      <span className="block truncate text-[10.5px] text-dim">{s.detail}</span>
                    </span>
                    <span className="shrink-0 text-[10.5px] text-dim">{s.agent}</span>
                    <span className="tnum w-10 shrink-0 text-right font-mono text-[10.5px] text-soft">{s.est}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <KV k="Credentials" v="read-only, against a snapshot replica" />
              <KV k="Write access" v="none until you approve the first plan" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setNewOpen(false)}>Cancel</Button>
            <Button
              size="sm"
              disabled={!repo.trim()}
              onClick={() => { setNewOpen(false); setRepo(''); toast.success('Onboarding queued', { description: 'Architect + Database Engineer will report at 100%.' }); }}
            >
              Start onboarding
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}
