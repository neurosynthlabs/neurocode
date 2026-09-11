import { useEffect, useState, type SyntheticEvent } from 'react';
import { Plus, UsersRound } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Empty, Field, Page, PageBody, PageHeader, Panel } from '@/components/os';
import { api, type Person, type TeamDoc } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { demoTeams, demoUsers } from '@/mock/rbac';
import { attempt, useAdmin } from './load';
import { Avatar, DemoNote, LoadError, Loading } from './kit';

interface TeamsData { teams: TeamDoc[]; people: Person[] }

const loadTeams = async (): Promise<TeamsData> => {
  const [teams, people] = await Promise.all([api.admin.teams(), api.admin.users()]);
  return { teams, people };
};
const DEMO: TeamsData = { teams: demoTeams, people: demoUsers };

export default function Teams() {
  const { can } = useAuth();
  const { data, setData, error, live, reload } = useAdmin(loadTeams, DEMO);
  const manage = live && can('teams:manage');
  const [editing, setEditing] = useState<TeamDoc | 'new' | null>(null);
  // Deleting is two clicks: the first arms the button for four seconds.
  const [armed, setArmed] = useState<string | null>(null);
  useEffect(() => {
    if (!armed) return;
    const id = window.setTimeout(() => setArmed(null), 4000);
    return () => window.clearTimeout(id);
  }, [armed]);

  const person = (id: string) => data?.people.find((p) => p.id === id);
  const put = (t: TeamDoc) => setData((d) => d && {
    ...d, teams: d.teams.some((x) => x.id === t.id) ? d.teams.map((x) => (x.id === t.id ? t : x)) : [...d.teams, t],
  });
  const remove = async (t: TeamDoc) => {
    if (armed !== t.id) { setArmed(t.id); return; }
    setArmed(null);
    const r = await attempt(() => api.admin.deleteTeam(t.id), 'Team not deleted');
    if (!r) return;
    setData((d) => d && { ...d, teams: d.teams.filter((x) => x.id !== t.id) });
    toast.success(`${t.name} deleted`, { description: 'Its members keep their accounts and roles.' });
  };

  return (
    <Page>
      <PageHeader
        title="Teams"
        subtitle="Teams group people by what they work on. They never change what anyone can do; roles do that."
        actions={<Button size="sm" onClick={() => setEditing('new')} disabled={!manage}><Plus className="size-3.5" />New team</Button>}
      />
      <PageBody>
        {!live && <DemoNote what="Creating and editing teams" />}
        {error ? <LoadError error={error} onRetry={reload} /> : !data ? <Loading /> : data.teams.length === 0 ? (
          <Empty
            icon={<UsersRound className="size-6" />} title="No teams yet"
            hint="Group people by what they work on, for example Core or ERP renewal."
            action={manage && <Button size="sm" onClick={() => setEditing('new')}><Plus className="size-3.5" />New team</Button>}
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {data.teams.map((t) => (
              <Panel
                key={t.id} title={t.name} eyebrow={`${t.members.length} ${t.members.length === 1 ? 'member' : 'members'}`}
                actions={manage && (
                  <div className="flex gap-1">
                    <Button size="xs" variant="ghost" onClick={() => setEditing(t)}>Edit</Button>
                    <Button size="xs" variant="ghost" className="text-danger hover:text-danger" onClick={() => void remove(t)}>
                      {armed === t.id ? 'Click again' : 'Delete'}
                    </Button>
                  </div>
                )}
              >
                <p className="text-[13px] text-soft">{t.description || 'No description.'}</p>
                <div className="mt-3.5 space-y-2">
                  {t.members.length === 0 && <p className="text-[12.5px] text-dim">Nobody yet.</p>}
                  {t.members.map((id) => {
                    const p = person(id);
                    return (
                      <div key={id} className="flex items-center gap-2.5">
                        <Avatar name={p?.name ?? id} size={26} />
                        <span className="min-w-0 flex-1 truncate text-[13px] text-ink-2">{p?.name ?? id}</span>
                        <span className="hidden min-w-0 truncate text-[12px] text-dim sm:block">{p?.email}</span>
                      </div>
                    );
                  })}
                </div>
              </Panel>
            ))}
          </div>
        )}
      </PageBody>
      {data && editing && (
        <TeamDialog team={editing === 'new' ? null : editing} people={data.people} onClose={() => setEditing(null)} onSaved={put} />
      )}
    </Page>
  );
}

function TeamDialog({ team, people, onClose, onSaved }: {
  team: TeamDoc | null; people: Person[]; onClose: () => void; onSaved: (t: TeamDoc) => void;
}) {
  const [name, setName] = useState(team?.name ?? '');
  const [description, setDescription] = useState(team?.description ?? '');
  const [members, setMembers] = useState<string[]>(team?.members ?? []);
  const [busy, setBusy] = useState(false);

  const submit = async (e: SyntheticEvent) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    const body = { name: name.trim(), description: description.trim(), members };
    const t = await attempt(
      () => (team ? api.admin.updateTeam(team.id, body) : api.admin.createTeam(body)), team ? 'Team not saved' : 'Team not created',
    );
    setBusy(false);
    if (!t) return;
    onSaved(t);
    toast.success(team ? `${t.name} saved` : `${t.name} created`);
    onClose();
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-[480px]">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{team ? `Edit ${team.name}` : 'New team'}</DialogTitle>
            <DialogDescription>Pick who belongs to it. Their roles stay as they are.</DialogDescription>
          </DialogHeader>
          <Field label="Name" value={name} onChange={setName} autoFocus={!team} placeholder="ERP renewal" />
          <Field label="What it works on" value={description} onChange={setDescription} placeholder="Legacy ERP, module by module" />
          <div>
            <div className="mb-1.5 text-[12.5px] font-medium text-soft">Members · {members.length}</div>
            <div className="max-h-[260px] divide-y divide-line/50 overflow-y-auto rounded-xl border border-line/70">
              {people.map((p) => (
                <label key={p.id} className="flex cursor-pointer items-center gap-3 px-3.5 py-2 hover:bg-surface-2/50">
                  <input
                    type="checkbox" className="size-4 shrink-0 accent-[var(--os-brand)]" checked={members.includes(p.id)}
                    onChange={(e) => setMembers(e.target.checked ? [...members, p.id] : members.filter((m) => m !== p.id))}
                  />
                  <Avatar name={p.name} size={26} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] text-ink">{p.name}</span>
                    <span className="block truncate text-[12px] text-dim">{p.email}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!name.trim() || busy}>{team ? 'Save team' : 'Create team'}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
