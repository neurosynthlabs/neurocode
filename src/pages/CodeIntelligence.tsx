import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ChevronRight, ChevronDown, Folder, FileCode, Database, Table2, Component,
  Search, Bug, Scale, FlaskConical, ArrowUpRight, ArrowDownRight,
} from 'lucide-react';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Mono, Empty, KV, Bar,
  SectionTitle, Stat, StatGrid,
} from '@/components/os';
import { repoTree, symbols, indexStatus, questions } from '@/mock/code';
import { agentName } from '@/mock/agents';
import { cn } from '@/lib/utils';
import type { CodeNode } from '@/types';

const KIND_ICON = { folder: Folder, file: FileCode, class: FileCode, sproc: Database, table: Table2, component: Component };

function Tree({ nodes, depth, open, onToggle, sel, onSelect, filter }: {
  nodes: CodeNode[]; depth: number; open: Set<string>; onToggle: (id: string) => void;
  sel: string; onSelect: (id: string) => void; filter: string;
}) {
  return (
    <>
      {nodes.map((n) => {
        const hasKids = !!n.children?.length;
        const isOpen = open.has(n.id);
        const Icon = KIND_ICON[n.kind] ?? FileCode;
        const match = !filter || (n.name + n.path).toLowerCase().includes(filter);
        const kidsMatch = hasKids && JSON.stringify(n.children).toLowerCase().includes(filter);
        if (filter && !match && !kidsMatch) return null;
        return (
          <div key={n.id}>
            <button
              onClick={() => (hasKids ? onToggle(n.id) : onSelect(n.id))}
              className={cn(
                'flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[13px] transition-colors',
                sel === n.id ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:bg-surface-2/60',
              )}
              style={{ paddingLeft: 8 + depth * 12 }}
            >
              {hasKids
                ? (isOpen ? <ChevronDown className="size-3 shrink-0 text-dim" /> : <ChevronRight className="size-3 shrink-0 text-dim" />)
                : <span className="w-3 shrink-0" />}
              <Icon className={cn('size-3.5 shrink-0', n.kind === 'folder' ? 'text-dim' : n.kind === 'sproc' || n.kind === 'table' ? 'text-violet' : 'text-brand')} />
              <span className="min-w-0 flex-1 truncate">{n.name}</span>
              {n.loc !== undefined && !hasKids && <span className="tnum shrink-0 text-[11px] text-dim">{n.loc}</span>}
              {n.risk && !hasKids && (
                <span className={cn('size-1.5 shrink-0 rounded-full',
                  n.risk === 'CRITICAL' || n.risk === 'HIGH' ? 'bg-danger' : n.risk === 'MEDIUM' ? 'bg-warn' : 'bg-ok')} />
              )}
            </button>
            {hasKids && (isOpen || (filter && kidsMatch)) && (
              <Tree nodes={n.children!} depth={depth + 1} open={open} onToggle={onToggle} sel={sel} onSelect={onSelect} filter={filter} />
            )}
          </div>
        );
      })}
    </>
  );
}

export default function CodeIntelligence() {
  const nav = useNavigate();
  const [open, setOpen] = useState(new Set(['be', 'be-tax', 'be-bill', 'db', 'db-proc']));
  const [sel, setSel] = useState('sym-invsvc');
  const [q, setQ] = useState('');
  const [pane, setPane] = useState<typeof questions[number]['pane']>('usedBy');

  const s = symbols[sel];
  const toggle = (id: string) => setOpen((o) => {
    const n = new Set(o);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const answer = useMemo(() => {
    if (!s) return null;
    switch (pane) {
      case 'usedBy': return { title: `${s.usedBy.length} callers depend on ${s.name}`, items: s.usedBy, icon: ArrowUpRight, tone: 'warn' as const, note: 'Changing the signature is a change to every one of these.' };
      case 'impact': return { title: `${s.usedBy.length + s.database.length + s.tests.length} things move if this changes`, items: [...s.usedBy.map((x) => `caller · ${x}`), ...s.database.map((x) => `data · ${x}`), ...s.tests.map((x) => `test · ${x}`)], icon: ArrowDownRight, tone: 'danger' as const, note: 'Run a full impact analysis before dispatching a change here.' };
      case 'database': return { title: `${s.database.length} objects · ${s.storedProcedures.length} procedures`, items: [...s.database, ...s.storedProcedures], icon: Database, tone: 'violet' as const, note: 'Mapped from the parsed schema, not guessed from names.' };
      case 'tests': return { title: `${s.tests.length} suites cover this`, items: s.tests, icon: FlaskConical, tone: 'ok' as const, note: 'Coverage is by call edge, not by filename convention.' };
      case 'bugs': return { title: `${s.knownBugs.length} known issues`, items: s.knownBugs, icon: Bug, tone: 'danger' as const, note: 'Closed bugs are kept — they are the cheapest form of memory.' };
    }
  }, [s, pane]);

  return (
    <Page>
      <PageHeader
        title="Code Intelligence"
        subtitle="Tree-sitter AST + LSP references + git history. The OS answers questions about the codebase from a parsed graph, not from a giant context window."
      >
        <div className="flex flex-wrap items-center gap-3 pb-3 text-[12.5px] text-dim">
          <Mono>{indexStatus.parser}</Mono>
          <span>{indexStatus.filesParsed.toLocaleString()} files</span>
          <span>{indexStatus.symbolsIndexed.toLocaleString()} symbols</span>
          <span>{indexStatus.callEdges.toLocaleString()} call edges</span>
          <span className="text-warn">{indexStatus.unresolved} unresolved imports</span>
          <span className="ml-auto">last full index {indexStatus.lastFullIndex} · incremental {indexStatus.incremental}</span>
        </div>
      </PageHeader>

      <PageBody className="flex h-full flex-col gap-0 p-0 md:flex-row">
        {/* Tree */}
        <div className="flex w-full shrink-0 max-h-[42vh] md:max-h-none md:w-[280px] flex-col border-b border-line md:border-b-0 md:border-r overflow-y-auto">
          <div className="shrink-0 border-b border-line p-2.5">
            <div className="flex h-9 items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 focus-within:border-brand">
              <Search className="size-3.5 shrink-0 text-dim" />
              <input value={q} onChange={(e) => setQ(e.target.value.toLowerCase())} placeholder="Filter files, classes, procedures…"
                className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-dim focus-visible:outline-none" />
            </div>
          </div>
          <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto py-1.5">
            <Tree nodes={repoTree} depth={0} open={open} onToggle={toggle} sel={sel} onSelect={(id) => symbols[id] && setSel(id)} filter={q} />
          </div>
          <div className="shrink-0 border-t border-line px-3 py-2">
            <div className="flex flex-wrap gap-1">
              {indexStatus.languages.map((l) => <Mono key={l}>{l}</Mono>)}
            </div>
          </div>
        </div>

        {/* Symbol detail */}
        <div className="min-w-0 flex-1 overflow-y-auto p-5">
          {!s ? <Empty title="Select a symbol" hint="Folders expand; files, classes, procedures and tables open here." /> : (
            <>
              <div className="mb-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-[17px] font-semibold text-ink">{s.name}</h2>
                  <Tag tone="neutral">{s.kind}</Tag>
                  <RiskPill risk={s.risk} />
                </div>
                <Mono className="mt-1 inline-block">{s.path}</Mono>
              </div>

              <StatGrid cols={5} className="mb-4">
                <Stat label="Lines" value={s.loc} />
                <Stat label="Complexity" value={s.complexity} tone={s.complexity > 80 ? 'danger' : s.complexity > 60 ? 'warn' : 'ok'} />
                <Stat label="Churn 90d" value={s.churn} sub="commits touching it" />
                <Stat label="Callers" value={s.usedBy.length} tone={s.usedBy.length > 4 ? 'warn' : 'neutral'} />
                <Stat label="Owner" value={agentName(symbols[sel] ? 'backend' : 'backend').split(' ')[0]} sub={s.lastChanged} />
              </StatGrid>

              <Panel eyebrow="What it actually does" title="Summary" className="mb-4">
                <p className="text-[13.5px] leading-relaxed text-ink-2">{s.summary}</p>
              </Panel>

              {/* Question bar */}
              <SectionTitle>Ask the graph</SectionTitle>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {questions.map((qq) => (
                  <button
                    key={qq.id}
                    onClick={() => setPane(qq.pane)}
                    className={cn('rounded-sm border px-2.5 py-1 text-[12.5px] transition-colors',
                      pane === qq.pane ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-line bg-surface text-soft hover:border-line-strong hover:text-ink')}
                  >
                    {qq.label}
                  </button>
                ))}
              </div>

              {answer && (
                <Panel eyebrow={answer.note} title={answer.title} className="mb-4" flush>
                  <div className="divide-y divide-line">
                    {answer.items.length === 0
                      ? <div className="px-3.5 py-3 text-[13px] text-dim">Nothing recorded.</div>
                      : answer.items.map((it) => (
                        <div key={it} className="flex items-center gap-2 px-3.5 py-1.5">
                          <answer.icon className={cn('size-3 shrink-0',
                            answer.tone === 'danger' ? 'text-danger' : answer.tone === 'warn' ? 'text-warn' : answer.tone === 'ok' ? 'text-ok' : 'text-violet')} />
                          <span className="font-mono text-[12.5px] text-ink-2">{it}</span>
                        </div>
                      ))}
                  </div>
                </Panel>
              )}

              {/* Relations */}
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-4">
                <Panel eyebrow={`${s.dependencies.length}`} title="Dependencies" flush>
                  <div className="divide-y divide-line">
                    {s.dependencies.length === 0 ? <div className="px-3.5 py-2 text-[12.5px] text-dim">none</div>
                      : s.dependencies.map((d) => <div key={d} className="truncate px-3.5 py-1.5 font-mono text-[12px] text-ink-2">{d}</div>)}
                  </div>
                </Panel>
                <Panel eyebrow={`${s.usedBy.length}`} title="Used by" flush>
                  <div className="divide-y divide-line">
                    {s.usedBy.length === 0 ? <div className="px-3.5 py-2 text-[12.5px] text-dim">nothing calls it</div>
                      : s.usedBy.map((d) => <div key={d} className="truncate px-3.5 py-1.5 font-mono text-[12px] text-ink-2">{d}</div>)}
                  </div>
                </Panel>
                <Panel eyebrow={`${s.database.length}`} title="Database" flush>
                  <div className="divide-y divide-line">
                    {s.database.length === 0 ? <div className="px-3.5 py-2 text-[12.5px] text-dim">no data access</div>
                      : s.database.map((d) => <div key={d} className="truncate px-3.5 py-1.5 font-mono text-[12px] text-violet">{d}</div>)}
                  </div>
                </Panel>
                <Panel eyebrow={`${s.storedProcedures.length}`} title="Stored procedures" flush>
                  <div className="divide-y divide-line">
                    {s.storedProcedures.length === 0 ? <div className="px-3.5 py-2 text-[12.5px] text-dim">none</div>
                      : s.storedProcedures.map((d) => <div key={d} className="truncate px-3.5 py-1.5 font-mono text-[12px] text-violet">{d}</div>)}
                  </div>
                </Panel>
              </div>

              <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-3">
                <Panel eyebrow="Coverage" title={<span className="flex items-center gap-1.5"><FlaskConical className="size-3.5 text-ok" />Tests</span>} flush>
                  <div className="divide-y divide-line">
                    {s.tests.length === 0 ? <div className="px-3.5 py-2 text-[12.5px] text-warn">no test covers this</div>
                      : s.tests.map((t) => <div key={t} className="px-3.5 py-1.5 text-[12.5px] text-ink-2">{t}</div>)}
                  </div>
                </Panel>
                <Panel eyebrow="History" title={<span className="flex items-center gap-1.5"><Bug className="size-3.5 text-danger" />Known bugs</span>} flush>
                  <div className="divide-y divide-line">
                    {s.knownBugs.length === 0 ? <div className="px-3.5 py-2 text-[12.5px] text-dim">clean record</div>
                      : s.knownBugs.map((b) => <div key={b} className="px-3.5 py-1.5 text-[12.5px] text-ink-2">{b}</div>)}
                  </div>
                </Panel>
                <Panel eyebrow="Why it is like this" title={<span className="flex items-center gap-1.5"><Scale className="size-3.5 text-brand" />Decisions</span>} flush>
                  <div className="divide-y divide-line">
                    {s.decisions.length === 0 ? <div className="px-3.5 py-2 text-[12.5px] text-dim">undocumented</div>
                      : s.decisions.map((d) => <div key={d} className="px-3.5 py-1.5 text-[12.5px] text-ink-2">{d}</div>)}
                  </div>
                </Panel>
              </div>

              <Panel className="mt-3" eyebrow="Change safety" title="Before you touch this">
                <div className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
                  <KV k="Blast radius" v={`${s.usedBy.length} callers · ${s.database.length} tables · ${s.tests.length} suites`} />
                  <KV k="Complexity" v={`${s.complexity} / 100`} />
                  <KV k="Churn" v={`${s.churn} commits in 90 days`} />
                  <KV k="Last changed" v={s.lastChanged} />
                </div>
                <Bar className="mt-2.5" pct={s.complexity} tone={s.complexity > 80 ? 'danger' : 'warn'} height="h-1.5" />
                <button onClick={() => nav('/architecture')} className="mt-3 text-[13px] text-brand hover:underline">
                  Run full impact analysis on {s.name} →
                </button>
              </Panel>
            </>
          )}
        </div>
      </PageBody>
    </Page>
  );
}
