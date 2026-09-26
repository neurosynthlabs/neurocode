import { useMemo, useState } from 'react';
import {
  ArrowDown, ArrowUp, BarChart3, ChevronLeft, ChevronRight, Database, FileWarning, Loader2, Play, RotateCw, Table2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Empty, Segmented, Tag, cx } from '@/components/os';
import { ApiError } from '@/lib/api';
import { useRemote } from '@/lib/remote';
import {
  data, type ColumnKind, type ColumnStats, type DataCell, type DataPlot, type DataQueryResult, type DataWhere,
} from '@/lib/live/data';

/* A data file opened in the Workbench: its rows (paged and sorted by the server), its columns (type,
   nulls, distinct, min, max, mean, and a chart of the one chosen), and a read-only SQL box over it. The
   file stays where it is; every figure here is the database's answer about it. */

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer.');
type View = 'rows' | 'columns' | 'sql';
const VIEWS: { id: View; label: string }[] = [{ id: 'rows', label: 'Rows' }, { id: 'columns', label: 'Columns' }, { id: 'sql', label: 'SQL' }];
const PAGE_SIZES = [50, 100, 250, 500];

const count = (n: number) => n.toLocaleString('en-US');

function size(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function number(n: number): string {
  if (Number.isInteger(n)) return count(n);
  const abs = Math.abs(n);
  if (abs !== 0 && (abs < 0.001 || abs >= 1e15)) return n.toExponential(3);
  return n.toLocaleString('en-US', { maximumFractionDigits: 4 });
}

/** One cell as the grid shows it: null set apart, numbers aligned, long text cut with the whole on hover. */
function CellText({ value }: { value: DataCell }) {
  if (value === null) return <span className="text-dim italic">null</span>;
  if (typeof value === 'boolean') return <span className="font-mono text-[12px] text-soft">{String(value)}</span>;
  if (typeof value === 'number') return <span className="tnum">{number(value)}</span>;
  return <span title={value.length > 60 ? value : undefined}>{value.length > 200 ? `${value.slice(0, 200)}…` : value}</span>;
}

/** A grid of rows. Headers sort when `onSort` is given; the sticky header and first column keep their place. */
function Grid({ columns, rows, offset, sort, desc, onSort }: {
  columns: { name: string; type: string; kind?: ColumnKind }[];
  rows: DataCell[][];
  offset: number;
  sort?: string | null;
  desc?: boolean;
  onSort?: (column: string) => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-max min-w-full border-separate border-spacing-0 text-[12.5px]">
        <thead>
          <tr>
            <th className="sticky top-0 left-0 z-20 border-r border-b border-line/70 bg-surface px-3 py-2 text-right font-normal text-dim tnum">#</th>
            {columns.map((c) => {
              const on = sort === c.name;
              const label = (
                <span className="flex items-center gap-1.5">
                  <span className="font-medium text-ink-2">{c.name}</span>
                  {c.type && <span className="font-mono text-[11px] font-normal text-dim">{c.type.toLowerCase()}</span>}
                  {on && (desc ? <ArrowDown className="size-3 text-brand" /> : <ArrowUp className="size-3 text-brand" />)}
                </span>
              );
              return (
                <th key={c.name} scope="col" aria-sort={on ? (desc ? 'descending' : 'ascending') : undefined}
                  className="sticky top-0 z-10 border-b border-line/70 bg-surface px-3 py-2 text-left whitespace-nowrap">
                  {onSort
                    ? <button type="button" onClick={() => onSort(c.name)} className="rounded-md hover:text-ink" title={`Sort by ${c.name}`}>{label}</button>
                    : label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={offset + i} className="hover:bg-surface-2/60">
              <td className="sticky left-0 border-r border-b border-line/40 bg-surface px-3 py-1.5 text-right text-dim tnum">{count(offset + i + 1)}</td>
              {r.map((v, j) => (
                <td key={j} className={cx('max-w-[28rem] truncate border-b border-line/40 px-3 py-1.5 text-ink-2',
                  (columns[j]?.kind === 'number' || typeof v === 'number') && 'text-right')}>
                  <CellText value={v} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Rows ───────────────────────────────────────────────────────── */

function RowsView({ where, total, nonce }: { where: DataWhere; total: number; nonce: number }) {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(100);
  const [sort, setSort] = useState<{ column: string; desc: boolean } | null>(null);
  const key = `${where.path}|${where.table ?? ''}|${offset}|${limit}|${sort?.column ?? ''}|${sort?.desc ?? ''}|${nonce}`;
  const page = useRemote(key, () => data.rows(where, { offset, limit, sort: sort?.column ?? null, desc: sort?.desc ?? false }));

  // Ascending, then descending, then the file's own order.
  const cycle = (column: string) => {
    setOffset(0);
    setSort((was) => (was?.column !== column ? { column, desc: false } : !was.desc ? { column, desc: true } : null));
  };

  const shown = page.data;
  const last = shown ? Math.min(shown.total, offset + shown.rows.length) : 0;
  if (page.error) return <Empty icon={<FileWarning className="size-6" />} title="These rows could not be read" hint={page.error} />;
  if (!shown) return <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Reading rows…</div>;
  if (total === 0 && shown.total === 0) {
    return <Empty icon={<Table2 className="size-6" />} title="No rows" hint="Columns, but no rows yet." />;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Grid columns={shown.columns} rows={shown.rows} offset={shown.offset} sort={shown.sort} desc={shown.desc} onSort={cycle} />
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line/70 px-3 py-1.5 text-[12.5px] text-soft">
        <span className="tnum">{shown.rows.length ? `${count(offset + 1)}–${count(last)}` : 'None'} of {count(shown.total)}</span>
        {page.loading && <Loader2 className="size-3.5 animate-spin text-dim" />}
        <span className="ml-auto flex items-center gap-1.5">
          <label className="flex items-center gap-1.5 text-dim">
            Rows per page
            <select value={limit} onChange={(e) => { setLimit(Number(e.target.value)); setOffset(0); }}
              className="h-7 rounded-md border border-line bg-surface-2/60 px-1.5 text-[12.5px] text-ink-2">
              {PAGE_SIZES.map((n) => <option key={n} value={n} className="bg-surface">{n}</option>)}
            </select>
          </label>
          <Button size="icon-sm" variant="ghost" aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
            <ChevronLeft className="size-4" />
          </Button>
          <Button size="icon-sm" variant="ghost" aria-label="Next page" disabled={last >= shown.total} onClick={() => setOffset(offset + limit)}>
            <ChevronRight className="size-4" />
          </Button>
        </span>
      </div>
    </div>
  );
}

/* ── Columns: statistics, and a chart of the one chosen ───────────── */

const W = 560;
const H = 180;
const FOOT = 18;

function Histogram({ plot }: { plot: Extract<DataPlot, { kind: 'histogram' }> }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...plot.bins.map((b) => b.count));
  const bw = W / Math.max(1, plot.bins.length);
  const on = hover === null ? null : plot.bins[hover];
  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="h-4 text-[12px] text-soft tnum">
        {on ? `${number(on.lo)} to ${number(on.hi)} · ${count(on.count)} row${on.count === 1 ? '' : 's'}` : `${plot.bins.length} bins · hover a bar`}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Histogram of ${plot.column}`} onMouseLeave={() => setHover(null)}>
        {[0.5, 1].map((f) => <line key={f} x1="0" x2={W} y1={H - FOOT - f * (H - FOOT - 6)} y2={H - FOOT - f * (H - FOOT - 6)} stroke="var(--os-line)" strokeDasharray="2 4" />)}
        {plot.bins.map((b, i) => {
          const h = (b.count / max) * (H - FOOT - 6);
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              <rect x={i * bw} y={0} width={bw} height={H} fill="transparent" />
              {h > 0 && <rect x={i * bw + 1} y={H - FOOT - h} width={Math.max(1, bw - 2)} height={h} rx="2" fill="var(--os-brand)" opacity={hover === i ? 1 : 0.75} />}
            </g>
          );
        })}
        <text x="0" y={H - 4} fontSize="10" fill="var(--os-dim)">{number(plot.bins[0]?.lo ?? 0)}</text>
        <text x={W} y={H - 4} fontSize="10" textAnchor="end" fill="var(--os-dim)">{number(plot.bins[plot.bins.length - 1]?.hi ?? 0)}</text>
      </svg>
    </figure>
  );
}

function TopValues({ plot }: { plot: Extract<DataPlot, { kind: 'top' }> }) {
  const max = Math.max(1, ...plot.values.map((v) => v.count));
  return (
    <div className="flex flex-col gap-1.5">
      {plot.values.map((v) => (
        <div key={String(v.value)} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-[12.5px]">
          <span className="truncate text-ink-2" title={String(v.value)}><CellText value={v.value} /></span>
          <span className="h-2 rounded-full bg-surface-3"><span className="block h-2 rounded-full bg-brand/75" style={{ width: `${(v.count / max) * 100}%` }} /></span>
          <span className="text-right text-soft tnum">{count(v.count)}</span>
        </div>
      ))}
      {plot.other > 0 && <p className="text-[12px] text-dim tnum">{count(plot.other)} more rows hold the other {count(plot.distinct - plot.values.length)} values.</p>}
    </div>
  );
}

function Line({ plot }: { plot: Extract<DataPlot, { kind: 'line' }> }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = plot.points.filter((p): p is { t: DataCell; value: number } => p.value !== null);
  if (pts.length === 0) return <p className="text-[13px] text-dim">No dates to draw.</p>;
  const lo = Math.min(...pts.map((p) => p.value));
  const hi = Math.max(...pts.map((p) => p.value));
  const span = hi - lo || 1;
  const x = (i: number) => (pts.length === 1 ? W / 2 : (i / (pts.length - 1)) * W);
  const y = (v: number) => H - FOOT - ((v - lo) / span) * (H - FOOT - 10) - 2;
  const d = pts.map((p, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(p.value)}`).join(' ');
  const on = hover === null ? null : pts[hover];
  const what = plot.y ? `mean ${plot.y}` : 'rows';
  const stamp = (t: DataCell) => String(t ?? '').slice(0, plot.unit === 'year' ? 4 : plot.unit === 'month' ? 7 : plot.unit === 'day' ? 10 : 16).replace('T', ' ');
  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="h-4 text-[12px] text-soft tnum">
        {on ? `${stamp(on.t)} · ${number(on.value)} ${what}` : `${what} per ${plot.unit}${plot.capped ? ' · the first 400 shown' : ''}`}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`${what} per ${plot.unit} over ${plot.column}`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const at = ((e.clientX - box.left) / box.width) * W;
          setHover(pts.length === 1 ? 0 : Math.max(0, Math.min(pts.length - 1, Math.round((at / W) * (pts.length - 1)))));
        }}>
        {[0.5, 1].map((f) => <line key={f} x1="0" x2={W} y1={H - FOOT - f * (H - FOOT - 10)} y2={H - FOOT - f * (H - FOOT - 10)} stroke="var(--os-line)" strokeDasharray="2 4" />)}
        <path d={d} fill="none" stroke="var(--os-brand)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        {on && <circle cx={x(hover ?? 0)} cy={y(on.value)} r="3.5" fill="var(--os-brand)" />}
        <text x="0" y={H - 4} fontSize="10" fill="var(--os-dim)">{stamp(pts[0].t)}</text>
        <text x={W} y={H - 4} fontSize="10" textAnchor="end" fill="var(--os-dim)">{stamp(pts[pts.length - 1].t)}</text>
      </svg>
    </figure>
  );
}

function Chart({ where, column, numbers, nonce }: { where: DataWhere; column: ColumnStats; numbers: string[]; nonce: number }) {
  const [y, setY] = useState('');
  const [bins, setBins] = useState(20);
  const key = `${where.path}|${where.table ?? ''}|${column.name}|${column.kind === 'date' ? y : ''}|${column.kind === 'number' ? bins : ''}|${nonce}`;
  const plot = useRemote(column.kind === 'other' ? null : key,
    () => data.plot(where, column.name, { bins: column.kind === 'number' ? bins : undefined, y: column.kind === 'date' && y ? y : null }));
  return (
    <section className="flex min-w-0 flex-col gap-3 px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto truncate text-[13.5px] font-medium text-ink">{column.name}</h3>
        {column.kind === 'number' && (
          <label className="flex items-center gap-1.5 text-[12.5px] text-dim">Bins
            <select value={bins} onChange={(e) => setBins(Number(e.target.value))} className="h-7 rounded-md border border-line bg-surface-2/60 px-1.5 text-[12.5px] text-ink-2">
              {[10, 20, 40, 60].map((n) => <option key={n} value={n} className="bg-surface">{n}</option>)}
            </select>
          </label>
        )}
        {column.kind === 'date' && numbers.length > 0 && (
          <label className="flex items-center gap-1.5 text-[12.5px] text-dim">Show
            <select value={y} onChange={(e) => setY(e.target.value)} className="h-7 max-w-[12rem] rounded-md border border-line bg-surface-2/60 px-1.5 text-[12.5px] text-ink-2">
              <option value="" className="bg-surface">Rows per period</option>
              {numbers.map((n) => <option key={n} value={n} className="bg-surface">Mean of {n}</option>)}
            </select>
          </label>
        )}
      </div>
      {column.kind === 'other' && <p className="text-[13px] text-dim">{column.type || 'This column'} values are not charted.</p>}
      {plot.error && <p className="text-[13px] text-danger">{plot.error}</p>}
      {plot.loading && !plot.data && <p className="flex items-center gap-2 text-[13px] text-dim"><Loader2 className="size-3.5 animate-spin" />Counting…</p>}
      {plot.data?.kind === 'histogram' && (plot.data.bins.length ? <Histogram plot={plot.data} /> : <p className="text-[13px] text-dim">Every value is empty.</p>)}
      {plot.data?.kind === 'top' && (plot.data.values.length ? <TopValues plot={plot.data} /> : <p className="text-[13px] text-dim">Every value is empty.</p>)}
      {plot.data?.kind === 'line' && <Line plot={plot.data} />}
      {plot.data && plot.data.nulls > 0 && <p className="text-[12px] text-dim tnum">{count(plot.data.nulls)} of {count(plot.data.total)} rows are empty here and left out.</p>}
    </section>
  );
}

function ColumnsView({ where, nonce }: { where: DataWhere; nonce: number }) {
  const stats = useRemote(`${where.path}|${where.table ?? ''}|${nonce}`, () => data.stats(where));
  const [chosen, setChosen] = useState<string | null>(null);
  const cols = useMemo(() => stats.data?.columns ?? [], [stats.data]);
  const numbers = useMemo(() => cols.filter((c) => c.kind === 'number').map((c) => c.name), [cols]);
  const selected = cols.find((c) => c.name === chosen) ?? cols.find((c) => c.kind !== 'other') ?? null;

  if (stats.error) return <Empty icon={<FileWarning className="size-6" />} title="The columns could not be measured" hint={stats.error} />;
  if (!stats.data) return <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Measuring every column…</div>;
  if (!cols.length) return <Empty icon={<Table2 className="size-6" />} title="No columns" hint="There is nothing in this table to measure." />;
  const rows = stats.data.rows;
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row lg:overflow-hidden">
      <div className="min-h-0 shrink-0 lg:flex-1 lg:overflow-auto">
        <table className="w-full min-w-[640px] border-separate border-spacing-0 text-[12.5px]">
          <thead>
            <tr className="text-left text-dim">
              {['Column', 'Type', 'Empty', 'Distinct', 'Min', 'Max', 'Mean'].map((h, i) => (
                <th key={h} scope="col" className={cx('sticky top-0 z-10 border-b border-line/70 bg-surface px-3 py-2 font-normal', i >= 2 && i !== 4 && i !== 5 && 'text-right')}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cols.map((c) => (
              <tr key={c.name} onClick={() => setChosen(c.name)} aria-selected={selected?.name === c.name}
                className={cx('cursor-pointer', selected?.name === c.name ? 'bg-brand/7' : 'hover:bg-surface-2/60')}>
                <td className="max-w-[14rem] truncate border-b border-line/40 px-3 py-2 font-medium text-ink-2">
                  <button type="button" className="flex items-center gap-1.5 text-left" onClick={() => setChosen(c.name)} aria-label={`Chart ${c.name}`}>
                    <BarChart3 className={cx('size-3.5 shrink-0', selected?.name === c.name ? 'text-brand' : 'text-dim')} />{c.name}
                  </button>
                </td>
                <td className="border-b border-line/40 px-3 py-2 font-mono text-[11.5px] text-soft">{c.type.toLowerCase() || '—'}</td>
                <td className="border-b border-line/40 px-3 py-2 text-right text-soft tnum">
                  {count(c.nulls)}{rows > 0 && c.nulls > 0 && <span className="text-dim"> · {Math.round((c.nulls / rows) * 1000) / 10}%</span>}
                </td>
                <td className="border-b border-line/40 px-3 py-2 text-right text-soft tnum" title={c.distinctApprox ? 'Estimated: DuckDB approximates above a million rows.' : undefined}>
                  {c.distinct === null ? '—' : `${c.distinctApprox ? '≈' : ''}${count(c.distinct)}`}
                </td>
                <td className="max-w-[10rem] truncate border-b border-line/40 px-3 py-2 text-ink-2"><CellText value={c.min} /></td>
                <td className="max-w-[10rem] truncate border-b border-line/40 px-3 py-2 text-ink-2"><CellText value={c.max} /></td>
                <td className="border-b border-line/40 px-3 py-2 text-right text-ink-2 tnum">{c.mean === null ? <span className="text-dim">—</span> : number(c.mean)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="px-3 py-2 text-[12px] text-dim tnum">
          {count(rows)} rows · measured in {count(stats.data.ms)} ms{stats.data.capped ? ` · the first ${cols.length} columns` : ''}
        </p>
      </div>
      <div className="shrink-0 border-t border-line/70 lg:w-[400px] lg:overflow-auto lg:border-t-0 lg:border-l">
        {selected ? <Chart key={selected.name} where={where} column={selected} numbers={numbers} nonce={nonce} />
          : <p className="px-5 py-4 text-[13px] text-dim">No column here can be charted.</p>}
      </div>
    </div>
  );
}

/* ── SQL ────────────────────────────────────────────────────────── */

function SqlView({ where, sqlite, table }: { where: DataWhere; sqlite: boolean; table: string | null }) {
  const start = sqlite ? `SELECT * FROM "${(table ?? 'table').replace(/"/g, '""')}" LIMIT 100` : 'SELECT * FROM data LIMIT 100';
  const [sql, setSql] = useState(start);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DataQueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!sql.trim() || running) return;
    setRunning(true);
    setError(null);
    try {
      setResult(await data.query(where, sql));
    } catch (e) {
      setResult(null);
      setError(reason(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-col gap-2 border-b border-line/70 px-3 py-3">
        <textarea value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} aria-label="SQL query"
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run(); } }}
          rows={4}
          className="w-full resize-y rounded-lg border border-line bg-surface-2/50 px-3 py-2 font-mono text-[12.5px] text-ink outline-none focus-visible:border-brand" />
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-dim">
          <Button size="sm" onClick={() => void run()} disabled={running || !sql.trim()}>
            {running ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}Run
          </Button>
          <span>⌘↵ runs it.</span>
          <span className="min-w-0">
            {sqlite
              ? 'One read-only SELECT over its tables.'
              : 'One SELECT over this file as data; no other file is read.'}
          </span>
        </div>
      </div>
      {error && <p className="shrink-0 border-b border-line/70 px-3 py-2 font-mono text-[12.5px] text-danger [overflow-wrap:anywhere]">{error}</p>}
      {result && (
        <>
          {result.columns.length === 0
            ? <p className="px-3 py-3 text-[13px] text-dim">The query answered with no columns.</p>
            : <Grid columns={result.columns} rows={result.rows} offset={0} />}
          <p className="shrink-0 border-t border-line/70 px-3 py-1.5 text-[12.5px] text-soft tnum">
            {result.rows.length === 0 ? 'No rows' : `${count(result.rows.length)} row${result.rows.length === 1 ? '' : 's'}`}
            {result.truncated && ` · first ${count(result.cap)} shown; add a LIMIT or WHERE for the rest`}
            {` · ${count(result.ms)} ms`}
          </p>
        </>
      )}
      {!result && !error && (
        <Empty icon={<Database className="size-6" />} title="Ask the file a question"
          hint={sqlite ? 'Name any of its tables; up to 1,000 rows come back.' : 'The file is the table data; up to 1,000 rows come back.'} />
      )}
    </div>
  );
}

/* ── The file ───────────────────────────────────────────────────── */

function DataFile({ path, projectId }: { path: string; projectId: string | null }) {
  const [table, setTable] = useState<string | null>(null);
  const [view, setView] = useState<View>('rows');
  const [nonce, setNonce] = useState(0);
  const info = useRemote(`${path}|${projectId ?? ''}|${table ?? ''}|${nonce}`, () => data.open({ path, projectId, table }));
  const doc = info.data;

  if (info.error) {
    return (
      <Empty icon={<FileWarning className="size-6" />} title="This file could not be opened" hint={info.error}
        action={<Button size="sm" variant="outline" onClick={() => setNonce((n) => n + 1)}><RotateCw className="size-3.5" />Try again</Button>} />
    );
  }
  if (!doc) {
    return <div className="flex h-full items-center justify-center gap-2 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />Reading the file…</div>;
  }
  const where: DataWhere = { path: doc.path, table: doc.table };
  const sqlite = doc.format === 'sqlite';
  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-line/70 px-3 py-2">
        <Tag>{doc.formatName}</Tag>
        {sqlite && doc.tables && doc.tables.length > 0 && (
          <label className="flex min-w-0 items-center gap-1.5 text-[12.5px] text-dim">Table
            <select value={doc.table ?? ''} onChange={(e) => setTable(e.target.value)}
              className="h-7 max-w-[14rem] rounded-md border border-line bg-surface-2/60 px-1.5 text-[12.5px] text-ink-2">
              {doc.tables.map((t) => <option key={t.name} value={t.name} className="bg-surface">{t.name}{t.kind === 'view' ? ' (view)' : ''} · {count(t.rows)}</option>)}
            </select>
          </label>
        )}
        <span className="text-[12.5px] text-soft tnum">{count(doc.rows)} rows · {doc.columns.length} columns · {size(doc.size)}</span>
        <span className="ml-auto flex items-center gap-2">
          <Segmented options={VIEWS} value={view} onChange={setView} />
          <Button size="icon-sm" variant="ghost" aria-label="Read the file again" title="Read the file again" onClick={() => setNonce((n) => n + 1)}>
            <RotateCw className={cx('size-3.5', info.loading && 'animate-spin')} />
          </Button>
        </span>
      </div>
      {sqlite && doc.tables?.length === 0 && view !== 'sql'
        ? <Empty icon={<Database className="size-6" />} title="No tables yet" hint="No tables or views. SQL still answers a SELECT." />
        : view === 'rows' ? <RowsView key={`${doc.table ?? ''}|${nonce}`} where={where} total={doc.rows} nonce={nonce} />
          : view === 'columns' ? <ColumnsView key={doc.table ?? ''} where={where} nonce={nonce} />
            : <SqlView key={doc.table ?? ''} where={where} sqlite={sqlite} table={doc.table} />}
    </div>
  );
}

/** A data file opened in the Workbench. `path` is what the Workbench opened: absolute, or a project path with its project. */
export default function DataView({ path, projectId }: { path: string; projectId?: string | null }) {
  return <DataFile key={`${projectId ?? ''}|${path}`} path={path} projectId={projectId ?? null} />;
}
