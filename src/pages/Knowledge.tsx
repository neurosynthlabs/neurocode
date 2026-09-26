import { useEffect, useMemo, useState } from 'react';
import { Search, Library, Link2, Boxes, Loader2, RefreshCw, FileText } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, Field, ListRow,
  DataTable, Row, Cell, Stat, StatGrid, KV, Empty, SectionTitle, Bar, More,
} from '@/components/os';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { knowledgeApi, type LiveDocs } from '@/lib/live/knowledge';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';
import { ago, bytes } from './code/format';
import { NoProject } from './code/shared';

/** The documents retrieval holds for the active project: the repository's own writing. */
export default function Knowledge() {
  const { project } = useProject();
  if (!project) return <NoProject title="Knowledge" hint="Onboard a repository to read its README, docs and notes." />;
  return <LiveKnowledge project={project} />;
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
        subtitle="What the repository says about itself: README, docs and notes."
        about={<>
          <p>Each document is split at its headings.</p>
          <p>A search finds it by its words, and by meaning when a lane embeds it.</p>
        </>}
        actions={can('projects:onboard') && (
          <Button size="sm" variant="outline" disabled={building} onClick={() => void rebuild()}>
            {building ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}{building ? 'Re-indexing…' : 'Re-index'}
          </Button>
        )}
      >
        <div className="pb-3">
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3 transition-colors">
            <Search className="size-4 shrink-0 text-brand" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search the documents"
              placeholder="Search the documents"
              className="min-w-0 flex-1 bg-transparent text-[14px] text-ink placeholder:text-dim focus-visible:outline-none" />
            {search && <button onClick={() => setSearch('')} className="text-[12px] text-dim hover:text-ink">Clear</button>}
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

          {(data.docs.length > 0 || data.onDisk > 0) && <StatGrid cols={5}>
            {/* The server lists at most a few hundred documents but counts the ones on disk over all of
                them, so the figures come from its counts and the list says when it was cut. */}
            <Stat label="Documents" value={Math.max(data.onDisk, data.docs.length).toLocaleString()}
              sub={data.onDisk > data.docs.length ? `listing the first ${data.docs.length} · ${data.notIndexed} awaiting index` : `${data.notIndexed} awaiting index`}
              icon={<Library className="size-3" />} />
            <Stat label="Chunks" value={(data.onDisk > data.docs.length && data.retrieval.byKind?.doc !== undefined
              ? data.retrieval.byKind.doc : data.docs.reduce((n, x) => n + x.chunks, 0)).toLocaleString()}
              sub={data.retrieval.semantic ? `${data.retrieval.model}${data.retrieval.lane ? ` · ${data.retrieval.lane}` : ''}` : 'words only, no embedding lane'} />
            <Stat label="Entities linked" value={data.entitiesLinked} tone={data.entitiesLinked ? 'brand' : 'neutral'} sub="names the code index declares" icon={<Link2 className="size-3" />} />
            <Stat label="Formats read" value={data.formats.length} sub={data.formats.map((f) => `.${f}`).join(' ')} />
            <Stat label="Changed since index" value={changed} tone={changed ? 'warn' : 'ok'}
              sub={data.retrieval.at ? `index built ${ago(data.retrieval.at)}` : 'never indexed'}
              onClick={changed && can('projects:onboard') && !building ? () => void rebuild() : undefined} />
          </StatGrid>}

          {data.docs.length === 0 ? (
            <Empty icon={<FileText className="size-6" />} title="No documents in this repository"
              hint={`No ${data.formats.map((f) => `.${f}`).join(', ')} file outside the skipped folders.`} />
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
      eyebrow={data ? `${results.length} results` : 'searching…'}
      title={<>Results for <span className="text-brand">“{query}”</span></>}>
      {error ? <Empty title="The search did not answer" hint={error} />
        : loading && !data ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Searching…" />
          : results.length === 0 ? <Empty title="Nothing here bears on that" hint="Try the documents' own words." /> : (
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
        <div className="border-t border-line/60 px-5 py-2.5">
          <div className="grid grid-cols-1 gap-x-6 md:grid-cols-3">
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
          {/* The two rules that shaped this list, said only when they did something: a file may give at
              most a couple of pieces, and a piece too far from the question is never handed to a model. */}
          {(counts.dropped > 0 || counts.floored > 0) && (
            <p className="mt-1.5 border-t border-line/60 pt-2 text-[11.5px] leading-relaxed text-dim">
              {counts.dropped > 0 && `${counts.dropped} skipped so one file could not fill the answer`}
              {counts.dropped > 0 && counts.floored > 0 && ' · '}
              {counts.floored > 0 && `${counts.floored} below the relevance floor, never handed to a session`}
            </p>
          )}
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
  // The server cut the list: these counts are of the listed documents, and the rail says so.
  const cut = data.onDisk > data.docs.length;
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
        {cut && (
          <p className="px-3.5 pt-1 text-[11.5px] leading-snug text-dim">
            Showing {data.docs.length} of {data.onDisk.toLocaleString()}; counts are of these.
          </p>
        )}
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
              <span className="ml-auto text-[11.5px] text-dim">{x.kind}</span>
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
              ? <><SectionTitle className="mb-1">First paragraph</SectionTitle><p className="text-[13.5px] leading-relaxed text-ink-2">{doc.summary}</p></>
              : <p className="text-[13px] text-dim">{doc.indexed ? 'No paragraph after its first heading.' : 'Not indexed yet, so nothing to quote.'}</p>}
            <div className="mt-3 grid grid-cols-1 gap-x-6 border-t border-line/60 pt-2.5 sm:grid-cols-2 xl:grid-cols-4">
              <KV k="Source" v={doc.source} />
              <KV k="Size" v={bytes(doc.bytes)} />
              <KV k="Chunks" v={`${doc.chunks} · ${doc.embedded} embedded`} />
              <KV k="Embedding" v={data.retrieval.semantic && data.retrieval.model ? data.retrieval.model : 'none — words only'} mono />
            </div>
          </Panel>
        )}

        {doc && (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel eyebrow={detail.data ? `${detail.data.entities.length} declared in code` : 'reading…'} title="Entities">
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
          <Panel flush title={`Sections (${detail.data.sections.length})`}>
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

        <More label="How a document is ingested"><Panel flush>
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
        </Panel></More>
      </div>
    </div>
  );
}
