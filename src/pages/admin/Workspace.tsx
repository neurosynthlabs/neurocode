import { useEffect, useState, type SyntheticEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Field, KV, Page, PageBody, PageHeader, Panel, Stat, StatGrid } from '@/components/os';
import { api, type WorkspaceInfo } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { demoTeams, demoUsers, roles } from '@/mock/rbac';
import { attempt, useAdmin, when } from './load';
import { DemoNote, LoadError, Loading } from './kit';

const DEMO_WS: WorkspaceInfo = {
  name: 'NeuroCode demo', createdAt: '2026-09-10T09:00:00', people: demoUsers.length, roles: roles.length, teams: demoTeams.length,
};
const loadWorkspace = () => api.admin.workspace();

export default function WorkspacePage() {
  const nav = useNavigate();
  const { can, refresh } = useAuth();
  const { mode, health, reset } = useData();
  const { data: ws, setData, error, live, reload } = useAdmin(loadWorkspace, DEMO_WS);
  const manage = live && can('workspace:admin');
  const [name, setName] = useState<string | null>(null);
  // Resetting is two clicks: the first arms the button for four seconds, the second does it.
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);

  const renamed = !!ws && !!name?.trim() && name.trim() !== ws.name;
  const rename = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!renamed || !name) return;
    const next = await attempt(() => api.admin.updateWorkspace(name.trim()), 'Workspace not renamed');
    if (!next) return;
    setData(next);
    setName(null);
    void refresh();  // the account menu shows the name too
    toast.success(`Renamed to ${next.name}`);
  };
  const resetData = async () => {
    if (!armed) { setArmed(true); return; }
    setArmed(false);
    if (await reset()) {
      toast.success(mode === 'live' ? 'Work data is back to the sample' : 'Demo data restored',
        { description: 'People, roles, teams, keys and the audit log were kept.' });
    }
  };

  return (
    <Page>
      <PageHeader title="Workspace" subtitle="What everyone here shares: its name, what it holds, and how it keeps people and keys safe." />
      <PageBody>
        {!live && <DemoNote what="Renaming the workspace" />}
        {error ? <LoadError error={error} onRetry={reload} /> : !ws ? <Loading /> : (
          <div className="space-y-5">
            <StatGrid cols={3}>
              <Stat label="People" value={ws.people} sub="with an account" onClick={() => nav('/admin/users')} />
              <Stat label="Roles" value={ws.roles} sub="5 built in" onClick={() => nav('/admin/roles')} />
              <Stat label="Teams" value={ws.teams} sub="groups of people" onClick={() => nav('/admin/teams')} />
            </StatGrid>

            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <Panel title="Name" eyebrow={`Created ${when(ws.createdAt)}`}>
                <form onSubmit={rename} className="flex flex-col gap-2 sm:flex-row sm:items-end">
                  <Field className="flex-1" label="Workspace name" value={name ?? ws.name} onChange={setName} disabled={!manage} />
                  <Button type="submit" disabled={!manage || !renamed}>Rename</Button>
                </form>
                <p className="mt-2 text-[12.5px] text-dim">Shown on the sign-in screen and in everyone’s account menu.</p>
              </Panel>
              <Panel title="Security" eyebrow="How access is kept safe">
                <KV k="Sessions" v="14 days, in an HttpOnly cookie" />
                <KV k="Passwords" v="scrypt, at least 10 characters" />
                <KV k="Wrong passwords" v="5 in a row pause that email for 30 s" />
                <KV k="Model keys" v="server/secrets.json · owner-only" mono />
                <KV k="On the record" v="sign-ins, access, keys, resets" />
              </Panel>
            </div>

            <Panel title="Work data" eyebrow={mode === 'live' ? 'SQLite, on this machine' : 'Sample data, in this tab'} className="border-danger/30">
              {health && <KV k="Database file" v={health.db.split('/').slice(-2).join('/')} mono />}
              {health && (
                <KV k="Holds" v={`${health.counts.tasks ?? 0} tasks · ${health.counts.plans ?? 0} plans · ${health.counts.memory ?? 0} facts · ${health.counts.activity ?? 0} log lines`} />
              )}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="max-w-xl text-[12.5px] leading-relaxed text-soft">
                  Reset puts the sample work back: tasks, plans, memory, approvals and the activity log. People, roles, teams,
                  keys and the audit log are kept.
                </p>
                <Button size="sm" variant="destructive" onClick={() => void resetData()} disabled={!can('workspace:admin')}>
                  {armed ? 'Click again to confirm' : 'Reset work data'}
                </Button>
              </div>
            </Panel>
          </div>
        )}
      </PageBody>
    </Page>
  );
}
