import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Microscope, Globe, GitBranch, BookOpen, Library, Brain, ExternalLink, EyeOff, Loader2, Square } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, Dot, Mono, ListRow, DataTable, Row, Cell,
  Bar, Ring, Empty, SectionTitle,
} from '@/components/os';
import { reports } from '@/mock/thinking';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { useProject } from '@/lib/project-context';
import { useRemote } from '@/lib/remote';
import {
  fetchReport, fetchResearch, startResearch, stopResearch,
  type ResearchDoc, type ResearchKind, type ResearchListItem,
} from '@/lib/live/research';
import { cn } from '@/lib/utils';

const SOURCES = [
  { id: 'web', label: 'Web', icon: Globe },
  { id: 'github', label: 'GitHub', icon: GitBranch },
  { id: 'docs', label: 'Documentation', icon: BookOpen },
  { id: 'internal', label: 'Internal knowledge', icon: Library },
  { id: 'memory', label: 'Project memory', icon: Brain },
] as const;
const VERDICT_TONE: Record<string, 'ok' | 'danger' | 'warn' | 'neutral'> = {
  Recommended: 'ok', Rejected: 'danger', Fallback: 'warn', Supplement: 'neutral', Later: 'neutral',
};

/** Live: research run over this project's retrieval. The demo (and no API) keeps the worked examples. */
export default function Research() {
  const { mode } = useData();
  return mode === 'live' ? <LiveResearch /> : <SampleResearch />;
}

function SampleResearch() {
  const [q, setQ] = useState('');
  const [src, setSrc] = useState<Set<string>>(new Set(['web', 'github', 'docs', 'internal', 'memory']));
  const [sel, setSel] = useState(reports[0].id);
  const r = useMemo(() => reports.find((x) => x.id === sel) ?? reports[0], [sel]);
  const maxHits = Math.max(...r.sweep.map((s) => s.hits), 1);

  const toggle = (id: string) => setSrc((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  return (
    <Page>
      <PageHeader
        title="Research"
        subtitle="Four search angles run in parallel, each blind to the others. Every claim carries a source, and the report says out loud what it could not cover."
      >
        <div className="space-y-2 pb-3">
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3">
            <Microscope className="size-4 shrink-0 text-brand" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="What should I investigate?"
              className="min-w-0 flex-1 bg-transparent text-[14px] text-ink placeholder:text-dim focus-visible:outline-none" />
            <Button size="sm" disabled={!q.trim() || src.size === 0}
              onClick={() => { toast.success('Research queued', { description: `${src.size} source${src.size > 1 ? 's' : ''} · 4 parallel angles` }); setQ(''); }}>
              Research
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="eyebrow mr-1">Sources</span>
            {SOURCES.map(({ id, label, icon: I }) => (
              <button key={id} onClick={() => toggle(id)}
                className={cn('flex items-center gap-1.5 rounded-sm border px-2 py-1 text-[12.5px] transition-colors',
                  src.has(id) ? 'border-brand/40 bg-brand/10 text-brand' : 'border-line bg-surface text-dim line-through')}>
                <I className="size-3" />{label}
              </button>
            ))}
          </div>
        </div>
      </PageHeader>

      <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
        <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
          {reports.map((x) => (
            <ListRow key={x.id} active={x.id === r.id} onClick={() => setSel(x.id)}>
              <div className="flex items-center gap-2">
                <Dot state={x.status === 'complete' ? 'ok' : 'running'} pulse={x.status === 'running'} />
                <Mono>{x.ref}</Mono>
                <span className="ml-auto tnum text-[11.5px] text-dim">{x.status === 'complete' ? `${x.confidence}%` : x.status}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-[13px] text-ink">{x.question}</p>
              <p className="mt-1 text-[11.5px] text-dim">{x.agents} agents · {x.durationS ? `${Math.round(x.durationS / 60)} min` : 'in progress'} · {x.createdAt}</p>
            </ListRow>
          ))}
        </div>

        <div className="min-w-0 flex-1 space-y-3 overflow-y-auto p-5">
          <Panel className="accent-top" eyebrow={`${r.ref} · ${r.agents} agents · ${r.createdAt}`} title={r.question}
            actions={r.status === 'complete' && <Ring pct={r.confidence} size={44} />}>
            {r.status !== 'complete' ? (
              <Empty icon={<Microscope className="size-5" />} title="Still running" hint="Two agents are sweeping documentation and internal memory. The report appears when all four angles return." />
            ) : (
              <>
                <SectionTitle>Summary</SectionTitle>
                <p className="text-[14px] leading-relaxed text-ink-2">{r.summary}</p>
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-2.5">
                  {r.sources.map((s) => <Tag key={s.kind} tone="neutral">{s.count} {s.kind}</Tag>)}
                </div>
              </>
            )}
          </Panel>

          {r.status === 'complete' && (
            <>
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                <Panel eyebrow="Each angle blind to the others" title="Multi-modal sweep">
                  {r.sweep.map((s) => (
                    <div key={s.angle} className="flex items-center gap-3 py-1">
                      <span className="w-36 shrink-0 text-[12.5px] text-ink-2">{s.angle}</span>
                      <Bar pct={(s.hits / maxHits) * 100} tone="brand" />
                      <span className="tnum w-6 shrink-0 text-right text-[12px] text-soft">{s.hits}</span>
                    </div>
                  ))}
                </Panel>
                <Panel className="border-warn/30" eyebrow="Completeness critic — honesty is the feature"
                  title={<span className="flex items-center gap-1.5"><EyeOff className="size-3.5 text-warn" />Not covered</span>}>
                  {r.gaps.length === 0 ? <p className="text-[13px] text-ok">No material gaps found.</p> : (
                    <ul className="space-y-1.5">
                      {r.gaps.map((g) => <li key={g} className="flex gap-1.5 text-[13px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-warn" />{g}</li>)}
                    </ul>
                  )}
                </Panel>
              </div>

              <Panel eyebrow={`${r.findings.length} findings`} title="Findings" flush>
                <div className="divide-y divide-line">
                  {r.findings.map((f, i) => (
                    <div key={i} className="flex gap-2.5 px-3.5 py-2">
                      <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{i + 1}</span>
                      <span className="text-[13px] text-ink-2">{f}</span>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel eyebrow="Compared honestly" title="Alternatives" flush>
                <DataTable head={['Option', 'Pros', 'Cons', 'Verdict']}>
                  {r.alternatives.map((a) => (
                    <Row key={a.name}>
                      <Cell className="font-medium text-ink">{a.name}</Cell>
                      <Cell className="text-[12.5px] text-ok">{a.pros.join(' · ')}</Cell>
                      <Cell className="text-[12.5px] text-danger">{a.cons.join(' · ')}</Cell>
                      <Cell><Tag tone={VERDICT_TONE[a.verdict.split(' ')[0]] ?? 'neutral'}>{a.verdict}</Tag></Cell>
                    </Row>
                  ))}
                </DataTable>
              </Panel>

              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                <Panel eyebrow="Shape of the solution" title="Architecture">
                  <p className="text-[13.5px] leading-relaxed text-ink-2">{r.architecture}</p>
                </Panel>
                <Panel eyebrow={`${r.risks.length} risks`} title="Risks">
                  <ul className="space-y-1">
                    {r.risks.map((k) => <li key={k} className="flex gap-1.5 text-[13px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-danger" />{k}</li>)}
                  </ul>
                </Panel>
              </div>

              <Panel className="accent-left" eyebrow="What the OS would do" title="Recommendation">
                <p className="text-[14px] leading-relaxed text-ink">{r.recommendation}</p>
              </Panel>

              <Panel eyebrow={`${r.citations.length} sources`} title="Citations" flush>
                <div className="divide-y divide-line">
                  {r.citations.map((c, i) => (
                    <div key={c.url} className="flex items-center gap-2.5 px-3.5 py-2">
                      <span className="tnum w-5 shrink-0 text-right font-mono text-[11.5px] text-dim">[{i + 1}]</span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-2">{c.label}</span>
                      <Tag tone="neutral">{c.via}</Tag>
                      <span className="flex shrink-0 items-center gap-1 font-mono text-[11.5px] text-dim"><ExternalLink className="size-3" />{c.url}</span>
                    </div>
                  ))}
                </div>
              </Panel>
            </>
          )}
        </div>
      </PageBody>
    </Page>
  );
}

/* ── live ─────────────────────────────────────────────────────── */

/** What the live toggles search. Web and GitHub stay visible, and disabled with the reason. */
const LIVE_SOURCES: { id: ResearchKind; label: string; icon: typeof Globe }[] = [
  { id: 'doc', label: 'Documentation', icon: BookOpen },
  { id: 'code', label: 'Internal knowledge', icon: Library },
  { id: 'memory', label: 'Project memory', icon: Brain },
];
const NO_FETCHER = 'Not connected: nothing in this app fetches the web or GitHub, so they cannot be searched.';
const IN_FLIGHT = new Set(['queued', 'running']);
const STATUS_DOT: Record<ResearchListItem['status'], string> = {
  queued: 'queued', running: 'running', complete: 'ok', failed: 'failed', cancelled: 'neutral',
};

const stamp = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '');
const minutes = (s: number) => (s < 60 ? `${s} s` : `${Math.round(s / 60)} min`);
const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');

function LiveResearch() {
  const { can } = useAuth();
  const { project } = useProject();
  const [q, setQ] = useState('');
  const [kinds, setKinds] = useState<Set<ResearchKind>>(new Set(['doc', 'code', 'memory']));
  const [picked, setPicked] = useState<{ project: string; ref: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const list = useRemote(`research:${project.id}`, () => fetchResearch(project.id));
  const items = list.data ?? [];
  const sel = picked?.project === project.id ? picked.ref : items[0]?.ref ?? null;
  const detail = useRemote(sel ? `research:${sel}` : null, () => fetchReport(sel ?? ''));

  // A report is written as it runs: read it again every two seconds until it has finished.
  const running = items.some((x) => IN_FLIGHT.has(x.status)) || (!!detail.data && IN_FLIGHT.has(detail.data.status));
  const reloads = useRef(() => {});
  useLayoutEffect(() => { reloads.current = () => { list.reload(); detail.reload(); }; });
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => reloads.current(), 2000);
    return () => window.clearInterval(id);
  }, [running]);

  const toggle = (id: ResearchKind) => setKinds((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const allowed = can('ai:use');
  const blocker = !allowed ? 'Your role cannot use AI features (ai:use).' : kinds.size === 0 ? 'Choose at least one source.' : q.trim().length < 3 ? 'Ask a question first.' : '';
  const start = async () => {
    setBusy(true);
    try {
      const made = await startResearch(q.trim(), project.id, [...kinds]);
      setQ('');
      setPicked({ project: project.id, ref: made.ref });
      list.reload();
      toast.success(`${made.ref} started`, { description: `${kinds.size} source${kinds.size > 1 ? 's' : ''} · angles are answered in parallel` });
    } catch (e) {
      toast.error('Research not started', { description: reason(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page>
      <PageHeader
        title="Research"
        subtitle={`A question split into angles, each answered on its own from ${project.name}'s code, documentation and memory. A citation is only ever a piece that angle was handed, and the report says what it could not cover.`}
      >
        <div className="space-y-2 pb-3">
          <div className="focus-brand flex h-9 items-center gap-2 rounded-md border border-line bg-base px-3">
            <Microscope className="size-4 shrink-0 text-brand" />
            <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={2000} disabled={!allowed}
              onKeyDown={(e) => { if (e.key === 'Enter' && !blocker && !busy) void start(); }}
              placeholder={allowed ? `What should I investigate in ${project.name}?` : 'Your role cannot start research.'}
              className="min-w-0 flex-1 bg-transparent text-[14px] text-ink placeholder:text-dim focus-visible:outline-none" />
            <Button size="sm" disabled={!!blocker || busy} title={blocker || undefined} onClick={() => void start()}>
              {busy && <Loader2 className="size-3.5 animate-spin" />}Research
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="eyebrow mr-1">Sources</span>
            {[{ id: 'web', label: 'Web', icon: Globe }, { id: 'github', label: 'GitHub', icon: GitBranch }].map(({ id, label, icon: I }) => (
              <button key={id} disabled title={NO_FETCHER}
                className="flex cursor-not-allowed items-center gap-1.5 rounded-sm border border-line bg-surface px-2 py-1 text-[12.5px] text-dim opacity-60">
                <I className="size-3" />{label}<span className="text-[11px]">not connected</span>
              </button>
            ))}
            {LIVE_SOURCES.map(({ id, label, icon: I }) => (
              <button key={id} onClick={() => toggle(id)} aria-pressed={kinds.has(id)}
                className={cn('flex items-center gap-1.5 rounded-sm border px-2 py-1 text-[12.5px] transition-colors',
                  kinds.has(id) ? 'border-brand/40 bg-brand/10 text-brand' : 'border-line bg-surface text-dim line-through')}>
                <I className="size-3" />{label}
              </button>
            ))}
          </div>
        </div>
      </PageHeader>

      {list.error ? (
        <PageBody><Empty title="Research did not load" hint={list.error} action={<Button size="sm" variant="outline" onClick={list.reload}>Try again</Button>} /></PageBody>
      ) : !list.data ? (
        <PageBody><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading research…" /></PageBody>
      ) : items.length === 0 ? (
        <PageBody>
          <Empty icon={<Microscope className="size-6" />} title={`No research on ${project.name} yet`}
            hint="Ask a question above. It is split into angles, each angle reads only what retrieval hands it, and every claim carries the piece it came from." />
        </PageBody>
      ) : (
        <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
          <div className="no-scrollbar w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[300px] overflow-y-auto border-b border-line md:border-b-0 md:border-r">
            {items.map((x) => (
              <ListRow key={x.id} active={x.ref === sel} onClick={() => setPicked({ project: project.id, ref: x.ref })}>
                <div className="flex items-center gap-2">
                  <Dot state={STATUS_DOT[x.status]} pulse={x.status === 'running'} />
                  <Mono>{x.ref}</Mono>
                  <span className="ml-auto tnum text-[11.5px] text-dim">{x.status === 'complete' ? `${x.confidence}% cited` : x.status}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-[13px] text-ink">{x.question}</p>
                <p className="mt-1 text-[11.5px] text-dim">{x.agents} angles · {x.durationS ? minutes(x.durationS) : IN_FLIGHT.has(x.status) ? 'in progress' : '—'} · {stamp(x.createdAt)}</p>
              </ListRow>
            ))}
          </div>
          <div className="min-w-0 flex-1 space-y-3 overflow-y-auto p-5">
            {detail.error ? <Empty title="This report did not load" hint={detail.error} />
              : !detail.data ? <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the report…" />
                : <LiveReport r={detail.data} canStop={allowed} onStopped={() => { list.reload(); detail.reload(); }} />}
          </div>
        </PageBody>
      )}
    </Page>
  );
}

function LiveReport({ r, canStop, onStopped }: { r: ResearchDoc; canStop: boolean; onStopped: () => void }) {
  const maxHits = Math.max(...r.sweep.map((s) => s.hits), 1);
  const [stopping, setStopping] = useState<string | null>(null);
  const stop = async () => {
    setStopping(r.ref);
    try {
      await stopResearch(r.ref);
      toast(`Stopping ${r.ref}`, { description: 'It stops at its next checkpoint and keeps the angles already answered.' });
      onStopped();
    } catch (e) {
      setStopping(null);
      toast.error('Not stopped', { description: reason(e) });
    }
  };
  const writer = r.provider === 'rules' ? 'written by rules, no model answered' : r.provider ? `${r.provider} · ${r.model}` : '';
  const answered = r.angles.filter((a) => a.status === 'done').length;

  return (
    <>
      <Panel className="accent-top" eyebrow={`${r.ref} · ${r.agents} angles · ${stamp(r.createdAt)}`} title={r.question}
        actions={r.status === 'complete'
          ? <span className="flex items-center gap-2 text-[11.5px] text-dim">cited coverage<Ring pct={r.confidence} size={44} /></span>
          : IN_FLIGHT.has(r.status) && canStop && (
            <Button size="xs" variant="outline" disabled={stopping === r.ref} onClick={() => void stop()}><Square className="size-3" />Stop</Button>
          )}>
        {IN_FLIGHT.has(r.status) ? (
          <Empty icon={<Loader2 className="size-5 animate-spin" />} title={r.status === 'queued' ? 'Queued' : 'Still running'}
            hint={r.agents ? `${answered} of ${r.agents} angles answered. The report appears when the synthesis is written.` : 'Splitting the question into angles.'} />
        ) : r.status !== 'complete' ? (
          <Empty icon={<Microscope className="size-5" />} title={r.status === 'cancelled' ? 'Stopped' : 'Failed'} hint={r.note || undefined} />
        ) : (
          <>
            <SectionTitle>Summary</SectionTitle>
            <p className="text-[14px] leading-relaxed text-ink-2">{r.summary}</p>
            <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-2.5">
              {r.sources.map((s) => <Tag key={s.kind} tone="neutral">{s.count} {s.kind}</Tag>)}
              {writer && <Tag tone={r.provider === 'rules' ? 'warn' : 'info'}>{writer}</Tag>}
            </div>
          </>
        )}
      </Panel>

      {r.angles.length > 0 && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <Panel eyebrow="Each angle blind to the others" title="Multi-modal sweep">
            {r.angles.map((a) => (
              <div key={a.n} className="py-1">
                <div className="flex items-center gap-3">
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2" title={a.question}>{a.question}</span>
                  <span className="tnum w-6 shrink-0 text-right text-[12px] text-soft">{a.hits}</span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <Bar pct={(a.hits / maxHits) * 100} tone="brand" />
                  <span className="shrink-0 font-mono text-[11px] text-dim">{a.status === 'done' ? (a.lane === 'rules' ? 'rules' : a.lane ?? '') : a.status}</span>
                </div>
              </div>
            ))}
          </Panel>
          {r.status === 'complete' && (
            <Panel className="border-warn/30" eyebrow="What this report could not cover"
              title={<span className="flex items-center gap-1.5"><EyeOff className="size-3.5 text-warn" />Not covered</span>}>
              <ul className="space-y-1.5">
                {r.gaps.map((g) => <li key={g} className="flex gap-1.5 text-[13px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-warn" />{g}</li>)}
              </ul>
            </Panel>
          )}
        </div>
      )}

      {r.status === 'complete' && (
        <>
          {r.findings.length > 0 && (
            <Panel eyebrow={`${r.findings.length} findings`} title="Findings" flush>
              <div className="divide-y divide-line">
                {r.findings.map((f, i) => (
                  <div key={i} className="flex gap-2.5 px-3.5 py-2">
                    <span className="tnum mt-px w-4 shrink-0 text-right font-mono text-[11.5px] text-dim">{i + 1}</span>
                    <span className="text-[13px] text-ink-2">{f}</span>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {r.alternatives.length > 0 && (
            <Panel eyebrow="Compared from the findings" title="Alternatives" flush>
              <DataTable head={['Option', 'Pros', 'Cons', 'Verdict']}>
                {r.alternatives.map((a) => (
                  <Row key={a.name}>
                    <Cell className="font-medium text-ink">{a.name}</Cell>
                    <Cell className="text-[12.5px] text-ok">{a.pros.join(' · ')}</Cell>
                    <Cell className="text-[12.5px] text-danger">{a.cons.join(' · ')}</Cell>
                    <Cell><Tag tone={VERDICT_TONE[a.verdict.split(' ')[0]] ?? 'neutral'}>{a.verdict}</Tag></Cell>
                  </Row>
                ))}
              </DataTable>
            </Panel>
          )}

          {(r.architecture || r.risks.length > 0) && (
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {r.architecture && (
                <Panel eyebrow="Shape of the solution" title="Architecture">
                  <p className="text-[13.5px] leading-relaxed text-ink-2">{r.architecture}</p>
                </Panel>
              )}
              {r.risks.length > 0 && (
                <Panel eyebrow={`${r.risks.length} risks`} title="Risks">
                  <ul className="space-y-1">
                    {r.risks.map((k) => <li key={k} className="flex gap-1.5 text-[13px] text-ink-2"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-danger" />{k}</li>)}
                  </ul>
                </Panel>
              )}
            </div>
          )}

          {r.recommendation && (
            <Panel className="accent-left" eyebrow="From the findings" title="Recommendation">
              <p className="text-[14px] leading-relaxed text-ink">{r.recommendation}</p>
            </Panel>
          )}

          <Panel eyebrow={`${r.citations.length} sources`} title="Citations" flush>
            {r.citations.length === 0 ? <Empty title="Nothing was cited" hint="No angle's finding named a piece it was handed." /> : (
              <div className="divide-y divide-line">
                {r.citations.map((c, i) => (
                  <div key={`${c.kind}:${c.url}`} className="px-3.5 py-2">
                    <div className="flex items-center gap-2.5">
                      <span className="tnum w-5 shrink-0 text-right font-mono text-[11.5px] text-dim">[{i + 1}]</span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-2">{c.label}</span>
                      <Tag tone="neutral">{c.via}</Tag>
                      <span className="hidden min-w-0 max-w-[40%] shrink truncate font-mono text-[11.5px] text-dim sm:block">{c.url}</span>
                    </div>
                    {c.excerpt && <p className="mt-1 ml-7 line-clamp-2 font-mono text-[11.5px] text-dim">{c.excerpt}</p>}
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </>
      )}
    </>
  );
}
