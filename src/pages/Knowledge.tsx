import { useMemo, useState } from 'react';
import { Search, Library, Upload, Link2, Boxes } from 'lucide-react';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Field, ListRow,
  DataTable, Row, Cell, Stat, StatGrid, KV, Empty, SectionTitle, Bar,
} from '@/components/os';
import { knowledgeDocs, docKinds, retrievalDemo, ingestPipeline, accepted } from '@/mock/knowledge';
import { projectName } from '@/mock/projects';
import { cn } from '@/lib/utils';

const VIA_TONE = { vector: 'brand', keyword: 'warn', graph: 'violet' } as const;

export default function Knowledge() {
  const [kind, setKind] = useState('all');
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(knowledgeDocs[0].id);
  const [search, setSearch] = useState('');

  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return knowledgeDocs.filter((d) =>
      (kind === 'all' || d.kind === kind) &&
      (!t || (d.ref + d.title + d.summary + d.entities.join(' ')).toLowerCase().includes(t)));
  }, [kind, q]);

  const d = useMemo(() => list.find((x) => x.id === sel) ?? list[0] ?? knowledgeDocs[0], [list, sel]);
  const showRetrieval = search.trim().length > 2;
  const results = useMemo(() => {
    const t = search.trim().toLowerCase();
    return retrievalDemo.results.filter((r) => !t || `${r.ref} ${r.title} ${r.why}`.toLowerCase().includes(t) || 'customer invoice tax'.includes(t));
  }, [search]);

  return (
    <Page>
      <PageHeader
        title="Knowledge"
        subtitle="Anything can be thrown at it — a PDF, a screenshot, a voice note, a schema. It is classified, chunked, linked to the parsed graph, and made retrievable three different ways."
      >
        <div className="pb-3">
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3 transition-colors">
            <Search className="size-4 shrink-0 text-brand" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Ask the corpus — try “customer invoice tax”"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none" />
            {search && <button onClick={() => setSearch('')} className="text-[11px] text-dim hover:text-ink">clear</button>}
          </div>
        </div>
      </PageHeader>

      <PageBody className="space-y-4">
        {showRetrieval && (
          <Panel className="accent-top" eyebrow={`hybrid retrieval · ${results.length} results`} title={<>Results for <span className="text-brand">“{search}”</span></>} flush>
            <DataTable head={['Result', 'Score', 'Found by', 'Why it ranked']}>
              {results.map((r) => (
                <Row key={r.ref}>
                  <Cell className="font-medium text-ink">{r.title}<Mono className="mt-0.5 block w-fit">{r.ref}</Mono></Cell>
                  <Cell><span className="flex items-center gap-2"><Bar className="w-16" pct={r.score * 100} /><span className="tnum text-[11px]">{r.score.toFixed(2)}</span></span></Cell>
                  <Cell><Tag tone={VIA_TONE[r.via as keyof typeof VIA_TONE]}>{r.via}</Tag></Cell>
                  <Cell className="max-w-[460px] text-[11.5px] text-soft">{r.why}</Cell>
                </Row>
              ))}
            </DataTable>
            <div className="grid grid-cols-1 gap-x-6 border-t border-line px-3.5 py-2.5 md:grid-cols-2">
              {retrievalDemo.retrievers.map((r) => (
                <div key={r.name} className="flex items-start gap-2 py-1">
                  <span className="tnum w-8 shrink-0 text-right text-[11px] text-brand">{r.hits}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[11.5px] text-ink-2">{r.name}</span>
                    <span className="block text-[10.5px] text-dim">{r.note}</span>
                  </span>
                </div>
              ))}
            </div>
          </Panel>
        )}

        <StatGrid cols={5}>
          <Stat label="Documents" value={knowledgeDocs.length} sub={`${knowledgeDocs.filter((x) => !x.indexed).length} awaiting index`} icon={<Library className="size-3" />} />
          <Stat label="Chunks" value={knowledgeDocs.reduce((n, x) => n + x.chunks, 0).toLocaleString()} sub="BGE-M3 · 1024 dims" />
          <Stat label="Entities linked" value={new Set(knowledgeDocs.flatMap((x) => x.entities)).size} tone="brand" sub="into the parsed graph" icon={<Link2 className="size-3" />} />
          <Stat label="Kinds accepted" value={accepted.length} sub="anything you can hand it" />
          <Stat label="Sources today" value={knowledgeDocs.filter((x) => x.addedAt.includes('min') || x.addedAt.includes('h ago') || x.addedAt.includes('today')).length} tone="ok" />
        </StatGrid>

        <div className="flex min-h-[520px] gap-3">
          {/* Kind rail */}
          <div className="w-44 shrink-0 rounded-md border border-line bg-surface py-2">
            <button onClick={() => setKind('all')}
              className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[12px]',
                kind === 'all' ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}>
              <span className="flex items-center gap-2"><Boxes className="size-3.5 text-dim" />All</span>
              <span className="tnum text-[11px] text-dim">{knowledgeDocs.length}</span>
            </button>
            <div className="mx-3.5 my-1.5 h-px bg-line" />
            {docKinds.map((k) => (
              <button key={k} onClick={() => { setKind(k); setSel(''); }}
                className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[12px] capitalize',
                  kind === k ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}>
                <span className="truncate">{k}</span>
                <span className="tnum text-[11px] text-dim">{knowledgeDocs.filter((x) => x.kind === k).length}</span>
              </button>
            ))}
          </div>

          {/* Doc list */}
          <div className="no-scrollbar w-[330px] shrink-0 overflow-y-auto rounded-md border border-line bg-surface">
            <div className="border-b border-line p-2.5">
              <Field value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Filter documents…" onClear={() => setQ('')} />
            </div>
            {list.length === 0 ? <Empty title="Nothing here" /> : list.map((x) => (
              <ListRow key={x.id} active={x.id === d.id} onClick={() => setSel(x.id)}>
                <div className="flex items-center gap-2">
                  <Dot state={x.indexed ? 'ok' : 'warn'} />
                  <Mono>{x.ref}</Mono>
                  <span className="eyebrow ml-auto">{x.kind}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-[12px] text-ink">{x.title}</p>
                <p className="mt-1 truncate text-[10.5px] text-dim">{x.source} · {x.addedAt}</p>
              </ListRow>
            ))}
          </div>

          {/* Detail */}
          <div className="min-w-0 flex-1 space-y-3 overflow-y-auto">
            <Panel eyebrow={`${d.kind} · ${projectName(d.projectId)} · added ${d.addedAt}`}
              title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{d.ref}</Mono>{d.title}</span>}
              actions={d.indexed ? <Tag tone="ok">indexed</Tag> : <Tag tone="warn">awaiting index</Tag>}>
              <p className="text-[12.5px] leading-relaxed text-ink-2">{d.summary}</p>
              <div className="mt-3 grid grid-cols-2 gap-x-6 border-t border-line pt-2.5 md:grid-cols-4">
                <KV k="Source" v={d.source} />
                <KV k="Size" v={d.size} />
                <KV k="Chunks" v={d.chunks.toLocaleString()} />
                <KV k="Embedding" v="BGE-M3" mono />
              </div>
            </Panel>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <Panel eyebrow={`${d.entities.length} extracted`} title="Entities">
                <div className="flex flex-wrap gap-1">{d.entities.map((e) => <Mono key={e}>{e}</Mono>)}</div>
              </Panel>
              <Panel eyebrow={`${d.linkedTo.length} links`} title="Connected to">
                <div className="flex flex-wrap gap-1">{d.linkedTo.map((l) => <Tag key={l} tone="brand">{l}</Tag>)}</div>
              </Panel>
            </div>

            <Panel eyebrow="What happens to anything you drop in" title={<span className="flex items-center gap-1.5"><Upload className="size-3.5 text-brand" />Ingestion</span>} flush>
              <div className="divide-y divide-line">
                {ingestPipeline.map((s) => (
                  <div key={s.n} className="flex items-start gap-2.5 px-3.5 py-2">
                    <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[10.5px] text-dim">{s.n}</span>
                    <span className="min-w-0 flex-1">
                      <span className="text-[12px] font-medium text-ink">{s.step}</span>
                      <span className="block text-[11.5px] text-dim">{s.detail}</span>
                    </span>
                  </div>
                ))}
              </div>
              <div className="border-t border-line px-3.5 py-2.5">
                <SectionTitle>Accepted inputs</SectionTitle>
                <div className="flex flex-wrap gap-1">{accepted.map((a) => <Tag key={a} tone="neutral">{a}</Tag>)}</div>
              </div>
            </Panel>
          </div>
        </div>
      </PageBody>
    </Page>
  );
}
