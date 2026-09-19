import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { toast } from 'sonner';
import { Segmented } from '@/components/os';
import { cn } from '@/lib/utils';

/* A small renderer for the Mermaid flowcharts a blueprint is drawn as — `flowchart LR`, subgraphs, boxes,
   cylinders for data stores, and arrows. It reads the subset the server and `toMermaid` write, and draws it
   as columns: one per group, in order, with arrows between them. The Mermaid text itself is one click away,
   to paste into a README or any Mermaid viewer. (The full `mermaid` package would draw any diagram, but it
   is a dependency the app does not have; this subset is everything a blueprint produces.) */

interface Node { id: string; label: string; store: boolean }
interface Group { id: string; title: string; nodes: string[] }
interface Parsed { groups: Group[]; nodes: Map<string, Node>; edges: { from: string; to: string; label: string }[] }

const unescape = (s: string) => s.replace(/#quot;/g, '"');

function parseMermaid(text: string): Parsed {
  const nodes = new Map<string, Node>();
  const groups: Group[] = [];
  const edges: Parsed['edges'] = [];
  const loose: Group = { id: '', title: '', nodes: [] };
  let current: Group | null = null;
  const define = (id: string, label: string, store: boolean) => {
    if (!nodes.has(id)) (current ?? loose).nodes.push(id);
    nodes.set(id, { id, label: unescape(label), store });
  };
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('%%') || /^flowchart\b|^graph\b/.test(line)) continue;
    const sub = line.match(/^subgraph\s+([\w-]+)\s*(?:\[\s*"?(.*?)"?\s*\])?$/);
    if (sub) {
      current = { id: sub[1], title: unescape(sub[2] ?? sub[1]), nodes: [] };
      groups.push(current);
      continue;
    }
    if (line === 'end') { current = null; continue; }
    const edge = line.match(/^([\w-]+)\s*-->\s*(?:\|(.*?)\|\s*)?([\w-]+)$/);
    if (edge) {
      [edge[1], edge[3]].forEach((id) => { if (!nodes.has(id)) define(id, id, false); });
      edges.push({ from: edge[1], to: edge[3], label: edge[2] ?? '' });
      continue;
    }
    const store = line.match(/^([\w-]+)\[\(\s*"(.*)"\s*\)\]$/);
    if (store) { define(store[1], store[2], true); continue; }
    const box = line.match(/^([\w-]+)[[(]{1,2}\s*"(.*)"\s*[\])]{1,2}$/) ?? line.match(/^([\w-]+)\[(.*)\]$/);
    if (box) define(box[1], box[2], false);
  }
  const all = loose.nodes.length ? [...groups.filter((g) => g.id !== 'data'), loose, ...groups.filter((g) => g.id === 'data')] : groups;
  return { groups: all.filter((g) => g.nodes.length), nodes, edges };
}

const COL = 196;
const GAP = 64;
const NODE_H = 42;
const ROW = 58;
const PAD = 14;
const HEAD = 30;

/** Two lines at most: the name, then what it is built with. */
function split(label: string): [string, string] {
  const at = label.indexOf(' · ');
  return at < 0 ? [label, ''] : [label.slice(0, at), label.slice(at + 3)];
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export function Diagram({ text, className }: { text: string; className?: string }) {
  const [view, setView] = useState<'drawing' | 'mermaid'>('drawing');
  const [copied, setCopied] = useState(false);
  const parsed = useMemo(() => parseMermaid(text), [text]);

  // Columns when there is room for every group side by side; otherwise each group is a row whose nodes
  // wrap, so a narrow panel or a phone still reads the drawing at full size.
  const box = useRef<HTMLDivElement>(null);
  const empty = parsed.groups.length === 0;
  const [room, setRoom] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const seen = new ResizeObserver(([entry]) => setRoom(Math.floor(entry.contentRect.width)));
    seen.observe(el);
    return () => seen.disconnect();
  }, [view, empty]);

  const layout = useMemo(() => {
    const at = new Map<string, { x: number; y: number }>();
    const across = parsed.groups.length * (COL + GAP) - GAP;
    if (!room || across <= room) {
      const boxes = parsed.groups.map((g, c) => {
        const x = c * (COL + GAP);
        g.nodes.forEach((id, r) => at.set(id, { x: x + PAD, y: HEAD + PAD + r * ROW }));
        return { g, x, y: 0, w: COL, h: HEAD + PAD * 2 + Math.max(1, g.nodes.length) * ROW - (ROW - NODE_H) };
      });
      return { at, boxes, width: Math.max(COL, across), height: Math.max(120, ...boxes.map((b) => b.h)), rows: false };
    }
    const width = Math.max(COL, room - 2);
    const node = COL - PAD * 2;
    const per = Math.max(1, Math.floor((width - PAD * 2 + 12) / (node + 12)));
    let y = 0;
    const boxes = parsed.groups.map((g) => {
      const lines = Math.max(1, Math.ceil(g.nodes.length / per));
      g.nodes.forEach((id, i) => at.set(id, { x: PAD + (i % per) * (node + 12), y: y + HEAD + PAD + Math.floor(i / per) * ROW }));
      const b = { g, x: 0, y, w: width, h: HEAD + PAD * 2 + lines * ROW - (ROW - NODE_H) };
      y += b.h + 28;
      return b;
    });
    return { at, boxes, width, height: Math.max(120, y - 28), rows: true };
  }, [parsed, room]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error('The clipboard is not available here. Select the text and copy it instead.');
    }
  };

  const w = COL - PAD * 2;
  return (
    <div className={cn('min-w-0', className)}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented options={[{ id: 'drawing', label: 'Diagram' }, { id: 'mermaid', label: 'Mermaid' }]} value={view} onChange={setView} />
        <button onClick={() => void copy()} className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] text-soft transition-colors hover:bg-surface-2 hover:text-ink">
          {copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}Copy Mermaid
        </button>
      </div>
      {view === 'mermaid' ? (
        <pre className="ascii max-h-[420px] overflow-auto rounded-lg bg-base p-4 font-mono text-[12px] ring-1 ring-line/60 ring-inset">{text}</pre>
      ) : parsed.groups.length === 0 ? (
        <p className="rounded-lg bg-base px-4 py-6 text-center text-[13px] text-dim ring-1 ring-line/60 ring-inset">
          Nothing to draw yet. Choose a technology for a layer, or add a service.
        </p>
      ) : (
        <div ref={box} className="overflow-x-auto rounded-lg bg-base p-3 ring-1 ring-line/60 ring-inset">
          <svg width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`} role="img"
            aria-label="Architecture diagram" className="block">
            <defs>
              <marker id="bp-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" style={{ fill: 'var(--os-dim)' }} />
              </marker>
            </defs>
            {layout.boxes.map(({ g, x, y, w: bw, h }) => (
              <g key={g.id || 'loose'}>
                {g.title && (
                  <>
                    <rect x={x + 0.5} y={y + 0.5} width={bw - 1} height={h - 1} rx={12}
                      style={{ fill: 'var(--os-surface)', stroke: 'var(--os-line)' }} />
                    <text x={x + PAD} y={y + 20} style={{ fill: 'var(--os-dim)', fontSize: 11.5, fontWeight: 500 }}>{g.title}</text>
                  </>
                )}
              </g>
            ))}
            {parsed.edges.map((e, i) => {
              const a = layout.at.get(e.from);
              const b = layout.at.get(e.to);
              if (!a || !b) return null;
              let d: string;
              if (layout.rows && b.y !== a.y) {
                // Rows: leave from the bottom (or top) edge and arrive at the facing edge.
                const down = b.y > a.y;
                const x1 = a.x + w / 2, y1 = down ? a.y + NODE_H : a.y, x2 = b.x + w / 2, y2 = down ? b.y : b.y + NODE_H;
                const bend = Math.max(20, Math.abs(y2 - y1) / 2) * (down ? 1 : -1);
                d = `M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}`;
              } else {
                const forward = b.x > a.x;
                const x1 = a.x + w;
                const y1 = a.y + NODE_H / 2;
                const x2 = forward ? b.x : b.x + w;
                const y2 = b.y + NODE_H / 2;
                const bend = forward ? Math.max(24, (x2 - x1) / 2) : 36;
                d = forward
                  ? `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
                  : `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 + bend} ${y2}, ${x2} ${y2}`;
              }
              return <path key={i} d={d} markerEnd="url(#bp-arrow)" style={{ fill: 'none', stroke: 'var(--os-line-strong)', strokeWidth: 1.3 }} />;
            })}
            {[...layout.at.entries()].map(([id, p]) => {
              const n = parsed.nodes.get(id);
              if (!n) return null;
              const [name, what] = split(n.label);
              return (
                <g key={id}>
                  <title>{n.label}</title>
                  {n.store ? (
                    <>
                      <rect x={p.x} y={p.y + 4} width={w} height={NODE_H - 4} rx={6} style={{ fill: 'var(--os-surface-2)', stroke: 'var(--os-line-strong)' }} />
                      <ellipse cx={p.x + w / 2} cy={p.y + 5} rx={w / 2} ry={5} style={{ fill: 'var(--os-surface-3)', stroke: 'var(--os-line-strong)' }} />
                    </>
                  ) : (
                    <rect x={p.x} y={p.y} width={w} height={NODE_H} rx={9} style={{ fill: 'var(--os-surface-2)', stroke: 'var(--os-line-strong)' }} />
                  )}
                  <text x={p.x + 10} y={p.y + (what ? 18 : 25)} style={{ fill: 'var(--os-ink)', fontSize: 12.5, fontWeight: 600 }}>{clip(name, 24)}</text>
                  {what && <text x={p.x + 10} y={p.y + 33} style={{ fill: 'var(--os-soft)', fontSize: 11.5 }}>{clip(what, 28)}</text>}
                </g>
              );
            })}
          </svg>
        </div>
      )}
    </div>
  );
}
