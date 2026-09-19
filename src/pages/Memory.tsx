import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Search, Pin, Archive, FileSearch, TriangleAlert, Globe, Layers, FilePlus2, Loader2, Sparkles, Zap, Split, Activity,
  Download, Check, X, Pencil, ChevronDown, ChevronUp, Scale,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Switch } from '@/components/ui/switch';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { SIGNAL_LABEL, tasteApi, tasteMarkdown, type TasteKind, type TasteRule, type TasteSignal } from '@/lib/live/taste';
import type { Extracted } from '@/lib/api';
import {
  Page, PageHeader, PageBody, Panel, Tag, Mono, Segmented, ListRow, Empty,
  Stat, StatGrid, KV, DataTable, Row, Cell, Field, SelectField,
} from '@/components/os';
import { MEMORY_CATEGORIES, RECALLED_BY, categoryLabel, memoryApi, type ConflictInput } from '@/lib/live/knowledge';
import { useProject } from '@/lib/project-context';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import type { MemoryCategory, Confidence, MemoryFact } from '@/types';
import { ago } from '@/lib/time';

const CONF_TONE: Record<Confidence, 'ok' | 'warn' | 'danger'> = { HIGH: 'ok', MEDIUM: 'warn', LOW: 'danger' };
const SEV_TONE = { HIGH: 'danger', MEDIUM: 'warn', LOW: 'neutral' } as const;
const DAY = 86_400_000;
const within = (iso: string | null, ms: number) => !!iso && Date.now() - new Date(iso).getTime() < ms;
/** What moves when a fact streams in, is pinned, recalled or archived: a key for reading the counts again. */
const changed = (live: MemoryFact[]) =>
  `${live.length}:${live.reduce((n, f) => n + f.hits24h, 0)}:${live.filter((f) => f.pinned).length}`;

export default function Memory() {
  const { projectId, project } = useProject();
  const [scope, setScope] = useState<'project' | 'global'>('project');
  const [cat, setCat] = useState<MemoryCategory | 'all'>('all');
  const [q, setQ] = useState('');
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState<'facts' | 'health' | 'conflicts' | 'taste'>('facts');
  const [adding, setAdding] = useState(false);
  const [marking, setMarking] = useState<{ a: string } | null>(null);

  const { memory, conflicts, setPinned, archive, searchMemory, resolveConflict } = useData();
  const query = q.trim();
  // An archived fact streams back as a change; it is out of recall, so it is out of these lists.
  const live = useMemo(() => memory.filter((f) => !f.archived), [memory]);

  // ⌘K opens a fact with ?ref=: show it whatever the filters were
  const wanted = useSearchParams()[0].get('ref');
  const wantedId = useMemo(() => live.find((f) => f.ref === wanted)?.id, [live, wanted]);
  // Adjusted during render rather than in an effect: a new ?ref= resets the filters once, before paint.
  const [opened, setOpened] = useState<string | undefined>(undefined);
  if (wantedId && wantedId !== opened) {
    setOpened(wantedId);
    setSel(wantedId);
    setCat('all');
    setQ('');
    setTab('facts');
  }

  // Search is the server's full text, best match first. Answers are keyed by their query, so a slow
  // answer never replaces a newer one; until it arrives, the local filter stands in.
  const [ranked, setRanked] = useState<{ q: string; refs: string[] } | null>(null);
  useEffect(() => {
    if (!query) return;
    const ctl = new AbortController();
    const id = window.setTimeout(() => {
      searchMemory(query, ctl.signal).then((refs) => setRanked({ q: query, refs })).catch(() => { /* superseded by a newer query */ });
    }, 120);
    return () => { window.clearTimeout(id); ctl.abort(); };
  }, [query, searchMemory]);
  const fts = query && ranked?.q === query ? ranked.refs : null;

  const scoped = useMemo(
    () => live.filter((f) => (scope === 'global' || !projectId ? f.projectId === null : f.projectId === projectId || f.projectId === null)),
    [live, scope, projectId],
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
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || (b.lastUsedAt ?? '').localeCompare(a.lastUsedAt ?? '') || b.createdAt.localeCompare(a.createdAt));
  }, [scoped, cat, query, fts]);

  const fact = useMemo(() => list.find((f) => f.id === sel) ?? list[0], [list, sel]);
  // The rail's figures are the server's counts for this scope: the list is a page of the facts, and
  // counting it made a large memory read as a small one. Keyed on what a streamed change moves.
  const statScope = scope === 'global' || !projectId ? 'global' : projectId;
  const stats = useRemote(`stats:${statScope}:${changed(live)}`, () => memoryApi.stats(statScope));
  const figure = (n: number | undefined) => (stats.data ? (n ?? 0).toLocaleString() : '…');

  return (
    <Page>
      <PageHeader
        title="Memory"
        subtitle="Facts the workspace keeps, each with its reason, its source and its evidence. Every time a feature uses one, that use is recorded."
        actions={
          <>
            <Button size="sm" variant="outline" onClick={() => setAdding(true)}><FilePlus2 className="size-3.5" />Add from text</Button>
            {project && (
              <Segmented
                options={[{ id: 'project', label: `${project.name} + global` }, { id: 'global', label: 'Global brain only' }]}
                value={scope}
                onChange={setScope}
              />
            )}
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <Segmented
            options={[
              { id: 'facts', label: `Facts (${scoped.length})` },
              { id: 'health', label: 'Use & health' },
              { id: 'conflicts', label: `Conflicts (${conflicts.length})` },
              { id: 'taste', label: 'Taste' },
            ]}
            value={tab}
            onChange={setTab}
          />
          {tab === 'facts' && (
            <div className="flex h-9 w-80 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
              <Search className="size-3.5 shrink-0 text-dim" />
              <input value={q} onChange={(e) => { setQ(e.target.value); setSel(null); }}
                placeholder="Search memory — words, or a ref like MEM-12"
                className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none" />
            </div>
          )}
          {tab === 'facts' && fts && <Tag tone="brand">Full text · ranked</Tag>}
        </div>
      </PageHeader>

      <PageBody className={cn(tab === 'facts' && 'flex h-full flex-col p-0')}>
        {tab === 'facts' && (scoped.length === 0 ? (
          <Empty icon={<Layers className="size-6" />} title="Nothing remembered yet"
            hint="Facts arrive when you answer a plan's open question or use Add from text. Each keeps its reason, its source and its evidence."
            action={<Button size="sm" variant="outline" onClick={() => setAdding(true)}>Add facts from text</Button>} />
        ) : (
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
                  <span className="tnum text-[12px] text-dim">{figure(stats.data?.held)}</span>
                </button>
                <div className="my-1.5 mx-3.5 h-px bg-line" />
                {MEMORY_CATEGORIES.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => { setCat(c.id); setSel(null); }}
                    className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[13px] transition-colors',
                      cat === c.id ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}
                  >
                    <span className="truncate">{c.label}</span>
                    <span className="tnum text-[12px] text-dim">{figure(stats.data?.byCategory[c.id]?.held)}</span>
                  </button>
                ))}
              </div>
              <div className="shrink-0 border-t border-line p-3">
                <KV k="Pinned" v={figure(stats.data?.pinned)} />
                <KV k="Recalled 24h" v={figure(stats.data?.recalled24h)} />
                <KV k="Written 24h" v={scoped.filter((f) => within(f.createdAt, DAY)).length} />
              </div>
            </div>

            {/* Categories as a scrollable chip row where the rail does not fit */}
            <div className="no-scrollbar flex shrink-0 gap-1.5 overflow-x-auto border-b border-line px-3 py-2 lg:hidden">
              {[{ id: 'all', label: 'All' }, ...MEMORY_CATEGORIES].map((c) => (
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
                    <Tag tone={CONF_TONE[f.confidence]}>{f.confidence}</Tag>
                    <span className="ml-auto text-[11.5px] text-dim">
                      {f.hits24h ? `${f.hits24h} ${f.hits24h === 1 ? 'recall' : 'recalls'} in 24h` : f.lastUsedAt ? `used ${ago(f.lastUsedAt)}` : 'not used yet'}
                    </span>
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
                      {fact.projectId === null
                        ? <Tag tone="violet"><Globe className="size-3" />global</Tag>
                        : <Tag tone="neutral">{project?.id === fact.projectId ? project.name : fact.projectId}</Tag>}
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
                      <KV k="Last used" v={fact.lastUsedAt ? ago(fact.lastUsedAt) : 'not yet'} />
                      <KV k="Recalls in 24h" v={fact.hits24h} />
                    </div>
                  </Panel>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Panel eyebrow="Use" title={fact.hits24h ? `${fact.hits24h} ${fact.hits24h === 1 ? 'recall' : 'recalls'} in 24 h` : 'Not recalled today'}>
                      <p className="text-[12.5px] leading-relaxed text-dim">
                        {fact.lastUsedAt ? `Last used ${ago(fact.lastUsedAt)}. ` : 'Nothing has used it yet. '}
                        A use is recorded when Ask memory cites it, or a session, a research or the compiler is handed it.
                      </p>
                    </Panel>
                    <Panel eyebrow="Tags" title="Indexed under">
                      {fact.tags.length === 0 ? <p className="text-[12.5px] text-dim">No tags.</p> : (
                        <div className="flex flex-wrap gap-1">
                          {fact.tags.map((t) => <span key={t} className="rounded-xs border border-line bg-surface-2 px-1.5 py-px font-mono text-[11.5px] text-ink-2">{t}</span>)}
                        </div>
                      )}
                    </Panel>
                  </div>

                  <Panel eyebrow="Evidence" title={fact.evidence.length ? `${fact.evidence.length} ${fact.evidence.length === 1 ? 'artefact backs' : 'artefacts back'} this claim` : 'No evidence recorded'} flush>
                    {fact.evidence.length > 0 && (
                      <div className="divide-y divide-line">
                        {fact.evidence.map((e) => (
                          <div key={e} className="flex items-center gap-2 px-3.5 py-1.5">
                            <FileSearch className="size-3 shrink-0 text-dim" />
                            <span className="truncate font-mono text-[12.5px] text-ink-2">{e}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </Panel>

                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" onClick={async () => { if (await setPinned(fact.ref, !fact.pinned)) toast(fact.pinned ? `${fact.ref} unpinned` : `${fact.ref} pinned`, { description: fact.pinned ? undefined : 'Pinned facts come first in every list and search.' }); }}><Pin className="size-3.5" />{fact.pinned ? 'Unpin' : 'Pin'}</Button>
                    <Button size="sm" variant="outline" disabled={live.length < 2} onClick={() => setMarking({ a: fact.ref })}><Split className="size-3.5" />Mark a contradiction</Button>
                    <Button size="sm" variant="destructive" onClick={async () => { if (await archive(fact.ref)) toast(`${fact.ref} archived`, { description: 'Out of recall, never deleted. The record stays recoverable.' }); }}><Archive className="size-3.5" />Archive</Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}

        {tab === 'health' && <Health live={live} conflicts={conflicts.length} />}

        {tab === 'taste' && (
          <Taste projectId={scope === 'global' || !projectId ? null : projectId}
            scopeName={scope === 'global' || !project ? 'the workspace' : project.name} />
        )}

        {tab === 'conflicts' && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <p className="max-w-3xl text-[13px] text-soft">
                Two facts that cannot both be true. Nothing detects this on its own: when you notice two that disagree,
                mark them, and both claims stay intact until you keep one. The other is archived, never deleted.
              </p>
              <Button size="sm" variant="outline" disabled={live.length < 2} onClick={() => setMarking({ a: fact?.ref ?? live[0]?.ref ?? '' })}>
                <Split className="size-3.5" />Mark a contradiction
              </Button>
            </div>
            {conflicts.length === 0 && (
              <Empty icon={<TriangleAlert className="size-6" />} title="No contradictions recorded"
                hint="NeuroCode does not check facts against each other. Mark two facts that disagree, from either fact or from here." />
            )}
            {conflicts.map((c) => (
              <Panel key={c.id} eyebrow={`recorded ${ago(c.detected)}`} title={c.topic} className={c.severity === 'HIGH' ? 'border-danger/35' : undefined}
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
                {c.detail && <p className="mt-2.5 text-[13px] text-soft">{c.detail}</p>}
                <div className="mt-2.5 flex items-center justify-between gap-3 border-t border-line pt-2.5">
                  {c.suggestion
                    ? <p className="flex items-start gap-1.5 text-[12.5px] text-ink-2"><Zap className="mt-px size-3 shrink-0 text-brand" />{c.suggestion}</p>
                    : <span />}
                  <div className="flex shrink-0 gap-1.5">
                    <Button size="xs" variant="outline" onClick={async () => { if (await resolveConflict(c.id, 'a')) toast.success('Kept A', { description: 'B is archived as superseded. Nothing is deleted.' }); }}>Keep A</Button>
                    <Button size="xs" variant="outline" onClick={async () => { if (await resolveConflict(c.id, 'b')) toast.success('Kept B', { description: 'A is archived as superseded. Nothing is deleted.' }); }}>Keep B</Button>
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
      {marking && (
        <MarkContradiction facts={live} first={marking.a} onClose={() => setMarking(null)}
          onFiled={() => { setMarking(null); setTab('conflicts'); }} />
      )}
    </Page>
  );
}

/** Every figure is the server's count over all of memory; recalls are the rows the server writes. */
function Health({ live, conflicts }: { live: MemoryFact[]; conflicts: number }) {
  // Keyed on what changes when a fact streams in or a recall lands, so both read again.
  const hits = useRemote(`recalls:${changed(live)}`, () => memoryApi.hits(50));
  const stats = useRemote(`stats:all:${changed(live)}`, () => memoryApi.stats());
  const s = stats.data;
  const shown = (n: number | undefined) => (s ? (n ?? 0).toLocaleString() : '…');
  const sub = (words: string) => (stats.error ? 'could not be counted' : words);

  const rows = MEMORY_CATEGORIES.flatMap((c) => {
    const n = s?.byCategory[c.id];
    return n && n.held > 0 ? [{ ...c, held: n.held, pinned: n.pinned, recalled: n.recalled24h, last: n.lastUsedAt }] : [];
  });

  return (
    <div className="space-y-4">
      <StatGrid cols={5}>
        <Stat label="Facts held" value={shown(s?.held)} sub={sub(s ? `${s.global.toLocaleString()} in the global brain` : 'counting…')} icon={<Layers className="size-3" />} />
        <Stat label="Pinned" value={shown(s?.pinned)} tone="brand" sub={sub('first in every list')} icon={<Pin className="size-3" />} />
        <Stat label="Recalled 24h" value={shown(s?.recalled24h)} sub={sub('uses by every feature')} icon={<Activity className="size-3" />} />
        <Stat label="Conflicts" value={conflicts} tone={conflicts ? 'danger' : 'neutral'} sub="waiting for a ruling" icon={<TriangleAlert className="size-3" />} />
        <Stat label={`Retired ${s?.retiredDays ?? 30}d`} value={shown(s?.retired)} sub={sub(`archived in the last ${s?.retiredDays ?? 30} days`)} icon={<Archive className="size-3" />} />
      </StatGrid>

      <Panel eyebrow="What memory holds, and what gets used" title="By category" flush>
        {stats.error ? <Empty title="The counts did not load" hint={stats.error} action={<Button size="sm" variant="outline" onClick={stats.reload}>Try again</Button>} />
          : !s ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Counting…" />
          : rows.length === 0 ? <Empty title="Nothing remembered yet" hint="Add facts from text, or answer a plan's open question." /> : (
          <DataTable head={['Category', 'Held', 'Pinned', 'Recalled 24h', 'Last used']}>
            {rows.map((r) => (
              <Row key={r.id}>
                <Cell className="font-medium text-ink">{r.label}</Cell>
                <Cell className="tnum">{r.held}</Cell>
                <Cell className="tnum">{r.pinned}</Cell>
                <Cell className="tnum">{r.recalled}</Cell>
                <Cell className="text-dim">{r.last ? ago(r.last) : 'not yet'}</Cell>
              </Row>
            ))}
          </DataTable>
        )}
      </Panel>

      <Panel eyebrow="Newest first" title="Recent recalls" flush>
        {hits.error ? <Empty title="Recalls did not load" hint={hits.error} action={<Button size="sm" variant="outline" onClick={hits.reload}>Try again</Button>} />
          : !hits.data ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading recalls…" />
            : hits.data.length === 0 ? (
              <Empty title="No fact has been recalled yet"
                hint="When Ask memory cites a fact, or a session, a research or the compiler is handed one, it is listed here." />
            ) : (
              <DataTable head={['Fact', 'Used by', 'For', 'When']}>
                {hits.data.map((h, i) => (
                  <Row key={`${h.ref}-${h.at}-${i}`}>
                    <Cell><Mono tone="brand">{h.ref}</Mono><span className="mt-0.5 block max-w-[420px] truncate text-[12.5px] text-soft">{h.title}</span></Cell>
                    <Cell className="text-[12.5px]">{RECALLED_BY[h.feature]}</Cell>
                    <Cell>{h.context ? <Mono>{h.context}</Mono> : <span className="text-[12.5px] text-dim">a question</span>}</Cell>
                    <Cell className="text-dim">{ago(h.at)}</Cell>
                  </Row>
                ))}
              </DataTable>
            )}
      </Panel>
    </div>
  );
}

/** A person says two facts cannot both be true. Nothing is archived until someone keeps one of them. */
function MarkContradiction({ facts, first, onClose, onFiled }: {
  facts: MemoryFact[]; first: string; onClose: () => void; onFiled: () => void;
}) {
  const { fileConflict } = useData();
  const [a, setA] = useState(first);
  const [b, setB] = useState(() => facts.find((f) => f.ref !== first)?.ref ?? '');
  const [topic, setTopic] = useState('');
  const [detail, setDetail] = useState('');
  const [severity, setSeverity] = useState<ConflictInput['severity']>('medium');
  const [busy, setBusy] = useState(false);
  const options = facts.map((f) => ({ value: f.ref, label: `${f.ref} · ${f.title.length > 70 ? `${f.title.slice(0, 69)}…` : f.title}` }));
  const blocker = !a || !b ? 'Pick two facts.' : a === b ? 'Pick two different facts.' : !topic.trim() ? 'Say what they disagree about.' : '';

  const save = async () => {
    if (blocker || busy) return;
    setBusy(true);
    const doc = await fileConflict({ a, b, topic: topic.trim(), detail: detail.trim(), severity });
    setBusy(false);
    if (!doc) return;
    toast.success('Contradiction recorded', { description: `${a} and ${b} stay as they are until you keep one.` });
    onFiled();
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>Mark a contradiction</DialogTitle>
          <DialogDescription>Two facts that cannot both be true. Both stay in memory until you keep one; the other is then archived.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <SelectField label="Claim A" value={a} onChange={setA} options={options} />
          <SelectField label="Claim B" value={b} onChange={setB} options={options} />
          <Field label="What they disagree about" value={topic} onChange={setTopic} placeholder="e.g. Where invoice totals are rounded" autoFocus />
          <label className="grid gap-1.5">
            <span className="text-[12.5px] font-medium text-ink-2">Detail</span>
            <textarea value={detail} onChange={(e) => setDetail(e.target.value)} rows={3} maxLength={2000}
              placeholder="What each one says, and what depends on the answer."
              className="focus-brand w-full resize-none rounded-lg border border-line bg-surface-2/60 p-3 text-[13.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:outline-none" />
          </label>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[12.5px] font-medium text-ink-2">Severity</span>
            <Segmented options={[{ id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }]} value={severity} onChange={setSeverity} />
          </div>
        </div>
        <DialogFooter>
          {blocker && <span className="mr-auto self-center text-[12.5px] text-dim">{blocker}</span>}
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} disabled={!!blocker || busy}>{busy && <Loader2 className="size-3.5 animate-spin" />}Record it</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Paste notes; the facts worth keeping come back as candidates, and only the ones you tick are stored. */
function AddFromText({ open, onOpenChange, projectId, onAdded }: {
  open: boolean; onOpenChange: (open: boolean) => void; projectId: string | null; onAdded: (facts: MemoryFact[]) => void;
}) {
  const { extract, addFacts } = useData();
  const [text, setText] = useState('');
  const [found, setFound] = useState<Extracted | null>(null);
  const [skip, setSkip] = useState<Set<number>>(() => new Set());
  const [chosen, setScope] = useState<'project' | 'global'>('project');
  // With no project there is only the workspace to file a fact under.
  const scope = projectId ? chosen : 'global';
  const [busy, setBusy] = useState(false);
  const picked = found ? found.facts.filter((_, i) => !skip.has(i)) : [];

  const close = () => { onOpenChange(false); setText(''); setFound(null); setSkip(new Set()); };
  const find = async () => {
    if (text.trim().length < 10 || busy) return;
    setBusy(true);
    const r = await extract(text.trim(), projectId ?? undefined);
    setBusy(false);
    if (r) { setFound(r); setSkip(new Set()); }
  };
  const save = async () => {
    if (!picked.length || busy) return;
    setBusy(true);
    const docs = await addFacts(scope === 'global' ? null : projectId, picked);
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
            Paste meeting notes, a requirement or a chat, in any language. The facts worth keeping come back, and you choose which to store.
          </DialogDescription>
        </DialogHeader>
        {!found ? (
          <textarea
            value={text} onChange={(e) => setText(e.target.value)} rows={9} autoFocus aria-label="Text to read"
            placeholder={'Paste meeting notes, a decision log or a review.\nLines like "We decided …" or "X must never …" become candidate facts.'}
            className="focus-brand w-full resize-none rounded-lg border border-line bg-surface-2/60 p-3 text-[13.5px] leading-relaxed text-ink placeholder:text-dim focus-visible:outline-none"
          />
        ) : found.facts.length === 0 ? (
          <Empty
            icon={<FileSearch className="size-6" />} title="No fact worth keeping was found"
            hint={found.provider === 'rules'
              ? 'The offline rules keep sentences that state a rule or a decision: must, never, always, decided… A model reads more; set one in Admin → AI providers.'
              : 'Nothing in this text looks durable enough to remember.'}
          />
        ) : (
          <div className="grid gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-dim">
              <span>{found.facts.length} found · {found.provider === 'rules' ? 'offline rules' : found.model}</span>
              {projectId && <Segmented options={[{ id: 'project', label: 'This project' }, { id: 'global', label: 'Global' }]} value={scope} onChange={setScope} />}
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

/* ── Taste ──────────────────────────────────────────────────────── */

const KIND_TONE: Record<TasteKind, 'ok' | 'danger' | 'warn' | 'info' | 'violet'> = {
  accept: 'ok', refuse: 'danger', rework_note: 'warn', edit_delta: 'info', plan_edit: 'violet',
};
const reasonOf = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

/** How this team likes the work done: rules learnt from its own decisions, each adopted by a person before any model sees it. */
function Taste({ projectId, scopeName }: { projectId: string | null; scopeName: string }) {
  const { can } = useAuth();
  const mayWrite = can('memory:write');
  const scopeKey = projectId ?? 'workspace';
  const rules = useRemote(`taste:${scopeKey}`, () => tasteApi.rules(projectId));
  const signals = useRemote(`taste-signals:${scopeKey}`, () => tasteApi.signals(projectId, 30));
  const [learning, setLearning] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [evidenceOf, setEvidenceOf] = useState<TasteRule | null>(null);
  const [showRetired, setShowRetired] = useState(false);

  const items = rules.data?.items ?? [];
  const proposed = items.filter((r) => r.status === 'proposed');
  const active = items.filter((r) => r.status === 'active');
  const retired = items.filter((r) => r.status === 'retired');
  const counts = rules.data?.signals;
  const reload = () => { rules.reload(); signals.reload(); };

  const learn = async () => {
    setLearning(true);
    try {
      const done = await tasteApi.learn(projectId);
      const n = done.proposed.length;
      toast.success(n ? `${n} rule${n === 1 ? '' : 's'} proposed` : 'Nothing new came of them', {
        description: `Read ${done.read} signal${done.read === 1 ? '' : 's'}${done.harvested ? `, ${done.harvested} gathered from recent decisions` : ''} · ${done.model}.`
          + (n ? ' Adopt the ones that are true; nothing reaches a model until you do.' : ''),
      });
      reload();
    } catch (e) {
      toast.error('Nothing learnt', { description: reasonOf(e) });
    } finally {
      setLearning(false);
    }
  };

  const change = async (rule: TasteRule, next: { text?: string; status?: 'active' | 'retired' }, said: string) => {
    setBusy(rule.id);
    try {
      await tasteApi.change(rule.id, next);
      toast(said, { description: rule.ref });
      setEditing(null);
      rules.reload();
    } catch (e) {
      toast.error('Not changed', { description: reasonOf(e) });
    } finally {
      setBusy(null);
    }
  };

  const exportMd = () => {
    const blob = new Blob([tasteMarkdown(items, scopeName)], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'taste.md';
    a.click();
    URL.revokeObjectURL(url);
  };

  const meta = (r: TasteRule) => (
    <span className="text-[11.5px] text-dim">
      <span className="text-ok">{r.support} for</span> · <span className={r.contradict ? 'text-warn' : undefined}>{r.contradict} against</span>
      {r.projectId === null && ' · workspace'}
      {r.status === 'active' && r.adoptedBy ? ` · adopted by ${r.adoptedBy}${r.adoptedAt ? ` ${ago(r.adoptedAt)}` : ''}` : ''}
      {r.status === 'proposed' && r.proposedBy ? ` · proposed by ${r.proposedBy} ${ago(r.createdAt)}` : ''}
    </span>
  );

  const words = (r: TasteRule) => editing?.id === r.id ? (
    <form className="mt-1 space-y-1.5" onSubmit={(e) => { e.preventDefault(); void change(r, { text: editing.text }, 'Rule reworded'); }}>
      <textarea autoFocus rows={2} maxLength={300} value={editing.text} aria-label={`Reword ${r.ref}`}
        onChange={(e) => setEditing({ id: r.id, text: e.target.value })}
        className="w-full resize-y rounded-sm border border-line bg-base px-2.5 py-1.5 text-[13px] text-ink focus-visible:border-brand focus-visible:outline-none" />
      <div className="flex gap-1.5">
        <Button size="xs" type="submit" disabled={busy === r.id || editing.text.trim().length < 8}><Check />Save</Button>
        <Button size="xs" type="button" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
      </div>
    </form>
  ) : <p className="text-[13.5px] leading-snug text-ink">{r.text}</p>;

  const evidenceButton = (r: TasteRule) => (
    <Button size="xs" variant="ghost" onClick={() => setEvidenceOf(r)}><Scale />Evidence ({r.evidence})</Button>
  );

  if (rules.loading && !rules.data) {
    return <p className="flex items-center gap-2 p-2 text-[13px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading what the team has decided…</p>;
  }
  if (rules.error && !rules.data) {
    return (
      <Empty icon={<TriangleAlert className="size-6" />} title="Taste did not load" hint={rules.error}
        action={<Button size="sm" variant="outline" onClick={reload}>Try again</Button>} />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-[13px] text-soft">
          How {scopeName === 'the workspace' ? 'this workspace' : scopeName} likes the work done, learnt from what people did: runs accepted or refused
          beside what the review found, runs sent back with notes, plans reshaped before dispatch, and their own commits on an agent’s branch.
          A model proposes rules from those signals; a rule reaches the compiler, the agents and the reviewer only once someone adopts it.
        </p>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={active.length === 0} onClick={exportMd}
            title={active.length ? 'The active rules as a Markdown file' : 'Adopt a rule first'}>
            <Download className="size-3.5" />Export taste.md
          </Button>
          <Button size="sm" disabled={!mayWrite || learning} onClick={learn}
            title={mayWrite ? 'Gathers recent decisions, then asks a model to propose rules from the unread signals' : 'Needs the memory:write permission'}>
            {learning ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
            {learning ? 'Learning…' : 'Learn from my decisions'}
          </Button>
        </div>
      </div>

      <StatGrid cols={4}>
        <Stat label="Signals not read yet" value={counts ? counts.unread.toLocaleString() : '…'}
          sub={counts ? `${counts.total.toLocaleString()} kept in all` : undefined} tone={counts?.unread ? 'brand' : undefined} />
        <Stat label="Proposed" value={rules.data?.counts.proposed ?? 0} sub="waiting for your word" tone={proposed.length ? 'warn' : undefined} />
        <Stat label="Active" value={rules.data?.counts.active ?? 0} sub="handed to every model" tone={active.length ? 'ok' : undefined} />
        <Stat label="Retired" value={rules.data?.counts.retired ?? 0} sub="rejected or switched off" />
      </StatGrid>

      <Panel eyebrow={proposed.length ? 'Adopt what is true, reject what is not' : 'From the last time it learnt'}
        title={`Proposed (${proposed.length})`} flush>
        {proposed.length === 0 ? (
          <p className="px-5 py-3 text-[13px] text-dim">
            {counts?.total
              ? 'Nothing is waiting. Learn from your decisions to read the signals that came in since.'
              : 'Nothing yet. Signals arrive as you accept, refuse or send back runs, shape plans, and commit on a run’s branch; then Learn from my decisions.'}
          </p>
        ) : (
          <div className="divide-y divide-line">
            {proposed.map((r) => (
              <div key={r.id} className="px-5 py-3">
                <div className="flex flex-wrap items-center gap-2"><Mono>{r.ref}</Mono>{meta(r)}</div>
                <div className="mt-1">{words(r)}</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <Button size="xs" disabled={!mayWrite || busy === r.id} onClick={() => void change(r, { status: 'active' }, 'Rule adopted')}><Check />Adopt</Button>
                  <Button size="xs" variant="ghost" disabled={!mayWrite || busy === r.id} onClick={() => setEditing({ id: r.id, text: r.text })}><Pencil />Edit</Button>
                  <Button size="xs" variant="ghost" disabled={!mayWrite || busy === r.id} onClick={() => void change(r, { status: 'retired' }, 'Rule rejected')}><X />Reject</Button>
                  {evidenceButton(r)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel eyebrow="Handed to the compiler, the agents and the reviewer after the project's instruction files"
        title={`Active (${active.length})`} flush>
        {active.length === 0 ? (
          <p className="px-5 py-3 text-[13px] text-dim">No rule is active. Adopt a proposed one, and plans record it under “Taste applied”.</p>
        ) : (
          <div className="divide-y divide-line">
            {active.map((r) => (
              <div key={r.id} className="flex items-start gap-3 px-5 py-3">
                <Switch className="mt-1" checked disabled={!mayWrite || busy === r.id} aria-label={`Switch off ${r.ref}`}
                  onCheckedChange={(on) => { if (!on) void change(r, { status: 'retired' }, 'Rule switched off'); }} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2"><Mono tone="brand">{r.ref}</Mono>{meta(r)}</div>
                  <div className="mt-1">{words(r)}</div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <Button size="xs" variant="ghost" disabled={!mayWrite || busy === r.id} onClick={() => setEditing({ id: r.id, text: r.text })}><Pencil />Edit</Button>
                    {evidenceButton(r)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
        {retired.length > 0 && (
          <>
            <button type="button" onClick={() => setShowRetired(!showRetired)}
              className="flex w-full items-center gap-1.5 border-t border-line px-5 py-2 text-left text-[12px] text-dim hover:text-ink-2">
              {showRetired ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}{retired.length} retired
            </button>
            {showRetired && (
              <div className="divide-y divide-line border-t border-line">
                {retired.map((r) => (
                  <div key={r.id} className="flex items-start gap-3 px-5 py-2.5">
                    <Switch className="mt-1" checked={false} disabled={!mayWrite || busy === r.id} aria-label={`Switch on ${r.ref}`}
                      onCheckedChange={(on) => { if (on) void change(r, { status: 'active' }, 'Rule switched on'); }} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2"><Mono>{r.ref}</Mono>{meta(r)}</div>
                      <p className="mt-0.5 text-[13px] text-soft">{r.text}</p>
                    </div>
                    {evidenceButton(r)}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel eyebrow="The moments it learns from, newest first" title="Signals" flush>
        {signals.error ? (
          <div className="flex items-center gap-2 px-5 py-3 text-[13px] text-danger">
            <span className="min-w-0 flex-1">The signals did not load: {signals.error}</span>
            <Button size="xs" variant="outline" onClick={signals.reload}>Try again</Button>
          </div>
        ) : !signals.data ? (
          <p className="flex items-center gap-2 px-5 py-3 text-[13px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the signals…</p>
        ) : signals.data.length === 0 ? (
          <p className="px-5 py-3 text-[13px] text-dim">
            None kept yet. Accepting or refusing a run, sending one back with notes, editing a plan’s steps and your own commits on a
            run’s branch before it merges are each kept as a signal. Decisions already made are gathered when you press Learn.
          </p>
        ) : (
          <div className="divide-y divide-line">
            {signals.data.map((sig) => <SignalRow key={sig.id} signal={sig} />)}
          </div>
        )}
      </Panel>

      <Evidence rule={evidenceOf} onClose={() => setEvidenceOf(null)} />
    </div>
  );
}

function SignalRow({ signal }: { signal: TasteSignal }) {
  return (
    <div className="flex items-start gap-2.5 px-5 py-2">
      <span className="w-32 shrink-0"><Tag tone={KIND_TONE[signal.kind]}>{SIGNAL_LABEL[signal.kind]}</Tag></span>
      <span className="min-w-0 flex-1 text-[12.5px] text-ink-2 [overflow-wrap:anywhere]">{signal.summary}</span>
      <span className="shrink-0 text-right text-[11.5px] text-dim">
        {signal.stance ? <span className={signal.stance === 'for' ? 'text-ok' : 'text-warn'}>{signal.stance} · </span> : null}
        {signal.distilled ? '' : 'unread · '}{ago(signal.at)}
      </span>
    </div>
  );
}

/** The drawer: every signal a rule cites, for it or against it, with what each was built from. */
function Evidence({ rule, onClose }: { rule: TasteRule | null; onClose: () => void }) {
  const found = useRemote(rule ? `taste-evidence:${rule.id}:${rule.updatedAt}` : null, () => tasteApi.evidence(rule?.id ?? 0));
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Sheet open={!!rule} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:w-[560px] sm:max-w-none">
        {rule && (
          <>
            <SheetHeader className="border-b border-line px-5 pt-5 pb-4">
              <Mono tone="brand">{rule.ref}</Mono>
              <SheetTitle className="mt-2 text-[15px] text-ink">{rule.text}</SheetTitle>
              <SheetDescription className="text-[12.5px] text-soft">
                {rule.support} signal{rule.support === 1 ? '' : 's'} for it, {rule.contradict} against — counted from the signals it cites, not taken from the model.
              </SheetDescription>
            </SheetHeader>
            <div className="divide-y divide-line">
              {found.loading && <p className="flex items-center gap-2 px-5 py-3 text-[13px] text-dim"><Loader2 className="size-3.5 animate-spin" />Reading the evidence…</p>}
              {found.error && <p className="px-5 py-3 text-[13px] text-danger">The evidence did not load: {found.error}</p>}
              {found.data && found.data.signals.length === 0 && (
                <p className="px-5 py-3 text-[13px] text-dim">The signals it cited are gone: their project was removed.</p>
              )}
              {found.data?.signals.map((sig) => (
                <div key={sig.id}>
                  <button type="button" className="w-full text-left hover:bg-surface-2/50" onClick={() => setOpen(open === sig.id ? null : sig.id)}>
                    <SignalRow signal={sig} />
                  </button>
                  {open === sig.id && (
                    <pre className="mx-5 mb-3 max-h-72 overflow-auto rounded-sm border border-line bg-base p-2.5 font-mono text-[11.5px] whitespace-pre-wrap text-ink-2">
                      {typeof sig.payload.patch === 'string' ? sig.payload.patch : JSON.stringify(sig.payload, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
