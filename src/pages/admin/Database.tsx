import { useState } from 'react';
import { DatabaseBackup, Eraser, Loader2, ShieldCheck, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Bar, Cell, DataTable, Dot, KV, Mono, Page, PageBody, PageHeader, Panel, Row, Stat, StatGrid } from '@/components/os';
import { api, request, type DatabaseCheck, type DatabaseInfo, type DatabaseOptimized } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { bytes } from '@/pages/code/format';
import { attempt, useAdmin, when } from './load';
import { LoadError, Loading } from './kit';

/** What Postgres' `wal_level` means, for a screen read by someone deciding whether to act. */
const WAL: Record<string, string> = {
  minimal: 'minimal: crash recovery only, no replicas and no point-in-time restore',
  replica: 'replica: enough to recover to a moment in time, and to feed a standby',
  logical: 'logical: replica, plus row-level streaming to another system',
};

/** One table that only ever grows, how long it is kept, and how many rows are older than that —
    counted by the server, so the button can say exactly what it would remove before it removes it. */
interface HistoryTable {
  table: string;
  label: string;
  note: string;
  /** The environment variable that sets how long this one is kept. */
  setting: string;
  /** 0 means keep everything, and then `rows` is 0 whatever the table holds. */
  days: number;
  cutoff: string | null;
  rows: number;
}
interface Retention { tables: HistoryTable[]; rows: number }
/** What a prune actually did. `note` is empty when the table was finished, and says why when it was not. */
interface Pruned { at: string; ms: number; removed: number; tables: (HistoryTable & { removed: number; note: string })[] }

const loadDatabase = () => api.admin.database();
/* These two live here rather than in `api.admin`: they are the whole of the History panel below, and
   nothing else asks for them. Counting is a statement per table, and a prune walks a year of rows. */
const loadRetention = () => request<Retention>('/admin/database/retention', { signal: AbortSignal.timeout(60_000) });
const pruneHistory = () => request<Pruned>('/admin/database/prune', { method: 'POST', signal: AbortSignal.timeout(600_000) });

/** "90 days", or "kept" when nothing is ever removed. */
const kept = (days: number) => (days ? `${days} days` : 'kept');

export default function DatabasePage() {
  const { can } = useAuth();
  const { data: db, error, reload } = useAdmin<DatabaseInfo>(loadDatabase);
  const { data: history, reload: reloadHistory } = useAdmin<Retention>(loadRetention);
  const manage = can('workspace:admin');
  const [busy, setBusy] = useState<'backup' | 'check' | 'optimize' | 'prune' | null>(null);
  const [check, setCheck] = useState<DatabaseCheck | null>(null);
  const [optimized, setOptimized] = useState<DatabaseOptimized | null>(null);
  const [pruned, setPruned] = useState<Pruned | null>(null);

  const backup = async () => {
    setBusy('backup');
    const made = await attempt(() => api.admin.backupDatabase(), 'No backup was made');
    setBusy(null);
    if (!made) return;
    toast.success('Backed up', { description: `${made.name} · ${bytes(made.bytes)}` });
    reload();
  };
  const runCheck = async () => {
    setBusy('check');
    const result = await attempt(() => api.admin.checkDatabase(), 'The check did not run');
    setBusy(null);
    if (result) setCheck(result);
  };
  const optimize = async () => {
    setBusy('optimize');
    const result = await attempt(() => api.admin.optimizeDatabase(), 'Not optimized');
    setBusy(null);
    if (!result) return;
    setOptimized(result);
    toast.success('Optimized', { description: `${bytes(result.beforeBytes)} → ${bytes(result.afterBytes)} in ${(result.ms / 1000).toFixed(1)} s` });
    reload();
  };

  const prune = async () => {
    setBusy('prune');
    const done = await attempt(() => pruneHistory(), 'Nothing was removed');
    setBusy(null);
    if (!done) return;
    setPruned(done);
    toast.success('History pruned', { description: `${done.removed.toLocaleString()} rows removed in ${(done.ms / 1000).toFixed(1)} s` });
    reloadHistory();
    reload();
  };

  const rows = db?.tables.reduce((n, t) => n + t.rows, 0) ?? 0;
  const biggest = Math.max(1, ...(db?.tables.map((t) => t.rows) ?? [1]));
  const latest = db?.migrations[db.migrations.length - 1];

  return (
    <Page>
      <PageHeader
        title="Database"
        subtitle={`${db ? `${db.engine} ${db.version} at ${db.path}` : 'The PostgreSQL database behind this workspace'}: people, access, the audit log, the work and the code index all live in it. Back it up, check it and keep it compact from here.`}
        actions={
          <Button size="sm" onClick={() => void backup()} disabled={!manage || busy !== null}>
            {busy === 'backup' ? <Loader2 className="size-3.5 animate-spin" /> : <DatabaseBackup className="size-3.5" />}Back up now
          </Button>
        }
      />
      <PageBody>
        {error ? <LoadError error={error} onRetry={reload} /> : !db ? <Loading /> : (
          <div className="space-y-5">
            <StatGrid cols={4}>
              <Stat label="On disk" value={bytes(db.sizeBytes + db.walBytes)} sub={`${db.pages.toLocaleString()} pages of ${bytes(db.pageSize)}`} />
              <Stat label="Tables" value={db.tables.length} sub={`${rows.toLocaleString()} rows · ${db.indexes} indexes`} />
              <Stat label="Schema" value={`v${latest?.version ?? 0}`} sub={latest?.name ?? 'no migrations'} />
              <Stat label="Backups" value={db.backups.length} tone={db.backups.length ? 'ok' : 'warn'}
                sub={db.backups[0] ? `latest ${when(db.backups[0].at)}` : 'none yet'} />
            </StatGrid>

            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <Panel title="Health" eyebrow="What keeps the data sound">
                <KV k="Engine" v={`${db.engine} ${db.version}`} />
                <KV k="Write-ahead log" v={WAL[db.walLevel] ?? db.walLevel} />
                <KV k="Foreign keys" v={check ? (check.foreignKeyProblems ? `${check.foreignKeyProblems.toLocaleString()} rows point at nothing` : 'every one walked, none violated') : 'declared in the schema · Check integrity walks them'} />
                <KV k="Audit log" v="append-only by design: a trigger refuses edits and deletes" />
                <KV k="Indexes" v={`${db.indexHealth.count} · ${db.indexHealth.unusedCount} never used`
                  + (db.indexHealth.invalidCount ? ` · ${db.indexHealth.invalidCount} invalid` : '')} />
                <KV k="Free pages" v={`${db.freePages.toLocaleString()} (${bytes(db.freePages * db.pageSize)})`} />
                {check && (
                  <div className="mt-3 flex items-start gap-2 rounded-lg border border-line bg-surface-2/50 px-3 py-2.5">
                    <Dot state={check.ok ? 'ok' : 'error'} className="mt-1.5" />
                    <span className="text-[13px] text-ink-2">
                      {check.ok ? 'Integrity is sound, and every foreign key points at a row.'
                        : `${check.integrity.filter((x) => x !== 'ok').join(' · ') || 'Integrity ok'}${check.foreignKeyProblems ? ` · ${check.foreignKeyProblems} rows point at nothing` : ''}`}
                    </span>
                  </div>
                )}
                {optimized && (
                  <p className="mt-2 text-[12.5px] text-dim">Last optimized: {bytes(optimized.beforeBytes)} → {bytes(optimized.afterBytes)}.</p>
                )}
                <div className="mt-4 flex flex-wrap gap-2 border-t border-line/60 pt-4">
                  <Button size="sm" variant="outline" onClick={() => void runCheck()} disabled={!manage || busy !== null}>
                    {busy === 'check' ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}Check integrity
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => void optimize()} disabled={!manage || busy !== null}>
                    {busy === 'optimize' ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}Optimize & compact
                  </Button>
                </div>
              </Panel>

              <Panel flush title="Backups" eyebrow={db.backupDir ? `the newest are kept in ${db.backupDir.split('/').slice(-2).join('/')}` : 'the newest are kept'}>
                {db.backups.length === 0 ? (
                  <p className="px-5 py-3 text-[13px] text-soft">No backup yet. One is made on its own before every reset.</p>
                ) : (
                  <DataTable head={['Backup', 'Size', 'Made']}>
                    {db.backups.map((b) => (
                      <Row key={b.name}>
                        <Cell mono className="max-w-[260px] truncate">{b.name}</Cell>
                        <Cell className="tnum whitespace-nowrap">{bytes(b.bytes)}</Cell>
                        <Cell className="whitespace-nowrap text-soft">{when(b.at)}</Cell>
                      </Row>
                    ))}
                  </DataTable>
                )}
                <p className="border-t border-line/60 px-5 py-3 text-[12.5px] leading-relaxed text-dim">
                  Backups are <Mono>pg_dump</Mono>, taken while the database stays in use, so nothing stops while one is
                  made. To restore, stop the API and run <Mono>pg_restore --clean --if-exists</Mono> against the dump.
                </p>
              </Panel>
            </div>

            <Panel flush title="History" eyebrow="What grows on its own, and how long it is kept">
              <p className="px-5 pb-1 pt-0.5 text-[13px] leading-relaxed text-ink-2">
                Run output, the feed, the ledger and the rest are only ever added to. Each is kept for as long as its
                own setting says and pruned a day at a time; the ledger and the audit log are kept for good — the audit
                log cannot be pruned at all, by anyone. Removing rows does not shrink the files on disk: Optimize &amp;
                compact does that.
              </p>
              {!history ? (
                <p className="px-5 py-3 text-[13px] text-soft">Counting what is past its keeping…</p>
              ) : (
                <DataTable head={['History', 'Kept for', 'Past that']}>
                  {history.tables.map((t) => {
                    const done = pruned?.tables.find((x) => x.table === t.table);
                    return (
                      <Row key={t.table}>
                        <Cell>
                          <span className="text-ink">{t.label}</span>
                          <span className="block text-[12.5px] text-dim">{t.note} · <Mono>{t.table}</Mono></span>
                        </Cell>
                        <Cell className="whitespace-nowrap">
                          <span className={t.days ? 'text-ink-2' : 'text-soft'}>{kept(t.days)}</span>
                          <span className="block text-[12.5px] text-dim"><Mono>{t.setting}</Mono></span>
                        </Cell>
                        <Cell className="tnum whitespace-nowrap">
                          {t.days ? t.rows.toLocaleString() : '—'}
                          {done && done.removed > 0 && (
                            <span className="block text-[12.5px] text-ok">{done.removed.toLocaleString()} removed</span>
                          )}
                          {done && done.note && <span className="block text-[12.5px] text-warn">{done.note}</span>}
                        </Cell>
                      </Row>
                    );
                  })}
                </DataTable>
              )}
              <div className="flex flex-wrap items-center gap-3 border-t border-line/60 px-5 py-4">
                <Button size="sm" variant="outline" onClick={() => void prune()}
                  disabled={!manage || busy !== null || !history || history.rows === 0}>
                  {busy === 'prune' ? <Loader2 className="size-3.5 animate-spin" /> : <Eraser className="size-3.5" />}
                  {history && history.rows > 0 ? `Remove ${history.rows.toLocaleString()} rows` : 'Nothing to remove'}
                </Button>
                <span className="text-[12.5px] text-dim">
                  {!history ? 'Counted before anything is removed.'
                    : history.rows > 0 ? `Counted just now, across ${history.tables.filter((t) => t.rows > 0).length} tables. A few thousand rows go per statement, so nothing else waits on it.`
                    : 'Nothing in any of them is past its keeping.'}
                </span>
              </div>
            </Panel>

            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <Panel flush title="Tables" eyebrow={`${rows.toLocaleString()} rows in all`}>
                <DataTable head={['Table', 'Rows', '']}>
                  {db.tables.map((t) => (
                    <Row key={t.name}>
                      <Cell mono>{t.name}</Cell>
                      <Cell className="tnum">{t.rows.toLocaleString()}</Cell>
                      <Cell className="w-[40%]"><Bar pct={(100 * t.rows) / biggest} tone="brand" /></Cell>
                    </Row>
                  ))}
                </DataTable>
              </Panel>
              <Panel flush title="Migrations" eyebrow="applied in order, each in its own transaction">
                <DataTable head={['Version', 'Migration', 'Applied']}>
                  {db.migrations.map((m) => (
                    <Row key={m.version}>
                      <Cell className="tnum">v{m.version}</Cell>
                      <Cell mono>{m.name}</Cell>
                      <Cell className="whitespace-nowrap text-soft">{when(m.appliedAt)}</Cell>
                    </Row>
                  ))}
                </DataTable>
              </Panel>
            </div>
          </div>
        )}
      </PageBody>
    </Page>
  );
}
