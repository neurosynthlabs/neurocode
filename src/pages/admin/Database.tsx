import { useState } from 'react';
import { DatabaseBackup, Loader2, ShieldCheck, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Bar, Cell, DataTable, Dot, KV, Mono, Page, PageBody, PageHeader, Panel, Row, Stat, StatGrid } from '@/components/os';
import { api, type DatabaseCheck, type DatabaseInfo, type DatabaseOptimized } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { bytes } from '@/pages/code/format';
import { attempt, useAdmin, when } from './load';
import { DemoNote, LoadError, Loading } from './kit';

/* Alembic records which revision a database stands on, never when it got there, so `appliedAt` is
   honestly null — the server sends it that way too. */
const MIGRATIONS = [
  { version: 1, name: 'initial relational schema', revision: '20fb0c814e83', appliedAt: null },
  { version: 2, name: 'login attempts', revision: 'ba71ca7751e2', appliedAt: null },
  { version: 3, name: 'a run log can be a tool call', revision: 'c3f1a9d24b70', appliedAt: null },
  { version: 4, name: 'memory conflicts point at facts', revision: '037e1c34f71c', appliedAt: null },
  { version: 5, name: 'the audit log refuses to be rewritten', revision: 'b95c70ab289a', appliedAt: null },
  { version: 6, name: 'a workspace chunk is unique too', revision: '0d132a2d78a9', appliedAt: null },
  { version: 7, name: 'the ledger records which agent asked', revision: 'b589e3c9d161', appliedAt: null },
  { version: 8, name: 'roles keep the catalogue order', revision: 'd226f409c8fa', appliedAt: null },
];
/** What Postgres' `wal_level` means, for a screen read by someone deciding whether to act. */
const WAL: Record<string, string> = {
  minimal: 'minimal: crash recovery only, no replicas and no point-in-time restore',
  replica: 'replica: enough to recover to a moment in time, and to feed a standby',
  logical: 'logical: replica, plus row-level streaming to another system',
};

const DEMO: DatabaseInfo = {
  path: '127.0.0.1:5432/neurocode', engine: 'PostgreSQL', version: '16.12', pageSize: 8192, pages: 594,
  freePages: 34, walLevel: 'replica', sizeBytes: 4_866_048, walBytes: 0,
  indexes: 93, indexHealth: { count: 93, unused: 41, invalid: 0 },
  migrations: MIGRATIONS, backups: [], backupDir: null,
  tables: [
    { name: 'activity', rows: 91 }, { name: 'ai_calls', rows: 0 }, { name: 'approvals', rows: 6 }, { name: 'audit_log', rows: 6 },
    { name: 'code_files', rows: 0 }, { name: 'memory', rows: 47 }, { name: 'plans', rows: 8 }, { name: 'projects', rows: 5 },
    { name: 'roles', rows: 5 }, { name: 'sessions', rows: 1 }, { name: 'tasks', rows: 19 }, { name: 'users', rows: 4 },
  ],
};
const loadDatabase = () => api.admin.database();

export default function DatabasePage() {
  const { can } = useAuth();
  const { data: db, error, live, reload } = useAdmin(loadDatabase, DEMO);
  const manage = live && can('workspace:admin');
  const [busy, setBusy] = useState<'backup' | 'check' | 'optimize' | null>(null);
  const [check, setCheck] = useState<DatabaseCheck | null>(null);
  const [optimized, setOptimized] = useState<DatabaseOptimized | null>(null);

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

  const rows = db?.tables.reduce((n, t) => n + t.rows, 0) ?? 0;
  const biggest = Math.max(1, ...(db?.tables.map((t) => t.rows) ?? [1]));
  const latest = db?.migrations[db.migrations.length - 1];

  return (
    <Page>
      <PageHeader
        title="Database"
        subtitle="One SQLite file on this machine, in write-ahead-log mode: people, access, the audit log, the work and the code index all live in it. Back it up, check it and keep it compact from here."
        actions={
          <Button size="sm" onClick={() => void backup()} disabled={!manage || busy !== null}>
            {busy === 'backup' ? <Loader2 className="size-3.5 animate-spin" /> : <DatabaseBackup className="size-3.5" />}Back up now
          </Button>
        }
      />
      <PageBody>
        {!live && <DemoNote what="Backups, checks and optimizing" />}
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
                <KV k="Foreign keys" v="enforced on every write" />
                <KV k="Audit log" v="append-only, enforced by a trigger" />
                {db.indexHealth && (
                  <KV k="Indexes" v={`${db.indexHealth.count} · ${db.indexHealth.unused} never used`
                    + (db.indexHealth.invalid ? ` · ${db.indexHealth.invalid} invalid` : '')} />
                )}
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

              <Panel flush title="Backups" eyebrow={`the newest 20 are kept${db.backupDir ? ` in ${db.backupDir.split('/').slice(-2).join('/')}` : ''}`}>
                {db.backups.length === 0 ? (
                  <p className="px-5 py-3 text-[13px] text-soft">No backup yet. One is made on its own before every reset and every schema change.</p>
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
