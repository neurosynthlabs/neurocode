import { useMemo, useState } from 'react';
import {
  Search, Pin, Archive, Pencil, FileSearch, TriangleAlert, TrendingDown, Zap, Globe, Layers,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Segmented, ListRow, Empty,
  Stat, StatGrid, KV, Bar, BlockBar, DataTable, Row, Cell,
} from '@/components/os';
import { memoryFacts, categoryMeta, categoryLabel, memoryConflicts, recentHits, memoryStats } from '@/mock/memory';
import { agentName } from '@/mock/agents';
import { projectName } from '@/mock/projects';
import { useProject } from '@/lib/project-context';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import type { MemoryCategory, Confidence } from '@/types';

const CONF_TONE: Record<Confidence, 'ok' | 'warn' | 'danger'> = { HIGH: 'ok', MEDIUM: 'warn', LOW: 'danger' };
const SEV_TONE = { HIGH: 'danger', MEDIUM: 'warn', LOW: 'neutral' } as const;

export default function Memory() {
  const { projectId } = useProject();
  const [scope, setScope] = useState<'project' | 'global'>('project');
  const [cat, setCat] = useState<MemoryCategory | 'all'>('all');
  const [q, setQ] = useState('');
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState<'facts' | 'health' | 'conflicts'>('facts');

  const scoped = useMemo(
    () => memoryFacts.filter((f) => (scope === 'global' ? f.projectId === 'global' : f.projectId === projectId || f.projectId === 'global')),
    [scope, projectId],
  );

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return scoped
      .filter((f) => (cat === 'all' ? true : f.category === cat))
      .filter((f) => (s ? (f.title + f.body + f.reason + f.tags.join(' ') + f.ref).toLowerCase().includes(s) : true))
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.strength - a.strength);
  }, [scoped, cat, q]);

  const fact = useMemo(() => list.find((f) => f.id === sel) ?? list[0], [list, sel]);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    scoped.forEach((f) => m.set(f.category, (m.get(f.category) ?? 0) + 1));
    return m;
  }, [scoped]);

  return (
    <Page>
      <PageHeader
        title="Memory"
        subtitle="Facts the OS has earned — each one carries its reason, its source and its evidence. Retrieval strengthens a fact; neglect decays it."
        actions={
          <Segmented
            options={[{ id: 'project', label: `${projectName(projectId)} + global` }, { id: 'global', label: 'Global brain only' }]}
            value={scope}
            onChange={setScope}
          />
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <Segmented
            options={[
              { id: 'facts', label: `Facts (${scoped.length})` },
              { id: 'health', label: 'Decay & health' },
              { id: 'conflicts', label: `Conflicts (${memoryConflicts.length})` },
            ]}
            value={tab}
            onChange={setTab}
          />
          {tab === 'facts' && (
            <div className="flex h-7 w-80 items-center gap-2 rounded-sm border border-line bg-surface-2 px-2.5 focus-within:border-brand">
              <Search className="size-3.5 shrink-0 text-dim" />
              <input value={q} onChange={(e) => { setQ(e.target.value); setSel(null); }}
                placeholder="Search memory — “TRANS”, “rounding”, “Hinglish”, MEM-142…"
                className="min-w-0 flex-1 bg-transparent text-[12px] text-ink placeholder:text-dim focus-visible:outline-none" />
            </div>
          )}
        </div>
      </PageHeader>

      <PageBody className={cn(tab === 'facts' && 'flex h-full flex-col p-0')}>
        {tab === 'facts' && (
          <div className="flex min-h-0 flex-1 gap-0">
            {/* Category rail */}
            <div className="flex w-52 shrink-0 flex-col border-r border-line">
              <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto py-2">
                <button
                  onClick={() => setCat('all')}
                  className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[12px] transition-colors',
                    cat === 'all' ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}
                >
                  <span className="flex items-center gap-2"><Layers className="size-3.5 text-dim" />All categories</span>
                  <span className="tnum text-[11px] text-dim">{scoped.length}</span>
                </button>
                <div className="my-1.5 mx-3.5 h-px bg-line" />
                {categoryMeta.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => { setCat(c.id); setSel(null); }}
                    className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[12px] transition-colors',
                      cat === c.id ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}
                  >
                    <span className="truncate">{c.label}</span>
                    <span className="tnum text-[11px] text-dim">{counts.get(c.id) ?? 0}</span>
                  </button>
                ))}
              </div>
              <div className="shrink-0 border-t border-line p-3">
                <KV k="Pinned" v={memoryStats.pinned} />
                <KV k="Decaying" v={<span className="text-warn">{memoryStats.decaying}</span>} />
                <KV k="Hits 24h" v={memoryStats.hits24h.toLocaleString()} />
                <KV k="Written 24h" v={memoryStats.writes24h} />
              </div>
            </div>

            {/* Fact list */}
            <div className="no-scrollbar w-[340px] shrink-0 overflow-y-auto border-r border-line">
              {list.length === 0 ? <Empty title="Nothing recalled" hint="No fact in this scope matches that search." /> : list.map((f) => (
                <ListRow key={f.id} active={fact?.id === f.id} onClick={() => setSel(f.id)}>
                  <div className="flex items-center gap-2">
                    <Mono tone={f.pinned ? 'brand' : 'neutral'}>{f.ref}</Mono>
                    {f.pinned && <Pin className="size-3 text-brand" />}
                    <span className="eyebrow ml-auto">{categoryLabel(f.category)}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[12px] text-ink">{f.title}</p>
                  <div className="mt-1.5 flex items-center gap-2">
                    <BlockBar pct={f.strength} width={8} tone={f.strength < 70 ? 'warn' : undefined} />
                    <span className="tnum text-[10.5px] text-dim">{f.strength}</span>
                    <Tag tone={CONF_TONE[f.confidence]}>{f.confidence}</Tag>
                    <span className="ml-auto text-[10.5px] text-dim">{f.hits} hits</span>
                  </div>
                </ListRow>
              ))}
            </div>

            {/* Detail */}
            <div className="min-w-0 flex-1 overflow-y-auto">
              {!fact ? <Empty title="Select a fact" /> : (
                <div className="space-y-3 p-5">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Mono tone="brand">{fact.ref}</Mono>
                      <Tag tone="neutral">{categoryLabel(fact.category)}</Tag>
                      <Tag tone={CONF_TONE[fact.confidence]}>confidence {fact.confidence}</Tag>
                      {fact.projectId === 'global' ? <Tag tone="violet"><Globe className="size-3" />global</Tag> : <Tag tone="neutral">{projectName(fact.projectId)}</Tag>}
                      {fact.pinned && <Tag tone="brand"><Pin className="size-3" />pinned</Tag>}
                    </div>
                    <h2 className="mt-2 text-[16px] leading-snug font-semibold text-ink">{fact.title}</h2>
                    <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{fact.body}</p>
                  </div>

                  <Panel eyebrow="Why this is true" title="Reason">
                    <p className="text-[12.5px] leading-relaxed text-ink-2">{fact.reason}</p>
                    <div className="mt-2.5 border-t border-line pt-2.5">
                      <KV k="Source" v={fact.source} />
                      <KV k="Created" v={fact.createdAt} />
                      <KV k="Last used" v={fact.lastUsed} />
                      <KV k="Retrieval hits" v={fact.hits} />
                    </div>
                  </Panel>

                  <div className="grid grid-cols-2 gap-3">
                    <Panel eyebrow="Strength" title={`${fact.strength} / 100`}>
                      <Bar pct={fact.strength} tone={fact.strength < 70 ? 'warn' : 'ok'} height="h-1.5" />
                      <p className="mt-2 text-[11.5px] text-dim">
                        {fact.pinned
                          ? 'Pinned — exempt from decay. It will be surfaced regardless of how long it sits unused.'
                          : `Half-life ${categoryMeta.find((c) => c.id === fact.category)?.halfLifeDays ?? 365} days. Each retrieval adds +${categoryMeta.find((c) => c.id === fact.category)?.reinforce ?? 3}.`}
                      </p>
                    </Panel>
                    <Panel eyebrow="Tags" title="Indexed under">
                      <div className="flex flex-wrap gap-1">
                        {fact.tags.map((t) => <span key={t} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px font-mono text-[10.5px] text-ink-2">{t}</span>)}
                      </div>
                    </Panel>
                  </div>

                  <Panel eyebrow="Evidence" title={`${fact.evidence.length} artefacts back this claim`} flush>
                    <div className="divide-y divide-line">
                      {fact.evidence.map((e) => (
                        <div key={e} className="flex items-center gap-2 px-3.5 py-1.5">
                          <FileSearch className="size-3 shrink-0 text-dim" />
                          <span className="truncate font-mono text-[11.5px] text-ink-2">{e}</span>
                        </div>
                      ))}
                    </div>
                  </Panel>

                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => toast('Evidence opened in Knowledge')}><FileSearch className="size-3.5" />View evidence</Button>
                    <Button size="sm" variant="outline" onClick={() => toast('Editing a fact requires a source — static prototype')}><Pencil className="size-3.5" />Edit</Button>
                    <Button size="sm" variant="outline" onClick={() => toast(fact.pinned ? 'Unpinned' : 'Pinned — exempt from decay')}><Pin className="size-3.5" />{fact.pinned ? 'Unpin' : 'Pin'}</Button>
                    <Button size="sm" variant="destructive" onClick={() => toast('Archived — recoverable for 90 days')}><Archive className="size-3.5" />Archive</Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === 'health' && (
          <div className="space-y-4">
            <StatGrid cols={5}>
              <Stat label="Facts held" value={memoryStats.total} sub={`${memoryStats.global} in the global brain`} icon={<Layers className="size-3" />} />
              <Stat label="Pinned" value={memoryStats.pinned} tone="brand" sub="never decay" icon={<Pin className="size-3" />} />
              <Stat label="Decaying" value={memoryStats.decaying} tone="warn" sub="strength under 70" icon={<TrendingDown className="size-3" />} />
              <Stat label="Conflicts" value={memoryStats.conflicting} tone="danger" sub="need a human ruling" icon={<TriangleAlert className="size-3" />} />
              <Stat label="Retired 30d" value={memoryStats.retired30d} sub="decayed below threshold" />
            </StatGrid>

            <Panel eyebrow="Human-like forgetting" title="Decay model by category" flush>
              <DataTable head={['Category', 'Half-life', 'Reinforcement per hit', 'Held', 'Behaviour']}>
                {categoryMeta.map((c) => (
                  <Row key={c.id}>
                    <Cell className="font-medium text-ink">{c.label}</Cell>
                    <Cell className="tnum">{c.halfLifeDays >= 3650 ? '∞' : `${c.halfLifeDays} d`}</Cell>
                    <Cell className="tnum text-ok">+{c.reinforce}</Cell>
                    <Cell className="tnum">{counts.get(c.id) ?? 0}</Cell>
                    <Cell className="max-w-[520px] text-[11.5px] text-soft">{c.note}</Cell>
                  </Row>
                ))}
              </DataTable>
              <p className="border-t border-line px-3.5 py-2.5 text-[11.5px] text-soft">
                <span className="text-ink-2">Pinned facts and HIGH-confidence decisions never decay.</span> Everything else
                loses strength on a per-category half-life and regains it every time an agent actually uses it — so the
                memory that matters gets louder and the rest goes quiet on its own.
              </p>
            </Panel>

            <Panel eyebrow="Reinforcement" title="Recent retrieval hits" flush>
              <DataTable head={['Fact', 'Agent', 'Context', 'When', 'Strength Δ']}>
                {recentHits.map((h, i) => (
                  <Row key={`${h.ref}-${i}`}>
                    <Cell mono className="text-brand">{h.ref}</Cell>
                    <Cell>{agentName(h.agent)}</Cell>
                    <Cell className="text-[11.5px] text-soft">{h.context}</Cell>
                    <Cell className="text-dim">{h.at}</Cell>
                    <Cell className="tnum text-ok">+{h.delta}</Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>
          </div>
        )}

        {tab === 'conflicts' && (
          <div className="space-y-3">
            <p className="text-[12px] text-soft">
              Two facts cannot both be true. The OS refuses to silently pick a winner — a contradiction is surfaced with
              both claims intact and waits for your ruling.
            </p>
            {memoryConflicts.map((c) => (
              <Panel key={c.id} eyebrow={`detected ${c.detected}`} title={c.topic} className={c.severity === 'HIGH' ? 'border-danger/35' : undefined}
                actions={<Tag tone={SEV_TONE[c.severity]}>{c.severity}</Tag>}>
                <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                  {[c.a, c.b].map((side, i) => (
                    <div key={i} className="rounded-sm border border-line bg-base p-3">
                      <div className="eyebrow mb-1">claim {i === 0 ? 'A' : 'B'}</div>
                      <p className="text-[12px] text-ink-2">{side}</p>
                    </div>
                  ))}
                </div>
                <p className="mt-2.5 text-[12px] text-soft">{c.detail}</p>
                <div className="mt-2.5 flex items-center justify-between gap-3 border-t border-line pt-2.5">
                  <p className="flex items-start gap-1.5 text-[11.5px] text-ink-2"><Zap className="mt-px size-3 shrink-0 text-brand" />{c.suggestion}</p>
                  <div className="flex shrink-0 gap-1.5">
                    <Button size="xs" variant="outline" onClick={() => toast('Kept A — B archived with a superseded-by link')}>Keep A</Button>
                    <Button size="xs" variant="outline" onClick={() => toast('Kept B — A archived with a superseded-by link')}>Keep B</Button>
                    <Button size="xs" variant="ghost" onClick={() => toast('Escalated to an ADR')}>Write ADR</Button>
                  </div>
                </div>
              </Panel>
            ))}
          </div>
        )}
      </PageBody>
    </Page>
  );
}
