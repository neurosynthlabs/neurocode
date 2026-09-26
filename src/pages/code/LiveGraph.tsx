import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { FileCode, GitBranchPlus, Globe, Loader2, Scale, Target } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Empty, ListRow, Mono, Page, PageBody, PageHeader, Panel, Segmented, Tag, Toolbar } from '@/components/os';
import { api, type CodeGraph, type ImpactTarget } from '@/lib/api';
import { useData } from '@/lib/data';
import { useRemote } from '@/lib/remote';
import { ago } from './format';
import type { Project } from '@/types';
import { NODE_COLOR } from './format';
import { ImpactPanel } from './shared';

/* Architecture for an onboarded project: its modules and the tables they touch, laid out by
   dependency depth, with the measured blast radius of whatever you click. */

const NW = 170, NH = 34, GX = 26, GY = 62, PAD = 20, PER_ROW = 6;
const EDGE_COLOR: Record<string, string> = {
  depends: 'var(--os-line-strong)', reads: 'var(--os-info)', writes: 'var(--os-warn)', calls: 'var(--os-violet)',
};

/** Layers by longest dependency path (bounded, so cycles cannot loop), wide layers wrapped into rows. */
function layout(g: CodeGraph) {
  const mods = g.nodes.filter((n) => n.kind === 'module');
  const data = g.nodes.filter((n) => n.kind !== 'module');
  const depth = new Map<string, number>(mods.map((m) => [m.id, 0]));
  const deps = g.edges.filter((e) => e.kind === 'depends');
  for (let pass = 0; pass < mods.length; pass++) {
    let moved = false;
    for (const e of deps) {
      const d = (depth.get(e.from) ?? 0) + 1;
      if (d <= 6 && d > (depth.get(e.to) ?? 0)) { depth.set(e.to, d); moved = true; }
    }
    if (!moved) break;
  }
  const layers: string[][] = [];
  for (const m of [...mods].sort((a, b) => a.label.localeCompare(b.label))) {
    const d = depth.get(m.id) ?? 0;
    (layers[d] ??= []).push(m.id);
  }
  const rows: { ids: string[]; label: string }[] = [];
  layers.filter(Boolean).forEach((ids, i) => {
    for (let at = 0; at < ids.length; at += PER_ROW) {
      rows.push({ ids: ids.slice(at, at + PER_ROW), label: at ? '' : i === 0 ? 'Entry points' : `Depended on · ${i}` });
    }
  });
  const tables = data.map((n) => n.id);
  for (let at = 0; at < tables.length; at += PER_ROW) rows.push({ ids: tables.slice(at, at + PER_ROW), label: at ? '' : 'Database' });

  const widest = Math.max(1, Math.min(PER_ROW, ...rows.map((r) => r.ids.length)), ...rows.map((r) => r.ids.length));
  const width = PAD * 2 + widest * (NW + GX) - GX;
  const pos = new Map<string, { x: number; y: number }>();
  rows.forEach((r, ri) => {
    const x0 = (width - (r.ids.length * (NW + GX) - GX)) / 2;
    r.ids.forEach((id, i) => pos.set(id, { x: x0 + i * (NW + GX), y: PAD + 16 + ri * (NH + GY) }));
  });
  return {
    pos, width, height: PAD * 2 + 16 + rows.length * (NH + GY) - GY,
    labels: rows.map((r, ri) => ({ label: r.label, y: PAD + 16 + ri * (NH + GY) })).filter((r) => r.label),
  };
}

/** The decisions memory holds for this project and for the whole workspace: facts filed under Decisions. */
function Decisions({ project }: { project: Project }) {
  const nav = useNavigate();
  const { memory } = useData();
  const decisions = useMemo(
    () => memory.filter((f) => f.category === 'decisions' && !f.archived && (f.projectId === project.id || f.projectId === null)),
    [memory, project.id],
  );
  if (decisions.length === 0) {
    return (
      <Panel>
        <Empty icon={<Scale className="size-6" />} title={`No decisions recorded for ${project.name} yet`}
          hint="Facts filed under Decisions in Memory appear here."
          action={<Button size="sm" variant="outline" onClick={() => nav('/memory')}>Open Memory</Button>} />
      </Panel>
    );
  }
  return (
    <Panel flush eyebrow={`${decisions.length} in memory`} title="Decisions">
      {decisions.map((f) => (
        <ListRow key={f.id} onClick={() => nav(`/memory?ref=${encodeURIComponent(f.ref)}`)}>
          <div className="flex items-center gap-2">
            <Mono tone={f.pinned ? 'brand' : 'neutral'}>{f.ref}</Mono>
            {f.projectId === null && <Tag tone="violet"><Globe className="size-3" />workspace</Tag>}
            <span className="ml-auto text-[11.5px] text-dim">{ago(f.createdAt)}</span>
          </div>
          <p className="mt-1 text-[13.5px] font-medium text-ink">{f.title}</p>
          <p className="mt-0.5 line-clamp-2 text-[12.5px] text-soft">{f.body}</p>
        </ListRow>
      ))}
    </Panel>
  );
}

const idOf = (t: ImpactTarget | null) => (!t ? null : 'module' in t ? `m:${t.module}` : 'object' in t ? `d:${t.object}` : null);
const nameOf = (t: ImpactTarget) => ('path' in t ? t.path : 'module' in t ? t.module : t.object);

export function LiveGraph({ project }: { project: Project }) {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const stamp = project.codeIndex?.at ?? 'none';
  const g = useRemote(`${project.id}:${stamp}:graph`, () => api.code.graph(project.id));
  const [tab, setTab] = useState<'graph' | 'decisions'>('graph');

  // ?path=, ?module= or ?object= (from Code Intelligence) focuses a target; a click picks another.
  const lp = params.get('path'), lm = params.get('module'), lo = params.get('object');
  const linked: ImpactTarget | null = lp ? { path: lp } : lm ? { module: lm } : lo ? { object: lo } : null;
  const link = params.toString();
  const [picked, setPicked] = useState<{ link: string; focus: ImpactTarget } | null>(null);
  const focus = picked && picked.link === link ? picked.focus : linked;
  const impact = useRemote(focus ? `${project.id}:${stamp}:impact:${JSON.stringify(focus)}` : null,
    () => api.code.impact(project.id, focus ?? { module: '' }));

  const L = useMemo(() => (g.data ? layout(g.data) : null), [g.data]);
  const sel = idOf(focus);

  return (
    <Page>
      <PageHeader
        title="Architecture"
        subtitle="How the modules depend on each other, and what a change moves."
        about={<p>Read from the code index: imports, type uses and SQL. The blast radius of a change is measured on this graph.</p>}
        actions={<Segmented options={[{ id: 'graph', label: 'Graph & impact' }, { id: 'decisions', label: 'Decisions' }]} value={tab} onChange={setTab} />}
      >
        {tab === 'graph' && g.data && (
          <Toolbar>
            {Object.entries({ Module: 'module', Table: 'table', Procedure: 'procedure', View: 'view' }).map(([label, kind]) => (
              <span key={kind} className="flex items-center gap-1.5 text-[12px] text-soft">
                <span className="size-2 rounded-xs" style={{ background: NODE_COLOR[kind] }} />{label}
              </span>
            ))}
            <span className="flex items-center gap-1.5 text-[12px] text-soft"><span className="h-px w-5 border-t border-dashed border-warn" />writes</span>
            <span className="ml-auto text-[12.5px] text-dim">
              {g.data.nodes.length} nodes · {g.data.edges.length} links{g.data.truncated ? ` · largest ${g.data.nodes.filter((n) => n.kind === 'module').length} of ${g.data.modules} modules` : ''}
            </span>
          </Toolbar>
        )}
      </PageHeader>

      <PageBody>
        {tab === 'decisions' ? (
          <Decisions project={project} />
        ) : g.error ? (
          <Empty title="The graph did not load" hint={g.error} action={<Button size="sm" variant="outline" onClick={g.reload}>Try again</Button>} />
        ) : !g.data || !L ? (
          <Empty icon={<Loader2 className="size-5 animate-spin" />} title="Loading the graph…" />
        ) : g.data.nodes.length === 0 ? (
          <Empty title={`${project.name} has no index yet`} hint="Index it in Code Intelligence to see its modules." />
        ) : (
          <div className="grid grid-cols-12 gap-4">
            <div className="col-span-12 2xl:col-span-7">
              <Panel title="Module graph" flush>
                <div className="overflow-auto p-3">
                  <svg width={L.width} height={L.height} className="block" role="img" aria-label={`Module graph of ${project.name}`}>
                    <defs>
                      <marker id="lg-arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                        <path d="M0 0 L8 4 L0 8 z" fill="var(--os-line-strong)" />
                      </marker>
                      <marker id="lg-hot" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                        <path d="M0 0 L8 4 L0 8 z" fill="var(--os-brand)" />
                      </marker>
                    </defs>
                    {L.labels.map((r) => (
                      <text key={r.label} x={4} y={r.y - 8} fontSize="10" fill="var(--os-dim)">{r.label}</text>
                    ))}
                    {g.data.edges.map((e, i) => {
                      const a = L.pos.get(e.from), b = L.pos.get(e.to);
                      if (!a || !b) return null;
                      const hot = sel !== null && (e.from === sel || e.to === sel);
                      const x1 = a.x + NW / 2, x2 = b.x + NW / 2;
                      let d: string;
                      if (b.y > a.y) {
                        const y1 = a.y + NH, y2 = b.y, mid = (y1 + y2) / 2;
                        d = `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
                      } else if (b.y < a.y) {
                        const y1 = a.y, y2 = b.y + NH, mid = (y1 + y2) / 2;
                        d = `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
                      } else {
                        d = `M ${x1} ${a.y} C ${x1} ${a.y - 28}, ${x2} ${b.y - 28}, ${x2} ${b.y}`;
                      }
                      return (
                        <path key={i} d={d} fill="none"
                          stroke={hot ? 'var(--os-brand)' : EDGE_COLOR[e.kind] ?? 'var(--os-line)'}
                          strokeWidth={(hot ? 1.4 : 1) + Math.min(2, Math.log2(e.weight + 1) * 0.45)}
                          strokeDasharray={e.kind === 'writes' ? '4 3' : undefined}
                          markerEnd={hot ? 'url(#lg-hot)' : 'url(#lg-arw)'}
                          opacity={hot ? 1 : sel ? 0.22 : 0.6} />
                      );
                    })}
                    {g.data.nodes.map((n) => {
                      const p = L.pos.get(n.id);
                      if (!p) return null;
                      const active = n.id === sel;
                      const choose = () => setPicked({ link, focus: n.kind === 'module' ? { module: n.label } : { object: n.label } });
                      return (
                        <g key={n.id} onClick={choose} style={{ cursor: 'pointer' }} role="button" tabIndex={0} aria-label={n.label}
                          onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); choose(); } }}>
                          <rect x={p.x} y={p.y} width={NW} height={NH} rx="6"
                            fill={active ? 'var(--os-brand-dim)' : 'var(--os-surface)'}
                            stroke={active ? 'var(--os-brand)' : n.risk === 'HIGH' || n.risk === 'CRITICAL' ? 'var(--os-danger)' : 'var(--os-line-strong)'}
                            strokeWidth={active ? 2 : 1} />
                          <rect x={p.x} y={p.y} width="3" height={NH} rx="1.5" fill={NODE_COLOR[n.kind] ?? 'var(--os-soft)'} />
                          <text x={p.x + 11} y={p.y + NH / 2 + 3.5} fontSize="11" fontFamily="var(--font-mono)"
                            fill={active ? 'var(--os-ink)' : 'var(--os-ink-2)'} fontWeight={active ? 600 : 400}>
                            {n.label.length > 21 ? `${n.label.slice(0, 20)}…` : n.label}
                          </text>
                          {n.kind === 'module' && <title>{`${n.label} · ${n.files} files · ${n.lines.toLocaleString()} lines`}</title>}
                        </g>
                      );
                    })}
                  </svg>
                </div>
                <p className="border-t border-line px-5 py-2.5 text-[12px] text-dim">
                  Arrows point to what a module depends on. Click one to see its impact.
                </p>
              </Panel>
            </div>

            <div className="col-span-12 space-y-3 2xl:col-span-5">
              {!focus ? (
                <Panel>
                  <Empty icon={<Target className="size-5" />} title="Pick a module or a table"
                    hint="See who uses it, what it reaches and which tests notice." />
                </Panel>
              ) : impact.error ? (
                <Panel><Empty title={`No impact report for ${nameOf(focus)}`} hint={impact.error} /></Panel>
              ) : !impact.data ? (
                <Panel><Empty icon={<Loader2 className="size-5 animate-spin" />} title="Measuring…" /></Panel>
              ) : (
                <ImpactPanel
                  impact={impact.data}
                  onOpen={(path) => setPicked({ link, focus: { path } })}
                  actions={<>
                    <Button size="sm" onClick={() => nav('/', { state: { draft: `Change ${nameOf(focus)}: ` } })}>
                      <GitBranchPlus className="size-3.5" />Plan this change
                    </Button>
                    {'path' in focus && (
                      <Button size="sm" variant="outline" onClick={() => nav(`/code?path=${encodeURIComponent(focus.path)}`)}>
                        <FileCode className="size-3.5" />Open file
                      </Button>
                    )}
                  </>}
                />
              )}
              {impact.data && impact.data.modules.length > 0 && (
                <Panel flush title="Modules it reaches">
                  <div className="flex flex-wrap gap-1.5 px-5 py-3">
                    {impact.data.modules.map((m) => (
                      <button key={m.name} onClick={() => setPicked({ link, focus: { module: m.name } })}
                        className="rounded-full border border-line bg-surface-2/60 px-2.5 py-1 text-[12px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink">
                        <span className="font-mono">{m.name}</span> <Tag tone="neutral">{m.files}</Tag>
                      </button>
                    ))}
                  </div>
                </Panel>
              )}
              {focus && 'path' in focus && <Mono className="inline-block max-w-full truncate">{focus.path}</Mono>}
            </div>
          </div>
        )}
      </PageBody>
    </Page>
  );
}
