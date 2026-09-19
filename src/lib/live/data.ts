import { request } from '@/lib/api';

/* Data files opened in the Workbench — CSV, TSV, Parquet, JSON Lines, SQLite. The server answers every
   question (a page of rows, the statistics, a chart, a query), so a large file never comes to the browser.
   A path is absolute, or a project path with the project it belongs to. Everything here only reads. */

export type DataFormat = 'csv' | 'tsv' | 'parquet' | 'jsonl' | 'sqlite';
/** What the screen does with a column: numbers get a histogram, dates a line, the rest their top values. */
export type ColumnKind = 'number' | 'date' | 'bool' | 'text' | 'other';
/** A cell as JSON carries it; an integer past 2^53, a NaN or bytes arrive as words. */
export type DataCell = string | number | boolean | null;

export interface DataColumn { name: string; type: string; kind: ColumnKind }
export interface DataTable { name: string; kind: 'table' | 'view'; rows: number }

export interface DataFileDoc {
  path: string;
  name: string;
  format: DataFormat;
  formatName: string;
  size: number;
  modified: string;
  /** A SQLite file's tables and views; null for the other formats. */
  tables: DataTable[] | null;
  /** The table these columns and rows are of; null for the other formats. */
  table: string | null;
  columns: DataColumn[];
  rows: number;
}

export interface DataPage {
  table: string | null;
  columns: DataColumn[];
  rows: DataCell[][];
  offset: number;
  limit: number;
  total: number;
  sort: string | null;
  desc: boolean;
}

export interface ColumnStats extends DataColumn {
  nulls: number;
  distinct: number | null;
  /** True above a million rows, where the distinct count is DuckDB's estimate. */
  distinctApprox: boolean;
  min: DataCell;
  max: DataCell;
  mean: number | null;
}
export interface DataStats { table: string | null; rows: number; columns: ColumnStats[]; capped: boolean; ms: number }

export type DataPlot =
  | { kind: 'histogram'; column: string; type: string; bins: { lo: number; hi: number; count: number }[]; nulls: number; total: number }
  | { kind: 'top'; column: string; type: string; values: { value: DataCell; count: number }[]; other: number; distinct: number; nulls: number; total: number }
  | { kind: 'line'; column: string; type: string; unit: 'hour' | 'day' | 'month' | 'year'; y: string | null; points: { t: DataCell; value: number | null }[]; capped?: boolean; nulls: number; total: number };

export interface DataQueryResult {
  columns: { name: string; type: string }[];
  rows: DataCell[][];
  truncated: boolean;
  cap: number;
  ms: number;
}

/** The extensions the data view opens, and whether a path is one of them. */
export const DATA_EXTENSIONS = ['.csv', '.tsv', '.tab', '.parquet', '.pq', '.jsonl', '.ndjson', '.db', '.sqlite', '.sqlite3'] as const;
export function isDataFile(path: string): boolean {
  const lower = path.toLowerCase();
  return DATA_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export interface DataWhere { path: string; projectId?: string | null; table?: string | null }

function qs(where: DataWhere, extra: Record<string, string | number | boolean | null | undefined> = {}): string {
  const params = new URLSearchParams({ path: where.path });
  if (where.projectId) params.set('projectId', where.projectId);
  if (where.table) params.set('table', where.table);
  for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined && v !== '') params.set(k, String(v));
  return params.toString();
}

/* A large file is read where it is, which takes a moment; the server stops a question at 30 s (a query at 15). */
const slow = () => AbortSignal.timeout(40000);

export const data = {
  open: (where: DataWhere) => request<DataFileDoc>(`/data/open?${qs(where)}`, { signal: slow() }),
  rows: (where: DataWhere, page: { offset: number; limit: number; sort?: string | null; desc?: boolean }) =>
    request<DataPage>(`/data/rows?${qs(where, { offset: page.offset, limit: page.limit, sort: page.sort, desc: page.sort ? page.desc : null })}`, { signal: slow() }),
  stats: (where: DataWhere) => request<DataStats>(`/data/stats?${qs(where)}`, { signal: slow() }),
  plot: (where: DataWhere, column: string, opts: { bins?: number; y?: string | null } = {}) =>
    request<DataPlot>(`/data/plot?${qs(where, { column, bins: opts.bins, y: opts.y })}`, { signal: slow() }),
  query: (where: DataWhere, sql: string) =>
    request<DataQueryResult>('/data/query', { method: 'POST', json: { path: where.path, projectId: where.projectId ?? null, sql }, signal: slow() }),
};
