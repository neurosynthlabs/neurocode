import { useEffect, useState, type SyntheticEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Field, KV, Page, PageBody, PageHeader, Panel, Stat, StatGrid } from '@/components/os';
import { api, type WorkspaceInfo } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useData } from '@/lib/data';
import { attempt, useAdmin, when } from './load';
import { LoadError, Loading } from './kit';
import { plural } from '@/lib/words';

const loadWorkspace = () => api.admin.workspace();
/** "30 s", "15 min", "1 h": the lock-out as someone reads it. */
const pause = (s: number) => (s < 120 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

export default function WorkspacePage() {
  const nav = useNavigate();
  const { can, refresh } = useAuth();
  const { health, reset } = useData();
  const { data: ws, setData, error, reload } = useAdmin<WorkspaceInfo>(loadWorkspace);
  const manage = can('workspace:admin');
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
    const done = await reset();
    if (!done) return;
    toast.success('Workspace emptied', {
      description: [
        done.backup ? `A backup was taken first: ${done.backup}.` : 'No backup was taken: pg_dump is not on this machine.',
        `People, roles, teams, keys, the audit log and the ${plural(done.counts.agents ?? 0, 'agent')} on the roster were kept.`,
      ].join(' '),
    });
  };

  return (
    <Page>
      <PageHeader title="Workspace" subtitle="What everyone here shares: its name, what it holds, and how it keeps people and keys safe." />
      <PageBody>
        {error ? <LoadError error={error} onRetry={reload} /> : !ws ? <Loading /> : (
          <div className="space-y-5">
            <StatGrid cols={3}>
              <Stat label="People" value={ws.people} sub="with an account" onClick={() => nav('/admin/users')} />
              <Stat label="Roles" value={ws.roles} sub={`${ws.builtinRoles} built in`} onClick={() => nav('/admin/roles')} />
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
                <KV k="Sessions" v={`${plural(ws.security.sessionDays, 'day')}, in an HttpOnly cookie`} />
                <KV k="Passwords" v={`scrypt, at least ${plural(ws.security.minPassword, 'character')}`} />
                <KV k="Wrong passwords" v={`${ws.security.loginAttempts} in a row pause that email for ${pause(ws.security.lockoutSeconds)}`} />
                <KV k="Model keys" v="kept by the API, never shown in full" />
                <KV k="On the record" v="sign-ins, access, keys, resets" />
              </Panel>
            </div>

            <Panel title="Work data" eyebrow="PostgreSQL, on this machine" className="border-danger/30">
              {health && <KV k="Database" v={health.db} mono />}
              {health && (
                // The planner's estimates, as /health reports them: a table it has not analysed yet reads 0.
                <KV k="Holds" v={[
                  plural(health.counts.tasks ?? 0, 'task'), plural(health.counts.plans ?? 0, 'plan'),
                  plural(health.counts.memory_facts ?? 0, 'fact'), plural(health.counts.activity ?? 0, 'log line'),
                ].join(' · ')} />
              )}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="max-w-xl text-[12.5px] leading-relaxed text-soft">
                  Reset empties the work: projects, tasks, plans, runs, memory, approvals and the activity log. A backup is
                  taken first. People, roles, teams, the agent roster, keys and the audit log are kept.
                </p>
                <Button size="sm" variant="destructive" onClick={() => void resetData()} disabled={!manage}>
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
