import { useEffect, useMemo, useState } from 'react';
import { Search, Library, Upload, Link2, Boxes, Loader2, RefreshCw, FileText } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Field, ListRow,
  DataTable, Row, Cell, Stat, StatGrid, KV, Empty, SectionTitle, Bar,
} from '@/components/os';
import { knowledgeDocs, docKinds, retrievalDemo, ingestPipeline, accepted } from '@/mock/knowledge';
import { projectName } from '@/mock/projects';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { knowledgeApi, type LiveDocs } from '@/lib/live/knowledge';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';
import { ago, bytes } from './code/format';

const VIA_TONE = { vector: 'brand', keyword: 'warn', graph: 'violet' } as const;

/** An onboarded project shows the documents retrieval really holds; a sample project (and the demo) shows the worked example. */
export default function Knowledge() {
  const { mode } = useData();
  const { project } = useProject();
  if (mode === 'live' && project.source) return <LiveKnowledge project={project} />;
  return <SampleKnowledge note={mode === 'live' ? `${project.name} is a sample project, so this is a worked example. Onboard a repository in Projects to see its own documents.` : undefined} />;
}

function SampleKnowledge({ note }: { note?: string }) {
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
          {note && <p className="pb-2 text-[12.5px] text-warn">{note}</p>}
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3 transition-colors">
            <Search className="size-4 shrink-0 text-brand" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Ask the corpus — try “customer invoice tax”"
              className="min-w-0 flex-1 bg-transparent text-[14px] text-ink placeholder:text-dim focus-visible:outline-none" />
            {search && <button onClick={() => setSearch('')} className="text-[12px] text-dim hover:text-ink">clear</button>}
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
                  <Cell><span className="flex items-center gap-2"><Bar className="w-16" pct={r.score * 100} /><span className="tnum text-[12px]">{r.score.toFixed(2)}</span></span></Cell>
                  <Cell><Tag tone={VIA_TONE[r.via as keyof typeof VIA_TONE]}>{r.via}</Tag></Cell>
                  <Cell className="max-w-[460px] text-[12.5px] text-soft">{r.why}</Cell>
                </Row>
              ))}
            </DataTable>
            <div className="grid grid-cols-1 gap-x-6 border-t border-line px-3.5 py-2.5 md:grid-cols-2">
              {retrievalDemo.retrievers.map((r) => (
                <div key={r.name} className="flex items-start gap-2 py-1">
                  <span className="tnum w-8 shrink-0 text-right text-[12px] text-brand">{r.hits}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[12.5px] text-ink-2">{r.name}</span>
                    <span className="block text-[11.5px] text-dim">{r.note}</span>
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

        <div className="flex min-h-[520px] flex-col gap-3 lg:flex-row">
          {/* Kind rail */}
          <div className="max-h-[30vh] w-full shrink-0 overflow-y-auto rounded-md border border-line bg-surface py-2 lg:max-h-none lg:w-44">
            <button onClick={() => setKind('all')}
              className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[13px]',
                kind === 'all' ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}>
              <span className="flex items-center gap-2"><Boxes className="size-3.5 text-dim" />All</span>
              <span className="tnum text-[12px] text-dim">{knowledgeDocs.length}</span>
            </button>
            <div className="mx-3.5 my-1.5 h-px bg-line" />
            {docKinds.map((k) => (
              <button key={k} onClick={() => { setKind(k); setSel(''); }}
                className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[13px] capitalize',
                  kind === k ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}>
                <span className="truncate">{k}</span>
                <span className="tnum text-[12px] text-dim">{knowledgeDocs.filter((x) => x.kind === k).length}</span>
              </button>
            ))}
          </div>

          {/* Doc list */}
          <div className="no-scrollbar w-full shrink-0 max-h-[42vh] lg:max-h-none lg:w-[330px] overflow-y-auto rounded-md border border-line bg-surface">
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
                <p className="mt-1 line-clamp-2 text-[13px] text-ink">{x.title}</p>
                <p className="mt-1 truncate text-[11.5px] text-dim">{x.source} · {x.addedAt}</p>
              </ListRow>
            ))}
          </div>

          {/* Detail */}
          <div className="min-w-0 flex-1 space-y-3 overflow-y-auto">
            <Panel eyebrow={`${d.kind} · ${projectName(d.projectId)} · added ${d.addedAt}`}
              title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{d.ref}</Mono>{d.title}</span>}
              actions={d.indexed ? <Tag tone="ok">indexed</Tag> : <Tag tone="warn">awaiting index</Tag>}>
              <p className="text-[13.5px] leading-relaxed text-ink-2">{d.summary}</p>
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 border-t border-line pt-2.5 xl:grid-cols-4">
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
                    <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{s.n}</span>
                    <span className="min-w-0 flex-1">
                      <span className="text-[13px] font-medium text-ink">{s.step}</span>
                      <span className="block text-[12.5px] text-dim">{s.detail}</span>
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

/* ═══════════════════════════════════════════════════════════════
   Live: the repository's own writing, as retrieval really holds it.
   ═══════════════════════════════════════════════════════════════ */

const HOW = {
  lexical: { label: 'words', tone: 'info', why: 'matched the words' },
  semantic: { label: 'meaning', tone: 'brand', why: 'close in meaning' },
  both: { label: 'both', tone: 'ok', why: 'matched both' },
} as const;

function LiveKnowledge({ project }: { project: Project }) {
  const { activity } = useData();
  const { can } = useAuth();
  // A rebuild ends with a "Retrieval ready" (or "failed") event on the stream: that is what reloads the list.
  const built = activity.find((e) => e.projectId === project.id && (e.action === 'Retrieval ready' || e.action === 'Retrieval failed'))?.id ?? 'none';
  const stamp = `${project.codeIndex?.at ?? 'none'}:${built}`;
  const d = useRemote(`${project.id}:docs:${stamp}`, () => knowledgeApi.docs(project.id));
  const [askedAt, setAskedAt] = useState<string | null>(null);
  const building = askedAt === stamp;

  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    const id = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);
  const asking = query.length > 2;
  const r = useRemote(asking ? `${project.id}:ask:${stamp}:${query}` : null, () => knowledgeApi.search(project.id, query));

  const [kind, setKind] = useState('all');
  const [q, setQ] = useState('');
  const [sel, setSel] = useState<string | null>(null);

  const rebuild = async () => {
    setAskedAt(stamp);
    try {
      await api.code.buildRetrieval(project.id);
      toast('Re-indexing the documents', { description: 'This list refreshes on its own when retrieval is ready.' });
    } catch (e) {
      setAskedAt(null);
      toast.error('Not re-indexed', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    }
  };

  const data = d.data;
  const changed = data ? data.stale + data.notIndexed : 0;
  return (
    <Page>
      <PageHeader
        title="Knowledge"
        subtitle={`What ${project.name}'s repository says about itself — README, docs, notes — split at its headings and found by its words, and by meaning when a lane embeds it.`}
        actions={can('projects:onboard') && (
          <Button size="sm" variant="outline" disabled={building} onClick={() => void rebuild()}>
            {building ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}{building ? 'Re-indexing…' : 'Re-index documents'}
          </Button>
        )}
      >
        <div className="pb-3">
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3 transition-colors">
            <Search className="size-4 shrink-0 text-brand" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Ask the corpus"
              placeholder="Ask the corpus — words always, meaning when a lane embeds"
              className="min-w-0 flex-1 bg-transparent text-[14px] text-ink placeholder:text-dim focus-visible:outline-none" />
            {search && <button onClick={() => setSearch('')} className="text-[12px] text-dim hover:text-ink">clear</button>}
          </div>
        </div>
      </PageHeader>

      {d.error && !data ? (
        <PageBody><Empty title="The documents did not load" hint={d.error} action={<Button size="sm" variant="outline" onClick={d.reload}>Try again</Button>} /></PageBody>
      ) : !data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Reading the documents…" /></PageBody>
      ) : (
        <PageBody className="space-y-4">
          {asking && <SearchResults query={query} loading={r.loading} error={r.error} data={r.data} onOpen={(path) => setSel(path)} />}

          <StatGrid cols={5}>
            <Stat label="Documents" value={data.docs.length} sub={`${data.notIndexed} awaiting index`} icon={<Library className="size-3" />} />
            <Stat label="Chunks" value={data.docs.reduce((n, x) => n + x.chunks, 0).toLocaleString()}
              sub={data.retrieval.semantic ? `${data.retrieval.model}${data.retrieval.lane ? ` · ${data.retrieval.lane}` : ''}` : 'words only, no embedding lane'} />
            <Stat label="Entities linked" value={data.entitiesLinked} tone={data.entitiesLinked ? 'brand' : 'neutral'} sub="names the code index declares" icon={<Link2 className="size-3" />} />
            <Stat label="Formats read" value={data.formats.length} sub={data.formats.map((f) => `.${f}`).join(' ')} />
            <Stat label="Changed since index" value={changed} tone={changed ? 'warn' : 'ok'}
              sub={data.retrieval.at ? `index built ${ago(data.retrieval.at)}` : 'never indexed'}
              onClick={changed && can('projects:onboard') && !building ? () => void rebuild() : undefined} />
          </StatGrid>

          {data.docs.length === 0 ? (
            <Empty icon={<FileText className="size-6" />} title="No documents in this repository"
              hint={`Nothing ending ${data.formats.map((f) => `.${f}`).join(', ')} was found outside the folders onboarding skips.`} />
          ) : (
            <LiveDocBrowser project={project} data={data} kind={kind} setKind={setKind} q={q} setQ={setQ} sel={sel} setSel={setSel} stamp={stamp} />
          )}
        </PageBody>
      )}
    </Page>
  );
}

function SearchResults({ query, loading, error, data, onOpen }: {
  query: string; loading: boolean; error: string | null;
  data: Awaited<ReturnType<typeof knowledgeApi.search>> | null; onOpen: (path: string) => void;
}) {
  const results = data?.results ?? [];
  const top = Math.max(0, ...results.map((x) => x.score));
  const counts = data?.counts;
  return (
    <Panel className="accent-top" flush
      eyebrow={data ? `hybrid retrieval · ${results.length} results` : 'searching…'}
      title={<>Results for <span className="text-brand">“{query}”</span></>}>
      {error ? <Empty title="The search did not answer" hint={error} />
        : loading && !data ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Searching…" />
          : results.length === 0 ? <Empty title="Nothing here bears on that" hint="Try the words the documents themselves would use." /> : (
            <DataTable head={['Result', 'Score', 'Found by', 'Why it ranked']}>
              {results.map((x) => (
                <Row key={x.ref} onClick={x.kind === 'doc' ? () => onOpen(x.path) : undefined}>
                  <Cell className="font-medium text-ink">{x.title}<Mono className="mt-0.5 block w-fit">{x.ref}</Mono></Cell>
                  <Cell><span className="flex items-center gap-2"><Bar className="w-16" pct={top ? (x.score / top) * 100 : 0} /><span className="tnum text-[12px]">{x.score.toFixed(3)}</span></span></Cell>
                  <Cell><Tag tone={HOW[x.how].tone}>{HOW[x.how].label}</Tag></Cell>
                  <Cell className="max-w-[460px] text-[12.5px] text-soft">{HOW[x.how].why} · {x.kind} · <span className="font-mono">{x.path}{x.line ? `:${x.line}` : ''}</span></Cell>
                </Row>
              ))}
            </DataTable>
          )}
      {counts && (
        <div className="grid grid-cols-1 gap-x-6 border-t border-line/60 px-5 py-2.5 md:grid-cols-3">
          {[
            { n: counts.lexical, name: 'Words · Postgres full text', note: counts.lexical >= counts.lexicalCap ? `pieces matching the words, counted to ${counts.lexicalCap.toLocaleString()}` : 'pieces matching the words' },
            { n: counts.semantic, name: `Meaning · ${data?.model || 'no model'}`, note: counts.semantic ? 'nearest neighbours compared' : 'off — no embedding lane answered' },
            { n: counts.fused, name: `Fused · reciprocal rank, k = ${counts.k}`, note: 'what survived both lists' },
          ].map((line) => (
            <div key={line.name} className="flex items-start gap-2 py-1">
              <span className="tnum w-10 shrink-0 text-right text-[12px] text-brand">{line.n.toLocaleString()}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-[12.5px] text-ink-2">{line.name}</span>
                <span className="block text-[11.5px] text-dim">{line.note}</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function LiveDocBrowser({ project, data, kind, setKind, q, setQ, sel, setSel, stamp }: {
  project: Project; data: LiveDocs; kind: string; setKind: (k: string) => void; q: string; setQ: (q: string) => void;
  sel: string | null; setSel: (path: string | null) => void; stamp: string;
}) {
  const kinds = useMemo(() => [...new Set(data.docs.map((x) => x.kind))].sort(), [data.docs]);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return data.docs.filter((x) => (kind === 'all' || x.kind === kind) && (!t || `${x.ref} ${x.title} ${x.summary}`.toLowerCase().includes(t)));
  }, [data.docs, kind, q]);
  const picked = list.find((x) => x.id === sel) ?? data.docs.find((x) => x.id === sel) ?? list[0] ?? null;
  const detail = useRemote(picked ? `${project.id}:doc:${picked.id}:${stamp}` : null, () => knowledgeApi.doc(project.id, picked?.id ?? ''));
  const doc = detail.data ?? picked;

  return (
    <div className="flex min-h-[520px] flex-col gap-3 lg:flex-row">
      <div className="max-h-[30vh] w-full shrink-0 overflow-y-auto rounded-xl border border-line bg-surface py-2 lg:max-h-none lg:w-44">
        <button onClick={() => setKind('all')}
          className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[13px]', kind === 'all' ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}>
          <span className="flex items-center gap-2"><Boxes className="size-3.5 text-dim" />All</span>
          <span className="tnum text-[12px] text-dim">{data.docs.length}</span>
        </button>
        <div className="mx-3.5 my-1.5 h-px bg-line" />
        {kinds.map((k) => (
          <button key={k} onClick={() => { setKind(k); setSel(null); }}
            className={cn('flex w-full items-center justify-between px-3.5 py-1.5 text-[13px]', kind === k ? 'bg-surface-2 font-medium text-ink' : 'text-soft hover:text-ink-2')}>
            <span className="truncate font-mono">.{k}</span>
            <span className="tnum text-[12px] text-dim">{data.docs.filter((x) => x.kind === k).length}</span>
          </button>
        ))}
      </div>

      <div className="no-scrollbar w-full shrink-0 max-h-[42vh] lg:max-h-none lg:w-[330px] overflow-y-auto rounded-xl border border-line bg-surface">
        <div className="border-b border-line p-2.5">
          <Field value={q} onChange={setQ} icon={<Search className="size-3.5" />} placeholder="Filter documents…" onClear={() => setQ('')} />
        </div>
        {list.length === 0 ? <Empty title="Nothing here" /> : list.map((x) => (
          <ListRow key={x.id} active={x.id === picked?.id} onClick={() => setSel(x.id)}>
            <div className="flex items-center gap-2">
              <Dot state={!x.indexed || x.stale ? 'warn' : 'ok'} />
              <Mono className="truncate">{x.ref}</Mono>
              <span className="eyebrow ml-auto">{x.kind}</span>
            </div>
            <p className="mt-1 line-clamp-2 text-[13px] text-ink">{x.title}</p>
            <p className="mt-1 truncate text-[11.5px] text-dim">{x.source} · {x.addedAt ? ago(x.addedAt) : 'no date'}</p>
          </ListRow>
        ))}
      </div>

      <div className="min-w-0 flex-1 space-y-3 overflow-y-auto">
        {doc && (
          <Panel eyebrow={`${doc.kind} · ${project.name}${doc.addedAt ? ` · changed ${ago(doc.addedAt)}` : ''}`}
            title={<span className="flex flex-wrap items-center gap-2"><Mono tone="brand">{doc.ref}</Mono>{doc.title}</span>}
            actions={!doc.indexed ? <Tag tone="warn">awaiting index</Tag> : doc.stale ? <Tag tone="warn">changed since index</Tag> : <Tag tone="ok">indexed</Tag>}>
            {doc.summary
              ? <><SectionTitle className="mb-1">The document's first paragraph</SectionTitle><p className="text-[13.5px] leading-relaxed text-ink-2">{doc.summary}</p></>
              : <p className="text-[13px] text-dim">{doc.indexed ? 'Its first piece has no paragraph after the heading.' : 'Not indexed, so there is no text to quote yet.'}</p>}
            <div className="mt-3 grid grid-cols-1 gap-x-6 border-t border-line/60 pt-2.5 sm:grid-cols-2 xl:grid-cols-4">
              <KV k="Source" v={doc.source} />
              <KV k="Size" v={doc.bytes ? bytes(doc.bytes) : '—'} />
              <KV k="Chunks" v={`${doc.chunks} · ${doc.embedded} embedded`} />
              <KV k="Embedding" v={data.retrieval.semantic ? data.retrieval.model ?? '—' : 'none — words only'} mono />
            </div>
          </Panel>
        )}

        {doc && (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel eyebrow={detail.data ? `${detail.data.entities.length} the index declares` : 'reading…'} title="Entities">
              {detail.error ? <p className="text-[12.5px] text-dim">{detail.error}</p>
                : detail.data?.entities.length ? <div className="flex flex-wrap gap-1">{detail.data.entities.map((e) => <Mono key={e}>{e}</Mono>)}</div>
                  : <p className="text-[12.5px] text-dim">{detail.data ? 'It names no symbol this project declares.' : '…'}</p>}
            </Panel>
            <Panel eyebrow={detail.data ? `${detail.data.linkedTo.length} links` : 'reading…'} title="Connected to">
              {detail.data?.linkedTo.length ? <div className="flex flex-wrap gap-1">{detail.data.linkedTo.map((l) => <Tag key={l} tone="brand">{l}</Tag>)}</div>
                : <p className="text-[12.5px] text-dim">{detail.data ? 'No file, task, plan or memory it names exists here.' : '…'}</p>}
            </Panel>
          </div>
        )}

        {detail.data && detail.data.sections.length > 0 && (
          <Panel flush eyebrow={`${detail.data.sections.length} pieces, split at its headings`} title="Sections">
            <div className="divide-y divide-line/60">
              {detail.data.sections.map((s) => (
                <div key={s.ref} className="flex items-center gap-2 px-5 py-1.5">
                  <span className="min-w-0 flex-1 truncate text-[13px] text-ink-2">{s.title}</span>
                  <Mono className="shrink-0">{s.ref}</Mono>
                  <Tag tone={s.embedded ? 'brand' : 'neutral'}>{s.embedded ? 'embedded' : 'words only'}</Tag>
                </div>
              ))}
            </div>
          </Panel>
        )}

        <Panel eyebrow="What really happens to a document" title={<span className="flex items-center gap-1.5"><Upload className="size-3.5 text-brand" />Ingestion</span>} flush>
          <div className="divide-y divide-line/60">
            {data.pipeline.map((s) => (
              <div key={s.n} className="flex items-start gap-2.5 px-5 py-2">
                <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{s.n}</span>
                <span className="min-w-0 flex-1">
                  <span className="text-[13px] font-medium text-ink">{s.step}</span>
                  <span className="block text-[12.5px] text-dim">{s.detail}</span>
                </span>
              </div>
            ))}
          </div>
          <div className="border-t border-line/60 px-5 py-2.5">
            <SectionTitle>Accepted inputs</SectionTitle>
            <div className="flex flex-wrap gap-1">{data.formats.map((a) => <Tag key={a} tone="neutral">.{a}</Tag>)}</div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
