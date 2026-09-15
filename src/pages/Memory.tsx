import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Search, Pin, Archive, Pencil, FileSearch, TriangleAlert, TrendingDown, Zap, Globe, Layers, FilePlus2, Loader2, Sparkles,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { Extracted } from '@/lib/api';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Segmented, ListRow, Empty,
  Stat, StatGrid, KV, Bar, BlockBar, DataTable, Row, Cell,
} from '@/components/os';
import { categoryMeta, categoryLabel, recentHits, memoryStats } from '@/mock/memory';
import { agentName } from '@/mock/agents';
import { projectName } from '@/mock/projects';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import type { MemoryCategory, Confidence, MemoryFact } from '@/types';
import { ago } from '@/lib/time';

const CONF_TONE: Record<Confidence, 'ok' | 'warn' | 'danger'> = { HIGH: 'ok', MEDIUM: 'warn', LOW: 'danger' };
const SEV_TONE = { HIGH: 'danger', MEDIUM: 'warn', LOW: 'neutral' } as const;

export default function Memory() {
  const { projectId } = useProject();
  const [scope, setScope] = useState<'project' | 'global'>('project');
  const [cat, setCat] = useState<MemoryCategory | 'all'>('all');
  const [q, setQ] = useState('');
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState<'facts' | 'health' | 'conflicts'>('facts');
  const [adding, setAdding] = useState(false);

  const { memory, conflicts, mode, setPinned, archive, searchMemory, resolveConflict } = useData();
  const query = q.trim();

  // ⌘K opens a fact with ?ref=: show it whatever the filters were
  const wanted = useSearchParams()[0].get('ref');
  const wantedId = useMemo(() => memory.find((f) => f.ref === wanted)?.id, [memory, wanted]);
  useEffect(() => {
    if (!wantedId) return;
    setSel(wantedId);
    setCat('all');
    setQ('');
    setTab('facts');
  }, [wantedId]);

  // With the local API up, search is the server's FTS5 index: prefix-matched word by word, best match
  // first. Answers are keyed by their query, so a slow answer never replaces a newer one; until it
  // arrives, the local filter stands in.
  const [ranked, setRanked] = useState<{ q: string; refs: string[] } | null>(null);
  useEffect(() => {
    if (mode !== 'live' || !query) return;
    const ctl = new AbortController();
    const id = window.setTimeout(() => {
      searchMemory(query, ctl.signal).then((refs) => setRanked({ q: query, refs })).catch(() => { /* aborted or offline */ });
    }, 120);
    return () => { window.clearTimeout(id); ctl.abort(); };
  }, [mode, query, searchMemory]);
  const fts = mode === 'live' && ranked?.q === query ? ranked.refs : null;

  const scoped = useMemo(
    () => memory.filter((f) => (scope === 'global' ? f.projectId === 'global' : f.projectId === projectId || f.projectId === 'global')),
    [memory, scope, projectId],
  );

  const list = useMemo(() => {
    const inCat = (f: MemoryFact) => cat === 'all' || f.category === cat;
    if (fts) {
      const byRef = new Map(scoped.map((f) => [f.ref, f]));
      return fts.map((r) => byRef.get(r)).filter((f): f is MemoryFact => !!f && inCat(f));
    }
    const s = query.toLowerCase();
    return scoped
      .filter(inCat)
      .filter((f) => (s ? (f.title + f.body + f.reason + f.tags.join(' ') + f.ref).toLowerCase().includes(s) : true))
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.strength - a.strength);
  }, [scoped, cat, query, fts]);

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
          <>
            <Button size="sm" variant="outline" onClick={() => setAdding(true)}><FilePlus2 className="size-3.5" />Add from text</Button>
            <Segmented
              options={[{ id: 'project', label: `${projectName(projectId)} + global` }, { id: 'global', label: 'Global brain only' }]}
              value={scope}
              onChange={setScope}
            />
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <Segmented
            options={[
              { id: 'facts', label: `Facts (${scoped.length})` },
              { id: 'health', label: 'Decay & health' },
              { id: 'conflicts', label: `Conflicts (${conflicts.length})` },
            ]}
            value={tab}
            onChange={setTab}
          />
          {tab === 'facts' && (
            <div className="flex h-9 w-80 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
              <Search className="size-3.5 shrink-0 text-dim" />
              <input value={q} onChange={(e) => { setQ(e.target.value); setSel(null); }}
                placeholder="Search memory — “TRANS”, “rounding”, “Hinglish”, MEM-142…"
                className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none" />
            </div>
          )}
          {tab === 'facts' && fts && <Tag tone="brand">FTS5 · ranked</Tag>}
        </div>
      </PageHeader>

      <PageBody className={cn(tab === 'facts' && 'flex h-full flex-col p-0')}>
        {tab === 'facts' && (
          <div className="flex min-h-0 flex-1 flex-col gap-0 lg:flex-row">
            {/* Category rail */}
            <div className="hidden w-52 shrink-0 flex-col border-r border-line lg:flex">
              <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto py-2">
                <button
                  onClick={() => setCat('all')}
                  className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[13px] transition-colors',
                    cat === 'all' ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}
                >
                  <span className="flex items-center gap-2"><Layers className="size-3.5 text-dim" />All categories</span>
                  <span className="tnum text-[12px] text-dim">{scoped.length}</span>
                </button>
                <div className="my-1.5 mx-3.5 h-px bg-line" />
                {categoryMeta.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => { setCat(c.id); setSel(null); }}
                    className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[13px] transition-colors',
                      cat === c.id ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}
                  >
                    <span className="truncate">{c.label}</span>
                    <span className="tnum text-[12px] text-dim">{counts.get(c.id) ?? 0}</span>
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

            {/* Categories as a scrollable chip row where the rail does not fit */}
            <div className="no-scrollbar flex shrink-0 gap-1.5 overflow-x-auto border-b border-line px-3 py-2 lg:hidden">
              {[{ id: 'all', label: 'All' }, ...categoryMeta].map((c) => (
                <button
                  key={c.id}
                  onClick={() => { setCat(c.id as MemoryCategory | 'all'); setSel(null); }}
                  className={cn('shrink-0 rounded-sm border px-2 py-1 text-[12.5px] transition-colors',
                    cat === c.id ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-line bg-surface text-soft')}
                >
                  {c.label}
                </button>
              ))}
            </div>

            {/* Fact list */}
            <div className="no-scrollbar w-full shrink-0 max-h-[42vh] lg:max-h-none lg:w-[340px] overflow-y-auto border-b border-line lg:border-b-0 lg:border-r">
              {list.length === 0 ? <Empty title="Nothing recalled" hint="No fact in this scope matches that search." /> : list.map((f) => (
                <ListRow key={f.id} active={fact?.id === f.id} onClick={() => setSel(f.id)}>
                  <div className="flex items-center gap-2">
                    <Mono tone={f.pinned ? 'brand' : 'neutral'}>{f.ref}</Mono>
                    {f.pinned && <Pin className="size-3 text-brand" />}
                    <span className="eyebrow ml-auto">{categoryLabel(f.category)}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[13px] text-ink">{f.title}</p>
                  <div className="mt-1.5 flex items-center gap-2">
                    <BlockBar pct={f.strength} width={8} tone={f.strength < 70 ? 'warn' : undefined} />
                    <span className="tnum text-[11.5px] text-dim">{f.strength}</span>
                    <Tag tone={CONF_TONE[f.confidence]}>{f.confidence}</Tag>
                    <span className="ml-auto text-[11.5px] text-dim">{f.hits} hits</span>
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
                      <Tag tone={CONF_TONE[fact.confidence]}>{fact.confidence[0]}{fact.confidence.slice(1).toLowerCase()} confidence</Tag>
                      {fact.projectId === 'global' ? <Tag tone="violet"><Globe className="size-3" />global</Tag> : <Tag tone="neutral">{projectName(fact.projectId)}</Tag>}
                      {fact.pinned && <Tag tone="brand"><Pin className="size-3" />pinned</Tag>}
                    </div>
                    <h2 className="mt-2 text-[16px] leading-snug font-semibold text-ink">{fact.title}</h2>
                    <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{fact.body}</p>
                  </div>

                  <Panel eyebrow="Why this is true" title="Reason">
                    <p className="text-[13.5px] leading-relaxed text-ink-2">{fact.reason}</p>
                    <div className="mt-2.5 border-t border-line pt-2.5">
                      <KV k="Source" v={fact.source} />
                      <KV k="Created" v={ago(fact.createdAt)} />
                      <KV k="Last used" v={ago(fact.lastUsed)} />
                      <KV k="Retrieval hits" v={fact.hits} />
                    </div>
                  </Panel>

                  <div className="grid grid-cols-2 gap-3">
                    <Panel eyebrow="Strength" title={`${fact.strength} / 100`}>
                      <Bar pct={fact.strength} tone={fact.strength < 70 ? 'warn' : 'ok'} height="h-1.5" />
                      <p className="mt-2 text-[12.5px] text-dim">
                        {fact.pinned
                          ? 'Pinned — exempt from decay. It will be surfaced regardless of how long it sits unused.'
                          : `Half-life ${categoryMeta.find((c) => c.id === fact.category)?.halfLifeDays ?? 365} days. Each retrieval adds +${categoryMeta.find((c) => c.id === fact.category)?.reinforce ?? 3}.`}
                      </p>
                    </Panel>
                    <Panel eyebrow="Tags" title="Indexed under">
                      <div className="flex flex-wrap gap-1">
                        {fact.tags.map((t) => <span key={t} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px font-mono text-[11.5px] text-ink-2">{t}</span>)}
                      </div>
                    </Panel>
                  </div>

                  <Panel eyebrow="Evidence" title={`${fact.evidence.length} artefacts back this claim`} flush>
                    <div className="divide-y divide-line">
                      {fact.evidence.map((e) => (
                        <div key={e} className="flex items-center gap-2 px-3.5 py-1.5">
                          <FileSearch className="size-3 shrink-0 text-dim" />
                          <span className="truncate font-mono text-[12.5px] text-ink-2">{e}</span>
                        </div>
                      ))}
                    </div>
                  </Panel>

                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => toast('Evidence opened in Knowledge')}><FileSearch className="size-3.5" />View evidence</Button>
                    <Button size="sm" variant="outline" onClick={() => toast('Editing a fact requires a source — static prototype')}><Pencil className="size-3.5" />Edit</Button>
                    <Button size="sm" variant="outline" onClick={async () => { if (await setPinned(fact.ref, !fact.pinned)) toast(fact.pinned ? `${fact.ref} unpinned` : `${fact.ref} pinned — exempt from decay`); }}><Pin className="size-3.5" />{fact.pinned ? 'Unpin' : 'Pin'}</Button>
                    <Button size="sm" variant="destructive" onClick={async () => { if (await archive(fact.ref)) toast(`${fact.ref} archived`, { description: 'Out of recall, never deleted. The record stays recoverable.' }); }}><Archive className="size-3.5" />Archive</Button>
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
              <Stat label="Conflicts" value={conflicts.length} tone="danger" sub="need a human ruling" icon={<TriangleAlert className="size-3" />} />
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
                    <Cell className="max-w-[520px] text-[12.5px] text-soft">{c.note}</Cell>
                  </Row>
                ))}
              </DataTable>
              <p className="border-t border-line px-3.5 py-2.5 text-[12.5px] text-soft">
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
                    <Cell className="text-[12.5px] text-soft">{h.context}</Cell>
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
            <p className="text-[13px] text-soft">
              Two facts cannot both be true. The OS refuses to silently pick a winner — a contradiction is surfaced with
              both claims intact and waits for your ruling.
            </p>
            {conflicts.length === 0 && <Empty title="No contradictions left" hint="Every fact in memory agrees with the others." />}
            {conflicts.map((c) => (
              <Panel key={c.id} eyebrow={`detected ${c.detected}`} title={c.topic} className={c.severity === 'HIGH' ? 'border-danger/35' : undefined}
                actions={<Tag tone={SEV_TONE[c.severity]}>{c.severity}</Tag>}>
                <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                  {[c.a, c.b].map((id, i) => {
                    const f = memory.find((x) => x.id === id);
                    return (
                      <div key={id} className="rounded-sm border border-line bg-base p-3">
                        <div className="eyebrow mb-1">claim {i === 0 ? 'A' : 'B'}{f && <> · <span className="font-mono normal-case">{f.ref}</span></>}</div>
                        <p className="text-[13px] text-ink-2">{f ? f.title : id}</p>
                      </div>
                    );
                  })}
                </div>
                <p className="mt-2.5 text-[13px] text-soft">{c.detail}</p>
                <div className="mt-2.5 flex items-center justify-between gap-3 border-t border-line pt-2.5">
                  <p className="flex items-start gap-1.5 text-[12.5px] text-ink-2"><Zap className="mt-px size-3 shrink-0 text-brand" />{c.suggestion}</p>
                  <div className="flex shrink-0 gap-1.5">
                    <Button size="xs" variant="outline" onClick={async () => { if (await resolveConflict(c.id, 'a')) toast.success('Kept A', { description: 'B is archived as superseded. Nothing is deleted.' }); }}>Keep A</Button>
                    <Button size="xs" variant="outline" onClick={async () => { if (await resolveConflict(c.id, 'b')) toast.success('Kept B', { description: 'A is archived as superseded. Nothing is deleted.' }); }}>Keep B</Button>
                    <Button size="xs" variant="ghost" onClick={async () => { if (await resolveConflict(c.id, 'adr')) toast('Escalated to an ADR', { description: 'Both facts stay until the decision is recorded.' }); }}>Write ADR</Button>
                  </div>
                </div>
              </Panel>
            ))}
          </div>
        )}
      </PageBody>
      <AddFromText
        open={adding} onOpenChange={setAdding} projectId={projectId}
        onAdded={(docs) => { setTab('facts'); setCat('all'); setQ(''); setSel(docs[0].id); }}
      />
    </Page>
  );
}

/** Paste notes; the facts worth keeping come back as candidates, and only the ones you tick are stored. */
function AddFromText({ open, onOpenChange, projectId, onAdded }: {
  open: boolean; onOpenChange: (open: boolean) => void; projectId: string; onAdded: (facts: MemoryFact[]) => void;
}) {
  const { extract, addFacts } = useData();
  const [text, setText] = useState('');
  const [found, setFound] = useState<Extracted | null>(null);
  const [skip, setSkip] = useState<Set<number>>(() => new Set());
  const [scope, setScope] = useState<'project' | 'global'>('project');
  const [busy, setBusy] = useState(false);
  const picked = found ? found.facts.filter((_, i) => !skip.has(i)) : [];

  const close = () => { onOpenChange(false); setText(''); setFound(null); setSkip(new Set()); };
  const find = async () => {
    if (text.trim().length < 10 || busy) return;
    setBusy(true);
    const r = await extract(text.trim(), projectId);
    setBusy(false);
    if (r) { setFound(r); setSkip(new Set()); }
  };
  const save = async () => {
    if (!picked.length || busy) return;
    setBusy(true);
    const docs = await addFacts(scope === 'global' ? 'global' : projectId, picked);
    setBusy(false);
    if (!docs?.length) return;
    toast.success(`${docs.length} ${docs.length === 1 ? 'fact' : 'facts'} added to memory`, { description: docs.map((d) => d.ref).join(' · ') });
    onAdded(docs);
    close();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>Add from text</DialogTitle>
          <DialogDescription>
            Paste meeting notes, a requirement or a chat, in English or Hinglish. The facts worth keeping come back, and you choose which to store.
          </DialogDescription>
        </DialogHeader>
        {!found ? (
          <textarea
            value={text} onChange={(e) => setText(e.target.value)} rows={9} autoFocus aria-label="Text to read"
            placeholder={'Billing review, 21 Aug.\nInvoices must round at the invoice level, never per line.\nWe decided to freeze MST_TAX until October.'}
            className="focus-brand w-full resize-none rounded-lg border border-line bg-surface-2/60 p-3 text-[13.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:outline-none"
          />
        ) : found.facts.length === 0 ? (
          <Empty
            icon={<FileSearch className="size-6" />} title="No fact worth keeping was found"
            hint={found.provider === 'rules'
              ? 'The offline rules keep sentences that state a rule or a decision: must, never, always, decided, zaroori… A model reads more; set one in Admin → AI providers.'
              : 'Nothing in this text looks durable enough to remember.'}
          />
        ) : (
          <div className="grid gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-dim">
              <span>{found.facts.length} found · {found.provider === 'rules' ? 'offline rules' : found.model}</span>
              <Segmented options={[{ id: 'project', label: 'This project' }, { id: 'global', label: 'Global' }]} value={scope} onChange={setScope} />
            </div>
            <div className="max-h-[46vh] divide-y divide-line/50 overflow-y-auto rounded-xl border border-line/70">
              {found.facts.map((f, i) => (
                <label key={i} className="flex cursor-pointer items-start gap-3 px-4 py-3 hover:bg-surface-2/50">
                  <input
                    type="checkbox" className="mt-1 size-4 shrink-0 accent-[var(--os-brand)]" checked={!skip.has(i)}
                    onChange={(e) => setSkip((s) => { const n = new Set(s); if (e.target.checked) n.delete(i); else n.add(i); return n; })}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium text-ink">{f.title}</span>
                    {f.body !== f.title && <span className="mt-0.5 block text-[13px] text-soft">{f.body}</span>}
                    <span className="mt-1.5 flex flex-wrap gap-1.5">
                      <Tag tone="neutral">{categoryLabel(f.category)}</Tag>
                      <Tag tone={CONF_TONE[f.confidence]}>{f.confidence}</Tag>
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={found ? () => setFound(null) : close}>{found ? 'Back' : 'Cancel'}</Button>
          {found ? (
            <Button onClick={() => void save()} disabled={busy || !picked.length}>
              {busy && <Loader2 className="size-3.5 animate-spin" />}Add {picked.length} {picked.length === 1 ? 'fact' : 'facts'}
            </Button>
          ) : (
            <Button onClick={() => void find()} disabled={busy || text.trim().length < 10}>
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}Find facts
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
