import { useState } from 'react';
import { FileClock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Cell, DataTable, Empty, Page, PageBody, PageHeader, Panel, Row, Segmented, Tag, type Tone } from '@/components/os';
import { api, type AuditEntry } from '@/lib/api';
import { attempt, useAdmin, when } from './load';
import { LoadError, Loading } from './kit';

type Filter = 'all' | 'auth' | 'access' | 'work' | 'system';

const PAGE = 100;
const loadAudit = () => api.admin.audit();
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'Everything' }, { id: 'auth', label: 'Sign-ins' }, { id: 'access', label: 'People & roles' },
  { id: 'work', label: 'Merges & tests' }, { id: 'system', label: 'AI & workspace' },
];
/* Every action the API writes to the log (each `AuditRepository.record` call on the server). One it
   writes that is not named here shows as its own action string, which is still exactly what happened. */
const LABEL: Record<string, string> = {
  'workspace.setup': 'Workspace created', 'workspace.update': 'Workspace renamed', 'data.reset': 'Workspace emptied',
  'auth.login': 'Signed in', 'auth.login_failed': 'Sign-in refused', 'auth.password': 'Changed own password',
  'user.create': 'Person added', 'user.update': 'Person changed', 'user.password_reset': 'Password set by an admin',
  'role.create': 'Role created', 'role.update': 'Role changed', 'role.delete': 'Role deleted',
  'team.create': 'Team created', 'team.update': 'Team changed', 'team.delete': 'Team deleted',
  'ai.update': 'Model keys changed', 'database.backup': 'Database backed up', 'database.optimize': 'Database optimized',
  'run.merge': 'Run merged', 'test.expect': 'Test failure expected', 'test.expect.remove': 'Test expectation removed',
};
const kind = (action: string): Filter =>
  action.startsWith('auth.') ? 'auth' : /^(user|role|team)\./.test(action) ? 'access'
    : /^(run|test)\./.test(action) ? 'work' : 'system';
const tone = (action: string): Tone =>
  action === 'auth.login_failed' ? 'danger' : action.endsWith('.delete') || action === 'data.reset' ? 'warn'
    : action.startsWith('auth.') ? 'neutral' : 'info';
const describe = (detail: Record<string, unknown>) => Object.entries(detail)
  .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v && typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
  .join(' · ');

export default function Audit() {
  const { data, error, reload } = useAdmin<AuditEntry[]>(loadAudit);
  const [older, setOlder] = useState<AuditEntry[]>([]);
  const [end, setEnd] = useState(false);
  const [filter, setFilter] = useState<Filter>('all');
  const all = [...(data ?? []), ...older];
  const shown = all.filter((e) => filter === 'all' || kind(e.action) === filter);

  const more = async () => {
    const last = all[all.length - 1];
    if (!last) return;
    const next = await attempt(() => api.admin.audit(last.seq), 'Older entries did not load');
    if (!next) return;
    setOlder((o) => [...o, ...next]);
    if (next.length < PAGE) setEnd(true);
  };

  return (
    <Page>
      <PageHeader
        title="Audit log"
        subtitle="Who changed what, when and from where."
        about={<>
          <p>Sign-ins, access, keys and workspace changes, merged runs, and failing tests marked as expected.</p>
          <p>Nothing here can be edited or deleted.</p>
        </>}
      >
        <div className="pb-3"><Segmented options={FILTERS} value={filter} onChange={setFilter} /></div>
      </PageHeader>
      <PageBody>
        {error ? <LoadError error={error} onRetry={reload} /> : !data ? <Loading /> : shown.length === 0 ? (
          <Empty icon={<FileClock className="size-6" />} title="Nothing recorded yet" hint="Entries appear here as people sign in and change access." />
        ) : (
          <Panel flush title={`${shown.length} ${shown.length === 1 ? 'entry' : 'entries'}`} eyebrow="Newest first">
            <DataTable head={['When', 'Who', 'What', 'Target', 'Detail', 'From']}>
              {shown.map((e) => (
                <Row key={e.seq}>
                  <Cell className="text-[13px] whitespace-nowrap text-soft">{when(e.at)}</Cell>
                  <Cell className="font-medium whitespace-nowrap text-ink">{e.user ?? <span className="font-normal text-dim">—</span>}</Cell>
                  <Cell><span title={e.action}><Tag tone={tone(e.action)}>{LABEL[e.action] ?? e.action}</Tag></span></Cell>
                  <Cell className="text-[13px] text-ink-2"><span className="block max-w-[240px] truncate" title={e.target}>{e.target || '—'}</span></Cell>
                  <Cell className="text-[12.5px] text-soft"><span className="block max-w-[320px] truncate" title={describe(e.detail)}>{describe(e.detail) || '—'}</span></Cell>
                  <Cell mono className="whitespace-nowrap text-dim">{e.ip || '—'}</Cell>
                </Row>
              ))}
            </DataTable>
            {!end && all.length >= PAGE && (
              <div className="border-t border-line/60 px-5 py-3">
                <Button size="sm" variant="ghost" onClick={() => void more()}>Load older entries</Button>
              </div>
            )}
          </Panel>
        )}
      </PageBody>
    </Page>
  );
}
