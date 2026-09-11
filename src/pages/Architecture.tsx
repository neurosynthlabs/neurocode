import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { TriangleAlert, Target, Layers, Eye, Scale } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Page, PageHeader, PageBody, Panel, Tag, RiskPill, Mono, Stat, StatGrid, Segmented,
  Toolbar, Ring, SectionTitle, Empty,
} from '@/components/os';
import { nodes, edges, impacts, layers, recentlyChanged } from '@/mock/architecture';
import { adrsByProject } from '@/mock/modules';
import { useProject } from '@/lib/project-context';
import { cn } from '@/lib/utils';
import type { GraphNode } from '@/types';

const LAYER_COLOR: Record<GraphNode['layer'], string> = {
  ui: 'var(--os-info)', api: 'var(--os-brand)', service: 'var(--os-violet)',
  repo: 'var(--os-soft)', sproc: 'var(--os-warn)', table: 'var(--os-ok)', external: 'var(--os-danger)',
};
const RISK_STROKE: Record<string, string> = {
  LOW: 'var(--os-line-strong)', MEDIUM: 'var(--os-warn)', HIGH: 'var(--os-danger)', CRITICAL: 'var(--os-danger)',
};
const NW = 152, NH = 30, H = 668;
// Sized from the data: the canvas used to be a fixed 960 and cut the rightmost nodes off by 12px.
const W = Math.max(...nodes.map((n) => n.x)) + NW + 12;
const ADR_TONE = { accepted: 'ok', proposed: 'warn', superseded: 'neutral', rejected: 'danger' } as const;

export default function Architecture() {
  const nav = useNavigate();
  const { projectId } = useProject();
  const [sel, setSel] = useState('svc-tax');
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showChanged, setShowChanged] = useState(false);
  const [tab, setTab] = useState<'graph' | 'decisions'>('graph');

  const visible = useMemo(() => nodes.filter((n) => !hidden.has(n.layer)), [hidden]);
  const visIds = useMemo(() => new Set(visible.map((n) => n.id)), [visible]);
  const visEdges = useMemo(() => edges.filter((e) => visIds.has(e.from) && visIds.has(e.to)), [visIds]);

  const touching = useMemo(
    () => new Set(visEdges.filter((e) => e.from === sel || e.to === sel).flatMap((e) => [e.from, e.to])),
    [visEdges, sel],
  );

  const impact = impacts[sel];
  const adrs = adrsByProject[projectId] ?? adrsByProject.erp;

  const toggleLayer = (id: string) => setHidden((h) => {
    const n = new Set(h);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const pos = (id: string) => nodes.find((n) => n.id === id)!;

  return (
    <Page>
      <PageHeader
        title="Architecture"
        subtitle="The call graph the OS actually parsed — and the blast radius of touching any node in it."
        actions={
          <Segmented options={[{ id: 'graph', label: 'Graph & impact' }, { id: 'decisions', label: `Decisions (${adrs.length})` }]} value={tab} onChange={setTab} />
        }
      >
        {tab === 'graph' && (
          <Toolbar>
            <span className="eyebrow mr-1">Layers</span>
            {layers.map((l) => (
              <button
                key={l.id}
                onClick={() => toggleLayer(l.id)}
                className={cn('flex items-center gap-1.5 rounded-sm border px-2 py-1 text-[11px] transition-colors',
                  hidden.has(l.id) ? 'border-line bg-surface text-dim line-through' : 'border-line-strong bg-surface-2 text-ink-2')}
                title={l.note}
              >
                <span className="size-2 rounded-xs" style={{ background: LAYER_COLOR[l.id as GraphNode['layer']] }} />
                {l.label}
              </button>
            ))}
            <button
              onClick={() => setShowChanged((v) => !v)}
              className={cn('ml-2 flex items-center gap-1.5 rounded-sm border px-2 py-1 text-[11px] transition-colors',
                showChanged ? 'border-brand bg-brand/10 text-brand' : 'border-line bg-surface text-soft hover:text-ink')}
            >
              <Eye className="size-3" />Recently changed
            </button>
            <span className="ml-auto text-[11.5px] text-dim">{visible.length} nodes · {visEdges.length} edges</span>
          </Toolbar>
        )}
      </PageHeader>

      <PageBody>
        {tab === 'decisions' ? (
          <Panel eyebrow="Rejected decisions are kept — they are memory too" title="Architecture decision records" flush>
            <div className="divide-y divide-line">
              {adrs.map((a) => (
                <div key={a.id} className="px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Mono tone="brand">{a.ref}</Mono>
                    <span className="text-[12.5px] font-medium text-ink">{a.title}</span>
                    <Tag tone={ADR_TONE[a.status]}>{a.status}</Tag>
                    {a.supersedes && <span className="text-[11px] text-dim">supersedes {a.supersedes}</span>}
                  </div>
                  <p className="mt-1 max-w-4xl text-[11.5px] text-soft">{a.summary}</p>
                  <p className="mt-1 flex items-center gap-1.5 text-[11px] text-dim"><Scale className="size-3" />{a.by} · {a.at}</p>
                </div>
              ))}
            </div>
          </Panel>
        ) : (
          <div className="grid grid-cols-12 gap-4">
            {/* Graph */}
            <div className="col-span-12 2xl:col-span-7">
              <Panel eyebrow="Parsed from AST + LSP, not inferred from names" title="Dependency graph" flush>
                <div className="overflow-auto p-3">
                  <svg width={W} height={H} className="min-w-[820px]">
                    <defs>
                      <marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                        <path d="M0 0 L8 4 L0 8 z" fill="var(--os-line-strong)" />
                      </marker>
                      <marker id="arw-hot" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                        <path d="M0 0 L8 4 L0 8 z" fill="var(--os-brand)" />
                      </marker>
                    </defs>

                    {/* layer bands */}
                    {layers.filter((l) => l.id !== 'external' && !hidden.has(l.id)).map((l) => {
                      const ys = nodes.filter((n) => n.layer === l.id).map((n) => n.y);
                      if (!ys.length) return null;
                      const y = Math.min(...ys) - 10;
                      return (
                        <g key={l.id}>
                          <line x1="0" y1={y} x2={W} y2={y} stroke="var(--os-line)" strokeDasharray="3 4" />
                          <text x="4" y={y - 4} fontSize="9" fill="var(--os-dim)" style={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>{l.label}</text>
                        </g>
                      );
                    })}

                    {/* edges */}
                    {visEdges.map((e, i) => {
                      const a = pos(e.from), b = pos(e.to);
                      const hot = e.from === sel || e.to === sel;
                      const x1 = a.x + NW / 2, y1 = a.y + NH;
                      const x2 = b.x + NW / 2, y2 = b.y;
                      const mid = (y1 + y2) / 2;
                      return (
                        <path
                          key={i}
                          d={`M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`}
                          fill="none"
                          stroke={hot ? 'var(--os-brand)' : 'var(--os-line)'}
                          strokeWidth={hot ? 1.6 : 1}
                          strokeDasharray={e.kind === 'writes' ? '4 3' : undefined}
                          markerEnd={hot ? 'url(#arw-hot)' : 'url(#arw)'}
                          opacity={hot ? 1 : 0.55}
                        />
                      );
                    })}

                    {/* nodes */}
                    {visible.map((n) => {
                      const active = n.id === sel;
                      const near = touching.has(n.id) && !active;
                      const changed = showChanged && recentlyChanged.has(n.id);
                      return (
                        <g key={n.id} onClick={() => setSel(n.id)} style={{ cursor: 'pointer' }}>
                          <rect
                            x={n.x} y={n.y} width={NW} height={NH} rx="4"
                            fill={active ? 'var(--os-brand-dim)' : near ? 'var(--os-surface-2)' : 'var(--os-surface)'}
                            stroke={active ? 'var(--os-brand)' : changed ? 'var(--os-info)' : RISK_STROKE[n.risk]}
                            strokeWidth={active ? 2 : changed ? 1.6 : 1}
                            strokeDasharray={changed && !active ? '3 2' : undefined}
                          />
                          <rect x={n.x} y={n.y} width="3" height={NH} rx="1.5" fill={LAYER_COLOR[n.layer]} />
                          <text
                            x={n.x + 10} y={n.y + NH / 2 + 3.5} fontSize="10.5"
                            fill={active ? 'var(--os-ink)' : 'var(--os-ink-2)'}
                            fontWeight={active ? 600 : 400}
                            fontFamily="var(--font-mono)"
                          >
                            {n.label.length > 20 ? n.label.slice(0, 19) + '…' : n.label}
                          </text>
                          {(n.risk === 'CRITICAL' || n.risk === 'HIGH') && (
                            <circle cx={n.x + NW - 8} cy={n.y + 8} r="2.5" fill="var(--os-danger)" />
                          )}
                        </g>
                      );
                    })}
                  </svg>
                </div>
                <div className="flex flex-wrap items-center gap-3 border-t border-line px-3.5 py-2 text-[10.5px] text-dim">
                  <span className="flex items-center gap-1.5"><span className="h-px w-5 bg-line-strong" />calls / depends</span>
                  <span className="flex items-center gap-1.5"><span className="h-px w-5 border-t border-dashed border-line-strong" />writes</span>
                  <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-danger" />HIGH or CRITICAL risk</span>
                  <span className="ml-auto">click any node to run its impact analysis</span>
                </div>
              </Panel>
            </div>

            {/* Impact */}
            <div className="col-span-12 space-y-3 2xl:col-span-5">
              {!impact ? (
                <Panel><Empty icon={<Target className="size-5" />} title={`No impact report for ${pos(sel).label}`}
                  hint="Reports are precomputed for the nodes the OS has been asked about. Pick TaxService, InvoiceService, SP_CalculateTax, TRANS_INVOICE or DispatchService." /></Panel>
              ) : (
                <>
                  <Panel
                    className={cn('accent-top', impact.risk === 'CRITICAL' || impact.risk === 'HIGH' ? 'border-danger/40' : 'border-warn/35')}
                    eyebrow="Impact analysis"
                    title={<span className="flex items-center gap-2"><Target className="size-3.5 text-brand" />If I change {impact.target}…</span>}
                    actions={<RiskPill risk={impact.risk} />}
                  >
                    <div className="flex items-center gap-4">
                      <Ring pct={impact.confidence} size={54} />
                      <div className="grid flex-1 grid-cols-4 gap-2">
                        {[
                          { v: impact.modules, l: 'modules' },
                          { v: impact.apis, l: 'APIs' },
                          { v: impact.tests, l: 'tests' },
                          { v: impact.legacyDeps, l: 'legacy deps' },
                        ].map((x) => (
                          <div key={x.l}>
                            <div className="tnum text-[19px] leading-none font-semibold text-ink">{x.v}</div>
                            <div className="eyebrow mt-1">{x.l}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                    <p className="mt-3 border-t border-line pt-2.5 text-[11px] text-dim">
                      Confidence {impact.confidence}% — measured against parsed call edges, not guessed from naming.
                    </p>
                  </Panel>

                  <StatGrid cols={2}>
                    <Stat label="Blast radius" value={impact.blastRadius.reduce((n, b) => n + b.items.length, 0)} sub="named artefacts" icon={<Layers className="size-3" />} />
                    <Stat label="Warnings" value={impact.warnings.length} tone="warn" sub="things that will bite" icon={<TriangleAlert className="size-3" />} />
                  </StatGrid>

                  <Panel eyebrow="Everything that moves with it" title="Blast radius" flush>
                    <div className="divide-y divide-line">
                      {impact.blastRadius.map((b) => (
                        <div key={b.label} className="px-3.5 py-2.5">
                          <div className="eyebrow mb-1.5">{b.label} · {b.items.length}</div>
                          <div className="flex flex-wrap gap-1">
                            {b.items.map((i) => <Mono key={i}>{i}</Mono>)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </Panel>

                  <Panel eyebrow="Read these before dispatching" title="Warnings" className="border-warn/30" flush>
                    <div className="divide-y divide-line">
                      {impact.warnings.map((w, i) => (
                        <div key={i} className="flex items-start gap-2 px-3.5 py-2">
                          <TriangleAlert className="mt-px size-3.5 shrink-0 text-warn" />
                          <span className="text-[12px] text-ink-2">{w}</span>
                        </div>
                      ))}
                    </div>
                  </Panel>

                  <Panel eyebrow="What the OS would do" title="Recommendation" className="accent-left">
                    <p className="text-[12.5px] leading-relaxed text-ink-2">{impact.recommendation}</p>
                    <div className="mt-3 flex gap-2">
                      <Button size="sm" onClick={() => nav('/plans')}>Compile a plan for this</Button>
                      <Button size="sm" variant="outline" onClick={() => nav('/code')}>Open in code intelligence</Button>
                    </div>
                  </Panel>
                </>
              )}

              <SectionTitle>Selected node</SectionTitle>
              <Panel flush>
                <div className="flex items-center gap-3 px-3.5 py-2.5">
                  <span className="size-2.5 rounded-xs" style={{ background: LAYER_COLOR[pos(sel).layer] }} />
                  <Mono tone="brand">{pos(sel).label}</Mono>
                  <Tag tone="neutral">{pos(sel).layer}</Tag>
                  <RiskPill risk={pos(sel).risk} bare />
                  <span className="ml-auto text-[11px] text-dim">
                    {edges.filter((e) => e.to === sel).length} in · {edges.filter((e) => e.from === sel).length} out
                  </span>
                </div>
              </Panel>
            </div>
          </div>
        )}
      </PageBody>
    </Page>
  );
}
