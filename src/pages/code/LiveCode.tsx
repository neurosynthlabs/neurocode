import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, ExternalLink, FileCode, Folder, FolderSearch, GitBranchPlus, Loader2, Network, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Cell, DataTable, Empty, Mono, Page, PageBody, PageHeader, Panel, RiskPill, Row, Stat, StatGrid, Tag } from '@/components/os';
import { ApiError, api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { FILE_BROWSER, absoluteIn, isDesktop, onPathMenu, openInEditor, revealInFinder } from '@/lib/desktop';
import { byParser, codeApi, type LiveCodeSummary } from '@/lib/live/code';
import { knowledgeApi, type RetrievalStatus } from '@/lib/live/knowledge';
import { sourcesApi } from '@/lib/live/sources';
import { useRemote } from '@/lib/remote';
import { cn } from '@/lib/utils';
import type { Project } from '@/types';
import { KIND_TONE, ago, baseName } from './format';
import { ImpactPanel } from './shared';

/* Code Intelligence for an onboarded project: its real index, read from the local API. */

export function LiveCode({ project }: { project: Project }) {
  const nav = useNavigate();
  const { can } = useAuth();
  // The index's finish time is on the project record, which streams in: a new index reloads every panel.
  const stamp = project.codeIndex?.at ?? 'none';
  const s = useRemote(`${project.id}:${stamp}`, () => codeApi.summary(project.id));

  // ?path= (from Architecture or a link) opens a file; a click picks another until the link changes.
  const linked = useSearchParams()[0].get('path');
  const [picked, setPicked] = useState<{ link: string | null; path: string | null } | null>(null);
  const sel = picked && picked.link === linked ? picked.path : linked;
  const open = (path: string | null) => setPicked({ link: linked, path });

  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    const id = window.setTimeout(() => setQuery(q.trim()), 150);
    return () => window.clearTimeout(id);
  }, [q]);
  const hits = useRemote(query ? `${project.id}:${stamp}:q:${query}` : null, () => api.code.search(project.id, query));

  const [askedFor, setAskedFor] = useState<string | null>(null);
  const indexing = askedFor === stamp || !!s.data?.indexing;
  const reindex = async () => {
    setAskedFor(stamp);
    try {
      await api.code.reindex(project.id);
      toast('Reading the code again', { description: 'This screen refreshes on its own when the index is ready.' });
    } catch (e) {
      setAskedFor(null);
      toast.error('Not re-indexed', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    }
  };

  const run = s.data?.run;
  return (
    <Page>
      <PageHeader
        title="Code Intelligence"
        subtitle={`Read from ${project.name} on this machine, each language by the parser named below. Every number here is measured.`}
        actions={can('projects:onboard') && s.data?.canIndex && (
          <Button size="sm" variant="outline" onClick={() => void reindex()} disabled={indexing}>
            {indexing ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}{indexing ? 'Indexing…' : 'Re-index'}
          </Button>
        )}
      >
        {run && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pb-3 text-[12.5px] text-dim">
            {byParser(run.parsers).map(([parser, langs]) => (
              <span key={parser} title={langs.join(', ')}><Mono>{parser}</Mono> {langs.length > 3 ? `${langs.length} languages` : langs.join(', ')}</span>
            ))}
            <span>{run.files.toLocaleString()} files</span>
            <span>{run.symbols.toLocaleString()} symbols</span>
            <span>{run.edges.toLocaleString()} dependencies</span>
            {run.unresolved > 0 && <span className="text-warn">{run.unresolved} unresolved imports</span>}
            <span className="sm:ml-auto">indexed {ago(run.finishedAt)} in {(run.ms / 1000).toFixed(1)} s</span>
          </div>
        )}
      </PageHeader>

      {s.error ? (
        <PageBody><Empty title="The index did not load" hint={s.error} action={<Button size="sm" variant="outline" onClick={s.reload}>Try again</Button>} /></PageBody>
      ) : !s.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the index…" /></PageBody>
      ) : !s.data.indexed ? (
        <PageBody>
          <Empty
            icon={indexing ? <Loader2 className="size-5 animate-spin" /> : <FileCode className="size-6" />}
            title={indexing ? `Reading ${project.name}…` : `${project.name} has no index yet`}
            hint={indexing ? 'Files, symbols and dependencies appear here the moment the index is ready.' : 'Index it to see its files, symbols, dependencies and the blast radius of every change.'}
            action={!indexing && can('projects:onboard') && <Button size="sm" onClick={() => void reindex()}>Index now</Button>}
          />
        </PageBody>
      ) : (
        <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
          <div className="flex w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] flex-col border-b border-line md:border-b-0 md:border-r">
            <div className="shrink-0 border-b border-line p-2.5">
              <div className="flex h-9 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
                <Search className="size-3.5 shrink-0 text-dim" />
                <input
                  value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the code"
                  placeholder="Files, classes, functions, tables…"
                  className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none"
                />
              </div>
            </div>
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto py-1.5">
              {query ? (
                hits.loading ? <p className="px-3 py-2 text-[12.5px] text-dim">Searching…</p>
                  : !hits.data?.length ? <p className="px-3 py-2 text-[12.5px] text-dim">Nothing matches “{query}”.</p>
                    : hits.data.map((h, i) => (
                      <button
                        key={`${h.path}:${h.name}:${i}`} onClick={() => open(h.path)}
                        className={cn('flex w-full flex-col px-3 py-1.5 text-left transition-colors hover:bg-surface-2/60', sel === h.path && 'bg-surface-2')}
                      >
                        <span className="flex min-w-0 items-center gap-1.5">
                          <Tag tone={KIND_TONE[h.kind] ?? 'neutral'}>{h.kind}</Tag>
                          <span className="truncate font-mono text-[12.5px] text-ink">{h.name}</span>
                        </span>
                        <span className="mt-0.5 truncate font-mono text-[11.5px] text-dim">{h.path}{h.line ? `:${h.line}` : ''}</span>
                      </button>
                    ))
              ) : (
                <Dir pid={project.id} stamp={stamp} path="" name="" depth={0} sel={sel} onSelect={open} />
              )}
            </div>
            <button onClick={() => open(null)} className={cn('shrink-0 border-t border-line px-3 py-2.5 text-left text-[12.5px] transition-colors hover:text-ink', sel ? 'text-soft' : 'font-medium text-ink')}>
              Overview of {project.name}
            </button>
          </div>

          <div className="min-w-0 flex-1 overflow-y-auto p-5">
            {sel ? (
              <FileView
                pid={project.id} stamp={stamp} path={sel} onOpen={open}
                onGraph={(path) => nav(`/architecture?path=${encodeURIComponent(path)}`)}
                onPlan={(path) => nav('/', { state: { draft: `Change ${path}: ` } })}
              />
            ) : (
              <>
                <Retrieval pid={project.id} stamp={stamp} canBuild={can('projects:onboard')} onOpen={open} />
                <Overview summary={s.data} onOpen={open} onModule={(m) => nav(`/architecture?module=${encodeURIComponent(m)}`)} />
              </>
            )}
          </div>
        </PageBody>
      )}
    </Page>
  );
}

/** One folder of the tree. It loads its own children the first time it opens. */
function Dir({ pid, stamp, path, name, depth, count, sel, onSelect }: {
  pid: string; stamp: string; path: string; name: string; depth: number; count?: number;
  sel: string | null; onSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth === 0 || (!!sel && sel.startsWith(`${path}/`)));
  const kids = useRemote(expanded ? `${pid}:${stamp}:dir:${path}` : null, () => api.code.files(pid, path));
  const pad = { paddingLeft: 8 + Math.max(0, depth - 1) * 12 };
  return (
    <div>
      {depth > 0 && (
        <button onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}
          className="flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[13px] text-ink-2 transition-colors hover:bg-surface-2/60" style={pad}>
          {expanded ? <ChevronDown className="size-3 shrink-0 text-dim" /> : <ChevronRight className="size-3 shrink-0 text-dim" />}
          <Folder className="size-3.5 shrink-0 text-dim" />
          <span className="min-w-0 flex-1 truncate">{name}</span>
          {count !== undefined && <span className="tnum shrink-0 text-[11px] text-dim">{count}</span>}
        </button>
      )}
      {expanded && (kids.loading && !kids.data ? (
        <p className="py-1 text-[12px] text-dim" style={{ paddingLeft: 8 + depth * 12 }}>…</p>
      ) : kids.data && (
        <>
          {kids.data.dirs.map((d) => (
            <Dir key={d.path} pid={pid} stamp={stamp} path={d.path} name={d.name} depth={depth + 1} count={d.files} sel={sel} onSelect={onSelect} />
          ))}
          {kids.data.files.map((f) => (
            <button key={f.path} onClick={() => onSelect(f.path)}
              className={cn('flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[13px] transition-colors',
                sel === f.path ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:bg-surface-2/60')}
              style={{ paddingLeft: 8 + depth * 12 + 12 }}>
              <FileCode className="size-3.5 shrink-0 text-brand" />
              <span className="min-w-0 flex-1 truncate">{f.name}</span>
              {f.fanIn > 0 && (
                <span className={cn('size-1.5 shrink-0 rounded-full', f.fanIn >= 15 ? 'bg-danger' : f.fanIn >= 4 ? 'bg-warn' : 'bg-ok')}
                  title={`${f.fanIn} files use it`} />
              )}
              <span className="tnum shrink-0 text-[11px] text-dim">{f.lines}</span>
            </button>
          ))}
        </>
      ))}
    </div>
  );
}

/** Retrieval: the chunks this project holds, and a search over them by meaning as well as by words. */
function Retrieval({ pid, stamp, canBuild, onOpen }: {
  pid: string; stamp: string; canBuild: boolean; onOpen: (path: string) => void;
}) {
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  // From the click until the first answer read after the server accepted the build: 'post' while the
  // request is out, then the poll whose answer ends the wait. After that the server's own `building`
  // says when it is done — it is marked before the 202 is sent, so that answer already carries it.
  const [pending, setPending] = useState<'post' | number | null>(null);
  useEffect(() => {
    const id = window.setTimeout(() => setQuery(q.trim()), 200);
    return () => window.clearTimeout(id);
  }, [q]);
  const [polled, setPolled] = useState(0);
  const r = useRemote(`${pid}:${stamp}:rag:${query}:${polled}`, () => knowledgeApi.retrieval(pid, query));
  // A new key reads nothing until it answers, so the last answer to the same question stands in while a
  // poll is out — never one for another question or another project.
  const asked = `${pid}:${query}`;
  const [last, setLast] = useState<{ asked: string; data: RetrievalStatus } | null>(null);
  if (r.data && r.data !== last?.data) setLast({ asked, data: r.data });
  const state = r.data ?? (last?.asked === asked ? last.data : null);
  const running = !!state?.building;
  if (typeof pending === 'number' && r.data && polled >= pending) setPending(null);
  const building = pending !== null || running;

  // While a build runs — this screen's or anyone's — ask again two seconds after each answer, until an
  // answer says it has finished.
  useEffect(() => {
    if (!running || !r.data) return;
    const id = window.setTimeout(() => setPolled((n) => n + 1), 2000);
    return () => window.clearTimeout(id);
  }, [running, r.data]);

  const build = async () => {
    setPending('post');
    try {
      await api.code.buildRetrieval(pid);
      toast('Building retrieval', { description: 'Chunking the code and the documents, then embedding what a lane can.' });
      // Nothing polls while the button is enabled, so no read has moved `polled` since the click.
      setPolled(polled + 1);
      setPending(polled + 1);
    } catch (e) {
      setPending(null);
      toast.error('Not built', { description: e instanceof ApiError ? e.message : 'The local API did not answer.' });
    }
  };

  const kinds = Object.entries(state?.byKind ?? {});
  return (
    <Panel
      className="mb-4" title="Retrieval"
      eyebrow={state?.built ? `${state.chunks.toLocaleString()} chunks · ${state.semantic ? 'meaning and words' : 'words only'}` : 'not built yet'}
      actions={canBuild && (
        <Button size="sm" variant="outline" disabled={building} onClick={() => void build()}>
          {building ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}Rebuild
        </Button>
      )}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px] text-dim">
        {kinds.map(([kind, n]) => <span key={kind}>{n.toLocaleString()} {kind}</span>)}
        {state?.semantic
          ? <Tag tone="ok">{state.model}{state.lane ? ` · ${state.lane}` : ''}</Tag>
          : <Tag tone="neutral">lexical only</Tag>}
        {state?.at && <span className="sm:ml-auto">built {ago(state.at)}</span>}
      </div>
      {state?.note && <p className="mt-2 text-[12.5px] text-warn">{state.note}</p>}

      <div className="mt-3 flex h-9 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
        <Search className="size-3.5 shrink-0 text-dim" />
        <input
          value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search by meaning"
          placeholder="Ask by meaning: where is this handled, and why there?"
          className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none"
        />
      </div>

      {query && (
        <div className="mt-2.5 space-y-1.5">
          {r.loading && !state ? <p className="text-[12.5px] text-dim">Searching…</p>
            : !state?.results.length ? <p className="text-[12.5px] text-dim">Nothing here bears on “{query}”.</p>
              : state.results.map((hit) => (
                <button
                  key={hit.ref} onClick={() => hit.kind === 'code' && onOpen(hit.ref.split('#')[0])}
                  className="flex w-full flex-col rounded-lg border border-line/70 bg-surface-2/40 px-3 py-2 text-left transition-colors hover:bg-surface-2"
                >
                  <span className="flex w-full min-w-0 items-center gap-2">
                    <Tag tone={hit.kind === 'code' ? 'brand' : hit.kind === 'memory' ? 'ok' : 'neutral'}>{hit.kind}</Tag>
                    <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{hit.title}</span>
                    <span className="shrink-0 text-[11px] text-dim">{hit.how}</span>
                  </span>
                  <Mono>{hit.ref}</Mono>
                  <span className="mt-1 line-clamp-2 font-mono text-[11.5px] whitespace-pre-wrap text-soft">{hit.text}</span>
                </button>
              ))}
        </div>
      )}
    </Panel>
  );
}

function Overview({ summary, onOpen, onModule }: { summary: LiveCodeSummary; onOpen: (path: string) => void; onModule: (m: string) => void }) {
  const run = summary.run;
  if (!run) return null;
  const hotspots = summary.hotspots ?? [];
  const db = summary.database;
  const parsing = new Map((summary.parsing ?? []).map((p) => [p.language, p]));
  const unparsed = summary.unparsed ?? [];
  return (
    <div className="space-y-4">
      <StatGrid cols={4}>
        <Stat label="Files" value={run.files.toLocaleString()} sub={`${summary.languages?.length ?? 0} languages`} />
        <Stat label="Symbols" value={run.symbols.toLocaleString()} sub="classes, functions, tables…" />
        <Stat label="Dependencies" value={run.edges.toLocaleString()} sub="imports, uses, reads, writes" />
        <Stat label="Unresolved imports" value={run.unresolved} tone={run.unresolved ? 'warn' : 'ok'}
          sub={run.unresolved ? 'the graph has gaps there' : 'every internal import was found'} />
      </StatGrid>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel flush title="Most depended on" eyebrow="Change these with the most care">
          {hotspots.length === 0 ? <Empty title="No file depends on another yet" /> : (
            <div className="divide-y divide-line/60">
              {hotspots.map((h) => (
                <button key={h.path} onClick={() => onOpen(h.path)} className="flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-surface-2/60">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-[12.5px] text-ink">{h.path}</span>
                    <span className="mt-0.5 block text-[12px] text-dim">
                      {h.fanIn} {h.fanIn === 1 ? 'file uses' : 'files use'} it · complexity {h.complexity}{h.churn ? ` · ${h.churn} changes in 90 days` : ''}
                    </span>
                  </span>
                  <RiskPill risk={h.risk} bare />
                </button>
              ))}
            </div>
          )}
        </Panel>
        <Panel flush title="Languages" eyebrow="and the parser that read each">
          <DataTable head={['Language', 'Files', 'Symbols', 'Parser']}>
            {(summary.languages ?? []).map((l) => {
              const read = parsing.get(l.name);
              return (
                <Row key={l.name}>
                  <Cell className="font-medium text-ink">{l.name}</Cell>
                  <Cell className="tnum">{l.files.toLocaleString()}</Cell>
                  <Cell className="tnum">{read?.parser ? read.symbols.toLocaleString() : '—'}</Cell>
                  <Cell className="whitespace-nowrap">{read?.parser ? <Mono>{read.parser}</Mono> : <span className="text-[12.5px] text-dim">counted only</span>}</Cell>
                </Row>
              );
            })}
          </DataTable>
          {unparsed.length > 0 && (
            <p className="border-t border-line px-5 py-2.5 text-[12px] text-dim">
              Seen but not read: {unparsed.join(', ')}. Their files are counted; nothing on this machine parses them, so they add no symbols or dependencies.
            </p>
          )}
        </Panel>
      </div>

      <Panel flush title="Modules" eyebrow="Open one in Architecture to see its blast radius">
        <DataTable head={['Module', 'Files', 'Lines', 'Symbols', 'Used by', 'Uses']}>
          {(summary.modules ?? []).map((m) => (
            <Row key={m.name} onClick={() => onModule(m.name)}>
              <Cell className="font-mono text-[12.5px] text-ink">{m.name}</Cell>
              <Cell className="tnum">{m.files.toLocaleString()}</Cell>
              <Cell className="tnum">{m.lines.toLocaleString()}</Cell>
              <Cell className="tnum">{m.symbols.toLocaleString()}</Cell>
              <Cell className="tnum">{m.fanIn ? `${m.fanIn} modules` : '—'}</Cell>
              <Cell className="tnum">{m.fanOut ? `${m.fanOut} modules` : '—'}</Cell>
            </Row>
          ))}
        </DataTable>
      </Panel>

      {db && db.objects > 0 && (
        <Panel flush title="Database objects" eyebrow={`${db.objects} declared in SQL · who reads, writes and calls them`}>
          <DataTable head={['Object', 'Kind', 'Read by', 'Written by', 'Called by']}>
            {db.top.map((o) => (
              <Row key={o.name} onClick={() => onOpen(o.path)}>
                <Cell className="font-mono text-[12.5px] text-ink">{o.name}</Cell>
                <Cell><Tag tone={KIND_TONE[o.kind] ?? 'neutral'}>{o.kind}</Tag></Cell>
                <Cell className="tnum">{o.readers || '—'}</Cell>
                <Cell className={cn('tnum', o.writers && 'text-warn')}>{o.writers || '—'}</Cell>
                <Cell className="tnum">{o.callers || '—'}</Cell>
              </Row>
            ))}
          </DataTable>
        </Panel>
      )}
    </div>
  );
}

function FileView({ pid, stamp, path, onOpen, onGraph, onPlan }: {
  pid: string; stamp: string; path: string; onOpen: (path: string) => void; onGraph: (path: string) => void; onPlan: (path: string) => void;
}) {
  const r = useRemote(`${pid}:${stamp}:file:${path}`, () => api.code.file(pid, path));
  // In the desktop app, where the file is on this Mac — so it can be opened in the person's editor or shown in the
  // Finder. The API names a source's folder only to someone who may browse the machine; for anyone else there is none.
  const sources = useRemote(isDesktop ? `${pid}:${stamp}:sources` : null, () => sourcesApi.list(pid));
  if (r.error) return <Empty title="This file did not load" hint={r.error} />;
  if (!r.data) return <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading…" />;
  const { file: f, symbols, dependsOn, database, usedBy, impact } = r.data;
  const internal = dependsOn.filter((d) => d.path);
  const packages = [...new Set(dependsOn.filter((d) => !d.path).map((d) => d.target))];
  const onDisk = sources.data ? absoluteIn(sources.data, f.path) : null;

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[17px] font-semibold text-ink">{baseName(f.path)}</h2>
          <Tag tone="neutral">{f.lang}</Tag>
          <Tag tone="neutral">{f.module}</Tag>
          {f.test && <Tag tone="ok">test</Tag>}
        </div>
        <Mono className="mt-1 inline-block max-w-full truncate">{f.path}</Mono>
        {onDisk && (
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => openInEditor(onDisk)}><ExternalLink className="size-3.5" />Open in editor</Button>
            <Button size="sm" variant="outline" onClick={() => revealInFinder(onDisk)}><FolderSearch className="size-3.5" />{FILE_BROWSER}</Button>
          </div>
        )}
      </div>

      <StatGrid cols={5}>
        <Stat label="Lines" value={f.lines.toLocaleString()} />
        <Stat label="Complexity" value={f.complexity} tone={f.complexity >= 80 ? 'danger' : f.complexity >= 30 ? 'warn' : 'ok'} sub="decision points" />
        <Stat label="Used by" value={f.fanIn} tone={f.fanIn >= 15 ? 'danger' : f.fanIn >= 4 ? 'warn' : 'neutral'} sub="files" />
        <Stat label="Uses" value={f.fanOut} sub="files in the repo" />
        <Stat label="Changes" value={f.churn} sub={f.changedAt ? `last ${ago(f.changedAt)}` : 'in 90 days · no git history'} />
      </StatGrid>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Panel flush eyebrow={`${symbols.length}`} title="Symbols">
          {symbols.length === 0 ? <p className="px-5 py-3 text-[12.5px] text-dim">No symbols declared here.</p> : (
            <div className="max-h-[320px] divide-y divide-line/60 overflow-y-auto">
              {symbols.map((sym, i) => (
                <div key={`${sym.name}:${i}`} className="flex items-center gap-2 px-5 py-1.5"
                  onContextMenu={(e) => onPathMenu(e, onDisk, { line: sym.line })}
                  onDoubleClick={onDisk ? () => openInEditor(onDisk, sym.line) : undefined}
                  title={onDisk ? `Double-click to open ${baseName(f.path)} at line ${sym.line} in your editor` : undefined}>
                  <Tag tone={KIND_TONE[sym.kind] ?? 'neutral'}>{sym.kind}</Tag>
                  <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-2">{sym.name}</span>
                  <span className="tnum shrink-0 font-mono text-[11.5px] text-dim">:{sym.line}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
        <Panel flush eyebrow={`${usedBy.length}`} title="Used by">
          {usedBy.length === 0 ? <p className="px-5 py-3 text-[12.5px] text-dim">Nothing in the repository uses it.</p> : (
            <div className="max-h-[320px] divide-y divide-line/60 overflow-y-auto">
              {usedBy.map((u) => (
                <button key={u.path} onClick={() => onOpen(u.path)} className="flex w-full items-center gap-2 px-5 py-1.5 text-left transition-colors hover:bg-surface-2/60">
                  <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-2">{u.path}</span>
                  {u.kinds.map((k) => <Tag key={k} tone={KIND_TONE[k] ?? 'neutral'}>{k}</Tag>)}
                </button>
              ))}
            </div>
          )}
        </Panel>
        <Panel flush eyebrow={`${internal.length} in the repo · ${packages.length} packages`} title="Depends on">
          {internal.length === 0 && packages.length === 0 ? <p className="px-5 py-3 text-[12.5px] text-dim">It depends on nothing.</p> : (
            <div className="max-h-[320px] overflow-y-auto">
              <div className="divide-y divide-line/60">
                {internal.map((d) => (
                  <button key={`${d.path}:${d.target}`} onClick={() => d.path && onOpen(d.path)} className="flex w-full items-center gap-2 px-5 py-1.5 text-left transition-colors hover:bg-surface-2/60">
                    <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-2">{d.path}</span>
                    <Tag tone={KIND_TONE[d.kind] ?? 'neutral'}>{d.kind}</Tag>
                  </button>
                ))}
              </div>
              {packages.length > 0 && (
                <div className="flex flex-wrap gap-1 border-t border-line/60 px-5 py-2.5">
                  {packages.map((p) => <Mono key={p}>{p}</Mono>)}
                </div>
              )}
            </div>
          )}
        </Panel>
        <Panel flush eyebrow={`${database.length}`} title="Database">
          {database.length === 0 ? <p className="px-5 py-3 text-[12.5px] text-dim">It touches no declared table or procedure.</p> : (
            <div className="divide-y divide-line/60">
              {database.map((d) => (
                <div key={`${d.object}:${d.kind}`} className="flex items-center gap-2 px-5 py-1.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-violet">{d.object}</span>
                  <Tag tone={KIND_TONE[d.kind] ?? 'neutral'}>{d.kind}</Tag>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {impact && (
        <ImpactPanel
          impact={impact} onOpen={onOpen}
          actions={<>
            <Button size="sm" onClick={() => onPlan(f.path)}><GitBranchPlus className="size-3.5" />Plan this change</Button>
            <Button size="sm" variant="outline" onClick={() => onGraph(f.path)}><Network className="size-3.5" />Show in Architecture</Button>
          </>}
        />
      )}
    </div>
  );
}
